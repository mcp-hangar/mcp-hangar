# Phase 5: Documentation Content - Context

**Gathered:** 2026-02-28
**Status:** Ready for planning

<domain>

## Phase Boundary

Create 4 missing documentation pages (Configuration Reference, MCP Tools Reference, Provider Groups Guide, Facade API Guide) and integrate them into the existing MkDocs site navigation. All pages follow existing documentation conventions (direct technical tone, tables + code blocks, single-page format).

</domain>

<decisions>

## Implementation Decisions

### Tools Reference organization

- Group 22 tools by category (6 groups): Lifecycle, Health, Discovery, Groups, Batch, Continuation
- Categories match source file organization in server/tools/
- Single page with quick-reference summary table at the top listing all 22 tools with one-line descriptions and anchor links
- Each tool documented as a structured card: description, parameters table (name/type/default/description), returns table, side effects list, one JSON request/response example

### Configuration Reference structure

- Single page covering all 13 YAML sections + environment variables
- Each config key documented in a table with columns: key, type, default, validation range, description, notes (for gotchas)
- One YAML snippet per config section showing common options with inline comments
- Environment variables in a dedicated section at the end with its own table (variable, default, description)
- Follows existing reference page style (hot-reload.md, cli.md)

### Provider Groups Guide structure

- Concept overview + reference sections (not step-by-step tutorial)
- Overview section explaining what groups are and when to use them
- Reference sections for: load balancing strategies, health policies, circuit breaker, tool access filtering
- One YAML config example per load balancing strategy (5 strategies: round_robin, weighted_round_robin, least_connections, random, priority)
- Follows existing guide structure (BATCH_INVOCATIONS.md pattern: overview, API ref, examples, limits)

### Facade API Guide structure

- Quick start (minimal working example) + API reference for each method + advanced patterns
- Both async (Hangar) and sync (SyncHangar) versions shown side by side in code examples
- Framework integration section covers FastAPI only (lifespan events, dependency injection)
- HangarConfig builder documented with fluent API examples

### General documentation conventions

- All 4 pages are single-page documents (consistent with existing docs)
- Direct, technical tone matching existing pages
- Heavy use of markdown tables for parameters and options
- Code blocks: YAML for config, Python for API usage, JSON for tool responses
- Reference pages integrate under mkdocs.yml "Reference" nav section
- Guide pages integrate under mkdocs.yml "Guides" nav section

### Claude's Discretion

- Exact ordering of sections within each page
- Admonition usage (info, warning boxes) placement
- Cross-reference links between the 4 new pages
- Whether to include a "Troubleshooting" or "Common Mistakes" subsection per page

</decisions>

<code_context>

## Existing Code Insights

### Reusable Assets

- `config.yaml.example`: Annotated example config with common options -- can extract YAML snippets from this
- `config.max.yaml`: Maximum config with all features enabled -- reference for exhaustive option listing
- Tool docstrings in `server/tools/*.py`: Each tool has comprehensive structured docstrings with CHOOSE THIS/SKIP THIS guidance, args, returns, examples -- can extract documentation content directly
- `facade.py` (L78-767): Complete Hangar/SyncHangar implementation with full method signatures and docstrings

### Established Patterns

- **Reference page style** (cli.md, hot-reload.md): 300-530 lines, parameter tables, code blocks, minimal prose
- **Guide page style** (BATCH_INVOCATIONS.md, HTTP_TRANSPORT.md): 250-340 lines, overview + API ref + examples + limits
- **Config loading** (server/config.py): All YAML keys parsed in `_load_provider_config()` (L209) and `_load_group_config()` (L331) with explicit defaults
- **Value object validation** (domain/value_objects/config.py): Validation ranges for IdleTTL (1-86400), HealthCheckInterval (5-3600), MaxConsecutiveFailures (1-100), etc.
- **Env var pattern**: All use MCP_ prefix, defined across bootstrap/runtime.py, cli/commands/serve.py, observability.py

### Integration Points

- `mkdocs.yml` nav: New reference pages under "Reference" section alongside cli.md and hot-reload.md
- `mkdocs.yml` nav: New guide pages under "Guides" section alongside existing 8 guides
- Cross-references: Groups guide can link to config reference for YAML syntax; Facade guide can link to tools reference for available tools

</code_context>

<specifics>

## Specific Ideas

No specific requirements -- open to standard approaches within the documented conventions. Follow existing page patterns.

</specifics>

<deferred>

## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 05-documentation-content*
*Context gathered: 2026-02-28*
