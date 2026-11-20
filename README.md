# IronTools

pyRevit extension for MEP work in Revit (tested on pyRevit 7 / Revit 2024).

## What you get

**Iron Tools tab**
- **3D MEP Router**: 3D Route, Branch, Settings
- **Sprinkler Tools**: Align & Connect, Connect Only
- **Modify Tools**: Lift Elements, Set Height By Ref

**Modify tab**: an "Iron Tools" panel with Lift Elements and Set Height By Ref (added automatically by `startup.py`).

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
