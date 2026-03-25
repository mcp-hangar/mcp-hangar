"""Enterprise behavioral profiling -- BSL 1.1 licensed.

Provides the BehavioralProfiler facade, SQLite-backed BaselineStore,
K8sNetworkMonitor for pod connection observability, and
bootstrap_behavioral() factory for conditional loading by the
server bootstrap pipeline.

See enterprise/LICENSE.BSL for license terms.
"""

from .baseline_store import BaselineStore
from .bootstrap import bootstrap_behavioral
from .k8s_network_monitor import K8sNetworkMonitor
from .profiler import BehavioralProfiler

__all__ = [
    "BaselineStore",
    "BehavioralProfiler",
    "K8sNetworkMonitor",
    "bootstrap_behavioral",
]
