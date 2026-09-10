"""Audit independent OSLO configurations without OSLO or proprietary fixtures.

Usage: python -m scripts.audit_oslo_configurations lens.len report.json
       --catalog fictional.glc --backend numpy --backend torch
       --polarization unpolarized

The report contains hashes and aggregate outcomes, never source names, raw error
messages, prescriptions or ray arrays. Inputs remain local. Successful import,
smoke tracing and native persistence are separate checks, not OSLO agreement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np

import optiland.backend as be
from optiland.fileio import load_oslo_file
from optiland.fileio.oslo.reader.parser import OsloDataParser
from optiland.optic import Optic
from optiland.rays.polarization_state import create_polarization

RTOL = 1e-10
ATOL = 1e-12


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _smoke(optic: Optic) -> dict:
    """Keep raw rays internal to persistence comparisons; report counts only."""
    summary = {
        "samples": 0,
        "rays": 0,
        "transmitted": 0,
        "zero_intensity": 0,
        "nonfinite_transmitted": 0,
        "nonfinite_intensity": 0,
    }
    values = []
    for hx, hy in optic.fields.get_field_coords():
        for wave in optic.wavelengths:
            rays = optic.trace(hx, hy, wave.value, num_rays=5, distribution="line_y")
            data = np.stack(
                [
                    be.to_numpy(getattr(rays, key))
                    for key in ("x", "y", "z", "L", "M", "N", "i")
                ]
            )
            live = data[-1] > 0
            finite = np.isfinite(data).all(axis=0)
            summary["samples"] += 1
            summary["rays"] += data.shape[1]
            summary["transmitted"] += int(np.sum(live & finite))
            summary["zero_intensity"] += int(np.sum(data[-1] == 0))
            summary["nonfinite_transmitted"] += int(np.sum(live & ~finite))
            summary["nonfinite_intensity"] += int(np.sum(~np.isfinite(data[-1])))
            values.append(data.tolist())
    return {"summary": summary, "values": values}


def _matches(first: dict, second: dict) -> bool:
    return first["summary"] == second["summary"] and np.allclose(
        first["values"], second["values"], rtol=RTOL, atol=ATOL, equal_nan=True
    )


def _reopen(path: Path, backend: str) -> dict:
    """Run only in the child, proving native data needs no external materials."""
    import optiland.fileio.oslo.catalog as catalog
    from optiland.materials.material_file import MaterialFile

    def forbidden(*args, **kwargs):
        raise RuntimeError("Native reload attempted external material access")

    catalog.read_glc_catalog = forbidden
    MaterialFile._read_file = forbidden
    be.set_backend(backend)
    be.set_precision("float64")
    if backend == "torch":
        be.set_device("cpu")
    with path.open(encoding="utf-8") as stream:
        optic = Optic.from_dict(json.load(stream))
    return _smoke(optic)


def _check_configuration(
    source: Path,
    catalogs: list[Path],
    index: int,
    polarization: str,
    fresh_process: bool,
) -> dict:
    row = {
        "configuration": index,
        "imported": False,
        "traced": False,
        "native_matches": False,
        "fresh_process_matches": None,
        "phase": "import",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            optic = load_oslo_file(
                source, strict=True, glass_catalogs=catalogs, configuration=index
            )
            row["import_warnings"] = len(caught)
            row["imported"] = True
            row["surfaces"] = optic.surfaces.num_surfaces
            row["fields"] = len(optic.fields)
            row["wavelengths"] = len(optic.wavelengths)
            if polarization != "ignore":
                optic.updater.set_polarization(create_polarization(polarization))
            row["phase"] = "serialization"
            snapshot = json.dumps(optic.to_dict())
            restored = Optic.from_dict(json.loads(snapshot))
            row["phase"] = "trace"
            original = _smoke(optic)
            row["trace"] = original["summary"]
            row["traced"] = True
            row["phase"] = "native_reload"
            row["native_matches"] = _matches(original, _smoke(restored))
            if fresh_process:
                row["phase"] = "fresh_process"
                with tempfile.TemporaryDirectory(
                    prefix="oslo-config-audit-"
                ) as directory:
                    path = Path(directory) / "snapshot.json"
                    path.write_text(snapshot, encoding="utf-8")
                    child = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "scripts.audit_oslo_configurations",
                            "--reopen",
                            str(path),
                            "--backend",
                            be.get_backend(),
                        ],
                        cwd=Path(__file__).resolve().parents[1],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=60,
                    )
                    row["fresh_process_matches"] = _matches(
                        original, json.loads(child.stdout)
                    )
            row["phase"] = "complete"
        except (
            Exception
        ) as exc:  # Continue independent configurations; omit private text.
            row["error_type"] = type(exc).__name__
        row["warnings"] = len(caught)
        row["warning_types"] = sorted({warning.category.__name__ for warning in caught})
    return row


def audit(
    source: Path,
    catalogs: list[Path],
    *,
    backends=("numpy", "torch"),
    polarization="ignore",
    fresh_process=True,
) -> dict:
    """Audit every declared snapshot, including inactive ones, at float64 on CPU.

    A source polarization is an explicit audit assumption. The default leaves it
    unchanged, so Jones lenses expose a trace failure until one is chosen.
    Native comparisons use rtol=1e-10 and atol=1e-12 on positions in millimeters,
    direction cosines and intensity; these tolerances do not claim OSLO parity.
    """
    if polarization not in {
        "ignore",
        "unpolarized",
        "H",
        "V",
        "L+45",
        "L-45",
        "RCP",
        "LCP",
    }:
        raise ValueError("Unknown source polarization")
    source, catalogs = Path(source), [Path(path) for path in catalogs]
    report = {
        "strict": True,
        "precision": "float64",
        "device": "cpu",
        "polarization_assumption": polarization,
        "fresh_process_requested": fresh_process,
        "tolerances": {"rtol": RTOL, "atol": ATOL},
        "configurations": [],
        "backend_errors": {},
        "note": (
            "Import, ray smoke and native persistence only; no OSLO license or "
            "execution required. OSLO parity and historical catalog equivalence "
            "are unverified."
        ),
    }
    report["source_revision"] = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=normal")
    report["source_dirty"] = bool(status) if status is not None else None
    report["audit_script_sha256"] = _hash(Path(__file__))
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        try:
            report["lens_sha256"] = _hash(source)
            data = OsloDataParser(source, strict=False).parse()
            report["catalog_sha256"] = sorted({_hash(path) for path in catalogs})
            report["parser_diagnostics"] = len(data.diagnostics)
        except (OSError, ValueError) as exc:
            report["parse_error"] = type(exc).__name__
            data = None

    previous_backend = be.get_backend()
    backend_settings = {}
    try:
        if data is not None:
            for backend in dict.fromkeys(backends):
                try:
                    be.set_backend(backend)
                    backend_settings[backend] = (
                        be.get_precision(),
                        be.get_device() if backend == "torch" else "cpu",
                    )
                    be.set_precision("float64")
                    if backend == "torch":
                        be.set_device("cpu")
                except (ValueError, ImportError) as exc:
                    report["backend_errors"][backend] = type(exc).__name__
                    continue
                for index, configuration in sorted(data.configurations.items()):
                    row = _check_configuration(
                        source, catalogs, index, polarization, fresh_process
                    )
                    row.update(backend=backend, active=configuration.active)
                    report["configurations"].append(row)
    finally:
        for backend, (precision, device) in backend_settings.items():
            be.set_backend(backend)
            be.set_precision(f"float{precision}")
            if backend == "torch":
                be.set_device(device)
        be.set_backend(previous_backend)
    rows = report["configurations"]
    report["summary"] = {
        "total": len(rows),
        **{
            key: sum(row[key] is True for row in rows)
            for key in ("imported", "traced", "native_matches", "fresh_process_matches")
        },
    }
    required = ["imported", "traced", "native_matches"]
    if fresh_process:
        required.append("fresh_process_matches")
    report["passed"] = bool(rows) and not (
        report.get("parse_error")
        or report["backend_errors"]
        or any(
            not all(row[key] for key in required)
            or row.get("trace", {}).get("nonfinite_transmitted", 0)
            or row.get("trace", {}).get("nonfinite_intensity", 0)
            for row in rows
        )
    )
    return report


def main() -> int:
    """Write aggregate audit outcomes and return nonzero when a check fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lens", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--catalog", action="append", default=[], type=Path)
    parser.add_argument("--backend", action="append", choices=("numpy", "torch"))
    parser.add_argument(
        "--polarization",
        default="ignore",
        choices=("ignore", "unpolarized", "H", "V", "L+45", "L-45", "RCP", "LCP"),
    )
    parser.add_argument("--no-fresh-process", action="store_true")
    parser.add_argument("--reopen", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.reopen is not None:
        print(json.dumps(_reopen(args.reopen, (args.backend or ["numpy"])[0])))
        return 0
    if args.lens is None or args.output is None:
        parser.error("lens and output are required")
    report = audit(
        args.lens,
        args.catalog,
        backends=args.backend or ["numpy", "torch"],
        polarization=args.polarization,
        fresh_process=not args.no_fresh_process,
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"]))
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
