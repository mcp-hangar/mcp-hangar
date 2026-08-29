**core:** approval-argument redaction was root-only, so a secret one level down
was persisted and served verbatim. `_sanitize_arguments` redacts by key name and
by value shape; the key-name pass ran over the top-level mapping only, while the
walk that goes deeper applied the value redactor alone -- and a plain password
has no shape for it to recognise. `{"config": {"password": "..."}}`, and the
same inside a list of records, reached the SQLite approval record and the REST
DTO served to every `approval:read` holder. The key-name check now applies at
every level, a sensitive key hides its whole subtree, and past the depth cap the
subtree is replaced rather than passed through -- this projection is stored and
served, so what the walk cannot inspect is dropped. `arguments_hash` is still
computed over the raw arguments; the dispatch-time substitution check depends on
that and is unchanged
