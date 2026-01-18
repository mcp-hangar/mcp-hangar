// Package v1alpha1 contains API Schema definitions for the mcp-hangar.io v1alpha1 API group
package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ProviderMode defines the execution mode for a provider
// +kubebuilder:validation:Enum=container;remote
type ProviderMode string

const (
	// ProviderModeContainer runs the provider as a Kubernetes Pod
	ProviderModeContainer ProviderMode = "container"
	// ProviderModeRemote connects to an external HTTP endpoint
	ProviderModeRemote ProviderMode = "remote"
)

// ProviderState represents the current state of a provider
// +kubebuilder:validation:Enum=Cold;Initializing;Ready;Degraded;Dead
type ProviderState string

const (
	ProviderStateCold         ProviderState = "Cold"
	ProviderStateInitializing ProviderState = "Initializing"
	ProviderStateReady        ProviderState = "Ready"
	ProviderStateDegraded     ProviderState = "Degraded"
	ProviderStateDead         ProviderState = "Dead"
)

// MCPProviderSpec defines the desired state of MCPProvider
type MCPProviderSpec struct {
	// Mode is the provider execution mode (container or remote)
	// +kubebuilder:validation:Required
	Mode ProviderMode `json:"mode"`

	// Image is the container image for the provider (required for container mode)
	// +optional
	Image string `json:"image,omitempty"`

	// Command overrides the container entrypoint
	// +optional
	Command []string `json:"command,omitempty"`

	// Args are arguments to the entrypoint
	// +optional
	Args []string `json:"args,omitempty"`

	// WorkingDir is the container working directory
	// +optional
	WorkingDir string `json:"workingDir,omitempty"`

	// Endpoint is the HTTP endpoint for remote providers
	// +optional
	Endpoint string `json:"endpoint,omitempty"`

	// Replicas is the desired number of provider replicas
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=10
	// +optional
	Replicas *int32 `json:"replicas,omitempty"`

	// IdleTTL is the duration before an idle provider is stopped
	// +kubebuilder:default="5m"
	// +optional
	IdleTTL string `json:"idleTTL,omitempty"`

	// StartupTimeout is the maximum time to wait for provider startup
	// +kubebuilder:default="30s"
	// +optional
	StartupTimeout string `json:"startupTimeout,omitempty"`

	// ShutdownGracePeriod is the grace period for graceful shutdown
	// +kubebuilder:default="30s"
	// +optional
	ShutdownGracePeriod string `json:"shutdownGracePeriod,omitempty"`

	// HealthCheck configures health checking
	// +optional
	HealthCheck *HealthCheckConfig `json:"healthCheck,omitempty"`

	// Resources defines resource requirements
	// +optional
	Resources *ResourceRequirements `json:"resources,omitempty"`

	// Env defines environment variables
	// +optional
	Env []EnvVar `json:"env,omitempty"`

	// Volumes defines volume mounts
	// +optional
	Volumes []Volume `json:"volumes,omitempty"`

	// SecurityContext defines pod security settings
	// +optional
	SecurityContext *SecurityContext `json:"securityContext,omitempty"`

	// ServiceAccountName is the ServiceAccount for the provider pod
	// +optional
	ServiceAccountName string `json:"serviceAccountName,omitempty"`

	// ImagePullSecrets for pulling the container image
	// +optional
	ImagePullSecrets []corev1.LocalObjectReference `json:"imagePullSecrets,omitempty"`

	// NodeSelector for pod scheduling
	// +optional
	NodeSelector map[string]string `json:"nodeSelector,omitempty"`

	// Tolerations for pod scheduling
	// +optional
	Tolerations []Toleration `json:"tolerations,omitempty"`

	// Affinity rules for pod scheduling
	// +optional
	Affinity *corev1.Affinity `json:"affinity,omitempty"`

	// PriorityClassName for pod scheduling priority
	// +optional
	PriorityClassName string `json:"priorityClassName,omitempty"`

	// Tools configures tool exposure
	// +optional
	Tools *ToolsConfig `json:"tools,omitempty"`

	// CircuitBreaker configures circuit breaker behavior
	// +optional
	CircuitBreaker *CircuitBreakerConfig `json:"circuitBreaker,omitempty"`

	// Observability configures observability features
	// +optional
	Observability *ObservabilityConfig `json:"observability,omitempty"`
}

// HealthCheckConfig defines health check settings
type HealthCheckConfig struct {
	// Enabled enables health checks
	// +kubebuilder:default=true
	Enabled *bool `json:"enabled,omitempty"`

	// Interval between health checks
	// +kubebuilder:default="30s"
	Interval string `json:"interval,omitempty"`

	// Timeout for each health check
	// +kubebuilder:default="5s"
	Timeout string `json:"timeout,omitempty"`

	// FailureThreshold before marking unhealthy
	// +kubebuilder:default=3
	// +kubebuilder:validation:Minimum=1
	FailureThreshold int32 `json:"failureThreshold,omitempty"`

	// SuccessThreshold before marking healthy
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=1
	SuccessThreshold int32 `json:"successThreshold,omitempty"`
}

// ResourceRequirements defines resource requests and limits
type ResourceRequirements struct {
	Requests *ResourceList `json:"requests,omitempty"`
	Limits   *ResourceList `json:"limits,omitempty"`
}

// ResourceList defines CPU and memory resources
type ResourceList struct {
	CPU    string `json:"cpu,omitempty"`
	Memory string `json:"memory,omitempty"`
}

// EnvVar defines an environment variable
type EnvVar struct {
	// Name of the environment variable
	Name string `json:"name"`

	// Value is the literal value
	// +optional
	Value string `json:"value,omitempty"`

	// ValueFrom references a Secret or ConfigMap
	// +optional
	ValueFrom *EnvVarSource `json:"valueFrom,omitempty"`
}

// EnvVarSource defines the source for an environment variable value
type EnvVarSource struct {
	SecretKeyRef    *SecretKeySelector    `json:"secretKeyRef,omitempty"`
	ConfigMapKeyRef *ConfigMapKeySelector `json:"configMapKeyRef,omitempty"`
}

// SecretKeySelector selects a key from a Secret
type SecretKeySelector struct {
	Name     string `json:"name"`
	Key      string `json:"key"`
	Optional *bool  `json:"optional,omitempty"`
}

// ConfigMapKeySelector selects a key from a ConfigMap
type ConfigMapKeySelector struct {
	Name     string `json:"name"`
	Key      string `json:"key"`
	Optional *bool  `json:"optional,omitempty"`
}

// Volume defines a volume mount
type Volume struct {
	// Name of the volume
	Name string `json:"name"`

	// MountPath within the container
	MountPath string `json:"mountPath"`

	// SubPath within the volume
	// +optional
	SubPath string `json:"subPath,omitempty"`

	// ReadOnly mount
	// +optional
	ReadOnly bool `json:"readOnly,omitempty"`

	// Secret volume source
	// +optional
	Secret *SecretVolumeSource `json:"secret,omitempty"`

	// ConfigMap volume source
	// +optional
	ConfigMap *ConfigMapVolumeSource `json:"configMap,omitempty"`

	// PersistentVolumeClaim source
	// +optional
	PersistentVolumeClaim *PVCVolumeSource `json:"persistentVolumeClaim,omitempty"`

	// EmptyDir volume source
	// +optional
	EmptyDir *EmptyDirVolumeSource `json:"emptyDir,omitempty"`
}

// SecretVolumeSource adapts a Secret
type SecretVolumeSource struct {
	SecretName string      `json:"secretName"`
	Items      []KeyToPath `json:"items,omitempty"`
}

// ConfigMapVolumeSource adapts a ConfigMap
type ConfigMapVolumeSource struct {
	Name  string      `json:"name"`
	Items []KeyToPath `json:"items,omitempty"`
}

// PVCVolumeSource references a PersistentVolumeClaim
type PVCVolumeSource struct {
	ClaimName string `json:"claimName"`
}

// EmptyDirVolumeSource is an empty directory volume
type EmptyDirVolumeSource struct {
	Medium    string `json:"medium,omitempty"`
	SizeLimit string `json:"sizeLimit,omitempty"`
}

// KeyToPath defines a key to path mapping
type KeyToPath struct {
	Key  string `json:"key"`
	Path string `json:"path"`
}

// SecurityContext defines security settings
type SecurityContext struct {
	RunAsNonRoot             *bool           `json:"runAsNonRoot,omitempty"`
	RunAsUser                *int64          `json:"runAsUser,omitempty"`
	RunAsGroup               *int64          `json:"runAsGroup,omitempty"`
	FSGroup                  *int64          `json:"fsGroup,omitempty"`
	ReadOnlyRootFilesystem   *bool           `json:"readOnlyRootFilesystem,omitempty"`
	AllowPrivilegeEscalation *bool           `json:"allowPrivilegeEscalation,omitempty"`
	Capabilities             *Capabilities   `json:"capabilities,omitempty"`
	SeccompProfile           *SeccompProfile `json:"seccompProfile,omitempty"`
}

// Capabilities defines Linux capabilities
type Capabilities struct {
	Add  []string `json:"add,omitempty"`
	Drop []string `json:"drop,omitempty"`
}

// SeccompProfile defines seccomp settings
type SeccompProfile struct {
	Type string `json:"type,omitempty"`
}

// Toleration defines a pod toleration
type Toleration struct {
	Key               string `json:"key,omitempty"`
	Operator          string `json:"operator,omitempty"`
	Value             string `json:"value,omitempty"`
	Effect            string `json:"effect,omitempty"`
	TolerationSeconds *int64 `json:"tolerationSeconds,omitempty"`
}

// ToolsConfig defines tool exposure settings
type ToolsConfig struct {
	// AllowList restricts exposed tools (empty = all)
	AllowList []string `json:"allowList,omitempty"`

	// DenyList blocks specific tools
	DenyList []string `json:"denyList,omitempty"`

	// RateLimit configures rate limiting
	RateLimit *RateLimitConfig `json:"rateLimit,omitempty"`
}

// RateLimitConfig defines rate limiting settings
type RateLimitConfig struct {
	RequestsPerMinute int32 `json:"requestsPerMinute,omitempty"`
	BurstSize         int32 `json:"burstSize,omitempty"`
}

// CircuitBreakerConfig defines circuit breaker settings
type CircuitBreakerConfig struct {
	// Enabled enables circuit breaker
	// +kubebuilder:default=true
	Enabled *bool `json:"enabled,omitempty"`

	// FailureThreshold before opening circuit
	// +kubebuilder:default=5
	FailureThreshold int32 `json:"failureThreshold,omitempty"`

	// SuccessThreshold before closing circuit
	// +kubebuilder:default=2
	SuccessThreshold int32 `json:"successThreshold,omitempty"`

	// ResetTimeout before attempting recovery
	// +kubebuilder:default="30s"
	ResetTimeout string `json:"resetTimeout,omitempty"`

	// HalfOpenRequests allowed during half-open state
	// +kubebuilder:default=3
	HalfOpenRequests int32 `json:"halfOpenRequests,omitempty"`
}

// ObservabilityConfig defines observability settings
type ObservabilityConfig struct {
	Tracing *TracingConfig `json:"tracing,omitempty"`
	Metrics *MetricsConfig `json:"metrics,omitempty"`
}

// TracingConfig defines tracing settings
type TracingConfig struct {
	Enabled      bool    `json:"enabled,omitempty"`
	SamplingRate float64 `json:"samplingRate,omitempty"`
}

// MetricsConfig defines metrics settings
type MetricsConfig struct {
	Enabled bool  `json:"enabled,omitempty"`
	Port    int32 `json:"port,omitempty"`
}

// MCPProviderStatus defines the observed state of MCPProvider
type MCPProviderStatus struct {
	// State is the current provider state
	State ProviderState `json:"state,omitempty"`

	// Phase is the overall phase
	Phase string `json:"phase,omitempty"`

	// Replicas is the desired replicas
	Replicas int32 `json:"replicas,omitempty"`

	// ReadyReplicas is the number of ready replicas
	ReadyReplicas int32 `json:"readyReplicas,omitempty"`

	// AvailableReplicas is the number of available replicas
	AvailableReplicas int32 `json:"availableReplicas,omitempty"`

	// ToolsCount is the number of exposed tools
	ToolsCount int32 `json:"toolsCount,omitempty"`

	// Tools is the list of tool names
	Tools []string `json:"tools,omitempty"`

	// Endpoint is the internal endpoint URL
	Endpoint string `json:"endpoint,omitempty"`

	// LastStartedAt is the last startup time
	LastStartedAt *metav1.Time `json:"lastStartedAt,omitempty"`

	// LastStoppedAt is the last shutdown time
	LastStoppedAt *metav1.Time `json:"lastStoppedAt,omitempty"`

	// LastHealthCheck is the last successful health check
	LastHealthCheck *metav1.Time `json:"lastHealthCheck,omitempty"`

	// ConsecutiveFailures counts consecutive health failures
	ConsecutiveFailures int32 `json:"consecutiveFailures,omitempty"`

	// ObservedGeneration is the generation observed by controller
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// PodName is the name of the managed Pod
	PodName string `json:"podName,omitempty"`

	// Conditions represent the latest available observations
	Conditions []Condition `json:"conditions,omitempty"`
}

// Condition represents a condition of a resource
type Condition struct {
	// Type of condition
	Type string `json:"type"`

	// Status of the condition
	// +kubebuilder:validation:Enum=True;False;Unknown
	Status metav1.ConditionStatus `json:"status"`

	// LastTransitionTime is the last time the condition transitioned
	LastTransitionTime metav1.Time `json:"lastTransitionTime,omitempty"`

	// Reason is a machine-readable reason
	Reason string `json:"reason,omitempty"`

	// Message is a human-readable description
	Message string `json:"message,omitempty"`

	// ObservedGeneration represents the generation observed
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:subresource:scale:specpath=.spec.replicas,statuspath=.status.replicas
// +kubebuilder:printcolumn:name="Mode",type=string,JSONPath=`.spec.mode`
// +kubebuilder:printcolumn:name="State",type=string,JSONPath=`.status.state`
// +kubebuilder:printcolumn:name="Tools",type=integer,JSONPath=`.status.toolsCount`
// +kubebuilder:printcolumn:name="Ready",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].status`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`
// +kubebuilder:resource:shortName=mcpp;provider,categories=mcp

// MCPProvider is the Schema for the mcpproviders API
type MCPProvider struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   MCPProviderSpec   `json:"spec,omitempty"`
	Status MCPProviderStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// MCPProviderList contains a list of MCPProvider
type MCPProviderList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []MCPProvider `json:"items"`
}

func init() {
	SchemeBuilder.Register(&MCPProvider{}, &MCPProviderList{})
}

// Helper methods

// GetReplicas returns the number of replicas (defaults to 1)
func (p *MCPProvider) GetReplicas() int32 {
	if p.Spec.Replicas == nil {
		return 1
	}
	return *p.Spec.Replicas
}

// IsCold returns true if the provider should be cold (replicas=0)
func (p *MCPProvider) IsCold() bool {
	return p.GetReplicas() == 0
}

// IsContainerMode returns true if running as container
func (p *MCPProvider) IsContainerMode() bool {
	return p.Spec.Mode == ProviderModeContainer
}

// IsRemoteMode returns true if connecting to remote endpoint
func (p *MCPProvider) IsRemoteMode() bool {
	return p.Spec.Mode == ProviderModeRemote
}

// GetPodName returns the expected pod name
func (p *MCPProvider) GetPodName() string {
	return "mcp-provider-" + p.Name
}

// SetCondition sets or updates a condition
func (s *MCPProviderStatus) SetCondition(condType string, status metav1.ConditionStatus, reason, message string) {
	now := metav1.Now()

	for i, c := range s.Conditions {
		if c.Type == condType {
			if c.Status != status {
				s.Conditions[i].LastTransitionTime = now
			}
			s.Conditions[i].Status = status
			s.Conditions[i].Reason = reason
			s.Conditions[i].Message = message
			return
		}
	}

	// Add new condition
	s.Conditions = append(s.Conditions, Condition{
		Type:               condType,
		Status:             status,
		LastTransitionTime: now,
		Reason:             reason,
		Message:            message,
	})
}

// GetCondition returns the condition with the given type
func (s *MCPProviderStatus) GetCondition(condType string) *Condition {
	for i := range s.Conditions {
		if s.Conditions[i].Type == condType {
			return &s.Conditions[i]
		}
	}
	return nil
}

// IsReady returns true if the Ready condition is True
func (s *MCPProviderStatus) IsReady() bool {
	cond := s.GetCondition("Ready")
	return cond != nil && cond.Status == metav1.ConditionTrue
}
