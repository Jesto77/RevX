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
# The outer loop is detected as the loop with the largest
# absolute area. Every other loop is treated as an opening and
# is offset in the opposite shape-direction, so the element
# itself grows or shrinks evenly on all edges.
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


def signed_area(loop):
    """Signed area of a CurveLoop in the XY plane.
    Positive  = counter-clockwise (CCW)
    Negative  = clockwise (CW)
    """
    area = 0.0
    it = loop.GetCurveLoopIterator()
    while it.MoveNext():
        c = it.Current
        p0 = c.GetEndPoint(0)
        p1 = c.GetEndPoint(1)
        area += (p0.X - p1.X) * (p0.Y + p1.Y)
    return area / 2.0


def offset_loops(loops, raw_offset):
    """Positive raw_offset = element EXPANDS everywhere:
       - outer boundary moves outward
       - inner openings shrink (so the element grows into them)

    Revit convention with normal = Z+:
      CCW loop -> positive offset = OUTWARD (loop gets bigger)
      CW  loop -> positive offset = INWARD  (loop gets smaller)

    Step 1: flip the sign for CW loops so +value = "loop gets bigger".
    Step 2: for openings (every loop except the outer one) reverse
            that, because a bigger opening means a smaller element.
    """
    # outer loop = largest absolute area
    areas = [signed_area(lp) for lp in loops]
    outer_idx = max(range(len(areas)), key=lambda i: abs(areas[i]))

    result = List[CurveLoop]()
    for i, loop in enumerate(loops):
        area = areas[i]
        loop_sign = 1.0 if area > 0 else -1.0
        effective_offset = raw_offset * loop_sign

        # openings move opposite to the outer boundary's shape
        if i != outer_idx:
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
    for lp in loops:
        outer = lp
        break
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