package controller

import (
	"context"
	"fmt"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/tools/record"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
	"github.com/mcp-hangar/mcp-hangar/operator/pkg/metrics"
)

// fakeEventRecorder captures events for synchronous assertion in tests.
// record.NewFakeRecorder uses a channel that is awkward for synchronous checks.
type fakeEventRecorder struct {
	events []string
}

func (f *fakeEventRecorder) Event(object runtime.Object, eventtype, reason, message string) {
	f.events = append(f.events, fmt.Sprintf("%s %s %s", eventtype, reason, message))
}

func (f *fakeEventRecorder) Eventf(object runtime.Object, eventtype, reason, messageFmt string, args ...interface{}) {
	msg := fmt.Sprintf(messageFmt, args...)
	f.events = append(f.events, fmt.Sprintf("%s %s %s", eventtype, reason, msg))
}

func (f *fakeEventRecorder) AnnotatedEventf(object runtime.Object, annotations map[string]string, eventtype, reason, messageFmt string, args ...interface{}) {
	msg := fmt.Sprintf(messageFmt, args...)
	f.events = append(f.events, fmt.Sprintf("%s %s %s", eventtype, reason, msg))
}

var _ record.EventRecorder = (*fakeEventRecorder)(nil)

// newViolationTestReconciler creates a reconciler with a fakeEventRecorder
// so tests can synchronously inspect emitted events.
func newViolationTestReconciler(objs ...runtime.Object) (*MCPProviderReconciler, *fakeEventRecorder) {
	rec := newTestReconciler(objs...)
	fakeRec := &fakeEventRecorder{}
	rec.Recorder = fakeRec
	return rec, fakeRec
}

// TestViolationDetection_FullReconcile_NetworkDrift verifies that a provider
// declaring network capabilities without a NetworkPolicyApplied=True condition
// gets a capability_drift ViolationRecord in CRD status plus a K8s Warning event.
func TestViolationDetection_FullReconcile_NetworkDrift(t *testing.T) {
	provider := newTestProvider("vio-net-drift", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https"},
			},
		},
	})
	// No NetworkPolicyApplied condition -- simulates drift

	r, fakeRec := newViolationTestReconciler(provider)
	ctx := context.Background()

	metrics.CapabilityViolationsTotal.Reset()

	err := r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)

	// Violation recorded in status
	require.Len(t, provider.Status.Violations, 1)
	v := provider.Status.Violations[0]
	assert.Equal(t, "capability_drift", v.Type)
	assert.Equal(t, "high", v.Severity)
	assert.Equal(t, "alert", v.Action) // default enforcement mode
	assert.Contains(t, v.Detail, "NetworkPolicy not applied")

	// ViolationDetected condition set to True
	cond := provider.Status.GetCondition(ConditionViolationDetected)
	require.NotNil(t, cond)
	assert.Equal(t, metav1.ConditionTrue, cond.Status)
	assert.Equal(t, "ViolationsFound", cond.Reason)

	// K8s Warning event emitted
	require.Len(t, fakeRec.events, 1)
	assert.Contains(t, fakeRec.events[0], "ViolationDetected")
	assert.Contains(t, fakeRec.events[0], "capability_drift")
}

// TestViolationDetection_FullReconcile_ToolDrift verifies that a provider with
// more tools than declared maximum gets an undeclared_tool ViolationRecord.
func TestViolationDetection_FullReconcile_ToolDrift(t *testing.T) {
	provider := newTestProvider("vio-tool-drift", "default", &mcpv1alpha1.ProviderCapabilities{
		Tools: &mcpv1alpha1.ToolCapabilitiesSpec{
			MaxCount: 3,
		},
	})
	provider.Status.ToolsCount = 7 // exceeds max of 3

	r, fakeRec := newViolationTestReconciler(provider)
	ctx := context.Background()

	metrics.CapabilityViolationsTotal.Reset()

	err := r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)

	require.Len(t, provider.Status.Violations, 1)
	v := provider.Status.Violations[0]
	assert.Equal(t, "undeclared_tool", v.Type)
	assert.Equal(t, "medium", v.Severity)
	assert.Equal(t, "alert", v.Action)
	assert.Contains(t, v.Detail, "7 tools")
	assert.Contains(t, v.Detail, "max declared is 3")

	// ViolationDetected condition
	cond := provider.Status.GetCondition(ConditionViolationDetected)
	require.NotNil(t, cond)
	assert.Equal(t, metav1.ConditionTrue, cond.Status)

	// K8s event emitted
	require.Len(t, fakeRec.events, 1)
	assert.Contains(t, fakeRec.events[0], "undeclared_tool")
}

// TestViolationDetection_ComplianceClearsCondition verifies that when a
// previously-violating provider becomes compliant, the ViolationDetected
// condition is cleared to False and a ViolationCleared event is emitted.
func TestViolationDetection_ComplianceClearsCondition(t *testing.T) {
	provider := newTestProvider("vio-compliance", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https"},
			},
		},
	})

	r, fakeRec := newViolationTestReconciler(provider)
	ctx := context.Background()

	// First reconcile: no NP condition -> violation detected
	err := r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)
	require.Len(t, provider.Status.Violations, 1)

	cond := provider.Status.GetCondition(ConditionViolationDetected)
	require.NotNil(t, cond)
	assert.Equal(t, metav1.ConditionTrue, cond.Status)

	// Now simulate compliance: set NetworkPolicyApplied=True
	provider.Status.SetCondition(ConditionNetworkPolicyApplied, metav1.ConditionTrue,
		"PolicyApplied", "NetworkPolicy applied successfully")

	// Reset recorder to capture only the clearing event
	fakeRec.events = nil

	// Second reconcile: NP condition is True -> no new violations -> clears condition
	err = r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)

	cond = provider.Status.GetCondition(ConditionViolationDetected)
	require.NotNil(t, cond)
	assert.Equal(t, metav1.ConditionFalse, cond.Status)
	assert.Equal(t, "NoViolations", cond.Reason)

	// ViolationCleared event emitted
	require.Len(t, fakeRec.events, 1)
	assert.Contains(t, fakeRec.events[0], "ViolationCleared")
}

// TestViolationDetection_AccumulatesAcrossCycles verifies that violations
// from multiple reconcile cycles accumulate in CRD status.Violations.
func TestViolationDetection_AccumulatesAcrossCycles(t *testing.T) {
	provider := newTestProvider("vio-accumulate", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https"},
			},
		},
		Tools: &mcpv1alpha1.ToolCapabilitiesSpec{
			MaxCount: 5,
		},
	})
	provider.Status.ToolsCount = 10 // exceeds max

	r, _ := newViolationTestReconciler(provider)
	ctx := context.Background()

	// Cycle 1: both network drift and tool drift detected
	err := r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)
	require.Len(t, provider.Status.Violations, 2)

	// Cycle 2: same violations still present -> appends more
	err = r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)
	assert.Len(t, provider.Status.Violations, 4, "violations should accumulate across cycles")

	// Verify types from both cycles are present
	typeCount := map[string]int{}
	for _, v := range provider.Status.Violations {
		typeCount[v.Type]++
	}
	assert.Equal(t, 2, typeCount["capability_drift"])
	assert.Equal(t, 2, typeCount["undeclared_tool"])
}

// TestViolationDetection_EnforcementModePropagatesToAction verifies that
// the capabilities.enforcementMode field propagates to ViolationRecord.Action.
func TestViolationDetection_EnforcementModePropagatesToAction(t *testing.T) {
	tests := []struct {
		name            string
		enforcementMode string
		expectedAction  string
	}{
		{"alert mode", "alert", "alert"},
		{"block mode", "block", "block"},
		{"quarantine mode", "quarantine", "quarantine"},
		{"empty defaults to alert", "", "alert"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			provider := newTestProvider("vio-enforce", "default", &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					Egress: []mcpv1alpha1.EgressRuleSpec{
						{Host: "api.example.com", Port: 443, Protocol: "https"},
					},
				},
				EnforcementMode: tt.enforcementMode,
			})

			r, _ := newViolationTestReconciler(provider)
			ctx := context.Background()

			err := r.reconcileViolationDetection(ctx, provider)
			require.NoError(t, err)
			require.Len(t, provider.Status.Violations, 1)
			assert.Equal(t, tt.expectedAction, provider.Status.Violations[0].Action)
		})
	}
}

// TestViolationDetection_MaxViolationsCapped verifies that violations are capped
// at MaxViolationRecords, with oldest entries evicted first.
func TestViolationDetection_MaxViolationsCapped(t *testing.T) {
	provider := newTestProvider("vio-cap", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https"},
			},
		},
	})

	// Pre-fill with MaxViolationRecords - 1 entries
	for i := 0; i < mcpv1alpha1.MaxViolationRecords-1; i++ {
		provider.Status.Violations = append(provider.Status.Violations, mcpv1alpha1.ViolationRecord{
			Type:      "old_violation",
			Detail:    fmt.Sprintf("pre-existing-%d", i),
			Severity:  "low",
			Action:    "alert",
			Timestamp: metav1.Now(),
		})
	}

	r, _ := newViolationTestReconciler(provider)
	ctx := context.Background()

	// One more reconcile adds 1 violation -> exactly at cap
	err := r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)
	assert.Len(t, provider.Status.Violations, mcpv1alpha1.MaxViolationRecords)

	// Another reconcile pushes over cap -> oldest evicted
	err = r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)
	assert.Len(t, provider.Status.Violations, mcpv1alpha1.MaxViolationRecords)

	// Most recent entry should be capability_drift, not old_violation
	last := provider.Status.Violations[len(provider.Status.Violations)-1]
	assert.Equal(t, "capability_drift", last.Type)
}

// TestViolationDetection_NilCapabilities_Noop verifies that a provider
// without capabilities spec triggers no violations and no status changes.
func TestViolationDetection_NilCapabilities_Noop(t *testing.T) {
	provider := newTestProvider("vio-nil-caps", "default", nil)

	r, fakeRec := newViolationTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileViolationDetection(ctx, provider)
	require.NoError(t, err)
	assert.Empty(t, provider.Status.Violations)
	assert.Nil(t, provider.Status.GetCondition(ConditionViolationDetected))
	assert.Empty(t, fakeRec.events, "no events should be emitted")
}
