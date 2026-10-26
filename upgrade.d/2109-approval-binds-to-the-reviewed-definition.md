### Approve a discovered server with the definition_digest you reviewed

A source re-reports its definition every cycle, so the definition waiting for
approval under a name can change between listing it and approving it. Every
listing now shows a `definition_digest`: the full SHA-256 of the definition
(its connection, metadata, mode and source). Pass the one you reviewed, and the
approval applies only that definition:

- MCP: `hangar_approve(mcp_server="svc", definition_digest="<digest>")`, with the
  digest from `hangar_discovered` or `hangar_quarantine`.
- REST: `POST /api/discovery/approve/svc?definition_digest=<digest>`, or the same
  with the JSON body `{"definition_digest": "<digest>"}`, with the digest from
  `GET /api/discovery/pending` or `GET /api/discovery/quarantined`.

Use `definition_digest`, not `fingerprint`. The 16-character fingerprint stays
in the listings for change detection, but it is too short to bind an approval
to: a source could produce two definitions that share one. Passed as the
digest, it is refused.

When the definition waiting has another digest, the approval is refused and
nothing changes. REST answers 409 with the code
`DiscoveredDefinitionChangedError` and `details.current_definition_digest`. The
tool answers that error type, with the current digest in its message. Review the
definition again and approve its current digest.

This is the recommended form. An approval without a digest still works as
before, but logs `discovery_approval_not_bound` at WARNING; it may be required
in a later release. A REST request with no body, or with `{}`, is such an
unbound approval. Anything that looks like an attempt to bind but cannot be read
as exactly one digest answers 400 instead of being approved unbound:

- a body that is not a JSON object (malformed JSON, an array, a form body);
- a key other than `definition_digest`, including `fingerprint` and other
  spellings;
- a `definition_digest` that is `null` or not a 64-character lowercase hex
  string;
- a query and body that disagree.

On the tool, a value that is not such a digest is an invalid argument. Passing
`null` there is the same as leaving the argument out.

#### Manual approval (`auto_register: false`) now runs the checks a registration runs

With `auto_register: false`, a discovered definition waits in pending without
ever going through the input validator or the source's own policy. An approval
used to register it anyway. An approval now runs both checks, for pending and
quarantined definitions alike, and refuses a definition that fails them, or one
whose source is no longer registered. The refusal answers
`{"approved": false, "error": "approval refused: <reason>"}`, and the definition
stays where it waited, so a reject can remove it. Examples are a command the
input validator refuses, or a Kubernetes namespace the source may not take
from.

With automatic registration, a definition the source's policy refuses is
quarantined, and approving it used to register it. That approval is now refused
too. Approval still overrides the other reasons a definition is quarantined: a
failed health or schema check, and the rate and count limits.

#### Withdrawn definitions are no longer approvable

A pending entry is cleared once a scan no longer reports that definition, in
either of two cases:

- the source reports the running definition again, or reports another one under
  the name;
- a source that is healthy after the scan stops reporting the name.

A definition still reported next to another under the same name is kept. This
happens, for example, during a rolling update with old and new pods side by side.

A quarantined change is cleared when its source reports the running definition
again. Removing a source clears what it left in pending and quarantine.
Approving any of these afterwards answers "not found", as for any name that is
not waiting.
