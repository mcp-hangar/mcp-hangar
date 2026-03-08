# Roadmap: MCP Hangar

## Milestones

- ✅ **v0.9 Security Hardening** -- Phases 1-4 (shipped 2026-02-15)
- ✅ **v0.10 Documentation & Kubernetes Maturity** -- Phases 5-7 (shipped 2026-03-01)
- 🚧 **v1.0 Production Hardening** -- Phases 8-10 (in progress)

## Phases

<details>
<summary>✅ v0.9 Security Hardening (Phases 1-4) -- SHIPPED 2026-02-15</summary>

- [x] Phase 1: Timing Attack Prevention (2/2 plans) -- completed 2026-02-15
- [x] Phase 2: Rate Limiter Hardening (2/2 plans) -- completed 2026-02-15
- [x] Phase 3: JWT Lifetime Enforcement (1/1 plan) -- completed 2026-02-15
- [x] Phase 4: API Key Rotation (2/2 plans) -- completed 2026-02-15

</details>

<details>
<summary>✅ v0.10 Documentation & Kubernetes Maturity (Phases 5-7) -- SHIPPED 2026-03-01</summary>

- [x] Phase 5: Documentation Content (2/2 plans) -- completed 2026-02-28
- [x] Phase 6: Kubernetes Controllers (3/3 plans) -- completed 2026-03-01
- [x] Phase 7: Helm Chart Maturity (1/1 plan) -- completed 2026-03-01

</details>

### 🚧 v1.0 Production Hardening (In Progress)

**Milestone Goal:** Harden MCP Hangar for production by fixing concurrency hazards, cleaning up exception handling, persisting critical state across restarts, and improving operational resilience with bounded startup, intelligent health checks, transport-agnostic rate limiting, and strict typing.

- [ ] **Phase 8: Safety Foundation** - Fix concurrency hazards, clean up exception handling, validate discovery-sourced commands (3 plans)
- [ ] **Phase 9: State Survival** - Persist saga checkpoints and circuit breaker state across restarts
- [ ] **Phase 10: Operational Hardening** - Event store snapshots, health check backoff, rate limiter middleware, Docker resilience, property-based testing, typing strictness

## Phase Details

### Phase 8: Safety Foundation

**Goal**: The codebase holds locks correctly, propagates exceptions specifically, and validates commands from untrusted discovery sources -- establishing the trustworthy foundation all subsequent hardening builds on
**Depends on**: Phase 7 (prior milestone)
**Requirements**: CONC-01, CONC-02, CONC-03, CONC-04, EXCP-01, SECR-01
**Success Criteria** (what must be TRUE):

  1. ProviderGroup operations that start member providers never hold the group lock while acquiring a provider lock -- the level-11-holds-level-10 deadlock path is eliminated
  2. Provider cold starts (subprocess launch, tool discovery) perform all I/O outside the provider lock, using INITIALIZING state guard so concurrent callers wait via threading.Event instead of blocking on the lock
  3. StdioClient request-response matching is race-free -- PendingRequest is registered before the request is written to stdin, so no response can arrive before its handler exists
  4. All 42 bare `except Exception:` catches are resolved -- fault-barriers kept with structured logging, cleanup paths narrowed to specific exceptions, bug-hiding catches removed or replaced
  5. Provider commands sourced from Docker labels or Kubernetes annotations are validated against a command allowlist before registration, preventing command injection from untrusted discovery sources
**Plans**: 3 plans
Plans:

- [x] 08-01-PLAN.md -- Quick wins + security + group lock fix (CONC-04, SECR-01, CONC-01) -- completed 2026-03-08
- [x] 08-02-PLAN.md -- Provider concurrency refactor (CONC-02, CONC-03) -- completed 2026-03-08
- [x] 08-03-PLAN.md -- Exception hygiene audit (EXCP-01) -- completed 2026-03-08

### Phase 9: State Survival

**Goal**: Saga and circuit breaker state survives process restarts -- incomplete sagas resume from their last checkpoint and circuit breakers remember known-bad providers
**Depends on**: Phase 8 (exception hygiene must be trustworthy before saga error handling; lock restructuring must be complete before persistence adds new I/O paths)
**Requirements**: PERS-01, PERS-02, PERS-03
**Success Criteria** (what must be TRUE):

  1. Saga state is checkpointed to SQLite after each step transition, and incomplete sagas are detected and resumed on bootstrap without emitting duplicate commands during event replay
  2. Circuit breaker state (state, failure_count, opened_at) persists in provider snapshots and is restored on restart, so previously-tripped breakers remain open against known-bad providers
  3. Both saga checkpoints and circuit breaker state use the existing SQLiteConnectionFactory and MigrationRunner infrastructure -- no new database connections or migration systems
**Plans**: 3 plans
Plans:

- [x] 09-01-PLAN.md -- Saga persistence foundation: SagaStateStore + saga serialization + SagaManager checkpoint (PERS-01) -- completed 2026-03-08
- [x] 09-02-PLAN.md -- Circuit breaker persistence: CB from_dict + ProviderSnapshot CB fields (PERS-03) -- completed 2026-03-08
- [ ] 09-03-PLAN.md -- Idempotency filter + bootstrap wiring: saga resume + CB restore (PERS-02, PERS-03)

### Phase 10: Operational Hardening

**Goal**: Startup time is bounded by snapshots, health checks use intelligent backoff, rate limiting covers all entry points, Docker discovery self-heals, and the core state machine is verified by property-based tests with strict typing
**Depends on**: Phase 9 (snapshots build on persistence patterns; testing and typing exercise hardened code to catch regressions)
**Requirements**: PERS-04, PERS-05, SECR-02, RESL-01, RESL-02, RESL-03, TEST-01, QUAL-01
**Success Criteria** (what must be TRUE):

  1. IEventStore supports snapshots and aggregate replay loads from latest snapshot plus subsequent events, bounding startup time regardless of total event history
  2. Health checks use exponential backoff with jitter for degraded providers (preventing thundering herd) and BackgroundWorker schedules checks based on provider state -- normal for READY, backoff for DEGRADED, longer ceiling for DEAD, skip for COLD
  3. Rate limiting is enforced at the command bus middleware layer, covering stdio, HTTP, and programmatic callers uniformly regardless of transport
  4. Docker discovery source reconnects automatically with retry and exponential backoff when the Docker daemon connection is lost
  5. Property-based tests using Hypothesis RuleBasedStateMachine verify that all Provider state transition sequences maintain invariants, and the package includes py.typed with incrementally-enabled mypy strictness and all resulting type errors fixed

## Progress

**Execution Order:**
Phases execute in numeric order: 8 -> 9 -> 10

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Timing Attack Prevention | v0.9 | 2/2 | Complete | 2026-02-15 |
| 2. Rate Limiter Hardening | v0.9 | 2/2 | Complete | 2026-02-15 |
| 3. JWT Lifetime Enforcement | v0.9 | 1/1 | Complete | 2026-02-15 |
| 4. API Key Rotation | v0.9 | 2/2 | Complete | 2026-02-15 |
| 5. Documentation Content | v0.10 | 2/2 | Complete | 2026-02-28 |
| 6. Kubernetes Controllers | v0.10 | 3/3 | Complete | 2026-03-01 |
| 7. Helm Chart Maturity | v0.10 | 1/1 | Complete | 2026-03-01 |
| 8. Safety Foundation | v1.0 | 3/3 | Complete | 2026-03-08 |
| 9. State Survival | v1.0 | 2/3 | In Progress | - |
| 10. Operational Hardening | v1.0 | 0/TBD | Not started | - |

---
*Created: 2026-02-15*
*Last updated: 2026-03-08 -- v1.0 roadmap created*
