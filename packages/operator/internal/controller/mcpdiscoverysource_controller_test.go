package controller

import (
	"fmt"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
)

// waitForDiscoveryCondition polls until the specified condition reaches the expected status on an MCPDiscoverySource.
func waitForDiscoveryCondition(t *testing.T, name, namespace, condType string, status metav1.ConditionStatus) {
	t.Helper()
	require.Eventually(t, func() bool {
		source := &mcpv1alpha1.MCPDiscoverySource{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, source); err != nil {
			return false
		}
		for _, c := range source.Status.Conditions {
			if c.Type == condType && c.Status == status {
				return true
			}
		}
		return false
	}, 15*time.Second, 250*time.Millisecond, "condition %s=%s not met for source %s/%s", condType, status, namespace, name)
}

// waitForManagedProviderCount polls until the number of MCPProviders managed by the given source equals count.
func waitForManagedProviderCount(t *testing.T, sourceName, namespace string, count int) {
	t.Helper()
	require.Eventually(t, func() bool {
		providerList := &mcpv1alpha1.MCPProviderList{}
		if err := k8sClient.List(ctx, providerList,
			client.InNamespace(namespace),
			client.MatchingLabels{LabelDiscoveryManagedBy: sourceName},
		); err != nil {
			return false
		}
		return len(providerList.Items) == count
	}, 15*time.Second, 250*time.Millisecond, "expected %d managed providers for source %s", count, sourceName)
}

// createConfigMap creates a ConfigMap with provider YAML definitions.
func createConfigMap(t *testing.T, name, namespace, yamlData string) *corev1.ConfigMap {
	t.Helper()
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
		},
		Data: map[string]string{
			"providers.yaml": yamlData,
		},
	}
	require.NoError(t, k8sClient.Create(ctx, cm))
	return cm
}

// createDiscoverySource creates an MCPDiscoverySource with ConfigMap type.
func createDiscoverySource(t *testing.T, name, namespace, configMapName string, mode mcpv1alpha1.DiscoveryMode) *mcpv1alpha1.MCPDiscoverySource {
	t.Helper()
	source := &mcpv1alpha1.MCPDiscoverySource{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
		},
		Spec: mcpv1alpha1.MCPDiscoverySourceSpec{
			Type: mcpv1alpha1.DiscoveryTypeConfigMap,
			Mode: mode,
			ConfigMapRef: &mcpv1alpha1.ConfigMapReference{
				Name: configMapName,
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, source))
	return source
}

// twoProviderYAML returns YAML for two remote providers.
const twoProviderYAML = `provider-a:
  mode: remote
  endpoint: http://provider-a:8080
provider-b:
  mode: remote
  endpoint: http://provider-b:8080
`

// oneProviderYAML returns YAML for a single remote provider.
const oneProviderYAML = `provider-a:
  mode: remote
  endpoint: http://provider-a:8080
`

// threeProviderYAML returns YAML for three remote providers.
const threeProviderYAML = `provider-a:
  mode: remote
  endpoint: http://provider-a:8080
provider-b:
  mode: remote
  endpoint: http://provider-b:8080
provider-c:
  mode: remote
  endpoint: http://provider-c:8080
`

func TestMCPDiscoverySource_ConfigMapDiscovery(t *testing.T) {
	ns := createNamespace(t, "test-disc-configmap")
	defer k8sClient.Delete(ctx, ns)

	sourceName := "cm-source"
	cmName := "providers-cm"

	createConfigMap(t, cmName, ns.Name, twoProviderYAML)
	createDiscoverySource(t, sourceName, ns.Name, cmName, mcpv1alpha1.DiscoveryModeAdditive)

	// Wait for Synced=True
	waitForDiscoveryCondition(t, sourceName, ns.Name, ConditionSynced, metav1.ConditionTrue)

	// Wait for 2 managed providers
	waitForManagedProviderCount(t, sourceName, ns.Name, 2)

	// Verify providers have correct labels and owner references
	providerList := &mcpv1alpha1.MCPProviderList{}
	require.NoError(t, k8sClient.List(ctx, providerList,
		client.InNamespace(ns.Name),
		client.MatchingLabels{LabelDiscoveryManagedBy: sourceName},
	))
	assert.Len(t, providerList.Items, 2)

	for _, p := range providerList.Items {
		// Check managed-by label
		assert.Equal(t, sourceName, p.Labels[LabelDiscoveryManagedBy],
			"provider %s should have managed-by label", p.Name)

		// Check owner reference
		require.Len(t, p.OwnerReferences, 1, "provider %s should have 1 owner ref", p.Name)
		assert.Equal(t, "MCPDiscoverySource", p.OwnerReferences[0].Kind)
		assert.Equal(t, sourceName, p.OwnerReferences[0].Name)
	}

	// Verify DiscoveredCount in status
	source := &mcpv1alpha1.MCPDiscoverySource{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: sourceName, Namespace: ns.Name}, source))
	assert.Equal(t, int32(2), source.Status.DiscoveredCount)
}

func TestMCPDiscoverySource_AdditiveNeverDeletes(t *testing.T) {
	ns := createNamespace(t, "test-disc-additive")
	defer k8sClient.Delete(ctx, ns)

	sourceName := "additive-source"
	cmName := "additive-cm"

	cm := createConfigMap(t, cmName, ns.Name, twoProviderYAML)
	createDiscoverySource(t, sourceName, ns.Name, cmName, mcpv1alpha1.DiscoveryModeAdditive)

	// Wait for 2 providers
	waitForManagedProviderCount(t, sourceName, ns.Name, 2)

	// Update ConfigMap to have only 1 provider
	cm.Data["providers.yaml"] = oneProviderYAML
	require.NoError(t, k8sClient.Update(ctx, cm))

	// Trigger reconcile by annotating the source (retry on conflict)
	require.Eventually(t, func() bool {
		source := &mcpv1alpha1.MCPDiscoverySource{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: sourceName, Namespace: ns.Name}, source); err != nil {
			return false
		}
		if source.Annotations == nil {
			source.Annotations = make(map[string]string)
		}
		source.Annotations["trigger-sync"] = "1"
		return k8sClient.Update(ctx, source) == nil
	}, 10*time.Second, 250*time.Millisecond, "failed to annotate source to trigger sync")

	// Wait for requeue/reconcile to process the update
	time.Sleep(3 * time.Second)

	// Additive mode: 2 providers should still exist (never deletes)
	providerList := &mcpv1alpha1.MCPProviderList{}
	require.NoError(t, k8sClient.List(ctx, providerList,
		client.InNamespace(ns.Name),
		client.MatchingLabels{LabelDiscoveryManagedBy: sourceName},
	))
	assert.Len(t, providerList.Items, 2, "additive mode should never delete providers")
}

func TestMCPDiscoverySource_AuthoritativeDeletes(t *testing.T) {
	ns := createNamespace(t, "test-disc-authorit")
	defer k8sClient.Delete(ctx, ns)

	sourceName := "auth-source"
	cmName := "auth-cm"

	cm := createConfigMap(t, cmName, ns.Name, twoProviderYAML)
	createDiscoverySource(t, sourceName, ns.Name, cmName, mcpv1alpha1.DiscoveryModeAuthoritative)

	// Wait for 2 providers
	waitForManagedProviderCount(t, sourceName, ns.Name, 2)

	// Update ConfigMap to have only 1 provider (remove provider-b)
	cm.Data["providers.yaml"] = oneProviderYAML
	require.NoError(t, k8sClient.Update(ctx, cm))

	// Trigger reconcile by annotating the source (retry on conflict)
	require.Eventually(t, func() bool {
		source := &mcpv1alpha1.MCPDiscoverySource{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: sourceName, Namespace: ns.Name}, source); err != nil {
			return false
		}
		if source.Annotations == nil {
			source.Annotations = make(map[string]string)
		}
		source.Annotations["trigger-sync"] = "1"
		return k8sClient.Update(ctx, source) == nil
	}, 10*time.Second, 250*time.Millisecond, "failed to annotate source to trigger sync")

	// Wait for managed provider count to drop to 1
	waitForManagedProviderCount(t, sourceName, ns.Name, 1)

	// Verify only provider-a remains
	providerList := &mcpv1alpha1.MCPProviderList{}
	require.NoError(t, k8sClient.List(ctx, providerList,
		client.InNamespace(ns.Name),
		client.MatchingLabels{LabelDiscoveryManagedBy: sourceName},
	))
	require.Len(t, providerList.Items, 1)
	expectedNameA := fmt.Sprintf("%s-provider-a", sourceName)
	assert.Equal(t, expectedNameA, providerList.Items[0].Name,
		"only provider-a should remain after authoritative deletion")
}

func TestMCPDiscoverySource_OwnerReferences(t *testing.T) {
	ns := createNamespace(t, "test-disc-ownerref")
	defer k8sClient.Delete(ctx, ns)

	sourceName := "owner-source"
	cmName := "owner-cm"

	createConfigMap(t, cmName, ns.Name, twoProviderYAML)
	source := createDiscoverySource(t, sourceName, ns.Name, cmName, mcpv1alpha1.DiscoveryModeAdditive)

	// Wait for providers
	waitForManagedProviderCount(t, sourceName, ns.Name, 2)

	// Verify owner references on each provider
	providerList := &mcpv1alpha1.MCPProviderList{}
	require.NoError(t, k8sClient.List(ctx, providerList,
		client.InNamespace(ns.Name),
		client.MatchingLabels{LabelDiscoveryManagedBy: sourceName},
	))

	// Refresh source to get UID
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: sourceName, Namespace: ns.Name}, source))

	for _, p := range providerList.Items {
		require.Len(t, p.OwnerReferences, 1, "provider %s should have exactly 1 owner ref", p.Name)
		ownerRef := p.OwnerReferences[0]
		assert.Equal(t, "MCPDiscoverySource", ownerRef.Kind)
		assert.Equal(t, sourceName, ownerRef.Name)
		assert.Equal(t, source.UID, ownerRef.UID)
		assert.Equal(t, sourceName, p.Labels[LabelDiscoveryManagedBy])
	}

	// Delete the source -- providers should be GC'd via owner references
	require.NoError(t, k8sClient.Delete(ctx, source))

	// Wait for source to be fully deleted
	require.Eventually(t, func() bool {
		err := k8sClient.Get(ctx, types.NamespacedName{Name: sourceName, Namespace: ns.Name}, &mcpv1alpha1.MCPDiscoverySource{})
		return err != nil
	}, 15*time.Second, 250*time.Millisecond, "source should be deleted")

	// Providers should be cleaned up (by finalizer or GC)
	require.Eventually(t, func() bool {
		list := &mcpv1alpha1.MCPProviderList{}
		if err := k8sClient.List(ctx, list,
			client.InNamespace(ns.Name),
			client.MatchingLabels{LabelDiscoveryManagedBy: sourceName},
		); err != nil {
			return false
		}
		return len(list.Items) == 0
	}, 15*time.Second, 250*time.Millisecond, "managed providers should be cleaned up after source deletion")
}

func TestMCPDiscoverySource_PausedFreeze(t *testing.T) {
	ns := createNamespace(t, "test-disc-paused")
	defer k8sClient.Delete(ctx, ns)

	sourceName := "paused-source"
	cmName := "paused-cm"

	cm := createConfigMap(t, cmName, ns.Name, twoProviderYAML)
	createDiscoverySource(t, sourceName, ns.Name, cmName, mcpv1alpha1.DiscoveryModeAdditive)

	// Wait for initial 2 providers
	waitForManagedProviderCount(t, sourceName, ns.Name, 2)

	// Set paused=true
	source := &mcpv1alpha1.MCPDiscoverySource{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: sourceName, Namespace: ns.Name}, source))
	source.Spec.Paused = true
	require.NoError(t, k8sClient.Update(ctx, source))

	// Wait for Paused=True condition
	waitForDiscoveryCondition(t, sourceName, ns.Name, ConditionPaused, metav1.ConditionTrue)

	// Update ConfigMap to add provider-c
	cm.Data["providers.yaml"] = threeProviderYAML
	require.NoError(t, k8sClient.Update(ctx, cm))

	// Wait enough time for at least one requeue cycle
	time.Sleep(3 * time.Second)

	// No new provider-c should be created while paused
	providerList := &mcpv1alpha1.MCPProviderList{}
	require.NoError(t, k8sClient.List(ctx, providerList,
		client.InNamespace(ns.Name),
		client.MatchingLabels{LabelDiscoveryManagedBy: sourceName},
	))
	assert.Len(t, providerList.Items, 2, "paused source should not create new providers")

	// Unpause
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: sourceName, Namespace: ns.Name}, source))
	source.Spec.Paused = false
	require.NoError(t, k8sClient.Update(ctx, source))

	// Wait for provider-c to appear
	waitForManagedProviderCount(t, sourceName, ns.Name, 3)
}

func TestMCPDiscoverySource_Deletion(t *testing.T) {
	ns := createNamespace(t, "test-disc-deletion")
	defer k8sClient.Delete(ctx, ns)

	sourceName := "del-source"
	cmName := "del-cm"

	createConfigMap(t, cmName, ns.Name, twoProviderYAML)
	source := createDiscoverySource(t, sourceName, ns.Name, cmName, mcpv1alpha1.DiscoveryModeAdditive)

	// Wait for providers
	waitForManagedProviderCount(t, sourceName, ns.Name, 2)

	// Delete the source
	require.NoError(t, k8sClient.Delete(ctx, source))

	// Wait for source to be fully removed (finalizer cleanup)
	require.Eventually(t, func() bool {
		err := k8sClient.Get(ctx, types.NamespacedName{Name: sourceName, Namespace: ns.Name}, &mcpv1alpha1.MCPDiscoverySource{})
		return err != nil
	}, 15*time.Second, 250*time.Millisecond, "source should be deleted after finalizer cleanup")

	// Managed providers should be cleaned up
	require.Eventually(t, func() bool {
		list := &mcpv1alpha1.MCPProviderList{}
		if err := k8sClient.List(ctx, list,
			client.InNamespace(ns.Name),
			client.MatchingLabels{LabelDiscoveryManagedBy: sourceName},
		); err != nil {
			return false
		}
		return len(list.Items) == 0
	}, 15*time.Second, 250*time.Millisecond, "managed providers should be cleaned up after source deletion")
}
