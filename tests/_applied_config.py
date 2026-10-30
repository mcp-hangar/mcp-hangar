"""Undo what loading a configuration put in force in the process.

`load_configuration()` is not a pure read. Through `apply_configuration` it
applies the process-wide sections and commits the `mcp_servers` section: the
servers into the runtime repository, their groups into `GROUPS`, and their
governance overlays -- tool-access policies, `tool_projection` pins and
withdrawals, `header_exposure` -- onto process-wide registries. That is what a
gateway wants. A test that calls it, directly or through `mcp-hangar pin`'s
`observe_digests`, leaves all of it behind for whichever test runs next in the
same process.

A `math.add` pin written that way outlived the pin tests and refused
`math/add` in a later file that never declared a pin (`digest_unverifiable`),
and withheld its schema from `GET /api/tools`. Which file came next decided
whether the suite was green.
"""

from __future__ import annotations


def undo_applied_configuration() -> None:
    """Put every section a configuration load applies back to its default.

    Imports are local so that a test module that never loads a configuration
    does not import the server package for this.
    """
    from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
    from mcp_hangar.domain.policies.header_exposure import clear_header_exposure_policies
    from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
    from mcp_hangar.domain.services.ui_resource_guard import reset_ui_resource_guard
    from mcp_hangar.infrastructure.persistence.log_buffer import remove_log_buffer
    from mcp_hangar.server import config as server_config
    from mcp_hangar.server.state import GROUPS, get_runtime
    from mcp_hangar.server.tools.batch.concurrency import reset_concurrency_manager

    # Every process-wide section absent: each is put back to its default, which
    # is what a reload of a file without it does.
    server_config.apply_process_config({"mcp_servers": {}})

    # The `mcp_servers` section: the servers the load built, their groups, and
    # the overlays they registered.
    repository = get_runtime().repository
    for mcp_server_id in list(server_config._BUILT_FROM):
        remove_log_buffer(mcp_server_id)
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)
    server_config._BUILT_FROM.clear()
    server_config._DECLARED_GROUPS.clear()
    GROUPS.clear()
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    clear_header_exposure_policies()
    reset_ui_resource_guard()
    reset_concurrency_manager()
