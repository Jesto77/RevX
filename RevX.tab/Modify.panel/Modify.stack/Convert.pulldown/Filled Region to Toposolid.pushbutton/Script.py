# -*- coding: utf-8 -*-
"""Convert selected Filled Regions <-> Toposolids in one click.

Select any mix of Filled Regions and Toposolids and run this tool - it
inspects each selected element's type and converts it in the correct
direction automatically. No need for two separate buttons.

REQUIRES REVIT 2024 OR LATER.
Toposolid is a Revit API class introduced in Revit 2024. On older
versions this script shows a clear message and exits instead of
crashing.

Important differences vs. a Floor<->Toposolid conversion:
  - A Filled Region is a flat, VIEW-SPECIFIC detail element with no
    Level or Height Offset parameter of its own. Toposolid -> Filled
    Region needs an active view to sketch into (ideally a plan view
    whose plane roughly matches the toposolid). Filled Region ->
    Toposolid needs a Level: if the filled region's host view is a
    plan view, its associated level is used automatically; otherwise
    you'll be asked to pick one.
  - A Filled Region's boundary curves do carry a real Z coordinate
    (from the view's sketch plane), even though the region displays as
    flat 2D. This script uses that Z, relative to the chosen Level, to
    set the new Toposolid's Height Offset From Level - so it doesn't
    silently land flat at the level's exact elevation.

Notes / assumptions:
  - If more than one candidate Toposolid Type / Filled Region Type
    exists in the project, you'll be asked which to convert to. With
    only one available, it's used automatically.
  - Only the boundary geometry transfers - no slope/grading data (a
    Filled Region has none to begin with), no line styles/hatch
    patterns, no instance parameters (Comments, Mark, etc.).
"""

__title__ = 'Filled Region <-> Toposolid'
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
        "Filled Region <-> Toposolid conversion isn't possible here.\n\n"
        "Run this tool in Revit 2024 or later.".format(REVIT_VERSION),
        title="Unsupported Revit Version",
        exitscript=True
    )

Toposolid = DB.Toposolid
ToposolidType = DB.ToposolidType


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def get_selected_elements():
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert("Select one or more Filled Regions and/or Toposolids first.",
                     exitscript=True)
    return [doc.GetElement(eid) for eid in sel_ids]


def get_sketch_curveloops(element):
    """Boundary profile off a sketch-based element (Toposolid) as CurveLoops."""
    sketch_id = element.SketchId
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


def get_toposolid_types():
    return list(DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Toposolid)
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
    return getattr(view, "GenLevel", None)


def get_height_offset(element):
    param = element.LookupParameter("Height Offset From Level")
    if param and param.HasValue:
        return param.AsDouble()
    return 0.0


def set_height_offset(element, value):
    param = element.LookupParameter("Height Offset From Level")
    if param and not param.IsReadOnly:
        param.Set(value)


def average_z(curve_loops):
    zs = []
    for loop in curve_loops:
        for curve in loop:
            zs.append(curve.GetEndPoint(0).Z)
    return sum(zs) / len(zs) if zs else 0.0


# ---------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------
def filled_region_to_toposolid(fr, topo_type, fallback_level):
    curve_loops = list(fr.GetBoundaries())
    if not curve_loops:
        logger.warning("Filled Region {} has no boundary - skipped.".format(fr.Id))
        return None

    level = get_level_for_filled_region(fr) or fallback_level
    if level is None:
        logger.warning("Filled Region {} - no Level available - skipped.".format(fr.Id))
        return None

    typed_loops = List[DB.CurveLoop](curve_loops)
    new_topo = Toposolid.Create(doc, typed_loops, topo_type.Id, level.Id)

    if new_topo:
        # The filled region's curves carry a real Z from the view's
        # sketch plane - use it (relative to the chosen level) as the
        # Toposolid's height offset, so it doesn't default to flat at
        # the level's exact elevation.
        offset = average_z(curve_loops) - level.Elevation
        set_height_offset(new_topo, offset)
    return new_topo


def toposolid_to_filled_region(topo, fr_type, view):
    curve_loops = get_sketch_curveloops(topo)
    if not curve_loops:
        logger.warning("Toposolid {} has no editable sketch - skipped.".format(topo.Id))
        return None
    typed_loops = List[DB.CurveLoop](curve_loops)
    return DB.FilledRegion.Create(doc, fr_type.Id, view.Id, typed_loops)


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    elements = get_selected_elements()

    toposolids = [e for e in elements if isinstance(e, Toposolid)]
    filled_regions = [e for e in elements if isinstance(e, DB.FilledRegion)]

    if not toposolids and not filled_regions:
        forms.alert("Nothing convertible in your selection.\n"
                     "Select Filled Regions and/or Toposolids and try again.",
                     exitscript=True)

    topo_type = None
    fallback_level = None
    if filled_regions:
        topo_type = pick_type(get_toposolid_types(), "Convert Filled Region(s) to which Toposolid Type?")
        if topo_type is None:
            forms.alert("No Toposolid Type available/selected - cancelled.", exitscript=True)
        needs_level_prompt = any(get_level_for_filled_region(fr) is None for fr in filled_regions)
        if needs_level_prompt:
            fallback_level = pick_level(get_levels(), "Convert to Toposolid(s) on which Level?")
            if fallback_level is None:
                forms.alert("No Level available/selected - cancelled.", exitscript=True)

    fr_type = None
    active_view = None
    if toposolids:
        fr_type = pick_type(get_filled_region_types(), "Convert Toposolid(s) to which Filled Region Type?")
        if fr_type is None:
            forms.alert("No Filled Region Type available/selected - cancelled.", exitscript=True)
        active_view = doc.ActiveView
        if active_view is None:
            forms.alert("No active view to sketch the Filled Region into - cancelled.",
                         exitscript=True)

    delete_originals = forms.alert(
        "Convert:\n"
        "  {} Filled Region(s) -> Toposolid\n"
        "  {} Toposolid(s) -> Filled Region\n\n"
        "Delete the original elements after conversion?"
        .format(len(filled_regions), len(toposolids)),
        yes=True, no=True
    )

    with revit.Transaction("Convert Filled Region <-> Toposolid"):
        for fr in filled_regions:
            filled_region_to_toposolid(fr, topo_type, fallback_level)

        for topo in toposolids:
            toposolid_to_filled_region(topo, fr_type, active_view)

        if delete_originals:
            to_delete = List[DB.ElementId](
                [fr.Id for fr in filled_regions] + [t.Id for t in toposolids]
            )
            doc.Delete(to_delete)


if __name__ == '__main__':
    main()