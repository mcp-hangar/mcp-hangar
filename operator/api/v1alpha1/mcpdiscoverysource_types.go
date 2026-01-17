// Package v1alpha1 contains API Schema definitions for the mcp-hangar.io v1alpha1 API group
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// DiscoveryType defines the type of discovery source
// +kubebuilder:validation:Enum=Namespace;ConfigMap;Annotations;ServiceDiscovery
type DiscoveryType string

const (
	DiscoveryTypeNamespace        DiscoveryType = "Namespace"
	DiscoveryTypeConfigMap        DiscoveryType = "ConfigMap"
	DiscoveryTypeAnnotations      DiscoveryType = "Annotations"
	DiscoveryTypeServiceDiscovery DiscoveryType = "ServiceDiscovery"
)

// DiscoveryMode defines how discovery handles changes
// +kubebuilder:validation:Enum=Additive;Authoritative
type DiscoveryMode string

const (
	// DiscoveryModeAdditive only adds new providers
	DiscoveryModeAdditive DiscoveryMode = "Additive"
	// DiscoveryModeAuthoritative adds and removes (syncs with source)
	DiscoveryModeAuthoritative DiscoveryMode = "Authoritative"
)

// MCPDiscoverySourceSpec defines the desired state of MCPDiscoverySource
type MCPDiscoverySourceSpec struct {
	// Type is the discovery source type
	// +kubebuilder:validation:Required
	Type DiscoveryType `json:"type"`

	// Mode determines add-only or full sync behavior
	// +kubebuilder:default=Additive
	Mode DiscoveryMode `json:"mode,omitempty"`

	// RefreshInterval is how often to rescan
	// +kubebuilder:default="1m"
	RefreshInterval string `json:"refreshInterval,omitempty"`

	// Paused pauses discovery (for maintenance)
	// +kubebuilder:default=false
	Paused bool `json:"paused,omitempty"`

	// NamespaceSelector selects namespaces to scan (for Namespace type)
	// +optional
	NamespaceSelector *NamespaceSelectorConfig `json:"namespaceSelector,omitempty"`

	// ConfigMapRef references a ConfigMap with provider definitions
	// +optional
	ConfigMapRef *ConfigMapReference `json:"configMapRef,omitempty"`

	// Annotations configures annotation-based discovery
	// +optional
	Annotations *AnnotationDiscoveryConfig `json:"annotations,omitempty"`

	// ServiceDiscovery configures service-based discovery
	// +optional
	ServiceDiscovery *ServiceDiscoveryConfig `json:"serviceDiscovery,omitempty"`

	// ProviderTemplate provides default settings for discovered providers
	// +optional
	ProviderTemplate *ProviderTemplateConfig `json:"providerTemplate,omitempty"`

	// Filters limit discovered providers
	// +optional
	Filters *DiscoveryFilters `json:"filters,omitempty"`

	// Ownership configures owner references
	// +optional
	Ownership *OwnershipConfig `json:"ownership,omitempty"`
}

// NamespaceSelectorConfig defines namespace selection
type NamespaceSelectorConfig struct {
	// MatchLabels selects namespaces with these labels
	MatchLabels map[string]string `json:"matchLabels,omitempty"`

	// MatchExpressions selects namespaces matching expressions
	MatchExpressions []metav1.LabelSelectorRequirement `json:"matchExpressions,omitempty"`

	// ExcludeNamespaces is a list of namespaces to exclude
	// +kubebuilder:default={"kube-system","kube-public","kube-node-lease"}
	ExcludeNamespaces []string `json:"excludeNamespaces,omitempty"`
}

// ConfigMapReference references a ConfigMap containing provider configs
type ConfigMapReference struct {
	// Name of the ConfigMap
	Name string `json:"name"`

	// Namespace of the ConfigMap (defaults to same namespace)
	// +optional
	Namespace string `json:"namespace,omitempty"`

	// Key within the ConfigMap containing provider definitions
	// +kubebuilder:default="providers.yaml"
	Key string `json:"key,omitempty"`
}

// AnnotationDiscoveryConfig configures annotation-based discovery
type AnnotationDiscoveryConfig struct {
	// PodSelector selects Pods to scan
	PodSelector map[string]string `json:"podSelector,omitempty"`

	// ServiceSelector selects Services to scan
	ServiceSelector map[string]string `json:"serviceSelector,omitempty"`

	// AnnotationPrefix is the prefix for MCP annotations
	// +kubebuilder:default="mcp-hangar.io"
	AnnotationPrefix string `json:"annotationPrefix,omitempty"`

	// RequiredAnnotations must be present for discovery
	// +kubebuilder:default={"mcp-hangar.io/provider"}
	RequiredAnnotations []string `json:"requiredAnnotations,omitempty"`
}

// ServiceDiscoveryConfig configures service-based discovery
type ServiceDiscoveryConfig struct {
	// Selector selects Services to discover
	Selector map[string]string `json:"selector,omitempty"`

	// PortName is the port name to use for MCP endpoint
	// +kubebuilder:default="mcp"
	PortName string `json:"portName,omitempty"`

	// Protocol is the endpoint protocol
	// +kubebuilder:default=http
	// +kubebuilder:validation:Enum=http;https
	Protocol string `json:"protocol,omitempty"`
}

// ProviderTemplateConfig provides defaults for discovered providers
type ProviderTemplateConfig struct {
	// Metadata contains default labels and annotations
	Metadata *TemplateMetadata `json:"metadata,omitempty"`

	// Spec contains default MCPProvider spec fields
	Spec *MCPProviderSpec `json:"spec,omitempty"`
}

// TemplateMetadata defines template metadata
type TemplateMetadata struct {
	Labels      map[string]string `json:"labels,omitempty"`
	Annotations map[string]string `json:"annotations,omitempty"`
}

// DiscoveryFilters limits discovered providers
type DiscoveryFilters struct {
	// IncludePatterns are regex patterns - only include matching names
	IncludePatterns []string `json:"includePatterns,omitempty"`

	// ExcludePatterns are regex patterns - exclude matching names
	ExcludePatterns []string `json:"excludePatterns,omitempty"`

	// MaxProviders limits the number of providers to discover
	// +optional
	MaxProviders *int32 `json:"maxProviders,omitempty"`
}

// OwnershipConfig defines ownership settings
type OwnershipConfig struct {
	// Controller sets controller owner reference
	// +kubebuilder:default=true
	Controller *bool `json:"controller,omitempty"`

	// BlockDeletion blocks deletion until providers removed
	// +kubebuilder:default=false
	BlockDeletion bool `json:"blockDeletion,omitempty"`
}

// MCPDiscoverySourceStatus defines the observed state of MCPDiscoverySource
type MCPDiscoverySourceStatus struct {
	// DiscoveredCount is the number of discovered providers
	DiscoveredCount int32 `json:"discoveredCount,omitempty"`

	// ManagedCount is the number of managed MCPProvider resources
	ManagedCount int32 `json:"managedCount,omitempty"`

	// LastSyncTime is the last successful sync time
	LastSyncTime *metav1.Time `json:"lastSyncTime,omitempty"`

	// LastSyncDuration is how long the last sync took
	LastSyncDuration string `json:"lastSyncDuration,omitempty"`

	// LastSyncError is the error from last sync (if any)
	LastSyncError string `json:"lastSyncError,omitempty"`

	// NextSyncTime is when the next sync is scheduled
	NextSyncTime *metav1.Time `json:"nextSyncTime,omitempty"`

	// DiscoveredProviders lists discovered providers
	DiscoveredProviders []DiscoveredProvider `json:"discoveredProviders,omitempty"`

	// ObservedGeneration is the generation observed by controller
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions represent the latest available observations
	Conditions []Condition `json:"conditions,omitempty"`
}

// DiscoveredProvider describes a discovered provider
type DiscoveredProvider struct {
	// Name of the provider
	Name string `json:"name"`

	// Source where it was discovered
	Source string `json:"source"`

	// DiscoveredAt is when it was discovered
	DiscoveredAt metav1.Time `json:"discoveredAt,omitempty"`

	// Managed indicates if MCPProvider was created
	Managed bool `json:"managed,omitempty"`

	// Error creating provider (if any)
	Error string `json:"error,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Type",type=string,JSONPath=`.spec.type`
// +kubebuilder:printcolumn:name="Mode",type=string,JSONPath=`.spec.mode`
// +kubebuilder:printcolumn:name="Discovered",type=integer,JSONPath=`.status.discoveredCount`
// +kubebuilder:printcolumn:name="Last Sync",type=date,JSONPath=`.status.lastSyncTime`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`
// +kubebuilder:resource:shortName=mcpds;discoverysource,categories=mcp

// MCPDiscoverySource is the Schema for the mcpdiscoverysources API
type MCPDiscoverySource struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   MCPDiscoverySourceSpec   `json:"spec,omitempty"`
	Status MCPDiscoverySourceStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// MCPDiscoverySourceList contains a list of MCPDiscoverySource
type MCPDiscoverySourceList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []MCPDiscoverySource `json:"items"`
}

func init() {
	SchemeBuilder.Register(&MCPDiscoverySource{}, &MCPDiscoverySourceList{})
}

// Helper methods

// IsAuthoritative returns true if in authoritative mode
func (d *MCPDiscoverySource) IsAuthoritative() bool {
	return d.Spec.Mode == DiscoveryModeAuthoritative
}

// IsPaused returns true if discovery is paused
func (d *MCPDiscoverySource) IsPaused() bool {
	return d.Spec.Paused
}

// ShouldSetController returns true if controller owner reference should be set
func (d *MCPDiscoverySource) ShouldSetController() bool {
	if d.Spec.Ownership == nil || d.Spec.Ownership.Controller == nil {
		return true // Default
	}
	return *d.Spec.Ownership.Controller
}

// SetCondition sets or updates a condition
func (s *MCPDiscoverySourceStatus) SetCondition(condType string, status metav1.ConditionStatus, reason, message string) {
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

	s.Conditions = append(s.Conditions, Condition{
		Type:               condType,
		Status:             status,
		LastTransitionTime: now,
		Reason:             reason,
		Message:            message,
	})
}
