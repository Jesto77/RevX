# -*- coding: utf-8 -*-
# ==========================================================
# OFFSET ELEMENTS  (mm input, element grows/shrinks everywhere)
# ==========================================================
#
# SUPPORTED:
# - Floors  (multi-loop / openings supported)
# - Ceilings
# - Roofs   (outer loop only - inner openings are lost)
# - Toposolids
#
# INPUT:
#   + value = element EXPANDS  (outer boundary moves outward,
#                               inner openings SHRINK)
#   - value = element SHRINKS  (outer boundary moves inward,
#                               inner openings GROW)
#
# Every loop is classified by NESTING (even-odd rule):
#   - not inside any other loop        -> outer boundary
#   - inside one loop                  -> opening
#   - inside an opening (island)       -> outer boundary again, etc.
# Several independent outer boundaries in one element are therefore
# all treated as outer boundaries, and every opening as an opening,
# so the element itself grows or shrinks evenly on all edges.
#
# ==========================================================
from pyrevit import revit, forms, script
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List
import clr

doc = revit.doc
uidoc = revit.uidoc

# ==========================================================
# INPUT
# ==========================================================
offset_mm = forms.ask_for_string(
    default='200',
    prompt='Enter Offset in mm\n(+ outward / - inward)',
    title='Offset Elements'
)

if not offset_mm:
    script.exit()

try:
    offset_mm = float(offset_mm)
except:
    forms.alert("Invalid offset value")
    script.exit()

# raw feet value - sign is handled PER LOOP below
raw_offset_ft = offset_mm / 304.8

# ==========================================================
# SELECT ELEMENTS
# ==========================================================
try:
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        "Select Floors / Roofs / Ceilings / Toposolids"
    )
except:
    script.exit()

if not refs:
    script.exit()

# ==========================================================
# HELPERS
# ==========================================================
def get_loops(elem):
    sketch_id = elem.SketchId
    if sketch_id == ElementId.InvalidElementId:
        return None
    sketch = doc.GetElement(sketch_id)
    if not sketch:
        return None
    loops = List[CurveLoop]()
    for arr in sketch.Profile:
        cl = CurveLoop()
        for c in arr:
            cl.Append(c)
        loops.Add(cl)
    return loops


def loop_points(loop):
    """XY points of a CurveLoop (arcs/splines tessellated), in loop order."""
    pts = []
    it = loop.GetCurveLoopIterator()
    while it.MoveNext():
        c = it.Current
        tess = list(c.Tessellate())
        for p in tess[:-1]:
            pts.append((p.X, p.Y))
    return pts


def poly_area(pts):
    """Signed polygon area. Positive = CCW, negative = CW."""
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def point_in_poly(x, y, pts):
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


def sample_point(loop):
    """A point on the loop itself (middle of its first curve)."""
    it = loop.GetCurveLoopIterator()
    it.MoveNext()
    p = it.Current.Evaluate(0.5, True)
    return (p.X, p.Y)


def classify_loops(loops):
    """Returns (areas, is_opening) per loop using the even-odd rule:
    a loop nested inside an ODD number of other loops is an opening."""
    polys = [loop_points(lp) for lp in loops]
    areas = [poly_area(p) for p in polys]
    samples = [sample_point(lp) for lp in loops]
    is_opening = []
    for i in range(len(polys)):
        depth = 0
        for j in range(len(polys)):
            if i != j and point_in_poly(samples[i][0], samples[i][1], polys[j]):
                depth += 1
        is_opening.append(depth % 2 == 1)
    return areas, is_opening


def offset_loops(loops, raw_offset):
    """Positive raw_offset = element EXPANDS everywhere:
       - every outer boundary moves outward
       - every opening shrinks (so the element grows into it)
    Negative = the reverse.

    Revit convention with normal = Z+:
      CCW loop -> positive offset = OUTWARD (loop gets bigger)
      CW  loop -> positive offset = INWARD  (loop gets smaller)

    Step 1: flip the sign for CW loops so +value = "loop gets bigger".
    Step 2: for openings reverse that, because a bigger opening means
            a smaller element.
    """
    areas, is_opening = classify_loops(loops)

    result = List[CurveLoop]()
    for i, loop in enumerate(loops):
        loop_sign = 1.0 if areas[i] > 0 else -1.0
        effective_offset = raw_offset * loop_sign
        if is_opening[i]:
            effective_offset = -effective_offset
        new_loop = CurveLoop.CreateViaOffset(loop, effective_offset, XYZ.BasisZ)
        result.Add(new_loop)
    return result


def copy_parameters(src, trg):
    for p in src.Parameters:
        try:
            if p.IsReadOnly:
                continue
            tp = trg.LookupParameter(p.Definition.Name)
            if not tp:
                continue
            if tp.IsReadOnly:
                continue
            if p.StorageType == StorageType.Double:
                tp.Set(p.AsDouble())
            elif p.StorageType == StorageType.Integer:
                tp.Set(p.AsInteger())
            elif p.StorageType == StorageType.String:
                tp.Set(p.AsString())
            elif p.StorageType == StorageType.ElementId:
                tp.Set(p.AsElementId())
        except:
            pass

# ==========================================================
# RECREATE
# ==========================================================
def recreate_floor(elem, loops):
    new_elem = Floor.Create(
        doc,
        loops,
        elem.FloorType.Id,
        elem.LevelId
    )
    copy_parameters(elem, new_elem)
    return new_elem


def recreate_ceiling(elem, loops):
    new_elem = Ceiling.Create(
        doc,
        loops,
        elem.GetTypeId(),
        elem.LevelId
    )
    copy_parameters(elem, new_elem)
    return new_elem


def recreate_roof(elem, loops):
    # NOTE: NewFootPrintRoof only accepts a single outer profile.
    # Inner openings are lost in this simplified recreate.
    outer = None
    best = -1.0
    for lp in loops:
        a = abs(poly_area(loop_points(lp)))
        if a > best:
            best = a
            outer = lp
    if outer is None:
        return None
    curve_array = CurveArray()
    iterator = outer.GetCurveLoopIterator()
    while iterator.MoveNext():
        curve_array.Append(iterator.Current)
    roof_type = doc.GetElement(elem.GetTypeId())
    level = doc.GetElement(elem.LevelId)
    mapping = clr.Reference[ModelCurveArray]()
    new_elem = doc.Create.NewFootPrintRoof(
        curve_array,
        level,
        roof_type,
        mapping
    )
    copy_parameters(elem, new_elem)
    return new_elem


def recreate_toposolid(elem, loops):
    new_elem = Toposolid.Create(
        doc,
        loops,
        elem.GetTypeId(),
        elem.LevelId
    )
    copy_parameters(elem, new_elem)
    return new_elem


# ==========================================================
# MAIN
# ==========================================================
success = 0
failed = []

t = Transaction(doc, "Offset Elements")
t.Start()

for r in refs:
    elem = doc.GetElement(r.ElementId)
    try:
        loops = get_loops(elem)
        if not loops or loops.Count == 0:
            failed.append("{} : No sketch loops".format(elem.Id))
            continue

        # Outer loop follows the input sign; openings move opposite
        # so the element grows/shrinks evenly on every edge.
        newloops = offset_loops(loops, raw_offset_ft)

        # FLOOR
        if isinstance(elem, Floor):
            new_elem = recreate_floor(elem, newloops)
        # CEILING
        elif isinstance(elem, Ceiling):
            new_elem = recreate_ceiling(elem, newloops)
        # ROOF
        elif isinstance(elem, FootPrintRoof):
            new_elem = recreate_roof(elem, newloops)
        # TOPOSOLID
        elif "Toposolid" in elem.GetType().Name:
            new_elem = recreate_toposolid(elem, newloops)
        else:
            failed.append("{} : Unsupported type".format(elem.Id))
            continue

        if new_elem is None:
            failed.append("{} : Create failed".format(elem.Id))
            continue

        doc.Delete(elem.Id)
        success += 1
    except Exception as ex:
        failed.append("{} : {}".format(elem.Id, str(ex)))

t.Commit()

forms.alert(
    "SUCCESS : {}\nFAILED : {}".format(success, len(failed)),
    title="Completed"
)