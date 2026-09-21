# -*- coding: utf-8 -*-
"""Convert selected Filled Regions <-> Floors in one click.

Select any mix of Filled Regions and Floors and run this tool - it
inspects each selected element's type and converts it in the correct
direction automatically. No need for two separate buttons.

Important differences vs. a Floor<->Toposolid conversion:
  - A Filled Region is a flat, VIEW-SPECIFIC detail element with no
    Level of its own. Floor -> Filled Region needs an active view to
    sketch into (ideally a plan/section/drafting view whose plane
    matches the floor). Filled Region -> Floor needs a Level: if the
    filled region's host view is a plan view, its associated level is
    used automatically; otherwise you'll be asked to pick one.
  - Filled Region boundaries are 2D loops on the view's sketch plane;
    Floor boundaries are 3D loops on a Level plane. The curve geometry
    transfers as-is, so double check the result if your view isn't
    aligned with the target level's plane.

Notes / assumptions:
  - If more than one candidate Filled Region Type / Floor Type exists
    in the project, you'll be asked which to convert to. With only one
    available, it's used automatically. Floor Type choices exclude
    Structural Foundation slabs.
  - Only the boundary geometry transfers - line styles/hatch patterns
    on the filled region and floor parameters (Comments, Mark, etc.)
    are NOT copied automatically.
  - Floor.Create(CurveLoop list) requires Revit 2022+. On older Revit
    this script falls back to the legacy doc.Create.NewFloor() call.
"""

__title__ = 'Filled Region <-> Floor'
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
# Helpers
# ---------------------------------------------------------------------
def get_selected_elements():
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert("Select one or more Filled Regions and/or Floors first.",
                     exitscript=True)
    return [doc.GetElement(eid) for eid in sel_ids]


def get_floor_sketch_curveloops(floor):
    sketch_id = floor.SketchId
    if sketch_id is None or sketch_id == DB.ElementId.InvalidElementId:
        return None
    sketch = doc.GetElement(sketch_id)
    loops = []
    for curve_array in sketch.Profile:
        loop = DB.CurveLoop()
        for curve in curve_array:
            loop.Append(curve)
        loops.append(loop)
    return loops


def get_floor_types():
    """Architectural Floor types only - excludes Structural Foundation slabs."""
    return list(DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Floors)
                .WhereElementIsElementType()
                .ToElements())


def get_filled_region_types():
    return list(DB.FilteredElementCollector(doc)
                .OfClass(DB.FilledRegionType)
                .ToElements())


def get_levels():
    return list(DB.FilteredElementCollector(doc)
                .OfClass(DB.Level)
                .ToElements())


def pick_type(types, prompt_title):
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
    return name_map[chosen_name] if chosen_name else None


def pick_level(levels, prompt_title):
    if not levels:
        return None
    if len(levels) == 1:
        return levels[0]
    name_map = {lvl.Name: lvl for lvl in levels}
    chosen_name = forms.SelectFromList.show(
        sorted(name_map.keys()),
        title=prompt_title,
        button_name="Select Level",
        multiselect=False
    )
    return name_map[chosen_name] if chosen_name else None


def get_level_for_filled_region(fr):
    """Use the host view's associated level if it has one (plan views);
    otherwise the caller falls back to asking the user."""
    view = doc.GetElement(fr.OwnerViewId)
    gen_level = getattr(view, "GenLevel", None)
    return gen_level


def create_floor(curve_loops, floor_type, level):
    typed_loops = List[DB.CurveLoop](curve_loops)
    try:
        # Modern API - Revit 2022+
        return DB.Floor.Create(doc, typed_loops, floor_type.Id, level.Id)
    except (AttributeError, TypeError):
        # Legacy API - pre-Revit 2022
        curve_array = DB.CurveArray()
        for loop in curve_loops:
            for curve in loop:
                curve_array.Append(curve)
        return doc.Create.NewFloor(curve_array, floor_type, level, False)


def floor_to_filled_region(floor, fr_type, view):
    curve_loops = get_floor_sketch_curveloops(floor)
    if not curve_loops:
        logger.warning("Floor {} has no editable sketch - skipped.".format(floor.Id))
        return None
    typed_loops = List[DB.CurveLoop](curve_loops)
    return DB.FilledRegion.Create(doc, fr_type.Id, view.Id, typed_loops)


def filled_region_to_floor(fr, floor_type, fallback_level):
    curve_loops = list(fr.GetBoundaries())
    if not curve_loops:
        logger.warning("Filled Region {} has no boundary - skipped.".format(fr.Id))
        return None
    level = get_level_for_filled_region(fr) or fallback_level
    if level is None:
        logger.warning("Filled Region {} - no Level available - skipped.".format(fr.Id))
        return None
    return create_floor(curve_loops, floor_type, level)


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    elements = get_selected_elements()

    floors = [e for e in elements if isinstance(e, DB.Floor)]
    filled_regions = [e for e in elements if isinstance(e, DB.FilledRegion)]

    if not floors and not filled_regions:
        forms.alert("Nothing convertible in your selection.\n"
                     "Select Floors and/or Filled Regions and try again.",
                     exitscript=True)

    fr_type = None
    active_view = None
    if floors:
        fr_type = pick_type(get_filled_region_types(), "Convert Floor(s) to which Filled Region Type?")
        if fr_type is None:
            forms.alert("No Filled Region Type available/selected - cancelled.", exitscript=True)
        active_view = doc.ActiveView
        if active_view is None:
            forms.alert("No active view to sketch the Filled Region into - cancelled.",
                         exitscript=True)

    floor_type = None
    fallback_level = None
    if filled_regions:
        floor_type = pick_type(get_floor_types(), "Convert Filled Region(s) to which Floor Type?")
        if floor_type is None:
            forms.alert("No Floor Type available/selected - cancelled.", exitscript=True)
        # Only prompt for a fallback level if at least one selected filled
        # region's host view has no associated level.
        needs_level_prompt = any(get_level_for_filled_region(fr) is None for fr in filled_regions)
        if needs_level_prompt:
            fallback_level = pick_level(get_levels(), "Convert to Floor(s) on which Level?")
            if fallback_level is None:
                forms.alert("No Level available/selected - cancelled.", exitscript=True)

    delete_originals = forms.alert(
        "Convert:\n"
        "  {} Floor(s) -> Filled Region\n"
        "  {} Filled Region(s) -> Floor\n\n"
        "Delete the original elements after conversion?"
        .format(len(floors), len(filled_regions)),
        yes=True, no=True
    )

    with revit.Transaction("Convert Filled Region <-> Floor"):
        for floor in floors:
            floor_to_filled_region(floor, fr_type, active_view)

        for fr in filled_regions:
            filled_region_to_floor(fr, floor_type, fallback_level)

        if delete_originals:
            to_delete = List[DB.ElementId](
                [f.Id for f in floors] + [fr.Id for fr in filled_regions]
            )
            doc.Delete(to_delete)


if __name__ == '__main__':
    main()