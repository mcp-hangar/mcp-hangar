"""What `init` changed in the client configs, so `init --undo` can put it back (#1771).

`init` used to leave a `<name>.backup.<timestamp>.json` beside every client file
it rewrote, a new one on every run. After two runs the newest backup was no
longer the file the user had before Hangar, and a project-level
`.mcp.backup.*.json` accumulated inside the user's repository.

Now the first run copies the pre-Hangar bytes of each client file into the
Hangar state directory and records the file in a manifest there, with the hash
of what `init` wrote. A later run finds the file in the manifest and keeps that
first backup. `restore()` puts every file back byte-for-byte and removes the
manifest, and refuses if a file changed since `init` wrote it, unless forced.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config_file import default_event_store_path

_MANIFEST_NAME = "init-manifest.json"
_BACKUP_DIR = "init-backups"
_VERSION = 1


def state_dir() -> Path:
    """The Hangar state directory: where the event store lives (`$XDG_STATE_HOME/mcp-hangar`)."""
    return default_event_store_path().parent


def _manifest_path() -> Path:
    return state_dir() / _MANIFEST_NAME


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load() -> dict[str, dict]:
    path = _manifest_path()
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    return dict(document.get("files") or {})


def _save(files: dict[str, dict]) -> None:
    path = _manifest_path()
    if not files:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": _VERSION, "files": files}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def remember_original(path: Path) -> Path | None:
    """Record *path* in the manifest before `init` writes it; keep only the first backup.

    Returns the backup path when this call made one, and None when the file was
    already in the manifest (a re-run keeps the pristine original) or did not
    exist (nothing to back up; undo then removes the file `init` created).
    """
    files = _load()
    key = str(path.absolute())
    if key in files:
        return None

    backup: Path | None = None
    entry: dict[str, object] = {"existed": path.is_file(), "backup": None, "written_sha256": None}
    if path.is_file():
        data = path.read_bytes()
        backup = state_dir() / _BACKUP_DIR / f"{_sha256(key.encode())[:12]}-{path.name}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(data)
        entry["backup"] = str(backup)
        entry["original_sha256"] = _sha256(data)
    files[key] = entry
    _save(files)
    return backup


def record_written(path: Path, before: bytes | None) -> None:
    """Record the hash of what `init` just wrote to *path*, so a later edit can be seen.

    *before* is what the file held just before this write (None: it did not
    exist). The recorded hash moves forward only while the file still was what
    `init` last wrote, or `init` has not written it yet: a file the user edited
    in between keeps the old hash, so undo still refuses to overwrite the edits.
    """
    files = _load()
    key = str(path.absolute())
    entry = files.get(key)
    if entry is None:
        return
    previous = entry.get("written_sha256")
    if previous is None or (before is not None and _sha256(before) == previous):
        entry["written_sha256"] = _sha256(path.read_bytes())
        files[key] = entry
        _save(files)


@dataclass(frozen=True)
class Changed:
    """A client file that is no longer what `init` wrote."""

    path: Path
    summary: str


@dataclass(frozen=True)
class RestoreResult:
    restored: list[Path]
    removed: list[Path]
    refused: list[Changed]


def restore(*, force: bool = False) -> RestoreResult | None:
    """Put every file in the manifest back as it was before `init`. None when there is no manifest.

    Nothing is touched when any file changed since `init` wrote it and *force* is
    not given: the result lists them, with a summary of the difference.
    """
    files = _load()
    if not files:
        return None

    refused: list[Changed] = []
    for key, entry in files.items():
        path = Path(key)
        if not path.is_file() or force:
            continue
        current = path.read_bytes()
        if entry.get("written_sha256") != _sha256(current):
            refused.append(Changed(path, _difference(entry, current)))
    if refused:
        return RestoreResult([], [], refused)

    restored: list[Path] = []
    removed: list[Path] = []
    for key, entry in files.items():
        path = Path(key)
        backup = entry.get("backup")
        if entry.get("existed") and backup:
            original = Path(str(backup)).read_bytes()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original)
            restored.append(path)
            Path(str(backup)).unlink(missing_ok=True)
        elif path.is_file():
            path.unlink()
            removed.append(path)
    _save({})
    return RestoreResult(restored, removed, [])


def _difference(entry: dict, current: bytes) -> str:
    """A short account of how the file now differs from what the backup would restore."""
    backup = entry.get("backup")
    before = Path(str(backup)).read_text(encoding="utf-8", errors="replace").splitlines() if backup else []
    after = current.decode("utf-8", errors="replace").splitlines()
    delta = list(difflib.unified_diff(before, after, lineterm="", n=0))
    added = sum(1 for line in delta if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in delta if line.startswith("-") and not line.startswith("---"))
    return f"changed since init wrote it; against the original: +{added} -{removed} lines"
