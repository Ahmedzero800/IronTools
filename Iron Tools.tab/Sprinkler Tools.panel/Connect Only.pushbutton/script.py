# -*- coding: utf-8 -*-
"""
pyRevit PushButton Script for Revit 2025
Title: Connect Only (Fixed Sprinkler Position - 25mm Nipple)
Description:
- Keeps each selected Sprinkler's 3D position (X, Y, Z) and Offset from Host 100% FIXED
  (does NOT move or align sprinklers to the pipe centerline).
- Ignores sprinklers that are already connected.
- Automatically shifts any conflicting inline Pipe Reducer along the line so that
  the LARGER diameter pipe increases in length, clearing space for the connection.
- Automatically deletes any Pipe Cap at the end of a pipe and extends the pipe along
  its axis to reach the longitudinal projection of the furthest sprinkler.
- Connects each Sprinkler to its target Pipe (Branch or Main, Horizontal or Sloped,
  Upright or Pendent):
  * If the sprinkler is already vertically aligned over/under the pipe centerline,
    connects directly via a vertical 25mm Nipple + Tee/Reducer (or End Elbow).
  * If the sprinkler is horizontally offset from the pipe centerline, routes a
    horizontal 25mm branch arm from the pipe (Tee/Reducer or End Elbow) to the
    sprinkler's XY position, turns vertically with a 90° Elbow into a 25mm Nipple,
    and connects to the sprinkler without moving the sprinkler at all.
- Automatically selects/highlights any unconnected sprinklers in the Revit model
  upon completion so the user can inspect them immediately.
"""

__title__ = "Connect Only\n(No Align)"
__author__ = "BIM Automation"
__doc__ = (
    "Connects Upright & Pendent Sprinklers to Pipes WITHOUT moving the sprinklers "
    "(X, Y, Z & Offset from Host stay 100% fixed), using 25mm Nipple/Branch + Tee/Elbow/Reducer."
)

from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ConnectorType,
    Domain,
    ElementId,
    ElementTransformUtils,
    FamilyInstance,
    Line,
    PartType,
    SubTransaction,
    Transaction,
    UnitTypeId,
    UnitUtils,
    XYZ,
)
from Autodesk.Revit.DB.Plumbing import Pipe, PlumbingUtils
import irontools_ui as dui

TOOL_TITLE = "Connect Only (No Align)"
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

# ---------------------------------------------------------------------------
# Configuration Constants
# ---------------------------------------------------------------------------
# Fixed Nipple & Branch Arm Diameter in Millimeters (always 25 mm)
NIPPLE_DIAMETER_MM = 25.0
NIPPLE_DIAMETER_FT = UnitUtils.ConvertToInternalUnits(NIPPLE_DIAMETER_MM, UnitTypeId.Millimeters)

# Maximum perpendicular XY distance (ft) to match a sprinkler to a pipe (~15 ft / 4.5 m)
MAX_XY_SEARCH_DIST_FT = 15.0

# Maximum longitudinal extension distance (ft) past an open/capped pipe end (~16.4 ft / 5 m)
MAX_PIPE_EXTENSION_FT = 16.4

# Distance threshold (ft) inside an open/capped pipe end to treat the connection as an End Elbow (~0.5 ft / 150 mm)
PIPE_END_TOLERANCE_FT = 0.50

# Horizontal XY tolerance (ft) under which the sprinkler is considered directly over/under the pipe centerline (~15 mm)
DIRECT_VERTICAL_XY_TOL_FT = UnitUtils.ConvertToInternalUnits(15.0, UnitTypeId.Millimeters)

# Clearance threshold (ft) around an existing inline Reducer (~0.65 ft / 200 mm)
REDUCER_CONFLICT_DIST_FT = UnitUtils.ConvertToInternalUnits(200.0, UnitTypeId.Millimeters)

# Target clearance (ft) after shifting a conflicting Reducer toward the smaller pipe (~0.82 ft / 250 mm)
REDUCER_SHIFT_CLEARANCE_FT = UnitUtils.ConvertToInternalUnits(250.0, UnitTypeId.Millimeters)

# Minimum vertical distance (ft) required to create a vertical 25mm Nipple (~0.05 ft / 15 mm)
MIN_NIPPLE_HEIGHT_FT = 0.05

# Minimum distance from pipe endpoints required by PlumbingUtils.BreakCurve (~0.05 ft / 15 mm)
MIN_BREAK_DIST_FT = 0.05

# Fallback to direct fitting connection if vertical clearance is too small for a 25mm Nipple
FALLBACK_DIRECT_FIT_WHEN_TOO_CLOSE = False


# ---------------------------------------------------------------------------
# Revit 2025 Compatibility Helpers
# ---------------------------------------------------------------------------
def get_id_value(element_id):
    """Returns 64-bit integer ID in Revit 2024/2025+ or 32-bit in older versions."""
    if element_id is None:
        return -1
    return element_id.Value if hasattr(element_id, "Value") else element_id.IntegerValue


def is_category(elem, builtin_cat):
    """Checks if an element belongs to the given BuiltInCategory."""
    if elem is None or elem.Category is None:
        return False
    return get_id_value(elem.Category.Id) == int(builtin_cat)


class SprinklerAndPipeSelectionFilter(ISelectionFilter):
    """Allows selecting Sprinklers, Pipes, and Pipe Fittings."""

    def AllowElement(self, elem):
        return (
            is_category(elem, BuiltInCategory.OST_Sprinklers)
            or is_category(elem, BuiltInCategory.OST_PipeCurves)
            or is_category(elem, BuiltInCategory.OST_PipeFitting)
        )

    def AllowReference(self, reference, position):
        return False


# ---------------------------------------------------------------------------
# Connector, Cap & Reducer Inspection Helpers
# ---------------------------------------------------------------------------
def get_piping_connectors(elem):
    """Returns a list of Piping Domain connectors from a FamilyInstance or MEPCurve."""
    connectors = []
    conn_mgr = None

    if isinstance(elem, FamilyInstance) and elem.MEPModel:
        conn_mgr = elem.MEPModel.ConnectorManager
    elif hasattr(elem, "ConnectorManager") and elem.ConnectorManager:
        conn_mgr = elem.ConnectorManager

    if conn_mgr is None:
        return connectors

    for conn in conn_mgr.Connectors:
        if conn.Domain == Domain.DomainPiping and (
            conn.ConnectorType == ConnectorType.End
            or conn.ConnectorType == ConnectorType.Curve
            or conn.ConnectorType == ConnectorType.Physical
        ):
            connectors.append(conn)
    return connectors


def is_sprinkler_already_connected(sprinkler):
    """Returns True if any piping connector on the sprinkler is already connected."""
    conns = get_piping_connectors(sprinkler)
    return any(c.IsConnected for c in conns)


def get_sprinkler_connector(sprinkler):
    """Returns the primary unconnected piping connector of a Sprinkler."""
    conns = get_piping_connectors(sprinkler)
    if not conns:
        return None
    for c in conns:
        if not c.IsConnected:
            return c
    return conns[0]


def get_pipe_diameter(pipe):
    """Returns the diameter (in internal ft) of a Pipe."""
    p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    if p:
        return p.AsDouble()
    conns = get_piping_connectors(pipe)
    if conns:
        return conns[0].Radius * 2.0
    return 0.0


def is_cap_fitting(elem):
    """Determines whether an element is a Pipe Cap fitting."""
    if not is_category(elem, BuiltInCategory.OST_PipeFitting):
        return False
    if isinstance(elem, FamilyInstance) and elem.MEPModel:
        try:
            if elem.MEPModel.PartType == PartType.Cap:
                return True
        except Exception:
            pass
    conns = get_piping_connectors(elem)
    return len(conns) == 1


def is_transition_reducer_fitting(elem):
    """Determines whether an element is a Pipe Reducer / Transition fitting."""
    if not is_category(elem, BuiltInCategory.OST_PipeFitting):
        return False
    conns = get_piping_connectors(elem)
    if len(conns) != 2:
        return False
    if isinstance(elem, FamilyInstance) and elem.MEPModel:
        try:
            if elem.MEPModel.PartType == PartType.Transition:
                return True
        except Exception:
            pass
    return abs(conns[0].Radius - conns[1].Radius) > 1e-5


def get_connected_cap_at_connector(pipe, end_conn):
    """If end_conn is connected to a Pipe Cap, returns the Cap element; otherwise None."""
    if end_conn is None or not end_conn.IsConnected:
        return None
    for ref_conn in end_conn.AllRefs:
        owner = ref_conn.Owner
        if owner is not None and get_id_value(owner.Id) != get_id_value(pipe.Id):
            if is_cap_fitting(owner):
                return owner
    return None


def is_pipe_end_open_or_capped(pipe, end_index):
    """
    Checks if endpoint `end_index` (0 or 1) of `pipe` is either unconnected
    or connected only to a removable Cap.
    Returns (is_extendable, end_connector, cap_element_or_None).
    """
    curve = pipe.Location.Curve
    end_pt = curve.GetEndPoint(end_index)

    best_conn = None
    min_dist = float("inf")
    for conn in get_piping_connectors(pipe):
        d = conn.Origin.DistanceTo(end_pt)
        if d < min_dist:
            min_dist = d
            best_conn = conn

    if best_conn is None:
        return (False, None, None)

    if not best_conn.IsConnected:
        return (True, best_conn, None)

    cap_elem = get_connected_cap_at_connector(pipe, best_conn)
    if cap_elem is not None:
        return (True, best_conn, cap_elem)

    return (False, best_conn, None)


# ---------------------------------------------------------------------------
# Full 3D Position (X, Y, Z) & Offset Lock for Sprinklers
# ---------------------------------------------------------------------------
def get_sprinkler_full_state(sprinkler):
    """Captures the Sprinkler's exact 3D Point (X, Y, Z) and Offset from Host."""
    orig_pt = sprinkler.Location.Point if sprinkler.Location else None
    offset_param = sprinkler.get_Parameter(BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    orig_offset = offset_param.AsDouble() if (offset_param and not offset_param.IsReadOnly) else None
    return orig_pt, orig_offset


def restore_sprinkler_full_state(doc, sprinkler, orig_pt, orig_offset):
    """Ensures the Sprinkler's X, Y, Z coordinates and Offset from Host never change."""
    if orig_offset is not None:
        offset_param = sprinkler.get_Parameter(BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
        if offset_param and not offset_param.IsReadOnly:
            if abs(offset_param.AsDouble() - orig_offset) > 1e-6:
                offset_param.Set(orig_offset)

    if orig_pt is not None and sprinkler.Location:
        curr_pt = sprinkler.Location.Point
        drift_vec = orig_pt - curr_pt
        if drift_vec.GetLength() > 1e-6:
            ElementTransformUtils.MoveElement(doc, sprinkler.Id, drift_vec)


# ---------------------------------------------------------------------------
# Reducer Conflict Resolution (Lengthening the Larger Diameter Pipe)
# ---------------------------------------------------------------------------
def get_reducer_and_adjacent_pipes(pipe, end_conn):
    """
    If `end_conn` on `pipe` is connected to a Reducer (Transition fitting),
    finds the Reducer and the two pipes (`large_pipe`, `small_pipe`) on either side.
    Returns (reducer_elem, large_pipe, small_pipe) or (None, None, None).
    """
    if end_conn is None or not end_conn.IsConnected:
        return (None, None, None)

    reducer_elem = None
    for ref_conn in end_conn.AllRefs:
        owner = ref_conn.Owner
        if owner is not None and get_id_value(owner.Id) != get_id_value(pipe.Id):
            if is_transition_reducer_fitting(owner):
                reducer_elem = owner
                break

    if reducer_elem is None:
        return (None, None, None)

    other_pipe = None
    for r_conn in get_piping_connectors(reducer_elem):
        for ref_c in r_conn.AllRefs:
            cand = ref_c.Owner
            if (
                cand is not None
                and get_id_value(cand.Id) != get_id_value(reducer_elem.Id)
                and get_id_value(cand.Id) != get_id_value(pipe.Id)
                and isinstance(cand, Pipe)
            ):
                other_pipe = cand
                break

    if other_pipe is None:
        return (None, None, None)

    d1 = get_pipe_diameter(pipe)
    d2 = get_pipe_diameter(other_pipe)
    if abs(d1 - d2) < 1e-5:
        return (None, None, None)

    if d1 > d2:
        return (reducer_elem, pipe, other_pipe)
    else:
        return (reducer_elem, other_pipe, pipe)


def shift_conflicting_reducer_to_lengthen_larger_pipe(doc, pipe, p_on_pipe, active_pipes):
    """
    Checks if `p_on_pipe` is too close to an inline Reducer at either end of `pipe`.
    If so, shifts the Reducer toward the SMALLER diameter pipe so that the LARGER
    diameter pipe increases in length, and returns (`large_pipe`, `new_p_on_pipe`).
    """
    if not pipe.IsValidObject:
        return pipe, p_on_pipe

    curve = pipe.Location.Curve
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)

    for end_idx, end_pt in ((0, p0), (1, p1)):
        if p_on_pipe.DistanceTo(end_pt) > REDUCER_CONFLICT_DIST_FT:
            continue

        end_conn, _ = get_closest_connector_to_point(pipe, end_pt, only_unconnected=False)
        reducer_elem, large_pipe, small_pipe = get_reducer_and_adjacent_pipes(pipe, end_conn)
        if reducer_elem is None or large_pipe is None or small_pipe is None:
            continue

        if not any(get_id_value(p.Id) == get_id_value(small_pipe.Id) for p in active_pipes):
            active_pipes.append(small_pipe)
        if not any(get_id_value(p.Id) == get_id_value(large_pipe.Id) for p in active_pipes):
            active_pipes.append(large_pipe)

        l_curve = large_pipe.Location.Curve
        s_curve = small_pipe.Location.Curve
        red_pt = reducer_elem.Location.Point if reducer_elem.Location else end_pt

        l_p0, l_p1 = l_curve.GetEndPoint(0), l_curve.GetEndPoint(1)
        l_far = l_p0 if l_p0.DistanceTo(red_pt) > l_p1.DistanceTo(red_pt) else l_p1

        s_p0, s_p1 = s_curve.GetEndPoint(0), s_curve.GetEndPoint(1)
        s_far = s_p0 if s_p0.DistanceTo(red_pt) > s_p1.DistanceTo(red_pt) else s_p1

        axis_vec = s_far - l_far
        total_len = axis_vec.GetLength()
        if total_len < 1e-6:
            continue
        u_hat = axis_vec.Normalize()

        d_sp = (p_on_pipe - l_far).DotProduct(u_hat)
        d_red = (red_pt - l_far).DotProduct(u_hat)
        d_s_far = (s_far - l_far).DotProduct(u_hat)

        target_d_red = max(d_sp, d_red) + REDUCER_SHIFT_CLEARANCE_FT
        shift_dist = target_d_red - d_red

        min_remaining_small_len = MIN_BREAK_DIST_FT * 4.0
        if shift_dist <= 1e-4 or (d_s_far - target_d_red) < min_remaining_small_len:
            continue

        shift_vec = u_hat.Multiply(shift_dist)

        moved = False
        sub_move = SubTransaction(doc)
        sub_move.Start()
        try:
            ElementTransformUtils.MoveElement(doc, reducer_elem.Id, shift_vec)
            doc.Regenerate()
            sub_move.Commit()
            moved = True
        except Exception:
            if sub_move.HasStarted():
                sub_move.RollBack()

        if not moved:
            sub_rebuild = SubTransaction(doc)
            sub_rebuild.Start()
            try:
                l_near = l_p1 if l_far.IsAlmostEqualTo(l_p0) else l_p0
                s_near = s_p1 if s_far.IsAlmostEqualTo(s_p0) else s_p0
                doc.Delete(reducer_elem.Id)
                doc.Regenerate()

                new_l_near = l_near + shift_vec
                new_s_near = s_near + shift_vec

                if l_far.IsAlmostEqualTo(l_p0):
                    large_pipe.Location.Curve = Line.CreateBound(l_far, new_l_near)
                else:
                    large_pipe.Location.Curve = Line.CreateBound(new_l_near, l_far)

                if s_far.IsAlmostEqualTo(s_p1):
                    small_pipe.Location.Curve = Line.CreateBound(new_s_near, s_far)
                else:
                    small_pipe.Location.Curve = Line.CreateBound(s_far, new_s_near)

                doc.Regenerate()
                c_large, _ = get_closest_connector_to_point(large_pipe, new_l_near, only_unconnected=True)
                c_small, _ = get_closest_connector_to_point(small_pipe, new_s_near, only_unconnected=True)
                if c_large is not None and c_small is not None:
                    doc.Create.NewTransitionFitting(c_large, c_small)
                    doc.Regenerate()
                    sub_rebuild.Commit()
                    moved = True
                else:
                    sub_rebuild.RollBack()
            except Exception:
                if sub_rebuild.HasStarted():
                    sub_rebuild.RollBack()

        if moved:
            _, new_p_line, _, _, _ = project_xy_onto_pipe_unbounded(p_on_pipe, large_pipe)
            if new_p_line is not None:
                return large_pipe, new_p_line

    return pipe, p_on_pipe


# ---------------------------------------------------------------------------
# Unbounded Line Projection & Pipe Matching
# ---------------------------------------------------------------------------
def project_xy_onto_pipe_unbounded(point_xyz, pipe):
    """
    Projects a 3D point perpendicular onto a Pipe's infinite line in the XY plane.
    Returns (perp_xy_dist, p_on_line, p_on_segment, t_raw, overhang_ft).
    """
    loc = pipe.Location
    if loc is None or not hasattr(loc, "Curve") or loc.Curve is None:
        return (float("inf"), None, None, 0.0, float("inf"))

    curve = loc.Curve
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)

    vx = p1.X - p0.X
    vy = p1.Y - p0.Y
    len_sq_xy = vx * vx + vy * vy

    if len_sq_xy < 1e-10:
        return (float("inf"), None, None, 0.0, float("inf"))

    len_xy = len_sq_xy ** 0.5
    wx = point_xyz.X - p0.X
    wy = point_xyz.Y - p0.Y

    t_raw = (wx * vx + wy * vy) / len_sq_xy
    t_clamped = max(0.0, min(1.0, t_raw))

    lx = p0.X + t_raw * (p1.X - p0.X)
    ly = p0.Y + t_raw * (p1.Y - p0.Y)
    lz = p0.Z + t_raw * (p1.Z - p0.Z)
    p_on_line = XYZ(lx, ly, lz)

    cx = p0.X + t_clamped * (p1.X - p0.X)
    cy = p0.Y + t_clamped * (p1.Y - p0.Y)
    cz = p0.Z + t_clamped * (p1.Z - p0.Z)
    p_on_segment = XYZ(cx, cy, cz)

    dx = point_xyz.X - lx
    dy = point_xyz.Y - ly
    perp_xy_dist = (dx * dx + dy * dy) ** 0.5

    if t_raw < 0.0:
        overhang_ft = -t_raw * len_xy
    elif t_raw > 1.0:
        overhang_ft = (t_raw - 1.0) * len_xy
    else:
        overhang_ft = 0.0

    return (perp_xy_dist, p_on_line, p_on_segment, t_raw, overhang_ft)


def find_target_pipe_for_sprinkler(point_xyz, pipes, allow_extension=True):
    """
    Finds the best pipe for a sprinkler in the XY plane.
    Returns (best_pipe, best_p_line, best_t_raw, best_perp_dist).
    """
    best_pipe = None
    best_score = float("inf")
    best_p_line = None
    best_t_raw = 0.0
    best_perp_dist = float("inf")

    for pipe in pipes:
        if not pipe.IsValidObject:
            continue
        perp_dist, p_on_line, p_on_seg, t_raw, overhang_ft = project_xy_onto_pipe_unbounded(
            point_xyz, pipe
        )
        if p_on_line is None or perp_dist > MAX_XY_SEARCH_DIST_FT:
            continue

        if t_raw < 0.0:
            can_ext, _, _ = is_pipe_end_open_or_capped(pipe, 0)
            if not (allow_extension and can_ext and overhang_ft <= MAX_PIPE_EXTENSION_FT):
                continue
        elif t_raw > 1.0:
            can_ext, _, _ = is_pipe_end_open_or_capped(pipe, 1)
            if not (allow_extension and can_ext and overhang_ft <= MAX_PIPE_EXTENSION_FT):
                continue

        score = perp_dist + (0.25 * overhang_ft)
        if score < best_score:
            best_score = score
            best_pipe = pipe
            best_p_line = p_on_line
            best_t_raw = t_raw
            best_perp_dist = perp_dist

    return best_pipe, best_p_line, best_t_raw, best_perp_dist


def get_closest_connector_to_point(elem, pt, only_unconnected=True):
    """Returns the connector on elem closest to pt."""
    best_conn = None
    min_dist = float("inf")
    for conn in get_piping_connectors(elem):
        if only_unconnected and conn.IsConnected:
            continue
        d = conn.Origin.DistanceTo(pt)
        if d < min_dist:
            min_dist = d
            best_conn = conn
    return best_conn, min_dist


def get_pipe_system_and_type_ids(pipe):
    """Extracts MEPSystemTypeId, PipeTypeId, and ReferenceLevelId from a Pipe."""
    pipe_type_id = pipe.GetTypeId()
    level_id = pipe.ReferenceLevel.Id if pipe.ReferenceLevel else ElementId.InvalidElementId

    sys_type_id = ElementId.InvalidElementId
    if pipe.MEPSystem is not None:
        sys_type_id = pipe.MEPSystem.GetTypeId()
    if sys_type_id == ElementId.InvalidElementId:
        sys_param = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if sys_param:
            sys_type_id = sys_param.AsElementId()

    return sys_type_id, pipe_type_id, level_id


# ---------------------------------------------------------------------------
# Pipe Extension & Cap Removal Before Connection
# ---------------------------------------------------------------------------
def extend_pipe_and_remove_caps_for_sprinklers(doc, pipe, sp_items):
    """
    Given a Pipe and the list of Sprinklers matched to it:
    - Deletes the Cap fitting automatically if present on an open/capped end.
    - Extends the pipe along its exact axis to reach the longitudinal projection
      (p_line) of the furthest sprinkler beyond its end.
    """
    if not sp_items:
        return

    curve = pipe.Location.Curve
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)

    new_p0 = p0
    new_p1 = p1

    min_item = min(sp_items, key=lambda x: x["t_raw"])
    can_ext_0, _, cap_0 = is_pipe_end_open_or_capped(pipe, 0)
    if can_ext_0:
        dist_to_p0 = min_item["p_line"].DistanceTo(p0)
        if min_item["t_raw"] < 0.0 or dist_to_p0 <= PIPE_END_TOLERANCE_FT:
            if cap_0 is not None and cap_0.IsValidObject:
                doc.Delete(cap_0.Id)
                doc.Regenerate()
            if min_item["t_raw"] < 0.0:
                new_p0 = min_item["p_line"]

    max_item = max(sp_items, key=lambda x: x["t_raw"])
    can_ext_1, _, cap_1 = is_pipe_end_open_or_capped(pipe, 1)
    if can_ext_1:
        dist_to_p1 = max_item["p_line"].DistanceTo(p1)
        if max_item["t_raw"] > 1.0 or dist_to_p1 <= PIPE_END_TOLERANCE_FT:
            if cap_1 is not None and cap_1.IsValidObject:
                doc.Delete(cap_1.Id)
                doc.Regenerate()
            if max_item["t_raw"] > 1.0:
                new_p1 = max_item["p_line"]

    if new_p0.DistanceTo(p0) > 1e-5 or new_p1.DistanceTo(p1) > 1e-5:
        if new_p0.DistanceTo(new_p1) > MIN_BREAK_DIST_FT:
            pipe.Location.Curve = Line.CreateBound(new_p0, new_p1)
            doc.Regenerate()


# ---------------------------------------------------------------------------
# 25mm Pipe Creation & Fixed-Sprinkler Connection Helpers
# ---------------------------------------------------------------------------
def create_25mm_pipe_segment(doc, ref_pipe, pt_start, pt_end):
    """
    Creates a 25mm Pipe segment between pt_start and pt_end and returns
    (pipe_seg, start_conn, end_conn).
    """
    sys_type_id, pipe_type_id, level_id = get_pipe_system_and_type_ids(ref_pipe)
    seg = Pipe.Create(doc, sys_type_id, pipe_type_id, level_id, pt_start, pt_end)

    diam_param = seg.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    if diam_param and not diam_param.IsReadOnly:
        diam_param.Set(NIPPLE_DIAMETER_FT)

    doc.Regenerate()
    c_start, _ = get_closest_connector_to_point(seg, pt_start, only_unconnected=True)
    c_end, _ = get_closest_connector_to_point(seg, pt_end, only_unconnected=True)
    return seg, c_start, c_end


def connect_nipple_to_fixed_sprinkler(doc, nipple, sprinkler, orig_pt, orig_offset):
    """
    Connects the sprinkler-side end of the 25mm Nipple to the Sprinkler connector
    while strictly restoring the Sprinkler's full 3D position (X, Y, Z) and Offset.
    """
    doc.Regenerate()
    sp_conn = get_sprinkler_connector(sprinkler)
    if sp_conn is None or sp_conn.IsConnected:
        restore_sprinkler_full_state(doc, sprinkler, orig_pt, orig_offset)
        return True

    nipple_sp_conn, _ = get_closest_connector_to_point(nipple, sp_conn.Origin, only_unconnected=True)
    if nipple_sp_conn is None:
        return False

    diam_diff = abs((nipple_sp_conn.Radius * 2.0) - (sp_conn.Radius * 2.0))

    connected = False
    if diam_diff > 1e-4:
        sub_trans = SubTransaction(doc)
        sub_trans.Start()
        try:
            doc.Create.NewTransitionFitting(nipple_sp_conn, sp_conn)
            sub_trans.Commit()
            connected = True
        except Exception:
            if sub_trans.HasStarted():
                sub_trans.RollBack()

    if not connected:
        try:
            nipple_sp_conn.ConnectTo(sp_conn)
            connected = True
        except Exception:
            connected = False

    doc.Regenerate()
    restore_sprinkler_full_state(doc, sprinkler, orig_pt, orig_offset)
    return connected


def connect_fixed_sprinkler_at_pipe_end(
    doc, pipe, end_index, p_on_pipe, sprinkler, sp_conn, perp_dist, dz, orig_pt, orig_offset
):
    """
    Connects a fixed-position Sprinkler at an open/capped pipe end:
    - If perp_dist <= DIRECT_VERTICAL_XY_TOL_FT: connects via vertical 25mm Nipple + Elbow.
    - If perp_dist > DIRECT_VERTICAL_XY_TOL_FT: connects via horizontal 25mm branch arm
      (Elbow at pipe end) + 90° Elbow + vertical 25mm Nipple to the Sprinkler.
    """
    can_ext, end_conn, cap_elem = is_pipe_end_open_or_capped(pipe, end_index)
    if not can_ext:
        return False

    if cap_elem is not None and cap_elem.IsValidObject:
        doc.Delete(cap_elem.Id)
        doc.Regenerate()

    loc_curve = pipe.Location.Curve
    p0 = loc_curve.GetEndPoint(0)
    p1 = loc_curve.GetEndPoint(1)
    if end_index == 0:
        if p0.DistanceTo(p_on_pipe) > 1e-5 and p1.DistanceTo(p_on_pipe) > MIN_BREAK_DIST_FT:
            pipe.Location.Curve = Line.CreateBound(p_on_pipe, p1)
    else:
        if p1.DistanceTo(p_on_pipe) > 1e-5 and p0.DistanceTo(p_on_pipe) > MIN_BREAK_DIST_FT:
            pipe.Location.Curve = Line.CreateBound(p0, p_on_pipe)

    doc.Regenerate()
    end_conn, _ = get_closest_connector_to_point(pipe, p_on_pipe, only_unconnected=True)
    if end_conn is None:
        return False

    sp_pt = sp_conn.Origin
    p_corner = XYZ(sp_pt.X, sp_pt.Y, p_on_pipe.Z)

    # Case A: Sprinkler is horizontally offset from pipe centerline -> Horizontal 25mm Arm + Vertical 25mm Nipple
    if perp_dist > DIRECT_VERTICAL_XY_TOL_FT and dz >= MIN_NIPPLE_HEIGHT_FT:
        sub_offset = SubTransaction(doc)
        sub_offset.Start()
        try:
            horiz_arm, arm_pipe_conn, _ = create_25mm_pipe_segment(doc, pipe, p_on_pipe, p_corner)
            vert_nipple, _, _ = create_25mm_pipe_segment(doc, pipe, p_corner, sp_pt)

            # 1. Connect Pipe End to Horizontal 25mm Arm with Elbow (+ Reducer if pipe > 25mm)
            doc.Create.NewElbowFitting(end_conn, arm_pipe_conn)
            doc.Regenerate()

            # 2. Connect Horizontal 25mm Arm to Vertical 25mm Nipple at p_corner with Elbow
            arm_corner_conn, _ = get_closest_connector_to_point(horiz_arm, p_corner, only_unconnected=True)
            nipple_corner_conn, _ = get_closest_connector_to_point(vert_nipple, p_corner, only_unconnected=True)
            doc.Create.NewElbowFitting(arm_corner_conn, nipple_corner_conn)
            doc.Regenerate()

            # 3. Connect top/bottom of Vertical 25mm Nipple to Sprinkler (keeping Sprinkler position locked)
            if connect_nipple_to_fixed_sprinkler(doc, vert_nipple, sprinkler, orig_pt, orig_offset):
                sub_offset.Commit()
                return True
            sub_offset.RollBack()
        except Exception:
            if sub_offset.HasStarted():
                sub_offset.RollBack()

    # Case B: Sprinkler is directly over/under pipe centerline (or small offset fallback)
    if dz >= MIN_NIPPLE_HEIGHT_FT:
        sub_vert = SubTransaction(doc)
        sub_vert.Start()
        try:
            nipple_start = XYZ(sp_pt.X, sp_pt.Y, p_on_pipe.Z) if perp_dist <= DIRECT_VERTICAL_XY_TOL_FT else p_on_pipe
            nipple, nipple_pipe_conn, _ = create_25mm_pipe_segment(doc, pipe, nipple_start, sp_pt)
            doc.Create.NewElbowFitting(end_conn, nipple_pipe_conn)
            if connect_nipple_to_fixed_sprinkler(doc, nipple, sprinkler, orig_pt, orig_offset):
                sub_vert.Commit()
                return True
            sub_vert.RollBack()
        except Exception:
            if sub_vert.HasStarted():
                sub_vert.RollBack()

    return False


def connect_fixed_sprinkler_along_pipe_middle(
    doc, pipe, p_on_pipe, sprinkler, sp_conn, perp_dist, dz, active_pipes, orig_pt, orig_offset
):
    """
    Connects a fixed-position Sprinkler along the body of a Pipe:
    - If perp_dist > DIRECT_VERTICAL_XY_TOL_FT: splits pipe at p_on_pipe, connects a
      horizontal 25mm branch arm via Tee (+ Reducer), turns vertically at p_corner
      with an Elbow into a vertical 25mm Nipple, and connects to the Sprinkler.
    - If perp_dist <= DIRECT_VERTICAL_XY_TOL_FT: splits pipe at p_on_pipe and connects
      directly via a vertical 25mm Nipple + Tee (+ Reducer).
    """
    sp_pt = sp_conn.Origin
    p_corner = XYZ(sp_pt.X, sp_pt.Y, p_on_pipe.Z)

    # Case A: Sprinkler is horizontally offset from pipe centerline -> Horizontal 25mm Arm + Vertical 25mm Nipple
    if perp_dist > DIRECT_VERTICAL_XY_TOL_FT and dz >= MIN_NIPPLE_HEIGHT_FT:
        # Attempt A1: Tee + Horizontal 25mm Arm + Elbow + Vertical 25mm Nipple
        sub_tee_arm = SubTransaction(doc)
        sub_tee_arm.Start()
        try:
            horiz_arm, arm_pipe_conn, _ = create_25mm_pipe_segment(doc, pipe, p_on_pipe, p_corner)
            vert_nipple, _, _ = create_25mm_pipe_segment(doc, pipe, p_corner, sp_pt)

            new_pipe_id = PlumbingUtils.BreakCurve(doc, pipe.Id, p_on_pipe)
            new_pipe = doc.GetElement(new_pipe_id)
            doc.Regenerate()

            c1, _ = get_closest_connector_to_point(pipe, p_on_pipe, only_unconnected=True)
            c2, _ = get_closest_connector_to_point(new_pipe, p_on_pipe, only_unconnected=True)

            if c1 is not None and c2 is not None and arm_pipe_conn is not None:
                doc.Create.NewTeeFitting(c1, c2, arm_pipe_conn)
                doc.Regenerate()

                arm_corner_conn, _ = get_closest_connector_to_point(horiz_arm, p_corner, only_unconnected=True)
                nipple_corner_conn, _ = get_closest_connector_to_point(vert_nipple, p_corner, only_unconnected=True)
                doc.Create.NewElbowFitting(arm_corner_conn, nipple_corner_conn)
                doc.Regenerate()

                if connect_nipple_to_fixed_sprinkler(doc, vert_nipple, sprinkler, orig_pt, orig_offset):
                    sub_tee_arm.Commit()
                    active_pipes.append(new_pipe)
                    return True
            sub_tee_arm.RollBack()
        except Exception:
            if sub_tee_arm.HasStarted():
                sub_tee_arm.RollBack()

        # Attempt A2: Takeoff/Tap + Horizontal 25mm Arm + Elbow + Vertical 25mm Nipple
        sub_tap_arm = SubTransaction(doc)
        sub_tap_arm.Start()
        try:
            horiz_arm, arm_pipe_conn, _ = create_25mm_pipe_segment(doc, pipe, p_on_pipe, p_corner)
            vert_nipple, _, _ = create_25mm_pipe_segment(doc, pipe, p_corner, sp_pt)

            doc.Create.NewTakeoffFitting(arm_pipe_conn, pipe)
            doc.Regenerate()

            arm_corner_conn, _ = get_closest_connector_to_point(horiz_arm, p_corner, only_unconnected=True)
            nipple_corner_conn, _ = get_closest_connector_to_point(vert_nipple, p_corner, only_unconnected=True)
            doc.Create.NewElbowFitting(arm_corner_conn, nipple_corner_conn)
            doc.Regenerate()

            if connect_nipple_to_fixed_sprinkler(doc, vert_nipple, sprinkler, orig_pt, orig_offset):
                sub_tap_arm.Commit()
                return True
            sub_tap_arm.RollBack()
        except Exception:
            if sub_tap_arm.HasStarted():
                sub_tap_arm.RollBack()

    # Case B: Sprinkler is directly over/under pipe centerline (or direct Nipple fallback)
    if dz >= MIN_NIPPLE_HEIGHT_FT:
        sub_vert_tee = SubTransaction(doc)
        sub_vert_tee.Start()
        try:
            nipple, nipple_pipe_conn, _ = create_25mm_pipe_segment(doc, pipe, p_on_pipe, sp_pt)
            new_pipe_id = PlumbingUtils.BreakCurve(doc, pipe.Id, p_on_pipe)
            new_pipe = doc.GetElement(new_pipe_id)
            doc.Regenerate()

            c1, _ = get_closest_connector_to_point(pipe, p_on_pipe, only_unconnected=True)
            c2, _ = get_closest_connector_to_point(new_pipe, p_on_pipe, only_unconnected=True)

            if c1 is not None and c2 is not None and nipple_pipe_conn is not None:
                doc.Create.NewTeeFitting(c1, c2, nipple_pipe_conn)
                if connect_nipple_to_fixed_sprinkler(doc, nipple, sprinkler, orig_pt, orig_offset):
                    sub_vert_tee.Commit()
                    active_pipes.append(new_pipe)
                    return True
            sub_vert_tee.RollBack()
        except Exception:
            if sub_vert_tee.HasStarted():
                sub_vert_tee.RollBack()

        sub_vert_tap = SubTransaction(doc)
        sub_vert_tap.Start()
        try:
            nipple, nipple_pipe_conn, _ = create_25mm_pipe_segment(doc, pipe, p_on_pipe, sp_pt)
            doc.Create.NewTakeoffFitting(nipple_pipe_conn, pipe)
            if connect_nipple_to_fixed_sprinkler(doc, nipple, sprinkler, orig_pt, orig_offset):
                sub_vert_tap.Commit()
                return True
            sub_vert_tap.RollBack()
        except Exception:
            if sub_vert_tap.HasStarted():
                sub_vert_tap.RollBack()

    return False


# ---------------------------------------------------------------------------
# Main Execution Entry Point
# ---------------------------------------------------------------------------
def main():
    selected_ids = list(uidoc.Selection.GetElementIds())
    elements = [doc.GetElement(eid) for eid in selected_ids if doc.GetElement(eid) is not None]

    all_sprinklers = [e for e in elements if is_category(e, BuiltInCategory.OST_Sprinklers)]
    pipes = [e for e in elements if is_category(e, BuiltInCategory.OST_PipeCurves) and isinstance(e, Pipe)]

    if not all_sprinklers or not pipes:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element,
                SprinklerAndPipeSelectionFilter(),
                "Select Sprinklers (Upright/Pendent) and Firefighting Pipes, then click Finish",
            )
            elements = [doc.GetElement(r.ElementId) for r in refs]
            all_sprinklers = [e for e in elements if is_category(e, BuiltInCategory.OST_Sprinklers)]
            pipes = [e for e in elements if is_category(e, BuiltInCategory.OST_PipeCurves) and isinstance(e, Pipe)]
        except Exception:
            return

    if not all_sprinklers or not pipes:
        dui.alert(TOOL_TITLE, "Please select at least one Sprinkler and one Firefighting Pipe.",
                  "warning", "Nothing to connect")
        return

    # Filter out sprinklers that are already connected
    sprinklers = []
    skipped_connected_count = 0
    for sp in all_sprinklers:
        if is_sprinkler_already_connected(sp):
            skipped_connected_count += 1
        else:
            sprinklers.append(sp)

    if not sprinklers:
        dui.show_result(TOOL_TITLE, "success", "All sprinklers are already connected",
                        stats=[(str(skipped_connected_count), "Already connected")])
        return

    active_pipes = list(pipes)
    connected_count = 0
    failed_element_ids = []

    t = Transaction(doc, "Connect Sprinklers Without Moving (25mm Nipple)")
    t.Start()

    try:
        # Phase 1: Match each unconnected Sprinkler to its target Pipe WITHOUT moving the sprinkler
        pipe_groups = {}

        for sp in sprinklers:
            sp_conn = get_sprinkler_connector(sp)
            if sp_conn is None:
                failed_element_ids.append(sp.Id)
                continue

            orig_pt, orig_offset = get_sprinkler_full_state(sp)
            target_pipe, p_line, t_raw, perp_dist = find_target_pipe_for_sprinkler(
                sp_conn.Origin, active_pipes, allow_extension=True
            )

            if target_pipe is None or p_line is None:
                failed_element_ids.append(sp.Id)
                continue

            rec = {
                "sprinkler": sp,
                "pipe": target_pipe,
                "p_line": p_line,
                "t_raw": t_raw,
                "perp_dist": perp_dist,
                "orig_pt": orig_pt,
                "orig_offset": orig_offset,
            }
            pid = get_id_value(target_pipe.Id)
            pipe_groups.setdefault(pid, []).append(rec)

        # Phase 2: Sort sprinklers along each pipe & extend open/capped Pipe ends / remove Caps
        ordered_records = []
        for pid, group_items in pipe_groups.items():
            group_items.sort(key=lambda x: x["t_raw"])
            pipe_elem = group_items[0]["pipe"]
            if pipe_elem and pipe_elem.IsValidObject:
                extend_pipe_and_remove_caps_for_sprinklers(doc, pipe_elem, group_items)

            middle_items = []
            end_items = []
            for item in group_items:
                if item["t_raw"] <= 0.01 or item["t_raw"] >= 0.99:
                    end_items.append(item)
                else:
                    middle_items.append(item)
            ordered_records.extend(middle_items + end_items)

        doc.Regenerate()

        # Phase 3: Connect each Sprinkler while keeping its 3D position (X, Y, Z) 100% fixed
        for rec in ordered_records:
            sp = rec["sprinkler"]
            orig_pt = rec["orig_pt"]
            orig_offset = rec["orig_offset"]

            sp_conn = get_sprinkler_connector(sp)
            if sp_conn is None or sp_conn.IsConnected:
                continue

            target_pipe, p_on_pipe, _, perp_dist = find_target_pipe_for_sprinkler(
                sp_conn.Origin, active_pipes, allow_extension=False
            )
            if target_pipe is None or p_on_pipe is None:
                failed_element_ids.append(sp.Id)
                continue

            # Shift conflicting inline Reducer so the LARGER diameter pipe increases in length
            target_pipe, p_on_pipe = shift_conflicting_reducer_to_lengthen_larger_pipe(
                doc, target_pipe, p_on_pipe, active_pipes
            )

            # Recompute perpendicular XY distance and vertical distance
            perp_dist, p_on_pipe, _, _, _ = project_xy_onto_pipe_unbounded(sp_conn.Origin, target_pipe)
            dz = abs(sp_conn.Origin.Z - p_on_pipe.Z)

            loc_curve = target_pipe.Location.Curve
            p0 = loc_curve.GetEndPoint(0)
            p1 = loc_curve.GetEndPoint(1)
            dist_to_p0 = p_on_pipe.DistanceTo(p0)
            dist_to_p1 = p_on_pipe.DistanceTo(p1)

            can_ext_0, _, _ = is_pipe_end_open_or_capped(target_pipe, 0)
            can_ext_1, _, _ = is_pipe_end_open_or_capped(target_pipe, 1)

            connected = False

            if can_ext_0 and dist_to_p0 <= PIPE_END_TOLERANCE_FT and dist_to_p0 <= dist_to_p1:
                connected = connect_fixed_sprinkler_at_pipe_end(
                    doc, target_pipe, 0, p_on_pipe, sp, sp_conn, perp_dist, dz, orig_pt, orig_offset
                )
            elif can_ext_1 and dist_to_p1 <= PIPE_END_TOLERANCE_FT:
                connected = connect_fixed_sprinkler_at_pipe_end(
                    doc, target_pipe, 1, p_on_pipe, sp, sp_conn, perp_dist, dz, orig_pt, orig_offset
                )

            loc_curve = target_pipe.Location.Curve
            dist_to_p0 = p_on_pipe.DistanceTo(loc_curve.GetEndPoint(0))
            dist_to_p1 = p_on_pipe.DistanceTo(loc_curve.GetEndPoint(1))
            can_break = (dist_to_p0 > MIN_BREAK_DIST_FT) and (dist_to_p1 > MIN_BREAK_DIST_FT)

            if not connected and can_break:
                connected = connect_fixed_sprinkler_along_pipe_middle(
                    doc,
                    target_pipe,
                    p_on_pipe,
                    sp,
                    sp_conn,
                    perp_dist,
                    dz,
                    active_pipes,
                    orig_pt,
                    orig_offset,
                )

            # Strictly verify Sprinkler position was never altered
            restore_sprinkler_full_state(doc, sp, orig_pt, orig_offset)

            if connected:
                connected_count += 1
            else:
                failed_element_ids.append(sp.Id)

        t.Commit()

        # Auto-select / highlight failed sprinklers in the Revit UI
        if failed_element_ids:
            try:
                net_id_list = List[ElementId](failed_element_ids)
                uidoc.Selection.SetElementIds(net_id_list)
            except Exception:
                pass

        # Summary card: green = all connected, amber = some failed, red = none connected
        total = len(sprinklers)
        details = ["Sprinkler positions were kept fixed."]
        if skipped_connected_count > 0:
            details.append("{0} sprinkler(s) were already connected and left untouched."
                           .format(skipped_connected_count))
        if failed_element_ids:
            failed_str = ", ".join(str(get_id_value(eid)) for eid in failed_element_ids)
            details.append("Not connected (now selected in your view): " + failed_str)

        if connected_count == 0:
            status, headline = "error", "No sprinklers were connected"
        elif failed_element_ids:
            status, headline = "warning", "Connected, with some sprinklers left"
        else:
            status, headline = "success", "All sprinklers connected"

        dui.show_result(
            TOOL_TITLE, status, headline,
            stats=[("{0}/{1}".format(connected_count, total), "Connected"),
                   (str(len(failed_element_ids)), "Failed"),
                   (str(skipped_connected_count), "Already connected")],
            details=details)

    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        dui.alert(TOOL_TITLE, "Nothing was changed. " + str(ex), "error", "The tool stopped with an error")


if __name__ == "__main__":
    main()
