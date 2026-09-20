"""Boot Hangar from a file, push an L7 egress policy over REST, and reload it (#1498).

Run as a script, in its own interpreter, by
``test_a_reload_keeps_an_l7_policy_set_over_the_api.py``:
``python _reload_l7_policy_harness.py <workdir> <out.json>``. Not collected by
pytest. A separate process for the reason ``_reload_served_harness.py`` gives:
``bootstrap()`` fills process-global state.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, the policy through ``POST /api/mcp_servers/{id}/l7_policy``, the reload
through ``POST /api/config/reload``, and the calls through ``hangar_call`` over
streamable HTTP. Both servers are real stdio subprocesses.

No configuration file declares an L7 policy -- there is no key for one -- so the
policy lives only on the running server object. The file is booted, both servers
are called, both are given a policy that denies ``add``, and then one reload:

* ``keep`` is unchanged, so the reload keeps the running object (#1470);
* ``edit`` gets a new ``env``, so the reload rebuilds it.

Each is then read back over REST and called again, so what is recorded is the
enforcement, not only the stored policy.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from _reload_keeps_harness import _Gateway, _server
from _reload_served_harness import BASE_URL, _served_app, _write

DIFF_KEYS = ("mcp_servers_added", "mcp_servers_removed", "mcp_servers_updated", "mcp_servers_unchanged")
SERVERS = ("keep", "edit")

#: What an operator pushes, and no file declares. ``add`` is the tool the mock
#: upstream serves and this harness calls; everything else stays allowed, so a
#: refusal names this policy and not a restrictive default.
L7_POLICY = {"tools": {"deny": ["add"]}, "defaultAction": "Allow"}


def _config(phase: str) -> dict[str, Any]:
    return {
        "config_reload": {"enabled": False},
        "mcp_servers": {
            "keep": _server(),
            "edit": _server(env={"MOCK_ADD_DESCRIPTION": "before" if phase == "boot" else "after"}),
        },
    }


class _L7Gateway(_Gateway):
    def set_l7(self, mcp_server: str) -> int:
        return self.client.post(f"/api/mcp_servers/{mcp_server}/l7_policy", json=L7_POLICY).status_code

    def l7(self, mcp_server: str) -> Any:
        """The policy the gateway holds for *mcp_server*, or the status code when it holds none."""
        response = self.client.get(f"/api/mcp_servers/{mcp_server}/l7_policy")
        return response.json() if response.status_code == 200 else response.status_code


def run(workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    path = workdir / "config.yaml"
    _write(path, _config("boot"))
    context = bootstrap(config_path=str(path))
    repository = context.runtime.repository
    out: dict[str, Any] = {}

    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _L7Gateway(client, {})
        out["boot"] = {
            "calls": {sid: gateway.call(sid) for sid in SERVERS},
            "set": {sid: gateway.set_l7(sid) for sid in SERVERS},
        }
        # In force before the reload, or nothing below says anything.
        out["under_policy"] = {"calls": {sid: gateway.call(sid) for sid in SERVERS}}
        booted = {sid: repository.get(sid) for sid in SERVERS}

        _write(path, _config("edited"))
        status, body = gateway.reload(None)
        out["edited"] = {
            "status": status,
            "diff": {key: body["result"].get(key) for key in DIFF_KEYS},
            "same_object": {sid: repository.get(sid) is server for sid, server in booted.items()},
            "policy": {sid: gateway.l7(sid) for sid in SERVERS},
            "calls": {sid: gateway.call(sid) for sid in SERVERS},
            # The rebuilt server did restart, with the file's new env.
            "edit_description": repository.get("edit").get_tools_dict()["add"].description,
        }

    for server in repository.get_all().values():
        server.shutdown()
    return out


def main(workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
