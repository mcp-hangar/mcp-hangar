"""Approval delivery channels resolve through entry points, not through core (WS-4).

``_build_delivery`` used to hardcode ``if channel == "slack"`` and import
``.delivery.slack``, which put one vendor's Block Kit payloads and signing scheme
in the core tree. The outbound side was already behind the ``ApprovalDelivery``
protocol -- the coupling was the branch above it.

Core now ships ``dashboard`` and ``noop`` and resolves anything else from the
``mcp_hangar.approvals.delivery`` entry-point group. The load-bearing claims:

* a core install with no vendor package works;
* adding an adapter requires no change here;
* a broken or missing adapter degrades rather than failing startup.

That last one is a judgment worth stating: an unknown channel returns ``noop``
with a warning instead of raising. Approvals then queue undelivered and stay
resolvable through the REST API, which is recoverable. Refusing to boot the
gateway because a *notification* channel is missing is not -- it turns a
degraded notification path into a total outage.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from mcp_hangar.approvals.bootstrap import DELIVERY_ENTRY_POINT_GROUP, _build_delivery
from mcp_hangar.approvals.delivery.dashboard import DashboardApprovalDelivery
from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery


def _config(channel: str, **channel_config):
    cfg = {"approvals": {"channel": channel}}
    if channel_config:
        cfg["approvals"][channel] = channel_config
    return cfg


class TestBuiltins:
    def test_dashboard_is_built_in(self) -> None:
        assert isinstance(_build_delivery(_config("dashboard")), DashboardApprovalDelivery)

    def test_noop_is_built_in(self) -> None:
        assert isinstance(_build_delivery(_config("noop")), NoOpApprovalDelivery)

    def test_no_config_at_all_is_noop(self) -> None:
        assert isinstance(_build_delivery(None), NoOpApprovalDelivery)

    def test_default_channel_is_dashboard(self) -> None:
        assert isinstance(_build_delivery({"approvals": {}}), DashboardApprovalDelivery)


class TestVendorAdaptersLoadFromEntryPoints:
    """The WS-4 acceptance: an adapter is installed, not imported from core."""

    def test_a_registered_channel_is_constructed_with_its_config(self) -> None:
        built = {}

        class _Adapter:
            def __init__(self, config):
                built["config"] = config

        entry_point = SimpleNamespace(
            name="pigeon-post",
            value="acme.pigeon:factory",
            load=lambda: _Adapter,
        )

        with patch("importlib.metadata.entry_points", return_value=[entry_point]) as ep:
            delivery = _build_delivery(_config("pigeon-post", loft="north"))

        assert isinstance(delivery, _Adapter)
        assert built["config"] == {"loft": "north"}
        # Looked up in the documented group, not scanned globally.
        assert ep.call_args.kwargs["group"] == DELIVERY_ENTRY_POINT_GROUP

    def test_core_does_not_import_any_vendor_module(self) -> None:
        """Nothing under ``approvals`` may name a vendor.

        This is the assertion that fails if someone re-adds the convenience
        import that this work removed.
        """
        import pathlib

        import mcp_hangar.approvals as approvals_pkg

        root = pathlib.Path(approvals_pkg.__file__).parent
        offenders = sorted(
            str(path.relative_to(root)) for path in root.rglob("*.py") if "slack" in path.name.lower()
        )

        assert offenders == [], f"vendor module back in core: {offenders}"


class TestDegradation:
    def test_unknown_channel_falls_back_to_noop(self) -> None:
        with patch("importlib.metadata.entry_points", return_value=[]):
            delivery = _build_delivery(_config("nobody-provides-this"))

        assert isinstance(delivery, NoOpApprovalDelivery)

    def test_an_entry_point_that_fails_to_load_does_not_raise(self) -> None:
        def _explode():
            raise ImportError("adapter package is broken")

        entry_point = SimpleNamespace(name="broken", value="broken:factory", load=_explode)

        with patch("importlib.metadata.entry_points", return_value=[entry_point]):
            delivery = _build_delivery(_config("broken"))

        assert isinstance(delivery, NoOpApprovalDelivery)

    def test_an_adapter_that_fails_to_construct_does_not_raise(self) -> None:
        class _Unconstructable:
            def __init__(self, config):
                raise RuntimeError("missing credentials")

        entry_point = SimpleNamespace(
            name="grumpy", value="grumpy:factory", load=lambda: _Unconstructable
        )

        with patch("importlib.metadata.entry_points", return_value=[entry_point]):
            delivery = _build_delivery(_config("grumpy"))

        assert isinstance(delivery, NoOpApprovalDelivery)
