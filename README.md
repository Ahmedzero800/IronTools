# IronTools

pyRevit extension for MEP work in Revit (tested on pyRevit 7 / Revit 2023-2025).

![IronTools ribbon in Revit](docs/images/ribbon.png)

## What you get

**Iron Tools tab**
- **3D MEP Router**: 3D Route, Branch, Settings
- **Sprinkler Tools**: Align & Connect, Connect Only
- **Modify Tools**: Lift Elements, Set Height By Ref
- **Annotation Tools**: Smart Tag
- **View Tools**: Template Manager *(new)*

**Modify tab**: an "Iron Tools" panel with Lift Elements and Set Height By Ref (added automatically by `startup.py`).

## Smart Tag

Tag a group of elements in one neat, aligned column instead of placing tags one by one.

1. Select the elements (or click the button and pick them).
2. Choose the **leader angle**, the **side** (auto from your click, right or left), a **tag type for each category** in the selection, the gap between tags, and whether to show the **live preview**.
3. Move the mouse: the preview shows where the tags and leaders will go. Click to place them.

Leader angle modes:
- **Free** (default): tags sit exactly where you click; each leader takes the straight line that suits it. Shortest leaders.
- **90°**: straight vertical legs, staggered so they never lie on top of each other.
- **45°**: parallel diagonal legs. If a 45° leg cannot reach without entering the tag column, the column moves out a little to keep the angle exact.

Tags are stacked tight (default gap 0.5 mm on paper) and aligned. The order of the tags is chosen so leaders do not cross and no leader runs through another tag. Works in plans, sections, elevations and locked 3D views (including isometric). Your choices are remembered, and one Ctrl+Z undoes everything.

<p align="center"><img src="docs/images/smart-tag.png" width="420"></p>

## Template Manager

All view templates of the project in one window.

- **List**: search by name, *Only used* filter, view type, and how many views use each template (click the number to select those views).
- **Settings**: tick which settings the template controls, grouped like Revit's dialog, with search and *Only controlled*. Select several templates to change them together: a grey tick means "only some", and values that differ show `*VARIES*`.
- **Accept / Discard**: renames and ticks wait for *Accept* (an orange dot marks templates with changes).
- **Copy / Paste settings**: copy chosen settings from one template to one or many others, including **V/G overrides, filters (same order and colours), view range and worksets**, optionally with the *Controlled by template* flags. Settings the target view type does not have are skipped and counted.
- **Duplicate / Delete** (delete asks first and tells you how many views lose their template). Every action is one Ctrl+Z in Revit.

<p align="center"><img src="docs/images/template-manager.png" width="720"></p>
<p align="center"><img src="docs/images/template-paste.png" width="420"></p>

## Screenshots

<table>
  <tr>
    <td align="center" width="50%"><b>3D MEP Router: Settings</b><br>Default length, slope and extra angles for 3D Route and Branch.<br><br><img src="docs/images/settings.png" width="380"></td>
    <td align="center" width="50%"><b>Lift Elements</b><br>Move the selection up or down by a set offset, with quick-value chips.<br><br><img src="docs/images/lift-elements.png" width="380"></td>
  </tr>
  <tr>
    <td align="center"><b>Set Height By Ref</b><br>Pick one element as the reference and move the whole group to a new height from its level.<br><br><img src="docs/images/set-height-by-ref.png" width="380"></td>
    <td align="center"><b>Result cards</b><br>Every tool ends with a clear summary: green = done, amber = done with notes, red = failed.<br><br><img src="docs/images/sprinkler-result.png" width="380"><br><img src="docs/images/result-warning.png" width="380"></td>
  </tr>
</table>

## Install (recommended, gets updates)

1. Make sure pyRevit is installed.
2. Open PowerShell or Command Prompt and run:

   ```
   pyrevit extend ui IronTools https://github.com/Ahmedzero800/IronTools.git
   ```

3. Open Revit (or click **pyRevit > Reload** if it is open). The **Iron Tools** tab appears.

**To update later:** pyRevit tab > **Update**, or run `pyrevit extensions update IronTools`.

## Install (manual, no updates)

1. Close Revit.
2. Press `Win + R`, type `%APPDATA%\pyRevit\Extensions`, press Enter (create the folder if missing).
3. Download this repo as ZIP (green **Code** button > **Download ZIP**), extract it, and copy the folder into `Extensions`, renamed to `IronTools.extension`.
4. Open Revit.

## Uninstall

```
pyrevit extend remove IronTools
```

Or delete `%APPDATA%\pyRevit\Extensions\IronTools.extension` and reload pyRevit.

---

## طريقة التثبيت

1. لازم يكون pyRevit متسطب.
2. افتح PowerShell واكتب الأمر:

   ```
   pyrevit extend ui IronTools https://github.com/Ahmedzero800/IronTools.git
   ```

3. افتح Revit (أو اعمل **Reload** من تاب pyRevit) هتلاقي تاب **Iron Tools** ظهر.

**للتحديث:** من تاب pyRevit اضغط **Update**.
