"""Owned file preparation and explicit publication in the calculation process."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from optiland.fileio import (
    load_codev_file,
    load_zemax_file,
    save_codev_file,
    save_zemax_file,
)
from optiland.optic import Optic
from optiland_gui.services.job_records import OpticSnapshot, check_cancelled
from optiland_gui.services.model_initialization import initialize_loaded_optic


def load_file(snapshot, parameters, progress, cancelled):
    """Parse and validate a candidate without touching the displayed document."""
    from optiland_gui.services.file_service import json_inf_nan_hook

    parameters["backend"].apply()
    progress("Reading optical file")
    file_format = parameters["format"]
    if file_format == "optiland":
        with open(parameters["path"], encoding="utf-8") as stream:
            candidate = Optic.from_dict(
                json.load(stream, object_hook=json_inf_nan_hook)
            )
    elif file_format == "zemax":
        candidate = load_zemax_file(parameters["path"])
    elif file_format == "codev":
        candidate = load_codev_file(parameters["path"])
    elif file_format == "object":
        candidate = snapshot.restore()
    else:
        raise ValueError(f"Unsupported file format: {file_format}")
    check_cancelled(cancelled)
    progress("Validating optical document")
    initialize_loaded_optic(candidate)
    check_cancelled(cancelled)
    return OpticSnapshot.capture(candidate)


def _stage_path(parameters):
    path = Path(parameters["staged_path"])
    destination = Path(parameters["path"])
    if (
        path.parent != destination.parent
        or not path.name.startswith(".optiland-")
        or path.suffix != ".tmp"
    ):
        raise ValueError("Invalid owned staging path.")
    return path


def _digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare_output(snapshot, parameters, progress, cancelled):
    """Write an owned sibling file while the destination remains untouched."""
    from optiland_gui.services.file_service import SpecialFloatEncoder

    path = _stage_path(parameters)
    progress("Preparing file contents")
    optic = snapshot.restore()
    try:
        # Reserve only our unique sibling file, never truncate the destination.
        with open(path, "x", encoding="utf-8") as stream:
            if parameters["format"] == "optiland":
                encoder = SpecialFloatEncoder(indent=4)
                for chunk in encoder.iterencode(optic.to_dict()):
                    check_cancelled(cancelled)
                    stream.write(chunk)
        if parameters["format"] == "zemax":
            save_zemax_file(optic, path)
        elif parameters["format"] == "codev":
            save_codev_file(optic, path)
        elif parameters["format"] != "optiland":
            raise ValueError("Unsupported output format.")
        check_cancelled(cancelled)
        progress("Verifying prepared file")
        return {"digest": _digest(path)}
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def publish_output(snapshot, parameters, progress, cancelled):
    """Called only after the GUI accepts the non-cancellable commit phase."""
    path = _stage_path(parameters)
    if _digest(path) != parameters["digest"]:
        raise ValueError("Prepared file contents changed before publication.")
    os.replace(path, parameters["path"])
    return {"published": True}


def reconcile_output(snapshot, parameters, progress, cancelled):
    """Confirm content after an interrupted publication without claiming rollback."""
    path = Path(parameters["path"])
    return {"matches": path.is_file() and _digest(path) == parameters["digest"]}


def cleanup_output(snapshot, parameters, progress, cancelled):
    """Remove only this operation's own staging file, outside the Qt thread."""
    _stage_path(parameters).unlink(missing_ok=True)
