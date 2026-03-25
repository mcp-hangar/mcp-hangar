"""Behavioral deviation detector -- BSL 1.1 licensed.

Implements the IDeviationDetector protocol. Compares incoming
NetworkObservation instances against a learned baseline and returns
a list of deviation dicts when the observation does not match expected
behavioral patterns.

Detection rules:
    1. NEW_DESTINATION -- observation to (host, port) not in any baseline record.
    2. PROTOCOL_DRIFT -- observation to a known (host, port) using a different
       protocol than recorded in the baseline.
    3. FREQUENCY_ANOMALY -- a destination whose observation rate is
       disproportionately high compared to the provider's average destination
       rate. Requires a minimum number of observations before triggering.

See enterprise/LICENSE.BSL for license terms.
"""

from datetime import UTC, datetime
from typing import Any

import structlog

from mcp_hangar.domain.contracts.behavioral import IBaselineStore
from mcp_hangar.domain.value_objects.behavioral import DeviationType, NetworkObservation

logger = structlog.get_logger(__name__)


class DeviationDetector:
    """Pure-logic deviation detector that compares observations to a baseline.

    Reads baseline data from an IBaselineStore and applies three detection
    rules. Returns a list of deviation dicts -- no side effects, no event
    emission, no metrics recording. Callers are responsible for acting on
    the returned deviations.

    Args:
        baseline_store: Provides baseline observation records per provider.
        frequency_threshold_multiplier: A destination's rate must exceed the
            provider's mean destination rate multiplied by this value to
            trigger a FREQUENCY_ANOMALY. Default 3.0.
        min_observations: Minimum observation_count required before the
            frequency check is applied. Default 10.
        severity_overrides: Optional dict mapping DeviationType value strings
            to custom severity levels. Merges over defaults.
    """

    _DEFAULT_SEVERITY: dict[str, str] = {
        DeviationType.NEW_DESTINATION.value: "critical",
        DeviationType.PROTOCOL_DRIFT.value: "high",
        DeviationType.FREQUENCY_ANOMALY.value: "medium",
        DeviationType.SCHEMA_DRIFT.value: "medium",
    }

    def __init__(
        self,
        baseline_store: IBaselineStore,
        frequency_threshold_multiplier: float = 3.0,
        min_observations: int = 10,
        severity_overrides: dict[str, str] | None = None,
    ) -> None:
        self._store = baseline_store
        self._frequency_threshold = frequency_threshold_multiplier
        self._min_observations = min_observations
        self._severity_map = {**self._DEFAULT_SEVERITY}
        if severity_overrides:
            self._severity_map.update(severity_overrides)

    def check_observation(self, observation: NetworkObservation) -> list[dict[str, Any]]:
        """Check an observation against the baseline.

        Applies detection rules in order:
        1. Look up exact match (host, port, protocol).
        2. If no exact match, check for (host, port) match with different protocol.
        3. If no (host, port) match at all, flag as new destination.
        4. For exact matches with enough observations, check frequency anomaly.

        Args:
            observation: The network observation to check.

        Returns:
            List of deviation dicts with keys: deviation_type, observed,
            baseline_expected, severity. Empty list means no deviation.
        """
        records = self._store.get_observations(observation.provider_id)

        # Build lookups: (host, port) -> [records], (host, port, protocol) -> record
        host_port_map: dict[tuple[str, int], list[dict[str, Any]]] = {}
        exact_map: dict[tuple[str, int, str], dict[str, Any]] = {}

        for rec in records:
            key_hp = (rec["host"], rec["port"])
            host_port_map.setdefault(key_hp, []).append(rec)
            key_exact = (rec["host"], rec["port"], rec["protocol"])
            exact_map[key_exact] = rec

        obs_host = observation.destination_host
        obs_port = observation.destination_port
        obs_proto = observation.protocol

        exact_key = (obs_host, obs_port, obs_proto)
        hp_key = (obs_host, obs_port)

        deviations: list[dict[str, Any]] = []

        if exact_key in exact_map:
            # Exact match -- check frequency anomaly only
            freq_dev = self._check_frequency(observation, exact_map[exact_key], records)
            if freq_dev is not None:
                deviations.append(freq_dev)
        elif hp_key in host_port_map:
            # (host, port) exists but with a different protocol -> PROTOCOL_DRIFT
            existing_protocols = [r["protocol"] for r in host_port_map[hp_key]]
            deviations.append(
                {
                    "deviation_type": DeviationType.PROTOCOL_DRIFT.value,
                    "observed": f"{obs_host}:{obs_port}/{obs_proto}",
                    "baseline_expected": f"{obs_host}:{obs_port}/{','.join(existing_protocols)}",
                    "severity": self._get_severity(DeviationType.PROTOCOL_DRIFT.value),
                }
            )
        else:
            # Completely new (host, port) -> NEW_DESTINATION
            deviations.append(
                {
                    "deviation_type": DeviationType.NEW_DESTINATION.value,
                    "observed": f"{obs_host}:{obs_port}/{obs_proto}",
                    "baseline_expected": "not in baseline",
                    "severity": self._get_severity(DeviationType.NEW_DESTINATION.value),
                }
            )

        logger.debug(
            "deviation_check_complete",
            provider_id=observation.provider_id,
            destination=f"{obs_host}:{obs_port}",
            deviations_count=len(deviations),
        )

        return deviations

    def _check_frequency(
        self,
        observation: NetworkObservation,
        record: dict[str, Any],
        all_records: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Check if a destination is contacted at an anomalously high rate.

        Compares the per-destination observation rate (observations/hour)
        against the mean rate across all the provider's destinations.
        If the destination's rate exceeds mean_rate * threshold, it is
        flagged as a FREQUENCY_ANOMALY.

        Requires record["observation_count"] >= min_observations to trigger.

        Args:
            observation: The current observation being checked.
            record: The matching baseline record for this exact destination.
            all_records: All baseline records for this provider.

        Returns:
            A deviation dict if anomaly detected, None otherwise.
        """
        if record["observation_count"] < self._min_observations:
            return None

        if len(all_records) < 2:
            # Cannot compare rate against mean with fewer than 2 destinations
            return None

        now = datetime.now(UTC)

        # Calculate rate for the matched destination
        dest_rate = self._calculate_rate(record, now)

        # Calculate mean rate across ALL destinations
        total_rate = 0.0
        for rec in all_records:
            total_rate += self._calculate_rate(rec, now)
        mean_rate = total_rate / len(all_records)

        if mean_rate <= 0:
            return None

        if dest_rate > mean_rate * self._frequency_threshold:
            return {
                "deviation_type": DeviationType.FREQUENCY_ANOMALY.value,
                "observed": f"{observation.destination_host}:{observation.destination_port} rate={dest_rate:.2f}/h",
                "baseline_expected": f"mean_rate={mean_rate:.2f}/h (threshold={self._frequency_threshold}x)",
                "severity": self._get_severity(DeviationType.FREQUENCY_ANOMALY.value),
            }

        return None

    def _calculate_rate(self, record: dict[str, Any], now: datetime) -> float:
        """Calculate observation rate (observations per hour) for a record.

        Uses lifetime average: observation_count / hours_elapsed_since_first_seen.
        Guards against division by zero with max(hours, 0.001).

        Args:
            record: Baseline record with first_seen and observation_count.
            now: Current UTC datetime.

        Returns:
            Rate in observations per hour.
        """
        first_seen = datetime.fromisoformat(record["first_seen"])
        # Ensure first_seen is timezone-aware (SQLite may strip tz info)
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=UTC)
        hours_elapsed = max((now - first_seen).total_seconds() / 3600.0, 0.001)
        return record["observation_count"] / hours_elapsed

    def _get_severity(self, deviation_type: str) -> str:
        """Look up severity for a deviation type.

        Args:
            deviation_type: Value string from DeviationType enum.

        Returns:
            Severity level string (critical, high, medium, low).
        """
        return self._severity_map.get(deviation_type, "medium")
