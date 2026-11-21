# -*- coding: utf-8 -*-
"""Smart Tag core: place tags in a neat, aligned column with 90 or 45 degree leaders.

Layout (seen in the view):

      element ----.                 tags stacked in one column,
                   \                left- or right-aligned on the picked point
      element ------\-----.
                     \     \______ [TAG 1]
                      \__________ [TAG 2]
                       elbows all on one vertical line, legs parallel
                       (90 = straight up/down, 45 = diagonal), so leaders never cross

Everything is computed in the view's own 2D frame (right / up directions),
so it works in plans, sections, elevations and locked 3D views.
"""

import math
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ElementId, FamilySymbol, FilteredElementCollector,
                               IndependentTag, LeaderEndCondition, LocationCurve, LocationPoint,
                               Reference, TagOrientation, Transaction, View3D, ViewType, XYZ)

MM_PER_FT = 304.8

# Model category -> its tag category (names, so a missing enum in an older Revit is just skipped)
_TAG_MAP_NAMES = [
    ("OST_PipeCurves", "OST_PipeTags"),
    ("OST_FlexPipeCurves", "OST_FlexPipeTags"),
    ("OST_PipeFitting", "OST_PipeFittingTags"),
    ("OST_PipeAccessory", "OST_PipeAccessoryTags"),
    ("OST_DuctCurves", "OST_DuctTags"),
    ("OST_FlexDuctCurves", "OST_FlexDuctTags"),
    ("OST_DuctFitting", "OST_DuctFittingTags"),
    ("OST_DuctAccessory", "OST_DuctAccessoryTags"),
    ("OST_DuctTerminal", "OST_DuctTerminalTags"),
    ("OST_CableTray", "OST_CableTrayTags"),
    ("OST_CableTrayFitting", "OST_CableTrayFittingTags"),
    ("OST_Conduit", "OST_ConduitTags"),
    ("OST_ConduitFitting", "OST_ConduitFittingTags"),
    ("OST_Sprinklers", "OST_SprinklerTags"),
    ("OST_MechanicalEquipment", "OST_MechanicalEquipmentTags"),
    ("OST_PlumbingFixtures", "OST_PlumbingFixtureTags"),
    ("OST_ElectricalEquipment", "OST_ElectricalEquipmentTags"),
    ("OST_ElectricalFixtures", "OST_ElectricalFixtureTags"),
    ("OST_LightingFixtures", "OST_LightingFixtureTags"),
    ("OST_LightingDevices", "OST_LightingDeviceTags"),
    ("OST_FireAlarmDevices", "OST_FireAlarmDeviceTags"),
    ("OST_DataDevices", "OST_DataDeviceTags"),
    ("OST_CommunicationDevices", "OST_CommunicationDeviceTags"),
    ("OST_SecurityDevices", "OST_SecurityDeviceTags"),
    ("OST_GenericModel", "OST_GenericModelTags"),
    ("OST_Walls", "OST_WallTags"),
    ("OST_Doors", "OST_DoorTags"),
    ("OST_Windows", "OST_WindowTags"),
    ("OST_Floors", "OST_FloorTags"),
    ("OST_StructuralColumns", "OST_StructuralColumnTags"),
    ("OST_StructuralFraming", "OST_StructuralFramingTags"),
    ("OST_Furniture", "OST_FurnitureTags"),
    ("OST_SpecialityEquipment", "OST_SpecialityEquipmentTags"),
]


def _id_int(eid):
    """ElementId -> int on every Revit version (IntegerValue was replaced by Value in 2024+)."""
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def _tag_map():
    result = {}
    for src, tag in _TAG_MAP_NAMES:
        s = getattr(BuiltInCategory, src, None)
        t = getattr(BuiltInCategory, tag, None)
        if s is not None and t is not None:
            result[_id_int(ElementId(s))] = t
    return result


TAG_MAP = _tag_map()


# ---------------------------------------------------------------
# Views and tag types
# ---------------------------------------------------------------
_TAGGABLE_VIEWS = set(getattr(ViewType, n) for n in
                      ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan",
                       "Section", "Elevation", "Detail") if hasattr(ViewType, n))


def view_problem(view):
    """None if tags can be placed in this view, else a short reason."""
    if view is None:
        return "There is no active view."
    if isinstance(view, View3D):
        if not view.IsLocked:
            return "Tags in a 3D view need a locked view. Lock the 3D view (Save Orientation and Lock View) or use a plan / section."
        return None
    if view.ViewType not in _TAGGABLE_VIEWS:
        return "Open a plan, section or elevation view (or a locked 3D view) and try again."
    return None


def is_taggable(element):
    cat = element.Category if element is not None else None
    return cat is not None and _id_int(cat.Id) in TAG_MAP


def _symbol_label(sym):
    fam = sym.FamilyName if hasattr(sym, "FamilyName") else sym.Family.Name
    name = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
    type_name = name.AsString() if name else ""
    return u"{} : {}".format(fam, type_name)


def tag_types_for(doc, category_id):
    """List of (ElementId, label) tag types for a model category, default first.
    Multi-category tags are added at the end."""
    tag_bic = TAG_MAP.get(_id_int(category_id))
    options = []
    if tag_bic is not None:
        syms = FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(tag_bic)
        options = [(s.Id, _symbol_label(s)) for s in syms]
        options.sort(key=lambda o: o[1].lower())
        try:
            default_id = doc.GetDefaultFamilyTypeId(ElementId(tag_bic))
        except Exception:
            default_id = ElementId.InvalidElementId
        if default_id is not None and default_id != ElementId.InvalidElementId:
            options.sort(key=lambda o: 0 if o[0] == default_id else 1)
    multi = getattr(BuiltInCategory, "OST_MultiCategoryTags", None)
    if multi is not None:
        msyms = FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(multi)
        for s in sorted(msyms, key=lambda x: _symbol_label(x).lower()):
            options.append((s.Id, _symbol_label(s) + u"  (multi-category)"))
    return options


# ---------------------------------------------------------------
# 2D geometry in the view frame
# ---------------------------------------------------------------
class ViewFrame(object):
    """Converts model points to the view's 2D (u = right, v = up) and back."""

    def __init__(self, view):
        self.o = view.Origin
        self.r = view.RightDirection
        self.u = view.UpDirection
        self.n = view.ViewDirection

    def to2d(self, p):
        d = p - self.o
        return (d.DotProduct(self.r), d.DotProduct(self.u))

    def depth(self, p):
        return (p - self.o).DotProduct(self.n)

    def to3d(self, uv, depth):
        return self.o + self.r * uv[0] + self.u * uv[1] + self.n * depth


def _closest_on_segment(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return a
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return (ax + t * dx, ay + t * dy)


def _ray_hits_segment(origin, direction, a, b):
    """Point where the ray origin + t*direction (t > 0) crosses segment a-b, or None."""
    ox, oy = origin
    dx, dy = direction
    ex, ey = b[0] - a[0], b[1] - a[1]
    den = dx * ey - dy * ex
    if abs(den) < 1e-9:
        return None  # parallel
    wx, wy = a[0] - ox, a[1] - oy
    t = (wx * ey - wy * ex) / den
    s = (wx * dy - wy * dx) / den
    if t <= 1e-9 or s < -1e-9 or s > 1 + 1e-9:
        return None
    return (ox + t * dx, oy + t * dy)


def _shape(element, frame):
    """('curve', a, b) or ('point', p) in view 2D, or None."""
    loc = element.Location
    if isinstance(loc, LocationCurve):
        c = loc.Curve
        return ("curve", frame.to2d(c.GetEndPoint(0)), frame.to2d(c.GetEndPoint(1)))
    if isinstance(loc, LocationPoint):
        return ("point", frame.to2d(loc.Point))
    bb = element.get_BoundingBox(None)
    if bb is not None:
        return ("point", frame.to2d((bb.Min + bb.Max) * 0.5))
    return None


def _closest_on_shape(p, shape):
    if shape[0] == "curve":
        return _closest_on_segment(p, shape[1], shape[2])
    return shape[1]


def _tag_box(tag, view, frame):
    """(umin, umax, vmin, vmax) of the tag in view 2D."""
    bb = tag.get_BoundingBox(view)
    if bb is None:
        return None
    us, vs = [], []
    for x in (bb.Min.X, bb.Max.X):
        for y in (bb.Min.Y, bb.Max.Y):
            for z in (bb.Min.Z, bb.Max.Z):
                u, v = frame.to2d(XYZ(x, y, z))
                us.append(u)
                vs.append(v)
    return (min(us), max(us), min(vs), max(vs))


# ---------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------
def place_tags(doc, view, elements, pick_point, tag_type_by_cat, angle=90,
               gap_mm=2.0, shoulder_mm=4.0, side_mode="auto", transaction_name="Smart Tag"):
    """Create one tag per element, stacked in a column at pick_point.

    tag_type_by_cat : {category id int: tag type ElementId}
    angle           : 90 (vertical legs) or 45 (diagonal legs)
    side_mode       : 'auto' (from the click), 'right' or 'left'
    gap_mm / shoulder_mm are paper millimetres (scaled by the view scale).
    Returns dict with counts and the created tag ids.
    """
    frame = ViewFrame(view)
    scale = float(view.Scale or 100)
    paper = scale / MM_PER_FT          # 1 paper mm in model feet
    gap = gap_mm * paper
    shoulder = shoulder_mm * paper

    P = frame.to2d(pick_point)
    depth = frame.depth(pick_point)

    # 1) Collect element shapes; skip elements without a chosen tag type
    items, skipped = [], []
    for el in elements:
        type_id = tag_type_by_cat.get(_id_int(el.Category.Id)) if el.Category else None
        shape = _shape(el, frame)
        if type_id is None or shape is None:
            skipped.append(el)
            continue
        items.append({"el": el, "type": type_id, "shape": shape,
                      "rep": _closest_on_shape(P, shape)})
    if not items:
        return {"created": [], "failed": [], "skipped": skipped}

    # Side / growth direction from the group's centre (curve midpoints), not from the
    # closest points: those sit right under the click and would make the side random
    def _mid(shape):
        if shape[0] == "curve":
            return ((shape[1][0] + shape[2][0]) * 0.5, (shape[1][1] + shape[2][1]) * 0.5)
        return shape[1]
    # Median (not mean): one very long pipe must not drag the centre away
    mids = [_mid(i["shape"]) for i in items]
    us = sorted(m[0] for m in mids)
    vs = sorted(m[1] for m in mids)
    cu = us[len(us) // 2]
    cv = vs[len(vs) // 2]
    if side_mode == "right":
        side = 1
    elif side_mode == "left":
        side = -1
    else:
        side = 1 if P[0] >= cu else -1      # tags to the right (+1) or left (-1) of the group
    grow_up = P[1] > cv                      # stack grows away from the group

    # Top tag <-> top element (then left-to-right for ties), keeps leaders tidy
    items.sort(key=lambda i: (-round(i["rep"][1], 4), side * i["rep"][0]))

    failed = []
    t = Transaction(doc, transaction_name)
    t.Start()
    try:
        # 2) Create tags without leaders, sitting ON their element: Revit's tag bounding box
        #    always stretches to the tagged element, so this is the only way to measure the text
        made = []
        for it in items:
            try:
                ref = Reference(it["el"])
                tag = IndependentTag.Create(doc, it["type"], view.Id, ref, False,
                                            TagOrientation.Horizontal, frame.to3d(it["rep"], depth))
                it["tag"], it["ref"] = tag, ref
                made.append(it)
            except Exception as ex:
                failed.append((it["el"], str(ex)))
        doc.Regenerate()

        # 3) Measure every tag and the head point's offset inside its box
        for it in made:
            box = _tag_box(it["tag"], view, frame)
            head = frame.to2d(it["tag"].TagHeadPosition)
            if box is None:
                box = (head[0], head[0] + 10 * paper, head[1] - 1.5 * paper, head[1] + 1.5 * paper)
            it["w"] = box[1] - box[0]
            it["h"] = box[3] - box[2]
            it["off_umin"] = box[0] - head[0]
            it["off_umax"] = box[1] - head[0]
            it["off_vmid"] = (box[2] + box[3]) * 0.5 - head[1]

        # 4) Stack positions (top -> bottom)
        total = sum(i["h"] for i in made) + gap * max(0, len(made) - 1)
        cursor = P[1] + total if grow_up else P[1]
        for it in made:
            it["vmid"] = cursor - it["h"] * 0.5
            cursor -= it["h"] + gap
            head_u = (P[0] - it["off_umin"]) if side > 0 else (P[0] - it["off_umax"])
            it["tag"].TagHeadPosition = frame.to3d((head_u, it["vmid"] - it["off_vmid"]), depth)

        # 5) Leaders: elbows on one line next to the column, legs parallel at the chosen angle
        elbow_u = P[0] - side * shoulder
        diag = (angle == 45)
        for it in made:
            tag, ref, shape = it["tag"], it["ref"], it["shape"]
            E = (elbow_u, it["vmid"])
            target = _closest_on_shape(E, shape)
            dv = 1.0 if target[1] > E[1] else -1.0
            end = None
            if abs(target[1] - E[1]) < 1e-6:
                end = target                       # same height: straight leader
            elif shape[0] == "curve":
                if diag:
                    d = (-side / math.sqrt(2), dv / math.sqrt(2))
                    end = _ray_hits_segment(E, d, shape[1], shape[2])
                if end is None:
                    # 90, or no room for a 45 leg: a clean straight leg is the tidy fallback
                    end = _ray_hits_segment(E, (0.0, dv), shape[1], shape[2])
            if end is None:
                # Point element (or the leg misses the run): start at the element and
                # move the elbow so the leg keeps the chosen angle
                end = target if shape[0] == "curve" else shape[1]
                rise = abs(it["vmid"] - end[1])
                eu = end[0] + side * rise if diag else end[0]
                if diag and side * (eu - elbow_u) > 0:
                    eu = end[0]                    # no room for 45: go straight instead
                # never let the elbow pass the tag column
                if side * (eu - elbow_u) > 0:
                    eu = elbow_u
                E = (eu, it["vmid"])
            try:
                tag.HasLeader = True
                if tag.CanLeaderEndConditionBeAssigned(LeaderEndCondition.Free):
                    tag.LeaderEndCondition = LeaderEndCondition.Free
                tag.SetLeaderEnd(ref, frame.to3d(end, depth))
                tag.SetLeaderElbow(ref, frame.to3d(E, depth))
            except Exception as ex:
                failed.append((it["el"], "leader: " + str(ex)))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise

    return {"created": [i["tag"].Id for i in made], "failed": failed, "skipped": skipped,
            "side": "right" if side > 0 else "left", "angle": angle}
