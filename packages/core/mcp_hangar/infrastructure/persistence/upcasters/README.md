# Event upcasters

This directory is for concrete upcasters used to evolve persisted event payload schemas.

The full documentation lives in the MkDocs site:

- `docs/architecture/EVENT_SOURCING.md`

Quick rules:

- Upcasting happens on read (deserialization).
- Each upcaster is a pure function and advances exactly one version (`vN -> vN+1`).
- Bumping `EVENT_VERSION_MAP` requires registering a complete upcaster chain.
