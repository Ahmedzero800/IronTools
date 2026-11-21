# -*- coding: utf-8 -*-
"""Smart Tag core: place tags in a neat, aligned column with exact 90 or 45 degree leaders.

Layout (seen in the view):

      element ----+                  tags stacked in one column, aligned on the
                  |                  picked point, equal spacing (tag height + gap)
      element ----|---+
                  |   |______ [TAG 1]
                  |__________ [TAG 2]

  * every leg is exactly 90 (straight up / down) or 45 (diagonal); the last part
    (the shoulder) is always horizontal into the tag
  * if a leg would end inside the tag column, the whole column moves out a little
    instead of bending the angle
  * tags are assigned to slots so that no two leaders cross (swap search)

Steps used by the button:
  collect()  -> items with their 2D shape in the view
  measure()  -> real text size of each tag (temporary tags, rolled back)
  compute_layout(items, click_uv, ...) -> pure geometry, also used by the live preview
  apply_layout() -> creates the tags in one transaction
"""

import math
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, ElementId, FamilySymbol,
                               FilteredElementCollector, IndependentTag, LeaderEndCondition,
                               LocationCurve, LocationPoint, Reference, TagOrientation,
                               Transaction, View3D, ViewType, XYZ)

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
            return ("Tags in a 3D view need a locked view. Lock the 3D view "
                    "(Save Orientation and Lock View) or use a plan / section.")
        return None
    if view.ViewType not in _TAGGABLE_VIEWS:
        return "Open a plan, section or elevation view (or a locked 3D view) and try again."
    return None


def is_taggable(element):
    cat = element.Category if element is not None else None
    return cat is not None and _id_int(cat.Id) in TAG_MAP


def symbol_label(sym):
    fam = sym.FamilyName if hasattr(sym, "FamilyName") else sym.Family.Name
    name = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
    return u"{} : {}".format(fam, name.AsString() if name else "")


_symbol_label = symbol_label   # old name, kept for compatibility


def tag_types_for(doc, category_id):
    """List of (ElementId, label) tag types for a model category, project default first.
    Multi-category tags are added at the end."""
    tag_bic = TAG_MAP.get(_id_int(category_id))
    options = []
    if tag_bic is not None:
        syms = FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(tag_bic)
        options = [(s.Id, symbol_label(s)) for s in syms]
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
        for s in sorted(msyms, key=lambda x: symbol_label(x).lower()):
            options.append((s.Id, symbol_label(s) + u"  (multi-category)"))
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
    dx, dy = b[0] - ax, b[1] - ay
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


def _orient(a, b, c):
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(v) < 1e-9:
        return 0
    return 1 if v > 0 else -1


def _on_seg(a, b, p):
    return (min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9 and
            min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9)


def _segments_cross(p1, p2, q1, q2):
    """True if the two segments touch or cross (collinear overlap counts)."""
    if (max(p1[0], p2[0]) < min(q1[0], q2[0]) - 1e-9 or max(q1[0], q2[0]) < min(p1[0], p2[0]) - 1e-9 or
            max(p1[1], p2[1]) < min(q1[1], q2[1]) - 1e-9 or max(q1[1], q2[1]) < min(p1[1], p2[1]) - 1e-9):
        return False
    o1, o2 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    o3, o4 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_seg(p1, p2, q1): return True
    if o2 == 0 and _on_seg(p1, p2, q2): return True
    if o3 == 0 and _on_seg(q1, q2, p1): return True
    if o4 == 0 and _on_seg(q1, q2, p2): return True
    return False


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


def _mid(shape):
    if shape[0] == "curve":
        return ((shape[1][0] + shape[2][0]) * 0.5, (shape[1][1] + shape[2][1]) * 0.5)
    return shape[1]


def _closest_on_shape(p, shape):
    if shape[0] == "curve":
        return _closest_on_segment(p, shape[1], shape[2])
    return shape[1]


def _tag_box(tag, view, frame):
    """(umin, umax, vmin, vmax) of the tag's bounding box in view 2D.
    NOTE: Revit stretches this box to the tagged element, see measure()."""
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


def _text_box_from_aabb(max_rel, min_rel, frame):
    """Tag text rectangle (umin, umax, vmin, vmax) relative to the head point.

    max_rel / min_rel: corners of the tag's model-axis bounding box relative to its head.
    The tag is a flat w x h rectangle in the view plane (directions r, u), so each model
    axis extent is  w*|r| + h*|u|.  Solving that (least squares) gives w and h exactly,
    in plans (r = X, u = Y) and in rotated / isometric 3D views alike."""
    ext = max_rel - min_rel
    cen = (max_rel + min_rel) * 0.5
    r, u = frame.r, frame.u
    ar = (abs(r.X), abs(r.Y), abs(r.Z))
    au = (abs(u.X), abs(u.Y), abs(u.Z))
    e = (ext.X, ext.Y, ext.Z)
    a11 = sum(x * x for x in ar)
    a22 = sum(x * x for x in au)
    a12 = sum(ar[i] * au[i] for i in range(3))
    b1 = sum(ar[i] * e[i] for i in range(3))
    b2 = sum(au[i] * e[i] for i in range(3))
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-9:
        return None
    w = (b1 * a22 - b2 * a12) / det
    h = (a11 * b2 - a12 * b1) / det
    cu = cen.DotProduct(r)
    cv = cen.DotProduct(u)
    return (cu - w * 0.5, cu + w * 0.5, cv - h * 0.5, cv + h * 0.5)


# ---------------------------------------------------------------
# Step 1: collect
# ---------------------------------------------------------------
def collect(view, elements, tag_type_by_cat):
    """items (dicts) for elements that have a tag type; and the skipped elements."""
    frame = ViewFrame(view)
    items, skipped = [], []
    for el in elements:
        type_id = tag_type_by_cat.get(_id_int(el.Category.Id)) if el.Category else None
        shape = _shape(el, frame)
        if type_id is None or shape is None:
            skipped.append(el)
            continue
        items.append({"el": el, "type": type_id, "shape": shape, "mid": _mid(shape)})
    return items, skipped


# ---------------------------------------------------------------
# Step 2: measure the real tag text size
# ---------------------------------------------------------------
def measure(doc, view, items):
    """Adds it['off'] = (umin, umax, vmin, vmax) of the tag text relative to its head point,
    and it['text']. Uses temporary tags inside a transaction that is rolled back.

    Revit's tag bounding box always stretches to the tagged element, so each tag is moved
    far up-right (its box max corner is then the text's max corner) and far down-left
    (box min corner = text min corner)."""
    frame = ViewFrame(view)
    paper = float(view.Scale or 100) / MM_PER_FT
    far = 300.0 * paper
    failed = []
    t = Transaction(doc, "Smart Tag measure")
    t.Start()
    try:
        live = []
        for it in items:
            depth = 0.0
            try:
                tag = IndependentTag.Create(doc, it["type"], view.Id, Reference(it["el"]), False,
                                            TagOrientation.Horizontal, frame.to3d(it["mid"], depth))
                it["_tmp"] = tag
                live.append(it)
            except Exception as ex:
                failed.append((it["el"], str(ex)))
        doc.Regenerate()
        for it in live:
            it["text"] = it["_tmp"].TagText or u""
        boxes = {}
        for key, sgn in (("hi", 1.0), ("lo", -1.0)):
            for it in live:
                m = it["mid"]
                it["_tmp"].TagHeadPosition = frame.to3d((m[0] + sgn * far, m[1] + sgn * far), 0.0)
            doc.Regenerate()
            for it in live:
                bb = it["_tmp"].get_BoundingBox(view)
                boxes.setdefault(id(it), {})[key] = (bb, it["_tmp"].TagHeadPosition)
        for it in live:
            b = boxes[id(it)]
            (bhi, hhi), (blo, hlo) = b["hi"], b["lo"]
            off = None
            if bhi is not None and blo is not None:
                off = _text_box_from_aabb(bhi.Max - hhi, blo.Min - hlo, frame)
            # sanity: fall back to a typical 20 x 4 mm label if the numbers look wrong
            if off is None or not (0 < off[1] - off[0] < 400 * paper) or not (0 < off[3] - off[2] < 100 * paper):
                off = (-10 * paper, 10 * paper, -2 * paper, 2 * paper)
            it["off"] = off
            del it["_tmp"]
    finally:
        t.RollBack()
    good = [it for it in items if "off" in it]
    return good, failed


# ---------------------------------------------------------------
# Step 3: layout (pure geometry, no Revit calls -> also used by the live preview)
# ---------------------------------------------------------------
def _poly_len(L):
    a, e, t = L["end"], L["elbow"], L["tip"]
    return math.hypot(e[0] - a[0], e[1] - a[1]) + math.hypot(t[0] - e[0], t[1] - e[1])


def _seg_hits_rect(a, b, rect):
    """True if segment a-b passes through the rectangle (umin, umax, vmin, vmax)."""
    u0, u1, v0, v1 = rect
    if max(a[0], b[0]) <= u0 or min(a[0], b[0]) >= u1 or max(a[1], b[1]) <= v0 or min(a[1], b[1]) >= v1:
        return False
    for p in (a, b):
        if u0 < p[0] < u1 and v0 < p[1] < v1:
            return True
    corners = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
    for i in range(4):
        c, d = corners[i], corners[(i + 1) % 4]
        o1, o2 = _orient(a, b, c), _orient(a, b, d)
        o3, o4 = _orient(c, d, a), _orient(c, d, b)
        if o1 * o2 < 0 and o3 * o4 < 0:
            return True
    return False


def _leaders_cross(L1, L2):
    s1 = ((L1["end"], L1["elbow"]), (L1["elbow"], L1["tip"]))
    s2 = ((L2["end"], L2["elbow"]), (L2["elbow"], L2["tip"]))
    for a, b in s1:
        for c, d in s2:
            if _segments_cross(a, b, c, d):
                return True
    return False


def compute_layout(items, click, angle=90, side_mode="auto", gap=0.0, shoulder=0.0,
                   step=None, optimize=True):
    """Pure 2D layout. click = (u, v). gap / shoulder / step in model feet.
    step: horizontal distance between the vertical legs in 90 mode (a 'ladder', so
          straight legs never lie on top of each other). Default = half a shoulder.
    Returns {side, col_u, rows: [ {item, v, head, rect, elbow, end, tip} ], crossings}."""
    n = len(items)
    if n == 0:
        return {"rows": [], "side": 1, "col_u": click[0], "crossings": 0}
    diag = (angle == 45)
    free = (angle == 0)          # Free: every tag takes the straight line that suits it
    us = sorted(it["mid"][0] for it in items)
    vs = sorted(it["mid"][1] for it in items)
    cu, cv = us[n // 2], vs[n // 2]          # median: one long pipe must not drag the centre
    if side_mode == "right":
        side = 1
    elif side_mode == "left":
        side = -1
    else:
        side = 1 if click[0] >= cu else -1
    grow_up = click[1] > cv

    heights = [it["off"][3] - it["off"][2] for it in items]
    slot_v = [0.0] * n

    def stack(order):
        """Slot centres for this order: every tag touches the next one (+ gap)."""
        hs = [heights[order[k]] for k in range(n)]
        total = sum(hs) + gap * (n - 1)
        top = click[1] + total if grow_up else click[1]
        for k in range(n):
            slot_v[k] = top - hs[k] * 0.5
            top -= hs[k] + gap

    # 90 mode: each slot gets its own vertical line. The slot at the outer end of the
    # stack gets the line farthest from the column, so its shoulder passes over the others.
    if step is None:
        step = shoulder * 0.5
    stagger = []
    for k in range(n):
        if diag or free:
            stagger.append(0.0)
        else:
            stagger.append(step * ((n - 1 - k) if grow_up else k))

    def leader(i, k, col_u):
        """Leader of item i in slot k: exact angle; 'viol' > 0 if it pokes into the column."""
        shape = items[i]["shape"]
        vk = slot_v[k]
        elbow_u = col_u - side * (shoulder + stagger[k])
        E0 = (elbow_u, vk)
        target = _closest_on_shape(E0, shape)
        if free:
            # short horizontal shoulder, then a straight line to the nearest point of the element
            if abs(target[1] - vk) < 1e-7:
                return {"end": target, "elbow": ((target[0] + elbow_u) * 0.5, vk),
                        "tip": (col_u, vk), "viol": 0.0}
            return {"end": target, "elbow": E0, "tip": (col_u, vk), "viol": 0.0}
        dv = 1.0 if target[1] > vk else -1.0
        end = None
        E = E0
        if abs(target[1] - vk) < 1e-7:
            end = target                                   # same height: straight leader
            E = ((end[0] + elbow_u) * 0.5, vk)
        elif shape[0] == "curve":
            d = (-side / math.sqrt(2), dv / math.sqrt(2)) if diag else (0.0, dv)
            end = _ray_hits_segment(E0, d, shape[1], shape[2])
        if end is None:
            # the leg from the shared elbow line misses: start at the element and keep the angle
            end = target if shape[0] == "curve" else shape[1]
            rise = abs(vk - end[1])
            if rise < 1e-7:
                E = ((end[0] + elbow_u) * 0.5, vk)
            else:
                E = (end[0] + side * rise if diag else end[0], vk)
        # > 0: the elbow / end would sit closer than one shoulder to the tag column
        limit = col_u - side * shoulder
        viol = side * (E[0] - limit)
        if side * (end[0] - limit) > viol:
            viol = side * (end[0] - limit)
        return {"end": end, "elbow": E, "tip": (col_u, vk), "viol": viol}

    # initial order: farthest from the column goes to the outer end of the stack
    base = sorted(range(n), key=lambda i: (side * items[i]["mid"][0], -items[i]["mid"][1]))
    if not grow_up:
        base.reverse()
    widths = [it["off"][1] - it["off"][0] for it in items]
    col = [click[0]]          # tag column edge (a list so the helpers below can change it)

    def fit_column(order):
        """90 / 45: move the column out until every leg fits (Free never moves it)."""
        col[0] = click[0]          # always start again from the click
        if free:
            return
        stack(order)
        for _ in range(4):
            worst = max(leader(order[k], k, col[0])["viol"] for k in range(n))
            if worst <= 1e-6:
                break
            col[0] += side * (worst + 1e-4)

    fit_column(base)
    cache = {}

    def lead(i, k):
        key = (i, k, round(slot_v[k], 6))
        if key not in cache:
            cache[key] = leader(i, k, col[0])
        return cache[key]

    def rects_for(order):
        out = []
        for k in range(n):
            i = order[k]
            w, h = widths[i], heights[i]
            u0, u1 = (col[0], col[0] + w) if side > 0 else (col[0] - w, col[0])
            out.append((u0, u1, slot_v[k] - h * 0.5, slot_v[k] + h * 0.5))
        return out

    W = 1e6

    def total_cost(order):
        stack(order)
        Ls = [lead(order[k], k) for k in range(n)]
        cross = 0
        for a in range(n):
            for b in range(a + 1, n):
                if _leaders_cross(Ls[a], Ls[b]):
                    cross += 1
        # a leader must not run through another tag
        clash = 0
        rs = rects_for(order)
        for a in range(n):
            for b in range(n):
                if a != b and _seg_hits_rect(Ls[a]["end"], Ls[a]["elbow"], rs[b]):
                    clash += 1
        bad = sum(1 for x in Ls if x["viol"] > 1e-6)
        return (cross + clash + bad) * W + sum(_poly_len(x) for x in Ls), cross + clash

    def local_search(order, deadline):
        import time
        best_c, _ = total_cost(order)
        improved = True
        while improved and time.time() < deadline:
            improved = False
            # swaps
            for a in range(n):
                for b in range(a + 1, n):
                    order[a], order[b] = order[b], order[a]
                    c, _ = total_cost(order)
                    if c < best_c - 1e-6:
                        best_c, improved = c, True
                    else:
                        order[a], order[b] = order[b], order[a]
                if time.time() > deadline:
                    break
            # moves: take one tag out and put it in another slot
            for a in range(n):
                for b in range(n):
                    if a == b:
                        continue
                    trial = order[:]
                    x = trial.pop(a)
                    trial.insert(b, x)
                    c, _ = total_cost(trial)
                    if c < best_c - 1e-6:
                        order[:] = trial
                        best_c, improved = c, True
                if time.time() > deadline:
                    break
        return order, best_c

    order = base[:]
    if optimize and n > 1:
        import time
        deadline = time.time() + (0.25 if n <= 6 else 1.5)
        best_order, best_c = local_search(base[:], deadline)
        # a second start (reversed) sometimes escapes a local minimum
        if total_cost(best_order)[1] > 0 and time.time() < deadline:
            o2, c2 = local_search(list(reversed(base)), deadline)
            if c2 < best_c:
                best_order = o2
        order = best_order
        if not free:
            # the column was pushed out for the FIRST order; refit it for the chosen order
            # (from the click again) and polish once more, so it sits as close as possible
            old = col[0]
            fit_column(order)
            if abs(col[0] - old) > 1e-9:
                cache.clear()
                order, _c = local_search(order, time.time() + (0.15 if n <= 6 else 0.6))

    # final stack for the chosen order (heights may differ per tag), legs re-checked
    fit_column(order)
    cache.clear()
    crossings = total_cost(order)[1]
    L = [lead(order[k], k) for k in range(n)]
    col_u = col[0]

    rows = []
    for k in range(n):
        it = items[order[k]]
        off = it["off"]
        vk = slot_v[k]
        head_u = (col_u - off[0]) if side > 0 else (col_u - off[1])
        head_v = vk - (off[2] + off[3]) * 0.5
        w = off[1] - off[0]
        rect = (col_u, col_u + w) if side > 0 else (col_u - w, col_u)
        h = off[3] - off[2]
        rows.append({"item": it, "v": vk, "head": (head_u, head_v),
                     "rect": (rect[0], rect[1], vk - h * 0.5, vk + h * 0.5),
                     "elbow": L[k]["elbow"], "end": L[k]["end"], "tip": L[k]["tip"]})
    return {"rows": rows, "side": side, "col_u": col_u, "crossings": crossings}


# ---------------------------------------------------------------
# Step 4: create the tags
# ---------------------------------------------------------------
def apply_layout(doc, view, layout, depth=0.0, transaction_name="Smart Tag"):
    frame = ViewFrame(view)
    created, failed = [], []
    t = Transaction(doc, transaction_name)
    t.Start()
    try:
        for row in layout["rows"]:
            it = row["item"]
            try:
                ref = Reference(it["el"])
                tag = IndependentTag.Create(doc, it["type"], view.Id, ref, False,
                                            TagOrientation.Horizontal, frame.to3d(row["head"], depth))
                tag.TagHeadPosition = frame.to3d(row["head"], depth)
                tag.HasLeader = True
                if tag.CanLeaderEndConditionBeAssigned(LeaderEndCondition.Free):
                    tag.LeaderEndCondition = LeaderEndCondition.Free
                tag.SetLeaderEnd(ref, frame.to3d(row["end"], depth))
                tag.SetLeaderElbow(ref, frame.to3d(row["elbow"], depth))
                created.append(tag.Id)
            except Exception as ex:
                failed.append((it["el"], str(ex)))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return created, failed


def place_tags(doc, view, elements, pick_point, tag_type_by_cat, angle=90,
               gap_mm=1.0, shoulder_mm=3.0, side_mode="auto", transaction_name="Smart Tag"):
    """All steps at once (no preview). Returns dict with created ids, failed and skipped."""
    frame = ViewFrame(view)
    paper = float(view.Scale or 100) / MM_PER_FT
    items, skipped = collect(view, elements, tag_type_by_cat)
    items, failed = measure(doc, view, items)
    layout = compute_layout(items, frame.to2d(pick_point), angle=angle, side_mode=side_mode,
                            gap=gap_mm * paper, shoulder=shoulder_mm * paper)
    created, failed2 = apply_layout(doc, view, layout, frame.depth(pick_point), transaction_name)
    return {"created": created, "failed": failed + failed2, "skipped": skipped,
            "side": "right" if layout["side"] > 0 else "left", "angle": angle,
            "crossings": layout["crossings"], "layout": layout}
