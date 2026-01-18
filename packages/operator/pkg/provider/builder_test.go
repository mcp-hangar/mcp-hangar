package provider

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mcpv1alpha1 "github.com/mapyr/mcp-hangar/operator/api/v1alpha1"
)

func TestBuildPodForProvider_BasicContainer(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
			UID:       "test-uid-123",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  "container",
			Image: "test-image:latest",
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	assert.NotNil(t, pod)
	assert.Equal(t, "mcp-provider-test-provider", pod.Name)
	assert.Equal(t, "default", pod.Namespace)
	assert.Equal(t, "test-image:latest", pod.Spec.Containers[0].Image)
	assert.Equal(t, corev1.RestartPolicyNever, pod.Spec.RestartPolicy)
}

func TestBuildPodForProvider_NoImage(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode: "container",
			// No image specified
		},
	}

	pod, err := BuildPodForProvider(provider)

	assert.Error(t, err)
	assert.Nil(t, pod)
	assert.Contains(t, err.Error(), "container mode requires image")
}

func TestBuildPodForProvider_WithResources(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  "container",
			Image: "test-image:latest",
			Resources: &mcpv1alpha1.ResourceRequirements{
				Requests: &mcpv1alpha1.ResourceList{
					CPU:    "100m",
					Memory: "128Mi",
				},
				Limits: &mcpv1alpha1.ResourceList{
					CPU:    "500m",
					Memory: "512Mi",
				},
			},
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	container := pod.Spec.Containers[0]

	assert.Equal(t, resource.MustParse("100m"), container.Resources.Requests[corev1.ResourceCPU])
	assert.Equal(t, resource.MustParse("128Mi"), container.Resources.Requests[corev1.ResourceMemory])
	assert.Equal(t, resource.MustParse("500m"), container.Resources.Limits[corev1.ResourceCPU])
	assert.Equal(t, resource.MustParse("512Mi"), container.Resources.Limits[corev1.ResourceMemory])
}

func TestBuildPodForProvider_WithEnvVars(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  "container",
			Image: "test-image:latest",
			Env: []mcpv1alpha1.EnvVar{
				{
					Name:  "CUSTOM_VAR",
					Value: "custom-value",
				},
				{
					Name: "SECRET_VAR",
					ValueFrom: &mcpv1alpha1.EnvVarSource{
						SecretKeyRef: &mcpv1alpha1.SecretKeySelector{
							Name: "my-secret",
							Key:  "password",
						},
					},
				},
			},
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	envVars := pod.Spec.Containers[0].Env

	// Should have default vars + custom vars
	assert.True(t, len(envVars) >= 6) // 4 default + 2 custom

	// Check default vars
	assert.Contains(t, envVars, corev1.EnvVar{Name: "MCP_PROVIDER_NAME", Value: "test-provider"})
	assert.Contains(t, envVars, corev1.EnvVar{Name: "MCP_PROVIDER_NAMESPACE", Value: "default"})

	// Check custom vars
	customVar := findEnvVar(envVars, "CUSTOM_VAR")
	require.NotNil(t, customVar)
	assert.Equal(t, "custom-value", customVar.Value)

	secretVar := findEnvVar(envVars, "SECRET_VAR")
	require.NotNil(t, secretVar)
	require.NotNil(t, secretVar.ValueFrom)
	require.NotNil(t, secretVar.ValueFrom.SecretKeyRef)
	assert.Equal(t, "my-secret", secretVar.ValueFrom.SecretKeyRef.Name)
	assert.Equal(t, "password", secretVar.ValueFrom.SecretKeyRef.Key)
}

func TestBuildPodForProvider_WithVolumes(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  "container",
			Image: "test-image:latest",
			Volumes: []mcpv1alpha1.Volume{
				{
					Name:      "data",
					MountPath: "/data",
					ReadOnly:  false,
					PersistentVolumeClaim: &mcpv1alpha1.PVCVolumeSource{
						ClaimName: "data-pvc",
					},
				},
				{
					Name:      "config",
					MountPath: "/config",
					ReadOnly:  true,
					ConfigMap: &mcpv1alpha1.ConfigMapVolumeSource{
						Name: "provider-config",
					},
				},
			},
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	assert.Len(t, pod.Spec.Volumes, 2)
	assert.Len(t, pod.Spec.Containers[0].VolumeMounts, 2)

	// Check PVC volume
	pvcVolume := findVolume(pod.Spec.Volumes, "data")
	require.NotNil(t, pvcVolume)
	require.NotNil(t, pvcVolume.PersistentVolumeClaim)
	assert.Equal(t, "data-pvc", pvcVolume.PersistentVolumeClaim.ClaimName)

	// Check ConfigMap volume
	cmVolume := findVolume(pod.Spec.Volumes, "config")
	require.NotNil(t, cmVolume)
	require.NotNil(t, cmVolume.ConfigMap)
	assert.Equal(t, "provider-config", cmVolume.ConfigMap.Name)

	// Check mounts
	dataMount := findVolumeMount(pod.Spec.Containers[0].VolumeMounts, "data")
	require.NotNil(t, dataMount)
	assert.Equal(t, "/data", dataMount.MountPath)
	assert.False(t, dataMount.ReadOnly)

	configMount := findVolumeMount(pod.Spec.Containers[0].VolumeMounts, "config")
	require.NotNil(t, configMount)
	assert.Equal(t, "/config", configMount.MountPath)
	assert.True(t, configMount.ReadOnly)
}

func TestBuildPodForProvider_WithSecurityContext(t *testing.T) {
	runAsUser := int64(1000)
	runAsNonRoot := true
	readOnlyRootFilesystem := true
	allowPrivilegeEscalation := false

	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  "container",
			Image: "test-image:latest",
			SecurityContext: &mcpv1alpha1.SecurityContext{
				RunAsUser:                &runAsUser,
				RunAsNonRoot:             &runAsNonRoot,
				ReadOnlyRootFilesystem:   &readOnlyRootFilesystem,
				AllowPrivilegeEscalation: &allowPrivilegeEscalation,
				Capabilities: &mcpv1alpha1.Capabilities{
					Drop: []string{"ALL"},
					Add:  []string{"NET_BIND_SERVICE"},
				},
			},
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	secCtx := pod.Spec.Containers[0].SecurityContext
	require.NotNil(t, secCtx)

	assert.Equal(t, int64(1000), *secCtx.RunAsUser)
	assert.True(t, *secCtx.RunAsNonRoot)
	assert.True(t, *secCtx.ReadOnlyRootFilesystem)
	assert.False(t, *secCtx.AllowPrivilegeEscalation)

	require.NotNil(t, secCtx.Capabilities)
	assert.Contains(t, secCtx.Capabilities.Drop, corev1.Capability("ALL"))
	assert.Contains(t, secCtx.Capabilities.Add, corev1.Capability("NET_BIND_SERVICE"))
}

func TestBuildPodForProvider_WithDefaultSecurityContext(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  "container",
			Image: "test-image:latest",
			// No security context specified
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)

	// Should have secure defaults
	secCtx := pod.Spec.Containers[0].SecurityContext
	require.NotNil(t, secCtx)
	assert.True(t, *secCtx.RunAsNonRoot)
	assert.True(t, *secCtx.ReadOnlyRootFilesystem)
	assert.False(t, *secCtx.AllowPrivilegeEscalation)

	require.NotNil(t, secCtx.Capabilities)
	assert.Contains(t, secCtx.Capabilities.Drop, corev1.Capability("ALL"))
}

func TestBuildPodForProvider_WithCommandAndArgs(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:    "container",
			Image:   "test-image:latest",
			Command: []string{"/app/provider"},
			Args:    []string{"--config", "/config/app.yaml", "--verbose"},
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	container := pod.Spec.Containers[0]

	assert.Equal(t, []string{"/app/provider"}, container.Command)
	assert.Equal(t, []string{"--config", "/config/app.yaml", "--verbose"}, container.Args)
}

func TestBuildPodForProvider_WithNodeSelector(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  "container",
			Image: "test-image:latest",
			NodeSelector: map[string]string{
				"disktype": "ssd",
				"zone":     "us-west-1a",
			},
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	assert.Equal(t, "ssd", pod.Spec.NodeSelector["disktype"])
	assert.Equal(t, "us-west-1a", pod.Spec.NodeSelector["zone"])
}

func TestBuildPodForProvider_WithTolerations(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:  "container",
			Image: "test-image:latest",
			Tolerations: []mcpv1alpha1.Toleration{
				{
					Key:      "key1",
					Operator: "Equal",
					Value:    "value1",
					Effect:   "NoSchedule",
				},
			},
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	require.Len(t, pod.Spec.Tolerations, 1)
	assert.Equal(t, "key1", pod.Spec.Tolerations[0].Key)
	assert.Equal(t, corev1.TolerationOperator("Equal"), pod.Spec.Tolerations[0].Operator)
	assert.Equal(t, "value1", pod.Spec.Tolerations[0].Value)
	assert.Equal(t, corev1.TaintEffect("NoSchedule"), pod.Spec.Tolerations[0].Effect)
}

func TestBuildPodForProvider_WithServiceAccount(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Mode:               "container",
			Image:              "test-image:latest",
			ServiceAccountName: "custom-sa",
		},
	}

	pod, err := BuildPodForProvider(provider)

	require.NoError(t, err)
	assert.Equal(t, "custom-sa", pod.Spec.ServiceAccountName)
}

func TestBuildLabels(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
			UID:       "test-uid-123",
		},
	}

	labels := buildLabels(provider)

	assert.Equal(t, "mcp-hangar-operator", labels[LabelManagedBy])
	assert.Equal(t, "test-provider", labels[LabelName])
	assert.Equal(t, "test-provider", labels[LabelInstance])
	assert.Equal(t, "provider", labels[LabelComponent])
	assert.Equal(t, "mcp-hangar", labels[LabelPartOf])
	assert.Equal(t, "test-provider", labels[LabelProvider])
	assert.Equal(t, "test-uid-123", labels[LabelProviderUID])
}

func TestBuildResourceRequirements(t *testing.T) {
	spec := &mcpv1alpha1.ResourceRequirements{
		Requests: &mcpv1alpha1.ResourceList{
			CPU:    "100m",
			Memory: "128Mi",
		},
		Limits: &mcpv1alpha1.ResourceList{
			CPU:    "1",
			Memory: "1Gi",
		},
	}

	reqs := buildResourceRequirements(spec)

	assert.Equal(t, resource.MustParse("100m"), reqs.Requests[corev1.ResourceCPU])
	assert.Equal(t, resource.MustParse("128Mi"), reqs.Requests[corev1.ResourceMemory])
	assert.Equal(t, resource.MustParse("1"), reqs.Limits[corev1.ResourceCPU])
	assert.Equal(t, resource.MustParse("1Gi"), reqs.Limits[corev1.ResourceMemory])
}

func TestBuildResourceRequirements_Partial(t *testing.T) {
	// Only requests, no limits
	spec := &mcpv1alpha1.ResourceRequirements{
		Requests: &mcpv1alpha1.ResourceList{
			CPU: "100m",
		},
	}

	reqs := buildResourceRequirements(spec)

	assert.Equal(t, resource.MustParse("100m"), reqs.Requests[corev1.ResourceCPU])
	assert.Empty(t, reqs.Requests[corev1.ResourceMemory])
	assert.Empty(t, reqs.Limits)
}

// Helper functions

func findEnvVar(envVars []corev1.EnvVar, name string) *corev1.EnvVar {
	for _, env := range envVars {
		if env.Name == name {
			return &env
		}
	}
	return nil
}

func findVolume(volumes []corev1.Volume, name string) *corev1.Volume {
	for _, vol := range volumes {
		if vol.Name == name {
			return &vol
		}
	}
	return nil
}

func findVolumeMount(mounts []corev1.VolumeMount, name string) *corev1.VolumeMount {
	for _, mount := range mounts {
		if mount.Name == name {
			return &mount
		}
	}
	return nil
}
