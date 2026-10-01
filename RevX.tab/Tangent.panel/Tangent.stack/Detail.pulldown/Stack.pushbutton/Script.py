# -*- coding: utf-8 -*-
"""Stack Detail Components and Filled Regions along a multi-point path.

Features:
- Pick 2+ points forming a polyline (ESC to finish)
- Layers stack downward from the path (Top → Bottom order)
- No gaps between layers
- Local segment alignment (prevents DC stepping/misplacement)
- Save/Load presets, left-to-right auto-correct
- Compatible with all Revit versions
- FILLED REGIONS ALWAYS HAVE VERTICAL START AND END CUTS
"""

__title__ = "Stack"
__author__ = "Jesto Joy"

import clr
import math
import json
import traceback

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
clr.AddReference('mscorlib')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List
from System.Windows.Forms import (
    Form, Label, TextBox, Button, ComboBox, CheckedListBox, DataGridView,
    DataGridViewTextBoxColumn, Panel, GroupBox, MessageBox,
    MessageBoxButtons, MessageBoxIcon, DialogResult,
    FormStartPosition, AnchorStyles, FormBorderStyle,
    DataGridViewAutoSizeColumnMode, DataGridViewAutoSizeColumnsMode,
    DataGridViewSelectionMode, FlatStyle, CheckBox, NumericUpDown
)
from System.Drawing import Size, Point, Color, Font, FontStyle
import System
import System.IO
import System.Text

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
app = __revit__.Application


# ============================================================
# PATHS / HELPERS
# ============================================================

appdata_path = System.Environment.GetFolderPath(
    System.Environment.SpecialFolder.ApplicationData)
PRESET_FOLDER = System.IO.Path.Combine(
    appdata_path, "pyRevit", "DetailStackerPresets")

if not System.IO.Directory.Exists(PRESET_FOLDER):
    try:
        System.IO.Directory.CreateDirectory(PRESET_FOLDER)
    except:
        pass


def safe_str(value):
    if value is None:
        return ""
    try:
        if isinstance(value, unicode):
            return value.encode('utf-8')
        return str(value)
    except:
        try:
            return str(value)
        except:
            return ""


def mm_to_feet(mm_val):
    return mm_val / 304.8


def feet_to_mm(feet_val):
    return feet_val * 304.8


# ============================================================
# DATA COLLECTION
# ============================================================

def get_filled_region_types():
    collector = FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements()
    result = {}
    for fr in collector:
        try:
            param = fr.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
            name = param.AsString() if (param and param.AsString()) else Element.Name.GetValue(fr)
        except:
            try:
                name = Element.Name.GetValue(fr)
            except:
                name = "Unknown FR {}".format(fr.Id.IntegerValue)
        if name:
            result[safe_str(name)] = fr.Id
    return result


def get_detail_component_types():
    collector = (FilteredElementCollector(doc)
                 .OfCategory(BuiltInCategory.OST_DetailComponents)
                 .OfClass(FamilySymbol).ToElements())
    result = {}
    for dc in collector:
        try:
            full_name = "{} : {}".format(dc.Family.Name, Element.Name.GetValue(dc))
        except:
            full_name = "Unknown DC {}".format(dc.Id.IntegerValue)
        result[safe_str(full_name)] = dc.Id
    return result


# ============================================================
# PRESETS
# ============================================================

def save_preset(name, stack_items):
    data = [{'name': safe_str(i['name']),
             'type': safe_str(i['type']),
             'height_mm': float(i['height_mm'])} for i in stack_items]
    safe_name = "".join(c for c in name if c not in r'<>:"/\|?*')
    filepath = System.IO.Path.Combine(PRESET_FOLDER, safe_name + '.json')
    try:
        json_str = json.dumps(data, indent=2, ensure_ascii=True)
        System.IO.File.WriteAllText(
            filepath, System.String(json_str), System.Text.Encoding.UTF8)
        return True
    except Exception as ex:
        MessageBox.Show("Failed to save preset:\n{}".format(str(ex)),
                        "Save Error", MessageBoxButtons.OK, MessageBoxIcon.Error)
        return False


def load_preset(filepath):
    try:
        net_str = System.IO.File.ReadAllText(filepath, System.Text.Encoding.UTF8)
        data = json.loads(safe_str(net_str))
        return [{'name': safe_str(e.get('name', '')),
                 'type': safe_str(e.get('type', '')),
                 'height_mm': float(e.get('height_mm', 100))} for e in data]
    except Exception as ex:
        MessageBox.Show("Failed to load preset:\n{}".format(str(ex)),
                        "Load Error", MessageBoxButtons.OK, MessageBoxIcon.Error)
        return None


def get_saved_presets():
    presets = []
    if System.IO.Directory.Exists(PRESET_FOLDER):
        try:
            for f in System.IO.Directory.GetFiles(PRESET_FOLDER, "*.json"):
                presets.append(
                    safe_str(System.IO.Path.GetFileNameWithoutExtension(f)))
        except:
            pass
    return sorted(presets)


def delete_preset(name):
    filepath = System.IO.Path.Combine(PRESET_FOLDER, name + '.json')
    try:
        if System.IO.File.Exists(filepath):
            System.IO.File.Delete(filepath)
            return True
    except:
        pass
    return False


# ============================================================
# UI FORM
# ============================================================

class StackerForm(Form):
    def __init__(self):
        self.filled_regions = get_filled_region_types()
        self.detail_components = get_detail_component_types()
        self.stack_items = []
        self.result = None
        self._current_items = []
        self._setup_form()
        self._create_controls()

    def _setup_form(self):
        self.Text = "Detail Stacker - Layer Builder"
        self.Size = Size(950, 820)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize = Size(850, 750)
        self.BackColor = Color.FromArgb(245, 245, 250)
        self.Font = Font("Segoe UI", 9)

    def _create_controls(self):
        header = Label()
        header.Text = "DETAIL STACKER"
        header.Font = Font("Segoe UI", 16, FontStyle.Bold)
        header.ForeColor = Color.FromArgb(50, 50, 120)
        header.Location = Point(20, 10)
        header.Size = Size(400, 35)
        self.Controls.Add(header)

        subtitle = Label()
        subtitle.Text = ("Multi-point path  |  Top-down stack  |  "
                         "No gaps  |  Save & Reuse Presets")
        subtitle.Font = Font("Segoe UI", 8)
        subtitle.ForeColor = Color.Gray
        subtitle.Location = Point(20, 42)
        subtitle.Size = Size(700, 20)
        self.Controls.Add(subtitle)

        # ---- PRESETS ----
        preset_group = GroupBox()
        preset_group.Text = "Presets"
        preset_group.Location = Point(15, 65)
        preset_group.Size = Size(905, 55)
        preset_group.Font = Font("Segoe UI", 9, FontStyle.Bold)
        preset_group.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        self.Controls.Add(preset_group)

        preset_label = Label()
        preset_label.Text = "Saved:"
        preset_label.Location = Point(10, 22)
        preset_label.Size = Size(45, 22)
        preset_label.Font = Font("Segoe UI", 9)
        preset_group.Controls.Add(preset_label)

        self.preset_combo = ComboBox()
        self.preset_combo.Location = Point(58, 20)
        self.preset_combo.Size = Size(250, 25)
        self.preset_combo.Font = Font("Segoe UI", 9)
        self.preset_combo.DropDownStyle = System.Windows.Forms.ComboBoxStyle.DropDownList
        preset_group.Controls.Add(self.preset_combo)

        self.btn_load_preset = Button()
        self.btn_load_preset.Text = "Load"
        self.btn_load_preset.Location = Point(315, 19)
        self.btn_load_preset.Size = Size(60, 26)
        self.btn_load_preset.Font = Font("Segoe UI", 8, FontStyle.Bold)
        self.btn_load_preset.BackColor = Color.FromArgb(100, 160, 220)
        self.btn_load_preset.ForeColor = Color.White
        self.btn_load_preset.FlatStyle = FlatStyle.Flat
        self.btn_load_preset.Click += self._on_load_preset
        preset_group.Controls.Add(self.btn_load_preset)

        self.btn_delete_preset = Button()
        self.btn_delete_preset.Text = "Delete"
        self.btn_delete_preset.Location = Point(380, 19)
        self.btn_delete_preset.Size = Size(60, 26)
        self.btn_delete_preset.Font = Font("Segoe UI", 8)
        self.btn_delete_preset.BackColor = Color.FromArgb(200, 120, 120)
        self.btn_delete_preset.ForeColor = Color.White
        self.btn_delete_preset.FlatStyle = FlatStyle.Flat
        self.btn_delete_preset.Click += self._on_delete_preset
        preset_group.Controls.Add(self.btn_delete_preset)

        save_label = Label()
        save_label.Text = "Name:"
        save_label.Location = Point(470, 22)
        save_label.Size = Size(45, 22)
        save_label.Font = Font("Segoe UI", 9)
        preset_group.Controls.Add(save_label)

        self.preset_name_box = TextBox()
        self.preset_name_box.Location = Point(518, 20)
        self.preset_name_box.Size = Size(250, 25)
        self.preset_name_box.Font = Font("Segoe UI", 9)
        preset_group.Controls.Add(self.preset_name_box)

        self.btn_save_preset = Button()
        self.btn_save_preset.Text = "Save"
        self.btn_save_preset.Location = Point(775, 19)
        self.btn_save_preset.Size = Size(60, 26)
        self.btn_save_preset.Font = Font("Segoe UI", 8, FontStyle.Bold)
        self.btn_save_preset.BackColor = Color.FromArgb(80, 160, 100)
        self.btn_save_preset.ForeColor = Color.White
        self.btn_save_preset.FlatStyle = FlatStyle.Flat
        self.btn_save_preset.Click += self._on_save_preset
        preset_group.Controls.Add(self.btn_save_preset)

        self.btn_open_folder = Button()
        self.btn_open_folder.Text = "Folder"
        self.btn_open_folder.Location = Point(840, 19)
        self.btn_open_folder.Size = Size(55, 26)
        self.btn_open_folder.Font = Font("Segoe UI", 7)
        self.btn_open_folder.BackColor = Color.FromArgb(160, 160, 180)
        self.btn_open_folder.ForeColor = Color.White
        self.btn_open_folder.FlatStyle = FlatStyle.Flat
        self.btn_open_folder.Click += self._on_open_folder
        preset_group.Controls.Add(self.btn_open_folder)

        # ---- LEFT ----
        left_group = GroupBox()
        left_group.Text = "Available Items"
        left_group.Location = Point(15, 130)
        left_group.Size = Size(420, 400)
        left_group.Font = Font("Segoe UI", 9, FontStyle.Bold)
        left_group.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Bottom
        self.Controls.Add(left_group)

        search_label = Label()
        search_label.Text = "Search:"
        search_label.Location = Point(10, 25)
        search_label.Size = Size(50, 22)
        search_label.Font = Font("Segoe UI", 9)
        left_group.Controls.Add(search_label)

        self.search_box = TextBox()
        self.search_box.Location = Point(65, 23)
        self.search_box.Size = Size(340, 25)
        self.search_box.Font = Font("Segoe UI", 9)
        self.search_box.TextChanged += self._on_search_changed
        left_group.Controls.Add(self.search_box)

        self.chk_show_fr = CheckBox()
        self.chk_show_fr.Text = "Filled Regions"
        self.chk_show_fr.Location = Point(65, 52)
        self.chk_show_fr.Size = Size(130, 22)
        self.chk_show_fr.Checked = True
        self.chk_show_fr.Font = Font("Segoe UI", 8)
        self.chk_show_fr.CheckedChanged += self._on_filter_changed
        left_group.Controls.Add(self.chk_show_fr)

        self.chk_show_dc = CheckBox()
        self.chk_show_dc.Text = "Detail Components"
        self.chk_show_dc.Location = Point(200, 52)
        self.chk_show_dc.Size = Size(150, 22)
        self.chk_show_dc.Checked = True
        self.chk_show_dc.Font = Font("Segoe UI", 8)
        self.chk_show_dc.CheckedChanged += self._on_filter_changed
        left_group.Controls.Add(self.chk_show_dc)

        self.items_list = CheckedListBox()
        self.items_list.Location = Point(10, 80)
        self.items_list.Size = Size(395, 230)
        self.items_list.Font = Font("Segoe UI", 9)
        self.items_list.CheckOnClick = True
        self.items_list.Anchor = (AnchorStyles.Top | AnchorStyles.Left |
                                  AnchorStyles.Bottom)
        left_group.Controls.Add(self.items_list)

        height_label = Label()
        height_label.Text = "Default Height (mm) for new FR:"
        height_label.Location = Point(10, 325)
        height_label.Size = Size(200, 22)
        height_label.Font = Font("Segoe UI", 9)
        height_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        left_group.Controls.Add(height_label)

        self.height_input = NumericUpDown()
        self.height_input.Location = Point(215, 323)
        self.height_input.Size = Size(80, 25)
        self.height_input.Minimum = System.Decimal(0)
        self.height_input.Maximum = System.Decimal(100000)
        self.height_input.Value = System.Decimal(100)
        self.height_input.DecimalPlaces = 1
        self.height_input.Font = Font("Segoe UI", 9)
        self.height_input.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        left_group.Controls.Add(self.height_input)

        self.btn_add = Button()
        self.btn_add.Text = "Add Selected  >>>"
        self.btn_add.Location = Point(10, 358)
        self.btn_add.Size = Size(395, 32)
        self.btn_add.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self.btn_add.BackColor = Color.FromArgb(70, 130, 180)
        self.btn_add.ForeColor = Color.White
        self.btn_add.FlatStyle = FlatStyle.Flat
        self.btn_add.Click += self._on_add_clicked
        self.btn_add.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        left_group.Controls.Add(self.btn_add)

        # ---- RIGHT ----
        right_group = GroupBox()
        right_group.Text = "Stack Order (Top to Bottom)  -  Edit H(mm) to override Auto"
        right_group.Location = Point(450, 130)
        right_group.Size = Size(480, 400)
        right_group.Font = Font("Segoe UI", 9, FontStyle.Bold)
        right_group.Anchor = (AnchorStyles.Top | AnchorStyles.Left |
                              AnchorStyles.Right | AnchorStyles.Bottom)
        self.Controls.Add(right_group)

        self.stack_grid = DataGridView()
        self.stack_grid.Location = Point(10, 25)
        self.stack_grid.Size = Size(390, 330)
        self.stack_grid.Font = Font("Segoe UI", 9)
        self.stack_grid.AllowUserToAddRows = False
        self.stack_grid.AllowUserToDeleteRows = False
        self.stack_grid.ReadOnly = False
        self.stack_grid.SelectionMode = DataGridViewSelectionMode.FullRowSelect
        self.stack_grid.MultiSelect = False
        self.stack_grid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill
        self.stack_grid.RowHeadersVisible = False
        self.stack_grid.BackgroundColor = Color.White
        self.stack_grid.Anchor = (AnchorStyles.Top | AnchorStyles.Left |
                                   AnchorStyles.Right | AnchorStyles.Bottom)

        col_order = DataGridViewTextBoxColumn()
        col_order.HeaderText = "#"
        col_order.Width = 30
        col_order.ReadOnly = True
        col_order.MinimumWidth = 30
        col_order.AutoSizeMode = DataGridViewAutoSizeColumnMode.None
        self.stack_grid.Columns.Add(col_order)

        col_name = DataGridViewTextBoxColumn()
        col_name.HeaderText = "Item Name"
        col_name.ReadOnly = True
        col_name.AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill
        self.stack_grid.Columns.Add(col_name)

        col_type = DataGridViewTextBoxColumn()
        col_type.HeaderText = "Type"
        col_type.Width = 50
        col_type.ReadOnly = True
        col_type.MinimumWidth = 45
        col_type.AutoSizeMode = DataGridViewAutoSizeColumnMode.None
        self.stack_grid.Columns.Add(col_type)

        col_height = DataGridViewTextBoxColumn()
        col_height.HeaderText = "H(mm)"
        col_height.Width = 65
        col_height.ReadOnly = False
        col_height.MinimumWidth = 55
        col_height.AutoSizeMode = DataGridViewAutoSizeColumnMode.None
        self.stack_grid.Columns.Add(col_height)

        self.stack_grid.CellEndEdit += self._on_cell_edit
        right_group.Controls.Add(self.stack_grid)

        btn_panel = Panel()
        btn_panel.Location = Point(405, 25)
        btn_panel.Size = Size(65, 330)
        btn_panel.Anchor = AnchorStyles.Top | AnchorStyles.Right | AnchorStyles.Bottom
        right_group.Controls.Add(btn_panel)

        self.btn_up = Button()
        self.btn_up.Text = "UP"
        self.btn_up.Location = Point(0, 10)
        self.btn_up.Size = Size(60, 32)
        self.btn_up.Font = Font("Segoe UI", 9, FontStyle.Bold)
        self.btn_up.BackColor = Color.FromArgb(200, 200, 220)
        self.btn_up.FlatStyle = FlatStyle.Flat
        self.btn_up.Click += self._on_move_up
        btn_panel.Controls.Add(self.btn_up)

        self.btn_down = Button()
        self.btn_down.Text = "DN"
        self.btn_down.Location = Point(0, 50)
        self.btn_down.Size = Size(60, 32)
        self.btn_down.Font = Font("Segoe UI", 9, FontStyle.Bold)
        self.btn_down.BackColor = Color.FromArgb(200, 200, 220)
        self.btn_down.FlatStyle = FlatStyle.Flat
        self.btn_down.Click += self._on_move_down
        btn_panel.Controls.Add(self.btn_down)

        self.btn_duplicate = Button()
        self.btn_duplicate.Text = "Copy"
        self.btn_duplicate.Location = Point(0, 100)
        self.btn_duplicate.Size = Size(60, 28)
        self.btn_duplicate.Font = Font("Segoe UI", 8)
        self.btn_duplicate.BackColor = Color.FromArgb(180, 210, 180)
        self.btn_duplicate.FlatStyle = FlatStyle.Flat
        self.btn_duplicate.Click += self._on_duplicate
        btn_panel.Controls.Add(self.btn_duplicate)

        self.btn_remove = Button()
        self.btn_remove.Text = "Del"
        self.btn_remove.Location = Point(0, 136)
        self.btn_remove.Size = Size(60, 28)
        self.btn_remove.Font = Font("Segoe UI", 8)
        self.btn_remove.BackColor = Color.FromArgb(220, 150, 150)
        self.btn_remove.FlatStyle = FlatStyle.Flat
        self.btn_remove.Click += self._on_remove
        btn_panel.Controls.Add(self.btn_remove)

        self.btn_clear = Button()
        self.btn_clear.Text = "Clear"
        self.btn_clear.Location = Point(0, 172)
        self.btn_clear.Size = Size(60, 28)
        self.btn_clear.Font = Font("Segoe UI", 8)
        self.btn_clear.BackColor = Color.FromArgb(220, 180, 150)
        self.btn_clear.FlatStyle = FlatStyle.Flat
        self.btn_clear.Click += self._on_clear_all
        btn_panel.Controls.Add(self.btn_clear)

        self.total_label = Label()
        self.total_label.Text = "Total Height: 0 mm"
        self.total_label.Location = Point(10, 365)
        self.total_label.Size = Size(390, 25)
        self.total_label.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self.total_label.ForeColor = Color.FromArgb(50, 50, 120)
        self.total_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        right_group.Controls.Add(self.total_label)

        # ---- BOTTOM ----
        bottom_panel = Panel()
        bottom_panel.Location = Point(15, 540)
        bottom_panel.Size = Size(910, 55)
        bottom_panel.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        self.Controls.Add(bottom_panel)

        info_label = Label()
        info_label.Text = (
            "Placement: Pick multiple points for the TOP path (2, 3, 4…).\n"
            "Press ESC when done. Layers stack downward with NO GAPS along the whole path."
        )
        info_label.Location = Point(0, 0)
        info_label.Size = Size(580, 50)
        info_label.Font = Font("Segoe UI", 7.5)
        info_label.ForeColor = Color.Gray
        bottom_panel.Controls.Add(info_label)

        self.btn_place = Button()
        self.btn_place.Text = "PLACE STACK"
        self.btn_place.Location = Point(680, 2)
        self.btn_place.Size = Size(220, 48)
        self.btn_place.Font = Font("Segoe UI", 13, FontStyle.Bold)
        self.btn_place.BackColor = Color.FromArgb(50, 150, 80)
        self.btn_place.ForeColor = Color.White
        self.btn_place.FlatStyle = FlatStyle.Flat
        self.btn_place.Anchor = AnchorStyles.Right | AnchorStyles.Bottom
        self.btn_place.Click += self._on_place_clicked
        bottom_panel.Controls.Add(self.btn_place)

        self.btn_cancel = Button()
        self.btn_cancel.Text = "Cancel"
        self.btn_cancel.Location = Point(590, 10)
        self.btn_cancel.Size = Size(80, 32)
        self.btn_cancel.Font = Font("Segoe UI", 9)
        self.btn_cancel.FlatStyle = FlatStyle.Flat
        self.btn_cancel.Click += self._on_cancel
        bottom_panel.Controls.Add(self.btn_cancel)

        self._populate_items_list()
        self._refresh_preset_list()

    # ---- PRESET HANDLERS ----
    def _refresh_preset_list(self):
        self.preset_combo.Items.Clear()
        for name in get_saved_presets():
            self.preset_combo.Items.Add(name)
        if self.preset_combo.Items.Count > 0:
            self.preset_combo.SelectedIndex = 0

    def _on_open_folder(self, sender, args):
        try:
            System.Diagnostics.Process.Start("explorer.exe", PRESET_FOLDER)
        except Exception as ex:
            MessageBox.Show("Cannot open folder:\n{}".format(str(ex)),
                            "Info", MessageBoxButtons.OK, MessageBoxIcon.Information)

    def _on_save_preset(self, sender, args):
        name = self.preset_name_box.Text.strip()
        if not name:
            MessageBox.Show("Enter a preset name.", "No Name",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        if not self.stack_items:
            MessageBox.Show("Stack is empty.", "Empty",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        if name in get_saved_presets():
            res = MessageBox.Show("Overwrite preset '{}'?".format(name),
                                  "Confirm", MessageBoxButtons.YesNo,
                                  MessageBoxIcon.Question)
            if res != DialogResult.Yes:
                return
        if save_preset(name, self.stack_items):
            MessageBox.Show("Preset '{}' saved!".format(name), "Saved",
                            MessageBoxButtons.OK, MessageBoxIcon.Information)
            self._refresh_preset_list()

    def _on_load_preset(self, sender, args):
        if self.preset_combo.SelectedIndex < 0:
            MessageBox.Show("Select a preset.", "No Selection",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        name = safe_str(self.preset_combo.SelectedItem.ToString())
        filepath = System.IO.Path.Combine(PRESET_FOLDER, name + '.json')
        data = load_preset(filepath)
        if data is None:
            return
        self.stack_items = []
        not_found, found = [], 0
        for entry in data:
            en = safe_str(entry.get('name', ''))
            et = safe_str(entry.get('type', ''))
            eh = float(entry.get('height_mm', 100))
            eid = None
            src = self.filled_regions if et == 'FR' else self.detail_components
            eid = src.get(en)
            if eid is None:
                for k, v in src.items():
                    if safe_str(k).lower() == en.lower():
                        eid = v
                        break
            if eid is not None:
                self.stack_items.append({
                    'name': en, 'type': et,
                    'element_id': eid, 'height_mm': eh})
                found += 1
            else:
                not_found.append("  - [{}] {}".format(et, en))
        self._refresh_stack_grid()
        if not_found and found == 0:
            MessageBox.Show("No items found in this project.\n" +
                            "\n".join(not_found[:15]),
                            "Load Failed", MessageBoxButtons.OK, MessageBoxIcon.Error)
        elif not_found:
            MessageBox.Show("Loaded {}/{}.\nMissing:\n{}".format(
                found, len(data), "\n".join(not_found)),
                "Partial", MessageBoxButtons.OK, MessageBoxIcon.Warning)
        else:
            MessageBox.Show("Loaded {} items.".format(found), "Loaded",
                            MessageBoxButtons.OK, MessageBoxIcon.Information)

    def _on_delete_preset(self, sender, args):
        if self.preset_combo.SelectedIndex < 0:
            return
        name = safe_str(self.preset_combo.SelectedItem.ToString())
        if MessageBox.Show("Delete '{}'?".format(name), "Confirm",
                           MessageBoxButtons.YesNo,
                           MessageBoxIcon.Question) == DialogResult.Yes:
            delete_preset(name)
            self._refresh_preset_list()

    def _populate_items_list(self):
        self.items_list.Items.Clear()
        search = self.search_box.Text.lower().strip()
        self._current_items = []
        if self.chk_show_fr.Checked:
            for name in sorted(self.filled_regions.keys()):
                if search and search not in name.lower():
                    continue
                self.items_list.Items.Add("[FR] " + name)
                self._current_items.append({
                    'name': name, 'type': 'FR',
                    'element_id': self.filled_regions[name]})
        if self.chk_show_dc.Checked:
            for name in sorted(self.detail_components.keys()):
                if search and search not in name.lower():
                    continue
                self.items_list.Items.Add("[DC] " + name)
                self._current_items.append({
                    'name': name, 'type': 'DC',
                    'element_id': self.detail_components[name]})

    def _on_search_changed(self, s, a):
        self._populate_items_list()

    def _on_filter_changed(self, s, a):
        self._populate_items_list()

    def _on_add_clicked(self, s, a):
        checked = self.items_list.CheckedIndices
        if checked.Count == 0:
            MessageBox.Show("Check at least one item.", "No Selection",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        for i in range(checked.Count):
            item = self._current_items[checked[i]]
            h = float(self.height_input.Value) if item['type'] == 'FR' else 0.0
            self.stack_items.append({
                'name': item['name'], 'type': item['type'],
                'element_id': item['element_id'], 'height_mm': h})
        for i in range(self.items_list.Items.Count):
            self.items_list.SetItemChecked(i, False)
        self._refresh_stack_grid()

    def _refresh_stack_grid(self):
        self.stack_grid.Rows.Clear()
        total = 0.0
        for i, item in enumerate(self.stack_items):
            r = self.stack_grid.Rows[self.stack_grid.Rows.Add()]
            r.Cells[0].Value = str(i + 1)
            r.Cells[1].Value = item['name']
            r.Cells[2].Value = item['type']
            if item['type'] == 'FR':
                r.Cells[3].Value = str(item['height_mm'])
                total += item['height_mm']
            else:
                if item['height_mm'] and item['height_mm'] > 0:
                    r.Cells[3].Value = str(item['height_mm'])
                    total += item['height_mm']
                else:
                    r.Cells[3].Value = "Auto"
                r.Cells[3].ReadOnly = False
        self.total_label.Text = "Total Height (known): {:.1f} mm".format(total)

    def _on_cell_edit(self, s, args):
        if args.ColumnIndex == 3 and args.RowIndex < len(self.stack_items):
            try:
                raw = safe_str(self.stack_grid.Rows[args.RowIndex].Cells[3].Value)
                raw = raw.strip().lower()
                if raw in ("", "auto"):
                    self.stack_items[args.RowIndex]['height_mm'] = 0.0
                else:
                    val = float(raw)
                    if val >= 0:
                        self.stack_items[args.RowIndex]['height_mm'] = val
            except:
                pass
            self._refresh_stack_grid()

    def _on_move_up(self, s, a):
        if self.stack_grid.SelectedRows.Count == 0:
            return
        idx = self.stack_grid.SelectedRows[0].Index
        if idx > 0:
            self.stack_items[idx], self.stack_items[idx - 1] = \
                self.stack_items[idx - 1], self.stack_items[idx]
            self._refresh_stack_grid()
            self.stack_grid.Rows[idx - 1].Selected = True

    def _on_move_down(self, s, a):
        if self.stack_grid.SelectedRows.Count == 0:
            return
        idx = self.stack_grid.SelectedRows[0].Index
        if idx < len(self.stack_items) - 1:
            self.stack_items[idx], self.stack_items[idx + 1] = \
                self.stack_items[idx + 1], self.stack_items[idx]
            self._refresh_stack_grid()
            self.stack_grid.Rows[idx + 1].Selected = True

    def _on_duplicate(self, s, a):
        if self.stack_grid.SelectedRows.Count == 0:
            return
        idx = self.stack_grid.SelectedRows[0].Index
        self.stack_items.insert(idx + 1, dict(self.stack_items[idx]))
        self._refresh_stack_grid()

    def _on_remove(self, s, a):
        if self.stack_grid.SelectedRows.Count == 0:
            return
        idx = self.stack_grid.SelectedRows[0].Index
        self.stack_items.pop(idx)
        self._refresh_stack_grid()

    def _on_clear_all(self, s, a):
        if MessageBox.Show("Clear all?", "Confirm",
                           MessageBoxButtons.YesNo,
                           MessageBoxIcon.Question) == DialogResult.Yes:
            self.stack_items = []
            self._refresh_stack_grid()

    def _on_place_clicked(self, s, a):
        if not self.stack_items:
            MessageBox.Show("Add at least one item.", "Empty",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        for item in self.stack_items:
            if item['type'] == 'FR' and item['height_mm'] <= 0:
                MessageBox.Show(
                    "FR '{}' needs positive height.".format(item['name']),
                    "Invalid", MessageBoxButtons.OK, MessageBoxIcon.Warning)
                return
        self.result = list(self.stack_items)
        self.DialogResult = DialogResult.OK
        self.Close()

    def _on_cancel(self, s, a):
        self.result = None
        self.DialogResult = DialogResult.Cancel
        self.Close()


# ============================================================
# VECTOR HELPERS
# ============================================================

TOL = 1e-7


def _vlen(v):
    return math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z)


def _vunit(v):
    L = _vlen(v)
    if L < 1e-12:
        return XYZ(0, 0, 0)
    return XYZ(v.X / L, v.Y / L, v.Z / L)


def _vsub(a, b):
    return XYZ(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def _vadd(a, b):
    return XYZ(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def _vscale(v, s):
    return XYZ(v.X * s, v.Y * s, v.Z * s)


def _vdot(a, b):
    return a.X * b.X + a.Y * b.Y + a.Z * b.Z


def _clean_pts(pts, tol=TOL):
    """Remove consecutive near-duplicates."""
    if not pts:
        return []
    out = [pts[0]]
    for p in pts[1:]:
        if _vlen(_vsub(p, out[-1])) > tol:
            out.append(p)
    return out


def ensure_path_left_to_right(pts):
    if len(pts) < 2:
        return pts
    dx = pts[-1].X - pts[0].X
    if abs(dx) < 1e-9:
        if pts[-1].Y < pts[0].Y:
            return list(reversed(pts))
        return list(pts)
    if dx < 0:
        return list(reversed(pts))
    return list(pts)


def segment_dir(p0, p1):
    return _vunit(_vsub(p1, p0))


def segment_perp_down(p0, p1):
    """Clockwise 90° from segment dir = downward for an L→R path."""
    d = segment_dir(p0, p1)
    return XYZ(d.Y, -d.X, 0.0)


def project_to_view_plane(pt, view):
    """Project a picked point onto the view's work plane."""
    try:
        origin = view.Origin
        normal = view.ViewDirection
        v = _vsub(pt, origin)
        dist = _vdot(v, normal)
        return XYZ(pt.X - normal.X * dist,
                   pt.Y - normal.Y * dist,
                   pt.Z - normal.Z * dist)
    except:
        return XYZ(pt.X, pt.Y, pt.Z)


# ============================================================
# OFFSET + LOOP BUILDING
# ============================================================

def offset_polyline_miter(pts, distance, miter_limit=4.0):
    """Standard perpendicular offset (used only for Detail Components)."""
    pts = _clean_pts(pts)
    n = len(pts)
    if n < 2:
        return list(pts)

    norms = []
    dirs = []
    for i in range(n - 1):
        d = segment_dir(pts[i], pts[i + 1])
        dirs.append(d)
        norms.append(XYZ(d.Y, -d.X, 0.0))

    out = []
    for i in range(n):
        if i == 0:
            out.append(_vadd(pts[0], _vscale(norms[0], distance)))
        elif i == n - 1:
            out.append(_vadd(pts[n - 1], _vscale(norms[n - 2], distance)))
        else:
            n0 = norms[i - 1]
            n1 = norms[i]
            avg = _vadd(n0, n1)
            avg_len = _vlen(avg)
            if avg_len < 1e-8:
                out.append(_vadd(pts[i], _vscale(n0, distance)))
                continue
            miter = _vscale(avg, 1.0 / avg_len)
            denom = _vdot(miter, n0)
            if abs(denom) < 1e-6:
                out.append(_vadd(pts[i], _vscale(n0, distance)))
                continue
            miter_dist = distance / denom
            max_miter = abs(distance) * miter_limit
            if abs(miter_dist) > max_miter:
                miter_dist = math.copysign(max_miter, miter_dist)
            out.append(_vadd(pts[i], _vscale(miter, miter_dist)))
    return out


def offset_polyline_miter_vertical_cuts(pts, distance, view, miter_limit=4.0):
    """Offsets polyline while ensuring start and end boundaries project strictly vertically."""
    pts = _clean_pts(pts)
    n = len(pts)
    if n < 2:
        return list(pts)

    right = view.RightDirection
    up = view.UpDirection
    origin = view.Origin

    def to_2d(p):
        v = _vsub(p, origin)
        return (_vdot(v, right), _vdot(v, up))

    def to_3d(u, v):
        return _vadd(origin, _vadd(_vscale(right, u), _vscale(up, v)))

    pts2d = [to_2d(p) for p in pts]

    dirs = []
    norms = []
    for i in range(n - 1):
        p0 = pts2d[i]
        p1 = pts2d[i + 1]
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        L = math.sqrt(dx*dx + dy*dy)
        if L < 1e-12:
            d = (1.0, 0.0)
        else:
            d = (dx/L, dy/L)
        dirs.append(d)
        norms.append((d[1], -d[0])) # Clockwise local normal

    out2d = []
    for i in range(n):
        if i == 0:
            # Start Point: Compute offset projected along vertical axis
            p0 = pts2d[0]
            p1 = pts2d[1]
            dx = p1[0] - p0[0]
            dy = p1[1] - p0[1]
            L = math.sqrt(dx*dx + dy*dy)
            if L > 1e-12 and abs(dx) > 1e-4:
                out2d.append((p0[0], p0[1] - distance * L / dx))
            else:
                n0 = norms[0]
                out2d.append((p0[0] + n0[0] * distance, p0[1] + n0[1] * distance))
        elif i == n - 1:
            # End Point: Compute offset projected along vertical axis
            p_penult = pts2d[n - 2]
            p_last = pts2d[n - 1]
            dx = p_last[0] - p_penult[0]
            dy = p_last[1] - p_penult[1]
            L = math.sqrt(dx*dx + dy*dy)
            if L > 1e-12 and abs(dx) > 1e-4:
                out2d.append((p_last[0], p_last[1] - distance * L / dx))
            else:
                n_last = norms[n - 2]
                out2d.append((p_last[0] + n_last[0] * distance, p_last[1] + n_last[1] * distance))
        else:
            # Intermediate Points: Normal clean miter join
            n0 = norms[i - 1]
            n1 = norms[i]
            avg_x = n0[0] + n1[0]
            avg_y = n0[1] + n1[1]
            avg_len = math.sqrt(avg_x*avg_x + avg_y*avg_y)
            if avg_len < 1e-8:
                out2d.append((pts2d[i][0] + n0[0] * distance, pts2d[i][1] + n0[1] * distance))
                continue
            miter = (avg_x / avg_len, avg_y / avg_len)
            denom = miter[0] * n0[0] + miter[1] * n0[1]
            if abs(denom) < 1e-6:
                out2d.append((pts2d[i][0] + n0[0] * distance, pts2d[i][1] + n0[1] * distance))
                continue
            miter_dist = distance / denom
            max_miter = abs(distance) * miter_limit
            if abs(miter_dist) > max_miter:
                miter_dist = math.copysign(max_miter, miter_dist)
            out2d.append((pts2d[i][0] + miter[0] * miter_dist, pts2d[i][1] + miter[1] * miter_dist))

    return [to_3d(p[0], p[1]) for p in out2d]


def make_closed_loop_from_ring(ring_pts):
    """Build a CurveLoop from a ring of points."""
    clean = _clean_pts(ring_pts, tol=1e-7)
    if len(clean) > 2 and _vlen(_vsub(clean[-1], clean[0])) <= 1e-7:
        clean = clean[:-1]
    if len(clean) < 3:
        raise ValueError("Degenerate polygon")

    loop = CurveLoop()
    count = len(clean)
    for i in range(count):
        p0 = clean[i]
        p1 = clean[(i + 1) % count]
        if _vlen(_vsub(p1, p0)) <= 1e-9:
            raise ValueError("Zero-length edge in ring")
        loop.Append(Line.CreateBound(p0, p1))
    return loop


def make_strip_loop(top_pts, bottom_pts):
    top = _clean_pts(top_pts)
    bot = _clean_pts(bottom_pts)
    if len(top) < 2 or len(bot) < 2:
        raise ValueError("Strip needs at least 2 points")

    ring = []
    ring.extend(top)
    ring.extend(reversed(bot))
    return make_closed_loop_from_ring(ring)


def create_fr_strip(fr_type_id, view, path_pts, top_offset, height_feet):
    """
    Create one FR layer:
    - top boundary = path offset by top_offset
    - bottom boundary = path offset by top_offset + height
    Both use vertical-cut ends so start/end edges are always vertical.
    """
    path_pts = _clean_pts(path_pts)
    if len(path_pts) < 2 or height_feet <= 1e-12:
        return []

    try:
        top_pts = offset_polyline_miter_vertical_cuts(path_pts, top_offset, view)
        bottom_pts = offset_polyline_miter_vertical_cuts(
            path_pts, top_offset + height_feet, view)
    except Exception:
        return []

    top_pts = _clean_pts(top_pts)
    bottom_pts = _clean_pts(bottom_pts)
    if len(top_pts) < 2 or len(bottom_pts) < 2:
        return []

    # Prefer one continuous strip
    try:
        loop = make_strip_loop(top_pts, bottom_pts)
        boundaries = List[CurveLoop]()
        boundaries.Add(loop)
        fr = FilledRegion.Create(doc, fr_type_id, view.Id, boundaries)
        return [fr]
    except Exception:
        pass

    # Fallback: per-segment quads (still vertical ends, no gaps)
    regions = []
    n = min(len(top_pts), len(bottom_pts))
    for i in range(n - 1):
        try:
            a, b = top_pts[i], top_pts[i + 1]
            a2, b2 = bottom_pts[i], bottom_pts[i + 1]
            if _vlen(_vsub(b, a)) <= 1e-9:
                continue
            ring = [a, b, b2, a2]
            loop = make_closed_loop_from_ring(ring)
            boundaries = List[CurveLoop]()
            boundaries.Add(loop)
            fr = FilledRegion.Create(doc, fr_type_id, view.Id, boundaries)
            if fr:
                regions.append(fr)
        except Exception:
            continue
    return regions


# ============================================================
# GEOMETRY EXTENT (DC alignment - Local Segment Origin)
# ============================================================

def _collect_points(geom_elem, points):
    if geom_elem is None:
        return
    for gobj in geom_elem:
        try:
            if isinstance(gobj, GeometryInstance):
                _collect_points(gobj.GetInstanceGeometry(), points)
            elif isinstance(gobj, Solid) and gobj.Edges:
                for edge in gobj.Edges:
                    try:
                        c = edge.AsCurve()
                        if c:
                            points.append(c.GetEndPoint(0))
                            points.append(c.GetEndPoint(1))
                            try:
                                points.append(c.Evaluate(0.5, True))
                            except:
                                pass
                    except:
                        pass
            elif isinstance(gobj, Curve):
                points.append(gobj.GetEndPoint(0))
                points.append(gobj.GetEndPoint(1))
                try:
                    points.append(gobj.Evaluate(0.5, True))
                except:
                    pass
            elif isinstance(gobj, Mesh):
                for v in gobj.Vertices:
                    points.append(v)
            elif isinstance(gobj, PolyLine):
                for pt in gobj.GetCoordinates():
                    points.append(pt)
            elif isinstance(gobj, Point):
                points.append(gobj.Coord)
        except:
            continue


def get_extent_along(element, view, origin, direction):
    """Measures min/max extent of geometry relative to local segment origin."""
    points = []
    try:
        opts = Options()
        opts.View = view
        opts.ComputeReferences = False
        geom = element.get_Geometry(opts)
        _collect_points(geom, points)
    except:
        pass
    if len(points) >= 2:
        projs = [_vdot(_vsub(p, origin), direction) for p in points]
        return min(projs), max(projs)
    try:
        bb = element.get_BoundingBox(view)
        if bb:
            corners = [
                XYZ(x, y, z)
                for x in (bb.Min.X, bb.Max.X)
                for y in (bb.Min.Y, bb.Max.Y)
                for z in (bb.Min.Z, bb.Max.Z)
            ]
            projs = [_vdot(_vsub(c, origin), direction) for c in corners]
            return min(projs), max(projs)
    except:
        pass
    return 0.0, mm_to_feet(10)


# ============================================================
# DC PLACEMENT
# ============================================================

def get_or_create_sketch_plane(view):
    sp = view.SketchPlane
    if not sp:
        try:
            plane = Plane.CreateByNormalAndOrigin(view.ViewDirection, view.Origin)
        except AttributeError:
            plane = Plane(view.ViewDirection, view.Origin)
        sp = SketchPlane.Create(doc, plane)
        view.SketchPlane = sp
    return sp


def place_dc_per_segment(dc_type_id, view, path_pts, top_offset):
    """Places Detail Component on EVERY segment of the offset path."""
    symbol = doc.GetElement(dc_type_id)
    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()

    top_path = offset_polyline_miter(path_pts, top_offset)
    top_path = _clean_pts(top_path)
    placement = ""
    try:
        placement = symbol.Family.FamilyPlacementType.ToString()
    except:
        pass

    instances = []
    max_h = mm_to_feet(1)

    for i in range(len(top_path) - 1):
        a, b = top_path[i], top_path[i + 1]
        if _vlen(_vsub(b, a)) < 1e-6:
            continue

        local_down = segment_perp_down(a, b)
        inst = None

        try:
            if "Curve" in placement or "Line" in placement:
                line = Line.CreateBound(a, b)
                inst = doc.Create.NewFamilyInstance(line, symbol, view)
        except:
            inst = None

        if inst is None:
            mid = XYZ((a.X + b.X) * 0.5, (a.Y + b.Y) * 0.5, (a.Z + b.Z) * 0.5)
            base_dir = _vunit(_vsub(b, a))
            angle = math.atan2(base_dir.Y, base_dir.X)
            try:
                inst = doc.Create.NewFamilyInstance(mid, symbol, view)
                if abs(angle) > 1e-6:
                    axis = Line.CreateBound(mid, _vadd(mid, XYZ(0, 0, 1)))
                    ElementTransformUtils.RotateElement(doc, inst.Id, axis, angle)
            except:
                try:
                    sp = get_or_create_sketch_plane(view)
                    inst = doc.Create.NewFamilyInstance(
                        mid, symbol, sp, StructuralType.NonStructural)
                    if abs(angle) > 1e-6:
                        axis = Line.CreateBound(mid, _vadd(mid, view.ViewDirection))
                        ElementTransformUtils.RotateElement(doc, inst.Id, axis, angle)
                except:
                    continue

        if inst:
            doc.Regenerate()
            min_p, max_p = get_extent_along(inst, view, a, local_down)

            if abs(min_p) > 1e-4:
                shift_vec = _vscale(local_down, -min_p)
                ElementTransformUtils.MoveElement(doc, inst.Id, shift_vec)
                doc.Regenerate()
                min_p, max_p = get_extent_along(inst, view, a, local_down)

            seg_h = max_p - min_p
            if seg_h > max_h:
                max_h = seg_h

            instances.append(inst)

    if not instances:
        return [], mm_to_feet(10)

    return instances, max_h


def place_dc_on_path(dc_type_id, view, path_pts, top_offset, angle_ref_dir):
    """Places single DC along whole path chord."""
    symbol = doc.GetElement(dc_type_id)
    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()

    top_path = offset_polyline_miter(path_pts, top_offset)
    top_path = _clean_pts(top_path)
    p0, p1 = top_path[0], top_path[-1]
    base_dir = _vunit(_vsub(p1, p0))
    if _vlen(base_dir) < 1e-9:
        base_dir = angle_ref_dir
    angle = math.atan2(base_dir.Y, base_dir.X)
    mid = XYZ((p0.X + p1.X) * 0.5, (p0.Y + p1.Y) * 0.5, (p0.Z + p1.Z) * 0.5)

    placement = ""
    try:
        placement = symbol.Family.FamilyPlacementType.ToString()
    except:
        pass

    instance = None
    placed_on_curve = False

    if "Curve" in placement or "Line" in placement:
        try:
            if _vlen(_vsub(p1, p0)) > 1e-6:
                line = Line.CreateBound(p0, p1)
                instance = doc.Create.NewFamilyInstance(line, symbol, view)
                placed_on_curve = True
        except:
            instance = None

    if instance is None:
        try:
            if "WorkPlane" in placement:
                sp = get_or_create_sketch_plane(view)
                instance = doc.Create.NewFamilyInstance(
                    mid, symbol, sp, StructuralType.NonStructural)
            else:
                instance = doc.Create.NewFamilyInstance(mid, symbol, view)
        except:
            try:
                sp = get_or_create_sketch_plane(view)
                instance = doc.Create.NewFamilyInstance(
                    mid, symbol, sp, StructuralType.NonStructural)
            except:
                try:
                    line = Line.CreateBound(p0, p1)
                    instance = doc.Create.NewFamilyInstance(line, symbol, view)
                    placed_on_curve = True
                except:
                    return None, mm_to_feet(10)

    if instance and not placed_on_curve and abs(angle) > 1e-6:
        try:
            axis = Line.CreateBound(mid, _vadd(mid, XYZ(0, 0, 1)))
            ElementTransformUtils.RotateElement(doc, instance.Id, axis, angle)
        except:
            try:
                axis = Line.CreateBound(mid, _vadd(mid, view.ViewDirection))
                ElementTransformUtils.RotateElement(doc, instance.Id, axis, angle)
            except:
                pass

    doc.Regenerate()

    meas_down = segment_perp_down(p0, p1)
    min_p, max_p = get_extent_along(instance, view, p0, meas_down)
    if abs(min_p) > 1e-4:
        shift_vec = _vscale(meas_down, -min_p)
        ElementTransformUtils.MoveElement(doc, instance.Id, shift_vec)
        doc.Regenerate()
        min_p, max_p = get_extent_along(instance, view, p0, meas_down)

    height = max(max_p - min_p, mm_to_feet(1))
    return instance, height


# ============================================================
# MAIN PLACEMENT
# ============================================================

def place_stack_on_path(stack_items, path_pts, view):
    path_pts = [project_to_view_plane(p, view) for p in path_pts]
    path_pts = ensure_path_left_to_right(path_pts)
    path_pts = _clean_pts(path_pts)

    if len(path_pts) < 2:
        TaskDialog.Show("Error", "Need at least 2 distinct points.")
        return

    ref_dir = segment_dir(path_pts[0], path_pts[1])
    current_offset = 0.0
    placed = []

    t = Transaction(doc, "Place Detail Stack (Multi-Point Path)")
    t.Start()
    try:
        for item in stack_items:
            if item['type'] == 'FR':
                h = mm_to_feet(item['height_mm'])
                # Stack correctly: this layer sits between current_offset and current_offset+h
                frs = create_fr_strip(
                    item['element_id'], view, path_pts, current_offset, h)
                placed.extend(frs)
                current_offset += h

            elif item['type'] == 'DC':
                user_h = item.get('height_mm', 0) or 0
                symbol = doc.GetElement(item['element_id'])
                ptype = ""
                try:
                    ptype = symbol.Family.FamilyPlacementType.ToString()
                except:
                    pass

                if len(path_pts) > 2:
                    insts, comp_h = place_dc_per_segment(
                        item['element_id'], view, path_pts, current_offset)
                    placed.extend(insts)
                else:
                    inst, comp_h = place_dc_on_path(
                        item['element_id'], view, path_pts,
                        current_offset, ref_dir)
                    if inst:
                        placed.append(inst)

                if user_h > 0:
                    current_offset += mm_to_feet(user_h)
                else:
                    current_offset += comp_h

        t.Commit()
        TaskDialog.Show(
            "Detail Stacker - Complete",
            "Placed {} elements along {}-point path.\n"
            "Total stack height: {:.1f} mm\n"
            "Layers are top-down with no gaps.\n"
            "Filled Region ends are vertical.".format(
                len(placed), len(path_pts), feet_to_mm(current_offset)))
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        TaskDialog.Show(
            "Error",
            "Failed:\n{}\n\n{}".format(str(ex), traceback.format_exc()))


# ============================================================
# MULTI-POINT PICKER
# ============================================================

def pick_path_points(view):
    sel = uidoc.Selection
    points = []

    TaskDialog.Show(
        "Pick Path Points",
        "Click points along the TOP path of the stack.\n\n"
        "  • Point 1, 2, 3, 4… as many as you need\n"
        "  • Path can bend at any angle\n"
        "  • Press ESC when finished (need at least 2 points)\n\n"
        "Layers stack downward from this path with no gaps.")

    while True:
        try:
            prompt = ("Pick path point #{}  |  ESC when done ({} so far)").format(
                len(points) + 1, len(points))
            pt = sel.PickPoint(prompt)
            pt = project_to_view_plane(pt, view)
            points.append(pt)
        except Exception as ex:
            name = type(ex).__name__
            msg = str(ex).lower()
            if ("OperationCanceled" in name or "cancel" in msg or "abort" in msg):
                break
            TaskDialog.Show("Error", "Point pick failed:\n{}".format(str(ex)))
            return None

    points = _clean_pts(points)
    if len(points) < 2:
        TaskDialog.Show(
            "Not enough points",
            "You picked {} valid point(s). Need at least 2.".format(len(points)))
        return None
    return points


# ============================================================
# MAIN
# ============================================================

def main():
    view = doc.ActiveView
    valid = [ViewType.Detail, ViewType.DraftingView, ViewType.Section,
             ViewType.Elevation, ViewType.FloorPlan, ViewType.CeilingPlan,
             ViewType.EngineeringPlan]
    if view.ViewType not in valid:
        TaskDialog.Show(
            "Error",
            "Run this in a Detail, Drafting, Section, Elevation or Plan view.")
        return

    form = StackerForm()
    result = form.ShowDialog()
    if result != DialogResult.OK or not form.result:
        return

    stack_items = form.result
    path_pts = pick_path_points(view)
    if not path_pts:
        return

    place_stack_on_path(stack_items, path_pts, view)


if __name__ == '__main__':
    main()