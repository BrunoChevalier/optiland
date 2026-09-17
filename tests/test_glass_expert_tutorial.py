"""Replay the published typed-pool example in a fresh, GUI-free process."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def test_published_example_has_distinct_pool_keys_and_emits_finite_report(capsys):
    script = (
        Path(__file__).resolve().parents[1]
        / "docs/samples/glassmap/typed_glass_expert.py"
    )
    namespace = runpy.run_path(str(script))
    pool = namespace["make_pool"]()
    assert len({c.display_name for c in pool.candidates}) == 1
    assert len({c.key for c in pool.candidates}) == 2
    runpy.run_path(str(script), run_name="__main__")
    report = json.loads(capsys.readouterr().out)
    assert report["candidate"]["eligible"]
    assert report["candidate"]["merit"] < report["baseline"]["merit"]


def test_typed_tutorial_runs_from_an_empty_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = root / "docs/samples/glassmap/typed_glass_expert.py"
    environment = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    report = json.loads(completed.stdout)
    assert report["outcome"] == "completed"
    assert report["baseline"]["materials"][0]["key"] is None
    assert not report["baseline"]["eligible"]
    assert report["baseline"]["merit"] == pytest.approx(111.68088592045, rel=1e-9)
    candidate = report["candidate"]
    assert candidate["eligible"] and report["improved"]
    assert candidate["merit"] < 1e-8
    assert candidate["materials"][0]["key"] == {
        "source_id": "tutorial:owned",
        "catalog": "synthetic",
        "record_id": "crown-b",
    }
    assert candidate["continuous"][0]["variable_id"] == "front-radius"
    assert candidate["continuous"][0]["value"] == pytest.approx(119.088352, abs=2e-4)
    assert report["total_trials"] == 5
    assert not report["omitted_trials"]
    assert len(report["trials"]) == 5
    assert completed.stderr == ""
    assert list(tmp_path.iterdir()) == []
