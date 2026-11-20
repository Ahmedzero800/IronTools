# -*- coding: utf-8 -*-
"""IronTools startup: mirrors the 'Modify Tools' buttons into Revit's Modify tab
(the 'Modify | <Category>' contextual tab you see when elements are selected)."""

# ---------------------------------------------------------------
# How it works
# - pyRevit runs this file every time Revit starts or pyRevit reloads.
# - It finds the buttons of  Iron Tools tab > Modify Tools panel.
# - It adds a panel called "Iron Tools" to Revit's Modify tab with
#   copies of those buttons.
# - Each copy just forwards the click to the original pyRevit button,
#   so there is only ONE script per tool (no duplicated code).
# - Any new button you add to "Modify Tools.panel" shows up here too
#   after a pyRevit reload.
# ---------------------------------------------------------------

import clr
clr.AddReference("AdWindows")
clr.AddReference("PresentationCore")
from Autodesk.Windows import (ComponentManager, RibbonPanel, RibbonPanelSource,
                              RibbonButton, RibbonItemSize)
from System.Windows.Input import ICommand

SOURCE_TAB_ID = "Iron Tools"        # pyRevit tab (folder "Iron Tools.tab")
SOURCE_PANEL_TITLE = "Modify Tools"  # pyRevit panel (folder "Modify Tools.panel")
TARGET_TAB_ID = "Modify"            # Revit's Modify / contextual Modify tab
MIRROR_PANEL_ID = "IronTools_ModifyCtx"
MIRROR_PANEL_TITLE = "Iron Tools"


def find_tab(tab_id):
    for tab in ComponentManager.Ribbon.Tabs:
        if tab.Id == tab_id:
            return tab
    return None


def find_original_button(button_id):
    """Look up the original pyRevit button at click time (survives pyRevit reloads)."""
    tab = find_tab(SOURCE_TAB_ID)
    if tab is None:
        return None
    for panel in tab.Panels:
        if panel.Source.Title == SOURCE_PANEL_TITLE:
            for item in panel.Source.Items:
                if item.Id == button_id:
                    return item
    return None


class ForwardCommand(ICommand):
    """Always enabled; forwards the click to the original pyRevit button."""

    def __init__(self, original_id):
        self.original_id = original_id

    def add_CanExecuteChanged(self, handler):
        pass

    def remove_CanExecuteChanged(self, handler):
        pass

    def CanExecute(self, parameter):
        return True

    def Execute(self, parameter):
        original = find_original_button(self.original_id)
        if original is not None and original.CommandHandler is not None:
            original.CommandHandler.Execute(original)


def build_mirror_panel():
    """Create (or re-create) the Iron Tools panel in the Modify tab. Returns True on success."""
    source_tab = find_tab(SOURCE_TAB_ID)
    target_tab = find_tab(TARGET_TAB_ID)
    if source_tab is None or target_tab is None:
        return False

    source_panel = None
    for panel in source_tab.Panels:
        if panel.Source.Title == SOURCE_PANEL_TITLE:
            source_panel = panel
            break
    if source_panel is None:
        return False

    # Remove the old copy first (avoids duplicates after a pyRevit reload)
    for panel in list(target_tab.Panels):
        if panel.Source is not None and panel.Source.Id == MIRROR_PANEL_ID:
            target_tab.Panels.Remove(panel)

    mirror_source = RibbonPanelSource()
    mirror_source.Id = MIRROR_PANEL_ID
    mirror_source.Title = MIRROR_PANEL_TITLE

    for item in source_panel.Source.Items:
        if not isinstance(item, RibbonButton):
            continue
        btn = RibbonButton()
        btn.Id = MIRROR_PANEL_ID + "_" + (item.Text or "").replace(" ", "")
        btn.Text = item.Text
        btn.Image = item.Image
        btn.LargeImage = item.LargeImage
        btn.ShowText = True
        btn.ShowImage = True
        btn.Size = RibbonItemSize.Large
        btn.Orientation = item.Orientation
        btn.ToolTip = item.ToolTip
        btn.CommandHandler = ForwardCommand(item.Id)
        btn.IsEnabled = True
        mirror_source.Items.Add(btn)

    if mirror_source.Items.Count == 0:
        return False

    mirror_panel = RibbonPanel()
    mirror_panel.Source = mirror_source
    target_tab.Panels.Add(mirror_panel)
    return True


# Try now; if the ribbon is not ready yet, retry when ribbon items finish loading
def _on_item_initialized(sender, args):
    if build_mirror_panel():
        ComponentManager.ItemInitialized -= _on_item_initialized


try:
    if not build_mirror_panel():
        ComponentManager.ItemInitialized += _on_item_initialized
except Exception as err:
    print("IronTools startup: could not add Modify tab panel: {}".format(err))
