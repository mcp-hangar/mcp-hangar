package controller

import (
	"context"
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
	"github.com/mcp-hangar/mcp-hangar/operator/pkg/metrics"
)

func TestReconcileViolationDetection_NilCapabilities(t *testing.T) {
	provider := newTestProvider("no-caps", "default", nil)
	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)
	assert.Empty(t, provider.Status.Violations, "no violations should be added when capabilities are nil")
}

func TestReconcileViolationDetection_NoViolations(t *testing.T) {
	// Provider with capabilities but everything in compliance:
	// - network egress declared AND NetworkPolicyApplied is True
	// - tools maxCount is 10, status.toolsCount is 5
	provider := newTestProvider("compliant", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https", CIDR: "10.0.0.0/8"},
			},
		},
		Tools: &mcpv1alpha1.ToolCapabilitiesSpec{
			MaxCount: 10,
		},
	})
	// Mark NetworkPolicyApplied as True
	provider.Status.SetCondition(ConditionNetworkPolicyApplied, metav1.ConditionTrue,
		"PolicyApplied", "NetworkPolicy applied")
	provider.Status.ToolsCount = 5

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)
	assert.Empty(t, provider.Status.Violations, "no violations when everything is compliant")
}

func TestReconcileViolationDetection_NetworkPolicyDrift(t *testing.T) {
	// Provider declares network egress but NetworkPolicyApplied condition is not True
	provider := newTestProvider("np-drift", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https", CIDR: "10.0.0.0/8"},
			},
		},
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)
	require.Len(t, provider.Status.Violations, 1, "should have one violation for NP drift")
	assert.Equal(t, "capability_drift", provider.Status.Violations[0].Type)
	assert.Equal(t, "high", provider.Status.Violations[0].Severity)
	assert.Contains(t, provider.Status.Violations[0].Detail, "NetworkPolicy not applied")
}

func TestReconcileViolationDetection_ToolCountDrift(t *testing.T) {
	// Provider declares tools.maxCount=5 but status.toolsCount=8
	provider := newTestProvider("tool-drift", "default", &mcpv1alpha1.ProviderCapabilities{
		Tools: &mcpv1alpha1.ToolCapabilitiesSpec{
			MaxCount: 5,
		},
	})
	provider.Status.ToolsCount = 8

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)
	require.Len(t, provider.Status.Violations, 1, "should have one violation for tool count drift")
	assert.Equal(t, "undeclared_tool", provider.Status.Violations[0].Type)
	assert.Equal(t, "medium", provider.Status.Violations[0].Severity)
	assert.Contains(t, provider.Status.Violations[0].Detail, "8 tools")
	assert.Contains(t, provider.Status.Violations[0].Detail, "max declared is 5")
}

func TestReconcileViolationDetection_CapsViolations(t *testing.T) {
	// Pre-fill status.Violations with 99 records, trigger 2 new violations
	provider := newTestProvider("cap-test", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https", CIDR: "10.0.0.0/8"},
			},
		},
		Tools: &mcpv1alpha1.ToolCapabilitiesSpec{
			MaxCount: 5,
		},
	})
	provider.Status.ToolsCount = 10

	// Pre-fill with 99 existing violations
	for i := 0; i < 99; i++ {
		provider.Status.Violations = append(provider.Status.Violations, mcpv1alpha1.ViolationRecord{
			Type:      "egress_denied",
			Detail:    "old violation",
			Severity:  "low",
			Action:    "alert",
			Timestamp: metav1.Now(),
		})
	}
	require.Len(t, provider.Status.Violations, 99)

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)

	// 99 existing + 2 new = 101, capped at 100
	assert.Len(t, provider.Status.Violations, mcpv1alpha1.MaxViolationRecords,
		"violations should be capped at MaxViolationRecords")

	// Newest violations should be at the end
	last := provider.Status.Violations[len(provider.Status.Violations)-1]
	assert.NotEqual(t, "old violation", last.Detail, "newest violation should be at the end")
}

func TestReconcileViolationDetection_MetricsIncrement(t *testing.T) {
	metrics.CapabilityViolationsTotal.Reset()

	provider := newTestProvider("metric-test", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https", CIDR: "10.0.0.0/8"},
			},
		},
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)

	val := testutil.ToFloat64(metrics.CapabilityViolationsTotal.WithLabelValues("default", "metric-test", "capability_drift"))
	assert.Equal(t, float64(1), val, "metric should be incremented for capability_drift violation")
}

func TestReconcileViolationDetection_SetsCondition(t *testing.T) {
	provider := newTestProvider("cond-set", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https", CIDR: "10.0.0.0/8"},
			},
		},
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)

	cond := provider.Status.GetCondition(ConditionViolationDetected)
	require.NotNil(t, cond, "ViolationDetected condition should be set")
	assert.Equal(t, metav1.ConditionTrue, cond.Status)
	assert.Equal(t, "ViolationsFound", cond.Reason)
}

func TestReconcileViolationDetection_ClearsCondition(t *testing.T) {
	// Set ViolationDetected=True on status, then call with no violations
	provider := newTestProvider("cond-clear", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https", CIDR: "10.0.0.0/8"},
			},
		},
	})
	// Mark NetworkPolicyApplied True (so no NP drift)
	provider.Status.SetCondition(ConditionNetworkPolicyApplied, metav1.ConditionTrue,
		"PolicyApplied", "NetworkPolicy applied")
	// Mark ViolationDetected True (previous state)
	provider.Status.SetCondition(ConditionViolationDetected, metav1.ConditionTrue,
		"ViolationsFound", "old violation")

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)

	cond := provider.Status.GetCondition(ConditionViolationDetected)
	require.NotNil(t, cond, "ViolationDetected condition should exist")
	assert.Equal(t, metav1.ConditionFalse, cond.Status, "condition should be cleared when no violations")
	assert.Equal(t, "NoViolations", cond.Reason)
}

func TestReconcileViolationDetection_EnforcementModeDefault(t *testing.T) {
	// Empty enforcementMode should default to "alert"
	provider := newTestProvider("enforce-default", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https", CIDR: "10.0.0.0/8"},
			},
		},
		EnforcementMode: "",
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)
	require.NotEmpty(t, provider.Status.Violations)
	assert.Equal(t, "alert", provider.Status.Violations[0].Action,
		"default enforcement mode should be alert")
}

func TestReconcileViolationDetection_EnforcementModeQuarantine(t *testing.T) {
	provider := newTestProvider("enforce-quarantine", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https", CIDR: "10.0.0.0/8"},
			},
		},
		EnforcementMode: "quarantine",
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	assert.NoError(t, err)
	require.NotEmpty(t, provider.Status.Violations)
	assert.Equal(t, "quarantine", provider.Status.Violations[0].Action,
		"violations should use configured enforcementMode")
}
