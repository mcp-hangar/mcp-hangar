package controller

import (
	"fmt"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
	"github.com/mcp-hangar/mcp-hangar/operator/pkg/networkpolicy"
)

// waitForProviderState polls until the MCPProvider reaches the expected state.
func waitForProviderState(t *testing.T, name, namespace string, state mcpv1alpha1.ProviderState) {
	t.Helper()
	require.Eventually(t, func() bool {
		p := &mcpv1alpha1.MCPProvider{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, p); err != nil {
			return false
		}
		return p.Status.State == state
	}, 15*time.Second, 250*time.Millisecond, "provider %s/%s did not reach state %s", namespace, name, state)
}

// waitForProviderCondition polls until the specified condition reaches the expected status.
func waitForProviderCondition(t *testing.T, name, namespace, condType string, status metav1.ConditionStatus) {
	t.Helper()
	require.Eventually(t, func() bool {
		p := &mcpv1alpha1.MCPProvider{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, p); err != nil {
			return false
		}
		cond := p.Status.GetCondition(condType)
		return cond != nil && cond.Status == status
	}, 15*time.Second, 250*time.Millisecond, "condition %s=%s not met for provider %s/%s", condType, status, namespace, name)
}

// waitForPodExists polls until the Pod with the given name exists.
func waitForPodExists(t *testing.T, name, namespace string) {
	t.Helper()
	require.Eventually(t, func() bool {
		pod := &corev1.Pod{}
		return k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, pod) == nil
	}, 15*time.Second, 250*time.Millisecond, "pod %s/%s not found", namespace, name)
}

// waitForPodNotExists polls until the Pod with the given name is gone.
func waitForPodNotExists(t *testing.T, name, namespace string) {
	t.Helper()
	require.Eventually(t, func() bool {
		pod := &corev1.Pod{}
		err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace}, pod)
		return err != nil
	}, 15*time.Second, 250*time.Millisecond, "pod %s/%s still exists", namespace, name)
}

func TestMCPProvider_ContainerMode_CreatesPod(t *testing.T) {
	ns := createNamespace(t, "test-provider-create")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-container",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "busybox:latest",
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Wait for Pod to be created
	podName := "mcp-provider-test-container"
	waitForPodExists(t, podName, ns.Name)

	// Verify Pod spec
	pod := &corev1.Pod{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: podName, Namespace: ns.Name}, pod))
	assert.Equal(t, "busybox:latest", pod.Spec.Containers[0].Image)
	assert.Equal(t, corev1.RestartPolicyNever, pod.Spec.RestartPolicy)

	// Verify owner reference
	require.Len(t, pod.OwnerReferences, 1)
	assert.Equal(t, "MCPProvider", pod.OwnerReferences[0].Kind)
	assert.Equal(t, "test-container", pod.OwnerReferences[0].Name)

	// Provider should be in Initializing state (envtest has no kubelet)
	waitForProviderState(t, "test-container", ns.Name, mcpv1alpha1.ProviderStateInitializing)
}

func TestMCPProvider_ContainerMode_NoImage_MarksDead(t *testing.T) {
	ns := createNamespace(t, "test-provider-noimg")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "no-image",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode: mcpv1alpha1.ProviderModeContainer,
			// No image
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Should be marked Dead with InvalidSpec
	waitForProviderState(t, "no-image", ns.Name, mcpv1alpha1.ProviderStateDead)
	waitForProviderCondition(t, "no-image", ns.Name, ConditionReady, metav1.ConditionFalse)
}

func TestMCPProvider_ColdStart_ReplicasZero(t *testing.T) {
	ns := createNamespace(t, "test-provider-cold")
	defer k8sClient.Delete(ctx, ns)

	replicas := int32(0)
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cold-provider",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:     mcpv1alpha1.ProviderModeContainer,
			Image:    "busybox:latest",
			Replicas: &replicas,
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Should be Cold, no Pod created
	waitForProviderState(t, "cold-provider", ns.Name, mcpv1alpha1.ProviderStateCold)

	// Verify no Pod exists
	pod := &corev1.Pod{}
	podName := "mcp-provider-cold-provider"
	err := k8sClient.Get(ctx, types.NamespacedName{Name: podName, Namespace: ns.Name}, pod)
	assert.Error(t, err, "Pod should not exist for cold provider")
}

func TestMCPProvider_RemoteMode_NoEndpoint_MarksDead(t *testing.T) {
	ns := createNamespace(t, "test-provider-remote")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "remote-no-ep",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode: mcpv1alpha1.ProviderModeRemote,
			// No endpoint
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	waitForProviderState(t, "remote-no-ep", ns.Name, mcpv1alpha1.ProviderStateDead)
}

func TestMCPProvider_RemoteMode_WithEndpoint_AssumedReady(t *testing.T) {
	ns := createNamespace(t, "test-provider-remote-ok")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "remote-ok",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:     mcpv1alpha1.ProviderModeRemote,
			Endpoint: "http://example.com:8080",
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Without HangarClient, remote providers are assumed ready
	waitForProviderState(t, "remote-ok", ns.Name, mcpv1alpha1.ProviderStateReady)

	// Verify endpoint is propagated to status
	result := &mcpv1alpha1.MCPProvider{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: "remote-ok", Namespace: ns.Name}, result))
	assert.Equal(t, "http://example.com:8080", result.Status.Endpoint)
}

func TestMCPProvider_SpecDrift_RecreatesPod(t *testing.T) {
	ns := createNamespace(t, "test-provider-drift")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "drift-test",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "busybox:1.0",
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	podName := "mcp-provider-drift-test"
	waitForPodExists(t, podName, ns.Name)

	// Record original Pod UID
	originalPod := &corev1.Pod{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: podName, Namespace: ns.Name}, originalPod))
	originalUID := originalPod.UID

	// Update the image (spec change)
	require.Eventually(t, func() bool {
		p := &mcpv1alpha1.MCPProvider{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: "drift-test", Namespace: ns.Name}, p); err != nil {
			return false
		}
		p.Spec.Image = "busybox:2.0"
		return k8sClient.Update(ctx, p) == nil
	}, 10*time.Second, 250*time.Millisecond, "failed to update provider image")

	// Wait for old Pod to be deleted
	waitForPodNotExists(t, podName, ns.Name)

	// Wait for new Pod to be created
	waitForPodExists(t, podName, ns.Name)

	// Verify new Pod has updated image
	newPod := &corev1.Pod{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: podName, Namespace: ns.Name}, newPod))
	assert.Equal(t, "busybox:2.0", newPod.Spec.Containers[0].Image)
	assert.NotEqual(t, originalUID, newPod.UID, "new Pod should have different UID")
}

func TestMCPProvider_NetworkPolicy_CreatedForCapabilities(t *testing.T) {
	ns := createNamespace(t, "test-provider-np")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "np-test",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "busybox:latest",
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
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
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Wait for NetworkPolicy to be created
	npName := networkpolicy.NetworkPolicyName("np-test")
	require.Eventually(t, func() bool {
		np := &networkingv1.NetworkPolicy{}
		return k8sClient.Get(ctx, types.NamespacedName{Name: npName, Namespace: ns.Name}, np) == nil
	}, 15*time.Second, 250*time.Millisecond, "NetworkPolicy not created")

	// Verify NetworkPolicy
	np := &networkingv1.NetworkPolicy{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: npName, Namespace: ns.Name}, np))
	assert.Contains(t, np.Spec.PolicyTypes, networkingv1.PolicyTypeEgress)
	assert.Equal(t, "np-test", np.Spec.PodSelector.MatchLabels["mcp-hangar.io/provider"])

	// Verify condition on provider
	waitForProviderCondition(t, "np-test", ns.Name, ConditionNetworkPolicyApplied, metav1.ConditionTrue)
}

func TestMCPProvider_Deletion_CleansPodAndFinalizer(t *testing.T) {
	ns := createNamespace(t, "test-provider-del")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "del-test",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "busybox:latest",
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	podName := "mcp-provider-del-test"
	waitForPodExists(t, podName, ns.Name)

	// Delete the provider
	require.NoError(t, k8sClient.Delete(ctx, provider))

	// Wait for provider to be fully removed (finalizer cleanup)
	require.Eventually(t, func() bool {
		err := k8sClient.Get(ctx, types.NamespacedName{Name: "del-test", Namespace: ns.Name}, &mcpv1alpha1.MCPProvider{})
		return err != nil
	}, 15*time.Second, 250*time.Millisecond, "provider should be deleted")

	// Pod should also be cleaned up
	waitForPodNotExists(t, podName, ns.Name)
}

func TestMCPProvider_CapabilitiesPropagatedToStatus(t *testing.T) {
	ns := createNamespace(t, "test-provider-caps")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "caps-test",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:     mcpv1alpha1.ProviderModeRemote,
			Endpoint: "http://example.com:8080",
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					Egress: []mcpv1alpha1.EgressRuleSpec{
						{Host: "db.internal", Port: 5432, Protocol: "tcp", CIDR: "10.0.0.0/8"},
					},
				},
				EnforcementMode: "block",
			},
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Wait for reconcile to propagate capabilities
	require.Eventually(t, func() bool {
		p := &mcpv1alpha1.MCPProvider{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: "caps-test", Namespace: ns.Name}, p); err != nil {
			return false
		}
		return p.Status.Capabilities != nil && p.Status.Capabilities.EnforcementMode == "block"
	}, 15*time.Second, 250*time.Millisecond, "capabilities not propagated to status")

	// Verify full capabilities structure
	result := &mcpv1alpha1.MCPProvider{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: "caps-test", Namespace: ns.Name}, result))
	require.NotNil(t, result.Status.Capabilities.Network)
	require.Len(t, result.Status.Capabilities.Network.Egress, 1)
	assert.Equal(t, "db.internal", result.Status.Capabilities.Network.Egress[0].Host)
}

func TestMCPProvider_Finalizer_Added(t *testing.T) {
	ns := createNamespace(t, "test-provider-fin")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "fin-test",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  mcpv1alpha1.ProviderModeContainer,
			Image: "busybox:latest",
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Wait for finalizer
	require.Eventually(t, func() bool {
		p := &mcpv1alpha1.MCPProvider{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: "fin-test", Namespace: ns.Name}, p); err != nil {
			return false
		}
		for _, f := range p.Finalizers {
			if f == finalizerName {
				return true
			}
		}
		return false
	}, 10*time.Second, 250*time.Millisecond, "finalizer not added")
}

func TestMCPProvider_UnknownMode_MarksNotReady(t *testing.T) {
	ns := createNamespace(t, "test-provider-unkmode")
	defer k8sClient.Delete(ctx, ns)

	// Create directly with unknown mode (bypass kubebuilder validation in envtest)
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "unknown-mode",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode: "invalid",
		},
	}
	// This may fail due to CRD validation; if so, the test documents that
	err := k8sClient.Create(ctx, provider)
	if err != nil {
		t.Skipf("CRD validation rejected unknown mode (expected): %v", err)
		return
	}

	waitForProviderCondition(t, "unknown-mode", ns.Name, ConditionReady, metav1.ConditionFalse)
}

func TestMCPProvider_ObservedGeneration_Updated(t *testing.T) {
	ns := createNamespace(t, "test-provider-obsgen")
	defer k8sClient.Delete(ctx, ns)

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "obsgen-test",
			Namespace: ns.Name,
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:     mcpv1alpha1.ProviderModeRemote,
			Endpoint: "http://example.com:8080",
		},
	}
	require.NoError(t, k8sClient.Create(ctx, provider))

	// Wait for reconcile
	require.Eventually(t, func() bool {
		p := &mcpv1alpha1.MCPProvider{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: "obsgen-test", Namespace: ns.Name}, p); err != nil {
			return false
		}
		return p.Status.ObservedGeneration > 0
	}, 15*time.Second, 250*time.Millisecond, "observedGeneration not updated")

	// Read final
	result := &mcpv1alpha1.MCPProvider{}
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Name: "obsgen-test", Namespace: ns.Name}, result))
	assert.Equal(t, result.Generation, result.Status.ObservedGeneration,
		fmt.Sprintf("observedGeneration (%d) should match generation (%d)", result.Status.ObservedGeneration, result.Generation))
}
