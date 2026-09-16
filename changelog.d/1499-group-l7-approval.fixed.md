**core:** a tool call that names a group now asks a human for the
`requireApproval` rule of the member it is routed to. The approval gate looked
the L7 egress policy up by the id the call named, and a group id is not a
server id -- the server repository holds no groups -- so a call naming a group
read no policy at all. The member's `requireApproval` rule never reached an
approver, and the member's own check then refused the call on invoke: an
operator who configured "ask a human" got "refuse". A call's governance
decision now records the policy of the server the call is routed to -- the one
it names, or, for a group, the member the group selected -- and the rule is
applied as it is for a call that names that member directly. A call naming the
member directly is unchanged, as is the refusal a call gets when no approver is
configured.
