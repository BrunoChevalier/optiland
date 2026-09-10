"""Bounded directory traversal and resource cleanup with synthetic filesystems."""

from __future__ import annotations

import json
import os
import runpy
import stat
import sys
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from optiland_gui.services import catalog_discovery
from optiland_gui.services.catalog_discovery import SearchLimits, discover_catalogs


def collect(root, **kwargs):
    messages = list(discover_catalogs(root, **kwargs))
    return [p for message in messages for p in message.get("paths", [])], messages[-1]


def test_recursive_case_insensitive_discovery_and_duplicate_basenames(tmp_path):
    for name in (
        "a.glc",
        "b.GLC",
        "nested/a.GlC",
        "nested/deeper/c.glc",
        "ignored.cdb",
    ):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.touch()
    paths, outcome = collect(tmp_path, limits=SearchLimits(batch=2))
    assert {str(Path(p).relative_to(tmp_path)) for p in paths} == {
        str(Path(p)) for p in ("a.glc", "b.GLC", "nested/a.GlC", "nested/deeper/c.glc")
    }
    assert outcome["complete"]
    assert outcome["found"] == 4


@pytest.mark.parametrize(
    "limits,reason",
    [
        (SearchLimits(entries=2), "Entry limit"),
        (SearchLimits(catalogs=2), "Catalog limit"),
        (SearchLimits(depth=0), "Depth limit"),
    ],
)
def test_resource_limits_are_explicit(tmp_path, limits, reason):
    for i in range(4):
        (tmp_path / f"{i}.glc").touch()
    (tmp_path / "nested").mkdir()
    paths, outcome = collect(tmp_path, limits=limits)
    assert not outcome["complete"] and reason in outcome["reason"]
    assert len(paths) <= min(4, limits.entries, limits.catalogs)


def test_timeout_and_cancellation_close_enumeration_handles(tmp_path):
    for i in range(6):
        (tmp_path / f"{i}.glc").touch()
    original = os.scandir
    handles = []

    def tracked(path):
        iterator = original(path)

        class Handle:
            closed = False

            def __next__(self):
                return next(iterator)

            def close(self):
                self.closed = True
                iterator.close()

        result = Handle()
        handles.append(result)
        return result

    ticks = iter([0, 0, 0, 11])
    with patch("optiland_gui.services.catalog_discovery.os.scandir", tracked):
        _, outcome = collect(tmp_path, clock=lambda: next(ticks, 11))
    assert outcome["reason"] == "Time limit reached"
    assert handles and all(h.closed for h in handles)
    _, cancelled = collect(tmp_path, cancelled=lambda: True)
    assert cancelled["reason"] == "Search cancelled"


def test_inaccessible_and_disappearing_directories_are_summarized(tmp_path):
    (tmp_path / "good.glc").touch()
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    original = os.scandir

    def failing(path):
        if Path(path) == blocked:
            raise PermissionError("synthetic")
        return original(path)

    with patch("optiland_gui.services.catalog_discovery.os.scandir", failing):
        paths, outcome = collect(tmp_path)
    assert paths == [str(tmp_path / "good.glc")]
    assert outcome["skipped"] == 1 and not outcome["complete"]
    _, outcome = collect(tmp_path / "disappeared")
    assert not outcome["complete"]


def test_directory_symlink_cycle_and_file_hardlink_are_not_duplicated(tmp_path):
    (tmp_path / "a.glc").touch()
    try:
        os.link(tmp_path / "a.glc", tmp_path / "b.glc")
    except OSError:
        pytest.skip("hardlinks unavailable")
    # Windows can restrict symlink creation; the reparse test below is portable.
    with suppress(OSError):
        (tmp_path / "cycle").symlink_to(tmp_path, target_is_directory=True)
    paths, outcome = collect(tmp_path)
    assert len(paths) == 1
    assert outcome["complete"]


def test_reparse_root_is_not_traversed(tmp_path):
    import stat
    from types import SimpleNamespace

    with patch.object(
        Path,
        "lstat",
        return_value=SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400),
    ):
        paths, outcome = collect(tmp_path)
    assert not paths and "reparse" in outcome["reason"]


def test_closing_generator_releases_handles(tmp_path):
    for i in range(3):
        (tmp_path / f"{i}.glc").touch()
    scan = discover_catalogs(tmp_path, limits=SearchLimits(batch=1))
    assert next(scan)["paths"]
    scan.close()
    # On Windows deleting an enumerated directory is a useful handle regression.
    for path in tmp_path.iterdir():
        path.unlink()
    tmp_path.rmdir()


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeout": 0},
        {"timeout": float("inf")},
        {"timeout": float("nan")},
        {"entries": 0},
        {"catalogs": True},
        {"batch": 1.5},
        {"depth": -1},
        {"depth": False},
    ],
)
def test_invalid_search_limits_are_rejected(overrides):
    with pytest.raises(ValueError):
        SearchLimits(**overrides)


def test_iterator_read_error_closes_handle_and_reports_partial_search(tmp_path):
    handle = MagicMock()
    handle.__next__.side_effect = OSError("Directory disconnected")
    with patch.object(catalog_discovery.os, "scandir", return_value=handle):
        paths, outcome = collect(tmp_path)
    assert paths == []
    assert not outcome["complete"] and outcome["skipped"] == 1
    handle.close.assert_called_once()


@pytest.mark.parametrize("reparse", [False, True])
def test_full_stat_rejects_directory_cycle_or_new_reparse_point(tmp_path, reparse):
    root_info = tmp_path.stat()
    entry = SimpleNamespace(
        name="cycle",
        path=str(tmp_path / "cycle"),
        stat=lambda **kwargs: SimpleNamespace(st_mode=stat.S_IFDIR, st_ino=0),
    )
    # Emulate Windows' incomplete DirEntry stat and identity from the full stat.
    full_info = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_ino=root_info.st_ino,
        st_dev=root_info.st_dev,
        st_file_attributes=0x400 if reparse else 0,
    )
    entries = iter([entry])
    handle = MagicMock()
    handle.__next__.side_effect = lambda: next(entries)
    with (
        patch.object(catalog_discovery.os, "scandir", return_value=handle) as scan,
        patch.object(catalog_discovery.os, "stat", return_value=full_info),
    ):
        paths, outcome = collect(tmp_path)
    assert paths == [] and outcome["complete"]
    scan.assert_called_once_with(tmp_path)
    handle.close.assert_called_once()


def test_helper_entry_point_emits_bounded_json_protocol(tmp_path, monkeypatch, capsys):
    (tmp_path / "sample.GLC").touch()
    monkeypatch.setattr(
        sys, "argv", [catalog_discovery.__file__, str(tmp_path), '{"batch": 1}']
    )
    runpy.run_path(catalog_discovery.__file__, run_name="__main__")
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert messages[0] == {"paths": [str(tmp_path / "sample.GLC")]}
    assert messages[1]["done"] and messages[1]["complete"]
    assert messages[1]["found"] == 1
