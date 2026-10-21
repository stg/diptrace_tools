# Part Database

Find parts used in earlier designs, see which projects used them, and record where you keep them.

The tool scans a base folder for **binary DipTrace schematics** (`.dch`). Its database holds each part's **Name, Value and Pattern**, with the exact **filename and RefDes** of every use. Schematics are never changed; XML exports are not needed.

## Get started

1. Open **Tools → Settings** and select the base folder to scan.
2. Choose the fields to search: **Name**, **Value**, **Pattern** and/or **Filename**. Save.
3. Click **Refresh database** — **F5**. The first refresh reads every schematic; later refreshes read only new or modified files and remove deleted files.
4. Type a search, then expand a result to see its filenames and RefDes.
5. Double-click a filename to open the schematic in its usual application.

The last refresh time appears above the search box. Progress and errors appear during refresh. Results start collapsed by default; change this in Settings.

## Search for a part

All search words must match the checked fields. Searches ignore letter case; RefDes is displayed but is not searched.

| Wildcard | Meaning | Example |
| --- | --- | --- |
| `*` | Any text | `USB*` |
| `#` | One or more digits | `Board_R#.dch` |
| `?` | One character | `R?02` |

For literal wildcard symbols, use `\*`, `\#` or `\?`.

- **Ctrl+F** focuses the filter.
- **Ctrl+C** copies the selected rows' displayed columns.
- **Edit** contains commands to expand or collapse results.

## Record where a part is kept

Double-click a part's **Location** cell and enter a label, such as `Cabinet 2, drawer 7`.

- A location belongs to the **Name / Value / Pattern** combination and is shared across all its schematic uses.
- Locations save automatically in `part_locations.json`, separately from the scan database.
- Refresh preserves locations. Clear a cell to remove its label.

Use **Tools → Open part locations** for external edits, then **Reload** to read them.

## Refresh versus Reload

| Action | Use it when? |
| --- | --- |
| **Refresh database** — **F5** | Schematics have been added, changed or deleted. Only changed files are read. |
| **File → Reload** — **Ctrl+R** | You want to reread the saved database and locations. It does not scan schematics. |
| **Tools → Rebuild database** | You need every schematic read again, even if its modification date is unchanged. |

Settings, the database and locations are saved beside the program. Use **Tools → Settings** to change the base folder, searchable fields or default expansion.

## Customize normalization

Normalization makes equivalent part names, values and footprints easier to find.

1. Choose **Tools → Open normalization rules**.
2. Edit and save the rules file.
3. **Refresh database** to apply it. Source schematics stay unchanged.

See the [shipped defaults](../README.md#normalization) and **Help → Normalization rule reference** for the rule language and examples.

## Start the program

| System | Launcher |
| --- | --- |
| Windows | `Launch Part Database.cmd` |
| macOS | `Launch Part Database.command` |
| Linux | `sh "Launch Part Database.sh"` |

Install dependencies using the [repository setup instructions](../README.md).
