# -*- coding: utf-8 -*-
__title__ = "Planting Quantity"
__author__ = "Jesto Joy"
__doc__ = ("Fills PQ on every PLANTING Filled Region: "
           "round(total Planting Area of the type) x Planting Density.")

import math
from collections import OrderedDict
from pyrevit import revit, DB, forms, script

doc = revit.doc

AREA_PARAM = "Planting Area"      # instance
DENSITY_PARAM = "Planting Density"  # type
PQ_PARAM = "PQ"                   # instance
TYPE_COMMENTS_VALUE = "PLANTING"


def type_name(t):
    p = t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    if p and p.HasValue:
        return p.AsString()
    return DB.Element.Name.__get__(t)


def type_comments(t):
    p = t.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS)
    if p and p.HasValue:
        return (p.AsString() or "").strip()
    return ""


def num_value(p):
    """Return numeric value of a parameter in its own display unit."""
    if p is None or not p.HasValue:
        return None
    if p.StorageType == DB.StorageType.Integer:
        return float(p.AsInteger())
    if p.StorageType == DB.StorageType.Double:
        val = p.AsDouble()
        # Area parameters are stored in internal units (sq ft) -> convert to sq m
        try:
            if p.Definition.GetDataType() == DB.SpecTypeId.Area:
                return DB.UnitUtils.ConvertFromInternalUnits(
                    val, DB.UnitTypeId.SquareMeters)
        except Exception:
            try:
                if p.Definition.ParameterType == DB.ParameterType.Area:
                    return DB.UnitUtils.ConvertFromInternalUnits(
                        val, DB.DisplayUnitType.DUT_SQUARE_METERS)
            except Exception:
                pass
        return val
    return None


def set_num(p, value):
    if p is None or p.IsReadOnly:
        return False
    if p.StorageType == DB.StorageType.Integer:
        return p.Set(int(value))
    if p.StorageType == DB.StorageType.Double:
        return p.Set(float(value))
    if p.StorageType == DB.StorageType.String:
        return p.Set(str(int(value)))
    return False


def round_half_up(x):
    return int(math.floor(x + 0.5))


# ---- collect PLANTING filled regions in the whole project ----
regions = (DB.FilteredElementCollector(doc)
           .OfClass(DB.FilledRegion)
           .WhereElementIsNotElementType()
           .ToElements())

groups = OrderedDict()   # type id (int) -> dict
for fr in regions:
    t = doc.GetElement(fr.GetTypeId())
    if t is None or type_comments(t).upper() != TYPE_COMMENTS_VALUE:
        continue
    view = doc.GetElement(fr.OwnerViewId)
    if view is None:
        continue
    # one group per (view, type) so PQ matches sheet-filtered schedules
    key = (str(fr.OwnerViewId), str(t.Id))  # IntegerValue removed in 2024+
    g = groups.setdefault(key, {"type": t, "view": view.Name,
                                "items": [], "area": 0.0})
    g["items"].append(fr)
    a = num_value(fr.LookupParameter(AREA_PARAM))
    if a is not None:
        g["area"] += a

if not groups:
    forms.alert("No Filled Regions found whose type Comments = '{}'."
                .format(TYPE_COMMENTS_VALUE), exitscript=True)

report = []
skipped = []
with revit.Transaction("Fill PQ"):
    for key, g in groups.items():
        t = g["type"]
        name = type_name(t)
        dens = num_value(t.LookupParameter(DENSITY_PARAM))
        if dens is None:
            skipped.append("{} [{}] : '{}' type parameter missing/empty"
                           .format(name, g["view"], DENSITY_PARAM))
            continue
        total_area = round_half_up(g["area"])
        pq = round_half_up(total_area * dens)
        fails = 0
        for fr in g["items"]:
            if not set_num(fr.LookupParameter(PQ_PARAM), pq):
                fails += 1
        report.append((g["view"], name, len(g["items"]), g["area"], total_area,
                       dens, pq, fails))

out = script.get_output()
out.print_md("## Fill PQ - PLANTING filled regions")
rows = [[v, n, c, "{:.3f}".format(a), ta, d, pq, f] for
        (v, n, c, a, ta, d, pq, f) in report]
if rows:
    out.print_table(rows, columns=["View", "Type", "Count", "Total area (exact)",
                                   "Total area (rounded)", "Density", "PQ",
                                   "Failed to write"])
for s in skipped:
    out.print_md("**Skipped:** " + s)