package controller

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/record"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
	"github.com/mcp-hangar/mcp-hangar/operator/pkg/networkpolicy"
)

// newTestReconciler creates an MCPProviderReconciler backed by a fake client.
func newTestReconciler(objs ...runtime.Object) *MCPProviderReconciler {
	scheme := runtime.NewScheme()
	_ = clientgoscheme.AddToScheme(scheme)
	_ = mcpv1alpha1.AddToScheme(scheme)

	fakeClient := fake.NewClientBuilder().
		WithScheme(scheme).
		WithRuntimeObjects(objs...).
		WithStatusSubresource(&mcpv1alpha1.MCPProvider{}).
		Build()

	return &MCPProviderReconciler{
		Client:   fakeClient,
		Scheme:   scheme,
		Recorder: record.NewFakeRecorder(10),
	}
}

// newTestProvider creates an MCPProvider test fixture with optional capabilities.
func newTestProvider(name, namespace string, caps *mcpv1alpha1.ProviderCapabilities) *mcpv1alpha1.MCPProvider {
	return &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
			UID:       "test-uid-123",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:         mcpv1alpha1.ProviderModeContainer,
			Image:        "test:latest",
			Capabilities: caps,
		},
	}
}

// boolPtr returns a pointer to a bool value.
func boolPtr(b bool) *bool {
	return &b
}

func TestReconcileNetworkPolicy_CreatesPolicy(t *testing.T) {
	provider := newTestProvider("test-provider", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{
					Host:     "api.example.com",
					Port:     443,
					Protocol: "https",
					CIDR:     "10.0.0.0/8",
				},
			},
		},
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileNetworkPolicy(ctx, provider)
	require.NoError(t, err)

	// Verify NetworkPolicy was created
	np := &networkingv1.NetworkPolicy{}
	npKey := types.NamespacedName{
		Name:      networkpolicy.NetworkPolicyName("test-provider"),
		Namespace: "default",
	}
	err = r.Get(ctx, npKey, np)
	require.NoError(t, err)

	// Verify name and namespace
	assert.Equal(t, "mcp-provider-test-provider-egress", np.Name)
	assert.Equal(t, "default", np.Namespace)

	// Verify labels
	assert.Equal(t, "mcp-hangar-operator", np.Labels["app.kubernetes.io/managed-by"])
	assert.Equal(t, "test-provider", np.Labels["mcp-hangar.io/provider"])

	// Verify PodSelector targets the provider
	assert.Equal(t, "test-provider", np.Spec.PodSelector.MatchLabels["mcp-hangar.io/provider"])

	// Verify egress rules exist (DNS + declared rule)
	assert.GreaterOrEqual(t, len(np.Spec.Egress), 2, "should have at least DNS + declared egress rule")

	// Verify policy types
	assert.Contains(t, np.Spec.PolicyTypes, networkingv1.PolicyTypeEgress)

	// Verify condition set on provider status
	cond := provider.Status.GetCondition(ConditionNetworkPolicyApplied)
	require.NotNil(t, cond)
	assert.Equal(t, metav1.ConditionTrue, cond.Status)
	assert.Equal(t, "PolicyApplied", cond.Reason)
}

func TestReconcileNetworkPolicy_NoCapabilities_NoPolicy(t *testing.T) {
	provider := newTestProvider("no-caps-provider", "default", nil)

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileNetworkPolicy(ctx, provider)
	require.NoError(t, err)

	// Verify no NetworkPolicy was created
	np := &networkingv1.NetworkPolicy{}
	npKey := types.NamespacedName{
		Name:      networkpolicy.NetworkPolicyName("no-caps-provider"),
		Namespace: "default",
	}
	err = r.Get(ctx, npKey, np)
	assert.True(t, err != nil, "NetworkPolicy should not exist")

	// Verify condition is False with NoPolicyNeeded
	cond := provider.Status.GetCondition(ConditionNetworkPolicyApplied)
	require.NotNil(t, cond)
	assert.Equal(t, metav1.ConditionFalse, cond.Status)
	assert.Equal(t, "NoPolicyNeeded", cond.Reason)
}

func TestReconcileNetworkPolicy_UpdatesPolicy(t *testing.T) {
	// Start with egress to port 443
	provider := newTestProvider("update-provider", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{
					Host:     "api.example.com",
					Port:     443,
					Protocol: "https",
					CIDR:     "10.0.0.0/8",
				},
			},
		},
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	// First reconcile -- creates
	err := r.reconcileNetworkPolicy(ctx, provider)
	require.NoError(t, err)

	// Verify initial policy
	np := &networkingv1.NetworkPolicy{}
	npKey := types.NamespacedName{
		Name:      networkpolicy.NetworkPolicyName("update-provider"),
		Namespace: "default",
	}
	err = r.Get(ctx, npKey, np)
	require.NoError(t, err)
	initialEgressCount := len(np.Spec.Egress)

	// Update capabilities to add port 5432
	provider.Spec.Capabilities.Network.Egress = append(provider.Spec.Capabilities.Network.Egress,
		mcpv1alpha1.EgressRuleSpec{
			Host:     "db.internal",
			Port:     5432,
			Protocol: "tcp",
			CIDR:     "10.1.0.0/16",
		},
	)

	// Second reconcile -- updates
	err = r.reconcileNetworkPolicy(ctx, provider)
	require.NoError(t, err)

	// Verify updated policy
	err = r.Get(ctx, npKey, np)
	require.NoError(t, err)
	assert.Greater(t, len(np.Spec.Egress), initialEgressCount, "should have more egress rules after update")
}

func TestReconcileNetworkPolicy_DeletesPolicyWhenCapabilitiesRemoved(t *testing.T) {
	// Start with capabilities
	provider := newTestProvider("delete-provider", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{
					Host:     "api.example.com",
					Port:     443,
					Protocol: "https",
					CIDR:     "10.0.0.0/8",
				},
			},
		},
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	// Create the policy
	err := r.reconcileNetworkPolicy(ctx, provider)
	require.NoError(t, err)

	// Verify it exists
	np := &networkingv1.NetworkPolicy{}
	npKey := types.NamespacedName{
		Name:      networkpolicy.NetworkPolicyName("delete-provider"),
		Namespace: "default",
	}
	err = r.Get(ctx, npKey, np)
	require.NoError(t, err)

	// Remove capabilities
	provider.Spec.Capabilities = nil

	// Reconcile again
	err = r.reconcileNetworkPolicy(ctx, provider)
	require.NoError(t, err)

	// Verify NetworkPolicy was deleted
	err = r.Get(ctx, npKey, np)
	assert.True(t, err != nil, "NetworkPolicy should have been deleted")

	// Verify condition is False
	cond := provider.Status.GetCondition(ConditionNetworkPolicyApplied)
	require.NotNil(t, cond)
	assert.Equal(t, metav1.ConditionFalse, cond.Status)
	assert.Equal(t, "NoPolicyNeeded", cond.Reason)
}

func TestReconcileNetworkPolicy_OwnerReference(t *testing.T) {
	provider := newTestProvider("owner-provider", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{
				{
					Host:     "api.example.com",
					Port:     443,
					Protocol: "https",
					CIDR:     "10.0.0.0/8",
				},
			},
		},
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileNetworkPolicy(ctx, provider)
	require.NoError(t, err)

	// Verify OwnerReference
	np := &networkingv1.NetworkPolicy{}
	npKey := types.NamespacedName{
		Name:      networkpolicy.NetworkPolicyName("owner-provider"),
		Namespace: "default",
	}
	err = r.Get(ctx, npKey, np)
	require.NoError(t, err)

	require.Len(t, np.OwnerReferences, 1)
	ownerRef := np.OwnerReferences[0]
	assert.Equal(t, "MCPProvider", ownerRef.Kind)
	assert.Equal(t, "owner-provider", ownerRef.Name)
	assert.Equal(t, provider.UID, ownerRef.UID)
	assert.NotNil(t, ownerRef.Controller)
	assert.True(t, *ownerRef.Controller)
}

func TestReconcileNetworkPolicy_DefaultDenyDNSOnly(t *testing.T) {
	// Empty egress list -- should produce only DNS egress rule (default-deny baseline)
	provider := newTestProvider("dns-only-provider", "default", &mcpv1alpha1.ProviderCapabilities{
		Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
			Egress: []mcpv1alpha1.EgressRuleSpec{},
		},
	})

	r := newTestReconciler(provider)
	ctx := context.Background()

	err := r.reconcileNetworkPolicy(ctx, provider)
	require.NoError(t, err)

	// Verify NetworkPolicy exists
	np := &networkingv1.NetworkPolicy{}
	npKey := types.NamespacedName{
		Name:      networkpolicy.NetworkPolicyName("dns-only-provider"),
		Namespace: "default",
	}
	err = r.Get(ctx, npKey, np)
	require.NoError(t, err)

	// With default DNS allowed and no other rules, should have exactly 1 egress rule (DNS)
	assert.Len(t, np.Spec.Egress, 1, "should have exactly DNS egress rule")

	// Verify it is the DNS rule (has port 53)
	dnsRule := np.Spec.Egress[0]
	require.Len(t, dnsRule.Ports, 2, "DNS rule should have UDP and TCP port 53")

	// Verify policy type is egress
	assert.Contains(t, np.Spec.PolicyTypes, networkingv1.PolicyTypeEgress)

	// Verify condition
	cond := provider.Status.GetCondition(ConditionNetworkPolicyApplied)
	require.NotNil(t, cond)
	assert.Equal(t, metav1.ConditionTrue, cond.Status)
}
