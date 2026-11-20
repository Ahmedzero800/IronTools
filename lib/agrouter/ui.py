# -*- coding: utf-8 -*-
"""User interaction for MEP 3D Router: settings, the colored direction gizmo, the step dialog and the routing loop."""
from Autodesk.Revit import DB
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import script

from agrouter import core
import irontools_ui as dui

TITLE = "MEP 3D Router"

SECTION = "MEP3DRouter"
DEFAULTS = {
    "length_mm": 1000.0,
    "slope_pct": 0.0,
    "extra_angles": "30, 60",
    "ask_each_step": True,
    "continuous": True,
}

YELLOW = (255, 190, 0)
GREEN = (0, 170, 0)
RED = (220, 0, 0)
EXTRA_COLORS = [(0, 110, 255), (200, 0, 200), (0, 180, 180), (140, 80, 0)]

def _inaccurate_ids():
    ids = set()
    group = getattr(DB.BuiltInFailures, "InaccurateFailures", None)
    for name in dir(group) if group else []:
        value = getattr(group, name, None)
        if isinstance(value, DB.FailureDefinitionId):
            ids.add(str(value.Guid))
    return ids


INACCURATE = _inaccurate_ids()


class QuietWarnings(DB.IFailuresPreprocessor):
    """Drops warnings before Revit shows them: all of them, or only those whose definition is in `only`."""

    def __init__(self, only=None):
        self.only = only

    def PreprocessFailures(self, accessor):
        for message in accessor.GetFailureMessages():
            if message.GetSeverity() != DB.FailureSeverity.Warning:
                continue
            if self.only is None or str(message.GetFailureDefinitionId().Guid) in self.only:
                accessor.DeleteWarning(message)
        return DB.FailureProcessingResult.Continue


def quiet(transaction, only=None):
    """Attach QuietWarnings to a started or unstarted transaction."""
    options = transaction.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(QuietWarnings(only))
    transaction.SetFailureHandlingOptions(options)


LINES_HIDDEN = ("Lines are hidden in this 3D view. Turn on Lines in Visibility/Graphics "
                "(or in the view template) and try again.")


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_angles(text):
    """Angles from a comma-separated string, kept between 1 and 179 degrees, without 45 and 90."""
    angles = []
    for part in str(text or "").replace(";", ",").split(","):
        try:
            value = float(part.strip())
        except ValueError:
            continue
        if 0 < value < 180 and value not in (45.0, 90.0) and value not in angles:
            angles.append(value)
    return angles


class Settings(object):
    """Saved user preferences, stored in the pyRevit config under [MEP3DRouter]."""

    def __init__(self):
        self._cfg = script.get_config(SECTION)
        self.length_mm = float(self._cfg.get_option("length_mm", DEFAULTS["length_mm"]))
        self.slope_pct = float(self._cfg.get_option("slope_pct", DEFAULTS["slope_pct"]))
        self.extra_angles = str(self._cfg.get_option("extra_angles", DEFAULTS["extra_angles"]))
        self.ask_each_step = _as_bool(self._cfg.get_option("ask_each_step", DEFAULTS["ask_each_step"]))
        self.continuous = _as_bool(self._cfg.get_option("continuous", DEFAULTS["continuous"]))

    def angles(self):
        return parse_angles(self.extra_angles)

    def save(self):
        for name in DEFAULTS:
            self._cfg.set_option(name, getattr(self, name))
        script.save_config()


class RunFilter(ISelectionFilter):
    """Lets the user pick only pipes, ducts, conduits and cable trays."""

    def AllowElement(self, element):
        return core.is_routable(element)

    def AllowReference(self, reference, point):
        return False


class IdFilter(ISelectionFilter):
    """Lets the user pick only the given element ids (the gizmo lines)."""

    def __init__(self, ids):
        self.ids = set(ids)

    def AllowElement(self, element):
        return core.id_value(element.Id) in self.ids

    def AllowReference(self, reference, point):
        return False


class Option(object):
    """One gizmo line: the direction it stands for, its color, the turn angle and the turning axis."""

    def __init__(self, direction, color, angle, perp, label):
        self.direction = direction
        self.color = color
        self.angle = angle
        self.perp = perp
        self.label = label


def turn_options(run, connector, extra_angles):
    """Straight on (yellow) plus 90 (green), 45 (red) and each extra angle toward the four sides."""
    side, up = core.perpendicular_axes(run, connector)
    perps = [(side, "right"), (side.Negate(), "left"), (up, "up"), (up.Negate(), "down")]
    options = [Option(run, YELLOW, 0.0, None, "straight")]
    angle_colors = [(90.0, GREEN), (45.0, RED)]
    for i, angle in enumerate(extra_angles):
        angle_colors.append((angle, EXTRA_COLORS[i % len(EXTRA_COLORS)]))
    for perp, name in perps:
        for angle, color in angle_colors:
            options.append(Option(core.turn_direction(run, perp, angle), color, angle, perp,
                                  "%g deg %s" % (angle, name)))
    return options


def branch_options(run):
    """The four 90-degree branch directions (green) around a run."""
    side, up = core.perpendicular_axes(run)
    return [Option(d, GREEN, 90.0, d, name) for d, name in
            [(side, "right"), (side.Negate(), "left"), (up, "up"), (up.Negate(), "down")]]


def _sketch_plane(doc, origin, direction):
    normal = direction.CrossProduct(DB.XYZ.BasisZ)
    if normal.GetLength() < 1e-6:
        normal = direction.CrossProduct(DB.XYZ.BasisX)
    return DB.SketchPlane.Create(doc, DB.Plane.CreateByNormalAndOrigin(normal.Normalize(), origin))


def _draw_gizmo(doc, view, origin, options, size):
    inner = max(size * 0.8, 0.35)
    outer = inner + max(size * 3.0, 2.5)
    lines = {}
    for opt in options:
        reach = outer * (1.25 if opt.angle == 0 else 1.0)
        line = DB.Line.CreateBound(origin + opt.direction.Multiply(inner), origin + opt.direction.Multiply(reach))
        curve = doc.Create.NewModelCurve(line, _sketch_plane(doc, origin, opt.direction))
        ogs = DB.OverrideGraphicSettings()
        ogs.SetProjectionLineColor(DB.Color(*opt.color))
        ogs.SetProjectionLineWeight(10)
        view.SetElementOverrides(curve.Id, ogs)
        lines[core.id_value(curve.Id)] = opt
    return lines


def pick_option(uidoc, origin, options, size, prompt="Click a direction line (Esc to cancel)"):
    """Show the gizmo at origin and let the user click one line.

    The gizmo is drawn inside a TransactionGroup that is always rolled back, so nothing stays in the model.
    Returns the picked Option, or None on Esc or when lines can't be shown.
    """
    doc = uidoc.Document
    view = doc.ActiveView
    group = DB.TransactionGroup(doc, "MEP 3D gizmo")
    group.Start()
    try:
        t = DB.Transaction(doc, "Show gizmo")
        t.Start()
        quiet(t)
        lines_cat = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Lines)
        if view.GetCategoryHidden(lines_cat.Id):
            try:
                view.SetCategoryHidden(lines_cat.Id, False)
            except Exception:
                t.RollBack()
                dui.alert(TITLE, LINES_HIDDEN, "warning", "Lines are hidden")
                return None
        lines = _draw_gizmo(doc, view, origin, options, size)
        t.Commit()
        uidoc.RefreshActiveView()
        try:
            ref = uidoc.Selection.PickObject(ObjectType.Element, IdFilter(lines.keys()), prompt)
        except OperationCanceledException:
            return None
        return lines.get(core.id_value(ref.ElementId))
    finally:
        group.RollBack()


def _num(text):
    return float(str(text).strip().replace(",", "."))


def step_dialog(note, length_mm, angle, slope_pct, continuous, angle_enabled, slope_enabled,
                icon="route"):
    """Dark IronTools dialog for length, angle, slope and whether to keep routing.
    Returns the values dict, or None when cancelled."""

    def _validate(v):
        try:
            length = _num(v["length"])
            ang = _num(v["angle"])
            slope = _num(v["slope"])
        except ValueError:
            return "Enter numbers only."
        if length <= 0:
            return "Length must be more than 0."
        if angle_enabled and not 0 < ang < 180:
            return "Angle must be between 0 and 180."
        if abs(slope) > 100:
            return "Slope must be between -100 and 100 %."
        return {"length_mm": length, "angle": ang, "slope_pct": slope,
                "continuous": v["continuous"]}

    return dui.ask_form(
        TITLE, subtitle=note,
        fields=[{"key": "length", "label": "Length", "value": "%g" % length_mm, "unit": "mm"},
                {"key": "angle", "label": "Angle", "value": "%g" % angle, "unit": "deg",
                 "enabled": angle_enabled},
                {"key": "slope", "label": "Slope", "value": "%g" % slope_pct, "unit": "%",
                 "hint": "+ down  /  - up", "enabled": slope_enabled}],
        toggles=[{"key": "continuous", "label": "Keep routing from the new end", "value": continuous}],
        icon=icon, ok_text="Route", validate=_validate)


def ask_step(settings, option, is_branch=False):
    """Values for one step: from the dialog, or straight from settings when ask_each_step is off."""
    turning = option.angle != 0
    if not settings.ask_each_step:
        return {"length_mm": settings.length_mm, "angle": option.angle,
                "slope_pct": settings.slope_pct, "continuous": settings.continuous}
    if is_branch:
        note = "Branch (tee) %s" % option.label
    else:
        note = "Turn %s" % option.label if turning else "Straight on (extend by length)"
    values = step_dialog(note, settings.length_mm, option.angle, settings.slope_pct, settings.continuous,
                         angle_enabled=turning and not is_branch, slope_enabled=turning or is_branch,
                         icon="branch" if is_branch else "route")
    if values:
        settings.length_mm = values["length_mm"]
        settings.continuous = values["continuous"]
        if turning or is_branch:
            settings.slope_pct = values["slope_pct"]
        settings.save()
    return values


def get_start(uidoc):
    """The run and open end to start from: a selected run with one open end, or a picked run.

    Returns (element, connector) or (None, None) when the user cancels or the run has no open end.
    """
    doc = uidoc.Document
    selected = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    if len(selected) == 1 and core.is_routable(selected[0]):
        ends = core.open_ends(selected[0])
        if len(ends) == 1:
            return selected[0], ends[0]
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element, RunFilter(),
            "Click the pipe, duct, conduit or cable tray near the end you want to continue from")
    except OperationCanceledException:
        return None, None
    element = doc.GetElement(ref.ElementId)
    ends = core.open_ends(element)
    if not ends:
        dui.alert(TITLE, "This piece is connected at both ends. Pick a run with an open end.",
                  "warning", "No open end")
        return None, None
    point = ref.GlobalPoint or element.Location.Curve.Evaluate(0.5, True)
    return element, min(ends, key=lambda c: c.Origin.DistanceTo(point))


def _select(uidoc, element):
    from System.Collections.Generic import List
    ids = List[DB.ElementId]()
    ids.Add(element.Id)
    uidoc.Selection.SetElementIds(ids)


def route_loop(uidoc, element, connector, settings):
    """Repeat: show the gizmo at the open end, ask for the step, add it. Stops on Esc, Cancel or one step
    when continuous routing is off. Each step is its own undo entry. Returns the number of steps made."""
    doc = uidoc.Document
    steps = 0
    while True:
        run = core.run_direction(element, connector)
        option = pick_option(uidoc, connector.Origin, turn_options(run, connector, settings.angles()),
                             core.size_of(connector))
        if option is None:
            break
        values = ask_step(settings, option)
        if not values:
            break
        if option.angle == 0:
            direction = run
        else:
            direction = core.apply_slope(core.turn_direction(run, option.perp, values["angle"]), values["slope_pct"])
        t = DB.Transaction(doc, "MEP 3D")
        t.Start()
        quiet(t, INACCURATE)
        try:
            element, connector = core.add_segment(doc, element, connector, direction,
                                                  core.mm_to_ft(values["length_mm"]))
            t.Commit()
        except core.RouteError as err:
            t.RollBack()
            dui.alert(TITLE, str(err), "error", "Could not add this segment")
            break
        except Exception as err:
            t.RollBack()
            dui.alert(TITLE, str(err), "error", "Routing failed")
            break
        steps += 1
        _select(uidoc, element)
        connector = core.closest_end(element, connector.Origin)
        if not values["continuous"]:
            break
    return steps


def branch_start(uidoc, settings):
    """Pick a point on a run, choose one of four 90-degree directions and add a tee branch.

    Returns (branch, open_connector, continuous) or (None, None, False).
    """
    doc = uidoc.Document
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, RunFilter(),
                                         "Click the pipe, duct, conduit or cable tray where the branch starts")
    except OperationCanceledException:
        return None, None, False
    element = doc.GetElement(ref.ElementId)
    try:
        point = core.clamp_branch_point(element, ref.GlobalPoint or element.Location.Curve.Evaluate(0.5, True))
    except core.RouteError as err:
        dui.alert(TITLE, str(err), "warning", "Cannot branch here")
        return None, None, False
    curve = element.Location.Curve
    run = (curve.GetEndPoint(1) - curve.GetEndPoint(0)).Normalize()
    size = core.size_of(core.end_connectors(element)[0])
    option = pick_option(uidoc, point, branch_options(run), size)
    if option is None:
        return None, None, False
    values = ask_step(settings, option, is_branch=True)
    if not values:
        return None, None, False
    direction = core.apply_slope(option.direction, values["slope_pct"])
    t = DB.Transaction(doc, "MEP 3D branch")
    t.Start()
    quiet(t, INACCURATE)
    try:
        branch, connector = core.add_branch(doc, element, point, direction, core.mm_to_ft(values["length_mm"]))
        t.Commit()
    except core.RouteError as err:
        t.RollBack()
        dui.alert(TITLE, str(err), "error", "Could not add the branch")
        return None, None, False
    except Exception as err:
        t.RollBack()
        dui.alert(TITLE, str(err), "error", "Branch failed")
        return None, None, False
    _select(uidoc, branch)
    return branch, connector, values["continuous"]
