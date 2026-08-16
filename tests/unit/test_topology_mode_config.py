"""tool_access.mode: absent is a default, misspelled is an error.

The two cases look similar and are not:

* **Absent.** The deployment never opted into the fail-closed ``front_door``
  topology. Switching it on during an upgrade would break working traffic, so
  absence resolves to ``egress`` silently and deliberately.
* **Present but unrecognised.** The operator was configuring this on purpose.
  Resolving ``front-door`` (hyphen) to ``egress`` gives them the permissive
  topology while their config file says the opposite, and a warning line in a
  log they may never read is not a fair trade for that.

Before this, both cases resolved to ``egress`` and neither was covered by a
test -- a default-permissive branch with no assertions on it.
"""

import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.config import _init_topology_mode_from_config
from mcp_hangar.domain.services import get_tool_access_resolver
from mcp_hangar.domain.services.tool_access_resolver import is_front_door


@pytest.fixture(autouse=True)
def _restore_topology_mode():
    """Leave the process-wide resolver as we found it."""
    resolver = get_tool_access_resolver()
    original = resolver.topology_mode
    yield
    resolver.set_topology_mode(original)


def _mode_after(config: dict) -> str:
    _init_topology_mode_from_config(config)
    return get_tool_access_resolver().topology_mode


class TestValidModes:
    def test_front_door_is_activated_only_when_asked_for(self):
        assert _mode_after({"tool_access": {"mode": "front_door"}}) == "front_door"

    def test_explicit_egress(self):
        assert _mode_after({"tool_access": {"mode": "egress"}}) == "egress"


class TestAbsenceIsBackwardCompatible:
    def test_no_tool_access_section(self):
        assert _mode_after({}) == "egress"

    def test_tool_access_section_without_mode(self):
        assert _mode_after({"tool_access": {}}) == "egress"

    def test_tool_access_section_of_the_wrong_shape(self):
        """A non-dict tool_access is treated as absent, not as an error.

        It cannot carry a mode, so there is no operator intent to honour or
        contradict here.
        """
        assert _mode_after({"tool_access": "yes please"}) == "egress"


class TestMisspelledModeRefusesToStart:
    @pytest.mark.parametrize(
        "value",
        ["front-door", "frontdoor", "FRONT_DOOR", "front_door ", "Egress", "", "strict"],
    )
    def test_unrecognised_value_raises(self, value):
        with pytest.raises(ConfigurationError) as excinfo:
            _init_topology_mode_from_config({"tool_access": {"mode": value}})
        assert "tool_access.mode" in str(excinfo.value)

    def test_error_names_the_valid_values_and_the_opt_out(self):
        """The message has to be actionable at 3am."""
        with pytest.raises(ConfigurationError) as excinfo:
            _init_topology_mode_from_config({"tool_access": {"mode": "front-door"}})
        message = str(excinfo.value)
        assert "'egress'" in message
        assert "'front_door'" in message
        assert "omit the key" in message

    def test_a_typo_never_leaves_the_resolver_on_the_permissive_mode(self):
        """The point of the change: no silent downgrade.

        Start from front_door, then feed a typo. The old code would have moved
        the resolver to egress; now it raises and leaves the mode alone.
        """
        resolver = get_tool_access_resolver()
        resolver.set_topology_mode("front_door")

        with pytest.raises(ConfigurationError):
            _init_topology_mode_from_config({"tool_access": {"mode": "front-door"}})

        assert resolver.topology_mode == "front_door"


class TestAskingWhetherThisIsAFrontDoor:
    """`is_front_door()` is the one place both callers ask, and both must survive
    an unreachable resolver: the flat-handler gate at bootstrap and the
    boot-time warm-up (#885). Neither had covered the fallback while each owned
    its own copy of it."""

    def test_it_reports_the_configured_mode(self):
        get_tool_access_resolver().set_topology_mode("front_door")
        assert is_front_door() is True

        get_tool_access_resolver().set_topology_mode("egress")
        assert is_front_door() is False

    def test_an_unreachable_resolver_answers_no_rather_than_raising(self, monkeypatch):
        # "Not a front door" is the answer that changes nothing: the meta-API
        # stays, and the warm-up does not start a fleet on a guess. Raising here
        # would take the process down during bootstrap.
        monkeypatch.setattr(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            _raise,
        )
        assert is_front_door() is False


def _raise():
    raise RuntimeError("no resolver")
