"""The traffic archive merges by date, so a re-run never duplicates a day (#1797).

GitHub keeps 14 days of traffic. The weekly `traffic-archive` workflow fetches
them and folds them into the `traffic` branch with `scripts/archive_traffic.py`;
the windows of two runs overlap by a week, and a manual dispatch overlaps
completely. Everything here runs offline: the fetch takes an injected getter.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("archive_traffic", ROOT / "scripts" / "archive_traffic.py")
assert _spec is not None and _spec.loader is not None
traffic = importlib.util.module_from_spec(_spec)
sys.modules["archive_traffic"] = traffic
_spec.loader.exec_module(traffic)


def _day(day: int, count: int, uniques: int = 1) -> dict[str, Any]:
    return {"timestamp": f"2026-10-{day:02d}T00:00:00Z", "count": count, "uniques": uniques}


def _raw(tmp_path: Path, name: str, views: list[dict[str, Any]], referrers: list[dict[str, Any]]) -> Path:
    raw = tmp_path / name / "mcp-hangar"
    raw.mkdir(parents=True)
    total = sum(v["count"] for v in views)
    (raw / "views.json").write_text(json.dumps({"count": total, "uniques": 1, "views": views}))
    (raw / "clones.json").write_text(json.dumps({"count": total, "uniques": 1, "clones": views}))
    (raw / "referrers.json").write_text(json.dumps(referrers))
    (raw / "paths.json").write_text(json.dumps([{"path": "/mcp-hangar/mcp-hangar", "count": 3, "uniques": 2}]))
    return raw.parent


def _snapshot(archive: Path) -> dict[str, bytes]:
    return {str(p.relative_to(archive)): p.read_bytes() for p in sorted(archive.rglob("*")) if p.is_file()}


class TestDailySeries:
    def test_overlapping_windows_give_the_union_without_duplicates(self) -> None:
        first = [_day(d, d) for d in range(1, 15)]
        second = [_day(d, d) for d in range(8, 22)]

        merged = traffic.merge_daily(first, second)

        timestamps = [e["timestamp"] for e in merged]
        assert timestamps == sorted(set(timestamps))
        assert len(merged) == 21

    def test_the_newer_fetch_of_a_partial_day_wins(self) -> None:
        merged = traffic.merge_daily([_day(14, 2)], [_day(14, 9, uniques=4)])

        assert merged == [_day(14, 9, uniques=4)]

    def test_extra_fields_from_the_api_are_not_kept(self) -> None:
        merged = traffic.merge_daily([], [{**_day(1, 1), "unexpected": True}])

        assert merged == [_day(1, 1)]


class TestMerge:
    def test_an_identical_rerun_writes_byte_identical_files(self, tmp_path: Path) -> None:
        raw = _raw(tmp_path, "raw", [_day(d, d) for d in range(1, 15)], [{"referrer": "a", "count": 1, "uniques": 1}])
        archive = tmp_path / "archive"

        traffic.merge(raw, archive, "2026-10-15")
        before = _snapshot(archive)
        traffic.merge(raw, archive, "2026-10-15")

        assert _snapshot(archive) == before

    def test_a_second_week_extends_the_series_and_adds_a_dated_snapshot(self, tmp_path: Path) -> None:
        archive = tmp_path / "archive"
        week1 = _raw(tmp_path, "w1", [_day(d, 1) for d in range(1, 15)], [{"referrer": "a", "count": 1, "uniques": 1}])
        week2 = _raw(tmp_path, "w2", [_day(d, 2) for d in range(8, 22)], [{"referrer": "b", "count": 5, "uniques": 2}])

        traffic.merge(week1, archive, "2026-10-15")
        traffic.merge(week2, archive, "2026-10-22")

        views = json.loads((archive / "mcp-hangar" / "views.json").read_text())["views"]
        assert len(views) == 21
        assert [v["count"] for v in views[:7]] == [1] * 7
        assert [v["count"] for v in views[7:]] == [2] * 14
        clones = json.loads((archive / "mcp-hangar" / "clones.json").read_text())["clones"]
        assert len(clones) == 21
        assert sorted(p.name for p in (archive / "mcp-hangar" / "referrers").iterdir()) == [
            "2026-10-15.json",
            "2026-10-22.json",
        ]

    def test_a_same_day_rerun_replaces_that_days_snapshot(self, tmp_path: Path) -> None:
        archive = tmp_path / "archive"
        morning = _raw(tmp_path, "am", [_day(1, 1)], [{"referrer": "a", "count": 1, "uniques": 1}])
        evening = _raw(tmp_path, "pm", [_day(1, 3)], [{"referrer": "a", "count": 4, "uniques": 1}])

        traffic.merge(morning, archive, "2026-10-15")
        traffic.merge(evening, archive, "2026-10-15")

        referrers = archive / "mcp-hangar" / "referrers"
        assert [p.name for p in referrers.iterdir()] == ["2026-10-15.json"]
        assert json.loads((referrers / "2026-10-15.json").read_text())[0]["count"] == 4

    def test_a_malformed_date_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            traffic.merge(tmp_path, tmp_path / "archive", "../../etc")


class TestFetch:
    def test_one_repository_without_access_does_not_stop_the_others(self, tmp_path: Path) -> None:
        def get(url: str, token: str) -> Any:
            assert token == "t"
            if "/repos/mcp-hangar/docs/" in url:
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b""))  # type: ignore[arg-type]
            if url.endswith("?per=day"):
                key = "views" if "/traffic/views" in url else "clones"
                return {"count": 1, "uniques": 1, key: [_day(1, 1)]}
            return []

        failures = traffic.fetch("mcp-hangar", ["mcp-hangar", "docs"], "t", tmp_path, get=get)

        assert len(failures) == 4
        assert all(f.startswith("mcp-hangar/docs ") and "HTTP 403" in f for f in failures)
        assert sorted(p.name for p in (tmp_path / "mcp-hangar").iterdir()) == [
            "clones.json",
            "paths.json",
            "referrers.json",
            "views.json",
        ]
        assert not (tmp_path / "docs").exists()

    def test_a_name_that_is_not_a_repository_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            traffic.fetch("mcp-hangar", [".."], "t", tmp_path, get=lambda url, token: {})

    def test_the_token_is_never_printed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def get(url: str, token: str) -> Any:
            raise urllib.error.HTTPError(url, 401, "Bad credentials", {}, io.BytesIO(b""))  # type: ignore[arg-type]

        monkeypatch.setenv("TRAFFIC_TOKEN", "secret-value")
        monkeypatch.setattr(traffic, "_http_get", get)

        rc = traffic.main(["fetch", "--owner", "mcp-hangar", "--repos", "mcp-hangar", "--out", str(tmp_path)])

        out = capsys.readouterr()
        assert rc == 1
        assert "HTTP 401" in out.out
        assert "secret-value" not in out.out + out.err

    def test_an_empty_token_fails_rather_than_calling_the_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRAFFIC_TOKEN", raising=False)
        monkeypatch.setattr(traffic, "_http_get", lambda url, token: pytest.fail("called the API"))

        assert traffic.main(["fetch", "--owner", "mcp-hangar", "--repos", "mcp-hangar", "--out", str(tmp_path)]) == 1
