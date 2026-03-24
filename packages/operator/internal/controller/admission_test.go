package controller

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
)

// ---------------------------------------------------------------------------
// CEL Admission Logic Validation Tests
//
// The MCPProvider CRD carries an XValidation CEL rule that rejects wildcard
// egress (host: '*') unless the annotation hangar.io/allow-unrestricted-egress
// is set to "true". The rule references self.metadata.annotations which
// requires K8s 1.30+ CEL environment for full API server evaluation.
//
// These tests verify the admission logic encoded in the CEL rule by testing
// the equivalent Go validation function. envtest tests below verify that
// providers without wildcard egress are accepted by the API server.
// ---------------------------------------------------------------------------

// validateCELAdmissionLogic mirrors the CEL XValidation rule from
// MCPProvider types (line 615 of mcpprovider_types.go):
//
//	!has(self.spec.capabilities) ||
//	!has(self.spec.capabilities.network) ||
//	!has(self.spec.capabilities.network.egress) ||
//	!self.spec.capabilities.network.egress.exists(e, e.host == '*') ||
//	(has(self.metadata.annotations) &&
//	 ('hangar.io/allow-unrestricted-egress' in self.metadata.annotations) &&
//	 self.metadata.annotations['hangar.io/allow-unrestricted-egress'] == 'true')
//
// Returns true if the provider passes validation (allowed), false if rejected.
func validateCELAdmissionLogic(provider *mcpv1alpha1.MCPProvider) bool {
	// No capabilities -> pass
	if provider.Spec.Capabilities == nil {
		return true
	}
	// No network -> pass
	if provider.Spec.Capabilities.Network == nil {
		return true
	}
	// No egress rules -> pass
	if provider.Spec.Capabilities.Network.Egress == nil {
		return true
	}
	// Check for wildcard egress
	hasWildcard := false
	for _, e := range provider.Spec.Capabilities.Network.Egress {
		if e.Host == "*" {
			hasWildcard = true
			break
		}
	}
	if !hasWildcard {
		return true
	}
	// Wildcard found -- check annotation
	ann := provider.GetAnnotations()
	if ann == nil {
		return false
	}
	return ann["hangar.io/allow-unrestricted-egress"] == "true"
}

func TestAdmission_WildcardEgressRejected(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cel-wildcard-rejected",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "test:latest",
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					Egress: []mcpv1alpha1.EgressRuleSpec{
						{Host: "*", Port: 443, Protocol: "https"},
					},
				},
			},
		},
	}

	result := validateCELAdmissionLogic(provider)
	assert.False(t, result, "wildcard egress without annotation should be rejected")
}

func TestAdmission_WildcardEgressWithOverrideAccepted(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cel-wildcard-override",
			Namespace: "default",
			Annotations: map[string]string{
				"hangar.io/allow-unrestricted-egress": "true",
			},
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "test:latest",
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					Egress: []mcpv1alpha1.EgressRuleSpec{
						{Host: "*", Port: 443, Protocol: "https"},
					},
				},
			},
		},
	}

	result := validateCELAdmissionLogic(provider)
	assert.True(t, result, "wildcard egress with override annotation should be accepted")
}

func TestAdmission_WrongAnnotationValueRejected(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cel-wrong-annotation",
			Namespace: "default",
			Annotations: map[string]string{
				"hangar.io/allow-unrestricted-egress": "yes",
			},
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "test:latest",
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					Egress: []mcpv1alpha1.EgressRuleSpec{
						{Host: "*", Port: 443, Protocol: "https"},
					},
				},
			},
		},
	}

	result := validateCELAdmissionLogic(provider)
	assert.False(t, result, "wrong annotation value should be rejected")
}

func TestAdmission_NoCapabilitiesAccepted(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cel-no-caps",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "test:latest",
		},
	}

	result := validateCELAdmissionLogic(provider)
	assert.True(t, result, "provider without capabilities should be accepted")
}

func TestAdmission_NonWildcardEgressAccepted(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cel-specific-host",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "test:latest",
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					Egress: []mcpv1alpha1.EgressRuleSpec{
						{Host: "api.example.com", Port: 443, Protocol: "https"},
					},
				},
			},
		},
	}

	result := validateCELAdmissionLogic(provider)
	assert.True(t, result, "specific egress host should be accepted without annotation")
}

func TestAdmission_EmptyEgressAccepted(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cel-empty-egress",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "test:latest",
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					Egress: []mcpv1alpha1.EgressRuleSpec{},
				},
			},
		},
	}

	result := validateCELAdmissionLogic(provider)
	assert.True(t, result, "empty egress list should be accepted")
}

func TestAdmission_ExpectedToolsAccepted(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cel-expected-tools",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "test:latest",
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Tools: &mcpv1alpha1.ToolCapabilitiesSpec{
					MaxCount:      10,
					ExpectedTools: []string{"calculate", "convert"},
				},
			},
		},
	}

	result := validateCELAdmissionLogic(provider)
	assert.True(t, result, "provider with expectedTools should be accepted")
}

// ---------------------------------------------------------------------------
// envtest CEL Integration Tests
//
// These tests verify that the envtest API server accepts providers that
// do NOT trigger CEL rejection. Providers without the capabilities field
// in the CRD schema pass through cleanly.
// ---------------------------------------------------------------------------

func TestAdmission_Envtest_NoCapsCreated(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "envtest-no-caps",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "test:latest",
		},
	}

	err := k8sClient.Create(ctx, provider)
	require.NoError(t, err, "provider without capabilities should be accepted by envtest API server")

	// Cleanup
	_ = k8sClient.Delete(ctx, provider)
}

// ---------------------------------------------------------------------------
// Egress Audit Tests (unit tests using fakeEventRecorder)
// ---------------------------------------------------------------------------

func TestReconcileEgressAudit_WildcardOverrideEmitsWarning(t *testing.T) {
	provider := newTestProvider("egress-audit-wildcard", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "*", Port: 443, Protocol: "https"},
			},
		},
	})
	provider.Annotations = map[string]string{
		"hangar.io/allow-unrestricted-egress": "true",
	}

	r, fakeRec := newViolationTestReconciler(provider)

	r.reconcileEgressAudit(context.Background(), provider)

	require.Len(t, fakeRec.events, 1, "should emit exactly one event")
	assert.Contains(t, fakeRec.events[0], corev1.EventTypeWarning)
	assert.Contains(t, fakeRec.events[0], ReasonUnrestrictedEgressAllowed)
	assert.Contains(t, fakeRec.events[0], "wildcard egress")
}

func TestReconcileEgressAudit_NoWildcardNoEvent(t *testing.T) {
	provider := newTestProvider("egress-audit-specific", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{Host: "api.example.com", Port: 443, Protocol: "https"},
			},
		},
	})

	r, fakeRec := newViolationTestReconciler(provider)

	r.reconcileEgressAudit(context.Background(), provider)

	assert.Empty(t, fakeRec.events, "no event should be emitted for specific hosts")
}

func TestReconcileEgressAudit_NilCapabilitiesNoEvent(t *testing.T) {
	provider := newTestProvider("egress-audit-nil", "default", nil)

	r, fakeRec := newViolationTestReconciler(provider)

	r.reconcileEgressAudit(context.Background(), provider)

	assert.Empty(t, fakeRec.events, "no event should be emitted for nil capabilities")
}
