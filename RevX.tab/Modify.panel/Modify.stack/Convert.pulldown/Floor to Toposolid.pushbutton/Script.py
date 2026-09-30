# -*- coding: utf-8 -*-
"""Convert selected Floors <-> Toposolids in one click.

Select any mix of Floors and Toposolids and run this tool - it inspects
each selected element's type and converts it in the correct direction
automatically (Floor -> Toposolid, Toposolid -> Floor). No need for two
separate buttons.

REQUIRES REVIT 2024 OR LATER.
Toposolid is a Revit API class introduced in Revit 2024. It does not
exist in Revit 2019-2023, so true bidirectional conversion is only
possible on 2024+. On older versions this script shows a clear message
and exits instead of crashing.

Notes / assumptions (adjust to fit your project standards):
  - If more than one target type exists in the project, you'll be asked
    which Toposolid Type / Floor Type to convert to. If there's only
    one available, it's used automatically with no prompt. Floor Type
    choices exclude Structural Foundation slabs.
  - Only the sketch boundary (footprint / shape) is transferred. Floor
    parameters (Comments, Mark, etc.) are NOT copied automatically.
  - Sloped/shape-edited floors: the boundary curves transfer, but
    point-based sub-element slope edits are not - you'll likely need to
    re-edit the surface on the new element for complex shapes.
  - You choose (via a prompt) whether the original elements are deleted
    after conversion, so you can review results before committing.
"""

__title__ = 'Floor <-> Toposolid'
__author__ = 'pyRevit'

import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB
from System.Collections.Generic import List

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application
logger = script.get_logger()


# ---------------------------------------------------------------------
# Version gate - Toposolid only exists from Revit 2024 (API year) on
# ---------------------------------------------------------------------
REVIT_VERSION = int(app.VersionNumber)
if REVIT_VERSION < 2024:
    forms.alert(
        "Toposolid elements were introduced in Revit 2024.\n\n"
        "This is Revit {}, which doesn't have the Toposolid API, so "
        "Floor <-> Toposolid conversion isn't possible here.\n\n"
        "Run this tool in Revit 2024 or later.".format(REVIT_VERSION),
        title="Unsupported Revit Version",
        exitscript=True
    )

# Only reference these after the version gate passes
Toposolid = DB.Toposolid
ToposolidType = DB.ToposolidType


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def get_selected_elements():
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert("Select one or more Floors and/or Toposolids first.",
                     exitscript=True)
    return [doc.GetElement(eid) for eid in sel_ids]


def get_sketch_curveloops(element):
    """Pull the boundary profile off a sketch-based element as CurveLoops."""
    sketch_id = element.SketchId
    if sketch_id is None or sketch_id == DB.ElementId.InvalidElementId:
        return None

    sketch = doc.GetElement(sketch_id)
    profile = sketch.Profile  # CurveArrArray

    loops = []
    for curve_array in profile:
        loop = DB.CurveLoop()
        for curve in curve_array:
            loop.Append(curve)
        loops.append(loop)
    return loops


def get_element_level_id(element):
    level_id = getattr(element, "LevelId", None)
    if level_id and level_id != DB.ElementId.InvalidElementId:
        return level_id
    level_param = element.get_Parameter(DB.BuiltInParameter.LEVEL_PARAM)
    if level_param:
        return level_param.AsElementId()
    return None


def get_floor_types():
    """Architectural Floor types only - excludes Structural Foundation
    slabs, which are also DB.FloorType instances but live under a
    different category and were the cause of the earlier bug."""
    return list(DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Floors)
                .WhereElementIsElementType()
                .ToElements())


def get_toposolid_types():
    return list(DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Toposolid)
                .WhereElementIsElementType()
                .ToElements())


def pick_type(types, prompt_title):
    """If there's more than one type available, ask which to use.
    If there's only one (or none), just use it / fail silently to caller."""
    if not types:
        return None
    if len(types) == 1:
        return types[0]

    name_map = {t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString(): t
                for t in types}
    chosen_name = forms.SelectFromList.show(
        sorted(name_map.keys()),
        title=prompt_title,
        button_name="Select Type",
        multiselect=False
    )
    if not chosen_name:
        return None
    return name_map[chosen_name]


def get_height_offset(element):
    """Read 'Height Offset From Level' - present on both Floor and
    Toposolid, keyed by name since the underlying BuiltInParameter
    differs between the two categories."""
    param = element.LookupParameter("Height Offset From Level")
    if param and param.HasValue:
        return param.AsDouble()
    return 0.0


def set_height_offset(element, value):
    param = element.LookupParameter("Height Offset From Level")
    if param and not param.IsReadOnly:
        param.Set(value)


def floor_to_toposolid(floor, topo_type):
    curve_loops = get_sketch_curveloops(floor)
    if not curve_loops:
        logger.warning("Floor {} has no editable sketch - skipped.".format(floor.Id))
        return None

    level_id = get_element_level_id(floor)
    # Must be an explicit .NET List[CurveLoop] - a plain Python list is
    # ambiguous to IronPython here, since Toposolid.Create also has an
    # IList[XYZ] overload (points-based).
    typed_loops = List[DB.CurveLoop](curve_loops)
    new_topo = Toposolid.Create(doc, typed_loops, topo_type.Id, level_id)
    if new_topo:
        set_height_offset(new_topo, get_height_offset(floor))
    return new_topo


def toposolid_to_floor(topo, floor_type):
    curve_loops = get_sketch_curveloops(topo)
    if not curve_loops:
        logger.warning("Toposolid {} has no editable sketch - skipped.".format(topo.Id))
        return None

    level_id = get_element_level_id(topo)
    typed_loops = List[DB.CurveLoop](curve_loops)
    new_floor = DB.Floor.Create(doc, typed_loops, floor_type.Id, level_id)
    if new_floor:
        set_height_offset(new_floor, get_height_offset(topo))
    return new_floor


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    elements = get_selected_elements()

    floors = [e for e in elements if isinstance(e, DB.Floor)]
    toposolids = [e for e in elements if isinstance(e, Toposolid)]

    if not floors and not toposolids:
        forms.alert("Nothing convertible in your selection.\n"
                     "Select Floors and/or Toposolids and try again.",
                     exitscript=True)

    # Ask which target type to use, only if there's more than one option.
    topo_type = None
    if floors:
        topo_type = pick_type(get_toposolid_types(), "Convert Floor(s) to which Toposolid Type?")
        if topo_type is None and get_toposolid_types():
            forms.alert("No Toposolid Type selected - cancelled.", exitscript=True)
        elif topo_type is None:
            forms.alert("No Toposolid Type exists in this project. "
                         "Create/load one, then re-run.", exitscript=True)

    floor_type = None
    if toposolids:
        floor_type = pick_type(get_floor_types(), "Convert Toposolid(s) to which Floor Type?")
        if floor_type is None and get_floor_types():
            forms.alert("No Floor Type selected - cancelled.", exitscript=True)
        elif floor_type is None:
            forms.alert("No (non-foundation) Floor Type exists in this project. "
                         "Create/load one, then re-run.", exitscript=True)

    delete_originals = forms.alert(
        "Convert:\n"
        "  {} Floor(s) -> Toposolid\n"
        "  {} Toposolid(s) -> Floor\n\n"
        "Delete the original elements after conversion?"
        .format(len(floors), len(toposolids)),
        yes=True, no=True
    )

    with revit.Transaction("Convert Floor <-> Toposolid"):
        for floor in floors:
            floor_to_toposolid(floor, topo_type)

        for topo in toposolids:
            toposolid_to_floor(topo, floor_type)

        if delete_originals:
            to_delete = List[DB.ElementId](
                [f.Id for f in floors] + [t.Id for t in toposolids]
            )
            doc.Delete(to_delete)


if __name__ == '__main__':
    main()