// Package v1alpha1 contains API Schema definitions for the mcp-hangar.io v1alpha1 API group
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// LoadBalancingStrategy defines the load balancing algorithm
// +kubebuilder:validation:Enum=RoundRobin;LeastConnections;Random;Weighted;Failover
type LoadBalancingStrategy string

const (
	StrategyRoundRobin       LoadBalancingStrategy = "RoundRobin"
	StrategyLeastConnections LoadBalancingStrategy = "LeastConnections"
	StrategyRandom           LoadBalancingStrategy = "Random"
	StrategyWeighted         LoadBalancingStrategy = "Weighted"
	StrategyFailover         LoadBalancingStrategy = "Failover"
)

// MCPProviderGroupSpec defines the desired state of MCPProviderGroup
type MCPProviderGroupSpec struct {
	// Selector selects MCPProviders to include in the group
	// +kubebuilder:validation:Required
	Selector *metav1.LabelSelector `json:"selector"`

	// Strategy is the load balancing strategy
	// +kubebuilder:default=RoundRobin
	Strategy LoadBalancingStrategy `json:"strategy,omitempty"`

	// Failover configures failover behavior
	// +optional
	Failover *FailoverConfig `json:"failover,omitempty"`

	// HealthPolicy defines group health requirements
	// +optional
	HealthPolicy *HealthPolicy `json:"healthPolicy,omitempty"`

	// SessionAffinity configures session stickiness
	// +optional
	SessionAffinity *SessionAffinityConfig `json:"sessionAffinity,omitempty"`

	// CircuitBreaker configures group-level circuit breaker
	// +optional
	CircuitBreaker *GroupCircuitBreakerConfig `json:"circuitBreaker,omitempty"`
}

// FailoverConfig defines failover settings
type FailoverConfig struct {
	// Enabled enables automatic failover
	// +kubebuilder:default=true
	Enabled *bool `json:"enabled,omitempty"`

	// MaxRetries is the maximum retry attempts
	// +kubebuilder:default=2
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=10
	MaxRetries int32 `json:"maxRetries,omitempty"`

	// RetryDelay is the delay between retries
	// +kubebuilder:default="1s"
	RetryDelay string `json:"retryDelay,omitempty"`

	// RetryOn lists conditions that trigger retry
	// +kubebuilder:default={"timeout","connection_error"}
	RetryOn []string `json:"retryOn,omitempty"`
}

// HealthPolicy defines group health requirements
type HealthPolicy struct {
	// MinHealthyPercentage is minimum healthy providers percentage
	// +kubebuilder:default=50
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=100
	MinHealthyPercentage int32 `json:"minHealthyPercentage,omitempty"`

	// MinHealthyCount is minimum healthy provider count (overrides percentage)
	// +optional
	MinHealthyCount *int32 `json:"minHealthyCount,omitempty"`

	// UnhealthyThreshold is consecutive failures before marking unhealthy
	// +kubebuilder:default=3
	// +kubebuilder:validation:Minimum=1
	UnhealthyThreshold int32 `json:"unhealthyThreshold,omitempty"`
}

// SessionAffinityConfig defines session affinity settings
type SessionAffinityConfig struct {
	// Enabled enables session affinity
	// +kubebuilder:default=false
	Enabled bool `json:"enabled,omitempty"`

	// Type is the affinity type (ClientIP or Header)
	// +kubebuilder:default=ClientIP
	// +kubebuilder:validation:Enum=ClientIP;Header
	Type string `json:"type,omitempty"`

	// Header is the header name for Header affinity type
	// +optional
	Header string `json:"header,omitempty"`

	// TTL is the session TTL
	// +kubebuilder:default="10m"
	TTL string `json:"ttl,omitempty"`
}

// GroupCircuitBreakerConfig defines group-level circuit breaker
type GroupCircuitBreakerConfig struct {
	// Enabled enables group circuit breaker
	// +kubebuilder:default=false
	Enabled bool `json:"enabled,omitempty"`

	// FailureThreshold before opening circuit
	// +kubebuilder:default=10
	FailureThreshold int32 `json:"failureThreshold,omitempty"`

	// ResetTimeout before attempting recovery
	// +kubebuilder:default="1m"
	ResetTimeout string `json:"resetTimeout,omitempty"`
}

// MCPProviderGroupStatus defines the observed state of MCPProviderGroup
type MCPProviderGroupStatus struct {
	// ProviderCount is total providers in group
	ProviderCount int32 `json:"providerCount,omitempty"`

	// ReadyCount is the number of ready providers
	ReadyCount int32 `json:"readyCount,omitempty"`

	// DegradedCount is the number of degraded providers
	DegradedCount int32 `json:"degradedCount,omitempty"`

	// ColdCount is the number of cold providers
	ColdCount int32 `json:"coldCount,omitempty"`

	// DeadCount is the number of dead providers
	DeadCount int32 `json:"deadCount,omitempty"`

	// ActiveStrategy is the currently active strategy
	ActiveStrategy string `json:"activeStrategy,omitempty"`

	// Providers contains provider member details
	Providers []ProviderMemberStatus `json:"providers,omitempty"`

	// ObservedGeneration is the generation observed by controller
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions represent the latest available observations
	Conditions []Condition `json:"conditions,omitempty"`
}

// ProviderMemberStatus defines the status of a group member
type ProviderMemberStatus struct {
	// Name of the provider
	Name string `json:"name"`

	// Namespace of the provider
	Namespace string `json:"namespace"`

	// State of the provider
	State string `json:"state,omitempty"`

	// Weight for weighted load balancing
	Weight int32 `json:"weight,omitempty"`

	// ActiveConnections for least connections strategy
	ActiveConnections int32 `json:"activeConnections,omitempty"`

	// LastHealthCheck time
	LastHealthCheck *metav1.Time `json:"lastHealthCheck,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Strategy",type=string,JSONPath=`.spec.strategy`
// +kubebuilder:printcolumn:name="Providers",type=integer,JSONPath=`.status.providerCount`
// +kubebuilder:printcolumn:name="Ready",type=integer,JSONPath=`.status.readyCount`
// +kubebuilder:printcolumn:name="Degraded",type=integer,JSONPath=`.status.degradedCount`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`
// +kubebuilder:resource:shortName=mcppg;providergroup,categories=mcp

// MCPProviderGroup is the Schema for the mcpprovidergroups API
type MCPProviderGroup struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   MCPProviderGroupSpec   `json:"spec,omitempty"`
	Status MCPProviderGroupStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// MCPProviderGroupList contains a list of MCPProviderGroup
type MCPProviderGroupList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []MCPProviderGroup `json:"items"`
}

func init() {
	SchemeBuilder.Register(&MCPProviderGroup{}, &MCPProviderGroupList{})
}

// Helper methods

// IsFailoverEnabled returns true if failover is enabled
func (g *MCPProviderGroup) IsFailoverEnabled() bool {
	if g.Spec.Failover == nil || g.Spec.Failover.Enabled == nil {
		return true // Default enabled
	}
	return *g.Spec.Failover.Enabled
}

// GetMaxRetries returns the maximum retry count
func (g *MCPProviderGroup) GetMaxRetries() int32 {
	if g.Spec.Failover == nil {
		return 2 // Default
	}
	return g.Spec.Failover.MaxRetries
}

// IsHealthy returns true if the group meets health requirements
func (s *MCPProviderGroupStatus) IsHealthy(policy *HealthPolicy) bool {
	if s.ProviderCount == 0 {
		return false
	}

	if policy == nil {
		return s.ReadyCount > 0
	}

	// Check minimum count first
	if policy.MinHealthyCount != nil {
		return s.ReadyCount >= *policy.MinHealthyCount
	}

	// Check percentage
	percentage := (s.ReadyCount * 100) / s.ProviderCount
	return percentage >= policy.MinHealthyPercentage
}

// SetCondition sets or updates a condition
func (s *MCPProviderGroupStatus) SetCondition(condType string, status metav1.ConditionStatus, reason, message string) {
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
