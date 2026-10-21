# JLCPCB BOM Editor

Create, maintain and check JLCPCB assembly files from a DipTrace design. Keep schematic data, editable JSON and JLCPCB part selections in sync, then bring primary name/value edits back into DipTrace.

## The workflow

1. **Export from DipTrace:** an XML schematic (`.dchxml`) and a placement/CPL CSV.
2. **Open the schematic** here. The editor creates a matching JSON file for your edits, part numbers, alternatives and notes.
3. **Edit and generate** the assembly BOM and CPL workbooks.
4. **Upload to JLCPCB**, review the selected parts and download its selection workbook.
5. **Read back the selections**, check them with AI, make corrections and generate again.
6. **Back-annotate** primary Name/Value edits, then **re-import the updated XML into DipTrace**.

## Prepare the input files

For `Board.dchxml`, the default placement filename is `Board_CPL.csv`, beside the schematic.

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

| Grid area | Contents |
| --- | --- |
| **Input** | Data from the exported schematic. |
| **JSON** | Your editable names, values, footprints, JLCPCB numbers and notes. |
| **Output** | The selected, normalized assembly data. |

- **Save JSON** keeps your edits. **Alternatives** retain other part choices while you select one for assembly.
- **Notes** are searchable and included in AI checking. They do not enter the assembly files or schematic back-annotation.
- Adjacent rows that generate one BOM line share the three Output cells. Sorting/filtering updates this grouping; JSON fields and notes stay separate.
- **Reload — Ctrl+R** rereads the schematic and JSON, asking before discarding unsaved edits.

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
