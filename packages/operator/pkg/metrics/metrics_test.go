package metrics

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/assert"
)

func TestReconcileTotal(t *testing.T) {
	// Reset counter before test
	ReconcileTotal.Reset()

	// Increment counter
	ReconcileTotal.WithLabelValues("mcpprovider", "success").Inc()
	ReconcileTotal.WithLabelValues("mcpprovider", "success").Inc()
	ReconcileTotal.WithLabelValues("mcpprovider", "error").Inc()

	// Verify metrics
	assert.Equal(t, float64(2), testutil.ToFloat64(ReconcileTotal.WithLabelValues("mcpprovider", "success")))
	assert.Equal(t, float64(1), testutil.ToFloat64(ReconcileTotal.WithLabelValues("mcpprovider", "error")))
}

func TestReconcileDuration(t *testing.T) {
	// Observe some durations
	ReconcileDuration.WithLabelValues("mcpprovider").Observe(0.1)
	ReconcileDuration.WithLabelValues("mcpprovider").Observe(0.5)
	ReconcileDuration.WithLabelValues("mcpprovider").Observe(1.2)

	// Verify that observations were recorded
	count := testutil.CollectAndCount(ReconcileDuration)
	assert.Greater(t, count, 0)
}

func TestProviderState(t *testing.T) {
	// Set provider states
	SetProviderState("default", "provider1", "Ready")
	SetProviderState("default", "provider2", "Degraded")

	// Verify metrics
	assert.Equal(t, float64(1), testutil.ToFloat64(ProviderState.WithLabelValues("default", "provider1", "Ready")))
	assert.Equal(t, float64(0), testutil.ToFloat64(ProviderState.WithLabelValues("default", "provider1", "Degraded")))
	assert.Equal(t, float64(1), testutil.ToFloat64(ProviderState.WithLabelValues("default", "provider2", "Degraded")))
}

func TestSetProviderState_ClearsOtherStates(t *testing.T) {
	// Set to Ready first
	SetProviderState("default", "provider1", "Ready")
	assert.Equal(t, float64(1), testutil.ToFloat64(ProviderState.WithLabelValues("default", "provider1", "Ready")))

	// Change to Degraded
	SetProviderState("default", "provider1", "Degraded")
	assert.Equal(t, float64(0), testutil.ToFloat64(ProviderState.WithLabelValues("default", "provider1", "Ready")))
	assert.Equal(t, float64(1), testutil.ToFloat64(ProviderState.WithLabelValues("default", "provider1", "Degraded")))
}

func TestProviderToolsCount(t *testing.T) {
	// Set tool counts
	ProviderToolsCount.WithLabelValues("default", "provider1").Set(5)
	ProviderToolsCount.WithLabelValues("default", "provider2").Set(10)

	// Verify metrics
	assert.Equal(t, float64(5), testutil.ToFloat64(ProviderToolsCount.WithLabelValues("default", "provider1")))
	assert.Equal(t, float64(10), testutil.ToFloat64(ProviderToolsCount.WithLabelValues("default", "provider2")))
}

func TestProviderHealthCheckFailures(t *testing.T) {
	// Reset counter
	ProviderHealthCheckFailures.Reset()

	// Increment failures
	ProviderHealthCheckFailures.WithLabelValues("default", "provider1").Inc()
	ProviderHealthCheckFailures.WithLabelValues("default", "provider1").Inc()
	ProviderHealthCheckFailures.WithLabelValues("default", "provider1").Inc()

	// Verify count
	assert.Equal(t, float64(3), testutil.ToFloat64(ProviderHealthCheckFailures.WithLabelValues("default", "provider1")))
}

func TestCRDCount(t *testing.T) {
	// Set CRD counts
	CRDCount.WithLabelValues("MCPProvider").Set(25)
	CRDCount.WithLabelValues("MCPProviderGroup").Set(5)
	CRDCount.WithLabelValues("MCPDiscoverySource").Set(3)

	// Verify metrics
	assert.Equal(t, float64(25), testutil.ToFloat64(CRDCount.WithLabelValues("MCPProvider")))
	assert.Equal(t, float64(5), testutil.ToFloat64(CRDCount.WithLabelValues("MCPProviderGroup")))
	assert.Equal(t, float64(3), testutil.ToFloat64(CRDCount.WithLabelValues("MCPDiscoverySource")))
}

func TestMetricsRegistered(t *testing.T) {
	// Verify all metrics are registered
	metrics := []prometheus.Collector{
		ReconcileTotal,
		ReconcileDuration,
		ProviderState,
		ProviderToolsCount,
		ProviderHealthCheckFailures,
		CRDCount,
	}

	for _, metric := range metrics {
		assert.NotNil(t, metric, "Metric should be initialized")
	}
}

func TestReconcileTotal_Labels(t *testing.T) {
	// Test different controller types
	ReconcileTotal.Reset()

	ReconcileTotal.WithLabelValues("mcpprovider", "success").Inc()
	ReconcileTotal.WithLabelValues("mcpprovidergroup", "success").Inc()
	ReconcileTotal.WithLabelValues("mcpdiscoverysource", "success").Inc()

	assert.Equal(t, float64(1), testutil.ToFloat64(ReconcileTotal.WithLabelValues("mcpprovider", "success")))
	assert.Equal(t, float64(1), testutil.ToFloat64(ReconcileTotal.WithLabelValues("mcpprovidergroup", "success")))
	assert.Equal(t, float64(1), testutil.ToFloat64(ReconcileTotal.WithLabelValues("mcpdiscoverysource", "success")))
}

func TestProviderState_AllStates(t *testing.T) {
	states := []string{"Cold", "Initializing", "Ready", "Degraded", "Dead"}

	for _, state := range states {
		SetProviderState("default", "test-provider", state)

		// Only the current state should be 1
		for _, s := range states {
			expected := float64(0)
			if s == state {
				expected = 1
			}
			assert.Equal(t, expected, testutil.ToFloat64(ProviderState.WithLabelValues("default", "test-provider", s)))
		}
	}
}
