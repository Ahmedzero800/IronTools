# -*- coding: utf-8 -*-
"""Set the defaults for 3D Route and Branch: length, slope, extra turn angles, whether to ask each step and whether to keep routing."""
from agrouter import ui
import irontools_ui as dui

settings = ui.Settings()


def _validate(v):
    try:
        length = float(v["length"].replace(",", "."))
        slope = float(v["slope"].replace(",", "."))
    except ValueError:
        return "Length and slope must be numbers."
    if length <= 0 or abs(slope) > 100:
        return "Length must be more than 0 and slope between -100 and 100 %."
    return {"length_mm": length, "slope_pct": slope,
            "extra_angles": ", ".join("%g" % a for a in ui.parse_angles(v["angles"])),
            "ask_each_step": v["ask"], "continuous": v["continuous"]}


values = dui.ask_form(
    "MEP 3D Router",
    subtitle="Default settings for 3D Route and Branch",
    fields=[{"key": "length", "label": "Default length", "value": "%g" % settings.length_mm, "unit": "mm"},
            {"key": "slope", "label": "Default slope", "value": "%g" % settings.slope_pct, "unit": "%",
             "hint": "+ down  /  - up"},
            {"key": "angles", "label": "Extra angles", "value": settings.extra_angles, "unit": "deg",
             "hint": "Comma separated, e.g. 30, 60"}],
    toggles=[{"key": "ask", "label": "Ask for length / angle / slope after picking a direction",
              "value": settings.ask_each_step},
             {"key": "continuous", "label": "Keep routing from the new end", "value": settings.continuous}],
    note="Gizmo colors: yellow straight, green 90, red 45, blue / purple / teal / brown extra angles.",
    icon="settings", ok_text="Save", validate=_validate)

if values:
    settings.length_mm = values["length_mm"]
    settings.slope_pct = values["slope_pct"]
    settings.extra_angles = values["extra_angles"]
    settings.ask_each_step = values["ask_each_step"]
    settings.continuous = values["continuous"]
    settings.save()
    dui.show_result("MEP 3D Router", "success", "Settings saved",
                    stats=[("%g" % settings.length_mm, "Length mm"),
                           ("%g" % settings.slope_pct, "Slope %")],
                    details=["Extra angles: " + (settings.extra_angles or "none")])
