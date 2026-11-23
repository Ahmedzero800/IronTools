# -*- coding: utf-8 -*-
"""View Template Manager: all view templates of the project in one window.
Rename, duplicate, delete, choose which settings each template controls (one or many
templates at once), and copy settings from one template to others."""

# ---------------------------------------------------------------
# Window
#   Left : every view template - search, 'Only used', view type, and how many views use it
#          (click the number to select those views).
#          Copy settings / Paste settings / Duplicate / Delete.
#   Right: the selected template's settings, grouped. Tick = controlled by the template.
#          Select several templates to change them together (grey tick = only some).
#   Changes (rename, ticks) wait for Accept - an orange dot marks them. Discard drops them.
#   Paste / Duplicate / Delete run straight away; each is one Ctrl+Z in Revit.
# ---------------------------------------------------------------

from pyrevit import revit
import irontools_ui as ui
import templatemgr_ui

doc = revit.doc
if doc.IsFamilyDocument:
    ui.alert("View Template Manager", "Open a project (not a family) and try again.", "warning",
             "No view templates in a family")
else:
    templatemgr_ui.ManagerWindow(doc, revit.uidoc).show()
