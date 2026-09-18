"""Public portable replay, independent from GUI modules and external catalogs."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

import optiland.backend as be
from optiland.fileio.optiland_handler import OptilandEncoder
from optiland.materials import DataMaterial
from optiland.materials.properties import MaterialKey, SourceStamp, describe_material
from optiland.optic import Optic
from optiland.optimization import OptimizationProblem
from optiland.optimization import glass_expert_replay as replay
from optiland.optimization.material_candidates import prepare_material_candidate


@pytest.fixture
def replay_payload():
    optic = Optic()
    material = DataMaterial.from_coefficients(
        "formula 5", [1.5, 0.008, -2], name="owned", wavelength_range=(0.35, 1.1)
    )
    optic.surfaces.add(index=0, radius=be.inf, thickness=be.inf)
    optic.surfaces.add(index=1, radius=50.0, thickness=5.0, material=material)
    optic.surfaces.add(index=2, radius=-50.0, thickness=45.0, is_stop=True)
    optic.surfaces.add(index=3, radius=be.inf)
    optic.set_aperture("EPD", 10.0)
    optic.fields.set_type("angle")
    optic.fields.add(y=0.0)
    optic.wavelengths.add(0.55, is_primary=True)
    optic.updater.update()
    problem = OptimizationProblem()
    problem.add_operand("f2", 85.0, input_data={"optic": optic})
    merit = float(problem.sum_squared())
    candidates = []
    for index in range(2):
        data = DataMaterial.from_coefficients(
            "formula 5",
            [1.48 + index * 0.05, 0.008, -2],
            name="same name",
            wavelength_range=(0.35, 1.1),
        )
        candidate = prepare_material_candidate(
            describe_material(data, MaterialKey("source", "catalog", str(index))),
            SourceStamp("source", "generation"),
            wavelengths_um=(0.55,),
        )
        candidates.append(
            {"record": candidate.to_dict(), "fingerprint": candidate.fingerprint}
        )
    variables = [
        {
            "id": "radius:1",
            "definition": {
                "type": "radius",
                "surface_number": 1,
                "min_val": 20.0,
                "max_val": 150.0,
            },
        },
        {"id": "material:1", "definition": {"type": "material", "surface_number": 1}},
    ]
    operands = [
        {
            "id": "focal",
            "definition": {
                "type": "f2",
                "target": 85.0,
                "weight": 1.0,
                "input_data": {},
            },
        }
    ]
    native = json.dumps(
        optic.to_dict(), cls=OptilandEncoder, sort_keys=True, separators=(",", ":")
    )
    return {
        "baseline": {
            "native": native,
            "digest": hashlib.sha256(native.encode()).hexdigest(),
        },
        "backend": {"name": "numpy", "device": "cpu", "precision": 64},
        "variables": variables,
        "operands": operands,
        "resolved_variables": copy.deepcopy(variables),
        "resolved_operands": copy.deepcopy(operands),
        "targets": [
            {
                "variable_id": "material:1",
                "surface_number": 1,
                "keys": [c["record"]["key"] for c in candidates],
            }
        ],
        "candidates": candidates,
        "settings": {
            "global_samples": 2,
            "num_neighbours": 1,
            "maxiter": 3,
            "tol": 0.001,
            "seed": 1234,
            "nd_scale": 1.0,
            "vd_scale": 1.0,
            "report_limit": 1000,
        },
        "operand_metadata": {},
        "baseline_merit": merit,
        "baseline_operands": [],
        "result": None,
        "warnings": [],
        "replay_unavailable": None,
    }


def encoded(payload):
    return replay.decode_glass_expert_manifest(
        replay.encode_glass_expert_manifest(payload)
    )


def native_edit(payload, edit):
    native = json.loads(payload["baseline"]["native"])
    edit(native)
    text = json.dumps(
        native, cls=OptilandEncoder, sort_keys=True, separators=(",", ":")
    )
    payload["baseline"] = {
        "native": text,
        "digest": hashlib.sha256(text.encode()).hexdigest(),
    }


def test_core_replay_actual_candidate_and_owned_payload(
    replay_payload, set_test_backend
):
    replay_payload["backend"]["name"] = be.get_backend()
    inputs = encoded(replay_payload)
    result = replay.replay_glass_expert(inputs)
    assert result.report.baseline.merit == pytest.approx(
        replay_payload["baseline_merit"]
    )
    assert result.report.candidate_eligible
    assert result.report.candidate.materials[0].key.source_id == "source"
    mutated = inputs.payload
    mutated["variables"].clear()
    assert len(inputs.payload["variables"]) == 2


@pytest.mark.parametrize("data", [b"", b"[]", b"\xff", b'{"a":NaN}', b'{"x":1,"x":2}'])
def test_external_json_is_strict_and_bounded(data):
    with pytest.raises(ValueError):
        replay.decode_glass_expert_manifest(data)


@pytest.mark.parametrize(
    "edit",
    [
        lambda p: p["backend"].update(name="custom"),
        lambda p: p["backend"].update(precision=True),
        lambda p: p["settings"].update(seed=True),
        lambda p: p["settings"].update(tol=0),
        lambda p: p["variables"].clear(),
        lambda p: p["operands"][0].update(id="radius:1"),
        lambda p: p["resolved_variables"][0].update(id="unknown"),
        lambda p: p["candidates"][0].update(fingerprint="wrong"),
        lambda p: p["candidates"].append(p["candidates"][0]),
        lambda p: p["targets"][0].update(keys=[]),
        lambda p: p["targets"][0].update(surface_number=True),
        lambda p: p["targets"][0].update(variable_id="missing"),
        lambda p: p["targets"][0].update(
            keys=[{"source_id": "absent", "catalog": "x", "record_id": "y"}]
        ),
        lambda p: p["baseline"].update(digest="wrong"),
        lambda p: p.update(baseline_merit=-1),
        lambda p: p.update(warnings=["x" * 2049]),
        lambda p: p.update(operand_metadata=[]),
        lambda p: p.update(targets=[]),
        lambda p: p.update(candidates=[]),
        lambda p: p.update(baseline_operands={}),
        lambda p: p.update(replay_unavailable=123),
        lambda p: p["variables"][0].update(id=""),
        lambda p: p["variables"][0].update(definition=[]),
        lambda p: p["variables"][0].update(extra=True),
        lambda p: p["candidates"][1]["record"]["stamp"].update(generation="other"),
        lambda p: p["targets"][0].update(keys=[p["candidates"][0]["record"]["key"]]),
        lambda p: p.update(
            result={"report": {"trials": [{}] * 1001}, "candidate_operands": []}
        ),
    ],
)
def test_metadata_admission_rejects_conflicts(replay_payload, edit):
    edit(replay_payload)
    with pytest.raises(ValueError):
        encoded(replay_payload)


def test_envelope_version_digest_and_budget(replay_payload, monkeypatch):
    original = json.loads(replay.encode_glass_expert_manifest(replay_payload))
    for change in (
        {"version": 2},
        {"version": True},
        {"digest": "wrong"},
        {"format": "other"},
    ):
        with pytest.raises(ValueError):
            replay.decode_glass_expert_manifest(json.dumps(original | change).encode())
    monkeypatch.setattr(replay, "MAX_REPLAY_BYTES", 100)
    with pytest.raises(ValueError, match="limit"):
        replay.encode_glass_expert_manifest(replay_payload)
    with pytest.raises(ValueError, match="limit"):
        replay.decode_glass_expert_manifest(b" " * 101)


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["surface_group"]["surfaces"][1].update(
            material_post={"type": "MaterialFile", "filename": "never-open"}
        ),
        lambda d: d["surface_group"]["surfaces"][1].update(
            aperture={
                "type": "FileAperture",
                "filepath": "never-open",
                "delimiter": None,
                "skip_header": 0,
            }
        ),
        lambda d: d["surface_group"]["surfaces"][1].update(type="UnknownSurface"),
        lambda d: d["surface_group"]["surfaces"][1]["geometry"].update(
            radius=float("nan")
        ),
    ],
)
def test_native_external_dependencies_rejected_before_construction(
    replay_payload, edit, monkeypatch
):
    native_edit(replay_payload, edit)
    monkeypatch.setattr(
        Optic,
        "from_dict",
        classmethod(lambda *a: pytest.fail("unsafe native construction")),
    )
    with pytest.raises(ValueError):
        replay.restore_replay_baseline(encoded(replay_payload))


def test_plugin_solve_and_field_constructor_rejected(replay_payload, monkeypatch):
    from optiland.fields.field_types.base import BaseFieldDefinition
    from optiland.solves.base import BaseSolve

    class Plugin:
        pass

    monkeypatch.setitem(BaseSolve._registry, "Plugin", Plugin)
    native_edit(
        replay_payload,
        lambda data: data.update(solves={"solves": [{"type": "Plugin"}]}),
    )
    with pytest.raises(ValueError, match="Custom native"):
        replay.restore_replay_baseline(encoded(replay_payload))
    native_edit(replay_payload, lambda data: data.update(solves={"solves": []}))
    monkeypatch.setitem(BaseFieldDefinition._registry, "plugin", Plugin)
    native_edit(
        replay_payload,
        lambda data: data["fields"].update(field_definition={"field_type": "Plugin"}),
    )
    with pytest.raises(ValueError, match="Custom field"):
        replay.restore_replay_baseline(encoded(replay_payload))


@pytest.mark.parametrize(
    "edit,message",
    [
        (
            lambda p: p["resolved_operands"][0]["definition"].update(type="efl"),
            "builtin|metric",
        ),
        (
            lambda p: p["resolved_operands"][0]["definition"].update(target=10),
            "target differs",
        ),
        (
            lambda p: p["resolved_operands"][0]["definition"].update(weight=2),
            "inputs or weights",
        ),
        (
            lambda p: p["resolved_variables"][0]["definition"].update(max_val=100),
            "original definition",
        ),
        (
            lambda p: p["resolved_variables"][0]["definition"].update(type="index"),
            "overlaps",
        ),
        (
            lambda p: p["resolved_variables"][0]["definition"].update(coeff_number=1),
            "arguments",
        ),
        (lambda p: p.update(baseline_merit=123456.0), "frozen objective"),
    ],
)
def test_resolved_definitions_cannot_change_recorded_problem(
    replay_payload, edit, message
):
    edit(replay_payload)
    with pytest.raises(ValueError, match=message):
        replay.replay_glass_expert(encoded(replay_payload))


@pytest.mark.parametrize("category", ["Paraxial", None])
def test_operand_presentation_category_preserves_frozen_optics(
    replay_payload, category
):
    if category is not None:
        for rows in ("operands", "resolved_operands"):
            replay_payload[rows][0]["definition"]["category"] = category
    result = replay.replay_glass_expert(encoded(replay_payload))
    assert result.report.baseline.merit == pytest.approx(
        replay_payload["baseline_merit"]
    )
    assert result.report.candidate_eligible


@pytest.mark.parametrize("category", [None, 1, False, "", " ", "x" * 201, {}])
def test_operand_category_rejects_invalid_presentation_values(replay_payload, category):
    for rows in ("operands", "resolved_operands"):
        replay_payload[rows][0]["definition"]["category"] = category
    with pytest.raises(ValueError, match="category must be"):
        replay.replay_glass_expert(encoded(replay_payload))


@pytest.mark.parametrize(
    "change", ["different", "original_only", "resolved_only", "unknown_original"]
)
def test_operand_annotations_cannot_change_or_hide_unsupported_fields(
    replay_payload, change
):
    raw = replay_payload["operands"][0]["definition"]
    resolved = replay_payload["resolved_operands"][0]["definition"]
    raw["category"] = resolved["category"] = "Paraxial"
    if change == "different":
        resolved["category"] = "Ray"
    elif change == "original_only":
        resolved.pop("category")
    elif change == "resolved_only":
        raw.pop("category")
    else:
        raw["unknown_annotation"] = "not supported"
    with pytest.raises(ValueError, match="category differs|explicit resolved"):
        replay.replay_glass_expert(encoded(replay_payload))


def test_missing_native_and_cooperative_stop(replay_payload):
    replay_payload["baseline"] = None
    replay_payload["replay_unavailable"] = "Unserializable component"
    with pytest.raises(ValueError, match="Unserializable"):
        replay.restore_replay_baseline(encoded(replay_payload))

    def stopped():
        raise RuntimeError("stopped")

    with pytest.raises(RuntimeError, match="stopped"):
        replay._check(stopped)


def test_native_coating_media_and_layers_are_owned(replay_payload, monkeypatch):
    from optiland.materials.material_file import MaterialFile
    from optiland.materials.portability import capture_material

    def add(data):
        surfaces = data["surface_group"]["surfaces"]
        pre, post = surfaces[0]["material_post"], surfaces[1]["material_post"]
        surfaces[1]["interaction_model"]["coating"] = {
            "type": "ThinFilmCoating",
            "material_pre": pre,
            "material_post": post,
            "layers": [
                {"material": post, "thickness_nm": 100.0, "name": "owned layer"}
            ],
        }
        surfaces[2]["interaction_model"]["coating"] = {
            "type": "FresnelCoating",
            "material_pre": post,
            "material_post": pre,
        }

    native_edit(replay_payload, add)
    monkeypatch.setattr(
        MaterialFile, "from_dict", classmethod(lambda *a: pytest.fail("catalog lookup"))
    )
    optic = replay.restore_replay_baseline(encoded(replay_payload))
    assert capture_material(
        optic.surfaces[1].interaction_model.coating.stack.layers[0].material
    ) == capture_material(optic.surfaces[1].material_post)
    assert optic.surfaces[2].interaction_model.coating.jones.material_pre is not None


@pytest.mark.parametrize("selector", ["primary", "[0.55]", [0.55]])
def test_original_primary_operand_and_index_variable_are_resolved(
    replay_payload, selector
):
    optic = replay.restore_replay_baseline(encoded(replay_payload))
    args = {
        "surface_number": 3,
        "Hx": 0.0,
        "Hy": 0.0,
        "Px": 0.0,
        "Py": 1.0,
        "wavelength": 0.55,
    }
    problem = OptimizationProblem()
    problem.add_operand("real_y_intercept", input_data={**args, "optic": optic})
    target = float(problem.operands[0].target)
    replay_payload["operands"] = [
        {
            "id": "ray",
            "definition": {
                "type": "real_y_intercept",
                "input_data_str": json.dumps(args | {"wavelength": selector}),
            },
        }
    ]
    replay_payload["resolved_operands"] = [
        {
            "id": "ray",
            "definition": {
                "type": "real_y_intercept",
                "target": target,
                "input_data": args,
            },
        }
    ]
    replay_payload["baseline_merit"] = 0.0
    replay_payload["variables"][0]["definition"] = {
        "type": "index",
        "surface_number": 2,
        "wavelength": selector,
        "min_val": 0.9,
        "max_val": 1.1,
    }
    replay_payload["resolved_variables"][0]["definition"] = replay_payload["variables"][
        0
    ]["definition"] | {"wavelength": 0.55}
    result = replay.replay_glass_expert(encoded(replay_payload))
    assert (
        result.report.outcome == "already_reached" and result.report.candidate is None
    )


@pytest.mark.parametrize(
    "edit,message",
    [
        (
            lambda p: p["resolved_variables"][0]["definition"].update(type="unknown"),
            "Unsupported",
        ),
        (
            lambda p: p["resolved_variables"][0]["definition"].update(min_val=200.0),
            "bounds",
        ),
        (
            lambda p: p["resolved_variables"][1]["definition"].update(surface_number=2),
            "target disagree",
        ),
        (
            lambda p: p["resolved_operands"][0]["definition"].update(
                input_data_str="{}"
            ),
            "resolved operand inputs",
        ),
        (
            lambda p: p["operands"][0]["definition"].update(type="real_y_intercept"),
            "original metric",
        ),
        (
            lambda p: p["operands"][0]["definition"].update(
                input_data=None, input_data_str="[]"
            ),
            "plain explicit data",
        ),
    ],
)
def test_runtime_definition_rejection(replay_payload, edit, message):
    edit(replay_payload)
    with pytest.raises(ValueError, match=message):
        replay.replay_glass_expert(encoded(replay_payload))


def test_duplicate_alias_and_missing_continuous_control(replay_payload):
    duplicate = {
        "id": "reciprocal",
        "definition": {"type": "reciprocal_radius", "surface_number": 1},
    }
    replay_payload["variables"].append(duplicate)
    replay_payload["resolved_variables"].append(copy.deepcopy(duplicate))
    with pytest.raises(ValueError, match="same optical parameter"):
        replay.replay_glass_expert(encoded(replay_payload))
    replay_payload["variables"] = [replay_payload["variables"][1]]
    replay_payload["resolved_variables"] = [replay_payload["resolved_variables"][1]]
    with pytest.raises(ValueError, match="continuous variable"):
        replay.replay_glass_expert(encoded(replay_payload))


def test_native_pickup_and_solve_conflicts_are_preserved(replay_payload):
    optic = replay.restore_replay_baseline(encoded(replay_payload))
    optic.pickups.add(2, "radius", 1)
    native_edit(
        replay_payload, lambda data: data.update(pickups=optic.pickups.to_dict())
    )
    with pytest.raises(ValueError, match="pickup controls"):
        replay.replay_glass_expert(encoded(replay_payload))
    from optiland.solves.curvature import ChiefRayAngleCurvatureSolve

    optic.pickups.pickups.clear()
    optic.solves.solves.append(ChiefRayAngleCurvatureSolve(optic, 1, 0.0))
    native_edit(
        replay_payload,
        lambda data: data.update(
            pickups=optic.pickups.to_dict(), solves=optic.solves.to_dict()
        ),
    )
    with pytest.raises(ValueError, match="solve controls"):
        replay.replay_glass_expert(encoded(replay_payload))


@pytest.mark.parametrize("change", ["no_surfaces", "legacy_interaction"])
def test_incomplete_native_prescription_is_rejected(replay_payload, change):
    def edit(data):
        if change == "no_surfaces":
            data["surface_group"]["surfaces"] = []
        else:
            data["surface_group"]["surfaces"][1].pop("interaction_model")

    native_edit(replay_payload, edit)
    with pytest.raises(ValueError, match="surface count|explicit surface"):
        replay.restore_replay_baseline(encoded(replay_payload))


def test_non_json_payload_and_empty_wavelength_rejected(replay_payload):
    replay_payload["warnings"] = [object()]
    with pytest.raises(ValueError, match="finite plain JSON"):
        replay.encode_glass_expert_manifest(replay_payload)
    replay_payload["warnings"] = []
    definition = {"type": "index", "surface_number": 2, "wavelength": []}
    replay_payload["variables"][0]["definition"] = definition
    replay_payload["resolved_variables"][0]["definition"] = definition | {
        "wavelength": 0.55
    }
    with pytest.raises(ValueError, match="one explicit wavelength"):
        replay.replay_glass_expert(encoded(replay_payload))


def test_bounded_replay_diagnostics_remain_explicit(replay_payload):
    replay_payload["warnings"] = [str(index) for index in range(1001)]
    result = replay.replay_glass_expert(encoded(replay_payload))
    assert len(result.report.warnings) == 1001
    assert "additional replay diagnostics omitted" in result.report.warnings[-1]


def test_toroidal_native_alias_and_constructor_padding(replay_payload):
    from optiland.geometries import EvenAsphere, ToroidalGeometry

    optic = replay.restore_replay_baseline(encoded(replay_payload))
    geometry = optic.surfaces[1].geometry
    toroidal = ToroidalGeometry(geometry.cs, 50.0, 50.0)
    native_edit(
        replay_payload,
        lambda data: data["surface_group"]["surfaces"][1].update(
            geometry=toroidal.to_dict()
        ),
    )
    for section in ("variables", "resolved_variables"):
        replay_payload[section][0]["definition"]["axis"] = "y"
        replay_payload[section].append(
            {
                "id": "reciprocal",
                "definition": {"type": "reciprocal_radius", "surface_number": 1},
            }
        )
    with pytest.raises(ValueError, match="same optical parameter"):
        replay.replay_glass_expert(encoded(replay_payload))
    asphere = EvenAsphere(geometry.cs, 50.0, coefficients=[0.0])
    native_edit(
        replay_payload,
        lambda data: data["surface_group"]["surfaces"][1].update(
            geometry=asphere.to_dict()
        ),
    )
    for section in ("variables", "resolved_variables"):
        replay_payload[section].pop()
        replay_payload[section][0]["definition"] = {
            "type": "asphere_coeff",
            "surface_number": 1,
            "coeff_number": 3,
        }
    with pytest.raises(ValueError, match="construction changed the reference"):
        replay.replay_glass_expert(encoded(replay_payload))
