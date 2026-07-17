# -*- coding: utf-8 -*-
# ==========================================================
# OFFSET ELEMENTS  (mm input, consistent direction for all loops)
# ==========================================================
#
# SUPPORTED:
# - Floors  (multi-loop / openings supported)
# - Ceilings
# - Roofs   (outer loop only — inner openings are lost)
# - Toposolids
#
# INPUT:
#   + value = OUTWARD  (expand)
#   - value = INWARD   (shrink)
#
# Each loop is offset individually based on its orientation so the
# same user input behaves identically on every element AND every
# inner void moves in the same physical direction as the boundary.
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

# raw feet value — sign is handled PER LOOP below
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
    """Offset every loop so the same raw sign moves in the same
    physical direction regardless of loop orientation.

    Revit convention with normal = Z+:
      CCW loop  -> positive offset = OUTWARD
      CW  loop  -> positive offset = INWARD

    We flip the sign for CW loops so the user's request is
    honoured identically on every loop.
    """
    result = List[CurveLoop]()
    for loop in loops:
        area = signed_area(loop)
        # CCW  : keep sign as-is  (positive = outward)
        # CW   : flip sign         (positive = inward, so flip to outward)
        loop_sign = 1.0 if area > 0 else -1.0
        effective_offset = raw_offset * loop_sign
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

        # Offset every loop individually with the correct sign.
        # This guarantees all loops (outer + inner voids) move in
        # the same physical direction for the same user input.
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
