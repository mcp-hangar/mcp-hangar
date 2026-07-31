"""The SEP-2663 wire models say on the wire exactly what SEP-2663 says.

These are not model-plumbing tests. Each one pins a field where `mcp_types`'
SEP-1686 fossil disagrees with SEP-2663 -- the disagreements that made
`2.0.0rc1` advertise a capability it could not serve. A failure here means the
served wire has drifted back toward the fossil.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from mcp_hangar.tasks_wire import (
    CancelTaskRequestParams,
    CreateTaskResult,
    EmptyResult,
    EXTENSION_ID,
    GetTaskRequestParams,
    GetTaskResult,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    NAME_BEARING_TASK_METHODS,
    Task,
    TASKS_METHODS,
    UpdateTaskRequestParams,
    missing_capability_error_data,
)

_NOW = "2026-07-28T10:00:00+00:00"


def _dump(model) -> dict:
    """Serialize the way a handler puts it on the wire."""
    return model.model_dump(by_alias=True, mode="json")


class TestCreateTaskResultIsFlat:
    """The single most consequential difference from the fossil."""

    def test_task_fields_sit_at_the_top_level(self):
        payload = _dump(CreateTaskResult(task_id="t-1", status="working", created_at=_NOW, last_updated_at=_NOW))

        assert payload["taskId"] == "t-1"
        assert payload["status"] == "working"
        # `mcp_types.CreateTaskResult` nests all of this under "task". A SEP-2663
        # client reading taskId off the top level would find nothing there --
        # silently, since a missing optional does not raise.
        assert "task" not in payload

    def test_result_type_discriminates_the_result(self):
        payload = _dump(CreateTaskResult(task_id="t-1", status="working", created_at=_NOW, last_updated_at=_NOW))

        # Required on the 2026-07-28 wire and absent from the fossil entirely.
        assert payload["resultType"] == "task"


class TestTtlIsMillisecondsAndSurvivesNull:
    def test_the_field_is_ttl_ms_not_ttl(self):
        payload = _dump(Task(task_id="t-1", status="working", created_at=_NOW, last_updated_at=_NOW, ttl_ms=300_000))

        assert payload["ttlMs"] == 300_000
        # The fossil's name. Serving it means serving the wrong unit under the
        # wrong key -- and there it is a REQUIRED field, so its absence is the
        # tell that the fossil is not in the serialization path.
        assert "ttl" not in payload

    def test_null_ttl_is_present_rather_than_omitted(self):
        """`ttlMs: number | null` is required-but-nullable: absent != null.

        Absent means "not implemented"; null means "no TTL". Pydantic drops a
        `None` default under `exclude_none`, which would flip one meaning into
        the other, so the wrap serializer re-inserts it.
        """
        payload = Task(task_id="t-1", status="working", created_at=_NOW, last_updated_at=_NOW).model_dump(
            by_alias=True, mode="json", exclude_none=True
        )

        assert "ttlMs" in payload
        assert payload["ttlMs"] is None

    def test_poll_hint_is_milliseconds(self):
        payload = _dump(
            Task(
                task_id="t-1",
                status="working",
                created_at=_NOW,
                last_updated_at=_NOW,
                poll_interval_ms=500,
            )
        )

        assert payload["pollIntervalMs"] == 500
        assert "pollInterval" not in payload


class TestGetTaskResultInlinesTheOutcome:
    def test_completed_task_inlines_its_tool_result(self):
        """SEP-2663 folds what needed a second `tasks/result` trip into the poll."""
        payload = _dump(
            GetTaskResult(
                task_id="t-1",
                status="completed",
                created_at=_NOW,
                last_updated_at=_NOW,
                result={"content": [{"type": "text", "text": "done"}], "isError": False},
            )
        )

        assert payload["resultType"] == "complete"
        assert payload["result"]["content"][0]["text"] == "done"
        assert payload["error"] is None

    def test_a_tool_error_is_still_a_completed_task(self):
        """`isError: true` is a result, not a task failure -- they are different layers."""
        payload = _dump(
            GetTaskResult(
                task_id="t-1",
                status="completed",
                created_at=_NOW,
                last_updated_at=_NOW,
                result={"content": [], "isError": True},
            )
        )

        assert payload["status"] == "completed"
        assert payload["result"]["isError"] is True

    def test_input_requests_survive_serialization(self):
        """The deliberate divergence from python-sdk#3005.

        Its `GetTaskResult(Task)` declares no `input_requests` and inherits
        pydantic `extra="ignore"`, so the map is dropped on parse -- breaking the
        in-task input loop the same PR documents. Hangar's consent gate reads it,
        so it is declared explicitly here.
        """
        requests = {"user_name": {"message": "Who are you?", "requestedSchema": {"type": "object"}}}
        payload = _dump(
            GetTaskResult(
                task_id="t-1",
                status="input_required",
                created_at=_NOW,
                last_updated_at=_NOW,
                input_requests=requests,
            )
        )

        assert payload["inputRequests"] == requests

    def test_input_requests_round_trip_from_the_wire(self):
        parsed = GetTaskResult.model_validate(
            {
                "taskId": "t-1",
                "status": "input_required",
                "createdAt": _NOW,
                "lastUpdatedAt": _NOW,
                "inputRequests": {"user_name": {"message": "Who?"}},
            }
        )

        assert parsed.input_requests == {"user_name": {"message": "Who?"}}


class TestAcknowledgements:
    def test_empty_ack_still_carries_result_type(self):
        """`tasks/cancel` / `tasks/update` change nothing but must still discriminate."""
        assert _dump(EmptyResult()) == {"resultType": "complete", "_meta": None}


class TestRequestParams:
    def test_params_accept_wire_and_python_spelling(self):
        assert GetTaskRequestParams.model_validate({"taskId": "t-1"}).task_id == "t-1"
        assert CancelTaskRequestParams(task_id="t-1").task_id == "t-1"

    def test_update_requires_answers(self):
        """A `tasks/update` with no answers is malformed, not an empty no-op."""
        with pytest.raises(ValueError):
            UpdateTaskRequestParams.model_validate({"taskId": "t-1"})

    def test_update_carries_the_answer_map(self):
        params = UpdateTaskRequestParams.model_validate(
            {"taskId": "t-1", "inputResponses": {"user_name": {"content": {"name": "ada"}}}}
        )

        assert params.input_responses == {"user_name": {"content": {"name": "ada"}}}


class TestServedMethodSet:
    def test_removed_methods_are_not_served(self):
        """SEP-2663 removes both. Re-adding one here must be a deliberate act."""
        assert "tasks/result" not in TASKS_METHODS
        assert "tasks/list" not in TASKS_METHODS

    def test_the_three_surviving_methods_are_served(self):
        assert {"tasks/get", "tasks/update", "tasks/cancel"} == TASKS_METHODS

    def test_every_served_method_must_carry_mcp_name(self):
        """SEP-2663 mandates `Mcp-Name: <taskId>` on all of them (via SEP-2243)."""
        assert NAME_BEARING_TASK_METHODS == TASKS_METHODS


class TestMissingCapability:
    def test_the_code_is_the_wire_contract(self):
        assert MISSING_REQUIRED_CLIENT_CAPABILITY == -32021

    def test_the_client_is_told_what_to_declare(self):
        """A modern client can fix its declaration and retry, so it gets a payload.

        (A legacy connection cannot, which is why that case is `-32601` instead.)
        """
        data = missing_capability_error_data()

        assert data["requiredCapabilities"]["extensions"] == {EXTENSION_ID: {}}
        assert EXTENSION_ID == "io.modelcontextprotocol/tasks"
        # Must survive the JSON-RPC error envelope.
        assert json.loads(json.dumps(data)) == data


class TestTheFossilIsNotInTheSerializationPath:
    def test_the_module_never_imports_mcp_types_task_shapes(self):
        """The acceptance criterion for vendoring, asserted on the source.

        A `from mcp_types import Task` here would compile and pass every test
        above that does not check a renamed field -- and would silently reinstate
        the SEP-1686 wire. Cheaper to forbid the import outright.

        Checked over the parsed AST, not the raw text: the module docstring names
        `mcp_types` repeatedly to explain *why* it must not be imported, and a
        substring search cannot tell prose from an import (it flagged the
        explanation on the first run).
        """
        source = (Path(__file__).resolve().parents[2] / "src" / "mcp_hangar" / "tasks_wire.py").read_text()
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        offenders = [name for name in imported if name == "mcp_types" or name.startswith("mcp_types.")]

        assert not offenders, (
            f"tasks_wire imports {offenders} -- mcp_types' Task* types are the SEP-1686 fossil, "
            "and importing them silently reinstates the wire this module exists to replace"
        )

    def test_models_are_not_subclasses_of_the_sdk_types(self):
        mcp_types = pytest.importorskip("mcp_types")

        fossil = getattr(mcp_types, "Task", None)
        if fossil is None:  # pragma: no cover -- SDK without the fossil
            pytest.skip("SDK has no mcp_types.Task")

        assert not issubclass(Task, fossil)
        assert not issubclass(CreateTaskResult, fossil)
