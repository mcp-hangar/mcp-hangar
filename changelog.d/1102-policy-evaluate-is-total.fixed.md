**core:** a tool call whose arguments could not be serialized ended in a stack
trace instead of a verdict. `_serialize_arguments` caught `TypeError` and
`ValueError` and promised `None` "so the caller can fail closed rather than
crash", but JSON nesting the encoder cannot walk raises `RecursionError`, which
is neither. The depth that triggers it is an interpreter detail rather than a
constant: 992 levels on CPython 3.11 -- this project's baseline, so roughly
7 KB of payload, far under any `maxPayloadBytes` an operator would set -- and
9 997 on 3.12, 34 710 on 3.14. `maxPayloadBytes` could not have
helped either way: the size check reads the string the serializer returns, so
the guard sat behind the thing that broke. The exception propagated out of
`evaluate()` and out of `McpServer._enforce_l7_policy`, which called it with no
`try`.

In Enforce the call was refused, but by accident of ordering rather than by
decision: no `EgressPolicyDeniedError`, no observation, no audit entry -- the
call died unattributed. In **Audit** the exception aborted the call before
dispatch, so a policy switched on in the mode ADR-013 documents as the safe
adoption path was a hard block instead of an observation.

`evaluate()` is now total: argument inspection that fails for any reason
produces a DENY carrying a reason, which Enforce raises on and Audit records
and lets through. The failure is logged with the underlying error. The same fix
covers `default=str`, which invokes an arbitrary `__str__` and can raise
anything.
