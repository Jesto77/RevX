# -- coding: utf-8 --
"""CopyScheduleToSheets - pick a schedule instance on a sheet,
then copy it to all sheets in the selected series at the same position."""

title = "Clone"
author = "Jesto Joy"

SCRIPT_VERSION = "v2 (2026-08-11)"

import time
from pyrevit import revit, forms, script

try:
    from System.Windows.Forms import Application as WinFormsApp
except Exception:
    WinFormsApp = None


def _flush_pending_input():
    if WinFormsApp is not None:
        try:
            WinFormsApp.DoEvents()
        except Exception:
            pass
    time.sleep(0.35)


from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    BuiltInCategory,
    ViewSheet,
    ScheduleSheetInstance,
)

logger = script.get_logger()
doc    = revit.doc
uidoc  = revit.uidoc

SERIES_PARAM_NAME = "Series Range"


# ─────────────────────────────────────────────────────────── helpers ──────────
def get_id_value(element_id):
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def get_series(sheet):
    p = sheet.LookupParameter(SERIES_PARAM_NAME)
    if p is not None:
        val = p.AsString()
        if val:
            return val.strip()
    return ''


def get_schedule_instances_on_sheet(sheet):
    return list(
        FilteredElementCollector(doc)
        .OfClass(ScheduleSheetInstance)
        .OwnedByView(sheet.Id)
        .ToElements())


# ──────────────────────────────────── pick schedule on active sheet ───────────
def pick_schedule_on_sheet():
    active_view = doc.ActiveView
    if not isinstance(active_view, ViewSheet):
        forms.alert(
            "Please open a Sheet view first, then run this script.",
            title="CopyScheduleToSheets")
        return None, None

    sheet     = active_view
    instances = get_schedule_instances_on_sheet(sheet)

    if not instances:
        forms.alert(
            "No schedule instances found on the current sheet '{} - {}'."
            .format(sheet.SheetNumber, sheet.Name),
            title="CopyScheduleToSheets")
        return None, None

    if len(instances) == 1:
        return instances[0], sheet

    # multiple schedules - let user choose
    label_map = {}
    for ssi in instances:
        schedule_view = doc.GetElement(ssi.ScheduleId)
        name = schedule_view.Name if schedule_view else "Unknown"
        pt   = ssi.Point
        label = u"{} (at {:.2f}, {:.2f})".format(name, pt.X, pt.Y)
        label_map[label] = ssi

    _flush_pending_input()
    choice = forms.SelectFromList.show(
        sorted(label_map.keys()),
        title       = "Select Schedule to Copy",
        button_name = "Use this schedule",
        multiselect = False)

    if not choice:
        return None, None
    return label_map[choice], sheet


# ──────────────────────────────────── pick series ────────────────────────────
def pick_target_series(exclude_sheet_id):
    all_sheets = list(
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Sheets)
        .WhereElementIsNotElementType()
        .ToElements())

    # exclude source sheet
    all_sheets = [
        sh for sh in all_sheets
        if get_id_value(sh.Id) != get_id_value(exclude_sheet_id)]

    # group by series
    series_map   = {}
    series_order = []
    for sh in all_sheets:
        s = get_series(sh)
        if not s:
            s = "(No Series)"
        if s not in series_map:
            series_map[s] = []
            series_order.append(s)
        series_map[s].append(sh)

    # sort sheets within each series
    for s in series_map:
        series_map[s].sort(key=lambda sh: sh.SheetNumber)

    if not series_order:
        forms.alert("No other sheets found in the project.",
                    title="CopyScheduleToSheets")
        return None

    # build labels with sheet count
    series_labels   = []
    label_to_series = {}
    for s in series_order:
        count = len(series_map[s])
        label = u"{}  ({} sheet{})".format(s, count, 's' if count != 1 else '')
        series_labels.append(label)
        label_to_series[label] = s

    _flush_pending_input()
    selected_labels = forms.SelectFromList.show(
        series_labels,
        title       = "Select Series to Copy Schedule Into",
        button_name = "Copy to All Sheets in Selected Series",
        multiselect = True)

    if not selected_labels:
        return None

    # collect all sheets from selected series
    target_sheets = []
    for lbl in selected_labels:
        if lbl in label_to_series:
            s = label_to_series[lbl]
            target_sheets.extend(series_map[s])

    return target_sheets if target_sheets else None


# ──────────────────────────────────── copy schedule to a sheet ───────────────
def copy_schedule_to_sheet(source_ssi, target_sheet):
    try:
        ScheduleSheetInstance.Create(
            doc, target_sheet.Id, source_ssi.ScheduleId, source_ssi.Point)
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════ MAIN ══

# ── 1. pick schedule on current sheet ─────────────────────────────────────────
source_ssi, source_sheet = pick_schedule_on_sheet()
if source_ssi is None:
    script.exit()

_flush_pending_input()

# ── 2. pick target series ────────────────────────────────────────────────────
target_sheets = pick_target_series(source_sheet.Id)
if not target_sheets:
    script.exit()

# ── 3. transaction ────────────────────────────────────────────────────────────
source_schedule = doc.GetElement(source_ssi.ScheduleId)
schedule_name   = source_schedule.Name if source_schedule else "Unknown"

t = Transaction(doc, "Copy Schedule '{}' to {} sheets".format(
    schedule_name, len(target_sheets)))
t.Start()
try:
    for sh in target_sheets:
        copy_schedule_to_sheet(source_ssi, sh)

    t.Commit()

except Exception:
    if t.HasStarted():
        t.RollBack()
    import traceback
    forms.alert(
        "Error - transaction rolled back:\n\n{}"
        .format(traceback.format_exc()),
        title="CopyScheduleToSheets")
    script.exit()