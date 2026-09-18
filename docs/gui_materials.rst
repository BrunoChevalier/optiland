.. _gui-materials:

Materials workspace
===================

The desktop Materials workspace links glass maps, an exact catalog table,
a property inspector and spectral comparisons. Open **Materials** in the sidebar, **View → Toggle
Materials**, or the **Materials** command in the command palette (``Ctrl+K``).
It is a dock: move, float, resize or hide it using the normal window controls.
You can browse without creating a lens. Selecting or inspecting a material does
not change the optical design.

For a reproducible walkthrough with sample files and expected matching results,
follow :doc:`gui_materials_tutorial`.

Catalogs and exact identity
---------------------------

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

Local RII and OSLO catalogs
---------------------------

In **Filters**, choose **Add source… → RII directory…** for a folder of supported
RII ``.yml`` material definitions, or **Add source… → OSLO GLC file…** for a supported
text ``.glc`` catalog. File selection is followed by background loading. The
catalog tree shows the local path; the inspector identifies the source format,
record and fingerprint. A GLC catalog is distinct from an OSLO lens file.

Enable the new catalog's checkbox to include it in a search. If you previously
chose **None**, newly loaded catalogs remain unchecked. Two records with the
same material name remain separately selectable, including two local sources.
Loading the same path again refreshes that source. A failed or unsupported
replacement retains its previously accepted contents and shows the diagnostic.

Select a local source or one of its catalogs in the tree and choose **Remove
source** to forget it from Materials. This removes its browser data and disables
Apply for a selected record from that source. It does not delete the file or
change materials already applied to the design. Installed and registered source
rows are not removed with this control. Local source choices belong to the
current GUI session; adding a source does not register it globally.

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
axis limits. **Apply** or ``Enter`` applies valid bounds and keeps the dialog
open. Invalid or unordered limits show a message and leave the view unchanged.
**Close** or ``Escape`` dismisses the dialog.

Choose a map in the **Material map view** list. Besides n_d versus V_d, the
available views are n_d versus n_F − n_C and the partial dispersions P_F,d,
P_g,F and P_d,C versus V_d. Each partial dispersion divides its indicated index
difference by n_F − n_C: P_F,d = (n_F − n_d)/(n_F − n_C),
P_g,F = (n_g − n_F)/(n_F − n_C), and P_d,C = (n_d − n_C)/(n_F − n_C).
Axis labels identify these quantities and their native conditions. The reference
wavelengths are F = 0.4861327 µm, d = 0.5875618 µm, C = 0.6562725 µm and
g = 0.4358343 µm. Missing reference values or a zero denominator produce an
unavailable coordinate. The catalog table remains accessible.

The patent tolerances continue to filter n_d and V_d in every map view. Their
rectangle is shown only in the n_d/V_d view, where it represents those axes.
Changing views retains exact selection and current-design context. Coordinate
refreshes and filtering preserve the viewport; Reset explicitly fits the data.

Compare a shortlist
-------------------

Select a record in **Browse** and click **Pin for comparison**, then open
**Compare**. Pin up to four exact records; equal names remain distinct. Adding
a fifth asks you to remove a pin first. Use **Move up**, **Move down** and
**Remove pin** on the current pin row. Selecting a pin or a spectral sample does
not change Browse selection or an Apply target. **Inspect in Browse** explicitly
opens that pin's exact record for inspection.

Select **Refractive index n(λ)** or **Internal transmission**. The default
**Common declared support** interval is the intersection of known support for
the chosen quantity. **Explicit interval** enables From/To in µm. The default
201 samples can be changed from 2 to 2001. Transmission also takes a thickness
in mm, including zero. Input edits request fresh results after a short pause;
**Recompute** requests them immediately. Finish incomplete numeric entries before
continuing. Displayed results and export buttons clear when inputs become stale.

The plot and **Spectral samples** table use the same wavelengths and values.
Curves have numbered labels, different line styles and marker shapes in both
themes. Missing, unsupported or invalid samples remain explicit table cells and
gaps in the curve. Select rows and press ``Ctrl+C`` to copy their displayed
samples. Plot arrows pan, ``+``/``-`` zoom, ``Home`` restores the initial view and
``Escape`` exits pan/rectangle-zoom mode. Drag the splitter to enlarge either the
plot or data tabs. **Expand data** temporarily gives the data tabs the plot's
space; **Show plot** restores the split view. This is useful for reading sample
rows in a compact dock. Compact controls remain reachable with their scroll bars.

Known support is enforced even when a native material's usual bounds policy is
``clamp``. Unknown index support is excluded from the default interval; an
explicit bounded interval allows evaluation with a warning. If known intervals
do not overlap, no curve is invented: inspect the pins and choose an appropriate
explicit interval. Individual unsupported wavelengths still remain gaps.

Internal transmission excludes interface reflection and scattering losses. It
requires actual extinction or internal-transmission data and uses their separate
support interval. Missing extinction remains missing even at zero thickness;
it does not become 100% transmission. Supplied zero and unity transmission are
preserved. Read thickness, support and native conditions together before comparing.
The workspace does not normalize temperature, pressure or index conventions.

**Details** shows the current pin's model, coefficients, metadata and native
conditions as bounded plain text. **Nearby alternatives** searches across loaded
sources using the current pin and positive n_d/V_d scales. Its signed deltas and
normalized distance describe geometric proximity, not optical performance.
**Inspect alternative in Browse** is an explicit selection action. Nearby results
clear when the target, scales or their source data change.

Removing a source leaves its pins visibly unavailable with their saved labels.
It discards their optical results; it never substitutes a same-name record.
Reload the exact source to resolve those identities again.

Save views and export results
-----------------------------

In Browse, enter a name beside **Save view** to save the map choice, filters,
exact selected catalogs, ordered pin identities, map bounds and current-design
context visibility. **Load view** restores that view; **Delete view** removes
the saved entry. Saving an existing name replaces that entry. These are local
application preferences. They do not store
catalog files or spectral results. Comparison quantity/interval/thickness are
chosen in Compare. Unavailable saved catalogs and pins stay explicit; load
their exact local sources at the same paths again rather than broadening the
search accidentally. Changed source contents can make an old record unavailable.

Browse's **Export CSV** captures the current result rows in displayed sort order,
including identities, coordinates, statuses, match deltas, support, native
conditions and source generations. The map toolbar's **Export PNG** captures the
visible plot. Compare provides **Export samples CSV…** and **Export plot PNG…**.
Its CSV contains one row per pin and wavelength, preserving every gap and its
reason; values match the plot/table samples. CSV uses locale-independent numeric
values. PNG embeds an ``Optiland`` metadata entry with the captured request,
sources, conditions and plot bounds.

An export freezes the accepted result before opening the filename chooser.
Changing filters while choosing a destination does not change that captured
artifact. Confirm the destination and wait for the completion message. A failed
or cancelled export does not change the lens, its native filename, modified
state or Undo history. Export buttons require current results. Use the normal
**File → Save** or **Save As** commands to save a lens document.

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

Apply a material to the design
------------------------------

1. In the **Lens Data Editor**, right-click the intended **Material** cell and
   choose **Browse Materials…**, or focus that cell and press ``Ctrl+M``. This
   captures the exact surface and opens Materials. The footer names its target:
   the medium after that surface. Row 0 targets the object-space medium.
2. Select an exact material in the map or table and read its inspector. Moving
   to another editor row or inspecting another material does not change the
   captured target. Check the footer's target before continuing.
3. Click **Apply material**. The shared calculation worker reconstructs the
   selected definition, checks configured wavelengths, and updates supported
   solves and pickups on a prepared copy. **Cancel Apply** cancels preparation.
4. Read the outcome below the target. Success publishes one document change and
   one undo entry. **Edit → Undo** and **Edit → Redo** restore the corresponding
   complete document asynchronously. A failed or cancelled preparation leaves
   the design and history unchanged.

The last image surface has no outgoing editable medium. Reflective surfaces,
material-controlled pickups and unsupported custom dependency/updater paths
show a reason instead of accepting a material assignment. Supported built-in
radius, conic and thickness pickups and supported optical solves are evaluated
on the prepared candidate; their resulting geometry can change with the glass.
The displayed match distance does not predict those optical effects.

Every configured wavelength must lie within any known n/k support, even for a
material whose normal bounds policy is ``clamp``. Unknown support is evaluated
at those wavelengths and reported. Missing extinction data retain the native
zero-k fallback with an explicit warning; this is not a transmission measurement.
Passing these checks does not establish that the new glass meets your optical
performance requirements: inspect the updated lens and rerun relevant analyses.

If the design changes after target capture, **Refresh target** explicitly renews
the retained surface against the current design. It does not choose the row
currently focused in the editor. If that surface was deleted or the document
was replaced, capture the intended Material cell again. A supported mapped
surface move retains the target but requires an explicit refresh. Undo/Redo
clears the target: capture the intended Material cell again afterward. A
successful material Apply renews its own target against the accepted document.
Existing Shape drafts require their own **Reload Shape** after a replacement;
review that draft before discarding its text.

Ordinary editing remains available during preparation and invalidates its
candidate. Replacing or removing its accepted source, changing the backend, or closing
the document also prevents stale acceptance. Finish or cancel Apply before
starting another file operation, Undo/Redo or surface placement. Resolve a
pending file-load conflict and finish other document operations or optimization
before starting Apply. The footer displays the current reason when unavailable.

An applied catalog definition is owned by the lens. **File → Save As** stores
the data in the native Optiland JSON document; reopening it does not depend on
the original local catalog file. The native path stays unchanged until you save
or choose Save As, and a successful Apply marks the document modified. Choosing
an already identical owned definition produces an unchanged outcome without a
new undo entry. Converting an equivalent file lookup into an owned definition
is a real persistence change.

GlassExpert: search, review and accept a complete design
--------------------------------------------------------

The **GlassExpert** tab combines discrete exact-material choices with the
continuous variables and operands configured in the existing Optimization
editor. **Edit objective and continuous variables** opens that editor. Follow
:doc:`gui_glass_expert_tutorial` for a complete synthetic example.

In **Setup**, add each intended medium after a surface and explicitly set its
pool from the inspected **Selection**, comparison **Pins** or Browse **Current
results**. Eligible and excluded records retain their qualified source/catalog/
record identifiers and reasons. The starting material is a separate reference;
it is not automatically included in a pool. One candidate fixes a target, while
at least one pool must contain more than one candidate. Pool creation runs in
the shared worker and does not silently truncate the chosen entries.

Set the global sample count, local neighbours, iteration limit, tolerance, seed
and positive ``n_d``/``V_d`` distance scales. The sample count must fit the
smallest nontrivial pool. These scales describe sampling geometry, independently
of optical merit weights. The seeded farthest-point search is bounded and does
not guarantee a global optimum.

**Prepare current design** freezes the baseline, exact owned definitions,
objective, bounds, source generations and backend. Review that summary before
**Run frozen setup**. Preparation and Run leave the current document unchanged;
**Stop** cancels the pending work. Setup controls and later Browse filtering do
not silently replace the inputs of a retained frozen result.

**Review** separates sum-squared merit, improvement, convergence and candidate
eligibility. Its Changes, Operands, Trials and Warnings pages use the worker's
frozen values. Every continuous value and exact material assignment belongs to
the same candidate. Operand bounds remain objective terms; their displayed
within/outside state is not an additional hard-constraint guarantee. A complete
valid candidate can be explicitly accepted without improvement or convergence;
failed, cancelled, incomplete or stale candidates cannot be applied.

**Apply complete candidate** is the only in-place acceptance action. Freshness
and conflict checks run again before one whole-document transaction and one Undo
entry. It preserves the lens path and marks a changed prescription modified.
An exactly unchanged native prescription does not create an Undo entry. Editing
the lens or objective, replacing a source, changing the backend or superseding
the run can revoke acceptance while retaining supported inspection/export.

**Open candidate separately** validates the portable complete prescription in
the worker and opens a new, modified, untitled lens window for ordinary optical
analysis. The original document remains unchanged. This action is unavailable
when the native components cannot be exported without losing behavior.

Choose **Save run setup**, **Export script** or **Export native candidate** in
the output control and click **Export…**. A run setup contains bounded embedded
data and provenance. The script uses public core replay APIs and is not executed
by export. Loading a setup is detached inspection/replay: files never carry live
document authority. Missing or changed sources stay explicit, and same-name
records are never substituted. Prepare the current design explicitly to acquire
fresh authority. Output files leave the open lens path, dirty flag and history
unchanged; ordinary File Save stores a lens after acceptance.

Keyboard, focus and compact docks
---------------------------------

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
* **Apply unavailable:** capture a Material cell, select an available exact
  record, and read the target/status footer. Refresh a stale retained target
  explicitly; select a new cell if its surface or document was replaced.
* **Apply failed at a wavelength:** compare the configured wavelengths with
  both n and k support. Changing a browsing filter does not change the design's
  wavelengths or extend measured support.
* **Apply cancelled after an edit or reload:** inspect the current design and
  source, refresh the intended target, and submit a new explicit Apply.

See :doc:`gui_quickstart` for installation, launch, dock layout and other tools.
