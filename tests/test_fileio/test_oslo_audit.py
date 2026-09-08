"""The example audit preserves diagnostics for arbitrary archive member names."""

import json
import zipfile

from scripts.audit_oslo_examples import audit


def test_archive_member_names_are_normalized_before_json_encoding(tmp_path):
    name = 'demos/quoted "lens"\\example.len'
    archive = tmp_path / "examples.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(
            name,
            'LEN NEW "audit" 50 3\nEBR 2\nANG 0\n'
            "TH 1e20\nNXT\nGLA 1.5\nRD 20\nTH 2\n"
            "UNMAPPED\nNXT\nAIR\nRD -20\nTH 20\nNXT\nAIR\nEND 3\n",
        )
        # ZipFile normalizes native directory separators when writing on Windows.
        name = zipped.namelist()[0]
    report = audit(archive, "numpy")
    row = json.loads(json.dumps(report))["files"][0]
    assert row["file"] == name
    assert row["import_error"] is None
    assert name in row["warnings"][0]
    assert name in row["strict_error"]
    assert "oslo-audit-" not in json.dumps(report)
    assert report["summary"] == {"warned": 1}
