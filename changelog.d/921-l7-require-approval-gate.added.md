`requireApproval` in an MCPEgressPolicy now routes matching tool calls into
the existing approval gate instead of failing closed as a slower deny. The
invoke path consults the L7 verdict alongside the MRTR tool-access policy,
blocks on `ApprovalGateService` (typed pending approval, `approval:resolve`
chokepoint, dispatch-time revalidation), and only a granted approval converts
the verdict -- deny still wins if the policy hardens during the hold, `Audit`
mode never asks a human, and a deployment with no approval channel stays
fail-closed exactly as before.
