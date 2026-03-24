package networkpolicy

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
)

func boolPtr(b bool) *bool                           { return &b }
func protocolPtr(p corev1.Protocol) *corev1.Protocol { return &p }

// --- NetworkPolicyName ---

func TestNetworkPolicyName(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		expected string
	}{
		{
			name:     "standard name",
			input:    "math-provider",
			expected: "mcp-provider-math-provider-egress",
		},
		{
			name:     "simple name",
			input:    "weather",
			expected: "mcp-provider-weather-egress",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := NetworkPolicyName(tt.input)
			assert.Equal(t, tt.expected, result)
		})
	}
}

// --- BuildNetworkPolicy ---

func TestBuildNetworkPolicy(t *testing.T) {
	tests := []struct {
		name     string
		provider *mcpv1alpha1.MCPProvider
		validate func(t *testing.T, np *networkingv1.NetworkPolicy)
	}{
		{
			name: "nil_capabilities",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: nil,
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				assert.Nil(t, np)
			},
		},
		{
			name: "nil_network",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: nil,
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				assert.Nil(t, np)
			},
		},
		{
			name: "empty_egress_dns_allowed",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress:     []mcpv1alpha1.EgressRuleSpec{},
							DNSAllowed: boolPtr(true),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				// Should have exactly 1 egress rule: DNS
				require.Len(t, np.Spec.Egress, 1)
				dnsRule := np.Spec.Egress[0]
				// DNS rule should have 2 ports: UDP 53 and TCP 53
				require.Len(t, dnsRule.Ports, 2)
				assertDNSPorts(t, dnsRule.Ports)
			},
		},
		{
			name: "empty_egress_dns_default_nil",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress:     []mcpv1alpha1.EgressRuleSpec{},
							DNSAllowed: nil, // default is true
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				// nil DNSAllowed defaults to true, so DNS rule should exist
				require.Len(t, np.Spec.Egress, 1)
				assertDNSPorts(t, np.Spec.Egress[0].Ports)
			},
		},
		{
			name: "cidr_rule_with_port",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress: []mcpv1alpha1.EgressRuleSpec{
								{
									Host:     "api.example.com",
									Port:     443,
									Protocol: "https",
									CIDR:     "203.0.113.0/24",
								},
							},
							DNSAllowed: boolPtr(true),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				// DNS rule + 1 CIDR rule
				require.Len(t, np.Spec.Egress, 2)

				cidrRule := np.Spec.Egress[1]
				// Should have IPBlock peer
				require.Len(t, cidrRule.To, 1)
				require.NotNil(t, cidrRule.To[0].IPBlock)
				assert.Equal(t, "203.0.113.0/24", cidrRule.To[0].IPBlock.CIDR)

				// Should have port 443 TCP
				require.Len(t, cidrRule.Ports, 1)
				assert.Equal(t, int32(443), cidrRule.Ports[0].Port.IntVal)
				assert.Equal(t, corev1.ProtocolTCP, *cidrRule.Ports[0].Protocol)
			},
		},
		{
			name: "host_only_rule",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress: []mcpv1alpha1.EgressRuleSpec{
								{
									Host:     "api.example.com",
									Port:     8080,
									Protocol: "http",
									// No CIDR -- host-only
								},
							},
							DNSAllowed: boolPtr(true),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				// DNS rule + 1 host-only rule
				require.Len(t, np.Spec.Egress, 2)

				hostRule := np.Spec.Egress[1]
				// No IPBlock peer for host-only rules
				assert.Empty(t, hostRule.To, "host-only rules should have no To peers")

				// Should have port 8080 TCP
				require.Len(t, hostRule.Ports, 1)
				assert.Equal(t, int32(8080), hostRule.Ports[0].Port.IntVal)
				assert.Equal(t, corev1.ProtocolTCP, *hostRule.Ports[0].Protocol)

				// Should have host-warnings annotation
				require.Contains(t, np.Annotations, "mcp-hangar.io/host-warnings")
				assert.Contains(t, np.Annotations["mcp-hangar.io/host-warnings"], "api.example.com")
			},
		},
		{
			name: "port_zero_any_port",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress: []mcpv1alpha1.EgressRuleSpec{
								{
									Host:     "internal-service",
									Port:     0, // any port
									Protocol: "tcp",
									CIDR:     "10.0.0.0/8",
								},
							},
							DNSAllowed: boolPtr(true),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				require.Len(t, np.Spec.Egress, 2)

				anyPortRule := np.Spec.Egress[1]
				// Port 0 means no Ports field
				assert.Empty(t, anyPortRule.Ports, "port=0 should omit Ports field")
				// But should still have IPBlock
				require.Len(t, anyPortRule.To, 1)
				assert.Equal(t, "10.0.0.0/8", anyPortRule.To[0].IPBlock.CIDR)
			},
		},
		{
			name: "dns_disabled",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress: []mcpv1alpha1.EgressRuleSpec{
								{
									Host:     "internal",
									Port:     443,
									Protocol: "https",
									CIDR:     "10.0.0.0/8",
								},
							},
							DNSAllowed: boolPtr(false),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				// No DNS rule, only the declared rule
				require.Len(t, np.Spec.Egress, 1)
				// The single rule should be the CIDR rule, not DNS
				assert.Len(t, np.Spec.Egress[0].To, 1)
				assert.Equal(t, "10.0.0.0/8", np.Spec.Egress[0].To[0].IPBlock.CIDR)
			},
		},
		{
			name: "loopback_allowed",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress:          []mcpv1alpha1.EgressRuleSpec{},
							DNSAllowed:      boolPtr(true),
							LoopbackAllowed: boolPtr(true),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				// DNS rule + loopback rule
				require.Len(t, np.Spec.Egress, 2)

				loopbackRule := np.Spec.Egress[1]
				require.Len(t, loopbackRule.To, 1)
				require.NotNil(t, loopbackRule.To[0].IPBlock)
				assert.Equal(t, "127.0.0.0/8", loopbackRule.To[0].IPBlock.CIDR)
			},
		},
		{
			name: "multiple_rules",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "multi-provider",
					Namespace: "production",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress: []mcpv1alpha1.EgressRuleSpec{
								{
									Host:     "api.example.com",
									Port:     443,
									Protocol: "https",
									CIDR:     "203.0.113.0/24",
								},
								{
									Host:     "db.internal",
									Port:     5432,
									Protocol: "tcp",
									CIDR:     "10.0.0.0/8",
								},
							},
							DNSAllowed: boolPtr(true),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				// DNS + 2 declared rules
				require.Len(t, np.Spec.Egress, 3)

				// First egress rule is DNS
				assertDNSPorts(t, np.Spec.Egress[0].Ports)

				// Second rule: api.example.com
				assert.Equal(t, "203.0.113.0/24", np.Spec.Egress[1].To[0].IPBlock.CIDR)
				assert.Equal(t, int32(443), np.Spec.Egress[1].Ports[0].Port.IntVal)

				// Third rule: db.internal
				assert.Equal(t, "10.0.0.0/8", np.Spec.Egress[2].To[0].IPBlock.CIDR)
				assert.Equal(t, int32(5432), np.Spec.Egress[2].Ports[0].Port.IntVal)
			},
		},
		{
			name: "protocol_mapping_https_to_tcp",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress: []mcpv1alpha1.EgressRuleSpec{
								{Host: "a", Port: 443, Protocol: "https", CIDR: "10.0.0.1/32"},
							},
							DNSAllowed: boolPtr(false),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				require.Len(t, np.Spec.Egress, 1)
				require.Len(t, np.Spec.Egress[0].Ports, 1)
				assert.Equal(t, corev1.ProtocolTCP, *np.Spec.Egress[0].Ports[0].Protocol)
			},
		},
		{
			name: "protocol_mapping_any_omits_protocol",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress: []mcpv1alpha1.EgressRuleSpec{
								{Host: "a", Port: 8080, Protocol: "any", CIDR: "10.0.0.1/32"},
							},
							DNSAllowed: boolPtr(false),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				require.Len(t, np.Spec.Egress, 1)
				require.Len(t, np.Spec.Egress[0].Ports, 1)
				assert.Nil(t, np.Spec.Egress[0].Ports[0].Protocol, "protocol 'any' should produce nil Protocol")
			},
		},
		{
			name: "protocol_mapping_http_grpc_tcp",
			provider: &mcpv1alpha1.MCPProvider{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-provider",
					Namespace: "default",
				},
				Spec: mcpv1alpha1.MCPProviderSpec{
					Capabilities: &mcpv1alpha1.ProviderCapabilities{
						Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
							Egress: []mcpv1alpha1.EgressRuleSpec{
								{Host: "a", Port: 80, Protocol: "http", CIDR: "10.0.0.1/32"},
								{Host: "b", Port: 50051, Protocol: "grpc", CIDR: "10.0.0.2/32"},
								{Host: "c", Port: 9090, Protocol: "tcp", CIDR: "10.0.0.3/32"},
							},
							DNSAllowed: boolPtr(false),
						},
					},
				},
			},
			validate: func(t *testing.T, np *networkingv1.NetworkPolicy) {
				require.NotNil(t, np)
				require.Len(t, np.Spec.Egress, 3)
				for i := range np.Spec.Egress {
					require.Len(t, np.Spec.Egress[i].Ports, 1)
					assert.Equal(t, corev1.ProtocolTCP, *np.Spec.Egress[i].Ports[0].Protocol,
						"protocol at index %d should be TCP", i)
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			np := BuildNetworkPolicy(tt.provider)
			tt.validate(t, np)
		})
	}
}

// --- Labels ---

func TestBuildNetworkPolicy_Labels(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-provider",
			Namespace: "default",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					DNSAllowed: boolPtr(true),
				},
			},
		},
	}

	np := BuildNetworkPolicy(provider)
	require.NotNil(t, np)

	assert.Equal(t, "mcp-hangar-operator", np.Labels["app.kubernetes.io/managed-by"])
	assert.Equal(t, "test-provider", np.Labels["mcp-hangar.io/provider"])
	assert.Equal(t, "network-policy", np.Labels["mcp-hangar.io/component"])
}

// --- PodSelector ---

func TestBuildNetworkPolicy_PodSelector(t *testing.T) {
	provider := &mcpv1alpha1.MCPProvider{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "math-provider",
			Namespace: "mcp-system",
		},
		Spec: mcpv1alpha1.MCPProviderSpec{
			Capabilities: &mcpv1alpha1.ProviderCapabilities{
				Network: &mcpv1alpha1.NetworkCapabilitiesSpec{
					DNSAllowed: boolPtr(true),
				},
			},
		},
	}

	np := BuildNetworkPolicy(provider)
	require.NotNil(t, np)

	assert.Equal(t, "mcp-provider-math-provider-egress", np.Name)
	assert.Equal(t, "mcp-system", np.Namespace)
	assert.Equal(t, "math-provider", np.Spec.PodSelector.MatchLabels["mcp-hangar.io/provider"])
	require.Len(t, np.Spec.PolicyTypes, 1)
	assert.Equal(t, networkingv1.PolicyTypeEgress, np.Spec.PolicyTypes[0])
}

// --- Helper ---

func assertDNSPorts(t *testing.T, ports []networkingv1.NetworkPolicyPort) {
	t.Helper()
	require.Len(t, ports, 2, "DNS rule should have 2 ports (UDP 53 + TCP 53)")

	var hasUDP, hasTCP bool
	for _, p := range ports {
		require.NotNil(t, p.Port)
		assert.Equal(t, int32(53), p.Port.IntVal)
		require.NotNil(t, p.Protocol)
		if *p.Protocol == corev1.ProtocolUDP {
			hasUDP = true
		}
		if *p.Protocol == corev1.ProtocolTCP {
			hasTCP = true
		}
	}
	assert.True(t, hasUDP, "DNS rule should include UDP 53")
	assert.True(t, hasTCP, "DNS rule should include TCP 53")
}
