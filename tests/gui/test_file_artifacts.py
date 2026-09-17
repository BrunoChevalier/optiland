"""Detached artifact encoding retains exact values and bounds every output."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import FrozenInstanceError
from io import StringIO
from threading import Event

import pytest
from PIL import Image

from optiland_gui.services import file_artifacts, file_tasks
from optiland_gui.services.file_artifacts import (
    BytesArtifact,
    CsvArtifact,
    RgbaArtifact,
    validate_artifact,
)
from optiland_gui.services.job_records import CalculationCancelled


def parameters(tmp_path, artifact):
    return {
        "path": str(tmp_path / "destination.dat"),
        "staged_path": str(tmp_path / ".optiland-artifact.tmp"),
        "artifact": artifact,
    }


def prepare(tmp_path, artifact, *, cancelled=None):
    params = parameters(tmp_path, artifact)
    result = file_tasks.prepare_artifact_output(
        None, params, lambda *_: None, cancelled or Event()
    )
    return params, result


def test_csv_round_trip_preserves_qualified_rows_missing_values_and_provenance(
    tmp_path,
):
    artifact = CsvArtifact(
        ("source", "reference", "n_d", "reason"),
        (
            ("user:glass", 'Étalon, "référence"\n2', 1.5168000345005883, ""),
            ("rii", "missing", None, "Outside declared n support"),
        ),
        (("view", "n_d versus V_d"), ("conditions", '{"temperature":20,"units":"µm"}')),
    )
    params, result = prepare(tmp_path, artifact)
    data = (tmp_path / ".optiland-artifact.tmp").read_bytes()
    rows = list(csv.DictReader(StringIO(data.decode("utf-8"))))
    assert rows[0]["reference"] == artifact.rows[0][1]
    assert float(rows[0]["n_d"]) == artifact.rows[0][2]
    assert rows[1]["n_d"] == "" and rows[1]["reason"] == artifact.rows[1][3]
    assert rows[0]["provenance:conditions"] == artifact.provenance[1][1]
    assert rows[1]["provenance:view"] == artifact.provenance[0][1]
    assert list(rows[0]) == [
        *artifact.columns,
        "provenance:view",
        "provenance:conditions",
    ]
    assert result["digest"] == hashlib.sha256(data).hexdigest()
    assert not (tmp_path / "destination.dat").exists()
    file_tasks.publish_output(None, {**params, **result}, None, Event())
    assert (tmp_path / "destination.dat").read_bytes() == data


def test_rgba_png_round_trip_preserves_padding_alpha_and_unicode_provenance(tmp_path):
    pixels = bytes((255, 0, 0, 32, 0, 128, 255, 255))
    artifact = RgbaArtifact(
        1,
        2,
        8,
        pixels[:4] + b"pad!" + pixels[4:] + b"pad!",
        (("optiland.provenance", '{"catalog":"Étalon","unit":"µm"}'),),
    )
    prepare(tmp_path, artifact)
    with Image.open(tmp_path / ".optiland-artifact.tmp") as image:
        assert image.mode == "RGBA" and image.size == (1, 2)
        assert image.tobytes() == pixels
        assert image.info["optiland.provenance"] == artifact.metadata[0][1]


def test_binary_artifact_is_frozen_and_preserves_exact_bytes(tmp_path):
    artifact = BytesArtifact(b'\xef\xbb\xbf{"view":"F-d"}\n')
    with pytest.raises(FrozenInstanceError):
        artifact.data = b"changed"
    prepare(tmp_path, artifact)
    assert (tmp_path / ".optiland-artifact.tmp").read_bytes() == artifact.data


@pytest.mark.parametrize(
    "factory",
    [
        lambda: BytesArtifact(bytearray(b"mutable")),
        lambda: CsvArtifact(["mutable"], ((1,),)),
        lambda: CsvArtifact(("x",), []),
        lambda: CsvArtifact(("x",), ()),
        lambda: CsvArtifact(("x",), ([1],)),
        lambda: CsvArtifact(("x",), ((1, 2),)),
        lambda: CsvArtifact(("x", "x"), ((1, 2),)),
        lambda: CsvArtifact(("",), ((1,),)),
        lambda: CsvArtifact(("x",), ((float("nan"),),)),
        lambda: CsvArtifact(("x",), ((float("inf"),),)),
        lambda: CsvArtifact(("x",), ((object(),),)),
        lambda: CsvArtifact(("x",), ((2**64,),)),
        lambda: CsvArtifact(("x",), (("nul\x00",),)),
        lambda: CsvArtifact(("x",), (("z" * 65537,),)),
        lambda: CsvArtifact(("x",), (("é" * 32769,),)),
        lambda: CsvArtifact(("provenance:view",), ((1,),), (("view", "a"),)),
        lambda: CsvArtifact(("x",), ((1,),), (("view", "a"), ("view", "b"))),
        lambda: CsvArtifact(("x",), ((1,),), (("", "a"),)),
        lambda: CsvArtifact(("x",), ((1,),), [("view", "a")]),
        lambda: CsvArtifact(("x",), ((1,),), (["view", "a"],)),
        lambda: RgbaArtifact(True, 1, 4, bytes(4)),
        lambda: RgbaArtifact(0, 1, 4, bytes(4)),
        lambda: RgbaArtifact(1, 1, 3, bytes(3)),
        lambda: RgbaArtifact(1, 1, 4, bytes(3)),
        lambda: RgbaArtifact(1, 1, 4, bytearray(4)),
        lambda: RgbaArtifact(8193, 1, 32772, b""),
        lambda: RgbaArtifact(8192, 8192, 32768, b""),
        lambda: RgbaArtifact(1, 1, 4, bytes(4), (("🔎", "value"),)),
        lambda: RgbaArtifact(1, 1, 4, bytes(4), (("x" * 80, "value"),)),
        lambda: RgbaArtifact(1, 1, 4, bytes(4), (("bad\tkeyword", "value"),)),
        lambda: RgbaArtifact(1, 1, 4, bytes(4), ((" bad keyword", "value"),)),
        lambda: RgbaArtifact(1, 1, 4, bytes(4), (("bad  keyword", "value"),)),
    ],
)
def test_malformed_or_mutable_artifacts_are_rejected_before_admission(factory):
    with pytest.raises(ValueError):
        factory()


@pytest.mark.parametrize(
    "limit,value,factory",
    [
        ("MAX_ARTIFACT_BYTES", 5, lambda: BytesArtifact(b"123456")),
        (
            "MAX_ARTIFACT_BYTES",
            4,
            lambda: RgbaArtifact(1, 1, 4, bytes(4), (("x", "y"),)),
        ),
        ("MAX_ARTIFACT_BYTES", 4, lambda: CsvArtifact(("x",), (("12345",),))),
        ("MAX_CSV_ROWS", 1, lambda: CsvArtifact(("x",), ((1,), (2,)))),
        ("MAX_CSV_CELLS", 1, lambda: CsvArtifact(("x", "y"), ((1, 2),))),
        ("MAX_CSV_COLUMNS", 1, lambda: CsvArtifact(("x", "y"), ((1, 2),))),
        (
            "MAX_METADATA_BYTES",
            3,
            lambda: CsvArtifact(("x",), ((1,),), (("ab", "cd"),)),
        ),
    ],
)
def test_admission_limits_are_enforced_without_large_allocations(
    monkeypatch, limit, value, factory
):
    monkeypatch.setattr(file_artifacts, limit, value)
    with pytest.raises(ValueError):
        factory()


def test_worker_revalidates_transport_without_trusting_constructor(tmp_path):
    artifact = BytesArtifact(b"captured")
    object.__setattr__(artifact, "data", bytearray(b"mutable"))
    with pytest.raises(ValueError):
        prepare(tmp_path, artifact)
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="Unsupported"):
        validate_artifact(object())


@pytest.mark.parametrize(
    "artifact",
    [
        BytesArtifact(b"123456"),
        CsvArtifact(("x",), (('"' * 100,),)),
        RgbaArtifact(1, 1, 4, bytes(4)),
    ],
)
def test_encoded_growth_limit_cleans_stage_and_preserves_destination(
    tmp_path, monkeypatch, artifact
):
    destination = tmp_path / "destination.dat"
    destination.write_bytes(b"preserve")
    monkeypatch.setattr(file_tasks, "MAX_ARTIFACT_BYTES", 5)
    with pytest.raises(ValueError, match="Encoded export"):
        prepare(tmp_path, artifact)
    assert destination.read_bytes() == b"preserve"
    assert not (tmp_path / ".optiland-artifact.tmp").exists()


def test_stage_collision_does_not_remove_preexisting_file(tmp_path):
    stage = tmp_path / ".optiland-artifact.tmp"
    stage.write_bytes(b"previous stage")
    with pytest.raises(FileExistsError):
        prepare(tmp_path, BytesArtifact(b"new"))
    assert stage.read_bytes() == b"previous stage"


def test_cancel_during_csv_output_cleans_only_owned_stage(tmp_path, monkeypatch):
    event = Event()
    original = file_tasks._ArtifactWriter.write
    writes = []

    def cancel_after_header(writer, data):
        result = original(writer, data)
        writes.append(data)
        event.set()
        return result

    monkeypatch.setattr(file_tasks._ArtifactWriter, "write", cancel_after_header)
    with pytest.raises(CalculationCancelled):
        prepare(tmp_path, CsvArtifact(("x",), ((1,), (2,))), cancelled=event)
    assert len(writes) == 1
    assert not list(tmp_path.iterdir())


def test_pre_cancelled_artifact_does_not_create_stage(tmp_path):
    event = Event()
    event.set()
    with pytest.raises(CalculationCancelled):
        prepare(tmp_path, BytesArtifact(b"captured"), cancelled=event)
    assert not list(tmp_path.iterdir())


def test_csv_boundaries_and_multibatch_validation_cancellation(tmp_path):
    values = (-(2**63), 2**63 - 1, True, False, None)
    artifact = CsvArtifact(("value",), tuple((value,) for value in values))
    prepare(tmp_path, artifact)
    rows = list(csv.reader(StringIO((tmp_path / ".optiland-artifact.tmp").read_text())))
    assert [row[0] for row in rows[1:]] == [
        "-9223372036854775808",
        "9223372036854775807",
        "True",
        "False",
        "",
    ]
    checks = []

    def stop_validation():
        checks.append(True)
        if len(checks) == 2:
            raise CalculationCancelled()

    many_rows = CsvArtifact(("value",), ((1,),) * 300)
    with pytest.raises(CalculationCancelled):
        validate_artifact(many_rows, cancel_check=stop_validation)
    assert len(checks) == 2


def test_large_bytes_cancel_between_owned_chunks(tmp_path, monkeypatch):
    event, writes = Event(), []
    original = file_tasks._ArtifactWriter.write

    def cancel_after_chunk(writer, data):
        writes.append(len(data))
        result = original(writer, data)
        event.set()
        return result

    monkeypatch.setattr(file_tasks._ArtifactWriter, "write", cancel_after_chunk)
    with pytest.raises(CalculationCancelled):
        prepare(tmp_path, BytesArtifact(b"a" * (2 * 1024 * 1024)), cancelled=event)
    assert writes == [1024 * 1024, 1024 * 1024]
    assert not list(tmp_path.iterdir())
