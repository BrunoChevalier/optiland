.. _gui-materials:

Materials workspace
===================

The desktop Materials workspace links a glass map, an exact catalog table and a
read-only property inspector. Open **Materials** in the sidebar, **View → Toggle
Materials**, or the **Materials** command in the command palette (``Ctrl+K``).
It is a dock: move, float, resize or hide it using the normal window controls.
You can browse without creating a lens. Selecting or inspecting a material does
not change the optical design.

Catalogs and exact identity
--------------------------

The first visit loads the installed glass catalogs in the calculation worker.
Select catalogs independently in the **Filters** pane. **All** selects the loaded
catalogs; **None** gives an empty result, rather than silently selecting every
catalog. All/None also sets the initial selection for catalogs that finish loading
later. Search matches material names and exact record identifiers; use the
catalog tree to choose a catalog. Source, catalog and record together identify a
material. Equal display names from two sources remain separate entries.

**Reload sources** stages replacement data before publishing it. A failed reload
retains the last accepted data and marks that source as failed in the catalog
tree and inspector. A first-load failure shows a diagnostic source row with no
data. **Stop loading** cancels Materials work without stopping another tool's
calculation. Source files are never changed by browsing.

**Show current-design context** overlays the current design's media with diamond
markers. They are context, not additional matches. A document edit refreshes this
context without cancelling an independent catalog query. The inspector identifies
the medium after each surface, following :doc:`conventions`.

Map, table and inspector
------------------------

The default map plots computed ``n_d`` vertically and ``V_d`` horizontally, with
the Abbe axis descending from left to right. Click a map point or select a table
row to inspect its exact record. Coincident or nearby points open a chooser of
qualified identities. Hover is temporary; it never replaces the selection.
Current-design diamonds and the selected outline remain distinguishable without
depending only on color.

Click table headers to sort, including numerical coordinates and match deltas.
Unavailable values stay last in either direction; equal values use exact identity
as a stable tie-breaker. Sorting preserves the selected material and keyboard row.
Records without valid map coordinates stay in the table with an explicit
missing/unsupported/invalid status. Missing values are never replaced with zero.
The inspector shows computed and separately attributed declared values, native
conditions, independent index/extinction support, measured-data availability,
source fingerprint, metadata and reference information.
Metadata previews are bounded: long text is truncated explicitly, and definitions
larger than 256 KiB keep their complete optical data without decoding a metadata
preview on the interface thread.

Filtering preserves the selected identity and the map viewport. If the selection
is outside the filters, the summary says so. **Locate** moves the view to that
selection without changing zoom or filters. **Reveal selected** explicitly clears
name, patent and wavelength restrictions and enables its catalog. A removed
source leaves the last inspected information visibly unavailable.

Use the plot toolbar for pan, rectangle zoom, reset and Back/Forward history.
Mouse-wheel zoom is centered on the pointer. The **Bounds** control sets numeric
axis limits. Invalid or unordered limits leave the current view unchanged.

Patent matching and supported wavelengths
-----------------------------------------

Enable **Patent match** and enter a target ``n_d`` and ``V_d``, each with its own
positive tolerance. **Find matches** applies both limits independently. The
rectangle on the map shows those tolerances. Signed ``Δn_d`` and ``ΔV_d`` and a
tolerance-scaled match distance appear in the table. Enabling patent matching
sorts by this distance; you can then choose another table sort. Distance describes
coordinate proximity, not equivalent lens performance.

Enable **Wavelength support** to require a wavelength interval in micrometers.
Choose explicitly whether to include unknown support. An incomplete numeric
entry or invalid interval preserves the previous result and explains the error;
it never silently widens a search. Pending, revoked or failed queries identify
previous rows as retained information that needs refresh.

Optical conventions and missing data
------------------------------------

The reference lines are d = 0.5875618 µm, F = 0.4861327 µm and C = 0.6562725 µm.
The displayed Abbe number uses those computed indices. Valid negative dispersion
is retained; undefined dispersion is shown explicitly. Declared catalog ``n_d``
and ``V_d`` are separate source claims and do not override computed matching
coordinates.

Values use each record's native conditions. They are **not normalized between
sources**. Compare the displayed index convention (air-relative or absolute),
wavelength convention (air or vacuum), temperature and pressure before drawing
conclusions from small differences. Unknown conditions stay unknown.

Index and extinction support can differ. ``raise`` or ``clamp`` is an evaluation
policy, not evidence that measurements cover an interval. Missing extinction or
transmission measurements do not mean measured 100% transmission. For model and
persistence details, see :doc:`developers_guide/material_discovery` and
:doc:`developers_guide/materials`.

Keyboard, focus and compact docks
--------------------------------

``Ctrl+F`` focuses and selects the search text. ``Tab`` and ``Shift+Tab`` move
through native controls; ``F6`` and ``Shift+F6`` move between visible search,
catalog, plot, table and inspector regions. With the table focused, ``Ctrl+C``
copies the selected row including its full source and record identifiers.

With the plot focused, arrow keys pan, ``+``/``-`` zoom and ``Home`` resets the
view. ``Escape`` closes a point chooser or leaves a navigation mode. Table
navigation provides a non-pointer route to every material, including coincident
points. At narrower sizes, **Filters** opens the filter region and **Details**
switches between results and inspection. Hiding a focused region returns focus
to its controlling button. Both application themes are supported.

Troubleshooting
---------------

* **No matches:** check that a catalog is selected, then inspect name, independent
  patent tolerances and wavelength restrictions. The tool does not relax them.
* **A selected glass disappears:** its selection is retained. Use Locate or
  Reveal selected depending on whether you want to change the view or filters.
* **Catalog load failed:** inspect the persistent source diagnostic. Correct the
  source or environment and use Reload sources; accepted data remains available.
* **Results need refresh:** the source or backend that owned the result changed.
  Find matches submits fresh criteria; previous rows remain inspectable.
* **No map coordinate:** read the table/inspector status. This can be a supported
  material with undefined Abbe dispersion, not a missing catalog entry.

See :doc:`gui_quickstart` for installation, launch, dock layout and other tools.
