// Package metrics provides Prometheus metrics for the MCP-Hangar operator
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"sigs.k8s.io/controller-runtime/pkg/metrics"
)

var (
	// ReconcileTotal counts total reconciliations
	ReconcileTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "reconcile_total",
			Help:      "Total number of reconciliations by controller and result",
		},
		[]string{"controller", "result"},
	)

	// ReconcileDuration tracks reconciliation duration
	ReconcileDuration = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "reconcile_duration_seconds",
			Help:      "Duration of reconciliation in seconds",
			Buckets:   prometheus.ExponentialBuckets(0.001, 2, 15),
		},
		[]string{"controller"},
	)

	// ProviderState tracks current provider states
	ProviderState = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "provider_state",
			Help:      "Current state of providers (1 = in this state)",
		},
		[]string{"namespace", "name", "state"},
	)

	// ProviderToolsCount tracks tools per provider
	ProviderToolsCount = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "provider_tools_count",
			Help:      "Number of tools exposed by provider",
		},
		[]string{"namespace", "name"},
	)

	// ProviderHealthCheckFailures tracks health check failures
	ProviderHealthCheckFailures = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "provider_health_check_failures_total",
			Help:      "Total health check failures by provider",
		},
		[]string{"namespace", "name"},
	)

	// ProviderRestarts tracks provider restarts
	ProviderRestarts = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "provider_restarts_total",
			Help:      "Total provider restarts",
		},
		[]string{"namespace", "name"},
	)

	// CRDCount tracks CRD instances
	CRDCount = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "crd_count",
			Help:      "Number of CRD instances by kind",
		},
		[]string{"kind"},
	)

	// GroupProviderCount tracks providers per group
	GroupProviderCount = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "group_provider_count",
			Help:      "Number of providers in each group",
		},
		[]string{"namespace", "name", "state"},
	)

	// DiscoverySourceCount tracks discovered providers
	DiscoverySourceCount = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "discovery_source_count",
			Help:      "Number of discovered providers by source",
		},
		[]string{"namespace", "name"},
	)

	// DiscoverySyncDuration tracks discovery sync duration
	DiscoverySyncDuration = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "discovery_sync_duration_seconds",
			Help:      "Duration of discovery sync operations",
			Buckets:   prometheus.ExponentialBuckets(0.1, 2, 10),
		},
		[]string{"namespace", "name"},
	)

	// HangarClientErrors tracks errors communicating with Hangar core
	HangarClientErrors = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "hangar_client_errors_total",
			Help:      "Total errors communicating with Hangar core",
		},
		[]string{"operation"},
	)

	// HangarClientLatency tracks latency of Hangar client calls
	HangarClientLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "mcp",
			Subsystem: "operator",
			Name:      "hangar_client_latency_seconds",
			Help:      "Latency of Hangar client calls",
			Buckets:   prometheus.ExponentialBuckets(0.01, 2, 10),
		},
		[]string{"operation"},
	)
)

func init() {
	// Register all metrics with the controller-runtime metrics registry
	metrics.Registry.MustRegister(
		ReconcileTotal,
		ReconcileDuration,
		ProviderState,
		ProviderToolsCount,
		ProviderHealthCheckFailures,
		ProviderRestarts,
		CRDCount,
		GroupProviderCount,
		DiscoverySourceCount,
		DiscoverySyncDuration,
		HangarClientErrors,
		HangarClientLatency,
	)
}

// SetProviderState updates state gauge for a provider
// Sets the specified state to 1 and all others to 0
func SetProviderState(namespace, name, state string) {
	states := []string{"Cold", "Initializing", "Ready", "Degraded", "Dead"}
	for _, s := range states {
		val := float64(0)
		if s == state {
			val = 1
		}
		ProviderState.WithLabelValues(namespace, name, s).Set(val)
	}
}

// ClearProviderMetrics removes all metrics for a deleted provider
func ClearProviderMetrics(namespace, name string) {
	states := []string{"Cold", "Initializing", "Ready", "Degraded", "Dead"}
	for _, s := range states {
		ProviderState.DeleteLabelValues(namespace, name, s)
	}
	ProviderToolsCount.DeleteLabelValues(namespace, name)
	ProviderHealthCheckFailures.DeleteLabelValues(namespace, name)
	ProviderRestarts.DeleteLabelValues(namespace, name)
}

// ClearGroupMetrics removes all metrics for a deleted group
func ClearGroupMetrics(namespace, name string) {
	states := []string{"Cold", "Initializing", "Ready", "Degraded", "Dead"}
	for _, s := range states {
		GroupProviderCount.DeleteLabelValues(namespace, name, s)
	}
}

// ClearDiscoveryMetrics removes all metrics for a deleted discovery source
func ClearDiscoveryMetrics(namespace, name string) {
	DiscoverySourceCount.DeleteLabelValues(namespace, name)
	DiscoverySyncDuration.DeleteLabelValues(namespace, name)
}
