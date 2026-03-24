// Package networkpolicy contains utilities for generating Kubernetes NetworkPolicy
// resources from MCPProvider capability declarations.
package networkpolicy

import (
	"strings"

	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"

	mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
)

const (
	// LabelManagedBy identifies resources managed by the operator.
	LabelManagedBy = "app.kubernetes.io/managed-by"
	// LabelProvider identifies the owning MCPProvider.
	LabelProvider = "mcp-hangar.io/provider"
	// LabelComponent classifies the resource type within the operator.
	LabelComponent = "mcp-hangar.io/component"

	// AnnotationHostWarnings lists host-only rules that lack CIDR enforcement.
	AnnotationHostWarnings = "mcp-hangar.io/host-warnings"

	// DefaultManagerName is the value for managed-by labels.
	DefaultManagerName = "mcp-hangar-operator"

	// componentNetworkPolicy is the component label value.
	componentNetworkPolicy = "network-policy"
)

// NetworkPolicyName returns the canonical name for a provider's egress NetworkPolicy.
func NetworkPolicyName(providerName string) string {
	return "mcp-provider-" + providerName + "-egress"
}

// BuildNetworkPolicy translates an MCPProvider's capability declarations into a
// Kubernetes NetworkPolicy resource. Returns nil if the provider declares no
// network capabilities.
//
// This is a pure function with no side effects -- it reads from the provider
// struct and returns a new NetworkPolicy. The reconciler is responsible for
// creating/updating the resource in the cluster.
func BuildNetworkPolicy(provider *mcpv1alpha1.MCPProvider) *networkingv1.NetworkPolicy {
	if provider.Spec.Capabilities == nil || provider.Spec.Capabilities.Network == nil {
		return nil
	}

	caps := provider.Spec.Capabilities.Network

	annotations := map[string]string{}
	warnings := hostWarnings(caps)
	if warnings != "" {
		annotations[AnnotationHostWarnings] = warnings
	}

	return &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:        NetworkPolicyName(provider.Name),
			Namespace:   provider.Namespace,
			Labels:      buildLabels(provider),
			Annotations: annotations,
		},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{
				MatchLabels: map[string]string{
					LabelProvider: provider.Name,
				},
			},
			PolicyTypes: []networkingv1.PolicyType{
				networkingv1.PolicyTypeEgress,
			},
			Egress: buildEgressRules(caps),
		},
	}
}

// buildLabels returns standard labels for the NetworkPolicy resource.
func buildLabels(provider *mcpv1alpha1.MCPProvider) map[string]string {
	return map[string]string{
		LabelManagedBy: DefaultManagerName,
		LabelProvider:  provider.Name,
		LabelComponent: componentNetworkPolicy,
	}
}

// buildEgressRules constructs the full list of egress rules from the network
// capabilities spec. Order: DNS (if allowed), loopback (if allowed), then
// declared egress rules in declaration order.
func buildEgressRules(caps *mcpv1alpha1.NetworkCapabilitiesSpec) []networkingv1.NetworkPolicyEgressRule {
	var rules []networkingv1.NetworkPolicyEgressRule

	// DNS: default-allow unless explicitly disabled
	if caps.DNSAllowed == nil || *caps.DNSAllowed {
		rules = append(rules, dnsEgressRule())
	}

	// Loopback: only if explicitly enabled
	if caps.LoopbackAllowed != nil && *caps.LoopbackAllowed {
		rules = append(rules, loopbackEgressRule())
	}

	// Declared egress rules
	for _, rule := range caps.Egress {
		rules = append(rules, translateEgressRule(rule))
	}

	return rules
}

// translateEgressRule converts a single EgressRuleSpec into a NetworkPolicy egress rule.
//
// CIDR rules produce IPBlock peers. Host-only rules (no CIDR) produce port-only
// rules because Kubernetes NetworkPolicy does not support hostname-based filtering.
// Host-only rules are documented via the host-warnings annotation.
//
// Port 0 means "any port" and omits the Ports field entirely.
func translateEgressRule(rule mcpv1alpha1.EgressRuleSpec) networkingv1.NetworkPolicyEgressRule {
	egressRule := networkingv1.NetworkPolicyEgressRule{}

	// Peer: IPBlock from CIDR
	if rule.CIDR != "" {
		egressRule.To = []networkingv1.NetworkPolicyPeer{
			{
				IPBlock: &networkingv1.IPBlock{
					CIDR: rule.CIDR,
				},
			},
		}
	}
	// Host-only (no CIDR): no To peers -- port-level enforcement only

	// Ports: omit for port=0 (any port)
	if rule.Port > 0 {
		egressRule.Ports = []networkingv1.NetworkPolicyPort{
			{
				Port:     portPtr(rule.Port),
				Protocol: mapProtocol(rule.Protocol),
			},
		}
	}

	return egressRule
}

// mapProtocol converts an application-level protocol hint to a Kubernetes network
// protocol. "https", "http", "grpc", and "tcp" all map to TCP. "any" or empty
// returns nil (meaning any protocol).
func mapProtocol(protocol string) *corev1.Protocol {
	switch strings.ToLower(protocol) {
	case "https", "http", "grpc", "tcp":
		p := corev1.ProtocolTCP
		return &p
	default:
		// "any" or "" -- allow any protocol
		return nil
	}
}

// dnsEgressRule returns an egress rule allowing DNS queries (UDP + TCP port 53).
func dnsEgressRule() networkingv1.NetworkPolicyEgressRule {
	return networkingv1.NetworkPolicyEgressRule{
		Ports: []networkingv1.NetworkPolicyPort{
			{
				Port:     portPtr(53),
				Protocol: protocolPtr(corev1.ProtocolUDP),
			},
			{
				Port:     portPtr(53),
				Protocol: protocolPtr(corev1.ProtocolTCP),
			},
		},
	}
}

// loopbackEgressRule returns an egress rule allowing traffic to 127.0.0.0/8.
func loopbackEgressRule() networkingv1.NetworkPolicyEgressRule {
	return networkingv1.NetworkPolicyEgressRule{
		To: []networkingv1.NetworkPolicyPeer{
			{
				IPBlock: &networkingv1.IPBlock{
					CIDR: "127.0.0.0/8",
				},
			},
		},
	}
}

// hostWarnings returns a comma-separated list of host-only egress rules (those
// without CIDR) that cannot be enforced at the network level.
func hostWarnings(caps *mcpv1alpha1.NetworkCapabilitiesSpec) string {
	var warnings []string
	for _, rule := range caps.Egress {
		if rule.CIDR == "" && rule.Host != "" {
			warnings = append(warnings, rule.Host)
		}
	}
	return strings.Join(warnings, ",")
}

// portPtr returns a pointer to an intstr.IntOrString from an int32 port number.
func portPtr(port int32) *intstr.IntOrString {
	p := intstr.FromInt32(port)
	return &p
}

// protocolPtr returns a pointer to a corev1.Protocol.
func protocolPtr(p corev1.Protocol) *corev1.Protocol {
	return &p
}
