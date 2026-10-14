# -*- coding: utf-8 -*-
__title__ = "Planting Quantity"
__author__ = "Jesto Joy"
__doc__ = ("1) Copies each PLANTING Filled Region's Area into Planting Area. "
           "2) Planting Quantity = round(Planting Area x Planting Density). "
           "3) PQ = round(total Planting Area per view+type) x Planting Density.")

import math
from collections import OrderedDict
from pyrevit import revit, DB, forms, script

doc = revit.doc

AREA_PARAM = "Planting Area"          # instance (filled automatically)
DENSITY_PARAM = "Planting Density"    # type (filled manually)
QTY_PARAM = "Planting Quantity"       # instance (filled by formula)
PQ_PARAM = "PQ"                       # instance
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


def to_sqm(val):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(
            val, DB.UnitTypeId.SquareMeters)
    except Exception:
        return DB.UnitUtils.ConvertFromInternalUnits(
            val, DB.DisplayUnitType.DUT_SQUARE_METERS)


def is_area_param(p):
    try:
        return p.Definition.GetDataType() == DB.SpecTypeId.Area
    except Exception:
        try:
            return p.Definition.ParameterType == DB.ParameterType.Area
        except Exception:
            return False


def num_value(p):
    """Numeric value of a parameter (area params returned in sq m)."""
    if p is None or not p.HasValue:
        return None
    if p.StorageType == DB.StorageType.Integer:
        return float(p.AsInteger())
    if p.StorageType == DB.StorageType.Double:
        val = p.AsDouble()
        return to_sqm(val) if is_area_param(p) else val
    return None


def set_num(p, value):
    if p is None or p.IsReadOnly:
        return False
    if p.StorageType == DB.StorageType.Integer:
        return p.Set(int(value))
    if p.StorageType == DB.StorageType.Double:
        return p.Set(float(value))
    if p.StorageType == DB.StorageType.String:
        return p.Set(str(value))
    return False


def set_area(p, sqm_value, internal_value):
    """Write the area; Area-type params take internal units, others sq m."""
    if p is None or p.IsReadOnly:
        return False
    if p.StorageType == DB.StorageType.Double:
        return p.Set(internal_value if is_area_param(p) else sqm_value)
    if p.StorageType == DB.StorageType.Integer:
        return p.Set(int(round_half_up(sqm_value)))
    return False


def round_half_up(x):
    return int(math.floor(x + 0.5))


# ---- collect PLANTING filled regions in the whole project ----
regions = (DB.FilteredElementCollector(doc)
           .OfClass(DB.FilledRegion)
           .WhereElementIsNotElementType()
           .ToElements())

groups = OrderedDict()   # (view id, type id) -> dict
for fr in regions:
    t = doc.GetElement(fr.GetTypeId())
    if t is None or type_comments(t).upper() != TYPE_COMMENTS_VALUE:
        continue
    view = doc.GetElement(fr.OwnerViewId)
    if view is None:
        continue
    key = (str(fr.OwnerViewId), str(t.Id))
    g = groups.setdefault(key, {"type": t, "view": view.Name, "items": []})
    g["items"].append(fr)

if not groups:
    forms.alert("No Filled Regions found whose type Comments = '{}'."
                .format(TYPE_COMMENTS_VALUE), exitscript=True)

report = []
skipped = []
with revit.Transaction("Planting Quantity"):
    for key, g in groups.items():
        t = g["type"]
        name = type_name(t)
        dens = num_value(t.LookupParameter(DENSITY_PARAM))

        total_area = 0.0
        area_fail = 0
        qty_fail = 0
        # Step 1 + 2: per-region Planting Area and Planting Quantity
        for fr in g["items"]:
            ap = fr.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED)
            if ap is None or not ap.HasValue:
                area_fail += 1
                continue
            internal = ap.AsDouble()
            sqm = to_sqm(internal)
            total_area += sqm
            if not set_area(fr.LookupParameter(AREA_PARAM), sqm, internal):
                area_fail += 1
            if dens is not None:
                qty = round_half_up(sqm * dens)
                if not set_num(fr.LookupParameter(QTY_PARAM), qty):
                    qty_fail += 1

        if dens is None:
            skipped.append("{} [{}] : '{}' type parameter missing/empty - "
                           "Planting Area copied, Planting Quantity and PQ "
                           "not filled".format(name, g["view"], DENSITY_PARAM))
            report.append((g["view"], name, len(g["items"]), total_area,
                           round_half_up(total_area), "-", "-", area_fail,
                           "-"))
            continue

        # Step 3: PQ = rounded total area of this view+type x density
        rounded_total = round_half_up(total_area)
        pq = round_half_up(rounded_total * dens)
        pq_fail = 0
        for fr in g["items"]:
            if not set_num(fr.LookupParameter(PQ_PARAM), pq):
                pq_fail += 1
        report.append((g["view"], name, len(g["items"]), total_area,
                       rounded_total, dens, pq, area_fail,
                       qty_fail + pq_fail))

out = script.get_output()
out.print_md("## Planting Quantity - PLANTING filled regions")
rows = [[v, n, c, "{:.3f}".format(a), ta, d, pq, af, f] for
        (v, n, c, a, ta, d, pq, af, f) in report]
if rows:
    out.print_table(rows, columns=["View", "Type", "Count",
                                   "Total area (exact)",
                                   "Total area (rounded)", "Density", "PQ",
                                   "Area copy failed",
                                   "Qty/PQ write failed"])
for s in skipped:
    out.print_md("**Skipped:** " + s)