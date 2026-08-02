"""Approval records must not carry secrets, and must still detect substitution.

Two invariants that pull in opposite directions and are therefore tested
together:

* **Confidentiality.** The persisted approval record and the REST DTO show tool
  arguments to every ``approval:read`` holder. A secret in an argument *value*
  must not survive into them. Key-name redaction alone missed this entirely.
* **Integrity.** ``arguments_hash`` answers "is the payload about to be
  dispatched the payload the approver approved". It must therefore be computed
  over the RAW arguments -- hashing the redacted copy makes two different
  secrets hash identically, so swapping one for the other between approval and
  dispatch becomes undetectable.

The last test in ``TestIntegrityHashIsOverRawArguments`` is the one that pins
the interaction: it fails if anyone "simplifies" the hash back onto the
sanitized projection.
"""

from mcp_hangar.approvals.service import _hash_arguments, _sanitize_arguments


GITHUB_PAT_A = "ghp_" + "A" * 36
GITHUB_PAT_B = "ghp_" + "B" * 36
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"


class TestValueLevelRedaction:
    def test_secret_under_an_innocuous_key_is_redacted(self):
        """The whole point: key-name matching never saw this one."""
        out = _sanitize_arguments({"body": f"Authorization: Bearer {JWT}"})
        assert JWT not in out["body"]

    def test_github_token_in_a_free_text_field(self):
        out = _sanitize_arguments({"message": f"deploy with {GITHUB_PAT_A} please"})
        assert GITHUB_PAT_A not in out["message"]

    def test_nested_dict_is_walked(self):
        out = _sanitize_arguments({"payload": {"inner": {"note": GITHUB_PAT_A}}})
        assert GITHUB_PAT_A not in str(out)

    def test_list_values_are_walked(self):
        out = _sanitize_arguments({"items": ["safe", GITHUB_PAT_A]})
        assert GITHUB_PAT_A not in str(out)

    def test_key_name_redaction_still_applies(self):
        out = _sanitize_arguments({"api_key": "whatever-this-is", "password": "hunter2"})
        assert out["api_key"] == "[REDACTED]"
        assert out["password"] == "[REDACTED]"

    def test_ordinary_values_survive_untouched(self):
        """Redaction must not mangle the arguments an approver needs to read."""
        args = {"path": "/tmp/report.csv", "limit": 50, "dry_run": True, "tags": ["a", "b"]}
        assert _sanitize_arguments(args) == args

    def test_non_string_scalars_are_preserved(self):
        out = _sanitize_arguments({"count": 3, "ratio": 1.5, "flag": False, "nothing": None})
        assert out == {"count": 3, "ratio": 1.5, "flag": False, "nothing": None}


class TestIntegrityHashIsOverRawArguments:
    def test_changed_arguments_change_the_hash(self):
        assert _hash_arguments({"path": "/a"}) != _hash_arguments({"path": "/b"})

    def test_identical_arguments_hash_identically(self):
        assert _hash_arguments({"a": 1, "b": 2}) == _hash_arguments({"b": 2, "a": 1})

    def test_two_different_secrets_do_not_collide(self):
        """The regression this guards.

        Both tokens redact to the same marker. If the hash were taken over the
        sanitized copy, these would be indistinguishable and a token swap
        between approval and dispatch would pass revalidation silently.
        """
        assert _sanitize_arguments({"note": GITHUB_PAT_A}) == _sanitize_arguments({"note": GITHUB_PAT_B})
        assert _hash_arguments({"note": GITHUB_PAT_A}) != _hash_arguments({"note": GITHUB_PAT_B})
