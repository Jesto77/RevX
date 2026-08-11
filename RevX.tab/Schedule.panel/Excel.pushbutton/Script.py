# -*- coding: utf-8 -*-
"""Export Revit Schedule to Excel - Match Schedule Exactly

Preserves formatting, colors, images, borders, row/column sizes
and merged cells from Revit schedules.

Compatible: Revit 2024, 2025, 2026, 2027
Requires: Microsoft Excel installed
"""

__title__ = "Schedule\nto Excel"
__author__ = "Jesto Joy"

import os
import tempfile
import shutil
import clr
import System
from System import Type, Activator
from System.Reflection import BindingFlags

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Drawing')

from Autodesk.Revit.DB import (
    ViewSchedule, FilteredElementCollector, SectionType,
    ElementId, BuiltInParameter, ImageType, Element,
    StorageType, ModelPathUtils
)

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()


# ============================================================
# Unit conversion constant
# ============================================================
# Revit internal units for lengths are DECIMAL FEET.
# 1 ft = 12 in, 1 in = 72 pt  ->  1 ft = 864 pt
FEET_TO_POINTS = 12.0 * 72.0

_PLAUSIBLE_FONT_MIN = 5.0
_PLAUSIBLE_FONT_MAX = 24.0


def convert_text_size_to_points(ts):
    """Try both raw and feet-converted interpretations of TextSize,
    keep whichever lands in a plausible schedule-text range."""
    try:
        ts = float(ts)
    except:
        return 10.0
    if ts <= 0:
        return 10.0

    as_is = ts
    as_feet = ts * FEET_TO_POINTS

    for candidate in (as_is, as_feet):
        if _PLAUSIBLE_FONT_MIN <= candidate <= _PLAUSIBLE_FONT_MAX:
            return round(candidate, 1)

    best = min((as_is, as_feet), key=lambda c: abs(c - 9.0))
    best = max(6.0, min(best, 18.0))
    return round(best, 1)


# ============================================================
# COM Helpers
# ============================================================
def _to_array(args):
    if not args:
        return System.Array.CreateInstance(System.Object, 0)
    arr = System.Array.CreateInstance(System.Object, len(args))
    for i, a in enumerate(args):
        arr[i] = a
    return arr


def com_get(obj, name, *args):
    return obj.GetType().InvokeMember(
        name, BindingFlags.GetProperty, None, obj, _to_array(args))


def com_set(obj, name, value):
    obj.GetType().InvokeMember(
        name, BindingFlags.SetProperty, None, obj, _to_array([value]))


def com_call(obj, name, *args):
    return obj.GetType().InvokeMember(
        name, BindingFlags.InvokeMethod, None, obj, _to_array(args))


# ============================================================
# Excel App
# ============================================================
def create_excel_app():
    try:
        excel_type = Type.GetTypeFromProgID("Excel.Application")
        if excel_type is None:
            raise Exception("Excel not installed.")
        app = Activator.CreateInstance(excel_type)
        com_set(app, "Visible", False)
        com_set(app, "DisplayAlerts", False)
        com_set(app, "ScreenUpdating", False)
        return app
    except Exception as e:
        forms.alert("Could not start Excel:\n{}".format(e), exitscript=True)


# ============================================================
# Color Helpers
# ============================================================
def revit_color_to_ole(revit_color, default=None):
    try:
        if revit_color is None:
            return default
        r = int(revit_color.Red)
        g = int(revit_color.Green)
        b = int(revit_color.Blue)
        return (b << 16) | (g << 8) | r
    except:
        return default


def is_valid_color(revit_color):
    try:
        if not revit_color.IsValid:
            return False
        r = int(revit_color.Red)
        g = int(revit_color.Green)
        b = int(revit_color.Blue)
        if r == 255 and g == 255 and b == 255:
            return False
        return True
    except:
        return False


# ============================================================
# Cell Style
# ============================================================
def get_cell_format(section, row, col):
    XL_LEFT = -4131
    XL_CENTER = -4108
    XL_RIGHT = -4152
    XL_TOP = -4160
    XL_BOTTOM = -4107

    fmt = {
        'text': '',
        'bg_color': None,
        'font_color': 0,
        'font_name': 'Arial',
        'font_size': 10.0,
        'bold': False,
        'italic': False,
        'underline': False,
        'h_align': XL_CENTER,
        'v_align': XL_CENTER,
    }

    try:
        fmt['text'] = section.GetCellText(row, col) or ''
    except:
        pass

    cell_style = None
    try:
        cell_style = section.GetTableCellStyle(row, col)
    except:
        try:
            cell_style = section.GetCellStyle(row, col)
        except:
            pass

    if cell_style is None:
        return fmt

    try:
        bg = cell_style.BackgroundColor
        if is_valid_color(bg):
            fmt['bg_color'] = revit_color_to_ole(bg)
    except:
        pass

    try:
        fg = cell_style.TextColor
        if fg and fg.IsValid:
            fmt['font_color'] = revit_color_to_ole(fg, 0) or 0
    except:
        pass

    try:
        fn = cell_style.FontName
        if fn:
            fmt['font_name'] = fn
    except:
        pass

    try:
        ts = float(cell_style.TextSize)
        fmt['font_size'] = convert_text_size_to_points(ts)
    except:
        pass

    try: fmt['bold'] = bool(cell_style.IsFontBold)
    except: pass
    try: fmt['italic'] = bool(cell_style.IsFontItalic)
    except: pass
    try: fmt['underline'] = bool(cell_style.IsFontUnderline)
    except: pass

    try:
        h = int(cell_style.FontHorizontalAlignment)
        h_map = {0: XL_LEFT, 1: XL_CENTER, 2: XL_RIGHT}
        fmt['h_align'] = h_map.get(h, XL_CENTER)
    except:
        pass

    try:
        v = int(cell_style.FontVerticalAlignment)
        v_map = {0: XL_TOP, 1: XL_CENTER, 2: XL_BOTTOM}
        fmt['v_align'] = v_map.get(v, XL_CENTER)
    except:
        pass

    return fmt


# ============================================================
# Image Extraction
# ============================================================
def get_image_id_from_element(element):
    if element is None:
        return None

    # Instance params
    try:
        for param in element.Parameters:
            try:
                if param.StorageType == StorageType.ElementId:
                    val = param.AsElementId()
                    if val and val != ElementId.InvalidElementId and val.IntegerValue > 0:
                        candidate = doc.GetElement(val)
                        if candidate and isinstance(candidate, ImageType):
                            return val
            except:
                continue
    except:
        pass

    # Builtin instance
    for bip in [BuiltInParameter.ALL_MODEL_IMAGE,
                BuiltInParameter.ALL_MODEL_TYPE_IMAGE]:
        try:
            param = element.get_Parameter(bip)
            if param:
                val = param.AsElementId()
                if val and val != ElementId.InvalidElementId and val.IntegerValue > 0:
                    return val
        except:
            continue

    # Type params
    try:
        type_id = element.GetTypeId()
        if type_id and type_id != ElementId.InvalidElementId:
            elem_type = doc.GetElement(type_id)
            if elem_type:
                try:
                    for param in elem_type.Parameters:
                        try:
                            if param.StorageType == StorageType.ElementId:
                                val = param.AsElementId()
                                if val and val != ElementId.InvalidElementId and val.IntegerValue > 0:
                                    candidate = doc.GetElement(val)
                                    if candidate and isinstance(candidate, ImageType):
                                        return val
                        except:
                            continue
                except:
                    pass

                for bip in [BuiltInParameter.ALL_MODEL_TYPE_IMAGE,
                            BuiltInParameter.ALL_MODEL_IMAGE]:
                    try:
                        param = elem_type.get_Parameter(bip)
                        if param:
                            val = param.AsElementId()
                            if val and val != ElementId.InvalidElementId and val.IntegerValue > 0:
                                return val
                    except:
                        continue
    except:
        pass

    return None


def save_image_to_file(image_id, temp_dir):
    if image_id is None or image_id == ElementId.InvalidElementId:
        return None

    try:
        image_element = doc.GetElement(image_id)
        if image_element is None:
            return None

        # Check existing
        for ext in ['.png', '.jpg', '.jpeg', '.bmp']:
            existing = os.path.join(temp_dir, "img_{}{}".format(image_id.IntegerValue, ext))
            if os.path.exists(existing):
                return existing

        # Method 1: Path property
        try:
            path_prop = image_element.Path
            if path_prop and os.path.exists(path_prop):
                ext = os.path.splitext(path_prop)[1].lower() or '.png'
                dest = os.path.join(temp_dir, "img_{}{}".format(image_id.IntegerValue, ext))
                shutil.copy(path_prop, dest)
                return dest
        except:
            pass

        # Method 2: GetImage()
        try:
            img = image_element.GetImage()
            if img is not None:
                dest = os.path.join(temp_dir, "img_{}.png".format(image_id.IntegerValue))
                img.Save(dest)
                return dest
        except:
            pass

        # Method 3: External ref
        try:
            ext_ref = image_element.GetExternalFileReference()
            if ext_ref:
                path = ModelPathUtils.ConvertModelPathToUserVisiblePath(
                    ext_ref.GetAbsolutePath())
                if path and os.path.exists(path):
                    ext = os.path.splitext(path)[1].lower() or '.png'
                    dest = os.path.join(temp_dir, "img_{}{}".format(image_id.IntegerValue, ext))
                    shutil.copy(path, dest)
                    return dest
        except:
            pass

        return None
    except Exception as e:
        logger.debug("Save img err: {}".format(e))
        return None


def find_image_column_indices(schedule):
    image_cols = set()

    try:
        definition = schedule.Definition

        visible_fields = []
        try:
            ordered_ids = definition.GetFieldOrder()
            for fid in list(ordered_ids):
                try:
                    field = definition.GetField(fid)
                    if not field.IsHidden:
                        visible_fields.append(field)
                except:
                    continue
        except:
            try:
                for i in range(definition.GetFieldCount()):
                    try:
                        field = definition.GetField(i)
                        if not field.IsHidden:
                            visible_fields.append(field)
                    except:
                        continue
            except:
                pass

        for col_index, field in enumerate(visible_fields):
            try:
                field_name = (field.GetName() or "")
                fnl = field_name.lower()
                param_id = field.ParameterId

                is_image = False
                if any(w in fnl for w in ['image', 'picture', 'photo']):
                    is_image = True
                try:
                    if param_id.IntegerValue in [
                        int(BuiltInParameter.ALL_MODEL_IMAGE),
                        int(BuiltInParameter.ALL_MODEL_TYPE_IMAGE)
                    ]:
                        is_image = True
                except:
                    pass

                if is_image:
                    image_cols.add(col_index)
            except:
                continue
    except Exception as e:
        logger.debug("Find img cols: {}".format(e))

    return image_cols


def get_schedule_body_elements(schedule):
    try:
        collector = FilteredElementCollector(doc, schedule.Id)
        return list(collector.ToElements())
    except Exception as e:
        logger.debug("Get elements err: {}".format(e))
        return []


# ============================================================
# Column widths
# ============================================================
MIN_IMAGE_COL_POINTS = 90.0
MIN_TEXT_COL_POINTS = 40.0
IMAGE_ROW_MIN_POINTS = 80.0
MAX_PLAUSIBLE_REVIT_COL_POINTS = 500.0
MIN_PLAUSIBLE_REVIT_COL_POINTS = 15.0


def calculate_content_col_widths_points(section, image_cols):
    """Content-driven column widths, in points."""
    n_rows = section.NumberOfRows
    n_cols = section.NumberOfColumns
    col_widths = {}

    for c in range(n_cols):
        if c in image_cols:
            col_widths[c] = MIN_IMAGE_COL_POINTS
            continue

        max_len = 5
        for r in range(n_rows):
            try:
                text = section.GetCellText(r, c) or ''
                for line in text.split('\n'):
                    stripped = line.strip()
                    if len(stripped) > max_len:
                        max_len = len(stripped)
            except:
                pass

        if c == 0:
            chars = min(max(max_len + 2, 6), 8)
        else:
            chars = min(max_len + 2, 30)

        col_widths[c] = max(chars * 7.0, MIN_TEXT_COL_POINTS)

    return col_widths


def get_plausible_revit_column_width_points(section, c):
    """Return Revit's stored column width in points, if plausible."""
    try:
        w_ft = section.GetColumnWidth(c)
        pts = float(w_ft) * FEET_TO_POINTS
        if MIN_PLAUSIBLE_REVIT_COL_POINTS <= pts <= MAX_PLAUSIBLE_REVIT_COL_POINTS:
            return pts
    except:
        pass
    return None


def set_column_width_points(ws, col_index_1based, target_points):
    """Set column width in points using two-pass calibration."""
    if target_points <= 0:
        target_points = MIN_TEXT_COL_POINTS

    col = com_get(ws, "Columns", col_index_1based)
    guess = max(target_points / 7.0, 2.0)
    try:
        com_set(col, "ColumnWidth", guess)
        actual_pts = float(com_get(col, "Width"))
        if actual_pts > 0.1:
            corrected = guess * (target_points / actual_pts)
            corrected = max(corrected, guess)
            com_set(col, "ColumnWidth", corrected)
    except:
        try:
            com_set(col, "ColumnWidth", guess)
        except:
            pass


def apply_column_widths(ws, section, image_cols):
    n_cols = section.NumberOfColumns
    content_widths = calculate_content_col_widths_points(section, image_cols)

    for c in range(n_cols):
        pts = content_widths.get(c, MIN_TEXT_COL_POINTS)
        revit_pts = get_plausible_revit_column_width_points(section, c)
        if revit_pts is not None:
            pts = max(pts, revit_pts)
        if c in image_cols:
            pts = max(pts, MIN_IMAGE_COL_POINTS)
        set_column_width_points(ws, c + 1, pts)


# ============================================================
# Borders
# ============================================================
def apply_cell_borders(target):
    """Apply thin black border to all four edges of a cell/range."""
    try:
        borders = com_get(target, "Borders")
    except:
        return
    for edge_idx in (7, 8, 9, 10):
        try:
            border = com_get(borders, "Item", edge_idx)
            com_set(border, "LineStyle", 1)
            com_set(border, "Weight", 2)
            com_set(border, "Color", 0x000000)
        except:
            continue


def apply_full_borders(ws, start_row, end_row, n_cols):
    """Apply full grid to table plus bolder outer boundary."""
    BLACK_RGB = 0x000000

    try:
        top_left = com_get(ws, "Cells", start_row, 1)
        bottom_right = com_get(ws, "Cells", end_row, n_cols)
        full_range = com_get(ws, "Range", top_left, bottom_right)

        borders = com_get(full_range, "Borders")

        for edge_idx in [7, 8, 9, 10, 11, 12]:
            try:
                border = com_get(borders, "Item", edge_idx)
                com_set(border, "LineStyle", 1)
                com_set(border, "Weight", 2)
                com_set(border, "Color", BLACK_RGB)
            except:
                pass

        for edge_idx in [7, 8, 9, 10]:
            try:
                border = com_get(borders, "Item", edge_idx)
                com_set(border, "LineStyle", 1)
                com_set(border, "Weight", -4138)
                com_set(border, "Color", BLACK_RGB)
            except:
                pass
    except Exception as e:
        logger.debug("Apply borders err: {}".format(e))


# ============================================================
# Export Section
# ============================================================
def export_section(schedule, section_type, ws, start_row, temp_dir,
                   image_cols, row_elements, apply_col_widths=False,
                   apply_default_header_style=False,
                   start_element_idx=0):
    try:
        section = schedule.GetTableData().GetSectionData(section_type)
    except:
        return start_row, [], start_element_idx

    if section is None:
        return start_row, [], start_element_idx

    n_rows = section.NumberOfRows
    n_cols = section.NumberOfColumns

    if n_rows == 0 or n_cols == 0:
        return start_row, [], start_element_idx

    merged_tracker = set()
    image_cells = []
    image_row_set = set()

    if apply_col_widths:
        apply_column_widths(ws, section, image_cols)

    element_iter_idx = start_element_idx

    for r in range(n_rows):
        excel_row = start_row + r

        is_category_row = False
        if section_type == SectionType.Body:
            try:
                first_cell = section.GetCellText(r, 0) or ''
                filled_count = 0
                for cc in range(n_cols):
                    if (section.GetCellText(r, cc) or '').strip():
                        filled_count += 1
                if filled_count <= 2 and first_cell.strip() and not first_cell.strip().isdigit():
                    is_category_row = True
            except:
                pass

        if (section_type == SectionType.Body and image_cols
                and not is_category_row):
            image_row_set.add(excel_row)

        for c in range(n_cols):
            if (r, c) in merged_tracker:
                continue

            excel_col = c + 1
            cell = com_get(ws, "Cells", excel_row, excel_col)

            merge_info = None
            try:
                merge = section.GetMergedCell(r, c)
                if merge:
                    top = merge.Top
                    bottom = merge.Bottom
                    left = merge.Left
                    right = merge.Right
                    if top != bottom or left != right:
                        for mr in range(top, bottom + 1):
                            for mc in range(left, right + 1):
                                merged_tracker.add((mr, mc))
                        if r == top and c == left:
                            merge_info = (top, bottom, left, right)
                        else:
                            continue
            except:
                pass

            fmt = get_cell_format(section, r, c)

            if apply_default_header_style and fmt['bg_color'] is None:
                fmt['bg_color'] = 0xD9D9D9
                fmt['bold'] = True

            # Image
            image_path = None
            if (section_type == SectionType.Body and c in image_cols
                    and not is_category_row):
                if row_elements and element_iter_idx < len(row_elements):
                    try:
                        elem = row_elements[element_iter_idx]
                        image_id = get_image_id_from_element(elem)
                        if image_id and image_id != ElementId.InvalidElementId:
                            image_path = save_image_to_file(image_id, temp_dir)
                    except Exception as e:
                        logger.debug("Row img err: {}".format(e))

            if not image_path:
                try:
                    com_set(cell, "Value2", fmt['text'])
                except:
                    try:
                        com_set(cell, "Value", fmt['text'])
                    except:
                        pass
            else:
                try:
                    com_set(cell, "Value2", "")
                except:
                    pass

            try:
                font = com_get(cell, "Font")
                com_set(font, "Name", fmt['font_name'])
                com_set(font, "Size", float(fmt['font_size']))
                com_set(font, "Bold", fmt['bold'])
                com_set(font, "Italic", fmt['italic'])
                com_set(font, "Underline", fmt['underline'])
                com_set(font, "Color", fmt['font_color'])
            except:
                pass

            if fmt['bg_color'] is not None:
                try:
                    interior = com_get(cell, "Interior")
                    com_set(interior, "Color", fmt['bg_color'])
                    com_set(interior, "Pattern", 1)
                except:
                    pass

            try:
                com_set(cell, "HorizontalAlignment", fmt['h_align'])
                com_set(cell, "VerticalAlignment", fmt['v_align'])
                com_set(cell, "WrapText", True)
            except:
                pass

            if merge_info:
                try:
                    top, bottom, left, right = merge_info
                    top_left = com_get(ws, "Cells", start_row + top, left + 1)
                    bottom_right = com_get(ws, "Cells", start_row + bottom, right + 1)
                    merge_range = com_get(ws, "Range", top_left, bottom_right)
                    com_call(merge_range, "Merge")
                    border_target = merge_range
                except:
                    border_target = cell
            else:
                border_target = cell

            apply_cell_borders(border_target)

            if image_path:
                image_cells.append((excel_row, excel_col, image_path))

        if section_type == SectionType.Body and not is_category_row:
            element_iter_idx += 1

    # AutoFit rows after content is placed
    if n_rows > 0:
        try:
            top_left = com_get(ws, "Cells", start_row, 1)
            bottom_right = com_get(ws, "Cells", start_row + n_rows - 1, n_cols)
            section_range = com_get(ws, "Range", top_left, bottom_right)
            entire_rows = com_get(section_range, "EntireRow")
            com_call(entire_rows, "AutoFit")
        except Exception as e:
            logger.debug("AutoFit err: {}".format(e))

        # Enforce minimum height for image rows
        for img_row in image_row_set:
            try:
                row_range = com_get(ws, "Rows", img_row)
                current_h = float(com_get(row_range, "RowHeight"))
                if current_h < IMAGE_ROW_MIN_POINTS:
                    com_set(row_range, "RowHeight", IMAGE_ROW_MIN_POINTS)
            except Exception as e:
                logger.debug("Image row height err: {}".format(e))

    return start_row + n_rows, image_cells, element_iter_idx


# ============================================================
# Insert Images (aspect-ratio preserving)
# ============================================================
def _get_image_pixel_size(image_path):
    """Return (width, height) in pixels, or None if unreadable."""
    try:
        from System.Drawing import Image as SDImage
        img = SDImage.FromFile(image_path)
        w, h = img.Width, img.Height
        img.Dispose()
        if w > 0 and h > 0:
            return float(w), float(h)
    except:
        pass
    return None


def insert_images(ws, image_cells):
    inserted = 0
    PAD = 3.0

    for excel_row, excel_col, image_path in image_cells:
        try:
            cell = com_get(ws, "Cells", excel_row, excel_col)
            cell_left = float(com_get(cell, "Left"))
            cell_top = float(com_get(cell, "Top"))
            cell_w = float(com_get(cell, "Width"))
            cell_h = float(com_get(cell, "Height"))

            avail_w = max(cell_w - 2 * PAD, 1.0)
            avail_h = max(cell_h - 2 * PAD, 1.0)

            px_size = _get_image_pixel_size(image_path)
            if px_size:
                img_w, img_h = px_size
                scale = min(avail_w / img_w, avail_h / img_h)
                draw_w = img_w * scale
                draw_h = img_h * scale
            else:
                draw_w = avail_w
                draw_h = avail_h

            left = cell_left + (cell_w - draw_w) / 2.0
            top = cell_top + (cell_h - draw_h) / 2.0

            shapes = com_get(ws, "Shapes")
            com_call(
                shapes, "AddPicture",
                image_path, False, True,
                left, top, draw_w, draw_h
            )
            inserted += 1
        except Exception as e:
            logger.debug("Insert img err ({},{}): {}".format(excel_row, excel_col, e))
    return inserted


# ============================================================
# Populate Worksheet
# ============================================================
def populate_worksheet(schedule, ws, temp_dir):
    image_cols = find_image_column_indices(schedule)
    row_elements = get_schedule_body_elements(schedule)

    all_images = []
    row = 1
    elem_idx = 0
    total_cols = 0

    try:
        body_section = schedule.GetTableData().GetSectionData(SectionType.Body)
        if body_section:
            total_cols = body_section.NumberOfColumns
    except:
        pass

    row, imgs, elem_idx = export_section(
        schedule, SectionType.Header, ws, row, temp_dir,
        image_cols, row_elements,
        apply_col_widths=True,
        apply_default_header_style=True,
        start_element_idx=elem_idx
    )
    all_images.extend(imgs)

    row, imgs, elem_idx = export_section(
        schedule, SectionType.Body, ws, row, temp_dir,
        image_cols, row_elements,
        apply_col_widths=False,
        apply_default_header_style=False,
        start_element_idx=elem_idx
    )
    all_images.extend(imgs)

    row, imgs, elem_idx = export_section(
        schedule, SectionType.Summary, ws, row, temp_dir,
        image_cols, row_elements,
        apply_col_widths=False,
        apply_default_header_style=False,
        start_element_idx=elem_idx
    )
    all_images.extend(imgs)

    end_row = row - 1
    if total_cols > 0 and end_row >= 1:
        apply_full_borders(ws, 1, end_row, total_cols)

    try:
        app = com_get(ws, "Application")
        com_call(app, "Calculate")
    except:
        pass

    if all_images:
        insert_images(ws, all_images)


# ============================================================
# Export Functions
# ============================================================
def export_schedule(schedule, filepath, excel_app, temp_dir):
    workbooks = com_get(excel_app, "Workbooks")
    wb = com_call(workbooks, "Add")
    worksheets = com_get(wb, "Worksheets")
    ws = com_get(worksheets, "Item", 1)

    sheet_name = schedule.Name
    for ch in ['\\', '/', '*', '?', ':', '[', ']']:
        sheet_name = sheet_name.replace(ch, '_')
    com_set(ws, "Name", sheet_name[:31])

    populate_worksheet(schedule, ws, temp_dir)

    if os.path.exists(filepath):
        try: os.remove(filepath)
        except: pass

    com_call(wb, "SaveAs", filepath, 51)
    com_call(wb, "Close", False)


def export_all_to_one(schedules, filepath, excel_app, temp_dir):
    workbooks = com_get(excel_app, "Workbooks")
    wb = com_call(workbooks, "Add")
    worksheets = com_get(wb, "Worksheets")

    while com_get(worksheets, "Count") > 1:
        last_ws = com_get(worksheets, "Item", com_get(worksheets, "Count"))
        com_call(last_ws, "Delete")

    used_names = set()

    for idx, schedule in enumerate(schedules):
        worksheets = com_get(wb, "Worksheets")
        if idx == 0:
            ws = com_get(worksheets, "Item", 1)
        else:
            count = com_get(worksheets, "Count")
            after_ws = com_get(worksheets, "Item", count)
            ws = com_call(worksheets, "Add",
                          System.Reflection.Missing.Value, after_ws)

        base_name = schedule.Name
        for ch in ['\\', '/', '*', '?', ':', '[', ']']:
            base_name = base_name.replace(ch, '_')
        base_name = base_name[:31]

        name = base_name
        count = 1
        while name in used_names:
            suffix = "_{}".format(count)
            name = (base_name[:31 - len(suffix)]) + suffix
            count += 1
        used_names.add(name)
        com_set(ws, "Name", name)

        populate_worksheet(schedule, ws, temp_dir)

    if os.path.exists(filepath):
        try: os.remove(filepath)
        except: pass

    com_call(wb, "SaveAs", filepath, 51)
    com_call(wb, "Close", False)


# ============================================================
# UI Helpers
# ============================================================
def get_all_schedules():
    schedules = FilteredElementCollector(doc)\
        .OfClass(ViewSchedule).ToElements()
    result = []
    for sch in schedules:
        try:
            if sch.IsTemplate: continue
            if sch.IsTitleblockRevisionSchedule: continue
            result.append(sch)
        except:
            result.append(sch)
    return sorted(result, key=lambda x: x.Name)


class ScheduleOption(forms.TemplateListItem):
    @property
    def name(self):
        return self.item.Name


# ============================================================
# MAIN
# ============================================================
def main():
    active_view = doc.ActiveView
    schedules_to_export = []

    if isinstance(active_view, ViewSchedule) and not active_view.IsTemplate:
        choice = forms.alert(
            "Export active schedule?\n\n'{}'".format(active_view.Name),
            options=["Yes - Active Only", "No - Choose Multiple", "Cancel"]
        )
        if choice == "Cancel" or choice is None:
            script.exit()
        if choice == "Yes - Active Only":
            schedules_to_export = [active_view]

    if not schedules_to_export:
        all_scheds = get_all_schedules()
        if not all_scheds:
            forms.alert("No schedules found.", exitscript=True)

        selected = forms.SelectFromList.show(
            [ScheduleOption(s) for s in all_scheds],
            title="Select Schedules",
            multiselect=True, button_name="Export"
        )
        if not selected:
            script.exit()
        schedules_to_export = selected

    mode = "Separate Files"
    if len(schedules_to_export) > 1:
        mode = forms.alert(
            "How to save?",
            options=["One File (Multiple Sheets)", "Separate Files"]
        )
        if not mode:
            script.exit()

    folder = forms.pick_folder(title="Select Output Folder")
    if not folder:
        script.exit()

    temp_dir = tempfile.mkdtemp(prefix="revit_sch_img_")

    output.print_md("## 🚀 Exporting Schedules...")
    excel_app = create_excel_app()

    success_count = 0
    fail_count = 0

    try:
        if mode == "One File (Multiple Sheets)":
            project_name = doc.Title or "Schedules"
            for ch in ['\\', '/', '*', '?', ':', '"', '<', '>', '|']:
                project_name = project_name.replace(ch, '_')
            filepath = os.path.join(folder, project_name + "_Schedules.xlsx")

            try:
                export_all_to_one(schedules_to_export, filepath, excel_app, temp_dir)
                output.print_md("✅ Exported {} schedule(s) to: `{}`".format(
                    len(schedules_to_export), filepath))
                success_count = len(schedules_to_export)
            except Exception as e:
                output.print_md("❌ Failed: {}".format(e))
                logger.error("Error: {}".format(e))
                fail_count = len(schedules_to_export)
        else:
            with forms.ProgressBar(title="Exporting...", cancellable=True) as pb:
                for i, sch in enumerate(schedules_to_export):
                    if pb.cancelled:
                        break
                    pb.update_progress(i + 1, len(schedules_to_export))

                    safe = sch.Name
                    for ch in ['\\', '/', '*', '?', ':', '"', '<', '>', '|']:
                        safe = safe.replace(ch, '_')
                    filepath = os.path.join(folder, safe + ".xlsx")

                    try:
                        export_schedule(sch, filepath, excel_app, temp_dir)
                        output.print_md("✅ **{}**".format(sch.Name))
                        success_count += 1
                    except Exception as e:
                        output.print_md("❌ **{}** — {}".format(sch.Name, e))
                        logger.error("Fail {}: {}".format(sch.Name, e))
                        fail_count += 1
    finally:
        try:
            com_set(excel_app, "ScreenUpdating", True)
            com_set(excel_app, "DisplayAlerts", True)
            com_call(excel_app, "Quit")
        except: pass

        try:
            System.Runtime.InteropServices.Marshal.ReleaseComObject(excel_app)
        except: pass

        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except: pass

    output.print_md("---")
    output.print_md("## ✔ Done: {} succeeded | ✖ {} failed".format(success_count, fail_count))

    if success_count > 0:
        try: os.startfile(folder)
        except: pass


if __name__ == '__main__':
    main()