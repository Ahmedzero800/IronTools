# -*- coding: utf-8 -*-
"""Continue a pipe, duct, conduit or cable tray from its open end: click a colored line to go straight (yellow) or turn 90 (green), 45 (red) or your extra angles, then set length, angle and slope."""
from pyrevit import revit, DB, script

from agrouter import ui
import irontools_ui as dui

uidoc = revit.uidoc
if not isinstance(revit.doc.ActiveView, DB.View3D):
    dui.alert("3D Route", "This tool works in a 3D view. Open a 3D view and try again.",
              "warning", "Open a 3D view first")
    script.exit()

settings = ui.Settings()
element, connector = ui.get_start(uidoc)
if element is not None:
    ui.route_loop(uidoc, element, connector, settings)
