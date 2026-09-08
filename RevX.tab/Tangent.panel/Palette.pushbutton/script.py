# -*- coding: utf-8 -*-
"""
Grid Divider & Image Linker for Palette
-------------------------------------------------------------------------------
Custom sizes are ACTUAL project units by default.
Adjust checkboxes (ON by default) scale the pattern to fill the picked box.
"""

__title__ = "Palette"
__author__ = "Jesto Joy"

import os
import sys
import re
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System")

import System
import System.Windows as W
import System.Windows.Controls as Controls
import System.Windows.Media as Media
import System.Windows.Forms as WinForms

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector, XYZ, Line, Transaction,
    TextNote, TextNoteOptions, ElementId
)
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
logger = script.get_logger()


# =============================================================================
# HELPERS
# =============================================================================
def pick_folder_dialog(title):
    dialog = WinForms.FolderBrowserDialog()
    dialog.Description = title
    dialog.ShowNewFolderButton = False
    if dialog.ShowDialog() == WinForms.DialogResult.OK:
        return dialog.SelectedPath
    return None


def get_line_styles():
    styles = []
    try:
        lines_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
        for sub in lines_cat.SubCategories:
            gs = sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
            if gs:
                styles.append(gs)
    except Exception:
        pass
    return styles


def pick_line_style():
    styles = get_line_styles()
    if not styles:
        forms.alert("No Line Styles found.", exitscript=True)
    by_name = {s.Name: s for s in styles if s.Name}
    chosen = forms.SelectFromList.show(
        sorted(by_name.keys()),
        title="Select Line Style for Grid",
        button_name="Use This Style"
    )
    if not chosen:
        sys.exit()
    return by_name[chosen]


def pick_two_corners():
    try:
        p1 = uidoc.Selection.PickPoint("Pick the FIRST corner (top-left recommended)")
        p2 = uidoc.Selection.PickPoint("Pick the OPPOSITE corner")
    except OperationCanceledException:
        sys.exit()
    return p1, p2


def rect_from_points(p1, p2):
    min_x, max_x = min(p1.X, p2.X), max(p1.X, p2.X)
    min_y, max_y = min(p1.Y, p2.Y), max(p1.Y, p2.Y)
    z = p1.Z
    if (max_x - min_x) < 1e-9 or (max_y - min_y) < 1e-9:
        forms.alert("Invalid rectangle picked.", exitscript=True)
    return min_x, min_y, max_x, max_y, z


def get_length_unit_type_id():
    try:
        return doc.GetUnits().GetFormatOptions(DB.SpecTypeId.Length).GetUnitTypeId()
    except Exception:
        return DB.UnitTypeId.Millimeters


def get_unit_label(unit_type_id):
    try:
        return DB.LabelUtils.GetLabelForUnit(unit_type_id)
    except Exception:
        return "units"


def to_internal(value_display, unit_type_id):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_display), unit_type_id)
    except Exception:
        return float(value_display) / 304.8


def to_display(value_internal, unit_type_id):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(value_internal), unit_type_id)
    except Exception:
        return float(value_internal) * 304.8


def parse_sizes(text):
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        raise ValueError("Enter at least one size.")
    vals = []
    for p in parts:
        v = float(p)
        if v <= 0:
            raise ValueError("Sizes must be > 0.")
        vals.append(v)
    return vals


def equal_strip_lengths(total_length, count):
    if count < 1:
        count = 1
    if total_length <= 0:
        raise ValueError("Length must be > 0.")
    step = total_length / float(count)
    return [step] * count


def custom_strip_lengths(sizes_display, repeat_count, unit_type_id, adjust, target_length_internal):
    if repeat_count < 1:
        repeat_count = 1
    sizes_internal = [to_internal(s, unit_type_id) for s in sizes_display]
    strips = []
    for _ in range(repeat_count):
        strips.extend(sizes_internal)

    total = sum(strips)
    if total <= 0:
        raise ValueError("Pattern sizes must sum to > 0.")

    if adjust:
        if target_length_internal <= 0:
            raise ValueError("Target length must be > 0 for Adjust.")
        scale = target_length_internal / total
        strips = [s * scale for s in strips]

    return strips


def edges_from_strips(start, strips, descending=False):
    edges = [start]
    pos = start
    for length in strips:
        pos = pos - length if descending else pos + length
        edges.append(pos)
    return edges


def clean_header_name(name):
    if not name:
        return name
    s = str(name).strip()
    s2 = re.sub(r'^\d+\s*[-–—._:)]\s*', '', s)
    s2 = re.sub(r'^\d+\s+', '', s2)
    s2 = s2.strip()
    return s2 if s2 else s


def mm_to_internal(mm):
    try:
        if hasattr(DB, "UnitTypeId") and hasattr(DB.UnitTypeId, "Millimeters"):
            return DB.UnitUtils.ConvertToInternalUnits(float(mm), DB.UnitTypeId.Millimeters)
    except Exception:
        pass
    try:
        return DB.UnitUtils.ConvertToInternalUnits(float(mm), DB.DisplayUnitType.DUT_MILLIMETERS)
    except Exception:
        pass
    return float(mm) / 304.8


def format_strips_display(strips_internal, unit_type_id, unit_label, adjusted=False):
    vals = [to_display(v, unit_type_id) for v in strips_internal]
    txt = ", ".join("{:g}".format(round(v, 3)) for v in vals[:12])
    if len(vals) > 12:
        txt += ", ..."
    total = to_display(sum(strips_internal), unit_type_id)
    mode = "adjusted to fit" if adjusted else "exact"
    return "{} {}  | total {:g} {} ({})".format(
        txt, unit_label, round(total, 3), unit_label, mode
    )


# =============================================================================
# MAIN GRID UI
# =============================================================================
class DivideWindow(forms.WPFWindow):
    def __init__(self, xaml_file, aspect_ratio, width_internal, height_internal, unit_type_id):
        forms.WPFWindow.__init__(self, xaml_file)
        self.aspect_ratio = aspect_ratio if aspect_ratio > 0 else 1.0
        self.width_internal = width_internal
        self.height_internal = height_internal
        self.unit_type_id = unit_type_id
        self.unit_label = get_unit_label(unit_type_id)

        self.result_ok = False
        self.link_images = False
        self.max_capacity = 0
        self.adjust_rows = False
        self.adjust_cols = False

        self.row_strips = []
        self.col_strips = []

        self.RowsBox.Text = "9"
        self.ColsBox.Text = "5"
        self.RowUnitLabel.Text = "ACTUAL sizes in {} (e.g. 20, 100, 20)".format(self.unit_label)
        self.ColUnitLabel.Text = "ACTUAL sizes in {} (e.g. 60, 60, 60)".format(self.unit_label)

        try:
            self.RowSizesBox.Text = "20, 100, 20"
            self.ColSizesBox.Text = "60, 60, 60"
            self.RowRepeatBox.Text = "3"
            self.ColRepeatBox.Text = "1"
        except Exception:
            pass

        # Ensure Adjust defaults to ON even if XAML missed IsChecked
        try:
            self.RowAdjustCheck.IsChecked = True
        except Exception:
            pass
        try:
            self.ColAdjustCheck.IsChecked = True
        except Exception:
            pass

        for box in (self.RowsBox, self.ColsBox, self.RowSizesBox,
                    self.RowRepeatBox, self.ColSizesBox, self.ColRepeatBox):
            box.TextChanged += self.on_change

        self.RowEqualRadio.Checked += self.row_mode_changed
        self.RowCustomRadio.Checked += self.row_mode_changed
        self.ColEqualRadio.Checked += self.col_mode_changed
        self.ColCustomRadio.Checked += self.col_mode_changed

        if hasattr(self, "RowAdjustCheck"):
            self.RowAdjustCheck.Checked += self.on_change
            self.RowAdjustCheck.Unchecked += self.on_change
        if hasattr(self, "ColAdjustCheck"):
            self.ColAdjustCheck.Checked += self.on_change
            self.ColAdjustCheck.Unchecked += self.on_change

        self.draw_preview()

    def _row_adjust(self):
        try:
            return bool(self.RowAdjustCheck.IsChecked)
        except Exception:
            return True  # default ON

    def _col_adjust(self):
        try:
            return bool(self.ColAdjustCheck.IsChecked)
        except Exception:
            return True  # default ON

    def row_mode_changed(self, sender, args):
        custom = bool(self.RowCustomRadio.IsChecked)
        self.RowEqualPanel.Visibility = W.Visibility.Collapsed if custom else W.Visibility.Visible
        self.RowCustomPanel.Visibility = W.Visibility.Visible if custom else W.Visibility.Collapsed
        self.draw_preview()

    def col_mode_changed(self, sender, args):
        custom = bool(self.ColCustomRadio.IsChecked)
        self.ColEqualPanel.Visibility = W.Visibility.Collapsed if custom else W.Visibility.Visible
        self.ColCustomPanel.Visibility = W.Visibility.Visible if custom else W.Visibility.Collapsed
        self.draw_preview()

    def on_change(self, sender, args):
        self.draw_preview()

    def _get_int(self, box, fallback):
        try:
            return max(1, min(200, int(box.Text)))
        except Exception:
            return fallback

    def get_row_strips(self):
        if self.RowEqualRadio.IsChecked:
            c = self._get_int(self.RowsBox, 3)
            strips = equal_strip_lengths(self.height_internal, c)
            return strips, None, "Rows: {} equal over picked height".format(c), False
        try:
            sizes = parse_sizes(self.RowSizesBox.Text)
            rep = self._get_int(self.RowRepeatBox, 1)
            adj = self._row_adjust()
            strips = custom_strip_lengths(
                sizes, rep, self.unit_type_id, adj, self.height_internal
            )
            msg = "Rows: " + format_strips_display(strips, self.unit_type_id, self.unit_label, adj)
            return strips, None, msg, adj
        except Exception as e:
            return [], str(e), "", False

    def get_col_strips(self):
        if self.ColEqualRadio.IsChecked:
            c = self._get_int(self.ColsBox, 3)
            strips = equal_strip_lengths(self.width_internal, c)
            return strips, None, "Cols: {} equal over picked width".format(c), False
        try:
            sizes = parse_sizes(self.ColSizesBox.Text)
            rep = self._get_int(self.ColRepeatBox, 1)
            adj = self._col_adjust()
            strips = custom_strip_lengths(
                sizes, rep, self.unit_type_id, adj, self.width_internal
            )
            msg = "Cols: " + format_strips_display(strips, self.unit_type_id, self.unit_label, adj)
            return strips, None, msg, adj
        except Exception as e:
            return [], str(e), "", False

    def draw_preview(self):
        import System.Windows.Shapes as Shapes

        rs, r_err, r_sum, r_adj = self.get_row_strips()
        cs, c_err, c_sum, c_adj = self.get_col_strips()
        errors = [e for e in (r_err, c_err) if e]
        self.ErrorText.Text = " | ".join(errors)
        self.SummaryText.Text = "{}   {}".format(r_sum, c_sum)

        valid = (len(errors) == 0 and len(rs) > 0 and len(cs) > 0)
        self.CreateBtn.IsEnabled = valid
        self.LinkImgBtn.IsEnabled = valid

        canvas = self.PreviewCanvas
        canvas.Children.Clear()
        if not valid:
            return

        total_w = sum(cs)
        total_h = sum(rs)
        ar = (total_w / total_h) if total_h > 0 else 1.0

        cw, ch, margin = float(canvas.Width), float(canvas.Height), 12.0
        avail_w, avail_h = cw - 2 * margin, ch - 2 * margin
        if (avail_w / avail_h) > ar:
            rect_h, rect_w = avail_h, avail_h * ar
        else:
            rect_w, rect_h = avail_w, avail_w / ar
        ox, oy = (cw - rect_w) / 2.0, (ch - rect_h) / 2.0

        def add_line(x1, y1, x2, y2, t):
            ln = Shapes.Line()
            ln.X1, ln.Y1, ln.X2, ln.Y2 = x1, y1, x2, y2
            ln.Stroke = Media.Brushes.Black
            ln.StrokeThickness = t
            canvas.Children.Add(ln)

        add_line(ox, oy, ox + rect_w, oy, 1.5)
        add_line(ox + rect_w, oy, ox + rect_w, oy + rect_h, 1.5)
        add_line(ox + rect_w, oy + rect_h, ox, oy + rect_h, 1.5)
        add_line(ox, oy + rect_h, ox, oy, 1.5)

        x = ox
        for L in cs[:-1]:
            x += rect_w * (L / total_w)
            add_line(x, oy, x, oy + rect_h, 1.0)

        y = oy
        for L in rs[:-1]:
            y += rect_h * (L / total_h)
            add_line(ox, y, ox + rect_w, y, 1.0)

    def _store_results(self, link):
        rs, re, _, r_adj = self.get_row_strips()
        cs, ce, _, c_adj = self.get_col_strips()
        if re or ce or not rs or not cs:
            return False
        self.row_strips = rs
        self.col_strips = cs
        self.adjust_rows = r_adj
        self.adjust_cols = c_adj
        self.result_ok = True
        self.link_images = link
        if link:
            if len(rs) % 3 != 0:
                forms.alert(
                    "Link Images needs rows in groups of 3 (Header, Image, Label).\n"
                    "Example: 20, 100, 20 with Repeat N.",
                    warn_icon=True
                )
                self.result_ok = False
                return False
            self.max_capacity = (len(rs) // 3) * len(cs)
        return True

    def create_click(self, sender, args):
        if self._store_results(False):
            self.Close()

    def link_images_click(self, sender, args):
        if self._store_results(True):
            self.Close()

    def cancel_click(self, sender, args):
        self.result_ok = False
        self.Close()


# =============================================================================
# GROUPED IMAGE SELECTOR UI
# =============================================================================
class ImgItem(object):
    def __init__(self, cat, path, name):
        self.category = cat.upper()
        self.filepath = path
        self.name = name
        self.label = os.path.splitext(name)[0].upper()


class ImageSelectorWindow(W.Window):
    def __init__(self, grouped, max_cap):
        W.Window.__init__(self)
        self.Title = "Select Images  —  Capacity: {} slots".format(max_cap)
        self.Height = 580
        self.Width = 640
        self.WindowStartupLocation = W.WindowStartupLocation.CenterScreen
        self.ResizeMode = W.ResizeMode.CanResize
        self.Background = Media.Brushes.White

        self._max_cap = max_cap
        self._checks = []
        self._bulk_updating = False
        self.selected = []

        root = Controls.DockPanel()
        self.Content = root

        bottom = Controls.StackPanel()
        bottom.Orientation = Controls.Orientation.Horizontal
        bottom.Margin = W.Thickness(10)
        Controls.DockPanel.SetDock(bottom, Controls.Dock.Bottom)

        self.counter = Controls.TextBlock()
        self.counter.VerticalAlignment = W.VerticalAlignment.Center
        self.counter.Margin = W.Thickness(0, 0, 16, 0)
        self.counter.FontWeight = W.FontWeights.Bold
        bottom.Children.Add(self.counter)

        def _btn(label, handler, width=85):
            b = Controls.Button()
            b.Content = label
            b.Width = width
            b.Height = 28
            b.Margin = W.Thickness(0, 0, 6, 0)
            b.Click += handler
            return b

        bottom.Children.Add(_btn("Select All", self._select_all))
        bottom.Children.Add(_btn("Clear", self._clear))
        bottom.Children.Add(_btn("Invert", self._invert))
        spacer = Controls.TextBlock()
        spacer.Width = 20
        bottom.Children.Add(spacer)
        ok = _btn("OK", self._ok, 90)
        ok.FontWeight = W.FontWeights.Bold
        bottom.Children.Add(ok)
        bottom.Children.Add(_btn("Cancel", self._cancel, 90))
        root.Children.Add(bottom)

        scroll = Controls.ScrollViewer()
        scroll.VerticalScrollBarVisibility = Controls.ScrollBarVisibility.Auto
        root.Children.Add(scroll)

        list_panel = Controls.StackPanel()
        list_panel.Margin = W.Thickness(6)
        scroll.Content = list_panel

        for cat in sorted(grouped.keys()):
            items = grouped[cat]
            header = Controls.Border()
            header.Background = Media.BrushConverter().ConvertFromString("#FF4A90D9")
            header.Padding = W.Thickness(8, 5, 8, 5)
            header.Margin = W.Thickness(0, 6, 0, 0)
            hdr_sp = Controls.StackPanel()
            hdr_sp.Orientation = Controls.Orientation.Horizontal
            grp_cb = Controls.CheckBox()
            grp_cb.VerticalAlignment = W.VerticalAlignment.Center
            grp_cb.Margin = W.Thickness(0, 0, 8, 0)
            hdr_sp.Children.Add(grp_cb)
            hdr_txt = Controls.TextBlock()
            hdr_txt.Text = "{}  ({} items)".format(cat, len(items))
            hdr_txt.Foreground = Media.Brushes.White
            hdr_txt.FontWeight = W.FontWeights.SemiBold
            hdr_txt.FontSize = 13
            hdr_sp.Children.Add(hdr_txt)
            header.Child = hdr_sp
            list_panel.Children.Add(header)

            cat_checks = []
            for it in items:
                row = Controls.Border()
                row.BorderBrush = Media.BrushConverter().ConvertFromString("#FFEEEEEE")
                row.BorderThickness = W.Thickness(0, 0, 0, 1)
                row.Padding = W.Thickness(28, 4, 8, 4)
                cb = Controls.CheckBox()
                cb.Content = it.name
                cb.FontSize = 12
                cb.Tag = it
                cb.Checked += self._on_check
                cb.Unchecked += self._on_check
                row.Child = cb
                list_panel.Children.Add(row)
                self._checks.append((it, cb))
                cat_checks.append(cb)

            def _make_grp_handler(children, g_cb):
                def handler(sender, args):
                    if self._bulk_updating:
                        return
                    self._bulk_updating = True
                    state = bool(g_cb.IsChecked)
                    for c in children:
                        c.IsChecked = state
                    self._clamp_to_capacity()
                    self._bulk_updating = False
                    self._update_counter()
                return handler
            grp_cb.Checked += _make_grp_handler(cat_checks, grp_cb)
            grp_cb.Unchecked += _make_grp_handler(cat_checks, grp_cb)

        self._update_counter()

    def _selected_items(self):
        return [it for it, cb in self._checks if cb.IsChecked]

    def _update_counter(self):
        n = len(self._selected_items())
        self.counter.Text = "Selected: {} / {}".format(n, self._max_cap)
        self.counter.Foreground = Media.Brushes.Red if n > self._max_cap else Media.Brushes.Black

    def _clamp_to_capacity(self):
        selected = [cb for it, cb in self._checks if cb.IsChecked]
        if len(selected) > self._max_cap:
            for cb in selected[self._max_cap:]:
                cb.IsChecked = False

    def _on_check(self, sender, args):
        if self._bulk_updating:
            return
        if sender.IsChecked and len(self._selected_items()) > self._max_cap:
            sender.IsChecked = False
            forms.alert("Grid capacity is {} images.".format(self._max_cap), title="Capacity Reached")
        self._update_counter()

    def _select_all(self, sender, args):
        self._bulk_updating = True
        for it, cb in self._checks:
            cb.IsChecked = True
        self._clamp_to_capacity()
        self._bulk_updating = False
        self._update_counter()

    def _clear(self, sender, args):
        self._bulk_updating = True
        for it, cb in self._checks:
            cb.IsChecked = False
        self._bulk_updating = False
        self._update_counter()

    def _invert(self, sender, args):
        self._bulk_updating = True
        currently = set(id(cb) for it, cb in self._checks if cb.IsChecked)
        for it, cb in self._checks:
            cb.IsChecked = (id(cb) not in currently)
        self._clamp_to_capacity()
        self._bulk_updating = False
        self._update_counter()

    def _ok(self, sender, args):
        self.selected = self._selected_items()
        if not self.selected:
            forms.alert("Please select at least one image.")
            return
        self.DialogResult = True
        self.Close()

    def _cancel(self, sender, args):
        self.selected = []
        self.DialogResult = False
        self.Close()


def get_image_data_workflow(max_cap):
    root_folder = pick_folder_dialog("Select Main Folder (grid capacity: {} images)".format(max_cap))
    if not root_folder:
        return []
    subdirs = sorted([d for d in os.listdir(root_folder) if os.path.isdir(os.path.join(root_folder, d))])
    if not subdirs:
        forms.alert("No subfolders found in the selected folder.")
        return []
    grouped = {}
    for cat in subdirs:
        cat_path = os.path.join(root_folder, cat)
        files = sorted([f for f in os.listdir(cat_path) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'))])
        if files:
            grouped[cat] = [ImgItem(cat, os.path.join(cat_path, f), f) for f in files]
    if not grouped:
        forms.alert("No images found inside the subfolders.")
        return []
    win = ImageSelectorWindow(grouped, max_cap)
    win.ShowDialog()
    if not win.selected:
        return []
    return [{
        'category': s.category,
        'header': clean_header_name(s.category),
        'path': s.filepath,
        'label': s.label
    } for s in win.selected]


# =============================================================================
# IMAGE TYPE CREATION
# =============================================================================
def _get_image_resource_type():
    try:
        return DB.ExternalResourceTypes.BuiltInExternalResourceTypes.Image
    except Exception:
        pass
    try:
        built = DB.ExternalResourceTypes.BuiltInExternalResourceTypes
        for name in ("Image", "Images", "RasterImage"):
            if hasattr(built, name):
                return getattr(built, name)
    except Exception:
        pass
    return None


def _get_image_type_sources():
    sources = []
    if hasattr(DB, "ImageTypeSource"):
        for name in ("Link", "Import", "Internal"):
            if hasattr(DB.ImageTypeSource, name):
                sources.append(getattr(DB.ImageTypeSource, name))
        if not sources:
            try:
                enum_type = clr.GetClrType(DB.ImageTypeSource)
                for v in System.Enum.GetValues(enum_type):
                    sources.append(v)
            except Exception:
                pass
    return sources


def _make_local_external_ref(filepath):
    filepath = os.path.abspath(str(filepath))
    mp = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(filepath)
    res_type = _get_image_resource_type()
    if res_type is None:
        raise Exception("Could not resolve BuiltInExternalResourceTypes.Image")
    last_ex = None
    for ptype_name in ("Absolute", "Relative"):
        try:
            ptype = getattr(DB.PathType, ptype_name)
            return DB.ExternalResourceReference.CreateLocalResource(doc, res_type, mp, ptype)
        except Exception as ex:
            last_ex = ex
    raise Exception("CreateLocalResource failed: {}".format(last_ex))


def create_image_type(filepath):
    filepath = os.path.abspath(str(filepath))
    if not os.path.isfile(filepath):
        raise Exception("File not found:\n{}".format(filepath))
    errors = []
    sources = _get_image_type_sources()
    try:
        ext_ref = _make_local_external_ref(filepath)
        for src in sources:
            try:
                opts = DB.ImageTypeOptions(ext_ref, src)
                img = DB.ImageType.Create(doc, opts)
                if img:
                    return img
            except Exception as ex:
                errors.append("Options(ExtRef, {}): {}".format(src, ex))
        try:
            opts = DB.ImageTypeOptions(ext_ref)
            img = DB.ImageType.Create(doc, opts)
            if img:
                return img
        except Exception as ex:
            errors.append("Options(ExtRef): {}".format(ex))
        try:
            img = DB.ImageType.Create(doc, ext_ref)
            if img:
                return img
        except Exception as ex:
            errors.append("Create(ExtRef): {}".format(ex))
    except Exception as ex:
        errors.append("Build ExtRef: {}".format(ex))

    sys_path = System.String(filepath)
    for p in (filepath, sys_path):
        for src in sources:
            try:
                opts = DB.ImageTypeOptions(p, src)
                img = DB.ImageType.Create(doc, opts)
                if img:
                    return img
            except Exception as ex:
                errors.append("Options({}, {}): {}".format(type(p).__name__, src, ex))
    try:
        mp = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(filepath)
        for src in sources:
            try:
                opts = DB.ImageTypeOptions(mp, src)
                img = DB.ImageType.Create(doc, opts)
                if img:
                    return img
            except Exception as ex:
                errors.append("Options(ModelPath, {}): {}".format(src, ex))
    except Exception as ex:
        errors.append("ModelPath fallback: {}".format(ex))
    raise Exception("Cannot create ImageType for '{}':\n{}".format(filepath, "\n".join(errors)))


def find_or_create_image_type(filepath):
    filepath = os.path.abspath(str(filepath))
    filename = os.path.basename(filepath)
    for img_type in FilteredElementCollector(doc).OfClass(DB.ImageType):
        try:
            if img_type.Name.lower() == filename.lower():
                return img_type
            p = getattr(img_type, "Path", None)
            if p and str(p).lower() == filepath.lower():
                return img_type
        except Exception:
            pass
    return create_image_type(filepath)


# =============================================================================
# GEOMETRY & PLACEMENT
# =============================================================================
def get_grid_cells_from_edges(x_edges, y_edges, z):
    cells = []
    for r in range(len(y_edges) - 1):
        row_cells = []
        top, bottom = y_edges[r], y_edges[r + 1]
        if bottom > top:
            top, bottom = bottom, top
        for c in range(len(x_edges) - 1):
            left, right = x_edges[c], x_edges[c + 1]
            if right < left:
                left, right = right, left
            row_cells.append({
                'left': left, 'right': right, 'top': top, 'bottom': bottom,
                'cx': (left + right) / 2.0, 'cy': (top + bottom) / 2.0, 'z': z,
                'w': right - left, 'h': top - bottom
            })
        cells.append(row_cells)
    return cells


def get_default_text_type_id():
    t_id = doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType)
    if t_id == ElementId.InvalidElementId:
        types = FilteredElementCollector(doc).OfClass(DB.TextNoteType).ToElements()
        if types:
            return types[0].Id
    return t_id


def place_text(cell, text_string, is_header):
    opt = TextNoteOptions()
    tid = get_default_text_type_id()
    if tid != ElementId.InvalidElementId:
        opt.TypeId = tid
    if is_header:
        opt.HorizontalAlignment = DB.HorizontalTextAlignment.Left
        pt = XYZ(cell['left'] + cell['w'] * 0.05, cell['bottom'] + cell['h'] * 0.20, cell['z'])
    else:
        opt.HorizontalAlignment = DB.HorizontalTextAlignment.Center
        pt = XYZ(cell['cx'], cell['cy'], cell['z'])
    TextNote.Create(doc, view.Id, pt, text_string, opt)


def _recenter_image(img_inst, cx, cy):
    try:
        bb = img_inst.get_BoundingBox(view)
        if not bb:
            return
        cur_cx = (bb.Min.X + bb.Max.X) / 2.0
        cur_cy = (bb.Min.Y + bb.Max.Y) / 2.0
        dx = cx - cur_cx
        dy = cy - cur_cy
        if abs(dx) > 1e-9 or abs(dy) > 1e-9:
            DB.ElementTransformUtils.MoveElement(doc, img_inst.Id, XYZ(dx, dy, 0))
    except Exception:
        pass


def place_image(cell, filepath):
    img_type = find_or_create_image_type(filepath)
    buffer = mm_to_internal(10.0)
    target_w = max(cell['w'] - 2.0 * buffer, 0.01)
    target_h = max(cell['h'] - 2.0 * buffer, 0.01)
    cx, cy, cz = cell['cx'], cell['cy'], cell['z']

    place_opts = DB.ImagePlacementOptions(XYZ(cx, cy, cz), DB.BoxPlacement.Center)
    img_inst = DB.ImageInstance.Create(doc, view, img_type.Id, place_opts)

    p_lock = None
    try:
        p_lock = img_inst.get_Parameter(DB.BuiltInParameter.RASTER_LOCK_PROPORTIONS)
    except Exception:
        pass
    if not p_lock:
        try:
            for p in img_inst.Parameters:
                n = p.Definition.Name.lower() if p.Definition else ""
                if "lock" in n and "proportion" in n:
                    p_lock = p
                    break
        except Exception:
            pass
    if p_lock and not p_lock.IsReadOnly:
        try:
            p_lock.Set(0)
        except Exception:
            pass

    pw = img_inst.get_Parameter(DB.BuiltInParameter.RASTER_SYMBOL_WIDTH)
    ph = img_inst.get_Parameter(DB.BuiltInParameter.RASTER_SYMBOL_HEIGHT)
    if pw and not pw.IsReadOnly:
        try:
            pw.Set(target_w)
        except Exception:
            pass
    if ph and not ph.IsReadOnly:
        try:
            ph.Set(target_h)
        except Exception:
            pass
    try:
        if hasattr(img_inst, "Width"):
            img_inst.Width = target_w
        if hasattr(img_inst, "Height"):
            img_inst.Height = target_h
    except Exception:
        pass

    _recenter_image(img_inst, cx, cy)
    return img_inst


def populate_grid(cells, image_data):
    num_cols = len(cells[0])
    num_blocks = len(cells) // 3
    idx = 0
    last_cat = None
    placed = 0
    failures = []
    for b in range(num_blocks):
        r_head, r_img, r_lbl = b * 3, b * 3 + 1, b * 3 + 2
        for c in range(num_cols):
            if idx >= len(image_data):
                return placed
            item = image_data[idx]
            try:
                if item['category'] != last_cat:
                    header_txt = item.get('header') or clean_header_name(item['category'])
                    place_text(cells[r_head][c], header_txt, True)
                    last_cat = item['category']
                place_image(cells[r_img][c], item['path'])
                place_text(cells[r_lbl][c], item['label'], False)
                placed += 1
            except Exception as ex:
                failures.append("{} -> {}".format(item['path'], ex))
                logger.error("Failed to place image {}: {}".format(item['path'], ex))
            idx += 1
    if failures:
        forms.alert("Some images failed ({}):\n\n{}".format(len(failures), "\n".join(failures[:5])),
                    title="Placement Issues", warn_icon=True)
    return placed


# =============================================================================
# MAIN
# =============================================================================
def main():
    line_style = pick_line_style()
    p1, p2 = pick_two_corners()
    min_x, min_y, max_x, max_y, z = rect_from_points(p1, p2)

    width = max_x - min_x
    height = max_y - min_y
    aspect_ratio = width / height if height else 1.0
    unit_type_id = get_length_unit_type_id()

    xaml_file = os.path.join(os.path.dirname(__file__), "ui.xaml")
    win = DivideWindow(xaml_file, aspect_ratio, width, height, unit_type_id)
    win.ShowDialog()
    if not win.result_ok:
        sys.exit()

    row_strips = win.row_strips
    col_strips = win.col_strips

    x_edges = edges_from_strips(min_x, col_strips, descending=False)
    y_edges = edges_from_strips(max_y, row_strips, descending=True)

    grid_min_x, grid_max_x = min(x_edges), max(x_edges)
    grid_min_y, grid_max_y = min(y_edges), max(y_edges)

    image_data = []
    if win.link_images:
        image_data = get_image_data_workflow(win.max_capacity)
        if not image_data:
            forms.alert("No images selected — creating empty grid only.")

    lines = [
        Line.CreateBound(XYZ(grid_min_x, grid_min_y, z), XYZ(grid_max_x, grid_min_y, z)),
        Line.CreateBound(XYZ(grid_max_x, grid_min_y, z), XYZ(grid_max_x, grid_max_y, z)),
        Line.CreateBound(XYZ(grid_max_x, grid_max_y, z), XYZ(grid_min_x, grid_max_y, z)),
        Line.CreateBound(XYZ(grid_min_x, grid_max_y, z), XYZ(grid_min_x, grid_min_y, z)),
    ]
    for x in x_edges[1:-1]:
        lines.append(Line.CreateBound(XYZ(x, grid_min_y, z), XYZ(x, grid_max_y, z)))
    for y in y_edges[1:-1]:
        lines.append(Line.CreateBound(XYZ(grid_min_x, y, z), XYZ(grid_max_x, y, z)))

    t = Transaction(doc, "Create Grid and Images")
    t.Start()
    try:
        for ln in lines:
            dc = doc.Create.NewDetailCurve(view, ln)
            try:
                dc.LineStyle = line_style
            except Exception:
                pass

        if win.link_images and image_data:
            cells = get_grid_cells_from_edges(x_edges, y_edges, z)
            populate_grid(cells, image_data)

        t.Commit()
        # No success popup — finishes silently
    except Exception as ex:
        t.RollBack()
        forms.alert("Failed:\n{}".format(ex))


if __name__ == "__main__":
    try:
        main()
    except OperationCanceledException:
        pass
    except Exception as e:
        forms.alert("Script error:\n{}\n\n{}".format(e, traceback.format_exc()), title="Error")