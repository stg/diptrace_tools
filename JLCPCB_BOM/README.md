# JLCPCB BOM Editor

Create, maintain and check JLCPCB assembly files from a DipTrace design. Keep schematic data, editable JSON and JLCPCB part selections in sync, then bring primary name/value edits back into DipTrace.

## The workflow

1. **Export from DipTrace:** an XML schematic (`.dchxml`) and a placement/CPL CSV.
2. **Open the schematic or an existing BOM project JSON** here. Opening a schematic loads its matching JSON, or creates one for your edits, part numbers, alternatives and notes. Opening a JSON loads the schematic from its `source` path, relative to the JSON's folder unless absolute.
3. **Edit and generate** the assembly BOM and CPL workbooks.
4. **Upload to JLCPCB**, review the selected parts and download its selection workbook.
5. **Read back the selections**, check them with AI, make corrections and generate again.
6. **Back-annotate** primary Name/Value edits, then **re-import the updated XML into DipTrace**.

## Prepare the input files

For `Board.dchxml`, the default placement filename is `Board_CPL.csv`, beside the schematic.

Multipart symbols are counted once using their main unit, including exports with separate styles for different units. Letter-only PCB designators such as `BOOT` remain visible in the editor and project JSON; DNM/DNP exclusion rules can omit them from assembly output.

Export these placement columns from DipTrace; their order may vary:

```text
RefDes;Origin X (mm);Origin Y (mm);Side;Rotation
```

| CSV field | Accepted forms |
| --- | --- |
| Designator | `RefDes` or `Designator` |
| Coordinates | `Origin X/Y`, `Mid X/Y` or `X/Y`; **each header must include its unit**. |
| Units | `(mm)`, `(in)`, `(inch)`, `(inches)`, `(mil)` or `(mils)` |
| Side | `Side` or `Layer` |
| Rotation | Degrees |

Comma, semicolon and tab separators are accepted; extra columns are allowed. Coordinates are converted to mm before placement corrections. Output uses three decimal places by default; project setting `cpl_decimals` can change this. The source CSV is never rewritten.

## Edit the project

The Open dialog and command line accept `.dchxml` and `.json`. A JSON can have a different name or folder from its schematic; Reload and Recent Files keep that chosen JSON. If its referenced schematic has moved, select the new location when prompted, then save JSON to remember the link. Cancelling leaves the current project unchanged. Output-folder and CPL paths retain their existing meaning relative to the schematic.

| Grid area | Contents |
| --- | --- |
| **Input** | Data from the exported schematic. |
| **JSON** | Your editable names, values, footprints, JLCPCB numbers and notes. |
| **Output** | The selected, normalized assembly data. |

- **Save JSON** keeps your edits. **Alternatives** retain other part choices while you select one for assembly.
- **Notes** are searchable and included in AI checking. They do not enter the assembly files or schematic back-annotation.
- Adjacent rows that generate one BOM line share the three Output cells. Sorting/filtering updates this grouping; JSON fields and notes stay separate.
- **Reload — Ctrl+R** rereads the schematic and JSON, asking before discarding unsaved edits.

### Combined alternatives

Use **Edit alternates → Add combined alternate** to replace several designators with one assembly component. Enter the member references in pin order, the replacement part, and an output/anchor reference from the members. The shared choice appears in every member's existing output selector. Editing or removing it from any member affects the same shared definition.

Preview the placement before applying. X/Y offsets are in mm along the anchor's CPL axes, rotated by its source rotation (the same convention on either PCB side); additional rotation affects the replacement only. The preview lists placement origins, not pads: verify the replacement's physical footprint and pin order. Generation checks the saved relative member positions (0.05 mm position tolerance, 0.01° rotation tolerance), PCB side and missing/duplicate placements before changing output files. A rigid move or rotation of the whole group remains valid.

Selecting a combined alternative warns which components will change and deactivates overlapping choices. Individual primary/alternate selections remain stored. Choosing an ordinary component on any member deactivates the combined choice and restores the other members; removing the shared alternative does the same. These operations support undo/redo. Group members remain separate grid rows so their selectors are always accessible.

Generation emits only the anchor designator in BOM/CPL, using the replacement part and adjusted origin. Stock checks and AI checks use this same assembly choice. Schematic back-annotation still uses individual primary fields. Mating plugs are independent and are not combined automatically. Definitions and active choices are saved in the JSON's `assembly_alternatives` list; each has `id`, `references`, `anchor`, `component`, `placement`, `layout` and `active` fields.

Projects with combined alternatives use JSON schema version 2 so older editors reject them instead of silently generating individual parts. Existing version 1 projects remain supported. The generation log lists `CPL footprints merged: A,B => A; C,D,E => C` separately from ordinary footprint corrections and exclusions.

Changing the anchor preserves the replacement's physical position and orientation. A changed source placement invalidates a pending preview and requires review again. Removing missing designators also offers to remove their shared alternatives, restoring the remaining members. Imports use workbook reference coverage: all members present means individual assignments; only the combined output reference present means the shared combined choice, regardless of which choice is active. Rows without selected part numbers still count as present. Partial coverage or multiple possible combined targets prompts before importing as individuals or skipping. A different combined part number can update that shared choice or become another combined alternative. Importing does not switch the active assembly choice.

Generation prepares both workbooks before publishing BOM, CPL and saved JSON together. A reported publication failure restores already-replaced files; close any open workbook and retry.

### Filter the grid

Press **Ctrl+F** to focus the filter. All words must match, ignoring case.

| Wildcard | Meaning |
| --- | --- |
| `*` | Any text |
| `#` | One or more digits; for example, `R#` finds numbered resistors. |
| `?` | One character |

Use `\*`, `\#` or `\?` for literal symbols. **Ctrl+C** copies selected rows' displayed columns.

## Generate and read back JLCPCB selections

**Generate outputs** saves JSON and writes the BOM/CPL workbooks, normally into `order`. It applies value/footprint normalization and your selected alternatives.

**Exclusion rules remove parts from both BOM and CPL.** Review the generated files, upload them to JLCPCB and check its selected parts.

To bring selections back, choose **Tools → JLCPCB part numbers from XLS(X)**. Both download styles are supported:

- The simple **JLC BOM** format.
- **BOM Matching / Assembly Order** listings, with `.xls` or `.xlsx` filenames.

Numbers are matched by designator. For a conflict with a saved primary number, choose **replace**, **add an alternative**, or **skip**. Review the resulting choices and generate again.

## Stock check

**Stock check** is available from the main window and the Tools menu. It shows the current output components, footprints and part numbers alongside JLCPCB stock, available JLCPCB idle parts and LCSC stock. Excluded parts are omitted and selected alternates are used. Idle stock is the sum of the available resale listings.

Enter the number of boards in **Order quantity**. Each line shows its count per board and required quantity, plus the applicable JLCPCB and LCSC unit and line prices in USD. The entire price-break data is kept in memory and prices recalculate as the quantity changes. For LCSC, when the required quantity is below the minimum quoted quantity, the first price is shown and the line price uses that minimum, labeled **min** and highlighted in yellow. These are component prices and do not include assembly fees, attrition, shipping or tax.

JLCPCB and LCSC stock is red when it is below the quantity required and yellow when it is below twice the quantity required. When multiple output lines use the same part number, the comparison uses their combined requirement. Idle stock is a fallback and is green when it covers the requirement.

The dialog checks unknown values automatically and keeps results in memory while the editor is open, including across projects. Values update as they arrive. **Refresh** checks every displayed part again, replacing each value as its new result arrives; **Abort** or closing the dialog stops the check and keeps the values already shown. Unknown values are blank, missing values show **No data** in yellow, and failures show **Fetch failed** in red. Failed lookups are retried when the dialog is reopened. Supplier website interfaces may change, and stock can change at any time.

## Check with AI

**Tools → Check with AI prepares instructions and comparison data for an AI assistant.** It does not run the AI comparison itself.

1. Run the check on the current project. Unsaved edits, selected alternatives and notes are included; no workbook or placement CSV is needed.
2. The tool collects supplier product details and opens a text report, normally `order/Board_AI_Check.txt`.
3. Copy or attach that report to an AI assistant to identify mismatches.
4. Review the findings, edit parts/alternatives or notes, and generate your final assembly files.

The report follows output normalization and exclusions. Unassigned JLCPCB numbers are flagged for review. A note follows its part line as `// note text`; no comment marker is added for an empty note.

RoHS is checked separately by the tool:

| Log color | Meaning |
| --- | --- |
| **Red** | An error, or a confirmed non-RoHS part. |
| **Green** | Every checked part has confirmed compliant RoHS data. |
| **Dark yellow** | Missing/unknown RoHS data, listed separately only when present. Missing CPL data is also yellow. |

Use **Tools → Open AI check settings** to change the instructions and accepted substitutions. Save that file before the next check.

## Bring edits back into DipTrace

**Back-annotate to schematic** updates primary **Name** and **Value** in the `.dchxml` and makes a backup. Footprints and JLCPCB numbers are not back-annotated.

**You must re-import the updated XML schematic into DipTrace and save the design there.** Back-annotation alone does not update the binary `.dch` schematic.

After further edits in DipTrace, export a fresh XML schematic and **Reload** here to compare it with your saved JSON.

## Filenames and defaults

| Default file | Purpose |
| --- | --- |
| `Board.dchxml` | Schematic exported from DipTrace. |
| `Board.json` | Editor project, beside the schematic. |
| `Board_CPL.csv` | Source placement CSV, beside the schematic. |
| `order/Board_BOM.xls` | Generated assembly BOM. |
| `order/Board_CPL.xls` | Generated assembly placement workbook. |
| `order/Board_AI_Check.txt` | AI instructions and comparison data. |
| `order/Board_AI_Check.rohs.txt` | Separate RoHS report. |

- **Tools → Open project settings JSON** changes this project's output folder, source CPL filename and generated filenames. The source CPL stays beside the schematic.
- **Tools → Open new-project defaults** changes the templates for future projects. Existing projects keep their settings.
- Generate replaces generated workbooks; the source CPL remains unchanged.

## Customize normalization

Use **Tools → Open normalization rules** to change output names, values, footprints and assembly exclusions. Save the file and generate again; editable JSON fields and the schematic stay unchanged.

See the [shipped defaults](../README.md#normalization) and **Help → Normalization rule reference** for the language and examples.

## Start the program

| System | Launcher |
| --- | --- |
| Windows | `Launch JLCPCB BOM Editor.cmd` |
| macOS | `Launch JLCPCB BOM Editor.command` |
| Linux | `sh "Launch JLCPCB BOM Editor.sh"` |

Install dependencies using the [repository setup instructions](../README.md).
