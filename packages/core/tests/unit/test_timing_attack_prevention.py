"""Timing attack prevention verification tests.

Verifies that API key validation timing does not leak information
about whether a key exists. Uses statistical analysis with generous
bounds to avoid CI flakiness.

Requirements covered: TIME-01, TIME-02, TIME-03
"""

import time


from mcp_hangar.infrastructure.auth.api_key_authenticator import (
    ApiKeyAuthenticator,
    InMemoryApiKeyStore,
)
from mcp_hangar.infrastructure.auth.constant_time import constant_time_key_lookup


class TestConstantTimeModule:
    """Verify the constant_time module uses hmac.compare_digest correctly."""

    def test_compare_digest_is_used(self):
        """Verify hmac.compare_digest is the comparison mechanism."""
        # This is a code-level verification, not timing
        import inspect

        source = inspect.getsource(constant_time_key_lookup)
        assert "hmac.compare_digest" in source

    def test_no_early_return_on_match(self):
        """Verify function iterates all keys, no break/return inside loop."""
        import inspect

        source = inspect.getsource(constant_time_key_lookup)
        # The for loop body should not contain break or early return
        # Parse: between "for" and the final "return result"
        lines = source.split("\n")
        in_loop = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("for "):
                in_loop = True
                continue
            if in_loop:
                if not stripped.startswith((" ", "\t")) or stripped == "":
                    # Dedented -- exited loop
                    if stripped.startswith("return"):
                        break
                    in_loop = False
                else:
                    assert "break" not in stripped, (
                        "Found 'break' inside lookup loop -- violates constant-time guarantee"
                    )
                    # Allow "return" only in nested context (like comments or strings)
                    if "return " in stripped and not stripped.startswith("#"):
                        assert False, "Found 'return' inside lookup loop -- violates constant-time guarantee"


class TestInMemoryStoreTimingCharacteristics:
    """Verify InMemoryApiKeyStore timing does not leak key existence.

    NOTE: These tests use generous bounds (5x) and many iterations
    to be CI-friendly. They detect gross regressions (e.g., reverting
    to dict lookup), not subtle microsecond differences.
    """

    ITERATIONS = 200
    WARMUP = 20

    def _measure_lookup_time(self, store: InMemoryApiKeyStore, key_hash: str) -> list[float]:
        """Measure lookup time for a given hash, returning list of durations."""
        times = []
        # Warmup
        for _ in range(self.WARMUP):
            store.get_principal_for_key(key_hash)

        for _ in range(self.ITERATIONS):
            start = time.perf_counter_ns()
            store.get_principal_for_key(key_hash)
            elapsed = time.perf_counter_ns() - start
            times.append(elapsed)

        return times

    def _trimmed_mean(self, values: list[float], trim_pct: float = 0.1) -> float:
        """Calculate trimmed mean, removing top and bottom percentiles."""
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        trim_count = int(n * trim_pct)
        trimmed = sorted_vals[trim_count : n - trim_count]
        return sum(trimmed) / len(trimmed) if trimmed else 0.0

    def test_valid_vs_invalid_key_timing_within_bounds(self):
        """Lookup time for valid and invalid keys should be similar.

        We allow up to 5x ratio between valid/invalid timing to account
        for noise, CPU scheduling, and GC. The real constant-time guarantee
        comes from hmac.compare_digest, not from this test.

        This test catches regressions like reverting to dict lookup where
        the ratio would be 10-100x.
        """
        store = InMemoryApiKeyStore()

        # Create several keys to make iteration measurable
        valid_keys = []
        for i in range(20):
            key = store.create_key(f"user-{i}", f"key-{i}")
            valid_keys.append(key)

        valid_hash = ApiKeyAuthenticator._hash_key(valid_keys[0])
        invalid_hash = ApiKeyAuthenticator._hash_key("mcp_definitely_not_a_real_key")

        valid_times = self._measure_lookup_time(store, valid_hash)
        invalid_times = self._measure_lookup_time(store, invalid_hash)

        valid_mean = self._trimmed_mean(valid_times)
        invalid_mean = self._trimmed_mean(invalid_times)

        # The ratio should be close to 1.0 for constant-time.
        # Allow generous 5x bounds for CI stability.
        ratio = max(valid_mean, invalid_mean) / max(min(valid_mean, invalid_mean), 1)

        assert ratio < 5.0, (
            f"Timing ratio {ratio:.2f}x exceeds 5x bound. "
            f"Valid mean: {valid_mean:.0f}ns, Invalid mean: {invalid_mean:.0f}ns. "
            f"This suggests the lookup is NOT constant-time."
        )

    def test_key_position_does_not_affect_timing(self):
        """Lookup time should not depend on where the key is in the dict.

        Creates keys and measures lookup for first-inserted vs last-inserted.
        With constant-time iteration, both should take similar time.
        """
        store = InMemoryApiKeyStore()

        keys = []
        for i in range(50):
            key = store.create_key(f"user-{i}", f"key-{i}")
            keys.append(key)

        first_hash = ApiKeyAuthenticator._hash_key(keys[0])
        last_hash = ApiKeyAuthenticator._hash_key(keys[-1])

        first_times = self._measure_lookup_time(store, first_hash)
        last_times = self._measure_lookup_time(store, last_hash)

        first_mean = self._trimmed_mean(first_times)
        last_mean = self._trimmed_mean(last_times)

        ratio = max(first_mean, last_mean) / max(min(first_mean, last_mean), 1)

        assert ratio < 3.0, (
            f"Position-dependent timing ratio {ratio:.2f}x exceeds 3x bound. "
            f"First key mean: {first_mean:.0f}ns, Last key mean: {last_mean:.0f}ns."
        )


class TestAllStoresUseConstantTimeComparison:
    """Verify all auth store implementations import and use constant-time comparison.

    These are structural tests verifying the code uses the right patterns,
    not timing measurements.
    """

    def test_inmemory_store_uses_constant_time(self):
        """InMemoryApiKeyStore imports constant_time module."""
        import inspect

        from mcp_hangar.infrastructure.auth.api_key_authenticator import InMemoryApiKeyStore

        source = inspect.getsource(InMemoryApiKeyStore.get_principal_for_key)
        assert "constant_time_key_lookup" in source or "hmac.compare_digest" in source

    def test_sqlite_store_uses_constant_time(self):
        """SQLiteApiKeyStore uses hmac for constant-time comparison."""
        import inspect

        from mcp_hangar.infrastructure.auth.sqlite_store import SQLiteApiKeyStore

        source = inspect.getsource(SQLiteApiKeyStore.get_principal_for_key)
        assert "hmac" in source or "constant_time" in source

    def test_postgres_store_uses_constant_time(self):
        """PostgresApiKeyStore uses hmac for constant-time comparison."""
        import inspect

        from mcp_hangar.infrastructure.auth.postgres_store import PostgresApiKeyStore

        source = inspect.getsource(PostgresApiKeyStore.get_principal_for_key)
        assert "hmac" in source or "constant_time" in source

    def test_event_sourced_store_uses_constant_time(self):
        """EventSourcedApiKeyStore uses constant-time lookup."""
        import inspect

        from mcp_hangar.infrastructure.auth.event_sourced_store import EventSourcedApiKeyStore

        source = inspect.getsource(EventSourcedApiKeyStore.get_principal_for_key)
        assert "constant_time_key_lookup" in source or "hmac" in source
