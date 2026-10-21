# PCB Layout

Inspect compact placement with your own body sizes and minimum gap. Edit one group in DipTrace, then use this tool to create or update repeated groups, including their routing and vias.

## Get started

1. Export your PCB from DipTrace as **PCB XML** (`.dipxml`).
2. **Open PCB / recipe…** and choose that XML. Choose a JSON filename for the layout recipe, or cancel to keep an unnamed session.
3. Add a group, for example `U1, C1`. Use more groups to inspect several sets of parts together.
4. For placement inspection, use **Fit**, show **Pads** if useful, and set **Minimum error gap**.
5. To repeat a design, open **Replication**, select an action and arrangement, then choose **Preview**.

Reopen a recipe JSON to continue a saved study. Several recipes can use the same PCB with different groups and replication settings.

## Inspect the placement

| Control | Purpose |
| --- | --- |
| Wheel / drag / **Fit** | Zoom, pan, or frame the selected groups. |
| Click / right-click a part | Inspect it, or define its body size. |
| **Show groups** | Focus on one group or show them all. Clearance checks still cover all defined groups. |
| **Show: Labels, Pads, Tracks, Vias** | Choose what to draw. Copper starts hidden. |
| **Mirror view** | Mirror the view horizontally. PCB coordinates stay unchanged. |
| **Minimum error gap** | Highlight same-side bodies whose gap is smaller than this value; the initial default is **0.2 mm**. |

**Red bodies mean a clearance problem.** This check covers physical bodies only, excluding pads and leads. It is a placement aid, not a complete manufacturing or electrical check.

**Inspect layer** emphasizes one copper layer. Other layers are composited at one-third opacity.

- Press **1–9** to inspect an available layer.
- **Page Up / Page Down** move between layers without wrapping.
- These keys leave text entry alone. Layer inspection affects viewing only.

## Define body sizes

Right-click a part and choose **Define body size**.

- Enter width, height and optional body rotation in mm/degrees.
- Match by **Pattern**, **Name**, or **both**. The priority is **both → Name → Pattern**; the latest entry wins a tie.
- Sizes are shared between recipes. A specific part can override a general footprint size.
- X/Y offsets start at the footprint origin and follow the part's mirroring and rotation. Zero offsets center the body on that origin.
- Remove a size entry to reveal the next matching entry or the drawing fallback.

Without a matching size, the tool uses the **component outline**, then the **silkscreen**. Clearance for these drawings uses their convex envelope and is an estimate. A marker indicates missing body geometry, so that part cannot be checked.

Body rotation corrections affect inspection only. They do not rotate the actual part in the exported PCB.

## Set up replication

1. Open the **Replication** fold-out, or use **Tools → Replication settings**.
2. Select the **Source group**, **Action** and **Arrangement**.
3. Set **What to replicate**, then choose **Preview** above the canvas.

| Action | What happens |
| --- | --- |
| **Create copies** | Add new parts with unused RefDes/IDs and fresh internal nets named `Net N`. Set the total instances, including the source; a grid uses columns × rows. |
| **Arrange existing groups** | Update the parts already listed in your groups. Put corresponding parts in the same list position. Destination identities and net assignments are preserved. |

With no groups, Create uses the whole PCB. New copies include nets connecting at least two selected pads; connections to outside parts are omitted, and lone external pads become unconnected.

| Arrangement | Settings |
| --- | --- |
| **Linear** | X/Y step per instance. |
| **Grid** | Columns and rows, plus separate X/Y step vectors for each direction. |
| **Rotational** | Center and angle step, or equal spacing around 360°. |

Group list order defines the arrangement positions. In Arrange mode, the source stays anchored at its current position.

Grid's **Fill order** chooses how the group list fills the cells:

- **Row first:** fill across columns, then start the next row.
- **Column first:** fill through rows, then start the next column.

### Rotate alternate rows or columns

In Grid, enable **Rotate odd**, then choose **Columns** or **Rows**.

- The first row/column is unchanged, the second turns **180°**, and the pattern repeats.
- A row/column turns **as a whole**: both group order and part orientation reverse.
- Row/column numbering follows the logical grid, independent of Mirror view.

The pivot is the center of the source group's part positions. Positions use the corrected footprint origin; technical XML offsets are handled internally.

## Choose routing to replicate

**Placement is always copied.** Under **What to replicate**, select **Tracks**, **Vias** and the allowed layers. These choices are separate from the toolbar's viewing checkboxes.

- Internal routing follows paths between pads in the group, including intervening vias. External branches and dangling loops are excluded.
- Connections are identified by the XML's **net, pad and trace references**, not by whether drawn lines touch a pad.
- Tracks through vias require **Vias**. Vias alone can be copied as standalone static vias.
- Arrange also repeats interlinks touching the source group when the destination pads belong to the matching existing net.
- When spacing changes, interlink bends are copied and attached endpoints move to their destination pads. End segments can change shape; the tool does not route around obstacles.

**Source** and **All** show current PCB content. All includes connections between displayed groups. **Preview** shows the proposed placement and the routing selected for copying; it is disabled when Replication is folded away.

### How grid interlinks repeat

An interlink keeps the same logical row/column offset between its groups.
The rule follows the selected Fill order, not consecutive group numbers.

| Template connection | Copies stop when? |
| --- | --- |
| **Horizontal** — across columns | The next endpoint would pass the end of a row. |
| **Vertical** — across rows | The next endpoint would pass the end of a column. |
| **Diagonal** — across both | Either endpoint would fall outside the grid. |

Group numbering never makes a link wrap into the next row or column. A full circular arrangement can wrap; a linear arrangement cannot. Custom routing around grid edges stays your responsibility.

### Preserve skipped connections

A **disjoint** is a connection with no valid equivalent at the destination: a required group, part, pad or matching net is absent. Such a copy is skipped.

| Preserve skipped connections | Existing inter-group routing |
| --- | --- |
| **On** — the default | Keep connections that replication does not replace, including special edge links and unmatched/disjoint connections. |
| **Off** | Remove selected inter-group routing that replication does not reproduce. Pad/net assignments stay unchanged. |

This option applies in **Arrange** mode on the selected routing layers. Junctions into a group's internal copper are retained rather than replaced independently.

Preview reports skipped copies. Inspect preserved edge links after moving groups: retaining a route does not reshape it for the new placement.

## Save, reload or change the input

| Action | Result |
| --- | --- |
| **Save recipe** — **Ctrl+S** | Save groups, view choices and replication settings as JSON. |
| **Save recipe as…** — **Ctrl+Shift+S** | Choose another recipe JSON filename. |
| **Replicated: Export** — **Ctrl+E** | Calculate the current proposal and write PCB XML. Ask for a filename the first time, then reuse it. |
| **Replicated: Export as…** — **Ctrl+Shift+E** | Choose another result filename. The current input, groups and mode stay loaded. |
| **Relink PCB…** — beside the input filename or in File | Choose another XML input for this recipe. Keep its groups/settings; every listed RefDes must exist in the new PCB. |
| **Reload PCB** — **Ctrl+R**, or the reload icon beside the input filename | Reread the current PCB XML. Keep your groups, unsaved replication settings and view; update Preview if selected. |
| **Revert recipe…** — beside Reload PCB, or in File | Reread the saved recipe JSON and linked PCB. Confirm before discarding unsaved recipe edits. |

The export commands are also in **File → Export PCB / Export PCB as…**. **Save writes recipe JSON; Export writes PCB XML.** Neither command performs the other operation. The output filename is remembered in the working session; save the recipe to keep it in that JSON too.

**Exporting to another PCB filename writes a result only.** It does not reload that result, relink the recipe or calculate another layout after exporting. Preview is optional before export.

**Exporting over the input** creates a timestamped backup and refreshes the board. In Create mode, it also asks whether to fill in the generated groups and switch to Arrange. Choose Yes for later exports to update those copies.

After editing/exporting in DipTrace, use **Reload PCB** to bring in the new XML without losing your current setup. To discard recipe edits or reread externally edited recipe JSON, use **Revert recipe…**. Reverting does not undo exported PCB files.

If the input changed outside the program, reload the PCB before overwriting it. Re-import exported XML into DipTrace and run its checks. Board outlines, pours and board graphics are retained but are not repeated.

## Sessions and settings

- **Closing the app** saves the working session, including unnamed studies and unsaved edits. The next launch restores it.
- **Switching recipes** still asks about unsaved recipe edits. Explicit Save writes the recipe JSON.
- View choices belong to the recipe, including the Replication fold-out, group/layer selection and camera. They are kept on closing; the session also preserves unfinished work.
- Scrolling over input controls scrolls the surrounding pane without changing their values. The canvas wheel still zooms.
- `session.json` and program settings live beside the program. Window position and recent files are program-wide.
- **Tools** opens the recipe, program settings, central `component_sizes.json` and new-recipe defaults. New defaults apply to newly created recipes.

## Start the program

| System | Launcher |
| --- | --- |
| Windows | `Launch PCB Layout.cmd` |
| macOS | `Launch PCB Layout.command` |
| Linux | `sh "Launch PCB Layout.sh"` |

Install dependencies using the [repository setup instructions](../README.md).
