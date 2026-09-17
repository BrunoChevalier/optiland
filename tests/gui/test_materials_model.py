"""Native results retain exact identity and numerical/unknown semantics."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import replace

import pytest
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QLocale,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtGui import QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTableView

from optiland.materials.properties import (
    MaterialKey,
    MaterialMatch,
    MaterialSummary,
    PropertyValue,
    ReferenceConditions,
)
from optiland_gui.models.materials_model import (
    MaterialsSortProxyModel,
    MaterialsTableModel,
)


def summary(source="installed", nd=1.5, vd=60):
    return MaterialSummary(
        MaterialKey(source, "SAME", "exact-reference"),
        "Equal name",
        PropertyValue(nd, "available")
        if nd is not None
        else PropertyValue(None, "invalid", reason="Malformed optical data"),
        PropertyValue(vd, "available")
        if vd is not None
        else PropertyValue(None, "missing", reason="Undefined dispersion"),
        (0.4, 0.8),
        None,
        "clamp",
        False,
        False,
    )


def test_proxy_sorts_numbers_and_preserves_qualified_identity(qapp):
    rows = (
        summary("first", 1.9, 100),
        summary("second", 1.4, 20),
        summary("third", 1.7, -60),
    )
    model = MaterialsTableModel()
    model.set_records(rows)
    proxy = MaterialsSortProxyModel()
    proxy.setSourceModel(model)
    proxy.sort(4, Qt.SortOrder.AscendingOrder)
    assert [proxy.index(i, 0).data(model.KeyRole) for i in range(3)] == [
        rows[2].key,
        rows[1].key,
        rows[0].key,
    ]
    assert model.key_at(proxy.mapToSource(proxy.index(0, 0)).row()) == rows[2].key
    assert model.row_for_key(rows[0].key) == 0
    assert len({proxy.index(i, 0).data(model.KeyRole) for i in range(3)}) == 3


def test_unavailable_properties_never_sort_as_zero(qapp):
    row = summary(nd=None, vd=None)
    model = MaterialsTableModel()
    model.set_records((row,))
    assert model.index(0, 3).data() == "Invalid"
    assert model.index(0, 4).data() == "Missing"
    assert model.index(0, 3).data(model.SortRole) is None
    assert model.index(0, 4).data(model.SortRole) is None
    assert model.index(0, 5).data() == "Not requested"
    assert model.index(0, 7).data() == "Not requested"
    assert model.index(0, 9).data() == "Unknown"
    tip = model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
    assert "Malformed optical data" in tip and "Undefined dispersion" in tip
    assert "exact-reference" in tip and "No portable definition" in tip


def test_localized_numbers_and_signed_match_deltas(qapp):
    previous = QLocale()
    try:
        QLocale.setDefault(QLocale("nl_BE"))
        row = summary()
        model = MaterialsTableModel()
        model.set_records((row,), (MaterialMatch(row.key, -0.005, 2.5, 0.75),))
        assert model.index(0, 3).data() == "1,5"
        assert model.index(0, 5).data() == "-0,005"
        assert model.index(0, 6).data() == "2,5"
        assert model.index(0, 7).data(model.SortRole) == 0.75
        assert model.index(0, 8).data() == "0,4–0,8"
        assert model.index(0, 8).data(model.SortRole) == 0.4
    finally:
        QLocale.setDefault(previous)


def test_filter_replacement_is_atomic_and_key_lookup_tracks_new_rows(qapp):
    first, second = summary("first"), summary("second")
    model = MaterialsTableModel()
    resets = []
    model.modelReset.connect(lambda: resets.append(model.rowCount()))
    model.set_records((first, second))
    model.set_records((second,))
    assert resets == [2, 1]
    assert model.row_for_key(first.key) == -1
    assert model.row_for_key(second.key) == 0
    assert model.key_at(-1) is None and model.key_at(1) is None
    with pytest.raises(ValueError, match="unique"):
        model.set_records((first, first))
    assert model.key_at(0) == second.key and resets == [2, 1]
    duplicate = MaterialMatch(second.key, None, None, 0)
    with pytest.raises(ValueError, match="unique"):
        model.set_records((second,), (duplicate, duplicate))


def test_tooltip_is_plain_qualified_native_conditions(qapp):
    row = replace(
        summary(),
        display_name="<b>Unsafe & label</b>",
        conditions=ReferenceConditions("air-relative", "air", 25),
    )
    model = MaterialsTableModel()
    model.set_records((row,))
    tip = model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
    assert (
        "<b>" not in tip
        and "Source: installed" in tip
        and "Record: exact-reference" in tip
    )
    assert "25 °C" in tip and "no convention normalization" in tip
    assert "air-relative" in tip and "Table bounds: clamp" in tip


def test_standard_model_roles_headers_and_read_only_flags(qapp):
    model = MaterialsTableModel()
    model.set_records((summary(),))
    child = model.index(0, 0)
    assert model.rowCount(child) == model.columnCount(child) == 0
    assert model.data(QModelIndex()) is None
    assert model.index(0, 1).data(model.SortRole) == "same"
    assert model.index(0, 2).data() == "installed"
    assert model.index(0, 0).data(Qt.ItemDataRole.DecorationRole) is None
    assert not model.flags(child) & Qt.ItemFlag.ItemIsEditable
    assert model.headerData(3, Qt.Orientation.Horizontal) == "n_d"
    assert model.headerData(0, Qt.Orientation.Vertical) == "1"
    assert model.headerData(10, Qt.Orientation.Horizontal) is None
    assert (
        model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
        is None
    )
    assert (
        model.index(0, 3).data(Qt.ItemDataRole.TextAlignmentRole)
        & Qt.AlignmentFlag.AlignRight
    )


def test_portable_summary_does_not_claim_capture_is_missing(qapp):
    model = MaterialsTableModel()
    model.set_records((replace(summary(), owned_data=b"{}"),))
    assert "No portable definition" not in model.index(0, 0).data(
        Qt.ItemDataRole.ToolTipRole
    )
    assert model.data(model.createIndex(0, 99)) is None


@pytest.mark.parametrize(
    "order", [Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder]
)
def test_missing_values_last_and_equal_values_use_exact_identity_ties(qapp, order):
    rows = (
        summary("z-missing", vd=None),
        summary("b", vd=60),
        summary("a", vd=60),
        summary("other", vd=20),
        summary("a-missing", vd=None),
    )
    model = MaterialsTableModel()
    model.set_records(rows)
    proxy = MaterialsSortProxyModel()
    proxy.setSourceModel(model)
    proxy.sort(4, order)
    sources = [proxy.index(i, 0).data(model.KeyRole).source_id for i in range(5)]
    assert sources == (
        (
            ["other", "a", "b"]
            if order == Qt.SortOrder.AscendingOrder
            else ["a", "b", "other"]
        )
        + ["a-missing", "z-missing"]
    )


def test_cached_sorting_refreshes_atomically_with_new_records_and_source(qapp):
    model = MaterialsTableModel()
    first = replace(summary("first"), n_range_um=None, k_range_um=(0.5, 0.7))
    model.set_records((first, summary("missing", vd=None), summary("second", vd=30)))
    proxy = MaterialsSortProxyModel()
    proxy.setSourceModel(model)
    proxy.sort(4, Qt.SortOrder.DescendingOrder)
    model.set_records((summary("missing", vd=None), summary("first", vd=10)))
    assert [proxy.index(i, 0).data(model.KeyRole).source_id for i in range(2)] == [
        "first",
        "missing",
    ]
    other = MaterialsTableModel()
    other.set_records((first,))
    proxy.setSourceModel(other)
    assert proxy.index(0, 0).data(model.KeyRole) == first.key
    assert other.index(0, 8).data(model.SortRole) is None
    assert other.index(0, 9).data(model.SortRole) == 0.5
    with pytest.raises(TypeError, match="MaterialsTableModel"):
        proxy.setSourceModel(QStandardItemModel())
    assert proxy.sourceModel() is other
    proxy.setSourceModel(None)
    assert proxy.rowCount() == 0


@pytest.mark.parametrize("column", range(10))
@pytest.mark.parametrize(
    "order", [Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder]
)
def test_every_column_sorts_values_with_unknowns_last(qapp, column, order):
    z = replace(
        summary("z", nd=1.9, vd=None), display_name="Zulu", n_range_um=(0.5, 0.8)
    )
    a = replace(
        summary("a", nd=None, vd=60),
        display_name="Alpha",
        n_range_um=None,
        k_range_um=(0.6, 0.9),
    )
    m = replace(
        summary("m", nd=1.2, vd=-2),
        display_name="Beta",
        n_range_um=(0.4, 0.6),
        k_range_um=(0.3, 0.7),
    )
    model = MaterialsTableModel()
    model.set_records(
        (z, a, m),
        (MaterialMatch(z.key, 0.01, -2, 0.5), MaterialMatch(m.key, -0.1, 2, 0.2)),
    )
    proxy = MaterialsSortProxyModel()
    proxy.setSourceModel(model)
    proxy.sort(column, order)
    ascending = ("amz", "amz", "amz", "mza", "maz", "mza", "zma", "mza", "mza", "maz")
    descending = ("zma", "amz", "zma", "zma", "amz", "zma", "mza", "zma", "zma", "amz")
    expected = ascending if order == Qt.SortOrder.AscendingOrder else descending
    assert [proxy.index(i, 0).data(model.KeyRole).source_id for i in range(3)] == list(
        expected[column]
    )
    for row in range(3):
        source = proxy.mapToSource(proxy.index(row, column))
        assert proxy.mapFromSource(source) == proxy.index(row, column)


def test_native_keyboard_current_row_and_persistent_index_survive_sort(qapp):
    model = MaterialsTableModel()
    model.set_records((summary("z", vd=70), summary("a", vd=20), summary("m", vd=50)))
    proxy = MaterialsSortProxyModel()
    proxy.setSourceModel(model)
    view = QTableView()
    view.setModel(proxy)
    view.show()
    view.setCurrentIndex(proxy.index(0, 0))
    QTest.keyClick(view, Qt.Key.Key_Down)
    assert view.currentIndex().data(model.KeyRole).source_id == "a"
    persistent = QPersistentModelIndex(view.currentIndex())
    proxy.sort(4, Qt.SortOrder.DescendingOrder)
    assert persistent.row() == 2
    assert view.currentIndex().data(model.KeyRole).source_id == "a"
    assert (
        view.selectionModel().selectedIndexes()[0].data(model.KeyRole).source_id == "a"
    )
    proxy.sort(4, Qt.SortOrder.AscendingOrder)
    assert persistent.row() == 0
    assert view.currentIndex().data(model.KeyRole).source_id == "a"
    proxy.sort(-1)
    assert persistent.row() == 1
    model.set_records((summary("new"),))
    assert not persistent.isValid()
    assert proxy.mapToSource(proxy.index(0, 0)) == model.index(0, 0)
    view.close()


def test_proxy_invalid_indexes_headers_flags_and_source_lifetime(qapp):
    model = MaterialsTableModel()
    model.set_records((summary("z", vd=70), summary("a", vd=20)))
    proxy = MaterialsSortProxyModel()
    assert proxy.columnCount() == 0
    assert proxy.headerData(0, Qt.Orientation.Horizontal) is None
    assert proxy.data(QModelIndex()) is None
    assert proxy.flags(QModelIndex()) == Qt.ItemFlag.NoItemFlags
    proxy.setSourceModel(model)
    proxy.setSourceModel(model)
    proxy.sort(4)
    proxy.sort(4)
    proxy.sort(99)
    child = proxy.index(0, 0)
    assert proxy.rowCount(child) == proxy.columnCount(child) == 0
    assert not proxy.parent(child).isValid()
    assert not proxy.index(0, 0, child).isValid()
    assert proxy.flags(child) & Qt.ItemFlag.ItemIsSelectable
    assert proxy.headerData(3, Qt.Orientation.Horizontal) == "n_d"
    assert proxy.headerData(0, Qt.Orientation.Vertical) == "2"
    assert proxy.headerData(2, Qt.Orientation.Vertical) is None
    assert not proxy.mapToSource(QModelIndex()).isValid()
    assert not proxy.mapToSource(model.index(0, 0)).isValid()
    assert not proxy.mapFromSource(QModelIndex()).isValid()
    assert not proxy.mapFromSource(proxy.index(0, 0)).isValid()
    model.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert proxy.rowCount() == proxy.columnCount() == 0


def test_unparented_proxy_observes_source_without_destructor_callback_crash():
    """Protect the suite from native faults while exercising real Qt ownership."""
    script = textwrap.dedent(
        """
        import ctypes
        import faulthandler
        import os
        import weakref
        if os.name == "nt":
            ctypes.windll.kernel32.SetErrorMode(0x0002)
        faulthandler.enable()
        from PySide6.QtWidgets import QApplication
        from optiland_gui.models.materials_model import (
            MaterialsTableModel, MaterialsSortProxyModel,
        )
        app = QApplication([])
        for index in range(3):
            model = MaterialsTableModel()
            proxy = MaterialsSortProxyModel()
            proxy.setSourceModel(model)
            observed = weakref.ref(model)
            del model
            assert observed() is None
            assert proxy.rowCount() == 0
        print("unparented lifecycle passed")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert completed.returncode == 0, completed.stderr
    assert "unparented lifecycle passed" in completed.stdout
