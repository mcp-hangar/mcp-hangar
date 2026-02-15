---
phase: 03-jwt-lifetime-enforcement
plan: 01
subsystem: infrastructure/auth
tags: [security, jwt, authentication, lifetime-enforcement]
completed: 2026-02-15T17:52:43Z

dependency_graph:
  requires: []
  provides:
    - JWT lifetime enforcement (max_token_lifetime)
    - TokenLifetimeExceededError exception
    - Configurable max_token_lifetime (YAML + env var)
  affects:
    - JWTAuthenticator
    - OIDCConfig
    - OIDCAuthConfig

tech_stack:
  added: []
  patterns:
    - TDD (RED-GREEN pattern)
    - Token lifetime validation (exp - iat)
    - Environment variable override (MCP_JWT_MAX_TOKEN_LIFETIME)
    - Constant-time comparison for security-sensitive code

key_files:
  created:
    - packages/core/tests/unit/test_jwt_lifetime.py
  modified:
    - packages/core/mcp_hangar/domain/exceptions.py
    - packages/core/mcp_hangar/infrastructure/auth/jwt_authenticator.py
    - packages/core/mcp_hangar/server/auth_config.py
    - packages/core/mcp_hangar/server/auth_bootstrap.py

decisions:
  - decision: "Default max_token_lifetime to 3600 seconds (1 hour)"
    rationale: "Balance between usability and security. 1 hour is common for access tokens, prevents excessively long-lived tokens while not requiring frequent re-auth"
    alternatives: ["600s (10min)", "7200s (2hr)", "86400s (24hr)"]
  - decision: "Setting max_token_lifetime=0 disables the check"
    rationale: "Provides escape hatch for environments that need to disable lifetime enforcement without removing config field"
    alternatives: ["Negative values disable", "No disable option"]
  - decision: "Enforce lifetime check before creating Principal"
    rationale: "Fail fast - reject invalid tokens before doing work. Keeps _claims_to_principal focused on claim mapping only"
    alternatives: ["Check after Principal creation", "Check in token validator"]
  - decision: "Raise specific TokenLifetimeExceededError rather than generic InvalidCredentialsError"
    rationale: "Clear error messages improve debugging and logging. Allows specific handling (e.g., different retry logic)"
    alternatives: ["Generic InvalidCredentialsError", "Reuse ExpiredCredentialsError"]
  - decision: "Env var MCP_JWT_MAX_TOKEN_LIFETIME overrides YAML config"
    rationale: "Follows existing MCP_* env var pattern. Enables per-environment tuning without config changes"
    alternatives: ["YAML only", "Separate env var per deployment"]

metrics:
  duration_seconds: 235
  duration_minutes: 3.9
  tasks_completed: 2
  files_created: 1
  files_modified: 4
  tests_added: 13
  tests_passing: 13
  commits: 2
---

# Phase 03 Plan 01: JWT Lifetime Enforcement Summary

JWT lifetime enforcement with configurable max_token_lifetime using TDD approach.

## Implementation

Added `max_token_lifetime` enforcement to `JWTAuthenticator` that rejects tokens where `(exp - iat) > max_token_lifetime`. Configurable via YAML (`oidc.max_token_lifetime_seconds`) and env var (`MCP_JWT_MAX_TOKEN_LIFETIME`), defaults to 3600 seconds. Tokens exceeding the limit are rejected with specific `TokenLifetimeExceededError` containing actual vs max lifetime for clear debugging.

## TDD Process

**RED Phase (Commit 1b3ff2a):**

- Created comprehensive test suite with 13 test cases covering:
  - Token within/exceeding lifetime limits
  - Boundary cases (exactly at limit, one second over)
  - Missing iat/exp claim validation
  - Default (3600s) and custom max_token_lifetime values
  - Disabled check (max_token_lifetime=0)
  - Config parsing with YAML and default values
- Added `TokenLifetimeExceededError` exception to domain/exceptions.py
- All 13 tests FAILED as expected (implementation not yet added)

**GREEN Phase (Commit fc64142):**

- Added `max_token_lifetime: int = 3600` field to `OIDCConfig` dataclass
- Implemented `_enforce_token_lifetime()` method in `JWTAuthenticator`:
  - Validates required iat/exp claims are present
  - Computes `token_lifetime = exp - iat`
  - Raises `TokenLifetimeExceededError` if lifetime exceeds max
  - Skips check when `max_token_lifetime <= 0` (disabled)
- Added `max_token_lifetime_seconds: int = 3600` to `OIDCAuthConfig`
- Updated `parse_auth_config()` to parse YAML field with env var override
- Wired `max_token_lifetime` in `bootstrap_auth()` when creating `OIDCConfig`
- All 13 tests now PASS (GREEN phase complete)
- No regressions: 60 existing auth tests still pass

## Deviations from Plan

None - plan executed exactly as written.

## Key Files

**Created:**

- `packages/core/tests/unit/test_jwt_lifetime.py` - 13 test cases for lifetime enforcement

**Modified:**

- `packages/core/mcp_hangar/domain/exceptions.py` - Added `TokenLifetimeExceededError`
- `packages/core/mcp_hangar/infrastructure/auth/jwt_authenticator.py` - Added `max_token_lifetime` field and `_enforce_token_lifetime()` method
- `packages/core/mcp_hangar/server/auth_config.py` - Added `max_token_lifetime_seconds` field and env var parsing
- `packages/core/mcp_hangar/server/auth_bootstrap.py` - Wire `max_token_lifetime` to `OIDCConfig`

## Verification Results

1. All 13 JWT lifetime tests pass
2. No regressions in existing auth tests (60 tests pass)
3. `TokenLifetimeExceededError` importable from `mcp_hangar.domain.exceptions`
4. `max_token_lifetime` present in OIDCConfig, OIDCAuthConfig, auth_config.py, auth_bootstrap.py, and jwt_authenticator.py
5. `MCP_JWT_MAX_TOKEN_LIFETIME` env var supported in auth_config.py

## Self-Check

**File existence:**

```
FOUND: packages/core/tests/unit/test_jwt_lifetime.py
FOUND: packages/core/mcp_hangar/domain/exceptions.py
FOUND: packages/core/mcp_hangar/infrastructure/auth/jwt_authenticator.py
FOUND: packages/core/mcp_hangar/server/auth_config.py
FOUND: packages/core/mcp_hangar/server/auth_bootstrap.py
```

**Commit existence:**

```
FOUND: 1b3ff2a (RED phase - failing tests)
FOUND: fc64142 (GREEN phase - implementation)
```

**Test results:**

```
13 JWT lifetime tests: PASS
60 existing auth tests: PASS
```

**Grep verification:**

```
TokenLifetimeExceededError: Found in exceptions.py, jwt_authenticator.py, test file
max_token_lifetime: Found in jwt_authenticator.py, auth_config.py, auth_bootstrap.py
MCP_JWT_MAX_TOKEN_LIFETIME: Found in auth_config.py
```

## Self-Check: PASSED

All artifacts created as planned. All tests pass. No missing files or commits.
