# -*- coding: utf-8 -*-
"""Model operations for MEP 3D Router: find open ends, extend, turn with elbows, branch with tees.

All lengths handed to these functions are in Revit internal units (feet) unless the name ends in _mm.
Every function that changes the model must be called inside an open Transaction.
"""
import math

from Autodesk.Revit import DB
from Autodesk.Revit.DB.Plumbing import Pipe, PlumbingUtils
from Autodesk.Revit.DB.Mechanical import Duct, MechanicalUtils
from Autodesk.Revit.DB.Electrical import Conduit, CableTray

MM = 1.0 / 304.8
Z = DB.XYZ.BasisZ


class RouteError(Exception):
    """A routing step that cannot be done; the message is meant for the user."""


def id_value(element_id):
    """Integer value of an ElementId on every Revit version (Value from 2024, IntegerValue before)."""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def mm_to_ft(value_mm):
    return float(value_mm) * MM


def is_routable(element):
    """True for straight pipes, ducts, conduits and cable trays (not flex, not fabrication)."""
    return isinstance(element, (Pipe, Duct, Conduit, CableTray))


def end_connectors(element):
    return [c for c in element.ConnectorManager.Connectors if c.ConnectorType == DB.ConnectorType.End]


def open_ends(element):
    return [c for c in end_connectors(element) if not c.IsConnected]


def closest_end(element, point):
    return min(end_connectors(element), key=lambda c: c.Origin.DistanceTo(point))


def far_end(element, point):
    return max(end_connectors(element), key=lambda c: c.Origin.DistanceTo(point))


def run_direction(element, connector):
    """Unit vector along the element, pointing out of the element through this connector."""
    curve = element.Location.Curve
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    other = p0 if connector.Origin.DistanceTo(p1) < connector.Origin.DistanceTo(p0) else p1
    return (connector.Origin - other).Normalize()


def size_of(connector):
    """Largest cross-section dimension of the connector, in feet."""
    if connector.Shape == DB.ConnectorProfileType.Round:
        return 2.0 * connector.Radius
    return max(connector.Width, connector.Height)


def perpendicular_axes(run, connector=None):
    """Two unit vectors (side, up) perpendicular to run and to each other.

    For a horizontal or sloped run, up is the world Z projected off the run and side is horizontal.
    For a vertical run, side follows the connector's X axis so the gizmo lines up with the element.
    """
    if abs(run.Z) < 0.99:
        up = (Z - run.Multiply(run.DotProduct(Z))).Normalize()
        side = up.CrossProduct(run).Normalize()
        return side, up
    ref = DB.XYZ.BasisX
    if connector is not None:
        try:
            ref = connector.CoordinateSystem.BasisX
        except Exception:
            pass
    side = ref - run.Multiply(run.DotProduct(ref))
    if side.GetLength() < 1e-6:
        side = DB.XYZ.BasisY - run.Multiply(run.DotProduct(DB.XYZ.BasisY))
    side = side.Normalize()
    up = run.CrossProduct(side).Normalize()
    return side, up


def turn_direction(run, perp, angle_deg):
    """Direction obtained by turning run toward perp by angle_deg."""
    a = math.radians(angle_deg)
    return (run.Multiply(math.cos(a)) + perp.Multiply(math.sin(a))).Normalize()


def apply_slope(direction, slope_pct):
    """Tilt a horizontal direction downward by slope_pct percent (negative goes up).

    Directions that are already not horizontal are returned unchanged.
    """
    if not slope_pct or abs(direction.Z) > 1e-3:
        return direction
    flat = DB.XYZ(direction.X, direction.Y, 0).Normalize()
    return (flat + DB.XYZ(0, 0, -float(slope_pct) / 100.0)).Normalize()


def extend(element, connector, length):
    """Lengthen (or shorten for a negative length) the element at the connector's end.

    Returns the end connector at the moved end.
    """
    curve = element.Location.Curve
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    direction = (p1 - p0).Normalize()
    if connector.Origin.DistanceTo(p1) < connector.Origin.DistanceTo(p0):
        new_curve = DB.Line.CreateBound(p0, p1 + direction.Multiply(length))
        moved = p1 + direction.Multiply(length)
    else:
        new_curve = DB.Line.CreateBound(p0 - direction.Multiply(length), p1)
        moved = p0 - direction.Multiply(length)
    if new_curve.Length < 2 * MM * 10:
        raise RouteError("The piece would become too short.")
    element.Location.Curve = new_curve
    return closest_end(element, moved)


def _copy_along(doc, element, start, run, length):
    """Copy element as a new straight piece from start along run with the given length."""
    copied = DB.ElementTransformUtils.CopyElement(doc, element.Id, run.Multiply(length * 3.0))
    piece = doc.GetElement(list(copied)[0])
    piece.Location.Curve = DB.Line.CreateBound(start, start + run.Multiply(length))
    return piece


def _rotate_to(doc, piece, origin, run, direction):
    """Rotate a piece lying along run (starting at origin) so it lies along direction."""
    angle = run.AngleTo(direction)
    if angle < 1e-6:
        return
    axis_dir = run.CrossProduct(direction).Normalize()
    axis = DB.Line.CreateBound(origin, origin + axis_dir)
    DB.ElementTransformUtils.RotateElement(doc, piece.Id, axis, angle)
    doc.Regenerate()
    curve = piece.Location.Curve
    far = far_end(piece, origin).Origin
    if (far - origin).Normalize().DotProduct(direction) < 0.999:
        DB.ElementTransformUtils.RotateElement(doc, piece.Id, axis, -2 * angle)
        doc.Regenerate()


ELBOW_HELP = ("Couldn't add the elbow. The piece may be too short for it, or its type has no elbow set "
              "(Routing Preferences for pipes and ducts, the type's fittings for cable trays and conduits).")
TEE_HELP = ("Couldn't add the tee. The branch may be too close to an end or a fitting, or the type has no tee set "
            "(Routing Preferences for pipes and ducts, the type's fittings for cable trays and conduits).")


def add_segment(doc, element, connector, direction, length):
    """Continue the run from connector along direction for length (feet).

    Straight on: the element itself is extended. Any other direction: a copy of the element is placed
    along direction and joined with an elbow. Returns (element_to_continue, open_connector).
    Raises RouteError when the elbow cannot be placed.
    """
    run = run_direction(element, connector)
    angle = math.degrees(run.AngleTo(direction))
    if angle < 0.05:
        return element, extend(element, connector, length)
    if angle > 179.0:
        raise RouteError("The new direction points back into the run.")
    origin = connector.Origin
    piece = _copy_along(doc, element, origin, run, length)
    _rotate_to(doc, piece, origin, run, direction)
    start = closest_end(piece, origin)
    try:
        doc.Create.NewElbowFitting(connector, start)
    except Exception:
        raise RouteError(ELBOW_HELP)
    return piece, far_end(piece, origin)


def _break_manual(doc, element, point):
    """Split a conduit or cable tray at point; keeps the far end's connections on the new piece."""
    curve = element.Location.Curve
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    end1 = closest_end(element, p1)
    partners = [r for r in end1.AllRefs
                if r.ConnectorType != DB.ConnectorType.Logical and id_value(r.Owner.Id) != id_value(element.Id)]
    for ref in partners:
        end1.DisconnectFrom(ref)
    copied = DB.ElementTransformUtils.CopyElement(doc, element.Id, (p1 - p0).Normalize().Multiply(curve.Length))
    second = doc.GetElement(list(copied)[0])
    element.Location.Curve = DB.Line.CreateBound(p0, point)
    second.Location.Curve = DB.Line.CreateBound(point, p1)
    doc.Regenerate()
    new_end = closest_end(second, p1)
    for ref in partners:
        new_end.ConnectTo(ref)
    return second


def break_at(doc, element, point):
    """Split element at point into two pieces; returns the second piece."""
    if isinstance(element, Pipe):
        return doc.GetElement(PlumbingUtils.BreakCurve(doc, element.Id, point))
    if isinstance(element, Duct):
        return doc.GetElement(MechanicalUtils.BreakCurve(doc, element.Id, point))
    return _break_manual(doc, element, point)


def project_on(element, point):
    return element.Location.Curve.Project(point).XYZPoint


def tee_clearance(element):
    """Room a tee needs on each side of its center: half the run size plus 75 mm."""
    return 0.5 * size_of(end_connectors(element)[0]) + mm_to_ft(75)


def clamp_branch_point(element, point):
    """Project point on the run and slide it inward so a tee fits between the ends.

    Raises RouteError when the piece is too short for any tee.
    """
    curve = element.Location.Curve
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    run = (p1 - p0).Normalize()
    length = p0.DistanceTo(p1)
    clearance = tee_clearance(element)
    if length < 2 * clearance:
        raise RouteError("This piece is too short for a tee (%d mm long, needs at least %d mm). "
                         "Click a longer piece." % (round(length / MM), round(2 * clearance / MM)))
    along = (project_on(element, point) - p0).DotProduct(run)
    along = min(max(along, clearance), length - clearance)
    return p0 + run.Multiply(along)


def add_branch(doc, element, point, direction, length):
    """Tee a new branch off the middle of element at point, along direction, length in feet.

    Returns (branch_element, open_connector). Raises RouteError when it cannot be placed.
    """
    curve = element.Location.Curve
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    run = (p1 - p0).Normalize()
    p = clamp_branch_point(element, point)
    branch = _copy_along(doc, element, p, run, length)
    _rotate_to(doc, branch, p, run, direction)
    second = break_at(doc, element, p)
    doc.Regenerate()
    try:
        doc.Create.NewTeeFitting(closest_end(element, p), closest_end(second, p), closest_end(branch, p))
    except Exception:
        raise RouteError(TEE_HELP)
    return branch, far_end(branch, p)
