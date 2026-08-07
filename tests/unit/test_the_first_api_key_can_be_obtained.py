"""A deployment authenticated by API keys can get its first one.

Every `/api/auth/**` route requires an admin principal, with no carve-out for
the first call, so the first key cannot be minted over HTTP. `auth
bootstrap-admin` is the command that exists to break that -- and it created an
API key as part of its atomic claim and then **discarded the secret**, printing
"No API key secret is printed by design".

That default is right for what it was built for: an OIDC principal, which
authenticates on its own identity and needs no key. It left a deployment whose
only authenticator is API keys with nowhere to go -- and, because the key row
was created regardless, with an unusable credential in its database.

`--show-key` hands it over. The claim is one-shot, so the message when the flag
is absent now says the flag exists, rather than leaving the operator to
discover the dead end after the claim is spent.

Verified end to end on the built image: bootstrap with `--show-key`, start the
gateway, `GET /api/mcp_servers/` answers 401 without the key and 200 with it.
"""

from __future__ import annotations

import inspect

from mcp_hangar.server.cli.commands import auth as auth_cli


class TestTheFlagExists:
    def test_show_key_is_an_option(self) -> None:
        assert "show_key" in inspect.signature(auth_cli.bootstrap_admin_command).parameters

    def test_it_is_off_by_default(self) -> None:
        # The OIDC case is the common one and needs no secret; printing one
        # unasked would put a global admin credential in terminal scrollback
        # and CI logs for every deployment that never wanted it.
        assert inspect.signature(auth_cli.bootstrap_admin_command).parameters["show_key"].default is False


class TestTheSecretIsPrintedOnlyWhenAsked:
    def test_the_raw_key_is_no_longer_discarded(self) -> None:
        source = inspect.getsource(auth_cli.bootstrap_admin_command)

        assert "_raw_key" not in source, "the underscore said 'deliberately unused', and it was the problem"
        assert "raw_key, key_id = result" in source

    def test_it_is_guarded_by_the_flag(self) -> None:
        source = inspect.getsource(auth_cli.bootstrap_admin_command)
        printed = source[source.index("if show_key:") :]

        assert "raw_key" in printed.split("else:")[0]
        assert "raw_key" not in printed.split("else:")[1], "the silent branch must stay silent"

    def test_the_silent_branch_names_the_flag(self) -> None:
        # The claim is one-shot. An operator who learns about `--show-key` after
        # spending it has no second chance, so the message has to arrive first.
        source = inspect.getsource(auth_cli.bootstrap_admin_command)
        silent = source[source.index("else:") :]

        assert "--show-key" in silent
        assert "one-shot" in silent

    def test_printing_it_says_what_it_is(self) -> None:
        source = inspect.getsource(auth_cli.bootstrap_admin_command)
        shown = source[source.index("if show_key:") : source.index("else:")]

        assert "not recoverable" in shown
        assert "global administrator" in shown, "a global admin credential should say so"


class TestTheDocstringStopsPromisingTheOldBehaviour:
    def test_it_no_longer_says_no_credential_is_printed(self) -> None:
        doc = auth_cli.bootstrap_admin_command.__doc__ or ""

        assert "No credential is printed" not in doc
        assert "--show-key" in doc or "show_key" in doc
