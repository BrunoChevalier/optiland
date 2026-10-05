.. _developers_guide_gui:

Optiland GUI
============

This document provides an overview of the Optiland Graphical User Interface (GUI), its components, architecture, and guidelines for developers looking to contribute to its development.

Overview
--------

The Optiland GUI, built with PySide6, provides an interactive way to design, analyze, and manage optical systems using the Optiland backend. It aims to offer a user-friendly experience while exposing the powerful features of the core library. The GUI is designed to be modular, allowing for the addition of new panels and functionalities.

Key Technologies
----------------

*   **PySide6**: The official Python bindings for Qt, used for creating the user interface.
*   **Matplotlib**: Used for 2D plotting in various panels (e.g., 2D system view, analysis plots).
*   **VTK (Visualization Toolkit)**: Used for 3D rendering of optical systems in the ViewerPanel. (Optional, GUI can run without it).

Core Architecture
-----------------

The GUI's architecture revolves around a main window that hosts several dockable panels, each dedicated to a specific aspect of optical design or analysis. The `OptilandConnector` class serves as a crucial intermediary, linking these GUI components to the underlying `Optic` object from the Optiland backend.

Key Components
--------------

Main Window (`main_window.py`)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The `MainWindow` class is the entry point and central hub of the GUI. It uses an `ActionManager` to create and manage `QAction` instances, and a `PanelManager` to handle the creation and layout of all dockable panels. Its responsibilities include:

*   **Menu Bar and Toolbars**: Provides standard application menus (File, Edit, View, Help) and quick-action toolbars.
*   **Theme Handling**: Supports light and dark themes, loading stylesheets (`.qss` files) and propagating theme changes to child widgets and plots.
*   **Window Control**: Implements a custom title bar for a modern look and feel, handling minimize, maximize, and close operations.
*   **Settings Persistence**: Uses `QSettings` to save and load window geometry and layout configurations.
*   **Dialogs**: Manages application-level dialogs like "About" and file open/save.

Optiland Connector (`optiland_connector.py`)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The `OptilandConnector` is a vital QObject that acts as a bridge between the GUI and the Optiland backend.

*   **`Optic` Object Management**: It holds and manages the instance of the `Optic` class, which represents the current optical system.
*   **Signal Emission**: Emits Qt signals when the optical system is loaded (`opticLoaded`), modified (`opticChanged`), or when specific parts like surface data or count change. GUI panels connect to these signals to refresh their views.
*   **Data Access and Modification**: Provides methods for GUI components to safely access data from the `Optic` object (e.g., `get_surface_data`, `get_column_headers`) and to modify it (e.g., `set_surface_data`, `add_surface`).
*   **Undo/Redo**: Delegates undo and redo operations to an `UndoRedoManager` instance.
*   **File Operations**: Handles loading and saving of optical systems in Optiland's JSON format.

Core Panels
~~~~~~~~~~~~~~

These are the primary QDockWidget or QWidget instances that provide specific functionalities:

*   **Lens Editor (`lens_editor.py`)**:

    *   Displays optical system data in a table format (similar to a lens data editor in commercial software).
    *   Allows users to view and edit surface parameters like radius, thickness, material, conic constant, and semi-diameter.
    *   Interacts with `OptilandConnector` to fetch data and commit changes, triggering updates in other relevant panels.
    *   Supports adding and removing surfaces.

*   **Viewer Panel (`viewer_panel.py`)**:

    *   Provides visual representations of the optical system.
    *   **2D View**: Uses Matplotlib to render a 2D cross-section of the system, including lens elements and ray paths. Includes basic navigation (pan/zoom) and a settings area to control ray display.
    *   **3D View**: Uses VTK (if available) to render a 3D model of the system, allowing for interactive rotation, panning, and zooming.
    *   Both views update in response to changes in the `Optic` object via signals from `OptilandConnector`.

    GUI ray sampling lives in ``services/layout_rays.py``. ``layout_pupil``
    returns pupil coordinates and an explicit chief index; the chief is sampled
    at normalized pupil coordinates ``Px = Py = 0`` for each field, using the
    configured aiming and polarization at the primary wavelength. GUI totals
    include the chief. ``RayCountSpinBox`` in ``widgets/ray_count.py`` steps
    through supported totals and normalizes typed values. In particular,
    hexapolar GUI totals count rays, while the helper translates them to the
    ring count expected by the existing Python distribution API. The
    :ref:`GUI quickstart <gui_quickstart>` describes the user-facing counts.

    ``trace_layout_rays`` retains chief identity alongside each physical path,
    including paths terminated by blocking. When only the chief is displayed,
    four additional, undisplayed rim probes size automatic lens outlines.
    Physical apertures and the schematic image marker retain their existing
    precedence; these probes do not alter the prescription or displayed count.

    The 2D presenter retains chief artists so ``style_chief_rays`` can change
    their color, stroke width and drawing order without submitting a calculation
    or changing the view limits. This styling belongs to the System Viewer;
    other consumers of the shared presenter need no chief-ray checkbox. The
    3D preparation includes the chief alongside 24 rim rays per field and creates
    ordinary depth-tested ray actors, with no chief-specific foreground overlay.

*   **Analysis Panel (`analysis_panel.py`)**:

    *   A comprehensive panel for running various optical analyses.
    *   Users can select an analysis type (e.g., Spot Diagram, Ray Fan, MTF).
    *   Dynamically generates a settings UI based on the selected analysis's parameters.
    *   Executes the analysis using the `OptilandConnector` to get the current `Optic` object.
    *   Displays results, typically as Matplotlib plots, in an embedded canvas.
    *   Supports multiple analysis "pages" (tabs or similar) and cloning of analyses.
    *   Includes features like saving/loading analysis settings.

*   **Optimization Panel (`optimization_panel.py`)**:

    *   Provides Variables, Operands, and Optimizer tabs for building an `OptimizationProblem`
        against the current `Optic` instance.
    *   Lets users add/edit variables and operands (with dialogs for adding and auto-generating
        operands), choose an optimizer and its settings, and run/stop the optimization with a
        persistent live-updating merit-function view.

*   **System Properties Panel (`system_properties_panel.py`)**:

    *   Manages system-wide settings that are not tied to individual surfaces.
    *   Uses a navigation tree to switch between different property editors (Aperture, Fields, Wavelengths, etc.).
    *   **Aperture Editor**: Configures the system aperture (e.g., Entrance Pupil Diameter, F-number).
    *   **Fields Editor**: Defines field points (e.g., angle or object height) and their vignetting factors.
    *   **Wavelengths Editor**: Manages the wavelengths used for analysis and their weights, including setting the primary wavelength.
    *   Changes made here are applied to the `Optic` object through the `OptilandConnector`.

Key Widgets
~~~~~~~~~~~~~~

*   **Sidebar Widget (`widgets/sidebar.py`)**:

    *   Provides main navigation between different functional areas of the GUI (e.g., Design, Analysis, Scripts).
    *   Can be collapsed to save space, showing only icons.
    *   Emits a `menuSelected` signal when a button is clicked, which the `MainWindow` uses to raise or show relevant panels.

*   **Python Terminal (`widgets/python_terminal.py`)**:

    *   An embedded IPython terminal.
    *   Provides direct access to the `OptilandConnector` instance (via the `connector` variable), allowing advanced users to interact with the optical system programmatically.
    *   Commands executed in the terminal can trigger GUI updates if they modify the `Optic` object.
    *   **Scientific Run** uses a separate ``CalculationJobs`` service registered
        with the connector for backend invalidation and asynchronous shutdown.
        Its worker receives an owned snapshot and returns a ``PreparedOptic``;
        normalization, material reconstruction and optical initialization happen
        in the worker. The GUI checks request identity, whole-document edit token
        and backend before installing the result with one Undo entry.
    *   The scientific worker receives no Qt objects, ``connector``, ``iface`` or
        interactive Console namespace. It executes trusted local Python, so this
        process boundary is not a security sandbox. A fresh execution namespace
        is created for each run; Restart also discards process and module state.
    *   ``gui.show_panel()`` and ``gui.refresh_views()`` record at most 32 commands
        for GUI-thread delivery after successful current execution. Currency is
        rechecked before each command. ``refresh_views()`` only repaints retained
        views; it neither applies a candidate nor schedules an optical solve.
    *   Source is limited to 1 MiB. Standard output and the formatted traceback
        each retain at most 64 KiB, 1,000 lines and 4,096 characters per line, plus
        a truncation marker. Progress updates are rate limited before reaching Qt.
        Stop revokes result application immediately and uses the existing worker
        cancellation grace period to retire an unresponsive process.

*   **Command Palette (`widgets/command_palette.py`)**:

    *   A floating panel that allows users to quickly search for and execute GUI commands.
    *   Similar to the command palette found in VS Code, it provides a centralized interface for discovering and running various tools, analyses, and layout operations.
    *   Triggered from the `MainWindow` via the `Ctrl+K` shortcut.

*   **Toast Notifications (``widgets/toast.py``)**:

    *   ``MainWindow`` owns a ``ToastManager``, also exposed as
        ``connector.toast_manager``. Call ``notify(message, severity)`` for
        information, success, warning, or error feedback.
    *   Cards can be dismissed by clicking them or their accessible close button.
        Non-error cards expire after seven seconds; errors persist until dismissed
        or evicted from the three-card stack.
    *   Keep these child widgets free of ``WA_TranslucentBackground``. Alongside
        native console/VTK widgets on Windows, this flag creates a layered native
        child window whose mouse hit testing can select the underlying console.

Styling and Resources
~~~~~~~~~~~~~~~~~~~~~~~~

*   **Qt StyleSheets (`.qss`)**: Themes (e.g., `dark_theme.qss`, `light_theme.qss`) are defined using QSS, Qt's CSS-like styling language. These are located in `optiland_gui/resources/styles/`.
*   **Resource Files (`resources.qrc`, `resources_rc.py`)**: Icons and other assets are managed using Qt's resource system. `resources.qrc` is an XML file defining resources, which is compiled into `resources_rc.py` using `pyside6-rcc`. This allows resources to be bundled with the application.
*   **Plot Styling (`gui_plot_utils.py`)**: Contains utility functions to apply consistent Matplotlib styles that match the selected GUI theme.

Materials workspace ownership
-----------------------------

``connector.materials`` exposes the shared ``MaterialsService`` for source
loading and property queries. It uses ``connector.calculation_jobs`` to evaluate
captured registry, GLC and current-design sources outside the Qt thread. Source
publication is atomic, failed reloads retain previously accepted data, and query
acceptance checks source generations and the relevant document/backend context.
See :doc:`material_discovery` for the API contract and runnable core examples.
See :doc:`../gui_materials` and :doc:`../gui_materials_tutorial` for user controls.

The service owns immutable accepted source data and a serialized query summary
for each source generation. Query jobs receive those summaries without copying
owned optical definitions on every keystroke. A failed staged reload retains
the accepted source. A publication revokes only queries that could include that
source; an independent catalog query survives current-design refresh.

``MaterialBrowserSources`` owns reload requests and display labels, and captures
live surface-material occurrences in bounded Qt event-loop batches. It performs
no spectral evaluation on Qt. Retained surface identities disambiguate current
occurrences; changing a document token during capture discards the capture.
Native ``QDockWidget.visibilityChanged`` drives this adapter: a child widget can
remain visible while hidden behind another dock tab, so child show/hide events
are insufficient. Hiding or stopping Materials cancels its own pending work.

``MaterialsPanel`` owns user criteria, selected exact ``MaterialKey``, visible
results and an independently retained inspected record. Publication uses an
exact-key lookup and caches qualified ``MapPoint`` values per source generation.
It clears only the affected cache when a source changes or disappears. Filtering
does not overwrite the selected identity, and current-design context does not
inflate the matching row count. Untrusted source text remains plain text;
metadata previews are explicitly bounded. No plot selection calls a document
mutation API.

``MaterialSourceControls`` owns one nonblocking native chooser and lexical,
path-qualified source identities. It passes local locators to the browser
adapter; directory traversal and parsing stay in the worker. It does not mutate
the global registry. Removal forgets a local source and its cache, preserving
the original file and any definition already embedded in a lens.

``MaterialApplyFooter`` displays a retained target and an exact selected key plus
source stamp. Only its explicit Apply action calls ``MaterialApplyService``.
The Lens Data Editor's material-cell action captures the actual Surface object;
table focus never grants new target authority. The service checks document edit
and history revisions, backend and source generation before accepting a worker's
prepared candidate. Known n/k support is checked independently of clamp policy;
unknown extinction retains its explicit native fallback warning.

``DocumentAcceptance`` supplies the short publication boundary shared with
history and placement. Ordinary live setters call ``assert_mutation_allowed``
before capture or mutation; they remain usable during asynchronous preparation,
which their edits invalidate. New file work and loaded-document publication use
``require_start("file")``. Already-owned writes use the short publication guard
so they can drain during close; close cancels pending material Apply first.
``FileOperations.has_pending_load`` includes a retained conflict candidate even
when no calculation remains active. A prepared replacement supplies a retained
surface mapping before UI rebuilding, then publishes one history/document event.
Do not reconstruct these contracts with a row number or a material name lookup.

``MaterialsTableModel`` provides immutable rows, locale-aware display and numeric
sort values. ``MaterialsSortProxyModel`` is a reset-only ``QAbstractProxyModel``:
it computes a row permutation with Python sorting, then remaps persistent Qt
indexes across layout changes. Missing values remain last in either direction;
equal values use the ascending exact key. It weakly observes the caller-owned
source QObject, avoiding source destruction during proxy teardown. Do not
replace this with a Python ``lessThan`` callback without repeating the large
catalog benchmark: crossing the Qt/Python boundary per comparison dominates
sorting time.

``GlassMap`` owns its local figure, toolbar, retained artists and screen-space
hit cache. Selection, current context and transient hover have separate visual
states. Equal context updates skip rebuilding an established view. Filtered-out
selection coordinates support only Locate and the selected outline; they never
become matches, hit candidates or reset limits. Themes change local artists and
toolbar palette without modifying global Matplotlib or application defaults.

The glass map and comparison canvases use the shared
``widgets.plot_navigation.PlotNavigationToolbar`` described in
`Shared 2D plot navigation`_. Left-click selects a material on the map;
right/middle drag and wheel zoom follow the common navigation contract.

``MaterialComparePanel`` keeps four ordered exact pins and native controls.
``MaterialComparisonService`` captures immutable source snapshots and validates
the shared worker's source/backend/request generations for map coordinates,
spectra and nearby alternatives. The Qt-free numerical contract is in
``optiland.materials.comparison``. One ``SpectralResult`` supplies plot and table
cells; missing values become NaN only in the drawing adapter. Numeric drafts
are validated and interpreted under signal blockers before reading their values.
Pin focus, Browse selection and the captured Apply target are separate states.

``MaterialPresetStore`` persists validated identities and view preferences in
QSettings. It retains unavailable catalog choices without widening the query.
``MaterialExportDialog`` freezes CSV or RGBA artifacts before opening its native
chooser; ``FileOperations`` publishes them through the shared atomic artifact
writer. A captured export cannot become a newer result while the chooser is
open. This path does not update native document path, dirty state or history.
Do not restore Matplotlib's independent Save action on either material plot.

Meaningful tests are in ``tests/gui/test_materials_service.py``,
``test_material_jobs.py``, ``test_material_browser.py``,
``test_materials_panel.py``, ``test_materials_model.py``, ``test_glass_map.py``,
``test_materials_dock_visibility.py`` and ``test_materials_delivery.py``. Apply
coverage includes ``test_material_apply.py``, ``test_material_apply_tasks.py``,
``test_materials_actions.py``, ``test_materials_workflow.py`` and
``test_document_mutation_guards.py``.
Comparison coverage adds ``test_material_compare_panel.py``,
``test_material_comparison_service.py``, ``test_material_comparison_jobs.py``,
``test_materials_comparison_integration.py``, ``test_material_exports.py`` and
``test_material_presets.py``. Core NumPy/Torch behavior is exercised by
``tests/test_material_comparison.py``.
Keep native keyboard/persistent-selection, exact source generations,
same-name/overlap, stale-result, source-lifecycle and actual-worker tests when
changing these contracts. GUI coverage is supplementary to the core CI
configuration, which omits GUI modules; do not silently combine the denominators.

Frozen GlassExpert worker strategy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The GlassExpert strategy uses the existing ``OptimizationService`` and
``CalculationJobs`` executor. ``capture_glass_expert_setup`` captures a
``PreparedOptic``, original variable/operand definitions and exact typed material
pools. Stable descriptor IDs sit beside canonical definition bytes; they are
not forwarded as arbitrary variable-constructor arguments. Current-design
ownership stays in the service, outside the frozen worker records.

``prepare_glass_expert`` evaluates a detached baseline through
``glass_expert_jobs.prepare_setup``. It checks target eligibility, supported
variables, conflicting pickups/solves, configured wavelengths and actual pool
resolution. Primary-wavelength selectors become numeric arguments, and implicit
operand targets are resolved against the pristine reference before material
substitution. A nonzero effective objective contribution is required. Frozen
settings are finite and bounded; the complete internal input/output budget is
64 MiB, with at most 32 material targets, 256 variables, 10,000 operand rows and
10,000 unique candidate definitions.

``run_glass_expert`` accepts the exact prepared setup. The worker rebuilds and
validates that setup, invokes the public typed core, then independently verifies
every continuous value/bound and exact material assignment, including singleton
pools. It reevaluates the final squared merit and proves unchanged surface
identity/order before producing the prepared candidate and row mapping.
Progress uses original descriptor associations even while the core activates its
continuous subset. Reports preserve sum-squared merit, eligibility, convergence
and improvement separately. ``OptimizationService.glassExpertProgress`` carries
the typed GlassExpert update; the ordinary ``progressChanged`` signal retains
its established payload. The existing optimizer panel derives Run/Stop controls
from the shared service's busy state.

Queue ``progress_policy="detached"`` allows retained old-document progress while
still checking job ID, generation, visibility, shutdown and captured backend.
It requires ``cancel_on_document_change=False``. Ordinary callers retain the
default current-document progress policy. Neither policy relaxes ``is_current``
or terminal acceptance freshness. The strategy additionally checks the frozen
run ID and input digest, and Stop retains ownership through terminal delivery.

GlassExpert completion retains a frozen result for inspection; it does not
publish a document. The original optimizer's automatic-if-current behavior is
unchanged. ``glass_expert_freshness`` is only a diagnostic: optical or metadata
edits, history, source generations, backend or changed nested definitions can
make a retained result stale. ``glass_expert_acceptance`` checks whether the
reviewed whole candidate can currently be accepted;
``apply_glass_expert_candidate`` repeats the checks around restoration and the
final shared publication boundary. Worker output alone never grants authority.

``GlassExpertPanel`` owns explicit UI target/pool selection, settings drafts and
frozen review pages. It uses ``GlassExpertPools`` to validate chosen exact records
in the shared queue. ``MaterialsPanel`` supplies selection, pins or the current
result set only when the user requests that pool. The existing Optimization
editor remains the sole live objective/continuous-variable editor.

The strategy retains the exact live optic/surfaces, edit and optical tokens,
history revision, original definitions, request generation and source payloads.
Its worker proof binds prepared/native bytes and the displayed review. Apply
restores and verifies the complete candidate without optical computation on Qt,
then publishes through ``DocumentAcceptance`` with a final read-only ownership
predicate. The short publication guard covers silent history/model assignment
and notifications. Undo/Redo restore the complete document, and a native no-op
adds no history entry. Close/reentry cannot revive a superseded owner.

The external manifest/replay boundary is separate. ``GlassExpertFiles`` reads and
prepares supported exports in the same shared queue, then uses the existing
artifact writer. ``optiland.optimization.glass_expert_replay`` is Qt-free and
contains only bounded JSON/native reconstruction and public core replay. A loaded
manifest is detached, including saved results; it cannot recreate an internal
``CandidateProof`` or current-document owner. Replaying embedded pools does not
resolve catalog names. Explicit preparation against today's design and source
generations is required before in-place acceptance can become available.

``glass_expert_portability`` captures native baseline/candidate bytes in the
worker, including untouched material and coating slots. Supported-behavior and
native round-trip checks gate portable output. Internal prepared transport is
distinct from external JSON; a serialized definition cannot contain a QObject,
callable, live acceptance token or executable registry alias. If native replay
cannot preserve the supported state, its reason is explicit and portable output
is unavailable. Core API usage remains independent of PySide; see
:doc:`../typed_glass_expert`.

Each application window's embedded Console owns its IPython namespace and
execution callbacks. Creating or closing a separate candidate window must not
execute another window's callbacks or invalidate its document. The consoles
still share the application process and are not isolation boundaries for long
work or arbitrary native code. Scientific Run and GlassExpert computations use
their worker services; console callbacks and kernels are cleaned up with their
owning window before Qt objects are destroyed.

Relevant tests are ``test_glass_expert_service.py``, ``test_glass_expert_jobs.py``
and ``test_calculation_jobs.py`` under ``tests/gui/``, plus public core tests
``tests/test_material_candidates.py`` and ``tests/test_glass_expert.py``.

Running the GUI
---------------

Once Optiland is installed, you can launch the GUI by simply typing the following command in your terminal or console:

.. code-block:: bash

   optiland

This command is a convenient shortcut to the main GUI script. Alternatively, you can run the GUI module directly using Python's `-m` flag, which can be useful for development:

.. code-block:: bash

   python -m optiland_gui.run_gui

Testing Notification Dismissal
------------------------------

Run ``python -m pytest tests/gui/test_toast.py`` for card and close-button clicks,
keyboard activation, timeouts, and stack cleanup. On Windows, run with the native
Qt platform (leave ``QT_QPA_PLATFORM`` unset) to include the native hit-testing
regressions. These cases are skipped on other platforms and with ``offscreen``.

The Windows regressions use ``WindowFromPoint`` to select the actual native
mouse recipient over a notification above a native text editor, then send input
to that window. Sending a test click directly to a label, or selecting it with
``QApplication.widgetAt``, bypasses layered-window hit testing and does not
reproduce clicks passing through to the console.

Shared 2D plot navigation
-------------------------

``widgets.plot_navigation.PlotNavigationToolbar`` owns the shared 2D mouse
gestures for lens layout, sag, analysis and optimization preview canvases.
Right/middle drag uses Matplotlib's frozen press-time pan transform; wheel zoom
uses each axis's scale transform so logarithmic and inverted axes behave
consistently. Ordinary left-click remains available to plot selection handlers.
Explicit toolbar tools use left-drag. A gesture holds the canvas widget lock
and records a completed view in navigation history; focus loss, Escape, scene
replacement and tool changes end active gestures.

Call ``finish_navigation()`` before replacing an existing scene, or ``update()``
when the replacement also requires clearing view history. Do not attach an
additional scroll or default-pan handler to a canvas using this toolbar.
``test_plot_navigation.py`` exercises the shared contract, while
``test_viewer_navigation.py`` checks real lens-worker integration.

The rectangle-zoom glyph combines a dotted selection box and magnifier, with
an explicit accessible name and tooltip. It follows the local foreground
palette and provides native-size pixmaps for standard and HiDPI displays.
Qt owns the finished icon; no Python icon-engine lifetime is exposed to Qt.

Contributing to the GUI
-----------------------

Developing for the Optiland GUI generally involves the following:

1.  **Understanding PySide6**: Familiarity with Qt concepts like signals and slots, layouts, widgets, and the event loop is essential.
2.  **Interacting with `OptilandConnector`**:

    *   When creating a new panel that needs to display or modify optical data, it should take an `OptilandConnector` instance in its constructor.
    *   Connect to relevant signals from the `OptilandConnector` (e.g., `opticChanged`, `surfaceDataChanged`) to update the panel's display when the underlying data changes.
    *   Use the connector's methods to fetch data (e.g., `get_optic()`, `get_surface_data()`) and its owning editing services to change it. Directly changing an `Optic` and only emitting `opticChanged` does not provide document conflict checks or an undo transaction.
3.  **Designing UI**:

    *   Use Qt Designer (optional) or create UI elements programmatically.
    *   Employ layouts (QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout) for responsive and well-organized UIs.
    *   Follow existing patterns for styling and theming. New widgets should respect the application's theme.
4.  **Undo/Redo**: Route changes through the established history transaction owned by the editing service. Whole-document restoration can complete asynchronously. Widgets should reflect that service's pending/accepted/failed state rather than pushing a dictionary onto history themselves.
5.  **Modularity**: Aim to keep panels self-contained and focused on specific functionalities.

**Example Workflow for Adding a New Panel:**

1.  Create a new Python file for your panel (e.g., `my_new_panel.py`).
2.  Define a QWidget or QDockWidget subclass.
3.  In its `__init__`, accept an `OptilandConnector` instance.
4.  Build the UI for your panel.
5.  Connect to signals from `OptilandConnector` to populate/update your panel.
6.  Implement logic to handle user interactions and, if necessary, modify the `Optic` object via the connector.
7.  In `main_window.py`:

    *   Instantiate your new panel.
    *   Add it as a QDockWidget or integrate it into the UI as appropriate.
    *   Optionally, add menu actions or sidebar buttons to control its visibility or interaction.

By following these guidelines and referring to existing panels as examples, developers can effectively contribute to and extend the Optiland GUI.
