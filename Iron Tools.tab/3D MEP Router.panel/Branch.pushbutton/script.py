# -*- coding: utf-8 -*-
"""Add a tee branch from the middle of a pipe, duct, conduit or cable tray: click the run where the branch starts, pick one of the four green directions, set the length and slope."""
from pyrevit import revit, DB, script

from agrouter import ui
import irontools_ui as dui

uidoc = revit.uidoc
if not isinstance(revit.doc.ActiveView, DB.View3D):
    dui.alert("Branch", "This tool works in a 3D view. Open a 3D view and try again.",
              "warning", "Open a 3D view first")
    script.exit()

settings = ui.Settings()
branch, connector, keep_going = ui.branch_start(uidoc, settings)
if branch is not None and keep_going:
    ui.route_loop(uidoc, branch, connector, settings)
