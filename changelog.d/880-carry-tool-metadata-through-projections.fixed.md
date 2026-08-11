**core:** a tool definition lost everything except `name`, `description` and
`inputSchema` on its way through the gateway. `title`, `annotations`,
`execution`, `icons` and the upstream's `_meta` were discarded at discovery, so
no surface downstream could serve them.

`annotations.readOnlyHint` and `destructiveHint` are how a client or an agent
harness decides whether a call needs a human in front of it. Behind Hangar
every tool looked alike, so that decision degraded to pattern-matching on tool
names -- the failure mode a policy enforcement plane exists to remove. `title`
is what a UI shows, and `execution.taskSupport` is how a client knows a tool
must be invoked as a task.

All five now travel from `tools/list` through to `hangar_tools`, the
`front_door` flat projection and the REST tool views alike. The flat projection
additionally regains `outputSchema`, which it dropped even though the other
surfaces kept it -- a client behind the front door had nothing to validate
structured output against.

Tool digests are deliberately unchanged: the pinned surface is still
`{description, inputSchema, outputSchema}`, so no existing pin is invalidated
by this release. Whether `annotations` belongs inside the pinned surface is a
separate question, filed rather than decided here.
