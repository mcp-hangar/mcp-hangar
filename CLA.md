# Contributor License Agreement

**MCP Hangar Project**
Effective date: 2026-03-24

## Overview

This Contributor License Agreement ("CLA") governs contributions to the MCP Hangar project.

The project uses a dual-license model:

- **Core** (`src/mcp_hangar/`, `packages/operator/`, `packages/helm-charts/`) — MIT License.
  No CLA required for core contributions.
- **Enterprise** (`enterprise/`) — Business Source License 1.1 (BSL 1.1).
  **CLA required for all enterprise/ contributions.**

If your contribution touches only MIT-licensed files, you do not need to sign this agreement.
If your contribution includes any file under `enterprise/`, this CLA applies.

---

## Why a CLA for enterprise/ contributions?

The BSL 1.1 license includes a Change Date after which each release converts to MIT.
To enable this conversion — and to maintain licensing authority over the enterprise codebase —
the project maintainer needs a license grant over all contributed code.

Without a CLA, accepting enterprise/ contributions would prevent future relicensing.

---

## Grant of Rights

By submitting a pull request that modifies or adds files under `enterprise/`, you ("Contributor"):

1. **Grant of Copyright License.** You grant the project maintainer (Marcin Pyrka,
   "Maintainer") a perpetual, worldwide, non-exclusive, royalty-free, irrevocable copyright
   license to reproduce, prepare derivative works of, publicly display, publicly perform,
   sublicense, and distribute your contributions and such derivative works.

2. **Grant of Patent License.** You grant the Maintainer a perpetual, worldwide, non-exclusive,
   royalty-free, irrevocable patent license to make, have made, use, offer to sell, sell, import,
   and otherwise transfer the work, where such license applies only to patent claims licensable
   by you that are necessarily infringed by your contribution or by the combination of your
   contribution with the project.

3. **Right to Relicense.** You grant the Maintainer the right to change the license of your
   contributions, including but not limited to converting from BSL 1.1 to MIT at the Change Date
   defined in `enterprise/LICENSE.BSL`.

4. **Retention of Rights.** You retain copyright ownership of your contributions. This CLA does
   not transfer ownership — it grants a license.

---

## Representations

By submitting enterprise/ contributions, you represent that:

- You are legally entitled to grant the above license.
- If your employer has rights to intellectual property you create, you have received permission
  to make contributions on behalf of your employer, or your employer has waived such rights.
- Your contribution is your original creation or you have the right to submit it under the
  terms described above.
- You are not aware of any third-party claims that would affect the license grants above.

---

## How to Agree

You do not need to sign a physical document. Agreement to this CLA is indicated by including
the following statement in your pull request description when contributing to `enterprise/`:

```
I have read and agree to the MCP Hangar Contributor License Agreement (CLA.md).
My contribution to enterprise/ is my original work and I grant the rights described therein.
```

GitHub: A CLA-check GitHub Action will be added to automate this process. Until then,
manual confirmation in the PR description is sufficient.

---

## Core (MIT) Contributions

Contributions to `src/mcp_hangar/`, `packages/operator/`, `packages/helm-charts/`,
`packages/ui/`, `tests/`, `docs/`, `scripts/`, `monitoring/`, or `examples/` are governed
exclusively by the [MIT License](LICENSE). No CLA is required.

---

## Questions

Questions about this CLA: open a GitHub Discussion or email the maintainer.
