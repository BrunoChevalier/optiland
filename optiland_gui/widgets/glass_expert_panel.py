"""Native configuration and explicit review of a detached GlassExpert run."""

from __future__ import annotations

import json
from contextlib import ExitStack

from PySide6.QtCore import QSignalBlocker, Qt, Signal, Slot
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from optiland.optimization.material_candidates import MaterialPool
from optiland_gui.services.file_artifacts import BytesArtifact
from optiland_gui.services.glass_expert_pools import GlassExpertPools
from optiland_gui.services.glass_expert_records import (
    GlassExpertSettings,
    MaterialTarget,
)
from optiland_gui.widgets.material_export_dialog import MaterialExportDialog


def _number(value):
    return "—" if value is None else f"{value:.12g}"


def _key(key):
    if key is None:
        return "Reference only"
    return f"{key.source_id} / {key.catalog} / {key.record_id}"


def _label(text, parent):
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _table(headers, parent):
    table = QTableWidget(0, len(headers), parent)
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    table.setMinimumWidth(0)
    return table


def _rows(table, rows):
    """Replace an immutable review page only when its owning result changes."""
    table.setUpdatesEnabled(False)
    try:
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(row, column, item)
    finally:
        table.setUpdatesEnabled(True)


class GlassExpertPanel(QWidget):
    """Configure exact pools; inspect frozen results before whole-model Apply.

    ``entries`` returns current accepted ``(MaterialSummary, SourceStamp)`` pairs
    for the explicit selection, pins or filtered-results action. It is called only
    on that action, never while preparing or running a previously frozen pool.
    """

    edit_objective_requested = Signal()
    help_requested = Signal()

    def __init__(self, connector, entries, parent=None):
        super().__init__(parent)
        from optiland_gui.services.glass_expert_files import GlassExpertFiles

        self.connector = connector
        self.service = connector._optimization_service
        self.entries = entries
        self.pools = GlassExpertPools(connector, self)
        self.files = GlassExpertFiles(connector, self)
        self.export_dialog = MaterialExportDialog(connector.file_operations, self)
        self._closed = False
        self._targets = {}
        self._exclusions = {}
        self._pool_row = None
        self._structure = None
        self._draft = 0
        self._prepared_draft = None
        self._seen_prepared = None
        self._seen_result = None
        self._loading = False
        self._load_dialog = None
        self._candidate_windows = []
        self._open_result = None
        self._preparing_notice = False
        self._build_ui()
        self.service.glassExpertChanged.connect(self.refresh)
        self.service.glassExpertProgress.connect(self._progress)
        self.service.stateChanged.connect(self.refresh)
        self.pools.ready.connect(self._pool_ready)
        self.pools.changed.connect(self._pool_changed)
        self.files.loaded.connect(self._loaded)
        self.files.exported.connect(self._exported)
        self.files.opened.connect(self._opened)
        self.files.statusChanged.connect(self._file_changed)
        self.export_dialog.message.connect(self.notice.setText)
        self.export_dialog.state_changed.connect(self.refresh)
        connector.document_state.committed.connect(self._document_changed)
        connector.materials.source_changed.connect(self.refresh)
        connector.materials.source_removed.connect(self.refresh)
        connector.document_acceptance.state_changed.connect(self.refresh)
        connector.file_operations.state_changed.connect(self.refresh)
        connector.history.state_changed.connect(self.refresh)
        connector.placement.state_changed.connect(self.refresh)
        connector.material_apply.state_changed.connect(self.refresh)
        connector.calculation_jobs.stopped.connect(self.close_service)
        self._document_changed()
        self.refresh()

    def _button(self, text, callback, layout):
        button = QPushButton(text, self)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _build_ui(self):
        self.setAccessibleName("GlassExpert frozen setup and candidate review")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        top = QHBoxLayout()
        self.objective_button = self._button(
            "Edit objective and continuous variables",
            self.edit_objective_requested.emit,
            top,
        )
        self.help_button = self._button("Help", self.help_requested.emit, top)
        layout.addLayout(top)
        self.pages = QTabWidget(self)
        layout.addWidget(self.pages, 1)
        self.setup_scroll = QScrollArea(self)
        self.setup_scroll.setWidgetResizable(True)
        setup = QWidget(self)
        form = QVBoxLayout(setup)
        form.addWidget(
            _label(
                "Configure objectives in Optimization. Here, choose a frozen "
                "material pool for each medium after a surface. "
                "The starting glass is a separate reference.",
                self,
            )
        )
        target_line = QHBoxLayout()
        self.surface = QComboBox(self)
        self.surface.setAccessibleName("Material target medium after surface")
        target_line.addWidget(self.surface, 1)
        self.add_target_button = self._button(
            "Add target", self.add_target, target_line
        )
        self.remove_target_button = self._button(
            "Remove target", self.remove_target, target_line
        )
        form.addLayout(target_line)
        self.target_table = _table(["Medium", "Pool", "Source state"], self)
        self.target_table.setAccessibleName("Configured material targets")
        self.target_table.setMinimumHeight(120)
        self.target_table.setMaximumHeight(180)
        self.target_table.currentCellChanged.connect(self._target_selected)
        form.addWidget(self.target_table)
        self.baseline_label = _label(
            "Choose a target to inspect its reference and pool.", self
        )
        form.addWidget(self.baseline_label)
        pool_line = QHBoxLayout()
        self.pool_mode = QComboBox(self)
        for label, value in (
            ("Selection", "selection"),
            ("Pins", "pins"),
            ("Current results", "results"),
        ):
            self.pool_mode.addItem(label, value)
        self.pool_mode.setAccessibleName("Explicit pool source")
        pool_line.addWidget(self.pool_mode, 1)
        self.pool_button = self._button(
            "Set pool from chosen entries", self.set_pool, pool_line
        )
        form.addLayout(pool_line)
        self.pool_table = _table(
            ["Material", "Catalog", "Source / record", "State / reason"], self
        )
        self.pool_table.setAccessibleName("Exact pool membership and exclusions")
        self.pool_table.setMinimumHeight(180)
        form.addWidget(self.pool_table)
        self.pool_conditions = _label("", self)
        form.addWidget(self.pool_conditions)
        form.addWidget(
            _label(
                "Distance scales affect candidate sampling in n_d/V_d space; "
                "they do not change objective weights. Search is limited "
                "and does not guarantee a global optimum.",
                self,
            )
        )
        settings = QFormLayout()
        settings.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.settings_controls = {}
        defaults = GlassExpertSettings()
        for name, title, maximum in (
            ("global_samples", "Global samples per pool", 10000),
            ("num_neighbours", "Local neighbours", 10000),
            ("maxiter", "Iterations per trial", 1000000),
            ("report_limit", "Retained trial records", 1000),
            ("seed", "Random seed", 4294967295),
        ):
            control = QDoubleSpinBox(self) if name == "seed" else QSpinBox(self)
            if name == "seed":
                control.setDecimals(0)
            control.setRange(
                2 if name == "global_samples" else 0 if name == "seed" else 1, maximum
            )
            control.setValue(getattr(defaults, name))
            self._setting(name, title, control, settings)
        for name, title in (
            ("tol", "Solver tolerance"),
            ("nd_scale", "n_d distance scale"),
            ("vd_scale", "V_d distance scale"),
        ):
            control = QLineEdit(self)
            validator = QDoubleValidator(0.0, 1.7976931348623157e308, 323, control)
            validator.setLocale(control.locale())
            validator.setNotation(QDoubleValidator.Notation.ScientificNotation)
            control.setValidator(validator)
            control.setText(control.locale().toString(getattr(defaults, name), "g", 17))
            self._setting(name, title, control, settings)
        form.addLayout(settings)
        self.problem_summary = QPlainTextEdit(self)
        self.problem_summary.setReadOnly(True)
        self.problem_summary.setTabChangesFocus(True)
        self.problem_summary.setAccessibleName(
            "Frozen objective variables and optical conditions"
        )
        self.problem_summary.setMinimumHeight(190)
        form.addWidget(self.problem_summary)
        self.setup_scroll.setWidget(setup)
        self.pages.addTab(self.setup_scroll, "Setup")

        review = QWidget(self)
        review_layout = QVBoxLayout(review)
        self.result_summary = _label(
            "Prepare a problem and run GlassExpert to review a complete candidate.",
            self,
        )
        review_layout.addWidget(self.result_summary)
        self.review_pages = QTabWidget(self)
        self.changes_table = _table(
            ["Variable", "Baseline", "Candidate", "Change"], self
        )
        self.operands_table = _table(
            [
                "Operand",
                "Baseline",
                "Candidate",
                "Target",
                "Weight",
                "Bounds",
                "Candidate constraints",
            ],
            self,
        )
        self.trials_table = _table(
            [
                "Trial",
                "Phase",
                "Material",
                "Merit",
                "Evaluations",
                "Solver converged",
                "Status",
            ],
            self,
        )
        self.warnings = QPlainTextEdit(self)
        self.warnings.setReadOnly(True)
        self.warnings.setTabChangesFocus(True)
        for widget, name in (
            (self.changes_table, "Changes"),
            (self.operands_table, "Operands"),
            (self.trials_table, "Trials"),
            (self.warnings, "Warnings"),
        ):
            widget.setAccessibleName("Frozen GlassExpert " + name.lower())
            self.review_pages.addTab(widget, name)
        review_layout.addWidget(self.review_pages, 1)
        self.pages.addTab(review, "Review")
        self.notice = _label("", self)
        self.status = _label("", self)
        self.freshness = _label("", self)
        layout.addWidget(self.notice)
        layout.addWidget(self.status)
        layout.addWidget(self.freshness)
        self.progress = QProgressBar(self)
        self.progress.setAccessibleName("GlassExpert progress; total work is not known")
        layout.addWidget(self.progress)
        run = QHBoxLayout()
        self.prepare_button = self._button(
            "Prepare current design", self.prepare_current, run
        )
        self.run_button = self._button("Run frozen setup", self.run_prepared, run)
        self.stop_button = self._button("Stop", self.stop, run)
        layout.addLayout(run)
        apply = QHBoxLayout()
        self.apply_button = self._button(
            "Apply complete candidate", self.apply_candidate, apply
        )
        self.open_button = self._button(
            "Open candidate separately", self.open_candidate, apply
        )
        layout.addLayout(apply)
        outputs = QHBoxLayout()
        self.output_kind = QComboBox(self)
        for text, kind in (
            ("Save run setup", "manifest"),
            ("Export script", "script"),
            ("Export native candidate", "candidate"),
        ):
            self.output_kind.addItem(text, kind)
        self.output_kind.setAccessibleName("GlassExpert output type")
        outputs.addWidget(self.output_kind, 1)
        self.export_button = self._button("Export…", self.export_run, outputs)
        self.load_button = self._button("Load run setup…", self.choose_setup, outputs)
        layout.addLayout(outputs)

    def _setting(self, name, title, control, layout):
        control.setAccessibleName(title)
        if isinstance(control, QLineEdit):
            control.textChanged.connect(self._draft_changed)
        else:
            control.setKeyboardTracking(False)
            control.valueChanged.connect(self._draft_changed)
            control.lineEdit().textEdited.connect(self._draft_changed)
        self.settings_controls[name] = control
        layout.addRow(title, control)

    @Slot()
    def _draft_changed(self, *args):
        if self._loading:
            return
        self._draft += 1
        self.refresh()

    def _selected_row(self):
        index = self.target_table.currentRow()
        rows = sorted(self._targets)
        return rows[index] if 0 <= index < len(rows) else None

    @Slot()
    def add_target(self):
        row = self.surface.currentData()
        if row is None or row in self._targets:
            return
        self._targets[row] = None
        self._target_rows(row)
        self._draft_changed()

    @Slot()
    def remove_target(self):
        row = self._selected_row()
        if row is not None:
            self._targets.pop(row)
            self._exclusions.pop(row, None)
            self._target_rows()
            self._draft_changed()

    def _source_state(self, pool):
        if pool is None:
            return "Choose a pool"
        for stamp in {c.stamp for c in pool.candidates}:
            source = self.connector.materials.source_result(stamp.source_id)
            if source is None:
                return "Missing source; frozen data retained"
            if source.snapshot.stamps != (stamp,):
                return "Source changed; recreate pool"
        return "Current sources"

    def _target_rows(self, selected=None):
        selected = self._selected_row() if selected is None else selected
        with QSignalBlocker(self.target_table):
            _rows(
                self.target_table,
                [
                    (
                        f"After surface {row}",
                        "Not set"
                        if pool is None
                        else "1 (fixed)"
                        if len(pool.candidates) == 1
                        else str(len(pool.candidates)),
                        self._source_state(pool),
                    )
                    for row, pool in sorted(self._targets.items())
                ],
            )
            rows = sorted(self._targets)
            if rows:
                self.target_table.selectRow(
                    rows.index(selected) if selected in rows else 0
                )
        self._target_selected()

    @Slot()
    def _target_selected(self, *args):
        row = self._selected_row()
        optic = self.connector.get_optic()
        reference = "Unavailable"
        if row is not None and optic is not None and row < len(optic.surfaces):
            material = optic.surfaces[row].material_post
            reference = str(getattr(material, "name", type(material).__name__))
        self.baseline_label.setText(
            "Choose a target to inspect its reference and pool."
            if row is None
            else f"Medium after surface {row} — current reference: {reference}. "
            "Only explicitly listed eligible records enter the pool."
        )
        pool = self._targets.get(row)
        _rows(
            self.pool_table,
            [
                (
                    c.display_name,
                    c.key.catalog,
                    f"{c.key.source_id} / {c.key.record_id}",
                    "Eligible",
                )
                for c in (() if pool is None else pool.candidates)
            ]
            + [
                (
                    c.label,
                    c.key.catalog,
                    f"{c.key.source_id} / {c.key.record_id}",
                    "Excluded: " + c.reason,
                )
                for c in self._exclusions.get(row, ())
            ],
        )
        conditions = sorted(
            {
                f"{c.conditions.index_convention} index; "
                f"{c.conditions.wavelength_convention} wavelengths; "
                f"T={_number(c.conditions.temperature_c)} °C; "
                f"p={_number(c.conditions.pressure_atm)} atm"
                for c in (() if pool is None else pool.candidates)
            }
        )
        self.pool_conditions.setText(
            "Native pool conditions: " + "; ".join(conditions) if conditions else ""
        )

    @Slot()
    def set_pool(self, mode=None):
        row = self._selected_row()
        if row is None:
            self.notice.setText("Add and select a material target first.")
            return
        if self.service.is_running or self.pools.busy:
            return
        try:
            entries = tuple(
                self.entries(
                    mode if isinstance(mode, str) else self.pool_mode.currentData()
                )
            )
            if not entries:
                raise ValueError(
                    "No current entries were chosen. "
                    "Select, pin or find exact materials first."
                )
            optic = self.connector.get_optic()
            if optic is None:
                raise ValueError("Open a design before configuring its material pools.")
            self._pool_row = row
            self.pools.build(
                entries, tuple(float(w.value) for w in optic.wavelengths.wavelengths)
            )
        except (ValueError, TypeError) as error:
            self.notice.setText(str(error))

    @Slot(object)
    def _pool_ready(self, result):
        row = self._pool_row
        if row not in self._targets:
            return
        self._targets[row] = (
            MaterialPool(result.candidates) if result.candidates else None
        )
        self._exclusions[row] = result.exclusions
        self._target_rows(row)
        self._draft_changed()

    @Slot()
    def _pool_changed(self):
        self.notice.setText(self.pools.message)
        self.refresh()

    def _settings(self):
        controls = tuple(self.settings_controls.values())
        if any(not control.hasAcceptableInput() for control in controls):
            raise ValueError("Complete every numeric setting before preparing the run.")
        with ExitStack() as stack:
            for control in controls:
                stack.enter_context(QSignalBlocker(control))
                if not isinstance(control, QLineEdit):
                    control.interpretText()
        values = {}
        for name, control in self.settings_controls.items():
            value = (
                control.locale().toDouble(control.text())[0]
                if isinstance(control, QLineEdit)
                else control.value()
            )
            values[name] = int(value) if name == "seed" else value
        return GlassExpertSettings(**values)

    @Slot()
    def prepare_current(self):
        try:
            if not self._targets or any(
                pool is None for pool in self._targets.values()
            ):
                raise ValueError("Every material target needs an eligible pool.")
            settings = self._settings()
            targets = tuple(
                MaterialTarget(f"material:{row}", row, pool)
                for row, pool in sorted(self._targets.items())
            )
            setup = self.service.capture_glass_expert_setup(targets, settings)
            self._prepared_draft = self._draft
            self._preparing_notice = True
            self.notice.setText(
                "Preparing today's design and Optimization definitions "
                "with the displayed pools."
            )
            self.service.prepare_glass_expert(setup)
        except (ValueError, TypeError) as error:
            self._preparing_notice = False
            self.notice.setText(str(error))
        self.refresh()

    @Slot()
    def run_prepared(self):
        if self._prepared_draft != self._draft:
            self.notice.setText("Setup controls changed. Prepare them before Run.")
            return
        try:
            self.service.run_glass_expert(self.service.glass_expert_prepared())
        except ValueError as error:
            self.notice.setText(str(error))
        self.refresh()

    @Slot()
    def stop(self):
        self.service.stop()
        self.pools.stop()
        self.files.stop()
        self.refresh()

    @Slot()
    def apply_candidate(self):
        self.service.apply_glass_expert_candidate()
        self.refresh()

    @Slot(object)
    def _progress(self, record):
        self.status.setText(
            f"{record.phase.capitalize()} · {record.trials} trials · "
            f"{record.evaluations} evaluations · best sum-squared merit "
            f"{_number(record.best_merit)} · {record.elapsed:.1f} s"
        )

    def _description(self, record):
        value = record.as_dict()
        surface = value.get("surface_number")
        parts = [record.identifier]
        if surface is not None:
            parts.append(f"Surface {surface}")
        parts.append(str(value["type"]))
        # Axis, coefficient index and operand inputs identify distinct physical
        # quantities. Bounds/targets/weights already have their own review fields.
        for name, item in value.items():
            if (
                name
                in {"type", "surface_number", "min_val", "max_val", "target", "weight"}
                or item is None
            ):
                continue
            rendered = json.dumps(item, ensure_ascii=False, sort_keys=True)
            parts.append(
                f"{name}={rendered[:512]}" + ("…" if len(rendered) > 512 else "")
            )
        description = " · ".join(parts)
        return (
            description
            if len(description) <= 2048
            else description[:2000] + "… (complete inputs in saved setup)"
        )

    def _show_prepared(self, prepared):
        setup = prepared.setup
        lines = [
            f"Frozen baseline sum-squared merit: {_number(prepared.baseline_merit)}",
            f"Backend: {setup.baseline.backend.name} / {setup.baseline.backend.device}",
            "Frozen wavelengths (µm): "
            + ", ".join(_number(w) for w in prepared.wavelengths_um),
            "",
            "Continuous variables and physical bounds:",
        ]
        for record in prepared.resolved_variables:
            data = record.as_dict()
            if data["type"] != "material":
                lines.append(
                    f"  {self._description(record)}: "
                    f"{data.get('min_val', 'unbounded')} … "
                    f"{data.get('max_val', 'unbounded')}"
                )
        lines.extend(
            ("", "Frozen objectives (targets resolved before changing materials):")
        )
        values = {r.identifier: r for r in prepared.baseline_operands}
        for record in prepared.resolved_operands:
            value = values[record.identifier]
            lines.append(
                f"  {self._description(record)}: baseline {_number(value.value)}, "
                f"target {_number(value.target)}, "
                f"weight {_number(value.effective_weight)}, "
                f"bounds {_number(value.min_val)} … {_number(value.max_val)}"
            )
        lines.extend(
            (
                "",
                "Native conditions appear with each pool. "
                "Unknown conditions remain unknown.",
            )
        )
        self.problem_summary.setPlainText("\n".join(lines))
        self.warnings.setPlainText("\n".join(prepared.warnings))

    def _show_result(self, result, prepared):
        report = result.report
        baseline, candidate = report.baseline, report.candidate
        improvement = (
            "not defined"
            if report.improvement_percent is None
            else f"{_number(report.improvement_percent)}%"
        )
        self.result_summary.setText(
            f"{report.outcome.replace('_', ' ').capitalize()} · sum-squared merit: "
            f"baseline {_number(None if baseline is None else baseline.merit)} → "
            f"candidate {_number(None if candidate is None else candidate.merit)}. "
            f"Improvement: {improvement}. Complete eligible candidate: "
            f"{'yes' if report.candidate_eligible else 'no'}. "
            f"{report.total_trials} trials, {report.evaluations} evaluations; "
            f"{report.omitted_trials} trial records omitted. "
            "Solver convergence is shown per trial."
        )
        descriptions = {
            r.identifier: self._description(r) for r in prepared.setup.variables
        }
        original = (
            {}
            if baseline is None
            else {v.variable_id: v.value for v in baseline.continuous}
        )
        changes = []
        if candidate is not None:
            for value in candidate.continuous:
                old = original.get(value.variable_id)
                changes.append(
                    (
                        descriptions[value.variable_id],
                        _number(old),
                        _number(value.value),
                        _number(None if old is None else value.value - old),
                    )
                )
            previous = (
                {}
                if baseline is None
                else {v.variable_id: v for v in baseline.materials}
            )
            for value in candidate.materials:
                old = previous.get(value.variable_id)
                changes.append(
                    (
                        descriptions[value.variable_id],
                        _key(None if old is None else old.key),
                        _key(value.key),
                        "Changed"
                        if old is None or old.fingerprint != value.fingerprint
                        else "Same native material",
                    )
                )
        _rows(self.changes_table, changes)
        operands = {r.identifier: r for r in result.candidate_operands}
        labels = {
            r.identifier: self._description(r) for r in prepared.resolved_operands
        }
        _rows(
            self.operands_table,
            [
                (
                    labels[row.identifier],
                    _number(row.value),
                    _number(operands[row.identifier].value)
                    if row.identifier in operands
                    else "—",
                    _number(row.target),
                    _number(row.effective_weight),
                    f"{_number(row.min_val)} … {_number(row.max_val)}",
                    "Within bounds"
                    if row.identifier in operands
                    and operands[row.identifier].within_bounds
                    else "Outside bounds"
                    if row.identifier in operands
                    else "No candidate",
                )
                for row in result.baseline_operands
            ],
        )
        _rows(
            self.trials_table,
            [
                (
                    t.index,
                    t.phase,
                    _key(t.candidate_key)
                    if t.candidate_key is not None
                    else "Complete assignment",
                    _number(t.merit),
                    t.evaluations,
                    "Yes" if t.converged else "No",
                    t.status + (": " + t.message if t.message else ""),
                )
                for t in report.trials
            ],
        )
        self.warnings.setPlainText("\n".join(result.warnings))
        self.pages.setCurrentIndex(1)

    @Slot()
    def refresh(self, *args):
        if self._closed:
            return
        prepared = self.service.glass_expert_prepared()
        result = self.service.glass_expert_result()
        state, message = self.service.glass_expert_status()
        busy = self.service.is_running or self.pools.busy or self.files.busy
        if self._preparing_notice and not self.service.is_running:
            self._preparing_notice = False
            self.notice.setText(message if state == "failed" else "")
        if prepared is not self._seen_prepared:
            self._seen_prepared = prepared
            if prepared is not None:
                self._show_prepared(prepared)
            else:
                self.problem_summary.setPlainText(
                    "No prepared frozen problem. Prepare the displayed setup "
                    "to evaluate its baseline."
                )
        if result is not self._seen_result:
            self._seen_result = result
            if result is not None and prepared is not None:
                self._show_result(result, prepared)
            else:
                self.result_summary.setText(
                    "No completed candidate for this setup. "
                    "Run the prepared problem to review a result."
                )
                for table in (
                    self.changes_table,
                    self.operands_table,
                    self.trials_table,
                ):
                    _rows(table, [])
        for index, (_, pool) in enumerate(sorted(self._targets.items())):
            item = self.target_table.item(index, 2)
            if item is not None:
                item.setText(self._source_state(pool))
        self.status.setText(f"{state.replace('_', ' ').capitalize()}: {message}")
        check = self.service.glass_expert_acceptance()
        stale = self.service.glass_expert_freshness()
        self.freshness.setText(
            check.message
            if result is not None
            else stale.message
            if prepared is not None
            else ""
        )
        if prepared is not None and self._draft != self._prepared_draft:
            self.freshness.setText(
                "Setup controls changed. Prepare current design to use them; "
                "retained results still describe the previous frozen setup."
            )
        self.prepare_button.setEnabled(not busy and bool(self._targets))
        self.run_button.setEnabled(
            not busy and prepared is not None and self._draft == self._prepared_draft
        )
        self.stop_button.setEnabled(
            busy and state not in {"stopping", "cancelling", "applying"}
        )
        self.stop_button.setText(
            "Stopping…" if state in {"stopping", "cancelling"} else "Stop"
        )
        self.apply_button.setEnabled(not busy and check.allowed)
        self.apply_button.setToolTip(check.message)
        self.open_button.setEnabled(
            not busy and result is not None and result.native_candidate is not None
        )
        self.open_button.setToolTip(
            "Open the complete portable prescription in a separate window for analysis."
            if result is not None and result.native_candidate is not None
            else (
                result.replay_unavailable
                or "No portable complete candidate is available."
            )
            if result is not None
            else "Run GlassExpert to create a candidate."
        )
        self.export_button.setEnabled(
            not busy and prepared is not None and not self.export_dialog.choosing
        )
        self.load_button.setEnabled(not busy and self._load_dialog is None)
        for control in (
            self.surface,
            self.add_target_button,
            self.remove_target_button,
            self.pool_mode,
            self.pool_button,
            *self.settings_controls.values(),
        ):
            control.setEnabled(not busy)
        self.progress.setRange(0, 0 if busy else 1)
        self.progress.setValue(0)
        self.progress.setVisible(busy)

    @Slot()
    def _document_changed(self, *args):
        optic = self.connector.get_optic()
        structure = (optic, tuple(optic.surfaces)) if optic is not None else None
        previous = self._structure
        changed = (structure is None) != (previous is None)
        if structure is not None and previous is not None:
            changed = (
                structure[0] is not previous[0]
                or len(structure[1]) != len(previous[1])
                or any(
                    a is not b for a, b in zip(structure[1], previous[1], strict=True)
                )
            )
        if changed:
            self._structure = structure
            self.pools.stop()
            self._pool_row = None
            self._targets.clear()
            self._exclusions.clear()
            self.surface.clear()
            if optic is not None:
                for row in range(1, max(1, len(optic.surfaces) - 1)):
                    self.surface.addItem(f"Medium after surface {row}", row)
            self._target_rows()
            self._draft_changed()
        self.refresh()

    @Slot()
    def choose_setup(self):
        if self._load_dialog is not None:
            self._load_dialog.raise_()
            return
        dialog = QFileDialog(self, "Load GlassExpert run setup")
        self._load_dialog = dialog
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter("GlassExpert setup (*.json)")
        dialog.finished.connect(self._setup_chosen)
        dialog.open()
        self.refresh()

    @Slot(int)
    def _setup_chosen(self, result):
        dialog, self._load_dialog = self._load_dialog, None
        if dialog is None:
            return
        paths = dialog.selectedFiles()
        dialog.deleteLater()
        if not self._closed and result == QDialog.DialogCode.Accepted and paths:
            self.load_setup(paths[0])
        self.refresh()

    def load_setup(self, path):
        if not self.service.is_running and not self.pools.busy:
            self.files.load(path)

    @Slot(object)
    def _loaded(self, loaded):
        if self._closed:
            return
        if self.service.is_running:
            self.notice.setText(
                "Optimization started before the setup could be installed. "
                "Load it again when idle."
            )
            return
        prepared = loaded.prepared
        self._loading = True
        try:
            self._targets = {
                target.surface_number: target.pool for target in prepared.setup.targets
            }
            self._exclusions.clear()
            for name, control in self.settings_controls.items():
                with QSignalBlocker(control):
                    value = getattr(prepared.setup.settings, name)
                    if isinstance(control, QLineEdit):
                        control.setText(control.locale().toString(value, "g", 17))
                    else:
                        control.setValue(value)
            self._target_rows()
        finally:
            self._loading = False
        self._draft += 1
        self._prepared_draft = self._draft
        self.service.prepare_glass_expert(prepared.setup)
        self.notice.setText(
            "Loaded a detached frozen setup. Run/export uses its stored baseline "
            "and objectives. Prepare current design explicitly replaces them "
            "with today's design and Optimization definitions.\n"
            + "\n".join(loaded.warnings)
        )
        self.refresh()

    @Slot()
    def export_run(self, kind=None):
        if self.export_dialog.choosing:
            return
        prepared = self.service.glass_expert_prepared()
        if prepared is not None:
            self.files.export(
                kind if isinstance(kind, str) else self.output_kind.currentData(),
                prepared,
                self.service.glass_expert_result(),
            )

    @Slot(object)
    def _exported(self, result):
        if self._closed:
            return
        if result.data is None:
            self.notice.setText(
                result.unavailable_reason or "This output is unavailable."
            )
            return
        names = {
            "manifest": ("json", "glass-expert-setup"),
            "script": ("py", "glass-expert-run"),
            "candidate": ("json", "glass-expert-candidate"),
        }
        extension, name = names[result.kind]
        self.export_dialog.choose(BytesArtifact(result.data), extension, name)
        self.refresh()

    @Slot()
    def _file_changed(self):
        self.notice.setText(self.files.message)
        self.refresh()

    @Slot()
    def open_candidate(self):
        result = self.service.glass_expert_result()
        prepared = self.service.glass_expert_prepared()
        if result is None or prepared is None or result.native_candidate is None:
            return
        self._open_result = result
        self.files.open_candidate(prepared, result)

    @Slot(object)
    def _opened(self, opened):
        from optiland_gui.main_window import MainWindow
        from optiland_gui.services.job_records import BackendConfig

        result, self._open_result = self._open_result, None
        if (
            self._closed
            or result is None
            or self.service.glass_expert_result() is not result
        ):
            return
        if opened.prepared.backend != BackendConfig.capture():
            self.notice.setText(
                "Switch to the run's backend before opening its candidate."
            )
            return
        window = None
        try:
            candidate = opened.prepared.restore()
            window = MainWindow()
            if self._closed or self.service.glass_expert_result() is not result:
                window.close()
                return
            window.connector._file_service._publish_candidate(
                candidate, None, modified=True, validated=True
            )
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            identity = id(window)
            window.destroyed.connect(lambda: self._forget_window(identity))
            self._candidate_windows.append(window)
            window.show()
        except Exception as error:
            if window is not None:
                window.close()
            self.notice.setText(f"Could not open candidate: {error}")

    def _forget_window(self, identity):
        self._candidate_windows = [
            w for w in self._candidate_windows if id(w) != identity
        ]

    @Slot()
    def close_service(self):
        self._closed = True
        self.pools.close()
        self.files.close()
        self.export_dialog.close()
        if self._load_dialog is not None:
            self._load_dialog.reject()
        self.setEnabled(False)
