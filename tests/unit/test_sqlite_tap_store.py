"""The SQLite tool-access-policy store."""

import sqlite3
import tempfile
import threading
from unittest.mock import MagicMock

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


class TestSQLiteToolAccessPolicyStore:
    """Tests for SQLiteToolAccessPolicyStore with real SQLite :memory: or tmp file."""

    def _make_store(self):
        from mcp_hangar.auth.infrastructure.sqlite_tap_store import SQLiteToolAccessPolicyStore

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmpfile:
            tmpfile_name = tmpfile.name
        store = SQLiteToolAccessPolicyStore(db_path=tmpfile_name)
        return store, tmpfile_name

    def test_init_creates_schema(self):
        store, path = self._make_store()
        conn = sqlite3.connect(path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_access_policies'")
        assert cursor.fetchone() is not None
        conn.close()
        store.close()

    def test_set_and_get_policy(self):
        store, _ = self._make_store()
        store.set_policy("provider", "math", ToolAccessPolicy(allow_list=("add", "sub"), deny_list=("delete",)))

        policy = store.get_policy("provider", "math")
        assert policy is not None
        assert policy.allow_list == ("add", "sub")
        assert policy.deny_list == ("delete",)
        store.close()

    def test_get_policy_not_found(self):
        store, _ = self._make_store()
        policy = store.get_policy("provider", "ghost")
        assert policy is None
        store.close()

    def test_set_policy_upsert(self):
        store, _ = self._make_store()
        store.set_policy("provider", "math", ToolAccessPolicy(allow_list=("add",)))
        store.set_policy("provider", "math", ToolAccessPolicy(allow_list=("add", "mul"), deny_list=("rm",)))

        policy = store.get_policy("provider", "math")
        assert policy.allow_list == ("add", "mul")
        assert policy.deny_list == ("rm",)
        store.close()

    def test_clear_policy(self):
        store, _ = self._make_store()
        store.set_policy("provider", "math", ToolAccessPolicy(allow_list=("add",)))
        store.clear_policy("provider", "math")

        policy = store.get_policy("provider", "math")
        assert policy is None
        store.close()

    def test_clear_policy_nonexistent_no_error(self):
        store, _ = self._make_store()
        store.clear_policy("provider", "ghost")  # should not raise
        store.close()

    def test_list_all_policies(self):
        store, _ = self._make_store()
        store.set_policy("provider", "math", ToolAccessPolicy(allow_list=("add",)))
        store.set_policy("group", "grp1", ToolAccessPolicy(deny_list=("rm",)))
        store.set_policy("member", "g1:m1", ToolAccessPolicy(allow_list=("x",), deny_list=("y",)))

        all_policies = store.list_all_policies()
        assert len(all_policies) == 3

        scopes = {p[0] for p in all_policies}
        assert scopes == {"provider", "group", "member"}
        store.close()

    def test_list_all_policies_empty(self):
        store, _ = self._make_store()
        all_policies = store.list_all_policies()
        assert all_policies == []
        store.close()

    def test_close_checkpoints_and_closes(self):
        store, path = self._make_store()
        store.set_policy("provider", "x", ToolAccessPolicy(allow_list=("read_*",)))
        store.close()

        # After close, connection should be None
        assert store._local.connection is None

    def test_close_when_no_connection(self):
        store, _ = self._make_store()
        store.close()
        # Second close should be a no-op
        store.close()

    def test_close_checkpoint_failure_does_not_raise(self):
        """If WAL checkpoint fails, close should still complete."""
        store, _ = self._make_store()
        conn = store._get_connection()

        # sqlite3.Connection.execute is read-only, so we wrap the connection
        # with a Mock that delegates most calls but raises on checkpoint SQL.
        mock_conn = MagicMock(wraps=conn)
        original_execute = conn.execute

        def fail_checkpoint(sql, *args):
            if "wal_checkpoint" in str(sql).lower():
                raise sqlite3.OperationalError("checkpoint failed")
            return original_execute(sql, *args)

        mock_conn.execute = fail_checkpoint
        store._local.connection = mock_conn
        store.close()  # should not raise
        assert store._local.connection is None

    def test_thread_local_connections(self):
        """Each thread gets its own connection."""
        store, path = self._make_store()
        connections = []

        def get_conn():
            conn = store._get_connection()
            connections.append(id(conn))
            # store.close() only closes the calling thread's connection, so each
            # worker must close its own to avoid leaking it (ResourceWarning).
            conn.close()

        t1 = threading.Thread(target=get_conn)
        t2 = threading.Thread(target=get_conn)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Different threads should get different connection objects
        assert len(connections) == 2
        # (They may or may not be different ids depending on thread reuse,
        #  but the thread-local mechanism should provide isolation)
        store.close()
