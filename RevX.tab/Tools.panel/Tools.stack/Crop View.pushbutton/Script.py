# -*- coding: utf-8 -*-
from __future__ import print_function, division, unicode_literals

"""Override Crop Region Line Pattern and Lineweight for Selected View Types.
   Finds each view's crop viewer element and applies per-view element overrides."""
__title__ = "Override\nCrop Region"
__author__ = "pyRevit Script"
__doc__ = "Override crop region line pattern and lineweight for selected view types."

import sys
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    View,
    ViewType,
    LinePatternElement,
    ElementId,
    Transaction,
    TransactionGroup,
    BuiltInParameter,
    BuiltInCategory,
    OverrideGraphicSettings,
    ElementCategoryFilter,
    XYZ,
)

from pyrevit import forms, script, revit

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
app   = __revit__.Application

logger = script.get_logger()
output = script.get_output()

_VIEWER_CAT_ID = ElementId(BuiltInCategory.OST_Viewers)


def _id_value(elem_id):
    """ElementId numeric value (2025: IntegerValue, 2026+: Value)."""
    if elem_id is None:
        return None
    try:
        return elem_id.Value
    except AttributeError:
        return elem_id.IntegerValue


def _crop_box_center(crop_box):
    tr = crop_box.Transform
    mn = tr.OfPoint(crop_box.Min)
    mx = tr.OfPoint(crop_box.Max)
    return XYZ((mn.X + mx.X) / 2.0, (mn.Y + mx.Y) / 2.0, (mn.Z + mx.Z) / 2.0)


def _resolve_crop_viewer_id(doc, view, viewer_ids):
    """Pick the OST_Viewers element that matches this view's crop box."""
    if not viewer_ids:
        return None
    if len(viewer_ids) == 1:
        return viewer_ids[0]

    try:
        target = _crop_box_center(view.CropBox)
    except Exception:
        return viewer_ids[0]

    best_id = viewer_ids[0]
    best_dist = None
    for eid in viewer_ids:
        el = doc.GetElement(eid)
        if el is None:
            continue
        try:
            bb = el.get_BoundingBox(view)
        except Exception:
            bb = None
        if bb is None:
            continue
        center = XYZ(
            (bb.Min.X + bb.Max.X) / 2.0,
            (bb.Min.Y + bb.Max.Y) / 2.0,
            (bb.Min.Z + bb.Max.Z) / 2.0,
        )
        dist = center.DistanceTo(target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_id = eid
    return best_id


def _viewer_ids_from_dependents(view):
    dep_filter = ElementCategoryFilter(BuiltInCategory.OST_Viewers)
    try:
        return list(view.GetDependentElements(dep_filter))
    except Exception:
        return []


def _find_crop_id_via_rollback_toggle(doc, view):
    """
    Temporarily toggle crop visibility to isolate the crop element, then roll
    back so Crop View / Crop Region Visible in the project stay unchanged.
    """
    tg = TransactionGroup(doc, "Find crop region")
    tg.Start()
    crop_id = None
    try:
        t_hide = Transaction(doc, "Find crop: hide")
        t_hide.Start()
        if not view.CropBoxActive:
            view.CropBoxActive = True
        view.CropBoxVisible = False
        t_hide.Commit()

        shown = list(FilteredElementCollector(doc, view.Id).ToElementIds())
        shown_ints = set(_id_value(eid) for eid in shown)

        t_show = Transaction(doc, "Find crop: show")
        t_show.Start()
        view.CropBoxVisible = True
        doc.Regenerate()
        t_show.Commit()

        new_ids = [
            eid
            for eid in FilteredElementCollector(doc, view.Id).ToElementIds()
            if _id_value(eid) not in shown_ints
        ]
        if not new_ids:
            extra = FilteredElementCollector(doc, view.Id).Excluding(shown)
            new_ids = list(extra.ToElementIds())

        viewer_new = []
        for eid in new_ids:
            el = doc.GetElement(eid)
            if el and el.Category and el.Category.Id == _VIEWER_CAT_ID:
                viewer_new.append(eid)
        crop_id = _resolve_crop_viewer_id(
            doc, view, viewer_new if viewer_new else new_ids
        )
    except Exception as ex:
        logger.debug("Rollback crop find failed on '{}': {}".format(
            view.Name, ex
        ))
    finally:
        tg.RollBack()

    return crop_id


def get_crop_region_id(doc, view):
    """
    Resolve the crop region element id. Does not leave crop settings changed
    in the project (discovery uses a rolled-back transaction group).
    """
    plan_types = (
        ViewType.FloorPlan,
        ViewType.CeilingPlan,
        ViewType.AreaPlan,
        ViewType.EngineeringPlan,
    )
    if view.ViewType in plan_types:
        crop_id = _find_crop_id_via_rollback_toggle(doc, view)
        if crop_id is not None:
            return crop_id

    dep_ids = _viewer_ids_from_dependents(view)
    if len(dep_ids) == 1:
        return dep_ids[0]

    crop_id = _resolve_crop_viewer_id(doc, view, dep_ids)
    if crop_id is not None:
        return crop_id

    return _find_crop_id_via_rollback_toggle(doc, view)


def build_crop_override_settings(lineweight, line_pattern_id):
    """Crop boundaries use projection lines; set cut lines too for section views."""
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineWeight(lineweight)
    ogs.SetCutLineWeight(lineweight)

    if line_pattern_id is None or _id_value(line_pattern_id) == -1:
        # Leave pattern unset — only weight/pattern overrides the user chose
        pass
    else:
        ogs.SetProjectionLinePatternId(line_pattern_id)
        ogs.SetCutLinePatternId(line_pattern_id)

    return ogs


# ── 1. View‑Type picker ────────────────────────────────────────────────────
VIEW_TYPE_MAP = {
    "Floor Plan": ViewType.FloorPlan,
    "Ceiling Plan": ViewType.CeilingPlan,
    "Section": ViewType.Section,
    "Elevation": ViewType.Elevation,
    "3D View": ViewType.ThreeD,
    "Detail View": ViewType.Detail,
    "Drafting View": ViewType.DraftingView,
    "Area Plan": ViewType.AreaPlan,
    "Engineering Plan": ViewType.EngineeringPlan,
    "Walkthrough": ViewType.Walkthrough,
}

existing_types = set()
all_views_raw = FilteredElementCollector(doc).OfClass(View).ToElements()
for v in all_views_raw:
    existing_types.add(v.ViewType)

available_vt_names = sorted(
    name for name, vt in VIEW_TYPE_MAP.items() if vt in existing_types
)

if not available_vt_names:
    forms.alert("No recognisable view types found in this project.", exitscript=True)

selected_vt_names = forms.SelectFromList.show(
    available_vt_names,
    title="Step 1 of 3 — Select View Types",
    multiselect=True,
    button_name="Next: Choose Line Pattern",
)

if not selected_vt_names:
    script.exit()

selected_view_types = set(VIEW_TYPE_MAP[n] for n in selected_vt_names)

# ── 2. Line Pattern picker ─────────────────────────────────────────────────
line_pattern_elements = (
    FilteredElementCollector(doc)
    .OfClass(LinePatternElement)
    .ToElements()
)

lp_dict = {"Solid Line (No Pattern)": None}
for lpe in sorted(line_pattern_elements, key=lambda x: x.Name):
    lp_dict[lpe.Name] = lpe.Id

lp_names = list(lp_dict.keys())

selected_lp_name = forms.SelectFromList.show(
    lp_names,
    title="Step 2 of 3 — Select Line Pattern",
    multiselect=False,
    button_name="Next: Choose Lineweight",
)

if not selected_lp_name:
    script.exit()

selected_lp_id = lp_dict[selected_lp_name]

# ── 3. Lineweight picker (1–16) ────────────────────────────────────────────
lineweight_options = [str(i) for i in range(1, 17)]

selected_lw_str = forms.SelectFromList.show(
    lineweight_options,
    title="Step 3 of 3 — Select Lineweight (1 = hairline, 16 = heaviest)",
    multiselect=False,
    button_name="Apply Overrides",
)

if not selected_lw_str:
    script.exit()

selected_lineweight = int(selected_lw_str)

# ── 4. Target views (no CropBoxActive filter) ─────────────────────────────
target_views = [
    v for v in all_views_raw
    if v.ViewType in selected_view_types
    and not v.IsTemplate
]

if not target_views:
    forms.alert("No views of the selected type(s) were found.", exitscript=True)

confirmed = forms.alert(
    "Ready to override crop region on {} view(s).\n\n"
    "  View types  : {}\n"
    "  Line pattern: {}\n"
    "  Lineweight  : {}\n\n"
    "Continue? (Your crop on/off and visibility settings will not change.)".format(
        len(target_views),
        ", ".join(selected_vt_names),
        selected_lp_name,
        selected_lineweight,
    ),
    yes=True, no=True,
)

if not confirmed:
    script.exit()

# ── 5. Find crop elements (rolled-back) then apply overrides ---------------
success_count = 0
error_count = 0
error_views = []
skipped_no_crop = []
override_settings = build_crop_override_settings(
    selected_lineweight, selected_lp_id
)

# Discovery must run outside the apply transaction (rollback group).
view_crop_map = {}
for view in target_views:
    try:
        crop_id = get_crop_region_id(doc, view)
        if crop_id is None:
            skipped_no_crop.append(view.Name)
        else:
            view_crop_map[_id_value(view.Id)] = (view, crop_id)
    except Exception as ex:
        error_count += 1
        error_views.append("{} - {}".format(view.Name, str(ex)))
        logger.error("Find crop failed on '{}': {}".format(view.Name, ex))

with revit.Transaction("Override Crop Region"):
    for view, crop_id in view_crop_map.values():
        try:
            view.SetElementOverrides(crop_id, override_settings)
            success_count += 1
        except Exception as ex:
            error_count += 1
            error_views.append("{} - {}".format(view.Name, str(ex)))
            logger.error("Override failed on '{}': {}".format(view.Name, ex))

for name in skipped_no_crop:
    error_count += 1
    error_views.append(
        "{} - No crop region (turn on Crop View for this plan)".format(name)
    )
    logger.error("No crop region on view '{}'".format(name))

# ── 6. Report ─────────────────────────────────────────────────────────────
output.print_md("## Crop Region Override — Complete")
output.print_md("---")
output.print_md("**Line Pattern :** `{}`".format(selected_lp_name))
output.print_md("**Lineweight   :** `{}`".format(selected_lineweight))
output.print_md("**View Types   :** `{}`".format(", ".join(selected_vt_names)))
output.print_md("---")
output.print_md("- **Overridden successfully** : {}".format(success_count))
output.print_md("- **Errors** : {}".format(error_count))

if error_views:
    output.print_md("### Views with Errors")
    for ev in error_views:
        output.print_md("- `{}`".format(ev))

if success_count > 0:
    forms.alert(
        "Done! Crop regions overridden on {} view(s).".format(success_count)
    )
else:
    forms.alert("No crop regions were overridden. Errors: {}".format(error_count))