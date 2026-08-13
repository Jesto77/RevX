# -*- coding: utf-8 -*-
"""Clone - Select an element on a sheet directly, auto-detect its type,
then copy it to other sheets at the same position."""

title  = "Clone"
author = "Jesto Joy"

import time
import traceback
from pyrevit import revit, forms, script

try:
    from System.Windows.Forms import Application as WinFormsApp
except Exception:
    WinFormsApp = None

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    BuiltInCategory,
    ViewSheet,
    View,
    ViewType,
    ViewDuplicateOption,
    Viewport,
    ScheduleSheetInstance,
    FamilyInstance,
    CategoryType,
    ElementId,
    ElementTransformUtils,
    Transform,
)
from Autodesk.Revit.UI.Selection import (
    ISelectionFilter,
    ObjectType,
)

logger = script.get_logger()
doc    = revit.doc
uidoc  = revit.uidoc

SERIES_PARAM_NAME  = "Series Range"
TITLEBLOCK_CAT_VAL = int(BuiltInCategory.OST_TitleBlocks)


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════

def flush():
    if WinFormsApp is not None:
        try:
            WinFormsApp.DoEvents()
        except Exception:
            pass
    time.sleep(0.25)


def id_val(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def get_series(sheet):
    p = sheet.LookupParameter(SERIES_PARAM_NAME)
    if p:
        v = p.AsString()
        if v:
            return v.strip()
    return ""


# ══════════════════════════════════════════════════════════
#  DETECT CATEGORY
# ══════════════════════════════════════════════════════════

def detect_category(element):
    if isinstance(element, ScheduleSheetInstance):
        return "Schedule"

    if isinstance(element, Viewport):
        v = doc.GetElement(element.ViewId)
        if v is None:
            return None
        if v.ViewType == ViewType.Legend:
            return "Legend"
        if v.ViewType == ViewType.DraftingView:
            return "Drafting View"
        return None

    if isinstance(element, FamilyInstance):
        cat = element.Category
        if cat is None:
            return None
        if id_val(cat.Id) == TITLEBLOCK_CAT_VAL:
            return None
        if cat.CategoryType == CategoryType.Annotation:
            return "Annotation Family"
        return None

    return None


# ══════════════════════════════════════════════════════════
#  SELECTION FILTER
# ══════════════════════════════════════════════════════════

class SheetElementFilter(ISelectionFilter):
    def AllowElement(self, element):
        return detect_category(element) is not None

    def AllowReference(self, reference, point):
        return False


# ══════════════════════════════════════════════════════════
#  GET SOURCE ELEMENT
# ══════════════════════════════════════════════════════════

def get_source_element(sheet):
    # check current selection first
    sel_ids = list(uidoc.Selection.GetElementIds())
    if sel_ids:
        for eid in sel_ids:
            el  = doc.GetElement(eid)
            cat = detect_category(el)
            if cat:
                return el, cat

    # prompt user to click an element
    flush()
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            SheetElementFilter(),
            "Click the element to clone (Schedule, Legend, Drafting View, or Annotation)")
    except Exception:
        return None, None

    el  = doc.GetElement(ref.ElementId)
    cat = detect_category(el)
    if cat is None:
        return None, None

    return el, cat


# ══════════════════════════════════════════════════════════
#  TARGET SHEETS
# ══════════════════════════════════════════════════════════

def get_other_sheets(exclude_id):
    return [
        sh for sh in
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Sheets)
        .WhereElementIsNotElementType()
        .ToElements()
        if id_val(sh.Id) != id_val(exclude_id)
    ]


def pick_by_series(other_sheets):
    series_map   = {}
    series_order = []

    for sh in other_sheets:
        s = get_series(sh) or "(No Series)"
        if s not in series_map:
            series_map[s] = []
            series_order.append(s)
        series_map[s].append(sh)

    for s in series_map:
        series_map[s].sort(key=lambda sh: sh.SheetNumber)

    labels     = []
    lbl_to_ser = {}
    for s in series_order:
        n   = len(series_map[s])
        lbl = u"{}  ({} sheet{})".format(s, n, "s" if n != 1 else "")
        labels.append(lbl)
        lbl_to_ser[lbl] = s

    flush()
    chosen = forms.SelectFromList.show(
        labels,
        title       = "Select Series",
        button_name = "Copy to All Sheets in Series",
        multiselect = True)

    if not chosen:
        return None

    result = []
    for lbl in chosen:
        result.extend(series_map[lbl_to_ser[lbl]])
    return result or None


def pick_by_sheets(other_sheets):
    ordered   = sorted(other_sheets, key=lambda sh: sh.SheetNumber)
    label_map = {}

    for sh in ordered:
        s   = get_series(sh) or "(No Series)"
        lbl = u"{} - {}   [{}]".format(sh.SheetNumber, sh.Name, s)
        label_map[lbl] = sh

    flush()
    chosen = forms.SelectFromList.show(
        list(label_map.keys()),
        title       = "Select Target Sheets",
        button_name = "Copy to Selected Sheets",
        multiselect = True)

    if not chosen:
        return None
    return [label_map[l] for l in chosen if l in label_map] or None


def pick_targets(exclude_id):
    flush()
    mode = forms.CommandSwitchWindow.show(
        ["By Series", "By Sheets"],
        message="Copy by Series or pick individual Sheets?")

    if not mode:
        return None

    others = get_other_sheets(exclude_id)
    if not others:
        return None

    if mode == "By Series":
        return pick_by_series(others)
    return pick_by_sheets(others)


# ══════════════════════════════════════════════════════════
#  COPY LOGIC
# ══════════════════════════════════════════════════════════

def get_all_view_names():
    return set(
        v.Name
        for v in FilteredElementCollector(doc).OfClass(View).ToElements()
        if hasattr(v, "Name"))


def unique_view_name(base, sheet_number, used):
    candidate = u"{} - {}".format(base, sheet_number)
    if candidate not in used:
        used.add(candidate)
        return candidate
    n = 2
    while True:
        c2 = u"{} - {} ({})".format(base, sheet_number, n)
        if c2 not in used:
            used.add(c2)
            return c2
        n += 1


def copy_to_sheet(category, src, src_sheet, tgt_sheet, used_names):
    try:
        if category == "Schedule":
            ScheduleSheetInstance.Create(
                doc, tgt_sheet.Id, src.ScheduleId, src.Point)
            return True

        if category == "Legend":
            Viewport.Create(
                doc, tgt_sheet.Id, src.ViewId, src.GetBoxCenter())
            return True

        if category == "Drafting View":
            src_view  = doc.GetElement(src.ViewId)
            new_vid   = src_view.Duplicate(ViewDuplicateOption.WithDetailing)
            new_view  = doc.GetElement(new_vid)
            new_view.Name = unique_view_name(
                src_view.Name, tgt_sheet.SheetNumber, used_names)
            Viewport.Create(
                doc, tgt_sheet.Id, new_vid, src.GetBoxCenter())
            return True

        if category == "Annotation Family":
            ElementTransformUtils.CopyElements(
                src_sheet,
                List[ElementId]([src.Id]),
                tgt_sheet,
                Transform.Identity,
                None)
            return True

    except Exception:
        logger.debug(
            "Failed: sheet '{}'".format(tgt_sheet.SheetNumber),
            exc_info=True)

    return False


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

# 1. Must be on a sheet
source_sheet = doc.ActiveView
if not isinstance(source_sheet, ViewSheet):
    forms.alert("Open a Sheet view first.", title="Clone")
    script.exit()

# 2. Get element (pre-selected or click)
source_el, category = get_source_element(source_sheet)
if source_el is None:
    script.exit()

# 3. Pick targets
flush()
target_sheets = pick_targets(source_sheet.Id)
if not target_sheets:
    script.exit()

# 4. Execute
used_names = get_all_view_names() if category == "Drafting View" else None

t = Transaction(doc, "Clone {} to {} sheets".format(category, len(target_sheets)))
t.Start()

try:
    ok = 0
    for sh in target_sheets:
        if copy_to_sheet(category, source_el, source_sheet, sh, used_names):
            ok += 1
    t.Commit()

except Exception:
    if t.HasStarted():
        t.RollBack()
    forms.alert(
        "Error - rolled back:\n\n{}".format(traceback.format_exc()),
        title="Clone")
    script.exit()

# 5. Done
forms.alert("Copied to {} of {} sheets.".format(ok, len(target_sheets)),
            title="Clone")