package controller

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
)

// waitForGroupCondition polls until the specified condition reaches the expected status
func waitForGroupCondition(t *testing.T, name, namespace, condType string, status metav1.ConditionStatus) {
	t.Helper()
	require.Eventually(t, func() bool {
		group := &mcpv1alpha1.MCPProviderGroup{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, group); err != nil {
			return false
		}
		for _, c := range group.Status.Conditions {
			if c.Type == condType && c.Status == status {
				return true
			}
		}
		return false
	}, 10*time.Second, 250*time.Millisecond, "condition %s=%s not met for group %s/%s", condType, status, namespace, name)
}

// waitForGroupProviderCount polls until the group status shows the expected provider count
func waitForGroupProviderCount(t *testing.T, name, namespace string, count int32) {
	t.Helper()
	require.Eventually(t, func() bool {
		group := &mcpv1alpha1.MCPProviderGroup{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, group); err != nil {
			return false
		}
		return group.Status.ProviderCount == count
	}, 10*time.Second, 250*time.Millisecond, "expected provider count %d for group %s/%s", count, namespace, name)
}

// createProvider creates an MCPProvider and sets its status state via the status subresource
func createProvider(t *testing.T, name, namespace string, state mcpv1alpha1.ProviderState, labels map[string]string) *mcpv1alpha1.MCPProvider {
	t.Helper()
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
			Labels:    labels,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode: mcpv1alpha1.ProviderModeRemote,
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Update status subresource to set state
	provider.Status.State = state
	require.NoError(t, k8sClient.Status().Update(ctx, provider))
	return provider
}

// createNamespace creates a namespace for test isolation
func createNamespace(t *testing.T, name string) *corev1.Namespace {
	t.Helper()
	ns := &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{Name: name},
	}
	require.NoError(t, k8sClient.Create(ctx, ns))
	return ns
}

func TestMCPProviderGroup_LabelSelection(t *testing.T) {
	ns := createNamespace(t, "test-group-label-sel")
	defer k8sClient.Delete(ctx, ns)

	// Create group selecting app=web
	group := &mcpv1alpha1.MCPProviderGroup{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "label-group",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderGroupSpec{
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{"app": "web"},
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, group))

	webLabels := map[string]string{"app": "web"}
	createProvider(t, "web-ready", ns.Name, mcpv1alpha1.ProviderStateReady, webLabels)
	createProvider(t, "web-degraded", ns.Name, mcpv1alpha1.ProviderStateDegraded, webLabels)
	// This one should NOT be selected
	createProvider(t, "api-ready", ns.Name, mcpv1alpha1.ProviderStateReady, map[string]string{"app": "api"})

	// Wait for group to reconcile with 2 providers
	waitForGroupProviderCount(t, "label-group", ns.Name, 2)

	// Verify counts
	result := &mcpv1alpha1.MCPProviderGroup{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: "label-group", Namespace: ns.Name}, result))
	assert.Equal(t, int32(2), result.Status.ProviderCount)
	assert.Equal(t, int32(1), result.Status.ReadyCount)
	assert.Equal(t, int32(1), result.Status.DegradedCount)

	// Verify unmatched provider is not in the list
	for _, p := range result.Status.Providers {
		assert.NotEqual(t, "api-ready", p.Name, "unmatched provider should not be in group")
	}
}

func TestMCPProviderGroup_StatusAggregation(t *testing.T) {
	ns := createNamespace(t, "test-group-status-agg")
	defer k8sClient.Delete(ctx, ns)

	groupLabels := map[string]string{"tier": "backend"}

	group := &mcpv1alpha1.MCPProviderGroup{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "agg-group",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderGroupSpec{
			Selector: &metav1.LabelSelector{
				MatchLabels: groupLabels,
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, group))

	// Create providers in various states
	createProvider(t, "ready-1", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)
	createProvider(t, "ready-2", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)
	createProvider(t, "degraded-1", ns.Name, mcpv1alpha1.ProviderStateDegraded, groupLabels)
	createProvider(t, "dead-1", ns.Name, mcpv1alpha1.ProviderStateDead, groupLabels)
	createProvider(t, "cold-1", ns.Name, mcpv1alpha1.ProviderStateCold, groupLabels)

	waitForGroupProviderCount(t, "agg-group", ns.Name, 5)

	result := &mcpv1alpha1.MCPProviderGroup{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: "agg-group", Namespace: ns.Name}, result))

	assert.Equal(t, int32(5), result.Status.ProviderCount)
	assert.Equal(t, int32(2), result.Status.ReadyCount)
	assert.Equal(t, int32(1), result.Status.DegradedCount)
	assert.Equal(t, int32(1), result.Status.DeadCount)
	assert.Equal(t, int32(1), result.Status.ColdCount)
	assert.Len(t, result.Status.Providers, 5)
}

func TestMCPProviderGroup_HealthPolicyThreshold(t *testing.T) {
	ns := createNamespace(t, "test-group-health-thresh")
	defer k8sClient.Delete(ctx, ns)

	groupLabels := map[string]string{"pool": "threshold"}
	minPct := int32(60)

	group := &mcpv1alpha1.MCPProviderGroup{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "threshold-group",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderGroupSpec{
			Selector: &metav1.LabelSelector{
				MatchLabels: groupLabels,
			},
			HealthPolicy: &mcpv1alpha1.HealthPolicy{
				MinHealthyPercentage: minPct,
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, group))

	// 3 ready + 2 dead = 60% healthy (meets threshold exactly)
	createProvider(t, "h-ready-1", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)
	createProvider(t, "h-ready-2", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)
	createProvider(t, "h-ready-3", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)
	createProvider(t, "h-dead-1", ns.Name, mcpv1alpha1.ProviderStateDead, groupLabels)
	createProvider(t, "h-dead-2", ns.Name, mcpv1alpha1.ProviderStateDead, groupLabels)

	// Threshold met at exactly 60%
	waitForGroupCondition(t, "threshold-group", ns.Name, ConditionReady, metav1.ConditionTrue)
	// Dead providers exist so Degraded is True
	waitForGroupCondition(t, "threshold-group", ns.Name, ConditionDegraded, metav1.ConditionTrue)
	// At least 1 ready so Available is True
	waitForGroupCondition(t, "threshold-group", ns.Name, ConditionAvailable, metav1.ConditionTrue)
}

func TestMCPProviderGroup_ZeroMembers(t *testing.T) {
	ns := createNamespace(t, "test-group-zero-members")
	defer k8sClient.Delete(ctx, ns)

	group := &mcpv1alpha1.MCPProviderGroup{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "empty-group",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderGroupSpec{
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{"nonexistent": "label"},
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, group))

	// No providers match, so Ready=Unknown with reason NoProviders
	waitForGroupCondition(t, "empty-group", ns.Name, ConditionReady, metav1.ConditionUnknown)
	waitForGroupCondition(t, "empty-group", ns.Name, ConditionAvailable, metav1.ConditionFalse)
	waitForGroupCondition(t, "empty-group", ns.Name, ConditionDegraded, metav1.ConditionFalse)

	// Verify reason
	result := &mcpv1alpha1.MCPProviderGroup{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: "empty-group", Namespace: ns.Name}, result))
	for _, c := range result.Status.Conditions {
		if c.Type == ConditionReady {
			assert.Equal(t, "NoProviders", c.Reason)
			break
		}
	}
}

func TestMCPProviderGroup_CoexistingReadyDegraded(t *testing.T) {
	ns := createNamespace(t, "test-group-coexist")
	defer k8sClient.Delete(ctx, ns)

	groupLabels := map[string]string{"pool": "coexist"}

	group := &mcpv1alpha1.MCPProviderGroup{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "coexist-group",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderGroupSpec{
			Selector: &metav1.LabelSelector{
				MatchLabels: groupLabels,
			},
			HealthPolicy: &mcpv1alpha1.HealthPolicy{
				MinHealthyPercentage: 30,
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, group))

	// 2 ready + 3 degraded = 40% healthy (above 30% threshold)
	createProvider(t, "co-ready-1", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)
	createProvider(t, "co-ready-2", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)
	createProvider(t, "co-deg-1", ns.Name, mcpv1alpha1.ProviderStateDegraded, groupLabels)
	createProvider(t, "co-deg-2", ns.Name, mcpv1alpha1.ProviderStateDegraded, groupLabels)
	createProvider(t, "co-deg-3", ns.Name, mcpv1alpha1.ProviderStateDegraded, groupLabels)

	// Both Ready=True and Degraded=True simultaneously
	waitForGroupCondition(t, "coexist-group", ns.Name, ConditionReady, metav1.ConditionTrue)
	waitForGroupCondition(t, "coexist-group", ns.Name, ConditionDegraded, metav1.ConditionTrue)
}

func TestMCPProviderGroup_Deletion(t *testing.T) {
	ns := createNamespace(t, "test-group-deletion")
	defer k8sClient.Delete(ctx, ns)

	groupLabels := map[string]string{"pool": "deleteme"}

	group := &mcpv1alpha1.MCPProviderGroup{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "del-group",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderGroupSpec{
			Selector: &metav1.LabelSelector{
				MatchLabels: groupLabels,
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, group))

	createProvider(t, "del-ready-1", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)
	createProvider(t, "del-ready-2", ns.Name, mcpv1alpha1.ProviderStateReady, groupLabels)

	// Wait for group to reconcile
	waitForGroupProviderCount(t, "del-group", ns.Name, 2)

	// Delete the group
	require.NoError(t, k8sClient.Delete(ctx, group))

	// Wait for group to be fully removed (finalizer cleaned up)
	require.Eventually(t, func() bool {
		err := k8sClient.Get(ctx, types.NamespacedName{Name: "del-group", Namespace: ns.Name}, &mcpv1alpha1.MCPProviderGroup{})
		return err != nil // NotFound expected
	}, 10*time.Second, 250*time.Millisecond, "group should be deleted")

	// Providers should still exist (group does not own providers)
	provider1 := &mcpv1alpha1.MCPProvider{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: "del-ready-1", Namespace: ns.Name}, provider1))
	provider2 := &mcpv1alpha1.MCPProvider{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: "del-ready-2", Namespace: ns.Name}, provider2))
}
