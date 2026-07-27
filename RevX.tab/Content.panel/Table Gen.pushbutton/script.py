# -*- coding: utf-8 -*-
"""TableGen - Excel/CSV to native Revit Schedule."""

__title__ = "TableGen"
__author__ = "Jesto Joy"
__doc__ = "Import Excel/CSV tables into Revit as native schedules."

SCRIPT_VERSION = "v32 (2026-07-16) configurable header rows in split"

import os
import re
import json
import codecs
import zipfile
import tempfile
from datetime import datetime
import xml.etree.ElementTree as ET

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    BuiltInCategory,
    ElementId,
    ViewSchedule,
    ScheduleSheetInstance,
    SectionType,
    TableCellStyle,
    TableCellStyleOverrideOptions,
    TableMergedCell,
    HorizontalAlignmentStyle,
    VerticalAlignmentStyle,
    XYZ,
)
from Autodesk.Revit.DB import Color as RevitColor

from System.Windows import (
    GridLength, GridUnitType, Thickness, VerticalAlignment,
    HorizontalAlignment, FontWeights, Clipboard, TextTrimming,
)
from System.Windows.Controls import (
    Grid, ColumnDefinition, RowDefinition, TextBlock, CheckBox,
    Border as WBorder,
)
from System.Windows.Media import SolidColorBrush, Color

logger = script.get_logger()
doc = revit.doc

MM_TO_FT = 1.0 / 304.8
XLNS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RELNS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
DOCRELNS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
ANS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'

IMG_ASPECT = 2.0 / 3.0
IMG_BUFFER = 1.25
PX_PER_MM = 12.0
CANVAS_CAP_PX = 2000

# Default header rows - used as fallback only; user can override per-split
SPLIT_HEADER_ROWS = 3
CATEGORY_MERGE_MIN_FRACTION = 0.6

INDEXED_COLORS = {
    0: (0, 0, 0), 1: (255, 255, 255), 2: (255, 0, 0), 3: (0, 255, 0),
    4: (0, 0, 255), 5: (255, 255, 0), 6: (255, 0, 255), 7: (0, 255, 255),
    8: (0, 0, 0), 9: (255, 255, 255), 10: (255, 0, 0), 11: (0, 255, 0),
    12: (0, 0, 255), 13: (255, 255, 0), 14: (255, 0, 255),
    15: (0, 255, 255), 16: (128, 0, 0), 17: (0, 128, 0), 18: (0, 0, 128),
    19: (128, 128, 0), 20: (128, 0, 128), 21: (0, 128, 128),
    22: (192, 192, 192), 23: (128, 128, 128), 24: (153, 153, 255),
    25: (153, 51, 102), 26: (255, 255, 204), 27: (204, 255, 255),
    28: (102, 0, 102), 29: (255, 128, 128), 30: (0, 102, 204),
    31: (204, 204, 255), 40: (0, 204, 255), 41: (204, 255, 255),
    42: (204, 255, 204), 43: (255, 255, 153), 44: (153, 204, 255),
    45: (255, 153, 204), 46: (204, 153, 255), 47: (255, 204, 153),
    48: (51, 102, 255), 49: (51, 204, 204), 50: (153, 204, 0),
    51: (255, 204, 0), 52: (255, 153, 0), 53: (255, 102, 0),
    54: (102, 102, 153), 55: (150, 150, 150), 56: (0, 51, 102),
    57: (51, 153, 102), 58: (0, 51, 0), 59: (51, 51, 0),
    60: (153, 51, 0), 61: (153, 51, 102), 62: (51, 51, 153),
    63: (51, 51, 51), 64: (0, 0, 0), 65: (255, 255, 255),
}


# ----------------------------------------------------------------- helpers
def get_id_value(element_id):
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def brush(rgb_or_hex):
    if isinstance(rgb_or_hex, tuple):
        r, g, b = rgb_or_hex
        return SolidColorBrush(Color.FromRgb(r, g, b))
    h = rgb_or_hex.lstrip('#')
    return SolidColorBrush(Color.FromRgb(
        int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))


def to_text(value):
    try:
        return unicode(value)
    except NameError:
        return str(value)


def excel_width_to_mm(width_chars):
    px = int(round(width_chars * 7.0 + 5))
    return px * 25.4 / 96.0


def excel_height_to_mm(points):
    return points * 25.4 / 72.0


def apply_tint(rgb, tint):
    r, g, b = rgb
    out = []
    for c in (r, g, b):
        if tint > 0:
            c = c + (255.0 - c) * tint
        elif tint < 0:
            c = c * (1.0 + tint)
        out.append(max(0, min(255, int(round(c)))))
    return tuple(out)


# ----------------------------------------------------------- xlsx theme
def _parse_theme(z):
    theme = []
    name = None
    for n in z.namelist():
        if n.startswith('xl/theme/'):
            name = n
            break
    if not name:
        return theme
    try:
        root = ET.fromstring(z.read(name))
        scheme = root.find('.//' + ANS + 'clrScheme')
        if scheme is None:
            return theme
        order = ['dk1', 'lt1', 'dk2', 'lt2', 'accent1', 'accent2',
                 'accent3', 'accent4', 'accent5', 'accent6',
                 'hlink', 'folHlink']
        raw = {}
        for tag in order:
            el = scheme.find(ANS + tag)
            if el is None:
                raw[tag] = (0, 0, 0)
                continue
            srgb = el.find(ANS + 'srgbClr')
            sysc = el.find(ANS + 'sysClr')
            hexv = None
            if srgb is not None:
                hexv = srgb.get('val')
            elif sysc is not None:
                hexv = sysc.get('lastClr', '000000')
            if hexv:
                raw[tag] = (int(hexv[0:2], 16), int(hexv[2:4], 16),
                            int(hexv[4:6], 16))
            else:
                raw[tag] = (0, 0, 0)
        idx_order = ['lt1', 'dk1', 'lt2', 'dk2', 'accent1', 'accent2',
                     'accent3', 'accent4', 'accent5', 'accent6',
                     'hlink', 'folHlink']
        theme = [raw[t] for t in idx_order]
    except Exception:
        pass
    return theme


def _resolve_color(el, theme):
    if el is None:
        return None
    rgb = el.get('rgb')
    if rgb and len(rgb) >= 6:
        h = rgb[-6:]
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    th = el.get('theme')
    if th is not None:
        try:
            base = theme[int(th)]
        except Exception:
            return None
        tint = el.get('tint')
        if tint:
            try:
                base = apply_tint(base, float(tint))
            except Exception:
                pass
        return base
    idx = el.get('indexed')
    if idx is not None:
        idx = int(idx)
        if idx in (64, 65):
            return None
        return INDEXED_COLORS.get(idx)
    return None


# ----------------------------------------------------------- xlsx styles
def _parse_styles(z, theme):
    fonts, fills, xfs = [], [], []
    if 'xl/styles.xml' not in z.namelist():
        return fonts, fills, xfs
    try:
        root = ET.fromstring(z.read('xl/styles.xml'))
        fonts_el = root.find(XLNS + 'fonts')
        if fonts_el is not None:
            for f in fonts_el.findall(XLNS + 'font'):
                sz = f.find(XLNS + 'sz')
                b_el = f.find(XLNS + 'b')
                i_el = f.find(XLNS + 'i')
                is_bold = (b_el is not None and
                           b_el.get('val', '1') not in ('0', 'false'))
                is_italic = (i_el is not None and
                             i_el.get('val', '1') not in ('0', 'false'))
                fonts.append({
                    'bold': is_bold,
                    'italic': is_italic,
                    'color': _resolve_color(
                        f.find(XLNS + 'color'), theme),
                    'size': float(sz.get('val'))
                    if sz is not None else None,
                    'name': (f.find(XLNS + 'name').get('val')
                             if f.find(XLNS + 'name') is not None
                             else None),
                })
        fills_el = root.find(XLNS + 'fills')
        if fills_el is not None:
            for fl in fills_el.findall(XLNS + 'fill'):
                pat = fl.find(XLNS + 'patternFill')
                bg = None
                if pat is not None and pat.get('patternType') == 'solid':
                    bg = _resolve_color(
                        pat.find(XLNS + 'fgColor'), theme)
                    if bg is None:
                        bg = _resolve_color(
                            pat.find(XLNS + 'bgColor'), theme)
                    if bg == (255, 255, 255):
                        bg = None
                fills.append(bg)
        style_xfs = []
        sxfs_el = root.find(XLNS + 'cellStyleXfs')
        if sxfs_el is not None:
            for xf in sxfs_el.findall(XLNS + 'xf'):
                s_fill_on = xf.get('applyFill') in ('1', 'true')
                s_font_on = xf.get('applyFont') in ('1', 'true')
                style_xfs.append({
                    'font': int(xf.get('fontId', '0'))
                    if s_font_on else 0,
                    'fill': int(xf.get('fillId', '0'))
                    if s_fill_on else 0,
                })
        xfs_el = root.find(XLNS + 'cellXfs')
        if xfs_el is not None:
            for xf in xfs_el.findall(XLNS + 'xf'):
                align = xf.find(XLNS + 'alignment')
                own_fill = int(xf.get('fillId', '0'))
                own_font = int(xf.get('fontId', '0'))
                xfid = xf.get('xfId')
                base = None
                if xfid is not None:
                    try:
                        base = style_xfs[int(xfid)]
                    except Exception:
                        base = None
                if xf.get('applyFill') in ('0', 'false'):
                    fill_idx = base['fill'] if base else 0
                else:
                    fill_idx = own_fill
                if xf.get('applyFont') in ('0', 'false'):
                    font_idx = base['font'] if base else 0
                else:
                    font_idx = own_font
                xfs.append({
                    'font': font_idx,
                    'fill': fill_idx,
                    'halign': (align.get('horizontal')
                               if align is not None else None),
                    'valign': (align.get('vertical')
                               if align is not None else None),
                    'apply_font': xf.get('applyFont')
                    not in ('0', 'false'),
                })
    except Exception:
        pass
    return fonts, fills, xfs


# --------------------------------------------------------- xlsx cell refs
def _col_to_index(cellref):
    col = 0
    for ch in cellref:
        if ch.isalpha():
            col = col * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return col - 1


def _index_to_col(idx):
    idx += 1
    out = ''
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def _ref_to_rc(ref):
    m = re.match(r'([A-Z]+)(\d+)', ref.upper())
    if not m:
        return 0, 0
    return int(m.group(2)) - 1, _col_to_index(m.group(1))


# --------------------------------------------------------- xlsx readers
def xlsx_sheet_names(path):
    z = zipfile.ZipFile(path)
    try:
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        return [sh.get('name') for sh in wb.iter(XLNS + 'sheet')]
    finally:
        z.close()


def parse_range(ref):
    if not ref:
        return None
    ref = ref.replace('$', '').strip().upper()
    if ':' not in ref:
        return None
    a, b = ref.split(':', 1)
    try:
        r1, c1 = _ref_to_rc(a)
        r2, c2 = _ref_to_rc(b)
    except Exception:
        return None
    if r2 < r1:
        r1, r2 = r2, r1
    if c2 < c1:
        c1, c2 = c2, c1
    return (r1, c1, r2, c2)


def xlsx_images(path, sheet_index, tempdir):
    images = []
    z = zipfile.ZipFile(path)
    try:
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rel_map = {}
        for rel in rels.iter(RELNS + 'Relationship'):
            rel_map[rel.get('Id')] = rel.get('Target')
        sheets = list(wb.iter(XLNS + 'sheet'))
        if sheet_index >= len(sheets):
            sheet_index = 0
        rid = sheets[sheet_index].get(DOCRELNS + 'id')
        target = rel_map.get(rid, 'worksheets/sheet1.xml')
        sheet_path = (target.lstrip('/')
                      if target.startswith('/')
                      else 'xl/' + target)
        sheet_rels_path = ('xl/worksheets/_rels/'
                           + os.path.basename(sheet_path) + '.rels')
        if sheet_rels_path not in z.namelist():
            return images
        sroot = ET.fromstring(z.read(sheet_path))
        drawing_el = sroot.find(XLNS + 'drawing')
        if drawing_el is None:
            return images
        drid = drawing_el.get(DOCRELNS + 'id')
        srels = ET.fromstring(z.read(sheet_rels_path))
        drawing_target = None
        for rel in srels.iter(RELNS + 'Relationship'):
            if rel.get('Id') == drid:
                drawing_target = rel.get('Target')
                break
        if not drawing_target:
            return images
        drawing_path = drawing_target.replace('../', 'xl/')
        if not drawing_path.startswith('xl/'):
            drawing_path = 'xl/' + drawing_path.lstrip('/')
        dr_rels_path = ('xl/drawings/_rels/'
                        + os.path.basename(drawing_path) + '.rels')
        media_map = {}
        if dr_rels_path in z.namelist():
            drels = ET.fromstring(z.read(dr_rels_path))
            for rel in drels.iter(RELNS + 'Relationship'):
                media_map[rel.get('Id')] = (
                    rel.get('Target').replace('../', 'xl/'))
        XDR = ('{http://schemas.openxmlformats.org/drawingml/2006/'
               'spreadsheetDrawing}')
        droot = ET.fromstring(z.read(drawing_path))
        for anchor_tag in ('twoCellAnchor', 'oneCellAnchor',
                           'absoluteAnchor'):
            for anchor in droot.iter(XDR + anchor_tag):
                frm = anchor.find(XDR + 'from')
                if frm is None:
                    a_row, a_col = 0, 0
                else:
                    try:
                        a_row = int(frm.find(XDR + 'row').text)
                        a_col = int(frm.find(XDR + 'col').text)
                    except Exception:
                        continue
                found_any = False
                for blip in anchor.iter(ANS + 'blip'):
                    embed_id = blip.get(DOCRELNS + 'embed')
                    link_id = blip.get(DOCRELNS + 'link')
                    rel_id = embed_id or link_id
                    media = media_map.get(rel_id)
                    if not media or media not in z.namelist():
                        if link_id:
                            images.append({
                                'row': a_row, 'col': a_col,
                                'file': None,
                                'source': media or '(external link)',
                                'linked': True})
                            found_any = True
                        continue
                    out = os.path.join(
                        tempdir,
                        'tg_{}_{}'.format(
                            len(images),
                            os.path.basename(media)))
                    with open(out, 'wb') as f:
                        f.write(z.read(media))
                    images.append({
                        'row': a_row, 'col': a_col, 'file': out,
                        'source': media,
                        'linked': bool(link_id and not embed_id)})
                    found_any = True
                if not found_any:
                    continue
        return images
    except Exception:
        return images
    finally:
        z.close()


def xlsx_print_area(path, sheet_index):
    z = zipfile.ZipFile(path)
    try:
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        for dn in wb.iter(XLNS + 'definedName'):
            if dn.get('name') != '_xlnm.Print_Area':
                continue
            if dn.get('localSheetId') != str(sheet_index):
                continue
            ref = dn.text or ''
            ref = ref.split(',')[0]
            if '!' in ref:
                ref = ref.split('!')[-1]
            return parse_range(ref)
        return None
    finally:
        z.close()


def xlsx_read_sheet(path, sheet_index, max_rows=1000, max_cols=60,
                    crop=None):
    z = zipfile.ZipFile(path)
    try:
        theme = _parse_theme(z)
        fonts, fills, xfs = _parse_styles(z, theme)
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            sroot = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in sroot.iter(XLNS + 'si'):
                shared.append(
                    u''.join(t.text or u''
                             for t in si.iter(XLNS + 't')))
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rel_map = {}
        for rel in rels.iter(RELNS + 'Relationship'):
            rel_map[rel.get('Id')] = rel.get('Target')
        sheets = list(wb.iter(XLNS + 'sheet'))
        if sheet_index >= len(sheets):
            sheet_index = 0
        rid = sheets[sheet_index].get(DOCRELNS + 'id')
        target = rel_map.get(rid, 'worksheets/sheet1.xml')
        sheet_path = (target.lstrip('/')
                      if target.startswith('/')
                      else 'xl/' + target)
        sroot = ET.fromstring(z.read(sheet_path))
        def_w_mm = excel_width_to_mm(8.43)
        def_h_mm = excel_height_to_mm(15.0)
        fmt_el = sroot.find(XLNS + 'sheetFormatPr')
        if fmt_el is not None:
            dcw = fmt_el.get('defaultColWidth')
            bw = fmt_el.get('baseColWidth')
            drh = fmt_el.get('defaultRowHeight')
            if dcw:
                def_w_mm = excel_width_to_mm(float(dcw))
            elif bw:
                def_w_mm = excel_width_to_mm(float(bw) + 0.83)
            if drh:
                def_h_mm = excel_height_to_mm(float(drh))
        col_w = {}
        cols_el = sroot.find(XLNS + 'cols')
        if cols_el is not None:
            for c_el in cols_el.findall(XLNS + 'col'):
                w = c_el.get('width')
                if w is None:
                    continue
                wmm = excel_width_to_mm(float(w))
                for ci in range(int(c_el.get('min')) - 1,
                                int(c_el.get('max'))):
                    if ci < max_cols:
                        col_w[ci] = wmm
        cells = {}
        row_h = {}
        max_col = 0
        max_row = -1
        for row_el in sroot.iter(XLNS + 'row'):
            r_idx = int(row_el.get('r', '0')) - 1
            if r_idx >= max_rows:
                continue
            ht = row_el.get('ht')
            if ht:
                row_h[r_idx] = excel_height_to_mm(float(ht))
            for c_el in row_el.iter(XLNS + 'c'):
                ref = c_el.get('r', 'A1')
                c_idx = _col_to_index(ref)
                if c_idx >= max_cols:
                    continue
                ctype = c_el.get('t', 'n')
                s_idx = c_el.get('s')
                v_el = c_el.find(XLNS + 'v')
                is_el = c_el.find(XLNS + 'is')
                value = u''
                if ctype == 's' and v_el is not None:
                    try:
                        value = shared[int(v_el.text)]
                    except Exception:
                        value = u''
                elif ctype == 'inlineStr' and is_el is not None:
                    value = u''.join(
                        t.text or u''
                        for t in is_el.iter(XLNS + 't'))
                elif v_el is not None and v_el.text is not None:
                    value = v_el.text
                    if re.match(r'^-?\d+\.0+$', value):
                        value = value.split('.')[0]
                has_fill = False
                if s_idx is not None:
                    try:
                        has_fill = (fills[xfs[int(s_idx)]['fill']]
                                    is not None)
                    except Exception:
                        has_fill = False
                if value or has_fill:
                    cells[(r_idx, c_idx)] = (value, s_idx)
                    if c_idx > max_col:
                        max_col = c_idx
                    if r_idx > max_row:
                        max_row = r_idx
                elif s_idx is not None:
                    try:
                        xf = xfs[int(s_idx)]
                        font = fonts[xf['font']]
                        if font['bold'] or font['italic']:
                            cells[(r_idx, c_idx)] = (value, s_idx)
                            if c_idx > max_col:
                                max_col = c_idx
                            if r_idx > max_row:
                                max_row = r_idx
                    except Exception:
                        pass
        merges = []
        mc_el = sroot.find(XLNS + 'mergeCells')
        if mc_el is not None:
            for m in mc_el.findall(XLNS + 'mergeCell'):
                ref = m.get('ref', '')
                if ':' in ref:
                    a, b = ref.split(':')
                    r1, c1 = _ref_to_rc(a)
                    r2, c2 = _ref_to_rc(b)
                    merges.append((r1, c1, r2, c2))
        if max_row < 0:
            return None
        if crop is not None:
            cr1, cc1, cr2, cc2 = crop
            row_off, col_off = cr1, cc1
            nrows = cr2 - cr1 + 1
            ncols = cc2 - cc1 + 1
        else:
            row_off = 0
            while row_off <= max_row and not any(
                    (cells.get((row_off, c), (u'', None))[0] or u'')
                    .strip()
                    or cells.get((row_off, c), (None, None))[1]
                    is not None
                    for c in range(max_col + 1)):
                row_off += 1
            col_off = 0
            nrows = max_row - row_off + 1
            ncols = max_col + 1
        data = [[cells.get((r + row_off, c + col_off),
                           (u'', None))[0]
                 for c in range(ncols)] for r in range(nrows)]

        styles = {}
        for r in range(nrows):
            for c in range(ncols):
                _, s_idx = cells.get(
                    (r + row_off, c + col_off), (u'', None))
                if s_idx is None:
                    continue
                try:
                    xf = xfs[int(s_idx)]
                    font = fonts[xf['font']]
                    fill_color = fills[xf['fill']]
                    st = {
                        'bg': fill_color,
                        'fcolor': font['color'],
                        'bold': font['bold'],
                        'italic': font['italic'],
                        'size': font['size'],
                        'name': font['name'],
                        'halign': xf['halign'],
                        'valign': xf['valign'],
                    }
                    has_style = (
                        st['bg'] is not None
                        or st['fcolor'] is not None
                        or st['bold'] is True
                        or st['italic'] is True
                        or st['size'] is not None
                        or st['name'] is not None
                        or st['halign'] is not None
                        or st['valign'] is not None
                    )
                    if has_style:
                        styles[(r, c)] = st
                except Exception:
                    continue

        local_merges = []
        for (r1, c1, r2, c2) in merges:
            lr1, lc1 = r1 - row_off, c1 - col_off
            lr2, lc2 = r2 - row_off, c2 - col_off
            if (lr2 < 0 or lc2 < 0
                    or lr1 >= nrows or lc1 >= ncols):
                continue
            lr1, lc1 = max(0, lr1), max(0, lc1)
            lr2 = min(nrows - 1, lr2)
            lc2 = min(ncols - 1, lc2)
            if lr2 > lr1 or lc2 > lc1:
                local_merges.append((lr1, lc1, lr2, lc2))
        return {
            'data': data,
            'col_widths': [col_w.get(c + col_off, def_w_mm)
                           for c in range(ncols)],
            'row_heights': [row_h.get(r + row_off, def_h_mm)
                            for r in range(nrows)],
            'styles': styles,
            'merges': local_merges,
        }
    finally:
        z.close()


# --------------------------------------------------------------- csv
def parse_csv(text):
    rows, row, field = [], [], []
    in_quotes = False
    i = 0
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    while i < len(text):
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < len(text) and text[i + 1] == '"':
                    field.append('"')
                    i += 1
                else:
                    in_quotes = False
            else:
                field.append(ch)
        else:
            if ch == '"':
                in_quotes = True
            elif ch == ',':
                row.append(''.join(field))
                field = []
            elif ch == '\n':
                row.append(''.join(field))
                field = []
                rows.append(row)
                row = []
            else:
                field.append(ch)
        i += 1
    if field or row:
        row.append(''.join(field))
        rows.append(row)
    return [r for r in rows if any(c.strip() for c in r)]


def plain_table(rows, max_rows=1000, max_cols=60):
    if not rows:
        return None
    rows = rows[:max_rows]
    ncols = min(max(len(r) for r in rows), max_cols)
    data = [[(to_text(r[c]) if c < len(r) else u'')
             for c in range(ncols)] for r in rows]
    return {
        'data': data,
        'col_widths': [None] * ncols,
        'row_heights': [None] * len(data),
        'styles': {},
        'merges': [],
        'images': [],
    }


# ---------------------------------------------------- SPLIT into parts
def detect_category_header_rows(nrows, ncols, merges, styles):
    """Rows that are wide fill-merges introducing the group BELOW them."""
    cat = set()
    if ncols <= 0:
        return cat
    min_span = max(2, int(round(ncols * CATEGORY_MERGE_MIN_FRACTION)))
    for (r1, c1, r2, c2) in merges:
        if r1 != r2:
            continue
        span = c2 - c1 + 1
        if span < min_span:
            continue
        has_fill = False
        for c in range(c1, c2 + 1):
            st = styles.get((r1, c))
            if st and st.get('bg') is not None:
                has_fill = True
                break
        if has_fill:
            cat.add(r1)
    return cat


def compute_balanced_split(nrows, header_rows, num_parts,
                           row_heights, merges, category_rows,
                           scale=1.0):
    """Split data rows into num_parts chunks that are as EQUAL
    in total height as possible."""
    if nrows <= header_rows or num_parts <= 1:
        return [(0, nrows - 1)] if nrows > 0 else []
    if num_parts > (nrows - header_rows):
        num_parts = nrows - header_rows

    heights = [((row_heights[r] * scale)
                if r < len(row_heights) and row_heights[r]
                else 1.0)
               for r in range(nrows)]

    cum = [0.0] * nrows
    running = 0.0
    for r in range(header_rows, nrows):
        running += heights[r]
        cum[r] = running
    total_data_h = cum[nrows - 1] if nrows > header_rows else 0.0
    if total_data_h <= 0:
        return compute_equal_rowcount_split(
            nrows, header_rows, num_parts, merges, category_rows)

    merge_end_for_row = {}
    for (m_r1, m_c1, m_r2, m_c2) in merges:
        if m_r2 == m_r1:
            continue
        for r in range(m_r1, m_r2 + 1):
            existing = merge_end_for_row.get(r, r)
            if m_r2 > existing:
                merge_end_for_row[r] = m_r2

    def adjust_end(end_row, min_end, max_end):
        moved = True
        while moved:
            moved = False
            m_end = merge_end_for_row.get(end_row, end_row)
            if m_end > end_row:
                end_row = m_end
                moved = True
        while end_row in category_rows and end_row > min_end:
            end_row -= 1
        if end_row < min_end:
            end_row = min_end
        if end_row > max_end:
            end_row = max_end
        return end_row

    ranges = []
    cursor = header_rows
    given_so_far = 0.0

    for part_i in range(num_parts - 1):
        remaining_parts = num_parts - part_i
        remaining_height = total_data_h - given_so_far
        target_this_part = remaining_height / float(remaining_parts)
        ideal_cum = given_so_far + target_this_part

        best_row = cursor
        best_diff = abs(cum[cursor] - ideal_cum)

        remaining_after = num_parts - part_i - 1
        max_allowed_end = (nrows - 1) - remaining_after

        r = cursor + 1
        while r <= max_allowed_end:
            d = abs(cum[r] - ideal_cum)
            if d < best_diff:
                best_diff = d
                best_row = r
            if cum[r] > ideal_cum and d > best_diff * 2:
                break
            r += 1

        if best_row < cursor:
            best_row = cursor
        if best_row > max_allowed_end:
            best_row = max_allowed_end

        end_row = adjust_end(best_row, cursor, max_allowed_end)

        if end_row >= nrows - 1:
            end_row = nrows - 2
        if end_row < cursor:
            end_row = cursor

        ranges.append((cursor, end_row))
        given_so_far = cum[end_row]
        cursor = end_row + 1

    if cursor <= nrows - 1:
        ranges.append((cursor, nrows - 1))

    return ranges


def compute_equal_rowcount_split(nrows, header_rows, num_parts,
                                 merges, category_rows):
    data_start = header_rows
    data_count = nrows - header_rows
    if data_count <= 0 or num_parts <= 1:
        return [(0, nrows - 1)]
    if num_parts > data_count:
        num_parts = data_count

    per_part = data_count / float(num_parts)
    ideal_ends = []
    for i in range(num_parts - 1):
        end = data_start + int(round(per_part * (i + 1))) - 1
        ideal_ends.append(end)

    merge_end_for_row = {}
    for (m_r1, m_c1, m_r2, m_c2) in merges:
        if m_r2 == m_r1:
            continue
        for r in range(m_r1, m_r2 + 1):
            existing = merge_end_for_row.get(r, r)
            if m_r2 > existing:
                merge_end_for_row[r] = m_r2

    adjusted_ends = []
    for end in ideal_ends:
        moved = True
        while moved:
            moved = False
            m_end = merge_end_for_row.get(end, end)
            if m_end > end:
                end = m_end
                moved = True
        while end in category_rows and end > data_start:
            end -= 1
        adjusted_ends.append(end)

    ranges = []
    cursor = data_start
    for end in adjusted_ends:
        if end < cursor:
            end = cursor
        if end >= nrows - 1:
            break
        ranges.append((cursor, end))
        cursor = end + 1
    if cursor <= nrows - 1:
        ranges.append((cursor, nrows - 1))
    return ranges


def measure_part_height(full_table, part_start, part_end,
                        header_rows, scale=1.0):
    """Return the physical mm height of a part (header + slice)."""
    rh = full_table['row_heights']
    total = 0.0
    for r in range(header_rows):
        if r < len(rh) and rh[r]:
            total += rh[r] * scale
    for r in range(part_start, part_end + 1):
        if r < header_rows:
            continue
        if r < len(rh) and rh[r]:
            total += rh[r] * scale
    return total


def compute_capped_split(nrows, header_rows, num_parts,
                         row_heights, merges, category_rows,
                         max_h_mm, scale=1.0):
    """Fill parts up to max_h_mm each (greedy)."""
    if nrows <= header_rows or num_parts <= 1:
        return [(header_rows, nrows - 1)] if nrows > header_rows \
            else ([(0, nrows - 1)] if nrows > 0 else [])

    heights = [((row_heights[r] * scale)
                if r < len(row_heights) and row_heights[r]
                else 0.0)
               for r in range(nrows)]

    total_data_h = sum(heights[header_rows:nrows])
    if total_data_h <= 0:
        return compute_equal_rowcount_split(
            nrows, header_rows, num_parts, merges, category_rows)

    header_h = sum(heights[:header_rows])
    budget = max_h_mm - header_h
    if budget <= 0:
        budget = max_h_mm

    merge_end_for_row = {}
    for (m_r1, m_c1, m_r2, m_c2) in merges:
        if m_r2 == m_r1:
            continue
        for r in range(m_r1, m_r2 + 1):
            existing = merge_end_for_row.get(r, r)
            if m_r2 > existing:
                merge_end_for_row[r] = m_r2

    def adjust_end(end_row, min_end):
        moved = True
        while moved:
            moved = False
            m_end = merge_end_for_row.get(end_row, end_row)
            if m_end > end_row:
                end_row = m_end
                moved = True
        while end_row in category_rows and end_row > min_end:
            end_row -= 1
        if end_row < min_end:
            end_row = min_end
        return end_row

    ranges = []
    cursor = header_rows
    parts_made = 0

    while parts_made < num_parts - 1 and cursor <= nrows - 1:
        cum = 0.0
        last_valid = None
        r = cursor
        while r <= nrows - 1:
            row_h = heights[r]
            if last_valid is not None and cum + row_h > budget:
                break
            cum += row_h
            last_valid = r
            r += 1
        if last_valid is None:
            last_valid = cursor

        end_row = adjust_end(last_valid, cursor)

        if end_row >= nrows - 1:
            break

        ranges.append((cursor, end_row))
        cursor = end_row + 1
        parts_made += 1

    if cursor <= nrows - 1:
        ranges.append((cursor, nrows - 1))

    return ranges


def compute_fit_scale_factor(full_table, header_rows, num_parts,
                             max_h_mm, scale=1.0, ranges=None):
    """Return factor so tallest part becomes exactly max_h_mm."""
    rh = full_table['row_heights']

    header_h = 0.0
    for r in range(header_rows):
        if r < len(rh) and rh[r]:
            header_h += rh[r] * scale

    if ranges:
        tallest = 0.0
        for (s, e) in ranges:
            h = header_h
            for r in range(s, e + 1):
                if r < len(rh) and rh[r]:
                    h += rh[r] * scale
            if h > tallest:
                tallest = h
        if tallest <= 0:
            return 1.0
        return max_h_mm / tallest

    total_data_h = 0.0
    for r in range(header_rows, len(rh)):
        if rh[r]:
            total_data_h += rh[r] * scale
    if num_parts <= 0:
        num_parts = 1
    avg_part_h = header_h + (total_data_h / float(num_parts))
    if avg_part_h <= 0:
        return 1.0
    return max_h_mm / avg_part_h


def pad_parts_to_equal_height(full_table, ranges, header_rows,
                              target_h_mm, scale=1.0):
    """Adjust row heights per-part so every part is EXACTLY target_h_mm.

    Returns dict keyed by (part_index, source_row_idx) -> new_height_mm."""
    rh = full_table['row_heights']

    header_h = 0.0
    for r in range(header_rows):
        if r < len(rh) and rh[r]:
            header_h += rh[r] * scale

    overrides = {}

    for part_idx, (s, e) in enumerate(ranges):
        data_h = 0.0
        data_rows = []
        for r in range(s, e + 1):
            if r < header_rows:
                continue
            if r < len(rh) and rh[r]:
                data_h += rh[r] * scale
                data_rows.append(r)

        current_h = header_h + data_h
        deficit = target_h_mm - current_h

        if deficit <= 0.01 or not data_rows:
            continue

        per_row_add = deficit / float(len(data_rows))
        for r in data_rows:
            new_h = (rh[r] * scale) + per_row_add
            overrides[(part_idx, r)] = new_h

    return overrides


def compute_fit_shrink_factor(full_table, ranges, header_rows,
                              max_h_mm, scale=1.0):
    """Return shrink factor <= 1.0 so no part exceeds max_h_mm."""
    worst = 0.0
    for (s, e) in ranges:
        h = measure_part_height(full_table, s, e,
                                header_rows, scale)
        if h > worst:
            worst = h
    if worst <= 0 or worst <= max_h_mm:
        return 1.0
    return max_h_mm / worst


def build_part_table(full_table, part_start, part_end, header_rows,
                     row_h_override=None):
    """Build a part-table slice.

    row_h_override: optional dict {source_row_idx: new_height_mm}."""
    src_data = full_table['data']
    src_styles = full_table['styles']
    src_merges = full_table['merges']
    src_row_h = full_table['row_heights']
    src_col_w = full_table['col_widths']
    src_imgs = full_table.get('images', [])

    ncols = len(src_data[0]) if src_data else 0

    row_map = {}
    new_data = []
    new_row_h = []

    for r in range(header_rows):
        row_map[r] = len(new_data)
        new_data.append(list(src_data[r]))
        new_row_h.append(src_row_h[r] if r < len(src_row_h) else None)

    for r in range(part_start, part_end + 1):
        if r < header_rows:
            continue
        row_map[r] = len(new_data)
        new_data.append(list(src_data[r]))
        if row_h_override and r in row_h_override:
            new_row_h.append(row_h_override[r])
        else:
            new_row_h.append(
                src_row_h[r] if r < len(src_row_h) else None)

    new_styles = {}
    for (r, c), st in src_styles.items():
        if r in row_map:
            new_styles[(row_map[r], c)] = st

    new_merges = []
    for (r1, c1, r2, c2) in src_merges:
        if r2 < header_rows:
            new_merges.append(
                (row_map[r1], c1, row_map[r2], c2))
            continue
        rows_in = [r for r in range(r1, r2 + 1) if r in row_map]
        if len(rows_in) < 2 and c1 == c2:
            if len(rows_in) == 0:
                continue
            if r1 == r2 and c2 > c1:
                new_merges.append(
                    (row_map[r1], c1, row_map[r2], c2))
            continue
        if not rows_in:
            continue
        new_r1 = row_map[min(rows_in)]
        new_r2 = row_map[max(rows_in)]
        if new_r2 > new_r1 or c2 > c1:
            new_merges.append((new_r1, c1, new_r2, c2))

    new_imgs = []
    for im in src_imgs:
        r = im.get('row', 0)
        if r in row_map:
            im2 = dict(im)
            im2['row'] = row_map[r]
            new_imgs.append(im2)

    return {
        'data': new_data,
        'col_widths': list(src_col_w),
        'row_heights': new_row_h,
        'styles': new_styles,
        'merges': new_merges,
        'images': new_imgs,
    }


# -------------------------------------------------------- import history
def _history_path():
    try:
        return script.get_document_data_file(
            'tablegen_history', 'json')
    except Exception:
        safe = re.sub(r'[^A-Za-z0-9_-]', '_',
                      doc.Title or 'project')
        return os.path.join(
            tempfile.gettempdir(),
            'tablegen_history_{}.json'.format(safe))


def load_history():
    try:
        with codecs.open(_history_path(), 'r',
                         encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_history_entry(name, source_file, nrows, ncols, nimgs,
                       settings=None):
    entries = load_history()
    entries = [e for e in entries if e.get('name') != name]
    entries.insert(0, {
        'name': name,
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'source': source_file or '(clipboard paste)',
        'size': '{}x{}'.format(nrows, ncols),
        'images': nimgs,
        'settings': settings or {},
    })
    entries = entries[:200]
    try:
        with codecs.open(_history_path(), 'w',
                         encoding='utf-8') as f:
            json.dump(entries, f)
    except Exception:
        pass


def save_history(entries):
    try:
        with codecs.open(_history_path(), 'w',
                         encoding='utf-8') as f:
            json.dump(entries, f)
    except Exception:
        pass


def project_schedule_names():
    names = set()
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            names.add(vs.Name)
        except Exception:
            continue
    return names


def prune_history():
    entries = load_history()
    existing = project_schedule_names()
    kept = [e for e in entries if e.get('name') in existing]
    if len(kept) != len(entries):
        save_history(kept)
    return kept


# ------------------------------------------------------------ main window
class TableGenWindow(forms.WPFWindow):

    def __init__(self, xaml_file):
        forms.WPFWindow.__init__(self, xaml_file)
        self.result = None
        self.table = None
        self.file_path = None
        self._suppress = False
        try:
            self.Title = "TableGen"
        except Exception:
            pass
        self.render_history()
        self.browse_btn.Click += self.browse
        self.paste_btn.Click += self.paste_clipboard
        self.sheet_cb.SelectionChanged += self.sheet_changed
        self.area_cb.SelectionChanged += self.area_changed
        self.range_tb.LostFocus += self.range_edited
        self.range_tb.KeyDown += self.range_keydown
        self.cancel_btn.Click += self._cancel
        self.create_btn.Click += self._create
        self.reload_btn.Click += self._reload_selected
        try:
            self.reloadfrom_btn.Click += self._reload_from
            self.remove_btn.Click += self._remove_selected
            self.duplicate_btn.Click += self._duplicate_selected
            self.resize_btn.Click += self._resize_selected
        except AttributeError:
            forms.alert(
                "Your ui.xaml is OUTDATED - buttons are missing.\n\n"
                "Copy the latest ui.xaml next to script.py and "
                "reload pyRevit.")
        try:
            self.split_btn.Click += self._split_selected
        except AttributeError:
            pass

    def _get_crop(self):
        if (not self.file_path
                or not self.file_path.lower().endswith('.xlsx')):
            return None
        idx = self.area_cb.SelectedIndex
        if idx == 1:
            pa = xlsx_print_area(
                self.file_path,
                max(0, self.sheet_cb.SelectedIndex))
            if pa is None:
                self.status_text.Text = (
                    "No print area defined - "
                    "showing entire sheet.")
            return pa
        elif idx == 2:
            rng = parse_range(self.range_tb.Text)
            if rng is None and self.range_tb.Text.strip():
                self.status_text.Text = (
                    "Invalid range '{}' - use e.g. A1:F40"
                    .format(self.range_tb.Text))
            return rng
        return None

    def _reload_sheet(self):
        if not self.file_path:
            return
        if self.file_path.lower().endswith('.xlsx'):
            try:
                crop = self._get_crop()
                table = xlsx_read_sheet(
                    self.file_path,
                    max(0, self.sheet_cb.SelectedIndex),
                    crop=crop)
                if table is not None:
                    imgs = xlsx_images(
                        self.file_path,
                        max(0, self.sheet_cb.SelectedIndex),
                        tempfile.gettempdir())
                    r_off = crop[0] if crop else 0
                    c_off = crop[1] if crop else 0
                    nrows = len(table['data'])
                    ncols = (len(table['data'][0])
                             if nrows else 0)
                    local = []
                    for im in imgs:
                        lr = im['row'] - r_off
                        lc = im['col'] - c_off
                        if 0 <= lr < nrows and 0 <= lc < ncols:
                            im2 = dict(im)
                            im2['row'] = lr
                            im2['col'] = lc
                            local.append(im2)
                    table['images'] = local
                self.set_table(table)
            except Exception as err:
                forms.alert(
                    "Could not read worksheet:\n{}".format(err))

    def area_changed(self, sender, args):
        try:
            self.range_tb.IsEnabled = (
                self.area_cb.SelectedIndex == 2)
        except Exception:
            pass
        if (self.area_cb.SelectedIndex == 1
                and self.file_path
                and self.file_path.lower().endswith('.xlsx')):
            pa = xlsx_print_area(
                self.file_path,
                max(0, self.sheet_cb.SelectedIndex))
            if pa:
                r1, c1, r2, c2 = pa
                self.range_tb.Text = "{}{}:{}{}".format(
                    _index_to_col(c1), r1 + 1,
                    _index_to_col(c2), r2 + 1)
        self._reload_sheet()

    def range_edited(self, sender, args):
        if self.area_cb.SelectedIndex == 2:
            self._reload_sheet()

    def range_keydown(self, sender, args):
        try:
            from System.Windows.Input import Key
            if args.Key in (Key.Enter, Key.Return):
                self._reload_sheet()
        except Exception:
            pass

    def browse(self, sender, args):
        path = forms.pick_file(
            files_filter='Tables (*.xlsx;*.csv)|*.xlsx;*.csv|'
                         'Excel (*.xlsx)|*.xlsx|CSV (*.csv)|*.csv')
        if not path:
            return
        self.file_path = path
        self.path_tb.Text = path
        self._suppress = True
        self.sheet_cb.Items.Clear()
        self._suppress = False
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == '.xlsx':
                names = xlsx_sheet_names(path)
                self._suppress = True
                for n in names:
                    self.sheet_cb.Items.Add(n)
                self._suppress = False
                if names:
                    self.sheet_cb.SelectedIndex = 0
            else:
                with codecs.open(path, 'r',
                                 encoding='utf-8-sig') as f:
                    self.set_table(
                        plain_table(parse_csv(f.read())))
        except Exception as err:
            forms.alert(
                "Could not read file:\n{}".format(err))
            return
        base = os.path.splitext(os.path.basename(path))[0]
        if self.name_tb.Text in ('New Table', ''):
            self.name_tb.Text = base

    def sheet_changed(self, sender, args):
        if (self._suppress or not self.file_path
                or self.sheet_cb.SelectedIndex < 0):
            return
        self._reload_sheet()

    def paste_clipboard(self, sender, args):
        try:
            text = Clipboard.GetText()
        except Exception:
            text = ''
        if not text.strip():
            self.status_text.Text = (
                "Clipboard empty - copy cells in Excel first.")
            return
        rows = [line.split('\t') for line in
                text.replace('\r\n', '\n').replace('\r', '\n')
                .split('\n') if line.strip('\t').strip()]
        self.set_table(plain_table(rows))
        self.path_tb.Text = (
            "(pasted from clipboard - no formatting)")

    def set_table(self, table):
        self.table = table
        ok = table is not None and bool(table['data'])
        self.create_btn.IsEnabled = ok
        if ok:
            nfmt = len(table['styles'])
            nimg = len(table.get('images', []))
            self.status_text.Text = (
                "{} rows x {} cols | {} formatted cells | "
                "{} merges | {} images"
                .format(len(table['data']),
                        len(table['data'][0]),
                        nfmt, len(table['merges']), nimg))
        else:
            self.status_text.Text = "No data found."

    def render_history(self):
        self.preview_host.Children.Clear()
        self.history_rows = []
        entries = prune_history()
        if not entries:
            empty = TextBlock()
            empty.Text = (
                "No TableGen tables currently in this project. "
                "Browse an Excel file above to add one.")
            empty.Foreground = brush('#9A9AB0')
            empty.FontSize = 13
            empty.Margin = Thickness(8, 8, 0, 0)
            self.preview_host.Children.Add(empty)
            return
        g = Grid()
        cd = ColumnDefinition()
        cd.Width = GridLength(30)
        g.ColumnDefinitions.Add(cd)
        for w in (220, 130, 90, 70):
            cd = ColumnDefinition()
            cd.Width = GridLength(w)
            g.ColumnDefinitions.Add(cd)
        cd = ColumnDefinition()
        cd.Width = GridLength(1, GridUnitType.Star)
        g.ColumnDefinitions.Add(cd)
        headers = ('', 'SCHEDULE NAME', 'IMPORTED',
                   'SIZE', 'IMAGES', 'SOURCE FILE LOCATION')
        g.RowDefinitions.Add(RowDefinition())
        for c, htxt in enumerate(headers):
            hb = TextBlock()
            hb.Text = htxt
            hb.Foreground = brush('#8FD3FF')
            hb.FontSize = 11
            hb.FontWeight = FontWeights.Bold
            hb.Margin = Thickness(6, 2, 6, 6)
            Grid.SetRow(hb, 0)
            Grid.SetColumn(hb, c)
            g.Children.Add(hb)
        for r, e in enumerate(entries):
            g.RowDefinitions.Add(RowDefinition())
            chk = CheckBox()
            chk.Margin = Thickness(8, 3, 0, 3)
            Grid.SetRow(chk, r + 1)
            Grid.SetColumn(chk, 0)
            g.Children.Add(chk)
            self.history_rows.append((chk, e))
            vals = (e.get('name', ''), e.get('date', ''),
                    e.get('size', ''),
                    str(e.get('images', '')),
                    e.get('source', ''))
            for c, v in enumerate(vals):
                tb = TextBlock()
                tb.Text = v
                tb.Foreground = (brush('#EFEFEF') if c == 0
                                 else brush('#C8C8DA'))
                tb.FontSize = 12
                tb.Margin = Thickness(6, 3, 6, 3)
                tb.TextTrimming = TextTrimming.CharacterEllipsis
                if c == 4:
                    tb.ToolTip = v
                Grid.SetRow(tb, r + 1)
                Grid.SetColumn(tb, c + 1)
                g.Children.Add(tb)
        self.preview_host.Children.Add(g)

    def _reload_selected(self, sender, args):
        picked = [e for chk, e in
                  getattr(self, 'history_rows', [])
                  if chk.IsChecked == True]
        if not picked:
            self.status_text.Text = (
                "Tick one or more tables in the list first.")
            return
        jobs, problems = [], []
        for e in picked:
            srcf = e.get('source', '')
            if not srcf or srcf == '(clipboard paste)':
                problems.append(
                    "{}: no source file (clipboard import)"
                    .format(e.get('name')))
                continue
            if not os.path.exists(srcf):
                problems.append(
                    "{}: file not found at {}"
                    .format(e.get('name'), srcf))
                continue
            jobs.append(e)
        if problems and not jobs:
            forms.alert(
                "Cannot reload:\n" + "\n".join(problems))
            return
        self.result = {'reload_jobs': jobs,
                       'reload_problems': problems}
        self.Close()

    def _reload_from(self, sender, args):
        try:
            picked = [e for chk, e in
                      getattr(self, 'history_rows', [])
                      if chk.IsChecked == True]
            if len(picked) != 1:
                self.status_text.Text = (
                    "Tick exactly ONE table, then click "
                    "'Reload from...'.")
                return
            entry = picked[0]
            new_path = forms.pick_file(
                files_filter='Tables (*.xlsx;*.csv)|*.xlsx;*.csv|'
                             'Excel (*.xlsx)|*.xlsx|'
                             'CSV (*.csv)|*.csv')
            if not new_path:
                return
            if isinstance(new_path, (list, tuple)):
                new_path = new_path[0]
            new_path = str(new_path)
            entries = load_history()
            for e in entries:
                if e.get('name') == entry.get('name'):
                    e['source'] = new_path
            save_history(entries)
            job = dict(entry)
            job['source'] = new_path
            self.result = {'reload_jobs': [job],
                           'reload_problems': []}
            self.Close()
        except Exception as err:
            forms.alert(
                "Reload from... failed:\n{}".format(err))

    def _remove_selected(self, sender, args):
        try:
            picked = [e for chk, e in
                      getattr(self, 'history_rows', [])
                      if chk.IsChecked == True]
            if not picked:
                self.status_text.Text = (
                    "Tick one or more tables in the list first.")
                return
            names = sorted(
                set(e.get('name') for e in picked))
            if not forms.alert(
                    "Delete {} schedule(s) from the PROJECT "
                    "and remove from this list?\n\n{}\n\n"
                    "This deletes the actual schedule view(s) "
                    "in Revit."
                    .format(len(names), '\n'.join(names)),
                    title="TableGen - Remove",
                    yes=True, no=True):
                return
            self.result = {'remove_names': names}
            self.Close()
        except Exception as err:
            forms.alert("Remove failed:\n{}".format(err))

    def _duplicate_selected(self, sender, args):
        try:
            picked = [e for chk, e in
                      getattr(self, 'history_rows', [])
                      if chk.IsChecked == True]
            if not picked:
                self.status_text.Text = (
                    "Tick one or more tables in the list first.")
                return
            existing = project_schedule_names()

            def next_name(base):
                m = re.match(
                    r'^(.*?)\s*\((\d+)\)\s*$', base)
                root = m.group(1) if m else base
                n = 2
                while True:
                    cand = "{} ({})".format(root, n)
                    if cand not in existing:
                        existing.add(cand)
                        return cand
                    n += 1

            jobs = []
            for e in picked:
                jobs.append({
                    'original': e,
                    'new_name': next_name(e.get('name', '')),
                })
            if not jobs:
                return
            self.result = {'duplicate_jobs': jobs}
            self.Close()
        except Exception as err:
            forms.alert("Duplicate failed:\n{}".format(err))

    def _resize_selected(self, sender, args):
        try:
            picked = [e for chk, e in
                      getattr(self, 'history_rows', [])
                      if chk.IsChecked == True]
            if not picked:
                self.status_text.Text = (
                    "Tick one or more tables in the list first.")
                return
            jobs, problems = [], []
            for e in picked:
                srcf = e.get('source', '')
                if not srcf or srcf == '(clipboard paste)':
                    problems.append(
                        "{}: cannot resize (clipboard import - "
                        "no source file)".format(e.get('name')))
                    continue
                if not os.path.exists(srcf):
                    problems.append(
                        "{}: source file not found: {}"
                        .format(e.get('name'), srcf))
                    continue
                jobs.append(e)
            if not jobs:
                forms.alert(
                    "Cannot resize:\n" + "\n".join(problems))
                return
            first = jobs[0]
            cur_rowh = (first.get('settings') or {}).get(
                'def_rowh', 20.0)
            cur_img = (first.get('settings') or {}).get(
                'img_size', 20.0)
            new_rowh_str = forms.ask_for_string(
                prompt=(
                    "Enter new UNIFORM row height in mm.\n\n"
                    "(current: {} mm row / {} mm image)\n\n"
                    "Image size will auto-match the row height."
                    .format(cur_rowh, cur_img)),
                title="TableGen - Resize rows & images",
                default=str(int(cur_rowh)))
            if not new_rowh_str or not new_rowh_str.strip():
                return
            try:
                new_rowh = float(new_rowh_str.strip())
                if new_rowh <= 0:
                    raise ValueError()
            except Exception:
                forms.alert(
                    "Row height must be a positive number (mm).")
                return
            self.result = {
                'resize_jobs': jobs,
                'resize_problems': problems,
                'new_rowh': new_rowh,
            }
            self.Close()
        except Exception as err:
            forms.alert("Resize failed:\n{}".format(err))

    def _split_selected(self, sender, args):
        """Split into N EXACT-equal parts by padding row heights.

        User specifies how many header rows to repeat on every part.
        """
        try:
            picked = [e for chk, e in
                      getattr(self, 'history_rows', [])
                      if chk.IsChecked == True]
            if len(picked) != 1:
                self.status_text.Text = (
                    "Tick exactly ONE table, then click "
                    "'Split into parts...'.")
                return
            entry = picked[0]
            srcf = entry.get('source', '')
            if (not srcf or srcf == '(clipboard paste)'
                    or not os.path.exists(srcf)):
                forms.alert(
                    "Cannot split '{}': source file not "
                    "available.\n\nSource: {}"
                    .format(entry.get('name'), srcf))
                return

            # Retrieve previously-saved header-row count for this entry
            prev_settings = entry.get('settings') or {}
            prev_header = prev_settings.get(
                'split_header', SPLIT_HEADER_ROWS)

            resp = forms.ask_for_string(
                prompt=(
                    "Split '{}' into N EXACT-equal parts, "
                    "each MAX_HEIGHT_MM tall.\n\n"
                    "Enter THREE values separated by commas:\n"
                    "   MAX_HEIGHT_MM , NUM_PARTS , HEADER_ROWS\n\n"
                    "  MAX_HEIGHT_MM  = target height of every "
                    "part (mm)\n"
                    "  NUM_PARTS      = how many schedule sheets "
                    "to make\n"
                    "  HEADER_ROWS    = how many TOP rows to "
                    "repeat as\n"
                    "                   header on EVERY split "
                    "part\n"
                    "                   (current saved value: "
                    "{})\n\n"
                    "Examples:\n"
                    "   550, 2, 3   -> 2 parts, 550 mm each,\n"
                    "                  top 3 rows repeat as "
                    "header\n"
                    "   600, 3, 1   -> 3 parts, 600 mm each,\n"
                    "                  top 1 row repeats as "
                    "header\n"
                    "   400, 4, 4   -> 4 parts, 400 mm each,\n"
                    "                  top 4 rows repeat as "
                    "header\n"
                    "   500, 2, 0   -> 2 parts, no header "
                    "repeated\n\n"
                    "How it works:\n"
                    "  1. Content is split into NUM_PARTS "
                    "balanced chunks.\n"
                    "  2. ALL row heights (and image size) are "
                    "scaled by\n"
                    "     ONE factor so the TALLEST part = "
                    "MAX_HEIGHT_MM.\n"
                    "  3. Shorter parts are PADDED so every part "
                    "becomes\n"
                    "     EXACTLY MAX_HEIGHT_MM tall.\n"
                    "  4. Images stay UNIFORM across every "
                    "part.\n\n"
                    "Rules:\n"
                    "  * The first HEADER_ROWS rows (whatever you "
                    "type) repeat as header on every part.\n"
                    "  * Blue category-headers start the next "
                    "part.\n"
                    "  * Merged cells are never split."
                    .format(entry.get('name'), prev_header)),
                title="TableGen - Split & fit to titleblock",
                default="550, 3, {}".format(prev_header))
            if not resp or not resp.strip():
                return

            # Parse: accept 2 or 3 comma-separated values
            try:
                parts_txt = [p.strip() for p in resp.split(',')]
                if len(parts_txt) < 2:
                    forms.alert(
                        "Please enter at least TWO values "
                        "separated by commas:\n"
                        "MAX_HEIGHT_MM, NUM_PARTS "
                        "[, HEADER_ROWS]")
                    return
                max_h = float(parts_txt[0])
                num_parts = int(float(parts_txt[1]))
                if len(parts_txt) >= 3 and parts_txt[2] != '':
                    header_rows = int(float(parts_txt[2]))
                else:
                    header_rows = prev_header

                if max_h <= 0 or num_parts < 2:
                    forms.alert(
                        "MAX_HEIGHT_MM must be > 0 and "
                        "NUM_PARTS must be >= 2.")
                    return
                if header_rows < 0:
                    forms.alert(
                        "HEADER_ROWS must be >= 0 "
                        "(0 means no repeated header).")
                    return
            except Exception:
                forms.alert(
                    "Invalid input.\n\nUse format:\n"
                    "HEIGHT_MM, NUM_PARTS, HEADER_ROWS\n"
                    "(e.g. 550, 3, 4)")
                return

            self.result = {
                'split_entry': entry,
                'split_max_h_mm': max_h,
                'split_num_parts': num_parts,
                'split_header_rows': header_rows,
            }
            self.Close()
        except Exception as err:
            forms.alert(
                "Split into parts failed:\n{}".format(err))

    def _cancel(self, sender, args):
        self.result = None
        self.Close()

    def _create(self, sender, args):
        if not self.table:
            return
        try:
            colw = float(self.colw_tb.Text)
            rowh = float(self.rowh_tb.Text)
        except Exception:
            forms.alert(
                "Column width / row height must be numbers (mm).")
            return
        try:
            scale = float(self.scale_tb.Text) / 100.0
            if scale <= 0:
                scale = 1.0
        except Exception:
            scale = 1.0
        try:
            img_size = float(self.imgsize_tb.Text)
            if img_size <= 0:
                img_size = 20.0
        except Exception:
            img_size = 20.0
        self.result = {
            'table': self.table,
            'name': self.name_tb.Text.strip() or "New Table",
            'def_colw': colw,
            'def_rowh': rowh,
            'scale': scale,
            'img_size': img_size,
            'source_file': self.file_path,
            'settings': {
                'sheet_index': max(
                    0, self.sheet_cb.SelectedIndex),
                'area_index': max(
                    0, self.area_cb.SelectedIndex),
                'range': self.range_tb.Text,
                'def_colw': colw,
                'def_rowh': rowh,
                'scale': scale,
                'img_size': img_size,
            },
        }
        self.Close()


# ------------------------------------------------ create native schedule
HALIGN_MAP = {
    'left': HorizontalAlignmentStyle.Left,
    'center': HorizontalAlignmentStyle.Center,
    'centerContinuous': HorizontalAlignmentStyle.Center,
    'right': HorizontalAlignmentStyle.Right,
}
VALIGN_MAP = {
    'top': VerticalAlignmentStyle.Top,
    'center': VerticalAlignmentStyle.Middle,
    'bottom': VerticalAlignmentStyle.Bottom,
}


def make_cell_style(st):
    style = TableCellStyle()
    opts = TableCellStyleOverrideOptions()

    if st.get('bg') is not None:
        opts.BackgroundColor = True
    if st.get('fcolor') is not None:
        opts.FontColor = True
    if 'bold' in st:
        opts.Bold = True
    if 'italic' in st:
        opts.Italics = True
    if st.get('size') is not None:
        opts.FontSize = True
    if st.get('name') is not None:
        opts.Font = True
    if st.get('halign') is not None:
        opts.HorizontalAlignment = True
    if st.get('valign') is not None:
        opts.VerticalAlignment = True

    style.SetCellStyleOverrideOptions(opts)

    if st.get('bg') is not None:
        r, g, b = st['bg']
        style.BackgroundColor = RevitColor(r, g, b)
    if st.get('fcolor') is not None:
        r, g, b = st['fcolor']
        style.TextColor = RevitColor(r, g, b)
    if 'bold' in st:
        style.IsFontBold = bool(st['bold'])
    if 'italic' in st:
        style.IsFontItalic = bool(st['italic'])
    if st.get('size') is not None:
        try:
            style.TextSize = float(st['size'])
        except Exception:
            pass
    if st.get('name') is not None:
        try:
            style.FontName = st['name']
        except Exception:
            pass
    if st.get('halign') and st['halign'] in HALIGN_MAP:
        style.FontHorizontalAlignment = HALIGN_MAP[st['halign']]
    if st.get('valign') and st['valign'] in VALIGN_MAP:
        style.FontVerticalAlignment = VALIGN_MAP[st['valign']]

    return style


def apply_sizes_uniform(header, first_r, first_c,
                        col_mms, row_mms):
    for c, wmm in enumerate(col_mms):
        try:
            header.SetColumnWidth(
                first_c + c, max(wmm, 1.0) * MM_TO_FT)
        except Exception:
            pass
    for r, hmm in enumerate(row_mms):
        try:
            header.SetRowHeight(
                first_r + r, max(hmm, 1.0) * MM_TO_FT)
        except Exception:
            pass
    return 1.0


def normalize_image(filepath, cell_w_mm, cell_h_mm,
                    img_size_mm, out_tag=''):
    try:
        try:
            import clr
            clr.AddReference('System.Drawing')
        except Exception:
            return filepath
        from System.Drawing import (Bitmap, Graphics, Image,
                                    Color as DColor, Rectangle)
        from System.Drawing.Imaging import ImageFormat
        from System.Drawing.Drawing2D import InterpolationMode

        if cell_w_mm <= 0 or cell_h_mm <= 0:
            return filepath

        cw = max(8, int(round(cell_w_mm * PX_PER_MM)))
        ch = max(8, int(round(cell_h_mm * PX_PER_MM)))

        if cw > CANVAS_CAP_PX or ch > CANVAS_CAP_PX:
            k = min(float(CANVAS_CAP_PX) / cw,
                    float(CANVAS_CAP_PX) / ch)
            cw = max(8, int(cw * k))
            ch = max(8, int(ch * k))
            actual_px_mm = cw / float(cell_w_mm)
        else:
            actual_px_mm = PX_PER_MM

        img_w_px = img_size_mm * actual_px_mm
        img_h_px = img_size_mm * IMG_ASPECT * actual_px_mm

        max_w = cw / IMG_BUFFER
        max_h = ch / IMG_BUFFER
        if img_w_px > max_w:
            k = max_w / img_w_px
            img_w_px *= k
            img_h_px *= k
        if img_h_px > max_h:
            k = max_h / img_h_px
            img_w_px *= k
            img_h_px *= k

        nw = max(1, int(round(img_w_px)))
        nh = max(1, int(round(img_h_px)))
        ox = (cw - nw) // 2
        oy = (ch - nh) // 2

        src_img = Image.FromFile(filepath)
        try:
            iw, ih = src_img.Width, src_img.Height
            if iw <= 0 or ih <= 0:
                return filepath

            bmp = Bitmap(cw, ch)
            g = Graphics.FromImage(bmp)
            try:
                g.Clear(DColor.White)
                g.InterpolationMode = (
                    InterpolationMode.HighQualityBicubic)
                g.DrawImage(
                    src_img, Rectangle(ox, oy, nw, nh))
            finally:
                g.Dispose()
            out_path = (os.path.splitext(filepath)[0]
                        + '_n{}.png'.format(out_tag))
            bmp.Save(out_path, ImageFormat.Png)
            bmp.Dispose()
            return out_path
        finally:
            src_img.Dispose()
    except Exception:
        return filepath


def create_schedule(name, table, def_colw, def_rowh,
                    scale=1.0, img_size_mm=20.0):
    data = table['data']
    nrows = len(data)
    ncols = len(data[0])

    sched = ViewSchedule.CreateKeySchedule(
        doc, ElementId(BuiltInCategory.OST_GenericModel))
    try:
        sched.Name = name
    except Exception:
        pass
    try:
        sched.Definition.ShowHeaders = False
    except Exception:
        pass

    tbl = sched.GetTableData()
    header = tbl.GetSectionData(SectionType.Header)
    first_r = header.FirstRowNumber
    first_c = header.FirstColumnNumber

    while header.NumberOfRows < nrows:
        header.InsertRow(header.LastRowNumber + 1)
    while header.NumberOfColumns < ncols:
        header.InsertColumn(header.LastColumnNumber + 1)

    col_mms = [(table['col_widths'][c]
                if c < len(table['col_widths'])
                and table['col_widths'][c] else def_colw) * scale
               for c in range(ncols)]
    row_mms = [(table['row_heights'][r]
                if r < len(table['row_heights'])
                and table['row_heights'][r] else def_rowh) * scale
               for r in range(nrows)]

    total_width_ft = sum(
        max(w, 1.0) * MM_TO_FT for w in col_mms)

    for sec_type in (SectionType.Body, SectionType.Footer,
                     SectionType.Summary):
        try:
            sec = tbl.GetSectionData(sec_type)
            if sec is None:
                continue
            nsec_cols = sec.NumberOfColumns
            if nsec_cols <= 0:
                continue
            sec_total = 0.0
            for sc in range(nsec_cols):
                try:
                    sec_total += sec.GetColumnWidth(
                        sec.FirstColumnNumber + sc)
                except Exception:
                    pass
            if sec_total < total_width_ft:
                per_col = total_width_ft / nsec_cols
                for sc in range(nsec_cols):
                    try:
                        sec.SetColumnWidth(
                            sec.FirstColumnNumber + sc,
                            per_col)
                    except Exception:
                        pass
        except Exception:
            pass

    apply_sizes_uniform(
        header, first_r, first_c, col_mms, row_mms)

    for (r1, c1, r2, c2) in table['merges']:
        try:
            mc = TableMergedCell(
                first_r + r1, first_c + c1,
                first_r + r2, first_c + c2)
            header.MergeCells(mc)
        except Exception:
            pass

    for r in range(nrows):
        for c in range(ncols):
            text = data[r][c]
            if text and text.strip():
                try:
                    header.SetCellText(
                        first_r + r, first_c + c, text)
                except Exception:
                    pass
            st = table['styles'].get((r, c))
            if st:
                try:
                    header.SetCellStyle(
                        first_r + r, first_c + c,
                        make_cell_style(st))
                except Exception:
                    pass

    img_log = []
    overlay = []
    imgs = table.get('images', [])
    img_apis = [m for m in dir(header)
                if 'image' in m.lower()
                or 'graphic' in m.lower()]

    if imgs:
        merge_lookup = {}
        for (mr1, mc1, mr2, mc2) in table['merges']:
            for rr in range(mr1, mr2 + 1):
                for cc in range(mc1, mc2 + 1):
                    merge_lookup[(rr, cc)] = (
                        mr1, mc1, mr2, mc2)

        def region_mm(row, col):
            reg = merge_lookup.get(
                (row, col), (row, col, row, col))
            r1, c1, r2, c2 = reg
            w = sum(col_mms[c]
                    for c in range(c1, min(c2 + 1, ncols)))
            h = sum(row_mms[r]
                    for r in range(r1, min(r2 + 1, nrows)))
            return w, h

        min_w = img_size_mm * IMG_BUFFER
        min_h = img_size_mm * IMG_ASPECT * IMG_BUFFER

        for im in imgs:
            if not im.get('file'):
                continue
            reg = merge_lookup.get(
                (im['row'], im['col']),
                (im['row'], im['col'],
                 im['row'], im['col']))
            r1, c1, r2, c2 = reg
            w, h = region_mm(im['row'], im['col'])
            if h < min_h:
                grow = (min_h - h) / max(1, (r2 - r1 + 1))
                for r in range(r1, min(r2 + 1, nrows)):
                    row_mms[r] += grow
                    try:
                        header.SetRowHeight(
                            first_r + r,
                            row_mms[r] * MM_TO_FT)
                    except Exception:
                        pass
            if w < min_w:
                grow = (min_w - w) / max(1, (c2 - c1 + 1))
                for c in range(c1, min(c2 + 1, ncols)):
                    col_mms[c] += grow
                    try:
                        header.SetColumnWidth(
                            first_c + c,
                            col_mms[c] * MM_TO_FT)
                    except Exception:
                        pass

        img_id_cache = {}
        for im in imgs:
            cell = "{}{}".format(
                _index_to_col(im['col']), im['row'] + 1)
            src_name = im.get('source') or '(unknown)'
            if not im.get('file'):
                img_log.append((
                    cell, src_name,
                    'SKIPPED - linked picture, bytes not '
                    'inside xlsx'))
                continue
            was_wmf = im['file'].lower().endswith(
                ('.wmf', '.emf'))
            reg_w, reg_h = region_mm(im['row'], im['col'])
            cache_key = (im['file'],
                         int(round(reg_w * 10)),
                         int(round(reg_h * 10)))
            if cache_key in img_id_cache:
                img_id = img_id_cache[cache_key]
            else:
                norm = normalize_image(
                    im['file'], reg_w, reg_h, img_size_mm,
                    out_tag='{}x{}'.format(
                        int(round(reg_w * 10)),
                        int(round(reg_h * 10))))
                if was_wmf and norm == im['file']:
                    img_log.append((
                        cell, src_name,
                        'SKIPPED - WMF/EMF could not be '
                        'converted.'))
                    continue
                img_id = import_image(norm)
                img_id_cache[cache_key] = img_id
            if img_id is None:
                img_log.append((
                    cell, src_name,
                    'FAILED - could not import image'))
                continue
            reg = merge_lookup.get(
                (im['row'], im['col']),
                (im['row'], im['col'],
                 im['row'], im['col']))
            tgt_r, tgt_c = reg[0], reg[1]
            placed = False
            errors = []
            if hasattr(header, 'InsertImage'):
                try:
                    valid = True
                    if hasattr(header,
                               'IsValidImageSymbolId'):
                        valid = (
                            header.IsValidImageSymbolId(
                                img_id))
                    if valid:
                        header.InsertImage(
                            first_r + tgt_r,
                            first_c + tgt_c, img_id)
                        placed = True
                    else:
                        errors.append(
                            'InsertImage: id rejected')
                except Exception as e:
                    errors.append(
                        'InsertImage: {}'.format(e))
            else:
                errors.append(
                    'InsertImage: not in this Revit')
            if not placed:
                for setter in ('SetCellImageId',
                               'SetImageCellId'):
                    if not hasattr(header, setter):
                        continue
                    try:
                        getattr(header, setter)(
                            first_r + tgt_r,
                            first_c + tgt_c, img_id)
                        placed = True
                        break
                    except Exception as e:
                        errors.append(
                            '{}: {}'.format(setter, e))
            if placed:
                img_log.append((
                    cell, src_name,
                    'IN SCHEDULE CELL (image set)'))
            else:
                overlay.append({
                    'row': im['row'],
                    'col': im['col'],
                    'img_id': img_id,
                    'cell': cell,
                    'source': src_name,
                    'errors': ' | '.join(errors)})

    final_cols = []
    for c in range(ncols):
        try:
            final_cols.append(
                header.GetColumnWidth(first_c + c))
        except Exception:
            final_cols.append(def_colw * MM_TO_FT)
    final_rows = []
    for r in range(nrows):
        try:
            final_rows.append(
                header.GetRowHeight(first_r + r))
        except Exception:
            final_rows.append(def_rowh * MM_TO_FT)

    return (sched, img_log, overlay,
            final_cols, final_rows, img_apis)


def import_image(filepath):
    try:
        from Autodesk.Revit.DB import (ImageTypeOptions,
                                       ImageType)
        try:
            from Autodesk.Revit.DB import ImageTypeSource
            opts = ImageTypeOptions(
                filepath, False, ImageTypeSource.Import)
        except ImportError:
            opts = ImageTypeOptions(filepath)
        img_type = ImageType.Create(doc, opts)
        return img_type.Id
    except Exception:
        try:
            from Autodesk.Revit.DB import ImageType
            img_type = ImageType.Create(doc, filepath)
            return img_type.Id
        except Exception:
            return None


# --------------------------------------------- sheet placement helper
def get_schedule_placements(name):
    """Return list of sheet placements for the schedule named `name`."""
    placements = []
    sched_id = None
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            if vs.Name == name:
                sched_id = vs.Id
                break
        except Exception:
            continue
    if sched_id is None:
        return placements
    for inst in FilteredElementCollector(doc).OfClass(
            ScheduleSheetInstance):
        try:
            if inst.ScheduleId == sched_id:
                placements.append({
                    'sheet_id': inst.OwnerViewId,
                    'point': inst.Point,
                })
        except Exception:
            continue
    return placements


# ----------------------------------------------------------- run UI
xaml_path = script.get_bundle_file('ui.xaml')
win = TableGenWindow(xaml_path)
win.ShowDialog()

if not win.result:
    script.exit()

cfg = win.result


def _on_dialog(sender, args):
    try:
        args.OverrideResult(1)
    except Exception:
        pass


uiapp = revit.uidoc.Application
try:
    uiapp.DialogBoxShowing += _on_dialog
except Exception:
    pass


def read_table_for(source_file, settings):
    if source_file.lower().endswith('.csv'):
        with codecs.open(source_file, 'r',
                         encoding='utf-8-sig') as f:
            return plain_table(parse_csv(f.read()))
    sheet_idx = settings.get('sheet_index', 0)
    crop = None
    area_idx = settings.get('area_index', 0)
    if area_idx == 1:
        crop = xlsx_print_area(source_file, sheet_idx)
    elif area_idx == 2:
        crop = parse_range(settings.get('range', ''))
    table = xlsx_read_sheet(source_file, sheet_idx, crop=crop)
    if table is not None:
        imgs = xlsx_images(source_file, sheet_idx,
                           tempfile.gettempdir())
        r_off = crop[0] if crop else 0
        c_off = crop[1] if crop else 0
        nrows = len(table['data'])
        ncols = (len(table['data'][0]) if nrows else 0)
        local = []
        for im in imgs:
            lr = im['row'] - r_off
            lc = im['col'] - c_off
            if 0 <= lr < nrows and 0 <= lc < ncols:
                im2 = dict(im)
                im2['row'] = lr
                im2['col'] = lc
                local.append(im2)
        table['images'] = local
    return table


def read_table_for_part(source_file, settings):
    """Re-read source and slice out the correct part.

    Respects 'split_header' in settings (user-configured header rows).
    """
    full = read_table_for(source_file, settings)
    if not full:
        return full
    part = settings.get('split_part')
    total = settings.get('split_total')
    if not part or not total or total < 2:
        return full

    # Use saved header row count; fall back to global default
    header_rows = settings.get('split_header', SPLIT_HEADER_ROWS)

    nrows = len(full['data'])
    ncols = len(full['data'][0]) if nrows else 0
    cat_rows = detect_category_header_rows(
        nrows, ncols, full['merges'], full['styles'])
    scale = settings.get('scale', 1.0)
    max_h = settings.get('split_max_h') or 0

    ranges = compute_balanced_split(
        nrows, header_rows, total,
        full['row_heights'], full['merges'], cat_rows,
        scale=scale)
    if part < 1 or part > len(ranges):
        return full
    start, end = ranges[part - 1]

    if max_h and max_h > 0:
        fit_factor = compute_fit_scale_factor(
            full, header_rows, len(ranges), max_h, scale,
            ranges=ranges)
        if fit_factor and abs(fit_factor - 1.0) > 1e-9:
            full['row_heights'] = [
                (h * fit_factor) if h else h
                for h in full['row_heights']]

        pad_overrides = pad_parts_to_equal_height(
            full, ranges, header_rows, max_h, scale=1.0)
        part_idx = part - 1
        part_override = {
            src_r: h
            for (pi, src_r), h in pad_overrides.items()
            if pi == part_idx
        }
        return build_part_table(full, start, end, header_rows,
                                row_h_override=part_override)

    return build_part_table(full, start, end, header_rows)


def delete_existing_schedule(name):
    for vs in FilteredElementCollector(doc).OfClass(
            ViewSchedule):
        try:
            if vs.Name == name:
                doc.Delete(vs.Id)
                return True
        except Exception:
            continue
    return False


def run_one(name, table, settings, source_file):
    placements = get_schedule_placements(name)

    t = Transaction(doc, "TableGen: {}".format(name))
    t.Start()
    try:
        delete_existing_schedule(name)
        result = create_schedule(
            name, table,
            settings.get('def_colw', 40.0),
            settings.get('def_rowh', 8.0),
            settings.get('scale', 1.0),
            settings.get('img_size', 20.0))
        new_view = result[0]
        img_log = result[1]
        overlay = result[2]
        for ov in overlay:
            img_log.append((
                ov['cell'], ov['source'],
                'NOT PLACED - Revit refused in-cell image. '
                'Attempts: {}'.format(
                    ov.get('errors', '?'))))

        for p in placements:
            try:
                ScheduleSheetInstance.Create(
                    doc, p['sheet_id'], new_view.Id, p['point'])
            except Exception:
                pass

        t.Commit()
    except Exception:
        if t.HasStarted():
            t.RollBack()
        raise
    placed = len([1 for _, _, s in img_log
                  if s.startswith('IN SCHEDULE CELL')])
    save_history_entry(
        name, source_file,
        len(table['data']), len(table['data'][0]),
        placed, settings)
    return new_view, img_log, placed


# --------------------------------------------------------- main dispatch
all_problems = []
last_view = None
total_imgs = 0
total_placed = 0
done_names = []

try:
    # ---- REMOVE ----
    if 'remove_names' in cfg:
        names = set(cfg['remove_names'])
        deleted = []
        t = Transaction(doc, "TableGen: remove tables")
        t.Start()
        try:
            for vs in list(
                    FilteredElementCollector(doc)
                    .OfClass(ViewSchedule)):
                try:
                    if vs.Name in names:
                        doc.Delete(vs.Id)
                        deleted.append(vs.Name)
                except Exception:
                    continue
            t.Commit()
        except Exception:
            if t.HasStarted():
                t.RollBack()
            raise
        entries = [e for e in load_history()
                   if e.get('name') not in names]
        save_history(entries)
        done_names = deleted
        missing = sorted(names - set(deleted))
        for nm in missing:
            all_problems.append(
                "{}: schedule not found in project "
                "(removed from list only)".format(nm))

    # ---- RESIZE ROWS ----
    elif 'resize_jobs' in cfg:
        all_problems.extend(cfg.get('resize_problems', []))
        new_rowh = cfg['new_rowh']
        for e in cfg['resize_jobs']:
            name = e.get('name')
            srcf = e.get('source')
            settings = dict(e.get('settings') or {})
            settings['def_rowh'] = new_rowh
            settings['img_size'] = max(4.0, new_rowh * 0.9)
            try:
                table = read_table_for_part(srcf, settings)
                if not table or not table.get('data'):
                    all_problems.append(
                        "{}: no data found in file"
                        .format(name))
                    continue
                table['row_heights'] = [
                    new_rowh for _ in table['data']]
                view, img_log, placed = run_one(
                    name, table, settings, srcf)
                last_view = view
                done_names.append(name)
                total_imgs += len(
                    table.get('images', []))
                total_placed += placed
                for c, f, s in img_log:
                    if not s.startswith('IN SCHEDULE CELL'):
                        all_problems.append(
                            "{} {}: {}".format(
                                name, c, s.split('.')[0]))
            except Exception as err:
                all_problems.append(
                    "{}: {}".format(name, err))

    # ---- DUPLICATE ----
    elif 'duplicate_jobs' in cfg:
        for job in cfg['duplicate_jobs']:
            orig_entry = job['original']
            new_name = job['new_name']
            orig_name = orig_entry.get('name', '')
            srcf = orig_entry.get('source', '')
            settings = orig_entry.get('settings') or {}

            orig_view = None
            for vs in FilteredElementCollector(doc).OfClass(
                    ViewSchedule):
                try:
                    if vs.Name == orig_name:
                        orig_view = vs
                        break
                except Exception:
                    continue
            if orig_view is None:
                all_problems.append(
                    "{}: original schedule '{}' not found"
                    .format(new_name, orig_name))
                continue

            if (srcf and srcf != '(clipboard paste)'
                    and os.path.exists(srcf)):
                try:
                    table = read_table_for_part(srcf, settings)
                    if not table or not table.get('data'):
                        raise ValueError(
                            "No data returned from file")
                    view, img_log, placed = run_one(
                        new_name, table, settings, srcf)
                    last_view = view
                    done_names.append(new_name)
                    total_imgs += len(
                        table.get('images', []))
                    total_placed += placed
                    for c, f, s in img_log:
                        if not s.startswith(
                                'IN SCHEDULE CELL'):
                            all_problems.append(
                                "{} {}: {}".format(
                                    new_name, c,
                                    s.split('.')[0]))
                    continue
                except Exception as err:
                    all_problems.append(
                        "{}: could not re-read source - {}"
                        .format(new_name, err))

            try:
                from Autodesk.Revit.DB import (
                    ViewDuplicateOption)
                t = Transaction(
                    doc,
                    "TableGen: duplicate {}".format(
                        orig_name))
                t.Start()
                try:
                    new_id = orig_view.Duplicate(
                        ViewDuplicateOption.Duplicate)
                    new_vs = doc.GetElement(new_id)
                    new_vs.Name = new_name
                    t.Commit()
                except Exception:
                    if t.HasStarted():
                        t.RollBack()
                    raise
                last_view = new_vs
                done_names.append(new_name)
                size_str = orig_entry.get('size', '0x0')
                try:
                    sz_parts = size_str.split('x')
                    orig_nrows = int(sz_parts[0])
                    orig_ncols = int(sz_parts[-1])
                except Exception:
                    orig_nrows, orig_ncols = 0, 0
                save_history_entry(
                    new_name,
                    srcf or '(clipboard paste)',
                    orig_nrows, orig_ncols,
                    int(orig_entry.get('images', 0)),
                    settings)
            except Exception as err:
                all_problems.append(
                    "{}: duplicate (fallback) failed - {}"
                    .format(new_name, err))

    # ---- RELOAD ----
    elif 'reload_jobs' in cfg:
        all_problems.extend(cfg.get('reload_problems', []))
        for e in cfg['reload_jobs']:
            name = e.get('name')
            srcf = e.get('source')
            settings = e.get('settings') or {}
            try:
                table = read_table_for_part(srcf, settings)
                if not table or not table.get('data'):
                    all_problems.append(
                        "{}: no data found in file"
                        .format(name))
                    continue
                view, img_log, placed = run_one(
                    name, table, settings, srcf)
                last_view = view
                done_names.append(name)
                total_imgs += len(
                    table.get('images', []))
                total_placed += placed
                for c, f, s in img_log:
                    if not s.startswith('IN SCHEDULE CELL'):
                        all_problems.append(
                            "{} {}: {}".format(
                                name, c, s.split('.')[0]))
            except Exception as err:
                all_problems.append(
                    "{}: {}".format(name, err))

    # ---- SPLIT INTO N EXACT-EQUAL PARTS (SCALE + PAD) ----
    elif 'split_entry' in cfg:
        entry = cfg['split_entry']
        max_h = cfg['split_max_h_mm']
        num_parts = cfg['split_num_parts']
        # Use user-configured header row count
        user_header_rows = cfg.get('split_header_rows',
                                   SPLIT_HEADER_ROWS)
        srcf = entry.get('source')
        base_settings = dict(entry.get('settings') or {})
        base_name = entry.get('name', 'Table')

        try:
            full_table = read_table_for(srcf, base_settings)
        except Exception as err:
            all_problems.append(
                "{}: could not read source - {}"
                .format(base_name, err))
            full_table = None

        if not full_table or not full_table.get('data'):
            all_problems.append(
                "{}: no data found in source".format(base_name))
        else:
            nrows_full = len(full_table['data'])
            ncols_full = (len(full_table['data'][0])
                          if nrows_full else 0)

            # Guard: header_rows must not exceed total rows
            if user_header_rows >= nrows_full:
                user_header_rows = max(0, nrows_full - 1)
                all_problems.append(
                    "{}: HEADER_ROWS clamped to {} "
                    "(table only has {} rows)"
                    .format(base_name, user_header_rows,
                            nrows_full))

            cat_rows = detect_category_header_rows(
                nrows_full, ncols_full,
                full_table['merges'], full_table['styles'])
            scale = base_settings.get('scale', 1.0)

            # 1) Split into balanced chunks using user header rows
            ranges = compute_balanced_split(
                nrows_full, user_header_rows, num_parts,
                full_table['row_heights'],
                full_table['merges'], cat_rows,
                scale=scale)
            actual_parts = len(ranges)

            # 2) Scale so tallest part <= max_h
            fit_factor = compute_fit_scale_factor(
                full_table, user_header_rows, actual_parts,
                max_h, scale, ranges=ranges)
            if fit_factor and abs(fit_factor - 1.0) > 1e-9:
                full_table['row_heights'] = [
                    (h * fit_factor) if h else h
                    for h in full_table['row_heights']]

            # 3) Scale image size by same factor
            orig_img_size = base_settings.get('img_size', 20.0)
            new_img_size = orig_img_size * fit_factor
            if new_img_size < 3.0:
                new_img_size = 3.0

            # 4) PAD each part's data rows so every part becomes
            #    EXACTLY max_h tall.
            pad_overrides = pad_parts_to_equal_height(
                full_table, ranges, user_header_rows,
                max_h, scale=1.0)

            # Compute final part heights (post-pad) for the log
            part_heights_after = []
            for i, (s, e) in enumerate(ranges):
                h = 0.0
                for r in range(user_header_rows):
                    if r < len(full_table['row_heights']) \
                            and full_table['row_heights'][r]:
                        h += full_table['row_heights'][r]
                for r in range(s, e + 1):
                    if r < user_header_rows:
                        continue
                    if (i, r) in pad_overrides:
                        h += pad_overrides[(i, r)]
                    elif r < len(full_table['row_heights']) \
                            and full_table['row_heights'][r]:
                        h += full_table['row_heights'][r]
                part_heights_after.append(h)

            if abs(fit_factor - 1.0) > 1e-9 or pad_overrides:
                all_problems.append(
                    "{}: scaled to {:.1f}% (images {:.1f} mm), "
                    "then padded to exactly {} mm each. "
                    "Header rows repeated: {}. "
                    "Parts: {}"
                    .format(base_name,
                            fit_factor * 100.0,
                            new_img_size,
                            max_h,
                            user_header_rows,
                            ", ".join(
                                "S{:02d}={:.0f}mm".format(
                                    i + 1, h)
                                for i, h in enumerate(
                                    part_heights_after))))

            if actual_parts < num_parts:
                all_problems.append(
                    "{}: requested {} parts but source only "
                    "supports {} (not enough data rows)."
                    .format(base_name, num_parts, actual_parts))

            # Remove the original schedule
            t = Transaction(
                doc,
                "TableGen: remove original for split")
            t.Start()
            try:
                delete_existing_schedule(base_name)
                t.Commit()
            except Exception:
                if t.HasStarted():
                    t.RollBack()
            hist = load_history()
            hist = [h for h in hist
                    if h.get('name') != base_name]
            save_history(hist)

            for i, (r_start, r_end) in enumerate(ranges):
                part_num = i + 1
                part_name = "{}_Sheet {:02d}".format(
                    base_name, part_num)
                try:
                    # Extract this part's row-height overrides
                    part_override = {
                        src_r: h
                        for (pi, src_r), h
                        in pad_overrides.items()
                        if pi == i
                    }

                    part_table = build_part_table(
                        full_table, r_start, r_end,
                        user_header_rows,
                        row_h_override=part_override)

                    part_settings = dict(base_settings)
                    part_settings['img_size'] = new_img_size
                    part_settings['split_part'] = part_num
                    part_settings['split_total'] = actual_parts
                    # Save user-chosen header rows so reload /
                    # re-split uses same value automatically
                    part_settings['split_header'] = (
                        user_header_rows)
                    part_settings['split_max_h'] = max_h
                    part_settings['split_num_parts'] = num_parts

                    view, img_log, placed = run_one(
                        part_name, part_table,
                        part_settings, srcf)
                    last_view = view
                    done_names.append(part_name)
                    total_imgs += len(
                        part_table.get('images', []))
                    total_placed += placed
                    for c, f, s in img_log:
                        if not s.startswith(
                                'IN SCHEDULE CELL'):
                            all_problems.append(
                                "{} {}: {}".format(
                                    part_name, c,
                                    s.split('.')[0]))
                except Exception as err:
                    all_problems.append(
                        "{}: {}".format(part_name, err))

    # ---- CREATE (normal) ----
    else:
        view, img_log, placed = run_one(
            cfg['name'], cfg['table'],
            cfg['settings'], cfg.get('source_file'))
        last_view = view
        done_names.append(cfg['name'])
        total_imgs = len(cfg['table'].get('images', []))
        total_placed = placed
        for c, f, s in img_log:
            if not s.startswith('IN SCHEDULE CELL'):
                all_problems.append(
                    "{}: {}".format(c, s.split('.')[0]))

finally:
    try:
        uiapp.DialogBoxShowing -= _on_dialog
    except Exception:
        pass

if last_view is not None:
    try:
        revit.uidoc.ActiveView = last_view
    except Exception:
        pass

# ------------------------------------------------ completion feedback
if all_problems:
    lines = all_problems[:10]
    if len(all_problems) > 10:
        lines.append(
            "... and {} more".format(len(all_problems) - 10))
    forms.alert(
        "{} table(s) processed: {}\n"
        "{} of {} images placed.\n\nIssues:\n{}"
        .format(len(done_names),
                ', '.join(done_names) or '-',
                total_placed, total_imgs,
                '\n'.join(lines)),
        title="TableGen")
else:
    try:
        if 'remove_names' in cfg:
            forms.toast(
                "{} schedule(s) deleted: {}"
                .format(len(done_names),
                        ', '.join(done_names) or '-'),
                title="TableGen")
        elif 'duplicate_jobs' in cfg:
            forms.toast(
                "{} schedule(s) duplicated: {}"
                .format(len(done_names),
                        ', '.join(done_names) or '-'),
                title="TableGen")
        elif 'resize_jobs' in cfg:
            forms.toast(
                "{} schedule(s) resized: {} | {} images"
                .format(len(done_names),
                        ', '.join(done_names) or '-',
                        total_placed),
                title="TableGen")
        elif 'split_entry' in cfg:
            forms.toast(
                "{} sheet(s) created: {} | {} images"
                .format(len(done_names),
                        ', '.join(done_names),
                        total_placed),
                title="TableGen")
        else:
            forms.toast(
                "{} table(s) updated: {} | {} images"
                .format(len(done_names),
                        ', '.join(done_names),
                        total_placed),
                title="TableGen")
    except Exception:
        pass