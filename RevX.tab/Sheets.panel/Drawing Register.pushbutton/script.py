# -- coding: utf-8 --
"""DrawingRegisterSync - create/update Sheets from an office
Drawing Register Excel, and tag every sheet with a 'Series Range'
project parameter taken from the register's section headers."""

title = "Drawing\nRegister Sync"
author = "Jesto Joy"

SCRIPT_VERSION = "v8 (2026-08-11) no popup results, console output only"

import os
import re
import csv
import time
import zipfile
import xml.etree.ElementTree as ET

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
)

logger = script.get_logger()
output = script.get_output()
doc    = revit.doc
app    = doc.Application

XLNS     = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RELNS    = '{http://schemas.openxmlformats.org/package/2006/relationships}'
DOCRELNS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

SERIES_PARAM_NAME = "Series Range"
NOT_USED_SERIES   = "NOT USED"


# ─────────────────────────────────────────────────────────── tiny helpers ─────
def get_id_value(element_id):
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def col_letters_to_index(letters):
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - ord('A') + 1)
    return idx - 1


# ────────────────────────────────────────────────────────────── xlsx reading ──
def _shared_strings(z):
    shared = []
    if 'xl/sharedStrings.xml' not in z.namelist():
        return shared
    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    for si in root.findall(XLNS + 'si'):
        texts = si.findall('.//' + XLNS + 't')
        shared.append(u''.join(t.text or u'' for t in texts))
    return shared


def _first_sheet_path(z):
    wb_root   = ET.fromstring(z.read('xl/workbook.xml'))
    sheets_el = wb_root.find(XLNS + 'sheets')
    first     = sheets_el.find(XLNS + 'sheet')
    rid       = first.get(DOCRELNS + 'id')
    rels_root = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    target    = None
    for rel in rels_root.findall(RELNS + 'Relationship'):
        if rel.get('Id') == rid:
            target = rel.get('Target')
            break
    if not target:
        return 'xl/worksheets/sheet1.xml'
    if target.startswith('xl/'):
        return target
    if target.startswith('/xl/'):
        return target[1:]
    return 'xl/' + target


def read_xlsx_grid(path):
    with zipfile.ZipFile(path) as z:
        shared     = _shared_strings(z)
        sheet_path = _first_sheet_path(z)
        sroot      = ET.fromstring(z.read(sheet_path))
        data_el    = sroot.find(XLNS + 'sheetData')
        rows       = []
        if data_el is None:
            return rows
        for row_el in data_el.findall(XLNS + 'row'):
            rowdict = {}
            for c_el in row_el.findall(XLNS + 'c'):
                ref     = c_el.get('r') or ''
                letters = ''.join(ch for ch in ref if ch.isalpha())
                if not letters:
                    continue
                col_idx = col_letters_to_index(letters)
                t       = c_el.get('t')
                v_el    = c_el.find(XLNS + 'v')
                is_el   = c_el.find(XLNS + 'is')
                val     = None
                if t == 's' and v_el is not None and v_el.text is not None:
                    try:
                        val = shared[int(v_el.text)]
                    except Exception:
                        val = None
                elif t == 'inlineStr' and is_el is not None:
                    texts = is_el.findall('.//' + XLNS + 't')
                    val   = u''.join(tt.text or u'' for tt in texts)
                elif v_el is not None:
                    val = v_el.text
                if val is not None:
                    val = val.strip()
                rowdict[col_idx] = val
            if rowdict:
                maxc = max(rowdict.keys())
                rows.append([rowdict.get(i) for i in range(maxc + 1)])
            else:
                rows.append([])
    return rows


def read_csv_grid(path):
    rows = []
    with open(path, 'rb') as f:
        reader = csv.reader(f)
        for r in reader:
            rows.append([(c.strip() if c else None) for c in r])
    return rows


def read_grid(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        return read_csv_grid(path)
    return read_xlsx_grid(path)


def cell(row, idx):
    if idx < len(row):
        return row[idx]
    return None


# ──────────────────────────────────────────────────────── number extraction ───
def extract_sheet_number(drawing_number):
    if not drawing_number:
        return None
    parts = [p for p in re.split(r'[-_]', drawing_number.strip()) if p]
    if len(parts) < 2:
        return None
    last, prev = parts[-1], parts[-2]
    if (last.isdigit() and len(last) == 2
            and prev.isdigit() and 3 <= len(prev) <= 6):
        return prev
    for seg in reversed(parts):
        if seg.isdigit() and len(seg) == 2:
            continue
        if seg.isdigit() and 3 <= len(seg) <= 6:
            return seg
    return None


# ─────────────────────────────────────────────────────── register parsing ─────
def parse_register(path):
    rows     = read_grid(path)
    problems = []
    entries  = []

    header_row = None
    for i, row in enumerate(rows):
        a = (cell(row, 0) or u'').strip().lower()
        b = (cell(row, 1) or u'').strip().lower()
        if a == 'nos' and 'drawing number' in b:
            header_row = i
            break
    if header_row is None:
        return entries, [
            "Could not find the header row (a row with 'Nos' in "
            "column A and 'Drawing number' in column B)."]

    current_series = None
    series_counter = -1

    for row in rows[header_row + 1:]:
        a     = cell(row, 0)
        b     = cell(row, 1)
        c     = cell(row, 2)
        has_a = bool(a and a.strip())
        has_b = bool(b and b.strip())

        if not has_a and not has_b:
            continue

        if has_b:
            num   = extract_sheet_number(b)
            title = (c or u'').strip()
            if not num:
                problems.append(
                    "Could not read sheet number from '{}' "
                    "(title: {}) - skipped.".format(b, title or '?'))
                continue
            if not title:
                problems.append(
                    "Drawing '{}' has no title in column C - skipped."
                    .format(b))
                continue
            entries.append({
                'number'        : num,
                'name'          : title,
                'series'        : current_series or u'00 - GENERAL',
                'drawing_number': b,
            })

        elif has_a:
            series_counter += 1
            current_series  = "{:02d} - {}".format(
                series_counter, a.strip())
            print("  Series header found: '{}'".format(current_series))

    return entries, problems


# ──────────────────────────── verify the parameter exists in the project ──────
def verify_series_parameter():
    iterator = doc.ParameterBindings.ForwardIterator()
    iterator.Reset()
    while iterator.MoveNext():
        if iterator.Key.Name == SERIES_PARAM_NAME:
            return True

    sheets = list(
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Sheets)
        .WhereElementIsNotElementType()
        .ToElements())
    if sheets:
        p = sheets[0].LookupParameter(SERIES_PARAM_NAME)
        if p is not None:
            return True

    return False


def set_series(sheet, value):
    p = sheet.LookupParameter(SERIES_PARAM_NAME)
    if p is None:
        return False
    if p.IsReadOnly:
        return False
    try:
        p.Set(value)
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────── titleblock picker ───
def pick_titleblock():
    symbols = list(
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsElementType()
        .ToElements())
    if not symbols:
        forms.alert(
            "No titleblock family types are loaded. Load one first.",
            title="DrawingRegisterSync")
        return None

    label_map = {}
    for sym in symbols:
        try:    fam_name  = sym.FamilyName
        except: fam_name  = "?"
        try:    type_name = sym.Name
        except: type_name = str(get_id_value(sym.Id))
        label_map[u"{} : {}".format(fam_name, type_name)] = sym

    choice = forms.SelectFromList.show(
        sorted(label_map.keys()),
        title       = "Select Titleblock for New Sheets",
        button_name = "Use this titleblock",
        multiselect = False)
    return label_map.get(choice) if choice else None


# ─────────────────────────────────────── series selection with sheet count ────
def pick_series_to_process(entries):
    series_counts = {}
    series_order  = []
    for e in entries:
        s = e['series']
        if s not in series_counts:
            series_counts[s] = 0
            series_order.append(s)
        series_counts[s] += 1

    label_to_series = {}
    display_list    = []
    for s in series_order:
        count = series_counts[s]
        label = u"{}  ({} sheet{})".format(s, count, 's' if count != 1 else '')
        label_to_series[label] = s
        display_list.append(label)

    _flush_pending_input()

    selected_labels = forms.SelectFromList.show(
        display_list,
        title       = "Select Series to Process",
        button_name = "Process Selected Series",
        multiselect = True)

    if not selected_labels:
        return None

    selected_series = set()
    for lbl in selected_labels:
        if lbl in label_to_series:
            selected_series.add(label_to_series[lbl])

    return selected_series


# ══════════════════════════════════════════════════════════════════════ MAIN ══
output.print_md("# DrawingRegisterSync {}".format(SCRIPT_VERSION))
print("Target parameter : '{}'".format(SERIES_PARAM_NAME))

# ── 1. pick the Excel / CSV file ──────────────────────────────────────────────
excel_path = forms.pick_file(
    files_filter='Excel & CSV files|*.xlsx;*.csv')

if not excel_path:
    script.exit()
if isinstance(excel_path, (list, tuple)):
    excel_path = excel_path[0]
excel_path = str(excel_path)
print("File selected: {}".format(excel_path))
_flush_pending_input()

# ── 2. parse the register ──────────────────────────────────────────────────────
entries, parse_problems = parse_register(excel_path)
if not entries:
    print("\nERROR: No drawing rows could be read from this file.")
    for p in parse_problems[:15]:
        print("  - {}".format(p))
    script.exit()

print("\n{} drawing entries read from file.".format(len(entries)))

# ── 3. verify parameter exists in project ─────────────────────────────────────
if not verify_series_parameter():
    output.print_md("## ERROR: Parameter '{}' not found".format(
        SERIES_PARAM_NAME))
    print("\nPlease create it manually:")
    print("  Manage -> Project Parameters -> Add")
    print("  Name        : {}".format(SERIES_PARAM_NAME))
    print("  Discipline  : Common")
    print("  Type        : Text")
    print("  Instance parameter")
    print("  Category    : Sheets")
    print("\nThen re-run this script.")
    script.exit()
print("Parameter '{}' found.".format(SERIES_PARAM_NAME))

# ── 4. let user pick which series to process ──────────────────────────────────
selected_series = pick_series_to_process(entries)
if selected_series is None:
    print("\nUser cancelled series selection.")
    script.exit()

print("\nUser selected {} series:".format(len(selected_series)))
for s in sorted(selected_series):
    print("  '{}'".format(s))

# ── 5. filter entries to only the selected series ─────────────────────────────
entries = [e for e in entries if e['series'] in selected_series]
print("\n{} sheets after series filter.".format(len(entries)))

if not entries:
    print("No sheets to process after filtering.")
    script.exit()

# ── 6. de-dupe by sheet number ────────────────────────────────────────────────
by_number     = {}
dupe_problems = []
for e in entries:
    if e['number'] in by_number:
        dupe_problems.append(
            "Duplicate sheet number '{}' ({}) - only first kept."
            .format(e['number'], e['name']))
        continue
    by_number[e['number']] = e

# ── 7. collect existing sheets ────────────────────────────────────────────────
existing_sheets    = list(
    FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_Sheets)
    .WhereElementIsNotElementType()
    .ToElements())
existing_by_number = {}
for sh in existing_sheets:
    try:
        existing_by_number[sh.SheetNumber] = sh
    except Exception:
        continue

to_update        = [e  for e in by_number.values()
                    if e['number'] in existing_by_number]
to_create        = [e  for e in by_number.values()
                    if e['number'] not in existing_by_number]

all_entries_full, _ = parse_register(excel_path)
all_numbers_in_register = set(
    extract_sheet_number(e['drawing_number'])
    for e in all_entries_full
    if extract_sheet_number(e['drawing_number']))
to_mark_not_used = [sh for num, sh in existing_by_number.items()
                    if num not in all_numbers_in_register]

print("\n{} to update  |  {} to create  |  {} to mark NOT USED".format(
    len(to_update), len(to_create), len(to_mark_not_used)))

# ── 8. pick titleblock if new sheets are needed ───────────────────────────────
titleblock_symbol = None
if to_create:
    titleblock_symbol = pick_titleblock()
    _flush_pending_input()
    if titleblock_symbol is None:
        print("\nNo titleblock selected - new sheets will NOT be created.")
        print("Existing sheets will still be synced.")

# ── 9. transaction ────────────────────────────────────────────────────────────
problems = list(parse_problems) + list(dupe_problems)
created, updated, marked = [], [], []

t = Transaction(doc, "DrawingRegisterSync - Series Range")
t.Start()
try:
    for e in to_update:
        sh = existing_by_number[e['number']]
        try:
            if sh.Name != e['name']:
                sh.Name = e['name']
            if set_series(sh, e['series']):
                updated.append(e['number'])
            else:
                problems.append(
                    "{}: could not write Series Range '{}'."
                    .format(e['number'], e['series']))
        except Exception as err:
            problems.append(
                "{}: update failed - {}".format(e['number'], err))

    if titleblock_symbol is not None:
        if not titleblock_symbol.IsActive:
            titleblock_symbol.Activate()
        for e in to_create:
            try:
                ns             = ViewSheet.Create(doc, titleblock_symbol.Id)
                ns.SheetNumber = e['number']
                ns.Name        = e['name']
                if set_series(ns, e['series']):
                    created.append(e['number'])
                else:
                    problems.append(
                        "{}: sheet created but Series Range '{}' "
                        "could not be set."
                        .format(e['number'], e['series']))
            except Exception as err:
                problems.append(
                    "{}: create failed - {}".format(e['number'], err))

    for sh in to_mark_not_used:
        try:
            if set_series(sh, NOT_USED_SERIES):
                marked.append(sh.SheetNumber)
            else:
                problems.append(
                    "{}: could not mark NOT USED.".format(sh.SheetNumber))
        except Exception as err:
            problems.append(
                "{}: NOT USED failed - {}".format(sh.SheetNumber, err))

    t.Commit()

except Exception:
    if t.HasStarted():
        t.RollBack()
    import traceback
    output.print_md("## ERROR - Transaction rolled back")
    print(traceback.format_exc())
    script.exit()

# ── 10. console report ────────────────────────────────────────────────────────
output.print_md("---")
output.print_md("## Results")
print("{} updated  |  {} created  |  {} marked NOT USED".format(
    len(updated), len(created), len(marked)))

if updated:
    output.print_md("### Updated Sheets")
    for num in sorted(updated):
        e = by_number[num]
        print("  {} - {} [{}]".format(num, e['name'], e['series']))

if created:
    output.print_md("### Created Sheets")
    for num in sorted(created):
        e = by_number[num]
        print("  {} - {} [{}]".format(num, e['name'], e['series']))

if marked:
    output.print_md("### Marked NOT USED")
    for num in sorted(marked):
        print("  {}".format(num))

if problems:
    output.print_md("### Issues")
    for p in problems:
        print("  - {}".format(p))

output.print_md("---")
print("Done.")