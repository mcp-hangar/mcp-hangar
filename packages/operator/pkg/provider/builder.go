// Package provider contains utilities for building Kubernetes resources from MCPProvider specs
package provider

import (
	"fmt"
	"strconv"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mcpv1alpha1 "github.com/mapyr/mcp-hangar/operator/api/v1alpha1"
)

const (
	// Labels
	LabelManagedBy   = "app.kubernetes.io/managed-by"
	LabelName        = "app.kubernetes.io/name"
	LabelInstance    = "app.kubernetes.io/instance"
	LabelComponent   = "app.kubernetes.io/component"
	LabelPartOf      = "app.kubernetes.io/part-of"
	LabelProvider    = "mcp-hangar.io/provider"
	LabelProviderUID = "mcp-hangar.io/provider-uid"

	// Annotations
	AnnotationGeneration = "mcp-hangar.io/generation"
	AnnotationConfigHash = "mcp-hangar.io/config-hash"

	// Container names
	ContainerProvider = "provider"

	// Default values
	DefaultManagerName = "mcp-hangar-operator"
)

// BuildPodForProvider creates a Pod spec from MCPProvider
func BuildPodForProvider(provider *mcpv1alpha1.MCPProvider) (*corev1.Pod, error) {
	if provider.Spec.Image == "" {
		return nil, fmt.Errorf("container mode requires image")
	}

	podName := provider.GetPodName()

	// Build main container
	container := buildContainer(provider)

	// Build volumes
	volumeMounts, volumes := buildVolumes(provider)
	container.VolumeMounts = volumeMounts

	// Build Pod
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      podName,
			Namespace: provider.Namespace,
			Labels:    buildLabels(provider),
			Annotations: map[string]string{
				AnnotationGeneration: strconv.FormatInt(provider.Generation, 10),
			},
		},
		Spec: corev1.PodSpec{
			Containers:                    []corev1.Container{container},
			Volumes:                       volumes,
			RestartPolicy:                 corev1.RestartPolicyNever, // Operator manages restarts
			ServiceAccountName:            provider.Spec.ServiceAccountName,
			NodeSelector:                  provider.Spec.NodeSelector,
			ImagePullSecrets:              provider.Spec.ImagePullSecrets,
			PriorityClassName:             provider.Spec.PriorityClassName,
			TerminationGracePeriodSeconds: getTerminationGracePeriod(provider),
		},
	}

	// Tolerations
	if len(provider.Spec.Tolerations) > 0 {
		pod.Spec.Tolerations = buildTolerations(provider.Spec.Tolerations)
	}

	// Affinity
	if provider.Spec.Affinity != nil {
		pod.Spec.Affinity = provider.Spec.Affinity
	}

	// Pod security context
	if provider.Spec.SecurityContext != nil {
		pod.Spec.SecurityContext = buildPodSecurityContext(provider.Spec.SecurityContext)
	} else {
		// Secure defaults
		pod.Spec.SecurityContext = defaultPodSecurityContext()
	}

	return pod, nil
}

// buildContainer creates the main provider container
func buildContainer(provider *mcpv1alpha1.MCPProvider) corev1.Container {
	container := corev1.Container{
		Name:            ContainerProvider,
		Image:           provider.Spec.Image,
		ImagePullPolicy: corev1.PullIfNotPresent,
	}

	// Command and args
	if len(provider.Spec.Command) > 0 {
		container.Command = provider.Spec.Command
	}
	if len(provider.Spec.Args) > 0 {
		container.Args = provider.Spec.Args
	}

	// Working directory
	if provider.Spec.WorkingDir != "" {
		container.WorkingDir = provider.Spec.WorkingDir
	}

	// Environment variables
	container.Env = buildEnvVars(provider)

	// Resources
	if provider.Spec.Resources != nil {
		container.Resources = buildResourceRequirements(provider.Spec.Resources)
	}

	// Container security context
	if provider.Spec.SecurityContext != nil {
		container.SecurityContext = buildContainerSecurityContext(provider.Spec.SecurityContext)
	} else {
		container.SecurityContext = defaultContainerSecurityContext()
	}

	return container
}

// buildLabels creates standard labels for provider resources
func buildLabels(provider *mcpv1alpha1.MCPProvider) map[string]string {
	labels := map[string]string{
		LabelManagedBy: DefaultManagerName,
		LabelName:      provider.Name,
		LabelInstance:  provider.Name,
		LabelComponent: "provider",
		LabelPartOf:    "mcp-hangar",
		LabelProvider:  provider.Name,
	}

	// Add provider UID for stronger ownership
	if provider.UID != "" {
		labels[LabelProviderUID] = string(provider.UID)
	}

	return labels
}

// buildEnvVars creates environment variables from provider spec
func buildEnvVars(provider *mcpv1alpha1.MCPProvider) []corev1.EnvVar {
	envVars := []corev1.EnvVar{
		{
			Name:  "MCP_PROVIDER_NAME",
			Value: provider.Name,
		},
		{
			Name:  "MCP_PROVIDER_NAMESPACE",
			Value: provider.Namespace,
		},
		{
			Name: "MCP_POD_NAME",
			ValueFrom: &corev1.EnvVarSource{
				FieldRef: &corev1.ObjectFieldSelector{
					FieldPath: "metadata.name",
				},
			},
		},
		{
			Name: "MCP_POD_IP",
			ValueFrom: &corev1.EnvVarSource{
				FieldRef: &corev1.ObjectFieldSelector{
					FieldPath: "status.podIP",
				},
			},
		},
	}

	// Add user-defined env vars
	for _, env := range provider.Spec.Env {
		envVar := corev1.EnvVar{
			Name: env.Name,
		}

		if env.Value != "" {
			envVar.Value = env.Value
		} else if env.ValueFrom != nil {
			envVar.ValueFrom = buildEnvVarSource(env.ValueFrom)
		}

		envVars = append(envVars, envVar)
	}

	return envVars
}

// buildEnvVarSource converts our EnvVarSource to k8s EnvVarSource
func buildEnvVarSource(source *mcpv1alpha1.EnvVarSource) *corev1.EnvVarSource {
	if source == nil {
		return nil
	}

	result := &corev1.EnvVarSource{}

	if source.SecretKeyRef != nil {
		result.SecretKeyRef = &corev1.SecretKeySelector{
			LocalObjectReference: corev1.LocalObjectReference{
				Name: source.SecretKeyRef.Name,
			},
			Key:      source.SecretKeyRef.Key,
			Optional: source.SecretKeyRef.Optional,
		}
	}

	if source.ConfigMapKeyRef != nil {
		result.ConfigMapKeyRef = &corev1.ConfigMapKeySelector{
			LocalObjectReference: corev1.LocalObjectReference{
				Name: source.ConfigMapKeyRef.Name,
			},
			Key:      source.ConfigMapKeyRef.Key,
			Optional: source.ConfigMapKeyRef.Optional,
		}
	}

	return result
}

// buildResourceRequirements converts our ResourceRequirements to k8s ResourceRequirements
func buildResourceRequirements(res *mcpv1alpha1.ResourceRequirements) corev1.ResourceRequirements {
	requirements := corev1.ResourceRequirements{}

	if res.Requests != nil {
		requirements.Requests = corev1.ResourceList{}
		if res.Requests.CPU != "" {
			requirements.Requests[corev1.ResourceCPU] = resource.MustParse(res.Requests.CPU)
		}
		if res.Requests.Memory != "" {
			requirements.Requests[corev1.ResourceMemory] = resource.MustParse(res.Requests.Memory)
		}
	}

	if res.Limits != nil {
		requirements.Limits = corev1.ResourceList{}
		if res.Limits.CPU != "" {
			requirements.Limits[corev1.ResourceCPU] = resource.MustParse(res.Limits.CPU)
		}
		if res.Limits.Memory != "" {
			requirements.Limits[corev1.ResourceMemory] = resource.MustParse(res.Limits.Memory)
		}
	}

	return requirements
}

// buildVolumes creates volume mounts and volumes from provider spec
func buildVolumes(provider *mcpv1alpha1.MCPProvider) ([]corev1.VolumeMount, []corev1.Volume) {
	var mounts []corev1.VolumeMount
	var volumes []corev1.Volume

	for _, vol := range provider.Spec.Volumes {
		mount := corev1.VolumeMount{
			Name:      vol.Name,
			MountPath: vol.MountPath,
			SubPath:   vol.SubPath,
			ReadOnly:  vol.ReadOnly,
		}
		mounts = append(mounts, mount)

		volume := corev1.Volume{
			Name: vol.Name,
		}

		if vol.Secret != nil {
			volume.VolumeSource = corev1.VolumeSource{
				Secret: &corev1.SecretVolumeSource{
					SecretName: vol.Secret.SecretName,
					Items:      buildKeyToPath(vol.Secret.Items),
				},
			}
		} else if vol.ConfigMap != nil {
			volume.VolumeSource = corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{
						Name: vol.ConfigMap.Name,
					},
					Items: buildKeyToPath(vol.ConfigMap.Items),
				},
			}
		} else if vol.PersistentVolumeClaim != nil {
			volume.VolumeSource = corev1.VolumeSource{
				PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
					ClaimName: vol.PersistentVolumeClaim.ClaimName,
				},
			}
		} else if vol.EmptyDir != nil {
			emptyDir := &corev1.EmptyDirVolumeSource{}
			if vol.EmptyDir.Medium == "Memory" {
				emptyDir.Medium = corev1.StorageMediumMemory
			}
			if vol.EmptyDir.SizeLimit != "" {
				quantity := resource.MustParse(vol.EmptyDir.SizeLimit)
				emptyDir.SizeLimit = &quantity
			}
			volume.VolumeSource = corev1.VolumeSource{
				EmptyDir: emptyDir,
			}
		}

		volumes = append(volumes, volume)
	}

	return mounts, volumes
}

// buildKeyToPath converts our KeyToPath to k8s KeyToPath
func buildKeyToPath(items []mcpv1alpha1.KeyToPath) []corev1.KeyToPath {
	if len(items) == 0 {
		return nil
	}

	result := make([]corev1.KeyToPath, len(items))
	for i, item := range items {
		result[i] = corev1.KeyToPath{
			Key:  item.Key,
			Path: item.Path,
		}
	}
	return result
}

// buildTolerations converts our Tolerations to k8s Tolerations
func buildTolerations(tolerations []mcpv1alpha1.Toleration) []corev1.Toleration {
	result := make([]corev1.Toleration, len(tolerations))
	for i, t := range tolerations {
		result[i] = corev1.Toleration{
			Key:               t.Key,
			Operator:          corev1.TolerationOperator(t.Operator),
			Value:             t.Value,
			Effect:            corev1.TaintEffect(t.Effect),
			TolerationSeconds: t.TolerationSeconds,
		}
	}
	return result
}

// buildPodSecurityContext creates pod-level security context
func buildPodSecurityContext(sc *mcpv1alpha1.SecurityContext) *corev1.PodSecurityContext {
	ctx := &corev1.PodSecurityContext{}

	if sc.RunAsNonRoot != nil {
		ctx.RunAsNonRoot = sc.RunAsNonRoot
	}
	if sc.RunAsUser != nil {
		ctx.RunAsUser = sc.RunAsUser
	}
	if sc.RunAsGroup != nil {
		ctx.RunAsGroup = sc.RunAsGroup
	}
	if sc.FSGroup != nil {
		ctx.FSGroup = sc.FSGroup
	}
	if sc.SeccompProfile != nil {
		ctx.SeccompProfile = &corev1.SeccompProfile{
			Type: corev1.SeccompProfileType(sc.SeccompProfile.Type),
		}
	}

	return ctx
}

// buildContainerSecurityContext creates container-level security context
func buildContainerSecurityContext(sc *mcpv1alpha1.SecurityContext) *corev1.SecurityContext {
	ctx := &corev1.SecurityContext{}

	if sc.RunAsNonRoot != nil {
		ctx.RunAsNonRoot = sc.RunAsNonRoot
	}
	if sc.RunAsUser != nil {
		ctx.RunAsUser = sc.RunAsUser
	}
	if sc.RunAsGroup != nil {
		ctx.RunAsGroup = sc.RunAsGroup
	}
	if sc.ReadOnlyRootFilesystem != nil {
		ctx.ReadOnlyRootFilesystem = sc.ReadOnlyRootFilesystem
	}
	if sc.AllowPrivilegeEscalation != nil {
		ctx.AllowPrivilegeEscalation = sc.AllowPrivilegeEscalation
	}
	if sc.Capabilities != nil {
		ctx.Capabilities = &corev1.Capabilities{}
		for _, cap := range sc.Capabilities.Add {
			ctx.Capabilities.Add = append(ctx.Capabilities.Add, corev1.Capability(cap))
		}
		for _, cap := range sc.Capabilities.Drop {
			ctx.Capabilities.Drop = append(ctx.Capabilities.Drop, corev1.Capability(cap))
		}
	}
	if sc.SeccompProfile != nil {
		ctx.SeccompProfile = &corev1.SeccompProfile{
			Type: corev1.SeccompProfileType(sc.SeccompProfile.Type),
		}
	}

	return ctx
}

// defaultPodSecurityContext returns secure default pod security context
func defaultPodSecurityContext() *corev1.PodSecurityContext {
	runAsNonRoot := true
	runAsUser := int64(65534) // nobody
	fsGroup := int64(65534)

	return &corev1.PodSecurityContext{
		RunAsNonRoot: &runAsNonRoot,
		RunAsUser:    &runAsUser,
		FSGroup:      &fsGroup,
		SeccompProfile: &corev1.SeccompProfile{
			Type: corev1.SeccompProfileTypeRuntimeDefault,
		},
	}
}

// defaultContainerSecurityContext returns secure default container security context
func defaultContainerSecurityContext() *corev1.SecurityContext {
	runAsNonRoot := true
	readOnlyRootFilesystem := true
	allowPrivilegeEscalation := false

	return &corev1.SecurityContext{
		RunAsNonRoot:             &runAsNonRoot,
		ReadOnlyRootFilesystem:   &readOnlyRootFilesystem,
		AllowPrivilegeEscalation: &allowPrivilegeEscalation,
		Capabilities: &corev1.Capabilities{
			Drop: []corev1.Capability{"ALL"},
		},
		SeccompProfile: &corev1.SeccompProfile{
			Type: corev1.SeccompProfileTypeRuntimeDefault,
		},
	}
}

// getTerminationGracePeriod returns termination grace period in seconds
func getTerminationGracePeriod(provider *mcpv1alpha1.MCPProvider) *int64 {
	// Default 30 seconds
	defaultGrace := int64(30)

	if provider.Spec.ShutdownGracePeriod == "" {
		return &defaultGrace
	}

	// Parse duration (simplified - just handle seconds for now)
	// Full implementation would parse "30s", "1m", etc.
	return &defaultGrace
}
