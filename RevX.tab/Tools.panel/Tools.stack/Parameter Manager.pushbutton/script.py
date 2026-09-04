# -*- coding: utf-8 -*-
"""Parameter Manager - A comprehensive parameter management tool for Revit."""

__title__ = "Parameter\nManager"
__doc__ = "Comprehensive Parameter Manager tool for managing shared, project and family parameters."

import clr
import os
import sys
import json

clr.AddReference('System')
clr.AddReference('System.Windows.Forms')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xml')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from System import *
from System.Collections.Generic import List
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Windows import (
    Window, MessageBox, MessageBoxButton, MessageBoxResult,
    MessageBoxImage, Visibility, Thickness, FontWeights, FontStyles,
    HorizontalAlignment, VerticalAlignment, GridLength, GridUnitType,
    TextWrapping, WindowStartupLocation, ResizeMode
)
from System.Windows.Controls import Orientation, ScrollBarVisibility
from System.Windows.Data import *
from System.Windows.Media import Brushes, SolidColorBrush, Color
from System.Windows.Input import Cursors
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *

from pyrevit import revit, script, forms
from pyrevit.framework import wpf

# WPF control types (for programmatic UI building)
import System.Windows.Controls as swc

WpfButton       = swc.Button
WpfTextBox      = swc.TextBox
WpfTextBlock    = swc.TextBlock
WpfCheckBox     = swc.CheckBox
WpfRadioButton  = swc.RadioButton
WpfComboBox     = swc.ComboBox
WpfStackPanel   = swc.StackPanel
WpfScrollViewer = swc.ScrollViewer
WpfGroupBox     = swc.GroupBox
WpfListBox      = swc.ListBox
WpfTreeView     = swc.TreeView
WpfTreeViewItem = swc.TreeViewItem

# Revit context
app   = __revit__.Application
doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

# ─── Version helpers ──────────────────────────────────────────────────────────

def get_revit_version():
    return int(app.VersionNumber)

REVIT_VERSION = get_revit_version()


def get_parameter_type_name(param_def):
    try:
        if REVIT_VERSION >= 2022:
            try:
                spec_id = param_def.GetDataType()
                if spec_id and not spec_id.Empty():
                    return LabelUtils.GetLabelForSpec(spec_id)
            except:
                pass
            try:
                if hasattr(param_def, 'ParameterType'):
                    return str(param_def.ParameterType)
            except:
                pass
            return "Text"
        else:
            return param_def.ParameterType.ToString()
    except:
        return "Text"


def get_parameter_group_name(param_def):
    try:
        if REVIT_VERSION >= 2022:
            try:
                group_id = param_def.GetGroupTypeId()
                if group_id and not group_id.Empty():
                    return LabelUtils.GetLabelForGroup(group_id)
            except:
                pass
            try:
                return LabelUtils.GetLabelFor(param_def.ParameterGroup)
            except:
                pass
            return "Other"
        else:
            try:
                return LabelUtils.GetLabelFor(param_def.ParameterGroup)
            except:
                return param_def.ParameterGroup.ToString()
    except:
        return "Other"


def get_builtin_param_group_list():
    groups = []
    try:
        if REVIT_VERSION >= 2022:
            try:
                from Autodesk.Revit.DB import GroupTypeId
                group_type = clr.GetClrType(GroupTypeId)
                for prop in group_type.GetProperties():
                    if prop.PropertyType.Name == "ForgeTypeId":
                        try:
                            ftid = prop.GetValue(None)
                            if ftid and not ftid.Empty():
                                label = LabelUtils.GetLabelForGroup(ftid)
                                groups.append((label, ftid))
                        except:
                            pass
            except:
                pass
        else:
            from System import Enum
            for bpg in Enum.GetValues(clr.GetClrType(BuiltInParameterGroup)):
                try:
                    label = LabelUtils.GetLabelFor(bpg)
                    groups.append((label, bpg))
                except:
                    pass
    except:
        pass
    if not groups:
        groups.append(("General", None))
    groups.sort(key=lambda x: x[0])
    return groups


def get_parameter_type_list():
    types = []
    try:
        if REVIT_VERSION >= 2022:
            try:
                from Autodesk.Revit.DB import SpecTypeId
                spec_type = clr.GetClrType(SpecTypeId)
                for prop in spec_type.GetProperties():
                    if prop.PropertyType.Name == "ForgeTypeId":
                        try:
                            ftid = prop.GetValue(None)
                            if ftid and not ftid.Empty():
                                label = LabelUtils.GetLabelForSpec(ftid)
                                types.append((label, ftid))
                        except:
                            pass
                for nt in spec_type.GetNestedTypes():
                    for prop in nt.GetProperties():
                        if prop.PropertyType.Name == "ForgeTypeId":
                            try:
                                ftid = prop.GetValue(None)
                                if ftid and not ftid.Empty():
                                    label = LabelUtils.GetLabelForSpec(ftid)
                                    types.append((label, ftid))
                            except:
                                pass
            except:
                pass
        else:
            from System import Enum
            for pt in Enum.GetValues(clr.GetClrType(ParameterType)):
                if pt != ParameterType.Invalid:
                    try:
                        types.append((pt.ToString(), pt))
                    except:
                        pass
    except:
        pass
    if not types:
        types.append(("Text", None))
    types.sort(key=lambda x: x[0])
    return types


def get_category_list():
    cats = []
    try:
        for cat in doc.Settings.Categories:
            if cat.AllowsBoundParameters:
                cats.append(cat)
    except:
        pass
    cats.sort(key=lambda c: c.Name)
    return cats


# ─── Data models ─────────────────────────────────────────────────────────────

class NotifyPropertyChangedBase(INotifyPropertyChanged):
    PropertyChanged = None

    def __init__(self):
        self._handlers = []

    def add_PropertyChanged(self, handler):
        self._handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)

    def OnPropertyChanged(self, name):
        for h in list(self._handlers):
            try:
                h(self, PropertyChangedEventArgs(name))
            except:
                pass


class ParameterItem(NotifyPropertyChangedBase):
    def __init__(self):
        NotifyPropertyChangedBase.__init__(self)
        self._is_selected   = False
        self.Name           = ""
        self.ParameterType  = ""
        self.ParameterGroup = ""
        self.IsInstance     = True
        self.IsShared       = False
        self.GUID           = ""
        self.Categories     = ""
        self.Source         = ""
        self.Definition     = None
        self.Binding        = None
        self.InternalParam  = None
        self.Value          = ""
        self.HasValue       = False

    @property
    def IsSelected(self):
        return self._is_selected

    @IsSelected.setter
    def IsSelected(self, value):
        if self._is_selected != value:
            self._is_selected = value
            self.OnPropertyChanged("IsSelected")


class SharedParamGroupItem(object):
    def __init__(self, name, definition_group=None):
        self.Name            = name
        self.DefinitionGroup = definition_group
        self.Parameters      = []


class SharedParamItem(object):
    def __init__(self):
        self.Name        = ""
        self.GUID        = ""
        self.Type        = ""
        self.Group       = ""
        self.Description = ""
        self.Definition  = None


# ─── Edit Parameter Dialog ────────────────────────────────────────────────────

class EditParameterDialog(Window):

    def __init__(self, param_item, all_categories, presets, param_groups, param_types):
        self.param_item     = param_item
        self.all_categories = all_categories
        self.presets        = presets
        self.param_groups   = param_groups
        self.param_types    = param_types
        self.Result         = None
        self._build_ui()

    def _build_ui(self):
        self.Title                 = "Edit: {}".format(self.param_item.Name)
        self.Width                 = 560
        self.Height                = 760
        self.WindowStartupLocation = WindowStartupLocation.CenterOwner
        self.ResizeMode            = ResizeMode.CanResize

        root_scroll = WpfScrollViewer()
        root_scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto

        outer        = WpfStackPanel()
        outer.Margin = Thickness(12)

        # Header
        hdr              = WpfTextBlock()
        hdr.Text         = u"Parameter: {}".format(self.param_item.Name)
        hdr.FontWeight   = FontWeights.Bold
        hdr.FontSize     = 14
        hdr.TextWrapping = TextWrapping.Wrap
        hdr.Margin       = Thickness(0, 0, 0, 10)
        outer.Children.Add(hdr)

        # Group + Type change
        gb_gt          = WpfGroupBox()
        gb_gt.Header   = "Parameter Group / Type"
        gb_gt.Margin   = Thickness(0, 0, 0, 8)

        gt_sp        = WpfStackPanel()
        gt_sp.Margin = Thickness(6)

        lbl_g        = WpfTextBlock()
        lbl_g.Text   = "Parameter Group:"
        lbl_g.Margin = Thickness(0, 0, 0, 2)
        gt_sp.Children.Add(lbl_g)

        self.dlg_groupCombo        = WpfComboBox()
        self.dlg_groupCombo.Margin = Thickness(0, 0, 0, 6)

        current_group_label = self.param_item.ParameterGroup
        current_group_idx   = 0
        for i, (label, _) in enumerate(self.param_groups):
            self.dlg_groupCombo.Items.Add(label)
            if label == current_group_label:
                current_group_idx = i
        self.dlg_groupCombo.SelectedIndex = current_group_idx
        gt_sp.Children.Add(self.dlg_groupCombo)

        lbl_t        = WpfTextBlock()
        lbl_t.Text   = "Parameter Type:"
        lbl_t.Margin = Thickness(0, 4, 0, 2)
        gt_sp.Children.Add(lbl_t)

        self.dlg_typeCombo        = WpfComboBox()
        self.dlg_typeCombo.Margin = Thickness(0, 0, 0, 4)

        current_type_label = self.param_item.ParameterType
        current_type_idx   = 0
        for i, (label, _) in enumerate(self.param_types):
            self.dlg_typeCombo.Items.Add(label)
            if label == current_type_label:
                current_type_idx = i
        self.dlg_typeCombo.SelectedIndex = current_type_idx
        gt_sp.Children.Add(self.dlg_typeCombo)

        warn              = WpfTextBlock()
        warn.Text         = (u"Note: Type change is only possible for PROJECT "
                             u"parameters and will RECREATE the parameter "
                             u"(existing values will be lost). "
                             u"Shared parameter types cannot be changed.")
        warn.FontSize     = 11
        warn.FontStyle    = FontStyles.Italic
        warn.TextWrapping = TextWrapping.Wrap
        warn.Margin       = Thickness(0, 4, 0, 0)
        warn.Foreground   = Brushes.DarkOrange
        gt_sp.Children.Add(warn)

        gb_gt.Content = gt_sp
        outer.Children.Add(gb_gt)

        # Binding (Instance / Type)
        gb_bind         = WpfGroupBox()
        gb_bind.Header  = "Binding Type"
        gb_bind.Margin  = Thickness(0, 0, 0, 8)

        bind_panel             = WpfStackPanel()
        bind_panel.Orientation = Orientation.Horizontal
        bind_panel.Margin      = Thickness(6)

        self.dlg_instanceRadio           = WpfRadioButton()
        self.dlg_instanceRadio.Content   = "Instance"
        self.dlg_instanceRadio.IsChecked = bool(self.param_item.IsInstance)
        self.dlg_instanceRadio.Margin    = Thickness(0, 0, 20, 0)

        self.dlg_typeRadio           = WpfRadioButton()
        self.dlg_typeRadio.Content   = "Type"
        self.dlg_typeRadio.IsChecked = not bool(self.param_item.IsInstance)

        bind_panel.Children.Add(self.dlg_instanceRadio)
        bind_panel.Children.Add(self.dlg_typeRadio)
        gb_bind.Content = bind_panel
        outer.Children.Add(gb_bind)

        # Presets
        gb_preset        = WpfGroupBox()
        gb_preset.Header = "Category Presets"
        gb_preset.Margin = Thickness(0, 0, 0, 8)

        preset_sp        = WpfStackPanel()
        preset_sp.Margin = Thickness(6)

        preset_row             = WpfStackPanel()
        preset_row.Orientation = Orientation.Horizontal

        self.dlg_presetCombo        = WpfComboBox()
        self.dlg_presetCombo.Width  = 200
        self.dlg_presetCombo.Margin = Thickness(0, 0, 6, 0)
        self.dlg_presetCombo.Items.Add("-- Select Preset --")
        for name in sorted(self.presets.keys()):
            self.dlg_presetCombo.Items.Add(name)
        self.dlg_presetCombo.SelectedIndex = 0

        replace_btn         = WpfButton()
        replace_btn.Content = "Replace"
        replace_btn.Width   = 70
        replace_btn.Margin  = Thickness(0, 0, 4, 0)
        replace_btn.Padding = Thickness(6, 4, 6, 4)
        replace_btn.Cursor  = Cursors.Hand
        replace_btn.ToolTip = "Replace selection with preset"
        replace_btn.Click  += self._on_replace_preset

        merge_btn         = WpfButton()
        merge_btn.Content = "Merge"
        merge_btn.Width   = 60
        merge_btn.Padding = Thickness(6, 4, 6, 4)
        merge_btn.Cursor  = Cursors.Hand
        merge_btn.ToolTip = "Add preset categories to current selection"
        merge_btn.Click  += self._on_merge_preset

        preset_row.Children.Add(self.dlg_presetCombo)
        preset_row.Children.Add(replace_btn)
        preset_row.Children.Add(merge_btn)
        preset_sp.Children.Add(preset_row)
        gb_preset.Content = preset_sp
        outer.Children.Add(gb_preset)

        # Category list
        gb_cats        = WpfGroupBox()
        gb_cats.Header = "Categories  (assigned ones are pre-checked)"
        gb_cats.Margin = Thickness(0, 0, 0, 8)

        cat_sp        = WpfStackPanel()
        cat_sp.Margin = Thickness(6)

        search_row             = WpfStackPanel()
        search_row.Orientation = Orientation.Horizontal
        search_row.Margin      = Thickness(0, 0, 0, 4)

        lbl_s                   = WpfTextBlock()
        lbl_s.Text              = "Search: "
        lbl_s.VerticalAlignment = VerticalAlignment.Center

        self.dlg_catSearch        = WpfTextBox()
        self.dlg_catSearch.Width  = 180
        self.dlg_catSearch.Margin = Thickness(0, 0, 6, 0)
        self.dlg_catSearch.TextChanged += self._on_search_cats

        all_btn         = WpfButton()
        all_btn.Content = "All"
        all_btn.Width   = 38
        all_btn.Margin  = Thickness(0, 0, 4, 0)
        all_btn.Padding = Thickness(4, 3, 4, 3)
        all_btn.Cursor  = Cursors.Hand
        all_btn.Click  += self._on_all_cats

        none_btn         = WpfButton()
        none_btn.Content = "None"
        none_btn.Width   = 46
        none_btn.Padding = Thickness(4, 3, 4, 3)
        none_btn.Cursor  = Cursors.Hand
        none_btn.Click  += self._on_none_cats

        search_row.Children.Add(lbl_s)
        search_row.Children.Add(self.dlg_catSearch)
        search_row.Children.Add(all_btn)
        search_row.Children.Add(none_btn)
        cat_sp.Children.Add(search_row)

        sv        = WpfScrollViewer()
        sv.Height = 260
        sv.VerticalScrollBarVisibility = ScrollBarVisibility.Visible

        self.dlg_catPanel = WpfStackPanel()

        current_cat_names = set()
        if self.param_item.Binding and self.param_item.Binding.Categories:
            for cat in self.param_item.Binding.Categories:
                current_cat_names.add(cat.Name)

        for cat in self.all_categories:
            cb           = WpfCheckBox()
            cb.Content   = cat.Name
            cb.Tag       = cat
            cb.IsChecked = cat.Name in current_cat_names
            cb.Margin    = Thickness(4, 2, 4, 2)
            cb.Checked   += self._on_cat_changed
            cb.Unchecked += self._on_cat_changed
            self.dlg_catPanel.Children.Add(cb)

        sv.Content = self.dlg_catPanel
        cat_sp.Children.Add(sv)

        self.dlg_catCountLabel          = WpfTextBlock()
        self.dlg_catCountLabel.Margin   = Thickness(0, 4, 0, 0)
        self.dlg_catCountLabel.FontSize = 11
        self._refresh_dlg_count()
        cat_sp.Children.Add(self.dlg_catCountLabel)

        gb_cats.Content = cat_sp
        outer.Children.Add(gb_cats)

        # OK / Cancel
        btn_row                     = WpfStackPanel()
        btn_row.Orientation         = Orientation.Horizontal
        btn_row.HorizontalAlignment = HorizontalAlignment.Right
        btn_row.Margin              = Thickness(0, 8, 0, 0)

        ok_btn         = WpfButton()
        ok_btn.Content = u"Apply Changes"
        ok_btn.Width   = 130
        ok_btn.Height  = 32
        ok_btn.Margin  = Thickness(0, 0, 8, 0)
        ok_btn.Padding = Thickness(8, 4, 8, 4)
        ok_btn.Cursor  = Cursors.Hand
        ok_btn.Click  += self._on_ok

        cancel_btn         = WpfButton()
        cancel_btn.Content = "Cancel"
        cancel_btn.Width   = 80
        cancel_btn.Height  = 32
        cancel_btn.Padding = Thickness(8, 4, 8, 4)
        cancel_btn.Cursor  = Cursors.Hand
        cancel_btn.Click  += self._on_cancel

        btn_row.Children.Add(ok_btn)
        btn_row.Children.Add(cancel_btn)
        outer.Children.Add(btn_row)

        root_scroll.Content = outer
        self.Content        = root_scroll

    def _refresh_dlg_count(self):
        try:
            count = sum(
                1 for cb in self.dlg_catPanel.Children
                if isinstance(cb, WpfCheckBox) and bool(cb.IsChecked))
            self.dlg_catCountLabel.Text = "{} categorie(s) selected".format(count)
        except:
            pass

    def _on_cat_changed(self, sender, e):
        self._refresh_dlg_count()

    def _on_search_cats(self, sender, e):
        search = self.dlg_catSearch.Text.lower().strip()
        for cb in self.dlg_catPanel.Children:
            if isinstance(cb, WpfCheckBox):
                cb.Visibility = (
                    Visibility.Visible
                    if (not search or search in str(cb.Content).lower())
                    else Visibility.Collapsed)

    def _on_all_cats(self, sender, e):
        for cb in self.dlg_catPanel.Children:
            if isinstance(cb, WpfCheckBox) and cb.Visibility == Visibility.Visible:
                cb.IsChecked = True
        self._refresh_dlg_count()

    def _on_none_cats(self, sender, e):
        for cb in self.dlg_catPanel.Children:
            if isinstance(cb, WpfCheckBox):
                cb.IsChecked = False
        self._refresh_dlg_count()

    def _apply_preset(self, replace):
        try:
            if self.dlg_presetCombo.SelectedIndex <= 0:
                MessageBox.Show("Select a preset first.", "Info",
                                MessageBoxButton.OK, MessageBoxImage.Information)
                return
            name = str(self.dlg_presetCombo.SelectedItem)
            if name not in self.presets:
                return
            preset_cats = set(self.presets[name])
            for cb in self.dlg_catPanel.Children:
                if isinstance(cb, WpfCheckBox):
                    if replace:
                        cb.IsChecked = str(cb.Content) in preset_cats
                    else:
                        if str(cb.Content) in preset_cats:
                            cb.IsChecked = True
            self._refresh_dlg_count()
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_replace_preset(self, sender, e):
        self._apply_preset(replace=True)

    def _on_merge_preset(self, sender, e):
        self._apply_preset(replace=False)

    def _on_ok(self, sender, e):
        try:
            selected_cats = [
                cb.Tag for cb in self.dlg_catPanel.Children
                if isinstance(cb, WpfCheckBox) and bool(cb.IsChecked)]
            if not selected_cats:
                MessageBox.Show("At least one category must be selected.",
                                "Error", MessageBoxButton.OK,
                                MessageBoxImage.Warning)
                return

            group_idx = self.dlg_groupCombo.SelectedIndex
            if group_idx < 0:
                group_idx = 0
            type_idx = self.dlg_typeCombo.SelectedIndex
            if type_idx < 0:
                type_idx = 0

            self.Result = {
                'categories':  selected_cats,
                'is_instance': bool(self.dlg_instanceRadio.IsChecked),
                'group_val':   self.param_groups[group_idx][1],
                'group_label': self.param_groups[group_idx][0],
                'type_val':    self.param_types[type_idx][1],
                'type_label':  self.param_types[type_idx][0],
            }
            self.DialogResult = True
            self.Close()
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_cancel(self, sender, e):
        self.DialogResult = False
        self.Close()


# ─── Main Window ──────────────────────────────────────────────────────────────

class ParameterManagerWindow(Window):

    def __init__(self):
        self.parameters          = []
        self.filtered_parameters = []
        self.shared_param_file   = None
        self.shared_groups       = []
        self.param_groups        = get_builtin_param_group_list()
        self.param_types         = get_parameter_type_list()
        self.all_categories      = get_category_list()
        self.category_presets    = {}

        xaml_file = os.path.join(os.path.dirname(__file__), 'ui.xaml')
        wpf.LoadComponent(self, xaml_file)

        self.Title = "Parameter Manager"

        self._load_category_presets()
        self._init_ui()
        self._load_parameters()
        self._load_shared_param_file()

    # ── preset persistence ────────────────────────────────────────────────────

    def _get_presets_path(self):
        return os.path.join(os.path.dirname(__file__), "category_presets.json")

    def _load_category_presets(self):
        try:
            path = self._get_presets_path()
            if os.path.exists(path):
                with open(path, 'r') as f:
                    self.category_presets = json.load(f)
            else:
                self.category_presets = {}
        except:
            self.category_presets = {}
        self._refresh_preset_combo()

    def _save_category_presets(self):
        try:
            with open(self._get_presets_path(), 'w') as f:
                json.dump(self.category_presets, f, indent=2)
        except Exception as ex:
            MessageBox.Show("Error saving presets: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def _refresh_preset_combo(self):
        try:
            if self.presetCombo:
                self.presetCombo.Items.Clear()
                self.presetCombo.Items.Add("-- Select Preset --")
                for name in sorted(self.category_presets.keys()):
                    self.presetCombo.Items.Add(name)
                self.presetCombo.SelectedIndex = 0
        except:
            pass

    # ── UI init ───────────────────────────────────────────────────────────────

    def _init_ui(self):
        try:
            if self.paramGroupCombo:
                self.paramGroupCombo.Items.Clear()
                self.paramGroupCombo.Items.Add("All Groups")
                for label, _ in self.param_groups:
                    self.paramGroupCombo.Items.Add(label)
                self.paramGroupCombo.SelectedIndex = 0

            if self.paramTypeCombo:
                self.paramTypeCombo.Items.Clear()
                self.paramTypeCombo.Items.Add("All Types")
                for label, _ in self.param_types:
                    self.paramTypeCombo.Items.Add(label)
                self.paramTypeCombo.SelectedIndex = 0

            if self.sourceFilterCombo:
                self.sourceFilterCombo.Items.Clear()
                self.sourceFilterCombo.Items.Add("All Sources")
                self.sourceFilterCombo.Items.Add("Project Parameters")
                self.sourceFilterCombo.Items.Add("Shared Parameters")
                self.sourceFilterCombo.SelectedIndex = 0

            if self.instanceTypeCombo:
                self.instanceTypeCombo.Items.Clear()
                self.instanceTypeCombo.Items.Add("All")
                self.instanceTypeCombo.Items.Add("Instance")
                self.instanceTypeCombo.Items.Add("Type")
                self.instanceTypeCombo.SelectedIndex = 0

            self._populate_category_listbox(self.categoryList)
            self._populate_category_listbox(self.editCategoryList)
            self._refresh_edit_cat_count()

            if self.addParamGroupCombo:
                self.addParamGroupCombo.Items.Clear()
                for label, _ in self.param_groups:
                    self.addParamGroupCombo.Items.Add(label)
                if self.addParamGroupCombo.Items.Count > 0:
                    self.addParamGroupCombo.SelectedIndex = 0

            if self.addParamTypeCombo:
                self.addParamTypeCombo.Items.Clear()
                for label, _ in self.param_types:
                    self.addParamTypeCombo.Items.Add(label)
                for i in range(self.addParamTypeCombo.Items.Count):
                    if str(self.addParamTypeCombo.Items[i]).lower() == "text":
                        self.addParamTypeCombo.SelectedIndex = i
                        break
                else:
                    if self.addParamTypeCombo.Items.Count > 0:
                        self.addParamTypeCombo.SelectedIndex = 0

            if self.sharedGroupCombo:
                self.sharedGroupCombo.Items.Clear()

            self._update_status("Ready")
        except Exception as ex:
            self._update_status("Init error: " + str(ex))

    def _populate_category_listbox(self, list_box):
        try:
            if list_box is None:
                return
            list_box.Items.Clear()
            for cat in self.all_categories:
                cb          = WpfCheckBox()
                cb.Content  = cat.Name
                cb.Tag      = cat
                cb.Margin   = Thickness(4, 2, 4, 2)
                cb.FontSize = 12
                list_box.Items.Add(cb)
        except:
            pass

    # ── helpers ───────────────────────────────────────────────────────────────

    def _update_status(self, msg):
        try:
            if self.statusText:
                self.statusText.Text = str(msg)
        except:
            pass

    def _refresh_edit_cat_count(self):
        try:
            if self.editCategoryList is None:
                return
            count = sum(
                1 for item in self.editCategoryList.Items
                if isinstance(item, WpfCheckBox) and bool(item.IsChecked))
            if self.editCatCountLabel:
                self.editCatCountLabel.Text = \
                    "{} categorie(s) selected".format(count)
        except:
            pass

    def _get_checked_edit_categories(self):
        result = []
        try:
            if self.editCategoryList:
                for item in self.editCategoryList.Items:
                    if isinstance(item, WpfCheckBox) and bool(item.IsChecked):
                        result.append(item.Tag)
        except:
            pass
        return result

    def _get_selected_params(self):
        return [p for p in self.filtered_parameters if p.IsSelected]

    # ── load parameters ───────────────────────────────────────────────────────

    def _load_parameters(self):
        self.parameters = []
        try:
            binding_map = doc.ParameterBindings
            iterator    = binding_map.ForwardIterator()
            iterator.Reset()

            while iterator.MoveNext():
                try:
                    definition = iterator.Key
                    binding    = iterator.Current

                    p                = ParameterItem()
                    p.Name           = definition.Name
                    p.ParameterType  = get_parameter_type_name(definition)
                    p.ParameterGroup = get_parameter_group_name(definition)
                    p.Definition     = definition
                    p.Binding        = binding
                    p.IsInstance     = isinstance(binding, InstanceBinding)

                    cat_names = []
                    if binding and binding.Categories:
                        for cat in binding.Categories:
                            cat_names.append(cat.Name)
                    p.Categories = ", ".join(sorted(cat_names))

                    try:
                        if hasattr(definition, 'GUID'):
                            p.GUID     = str(definition.GUID)
                            p.IsShared = True
                    except:
                        pass
                    if not p.IsShared:
                        try:
                            if hasattr(definition, 'IsShared'):
                                p.IsShared = definition.IsShared
                        except:
                            pass

                    p.Source = "Shared" if p.IsShared else "Project"
                    self.parameters.append(p)
                except:
                    continue
        except Exception as ex:
            self._update_status("Error loading parameters: " + str(ex))

        self._apply_filters()
        self._update_status("Loaded {} parameters".format(len(self.parameters)))

    def _load_shared_param_file(self):
        self.shared_groups = []
        try:
            spf = app.OpenSharedParameterFile()
            if spf:
                self.shared_param_file = spf
                if self.sharedFilePathText:
                    self.sharedFilePathText.Text = (
                        spf.Filename if hasattr(spf, 'Filename') else "Loaded")

                for group in spf.Groups:
                    g = SharedParamGroupItem(group.Name, group)
                    for defn in group.Definitions:
                        sp            = SharedParamItem()
                        sp.Name       = defn.Name
                        sp.Definition = defn
                        try:
                            sp.GUID = str(defn.GUID)
                        except:
                            pass
                        sp.Type  = get_parameter_type_name(defn)
                        sp.Group = group.Name
                        try:
                            if hasattr(defn, 'Description'):
                                sp.Description = defn.Description or ""
                        except:
                            pass
                        g.Parameters.append(sp)
                    self.shared_groups.append(g)

                self._populate_shared_tree()
                self._populate_shared_group_combo()
            else:
                if self.sharedFilePathText:
                    self.sharedFilePathText.Text = "No shared parameter file loaded"
        except Exception as ex:
            if self.sharedFilePathText:
                self.sharedFilePathText.Text = "Error: " + str(ex)

    def _populate_shared_tree(self):
        try:
            if not self.sharedParamTree:
                return
            self.sharedParamTree.Items.Clear()
            for group in self.shared_groups:
                gi            = WpfTreeViewItem()
                gi.Header     = u"[{}]  ({} params)".format(
                    group.Name, len(group.Parameters))
                gi.IsExpanded = True
                gi.Tag        = group
                for param in group.Parameters:
                    pi        = WpfTreeViewItem()
                    pi.Header = u"  {}  [{}]".format(param.Name, param.Type)
                    pi.Tag    = param
                    gi.Items.Add(pi)
                self.sharedParamTree.Items.Add(gi)
        except:
            pass

    def _populate_shared_group_combo(self):
        try:
            if self.sharedGroupCombo:
                self.sharedGroupCombo.Items.Clear()
                for g in self.shared_groups:
                    self.sharedGroupCombo.Items.Add(g.Name)
                if self.sharedGroupCombo.Items.Count > 0:
                    self.sharedGroupCombo.SelectedIndex = 0
        except:
            pass

    # ── filters ───────────────────────────────────────────────────────────────

    def _apply_filters(self):
        filtered = list(self.parameters)
        try:
            if self.searchBox and self.searchBox.Text:
                search = self.searchBox.Text.lower().strip()
                if search:
                    filtered = [
                        p for p in filtered
                        if search in p.Name.lower()
                        or search in p.Categories.lower()
                        or search in p.ParameterType.lower()
                        or search in p.GUID.lower()]

            if self.sourceFilterCombo and self.sourceFilterCombo.SelectedIndex > 0:
                sel = str(self.sourceFilterCombo.SelectedItem)
                if "Project" in sel:
                    filtered = [p for p in filtered if p.Source == "Project"]
                elif "Shared" in sel:
                    filtered = [p for p in filtered if p.Source == "Shared"]

            if self.instanceTypeCombo and self.instanceTypeCombo.SelectedIndex > 0:
                sel = str(self.instanceTypeCombo.SelectedItem)
                if "Instance" in sel:
                    filtered = [p for p in filtered if p.IsInstance]
                elif "Type" in sel:
                    filtered = [p for p in filtered if not p.IsInstance]

            if self.paramGroupCombo and self.paramGroupCombo.SelectedIndex > 0:
                sel_group = str(self.paramGroupCombo.SelectedItem)
                filtered  = [p for p in filtered if p.ParameterGroup == sel_group]

            if self.paramTypeCombo and self.paramTypeCombo.SelectedIndex > 0:
                sel_type = str(self.paramTypeCombo.SelectedItem)
                filtered = [p for p in filtered if p.ParameterType == sel_type]
        except:
            pass

        self.filtered_parameters = filtered
        self._update_datagrid()

    def _update_datagrid(self):
        try:
            if self.paramDataGrid:
                self.paramDataGrid.ItemsSource = None
                self.paramDataGrid.ItemsSource = self.filtered_parameters
            if self.countText:
                self.countText.Text = "Showing {} of {} parameters".format(
                    len(self.filtered_parameters), len(self.parameters))
        except Exception as ex:
            self._update_status("Grid error: " + str(ex))

    # ═══════════════════════════════════════════════════════════════════════════
    # EVENT HANDLERS
    # ═══════════════════════════════════════════════════════════════════════════

    def OnSearchChanged(self, sender, e):
        self._apply_filters()

    def OnFilterChanged(self, sender, e):
        self._apply_filters()

    def OnRefresh(self, sender, e):
        self._load_parameters()
        self._load_shared_param_file()
        self._update_status("Refreshed")

    def OnSelectAll(self, sender, e):
        for p in self.filtered_parameters:
            p.IsSelected = True
        self._update_datagrid()

    def OnSelectNone(self, sender, e):
        for p in self.filtered_parameters:
            p.IsSelected = False
        self._update_datagrid()

    def OnInvertSelection(self, sender, e):
        for p in self.filtered_parameters:
            p.IsSelected = not p.IsSelected
        self._update_datagrid()

    # ── Category list (Add Parameter tab) ─────────────────────────────────────

    def OnSelectAllCategories(self, sender, e):
        if self.categoryList:
            for item in self.categoryList.Items:
                if isinstance(item, WpfCheckBox) and item.Visibility == Visibility.Visible:
                    item.IsChecked = True

    def OnSelectNoCategories(self, sender, e):
        if self.categoryList:
            for item in self.categoryList.Items:
                if isinstance(item, WpfCheckBox):
                    item.IsChecked = False

    def OnSearchCategories(self, sender, e):
        search = (self.categorySearchBox.Text.lower().strip()
                  if self.categorySearchBox else "")
        if self.categoryList:
            for item in self.categoryList.Items:
                if isinstance(item, WpfCheckBox):
                    item.Visibility = (
                        Visibility.Visible
                        if (not search or search in str(item.Content).lower())
                        else Visibility.Collapsed)

    # ── Category list (Edit Categories tab) ───────────────────────────────────

    def OnSearchEditCategories(self, sender, e):
        search = (self.editCategorySearchBox.Text.lower().strip()
                  if self.editCategorySearchBox else "")
        if self.editCategoryList:
            for item in self.editCategoryList.Items:
                if isinstance(item, WpfCheckBox):
                    item.Visibility = (
                        Visibility.Visible
                        if (not search or search in str(item.Content).lower())
                        else Visibility.Collapsed)

    # ── Add Project Parameter ─────────────────────────────────────────────────

    def OnAddProjectParam(self, sender, e):
        try:
            name = (self.addParamNameBox.Text.strip()
                    if self.addParamNameBox else "")
            if not name:
                MessageBox.Show("Please enter a parameter name.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return

            for p in self.parameters:
                if p.Name == name:
                    MessageBox.Show(
                        "Parameter '{}' already exists.".format(name),
                        "Error", MessageBoxButton.OK, MessageBoxImage.Warning)
                    return

            group_idx = (self.addParamGroupCombo.SelectedIndex
                         if self.addParamGroupCombo else 0)
            if group_idx < 0:
                group_idx = 0
            type_idx = (self.addParamTypeCombo.SelectedIndex
                        if self.addParamTypeCombo else 0)
            if type_idx < 0:
                type_idx = 0

            is_instance = True
            if self.addTypeRadio and self.addTypeRadio.IsChecked:
                is_instance = False

            selected_cats = []
            if self.categoryList:
                for item in self.categoryList.Items:
                    if isinstance(item, WpfCheckBox) and item.IsChecked:
                        selected_cats.append(item.Tag)

            if not selected_cats:
                MessageBox.Show("Please select at least one category.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return

            self._add_project_param_via_shared(
                name,
                self.param_types[type_idx][1],
                self.param_groups[group_idx][1],
                is_instance, selected_cats)

        except Exception as ex:
            MessageBox.Show("Error adding parameter: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def _add_project_param_via_shared(self, name, param_type_val,
                                      param_group_val, is_instance, categories):
        import tempfile

        original_spf_path = None
        try:
            original_spf = app.OpenSharedParameterFile()
            if original_spf and hasattr(original_spf, 'Filename'):
                original_spf_path = original_spf.Filename
        except:
            pass

        temp_path = os.path.join(tempfile.gettempdir(), "pyrevit_pm_temp.txt")
        try:
            with open(temp_path, 'w') as f:
                f.write("# Temporary shared parameter file\n")
                f.write("*META\tVERSION\tMINVERSION\n")
                f.write("META\t2\t1\n")
                f.write("*GROUP\tID\tNAME\n")
                f.write("*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY"
                        "\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\n")

            app.SharedParametersFilename = temp_path
            temp_spf = app.OpenSharedParameterFile()
            if temp_spf is None:
                raise Exception("Could not create temporary shared parameter file")

            temp_group = None
            for g in temp_spf.Groups:
                if g.Name == "TempGroup":
                    temp_group = g
                    break
            if temp_group is None:
                temp_group = temp_spf.Groups.Create("TempGroup")

            opts         = ExternalDefinitionCreationOptions(name, param_type_val)
            opts.Visible = True
            ext_def      = temp_group.Definitions.Create(opts)

            t = Transaction(doc, "Add Project Parameter")
            t.Start()
            try:
                cat_set = CategorySet()
                for cat in categories:
                    cat_set.Insert(cat)

                new_binding = (InstanceBinding(cat_set)
                               if is_instance else TypeBinding(cat_set))

                if param_group_val is None:
                    if REVIT_VERSION >= 2022:
                        try:
                            param_group_val = GroupTypeId.General
                        except:
                            param_group_val = BuiltInParameterGroup.PG_GENERAL
                    else:
                        param_group_val = BuiltInParameterGroup.PG_GENERAL

                doc.ParameterBindings.Insert(ext_def, new_binding, param_group_val)
                t.Commit()
                self._update_status("Parameter '{}' added".format(name))
                MessageBox.Show(
                    "Parameter '{}' added successfully!".format(name),
                    "Success", MessageBoxButton.OK, MessageBoxImage.Information)
            except Exception as ex:
                t.RollBack()
                raise ex
        finally:
            if original_spf_path:
                try:
                    app.SharedParametersFilename = original_spf_path
                except:
                    pass
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
        self._load_parameters()

    # ── Add Shared to Project ─────────────────────────────────────────────────

    def OnAddSharedToProject(self, sender, e):
        try:
            if not self.sharedParamTree:
                return
            selected = self.sharedParamTree.SelectedItem
            if selected is None:
                MessageBox.Show("Please select a shared parameter from the tree.",
                                "Info", MessageBoxButton.OK, MessageBoxImage.Information)
                return

            tag = selected.Tag if hasattr(selected, 'Tag') else None
            if not isinstance(tag, SharedParamItem):
                MessageBox.Show("Please select a parameter, not a group.",
                                "Info", MessageBoxButton.OK, MessageBoxImage.Information)
                return
            sp = tag

            selected_cats = []
            if self.categoryList:
                for item in self.categoryList.Items:
                    if isinstance(item, WpfCheckBox) and item.IsChecked:
                        selected_cats.append(item.Tag)
            if not selected_cats:
                MessageBox.Show("Please select at least one category (Add Parameter tab).",
                                "Error", MessageBoxButton.OK, MessageBoxImage.Warning)
                return

            is_instance = True
            if self.addTypeRadio and self.addTypeRadio.IsChecked:
                is_instance = False

            group_idx = (self.addParamGroupCombo.SelectedIndex
                         if self.addParamGroupCombo else 0)
            if group_idx < 0:
                group_idx = 0

            t = Transaction(doc, "Add Shared Parameter to Project")
            t.Start()
            try:
                cat_set = CategorySet()
                for cat in selected_cats:
                    cat_set.Insert(cat)
                binding = (InstanceBinding(cat_set) if is_instance
                           else TypeBinding(cat_set))
                doc.ParameterBindings.Insert(
                    sp.Definition, binding, self.param_groups[group_idx][1])
                t.Commit()
                self._update_status("Shared parameter '{}' added".format(sp.Name))
                MessageBox.Show(
                    "Shared parameter '{}' added to project!".format(sp.Name),
                    "Success", MessageBoxButton.OK, MessageBoxImage.Information)
            except Exception as ex:
                t.RollBack()
                raise ex
            self._load_parameters()
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Delete ────────────────────────────────────────────────────────────────

    def OnDeleteSelected(self, sender, e):
        selected = self._get_selected_params()
        if not selected:
            MessageBox.Show("No parameters selected. Tick the checkboxes first.",
                            "Info", MessageBoxButton.OK, MessageBoxImage.Information)
            return
        result = MessageBox.Show(
            "Delete {} selected parameter(s)? This cannot be undone.".format(len(selected)),
            "Confirm Delete", MessageBoxButton.YesNo, MessageBoxImage.Warning)
        if result != MessageBoxResult.Yes:
            return

        t = Transaction(doc, "Delete Parameters")
        t.Start()
        try:
            deleted = 0
            errors  = []
            for p in selected:
                try:
                    if p.Definition:
                        doc.ParameterBindings.Remove(p.Definition)
                        deleted += 1
                except Exception as ex:
                    errors.append("{}: {}".format(p.Name, str(ex)))
            t.Commit()
            msg = "Deleted {} parameter(s).".format(deleted)
            if errors:
                msg += "\n\nErrors:\n" + "\n".join(errors)
            MessageBox.Show(msg, "Delete Results",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            self._load_parameters()
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Core category application ─────────────────────────────────────────────

    def _apply_categories_to_params(self, params, cats, merge=False):
        t = Transaction(doc, "Update Parameter Categories")
        t.Start()
        try:
            modified = 0
            errors   = []
            for p in params:
                if not p.Definition or not p.Binding:
                    continue
                try:
                    cat_set = CategorySet()
                    if merge and p.Binding.Categories:
                        for existing_cat in p.Binding.Categories:
                            cat_set.Insert(existing_cat)
                    for cat in cats:
                        cat_set.Insert(cat)
                    new_binding = (InstanceBinding(cat_set) if p.IsInstance
                                   else TypeBinding(cat_set))
                    doc.ParameterBindings.ReInsert(p.Definition, new_binding)
                    modified += 1
                except Exception as ex:
                    errors.append("{}: {}".format(p.Name, str(ex)))
            t.Commit()
            msg = "Modified {} parameter(s).".format(modified)
            if errors:
                msg += "\n\nErrors:\n" + "\n".join(errors)
            MessageBox.Show(msg, "Success",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            self._load_parameters()
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Modify / Add / Remove categories ─────────────────────────────────────

    def OnModifyCategories(self, sender, e):
        selected = self._get_selected_params()
        if not selected:
            MessageBox.Show("No parameters selected. Tick the checkboxes first.",
                            "Info", MessageBoxButton.OK, MessageBoxImage.Information)
            return

        new_cats = self._get_checked_edit_categories()
        if not new_cats:
            if self.categoryList:
                for item in self.categoryList.Items:
                    if isinstance(item, WpfCheckBox) and item.IsChecked:
                        new_cats.append(item.Tag)
        if not new_cats:
            MessageBox.Show("Please select categories to assign.",
                            "Error", MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        result = MessageBox.Show(
            "Replace categories for {} parameter(s)?".format(len(selected)),
            "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result == MessageBoxResult.Yes:
            self._apply_categories_to_params(selected, new_cats, merge=False)

    def OnAddCategoriesToSelected(self, sender, e):
        selected = self._get_selected_params()
        if not selected:
            MessageBox.Show("No parameters selected.", "Info",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            return
        new_cats = self._get_checked_edit_categories()
        if not new_cats:
            MessageBox.Show("No categories checked in the panel.", "Error",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        result = MessageBox.Show(
            "ADD {} categorie(s) to {} parameter(s)?\n"
            "(Existing categories will be kept)".format(
                len(new_cats), len(selected)),
            "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result == MessageBoxResult.Yes:
            self._apply_categories_to_params(selected, new_cats, merge=True)

    def OnRemoveCategoriesFromSelected(self, sender, e):
        selected = self._get_selected_params()
        if not selected:
            MessageBox.Show("No parameters selected.", "Info",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            return
        cats_to_remove = self._get_checked_edit_categories()
        if not cats_to_remove:
            MessageBox.Show("No categories checked to remove.", "Error",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        remove_names = set(c.Name for c in cats_to_remove)
        result = MessageBox.Show(
            "REMOVE {} categorie(s) from {} parameter(s)?".format(
                len(cats_to_remove), len(selected)),
            "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result != MessageBoxResult.Yes:
            return

        t = Transaction(doc, "Remove Parameter Categories")
        t.Start()
        try:
            modified = 0
            errors   = []
            for p in selected:
                if not p.Definition or not p.Binding:
                    continue
                try:
                    cat_set = CategorySet()
                    if p.Binding.Categories:
                        for existing_cat in p.Binding.Categories:
                            if existing_cat.Name not in remove_names:
                                cat_set.Insert(existing_cat)
                    if cat_set.Size == 0:
                        errors.append("{}: must keep at least one category".format(p.Name))
                        continue
                    new_binding = (InstanceBinding(cat_set) if p.IsInstance
                                   else TypeBinding(cat_set))
                    doc.ParameterBindings.ReInsert(p.Definition, new_binding)
                    modified += 1
                except Exception as ex:
                    errors.append("{}: {}".format(p.Name, str(ex)))
            t.Commit()
            msg = "Modified {} parameter(s).".format(modified)
            if errors:
                msg += "\n\nSkipped:\n" + "\n".join(errors)
            MessageBox.Show(msg, "Done",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            self._load_parameters()
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Show current categories ───────────────────────────────────────────────

    def OnShowParamCategories(self, sender, e):
        selected = self._get_selected_params()
        if not selected:
            MessageBox.Show(
                "No parameters selected. Tick the checkboxes in Project Parameters tab.",
                "Info", MessageBoxButton.OK, MessageBoxImage.Information)
            return

        if len(selected) == 1:
            p = selected[0]
            current_cat_names = set()
            if p.Binding and p.Binding.Categories:
                for cat in p.Binding.Categories:
                    current_cat_names.add(cat.Name)
            if self.editCategoryList:
                for item in self.editCategoryList.Items:
                    if isinstance(item, WpfCheckBox):
                        item.IsChecked = (str(item.Content) in current_cat_names)
            self._refresh_edit_cat_count()
            self._update_status(u"Loaded categories for: {}  ({} assigned)".format(
                p.Name, len(current_cat_names)))
        else:
            all_sets = []
            for p in selected:
                cats = set()
                if p.Binding and p.Binding.Categories:
                    for cat in p.Binding.Categories:
                        cats.add(cat.Name)
                all_sets.append(cats)
            common = set.intersection(*all_sets) if all_sets else set()
            if self.editCategoryList:
                for item in self.editCategoryList.Items:
                    if isinstance(item, WpfCheckBox):
                        item.IsChecked = (str(item.Content) in common)
            self._refresh_edit_cat_count()
            self._update_status(
                u"Showing COMMON categories for {} params ({} common)".format(
                    len(selected), len(common)))

    # ── View Categories Dialog ────────────────────────────────────────────────

    def OnViewCategoriesDialog(self, sender, e):
        try:
            selected = self._get_selected_params()
            if not selected:
                MessageBox.Show(
                    "No parameters selected. Tick the checkboxes first.",
                    "Info", MessageBoxButton.OK, MessageBoxImage.Information)
                return

            lines = []
            for p in selected:
                cat_list = []
                if p.Binding and p.Binding.Categories:
                    for cat in p.Binding.Categories:
                        cat_list.append(cat.Name)
                cat_list.sort()

                lines.append(u"=== {} ===".format(p.Name))
                lines.append(u"Type: {}   |   Group: {}   |   {}".format(
                    p.ParameterType, p.ParameterGroup,
                    "Instance" if p.IsInstance else "Type"))
                lines.append(u"Total: {} categorie(s)".format(len(cat_list)))
                lines.append("")
                for cn in cat_list:
                    lines.append(u"  - " + cn)
                lines.append("")
                lines.append("")

            message = "\n".join(lines)
            self._show_scrollable_message(
                "Categories for {} parameter(s)".format(len(selected)),
                message)

        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def _show_scrollable_message(self, title, message):
        try:
            win                       = Window()
            win.Title                 = title
            win.Width                 = 520
            win.Height                = 600
            win.WindowStartupLocation = WindowStartupLocation.CenterOwner
            win.Owner                 = self

            outer        = WpfStackPanel()
            outer.Margin = Thickness(10)

            sv        = WpfScrollViewer()
            sv.Height = 500
            sv.VerticalScrollBarVisibility = ScrollBarVisibility.Visible

            tb              = WpfTextBlock()
            tb.Text         = message
            tb.TextWrapping = TextWrapping.Wrap
            tb.FontSize     = 12
            tb.Padding      = Thickness(8)

            sv.Content = tb
            outer.Children.Add(sv)

            close_btn                     = WpfButton()
            close_btn.Content             = "Close"
            close_btn.Width               = 100
            close_btn.Height              = 32
            close_btn.Margin              = Thickness(0, 10, 0, 0)
            close_btn.HorizontalAlignment = HorizontalAlignment.Right
            close_btn.Cursor              = Cursors.Hand

            def _close(sender, e):
                win.Close()

            close_btn.Click += _close
            outer.Children.Add(close_btn)

            win.Content = outer
            win.ShowDialog()
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Toggle Instance / Type ────────────────────────────────────────────────

    def OnToggleInstanceType(self, sender, e):
        selected = self._get_selected_params()
        if not selected:
            MessageBox.Show("No parameters selected. Tick the checkboxes first.",
                            "Info", MessageBoxButton.OK, MessageBoxImage.Information)
            return
        t = Transaction(doc, "Toggle Instance/Type")
        t.Start()
        try:
            toggled = 0
            for p in selected:
                if p.Definition and p.Binding:
                    cat_set = CategorySet()
                    if p.Binding.Categories:
                        for cat in p.Binding.Categories:
                            cat_set.Insert(cat)
                    new_binding = (TypeBinding(cat_set) if p.IsInstance
                                   else InstanceBinding(cat_set))
                    doc.ParameterBindings.ReInsert(p.Definition, new_binding)
                    toggled += 1
            t.Commit()
            MessageBox.Show("Toggled {} parameter(s).".format(toggled),
                            "Success", MessageBoxButton.OK, MessageBoxImage.Information)
            self._load_parameters()
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Edit Parameter Dialog ─────────────────────────────────────────────────

    def OnEditParameter(self, sender, e):
        selected = self._get_selected_params()
        if not selected:
            MessageBox.Show("No parameters selected. Tick the checkboxes first.",
                            "Info", MessageBoxButton.OK, MessageBoxImage.Information)
            return
        if len(selected) == 1:
            self._open_edit_dialog(selected[0])
        else:
            MessageBox.Show(
                "{} parameters selected.\n\n"
                "Edit Dialog works on one parameter at a time.\n"
                "For bulk changes use the Edit Categories tab.".format(len(selected)),
                "Info", MessageBoxButton.OK, MessageBoxImage.Information)

    def _open_edit_dialog(self, param_item):
        try:
            edit_win       = EditParameterDialog(
                param_item, self.all_categories,
                self.category_presets, self.param_groups,
                self.param_types)
            edit_win.Owner = self
            if edit_win.ShowDialog():
                if edit_win.Result:
                    self._apply_edit_result(param_item, edit_win.Result)
        except Exception as ex:
            MessageBox.Show("Error opening edit dialog: " + str(ex),
                            "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _apply_edit_result(self, param_item, result):
        try:
            new_cats       = result['categories']
            is_instance    = result['is_instance']
            new_group_val  = result['group_val']
            new_group_lbl  = result['group_label']
            new_type_val   = result['type_val']
            new_type_lbl   = result['type_label']

            type_changed  = (new_type_lbl != param_item.ParameterType)
            group_changed = (new_group_lbl != param_item.ParameterGroup)

            if type_changed:
                if param_item.IsShared:
                    MessageBox.Show(
                        "Cannot change type of a SHARED parameter.\n\n"
                        "Type is defined in the shared parameter file.\n"
                        "Only categories, group and binding will be updated.",
                        "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
                    type_changed = False
                else:
                    r = MessageBox.Show(
                        "Changing the parameter TYPE requires deleting and "
                        "recreating the parameter '{}'.\n\n"
                        "WARNING: ALL existing values in the project will be LOST.\n\n"
                        "Continue?".format(param_item.Name),
                        "Confirm Type Change",
                        MessageBoxButton.YesNo, MessageBoxImage.Warning)
                    if r != MessageBoxResult.Yes:
                        type_changed = False

            if type_changed:
                self._recreate_param_with_new_type(
                    param_item, new_type_val, new_group_val,
                    is_instance, new_cats)
                return

            t = Transaction(doc, "Edit Parameter")
            t.Start()
            try:
                cat_set = CategorySet()
                for cat in new_cats:
                    cat_set.Insert(cat)

                new_binding = (InstanceBinding(cat_set) if is_instance
                               else TypeBinding(cat_set))

                doc.ParameterBindings.Remove(param_item.Definition)
                doc.ParameterBindings.Insert(
                    param_item.Definition, new_binding, new_group_val)

                t.Commit()

                changes = []
                if group_changed:
                    changes.append(u"Group: {} -> {}".format(
                        param_item.ParameterGroup, new_group_lbl))
                changes.append("Categories updated ({})".format(len(new_cats)))
                changes.append("Binding: {}".format(
                    "Instance" if is_instance else "Type"))

                MessageBox.Show(
                    u"Parameter '{}' updated:\n\n{}".format(
                        param_item.Name, "\n".join(changes)),
                    "Success",
                    MessageBoxButton.OK, MessageBoxImage.Information)
                self._load_parameters()
            except Exception as ex:
                t.RollBack()
                MessageBox.Show("Error updating parameter: " + str(ex), "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)

        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def _recreate_param_with_new_type(self, param_item, new_type_val,
                                      new_group_val, is_instance, categories):
        import tempfile

        name              = param_item.Name
        original_spf_path = None

        try:
            original_spf = app.OpenSharedParameterFile()
            if original_spf and hasattr(original_spf, 'Filename'):
                original_spf_path = original_spf.Filename
        except:
            pass

        temp_path = os.path.join(tempfile.gettempdir(), "pyrevit_pm_temp.txt")

        try:
            t1 = Transaction(doc, "Remove Old Parameter")
            t1.Start()
            try:
                doc.ParameterBindings.Remove(param_item.Definition)
                t1.Commit()
            except Exception as ex:
                t1.RollBack()
                raise Exception("Failed to remove old parameter: " + str(ex))

            with open(temp_path, 'w') as f:
                f.write("# Temporary shared parameter file\n")
                f.write("*META\tVERSION\tMINVERSION\n")
                f.write("META\t2\t1\n")
                f.write("*GROUP\tID\tNAME\n")
                f.write("*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY"
                        "\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\n")

            app.SharedParametersFilename = temp_path
            temp_spf = app.OpenSharedParameterFile()
            if temp_spf is None:
                raise Exception("Could not create temporary shared parameter file")

            temp_group = None
            for g in temp_spf.Groups:
                if g.Name == "TempGroup":
                    temp_group = g
                    break
            if temp_group is None:
                temp_group = temp_spf.Groups.Create("TempGroup")

            opts         = ExternalDefinitionCreationOptions(name, new_type_val)
            opts.Visible = True
            ext_def      = temp_group.Definitions.Create(opts)

            t2 = Transaction(doc, "Add New Parameter with New Type")
            t2.Start()
            try:
                cat_set = CategorySet()
                for cat in categories:
                    cat_set.Insert(cat)

                new_binding = (InstanceBinding(cat_set) if is_instance
                               else TypeBinding(cat_set))

                if new_group_val is None:
                    if REVIT_VERSION >= 2022:
                        try:
                            new_group_val = GroupTypeId.General
                        except:
                            new_group_val = BuiltInParameterGroup.PG_GENERAL
                    else:
                        new_group_val = BuiltInParameterGroup.PG_GENERAL

                doc.ParameterBindings.Insert(ext_def, new_binding, new_group_val)
                t2.Commit()

                MessageBox.Show(
                    u"Parameter '{}' recreated with new type.\n\n"
                    u"Note: Previous values were cleared.".format(name),
                    "Success",
                    MessageBoxButton.OK, MessageBoxImage.Information)
                self._load_parameters()
            except Exception as ex:
                t2.RollBack()
                raise ex

        finally:
            if original_spf_path:
                try:
                    app.SharedParametersFilename = original_spf_path
                except:
                    pass
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass

    # ── Category Presets ──────────────────────────────────────────────────────

    def OnSavePreset(self, sender, e):
        try:
            preset_name = (self.presetNameBox.Text.strip()
                           if self.presetNameBox else "")
            if not preset_name:
                MessageBox.Show("Please enter a preset name.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            selected_cats = [
                str(item.Content)
                for item in self.editCategoryList.Items
                if isinstance(item, WpfCheckBox) and bool(item.IsChecked)]
            if not selected_cats:
                MessageBox.Show("No categories selected in the panel.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            if preset_name in self.category_presets:
                r = MessageBox.Show(
                    "Preset '{}' already exists. Overwrite?".format(preset_name),
                    "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
                if r != MessageBoxResult.Yes:
                    return
            self.category_presets[preset_name] = selected_cats
            self._save_category_presets()
            self._refresh_preset_combo()
            if self.presetCombo:
                for i in range(self.presetCombo.Items.Count):
                    if str(self.presetCombo.Items[i]) == preset_name:
                        self.presetCombo.SelectedIndex = i
                        break
            if self.presetNameBox:
                self.presetNameBox.Text = ""
            self._update_status(u"Preset '{}' saved ({} categories)".format(
                preset_name, len(selected_cats)))
            MessageBox.Show(
                "Preset '{}' saved with {} categories.".format(
                    preset_name, len(selected_cats)),
                "Success", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            MessageBox.Show("Error saving preset: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def OnLoadPreset(self, sender, e):
        try:
            if not self.presetCombo or self.presetCombo.SelectedIndex <= 0:
                MessageBox.Show("Please select a preset.", "Info",
                                MessageBoxButton.OK, MessageBoxImage.Information)
                return
            preset_name = str(self.presetCombo.SelectedItem)
            if preset_name not in self.category_presets:
                return
            preset_cats = set(self.category_presets[preset_name])
            if self.editCategoryList:
                for item in self.editCategoryList.Items:
                    if isinstance(item, WpfCheckBox):
                        item.IsChecked = (str(item.Content) in preset_cats)
            self._refresh_edit_cat_count()
            self._update_status(u"Preset '{}' loaded".format(preset_name))
        except Exception as ex:
            MessageBox.Show("Error loading preset: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def OnDeletePreset(self, sender, e):
        try:
            if not self.presetCombo or self.presetCombo.SelectedIndex <= 0:
                MessageBox.Show("Please select a preset to delete.", "Info",
                                MessageBoxButton.OK, MessageBoxImage.Information)
                return
            preset_name = str(self.presetCombo.SelectedItem)
            result = MessageBox.Show(
                "Delete preset '{}'?".format(preset_name),
                "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
            if result == MessageBoxResult.Yes:
                del self.category_presets[preset_name]
                self._save_category_presets()
                self._refresh_preset_combo()
                self._update_status(u"Preset '{}' deleted".format(preset_name))
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def OnApplyPresetToSelected(self, sender, e):
        try:
            if not self.presetCombo or self.presetCombo.SelectedIndex <= 0:
                MessageBox.Show("Please select a preset.", "Info",
                                MessageBoxButton.OK, MessageBoxImage.Information)
                return
            preset_name = str(self.presetCombo.SelectedItem)
            if preset_name not in self.category_presets:
                return
            preset_cat_names = self.category_presets[preset_name]
            cats = [c for c in self.all_categories if c.Name in preset_cat_names]
            if not cats:
                MessageBox.Show("No matching categories found.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            selected_params = self._get_selected_params()
            if not selected_params:
                MessageBox.Show(
                    "No parameters selected. Tick checkboxes in Project Parameters tab.",
                    "Info", MessageBoxButton.OK, MessageBoxImage.Information)
                return
            result = MessageBox.Show(
                "Apply preset '{}' ({} categories) to {} parameter(s)?\n"
                "(This will REPLACE the current categories)".format(
                    preset_name, len(cats), len(selected_params)),
                "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
            if result == MessageBoxResult.Yes:
                self._apply_categories_to_params(selected_params, cats, merge=False)
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Shared Parameter File ─────────────────────────────────────────────────

    def OnBrowseSharedFile(self, sender, e):
        dlg        = OpenFileDialog()
        dlg.Filter = "Shared Parameter Files (*.txt)|*.txt|All Files (*.*)|*.*"
        dlg.Title  = "Select Shared Parameter File"
        if dlg.ShowDialog():
            try:
                app.SharedParametersFilename = dlg.FileName
                self._load_shared_param_file()
                self._update_status("Loaded: " + dlg.FileName)
            except Exception as ex:
                MessageBox.Show("Error: " + str(ex), "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)

    def OnCreateSharedFile(self, sender, e):
        dlg          = SaveFileDialog()
        dlg.Filter   = "Shared Parameter Files (*.txt)|*.txt"
        dlg.Title    = "Create Shared Parameter File"
        dlg.FileName = "SharedParameters.txt"
        if dlg.ShowDialog():
            try:
                with open(dlg.FileName, 'w') as f:
                    f.write("# This is a Revit shared parameter file.\n"
                            "*META\tVERSION\tMINVERSION\n"
                            "META\t2\t1\n"
                            "*GROUP\tID\tNAME\n"
                            "*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY"
                            "\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\n")
                app.SharedParametersFilename = dlg.FileName
                self._load_shared_param_file()
                self._update_status("Created: " + dlg.FileName)
            except Exception as ex:
                MessageBox.Show("Error: " + str(ex), "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)

    def OnAddSharedGroup(self, sender, e):
        try:
            group_name = (self.newSharedGroupBox.Text.strip()
                          if self.newSharedGroupBox else "")
            if not group_name:
                MessageBox.Show("Please enter a group name.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            spf = app.OpenSharedParameterFile()
            if spf is None:
                MessageBox.Show("No shared parameter file loaded.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)
                return
            for g in spf.Groups:
                if g.Name == group_name:
                    MessageBox.Show("Group '{}' already exists.".format(group_name),
                                    "Error", MessageBoxButton.OK, MessageBoxImage.Warning)
                    return
            spf.Groups.Create(group_name)
            self._load_shared_param_file()
            self._update_status("Group '{}' created".format(group_name))
            self.newSharedGroupBox.Text = ""
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def OnAddSharedParam(self, sender, e):
        try:
            name = (self.newSharedParamNameBox.Text.strip()
                    if self.newSharedParamNameBox else "")
            if not name:
                MessageBox.Show("Please enter a parameter name.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            spf = app.OpenSharedParameterFile()
            if spf is None:
                MessageBox.Show("No shared parameter file loaded.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)
                return
            group_name = (str(self.sharedGroupCombo.SelectedItem)
                          if (self.sharedGroupCombo and self.sharedGroupCombo.SelectedItem)
                          else "")
            if not group_name:
                MessageBox.Show("Please select a group.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            target_group = None
            for g in spf.Groups:
                if g.Name == group_name:
                    target_group = g
                    break
            if target_group is None:
                MessageBox.Show("Group not found.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)
                return
            type_idx = (self.addParamTypeCombo.SelectedIndex
                        if self.addParamTypeCombo else 0)
            if type_idx < 0:
                type_idx = 0
            opts         = ExternalDefinitionCreationOptions(
                name, self.param_types[type_idx][1])
            desc         = (self.newSharedParamDescBox.Text.strip()
                            if self.newSharedParamDescBox else "")
            if desc:
                opts.Description = desc
            opts.Visible = True
            target_group.Definitions.Create(opts)
            self._load_shared_param_file()
            self._update_status("Shared parameter '{}' created".format(name))
            self.newSharedParamNameBox.Text = ""
            if self.newSharedParamDescBox:
                self.newSharedParamDescBox.Text = ""
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Parameter Values ──────────────────────────────────────────────────────

    def OnReadValues(self, sender, e):
        try:
            selection = uidoc.Selection.GetElementIds()
            if not selection or selection.Count == 0:
                MessageBox.Show("Please select elements in the Revit view first.",
                                "Info", MessageBoxButton.OK, MessageBoxImage.Information)
                return
            value_items = []
            for eid in selection:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                for param in elem.Parameters:
                    try:
                        pi               = ParameterItem()
                        pi.Name          = param.Definition.Name
                        pi.ParameterType = get_parameter_type_name(param.Definition)
                        pi.InternalParam = param
                        if param.HasValue:
                            pi.HasValue = True
                            storage     = param.StorageType
                            if storage == StorageType.String:
                                pi.Value = param.AsString() or ""
                            elif storage == StorageType.Integer:
                                pi.Value = str(param.AsInteger())
                            elif storage == StorageType.Double:
                                pi.Value = param.AsValueString() or str(param.AsDouble())
                            elif storage == StorageType.ElementId:
                                pi.Value = str(param.AsElementId().IntegerValue)
                            else:
                                pi.Value = param.AsValueString() or ""
                        else:
                            pi.Value    = ""
                            pi.HasValue = False
                        elem_name = ""
                        try:
                            if hasattr(elem, 'Name') and elem.Name:
                                elem_name = elem.Name
                        except:
                            pass
                        pi.Source = "{} [{}]".format(
                            elem_name or "Element", eid.IntegerValue)
                        value_items.append(pi)
                    except:
                        continue
            if self.valueDataGrid:
                self.valueDataGrid.ItemsSource = None
                self.valueDataGrid.ItemsSource = value_items
            self._update_status("Read values from {} element(s)".format(selection.Count))
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def OnWriteValues(self, sender, e):
        try:
            param_name = (self.writeParamNameBox.Text.strip()
                          if self.writeParamNameBox else "")
            new_value  = (self.writeParamValueBox.Text
                          if self.writeParamValueBox else "")
            if not param_name:
                MessageBox.Show("Enter a parameter name to write to.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            selection = uidoc.Selection.GetElementIds()
            if not selection or selection.Count == 0:
                MessageBox.Show("Please select elements.", "Info",
                                MessageBoxButton.OK, MessageBoxImage.Information)
                return
            t = Transaction(doc, "Write Parameter Values")
            t.Start()
            try:
                count = 0
                for eid in selection:
                    elem = doc.GetElement(eid)
                    if elem is None:
                        continue
                    param = elem.LookupParameter(param_name)
                    if param and not param.IsReadOnly:
                        storage = param.StorageType
                        try:
                            if storage == StorageType.String:
                                param.Set(new_value); count += 1
                            elif storage == StorageType.Integer:
                                param.Set(int(new_value)); count += 1
                            elif storage == StorageType.Double:
                                param.Set(float(new_value)); count += 1
                            elif storage == StorageType.ElementId:
                                param.Set(ElementId(int(new_value))); count += 1
                        except:
                            pass
                t.Commit()
                MessageBox.Show("Updated {} element(s).".format(count), "Success",
                                MessageBoxButton.OK, MessageBoxImage.Information)
                self._update_status("Written values to {} elements".format(count))
            except Exception as ex:
                t.RollBack()
                raise ex
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Export / Import ───────────────────────────────────────────────────────

    def OnExportCSV(self, sender, e):
        dlg          = SaveFileDialog()
        dlg.Filter   = "CSV Files (*.csv)|*.csv"
        dlg.Title    = "Export Parameters to CSV"
        dlg.FileName = "Parameters.csv"
        if dlg.ShowDialog():
            try:
                with open(dlg.FileName, 'w') as f:
                    f.write("Name,Type,Group,Instance/Type,Source,GUID,Categories\n")
                    for p in self.filtered_parameters:
                        f.write('{},{},{},{},{},{},{}\n'.format(
                            p.Name.replace(",", ";"),
                            p.ParameterType, p.ParameterGroup,
                            "Instance" if p.IsInstance else "Type",
                            p.Source, p.GUID,
                            p.Categories.replace(",", ";")))
                self._update_status("Exported to: " + dlg.FileName)
                MessageBox.Show("Exported {} parameters.".format(
                    len(self.filtered_parameters)),
                    "Success", MessageBoxButton.OK, MessageBoxImage.Information)
            except Exception as ex:
                MessageBox.Show("Error: " + str(ex), "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)

    def OnExportJSON(self, sender, e):
        dlg          = SaveFileDialog()
        dlg.Filter   = "JSON Files (*.json)|*.json"
        dlg.Title    = "Export Parameters to JSON"
        dlg.FileName = "Parameters.json"
        if dlg.ShowDialog():
            try:
                data = [{"Name": p.Name, "Type": p.ParameterType,
                         "Group": p.ParameterGroup,
                         "IsInstance": p.IsInstance, "Source": p.Source,
                         "GUID": p.GUID, "Categories": p.Categories}
                        for p in self.filtered_parameters]
                with open(dlg.FileName, 'w') as f:
                    json.dump(data, f, indent=2)
                self._update_status("Exported to: " + dlg.FileName)
                MessageBox.Show("Exported {} parameters.".format(len(data)),
                                "Success", MessageBoxButton.OK,
                                MessageBoxImage.Information)
            except Exception as ex:
                MessageBox.Show("Error: " + str(ex), "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)

    def OnImportCSV(self, sender, e):
        dlg        = OpenFileDialog()
        dlg.Filter = "CSV Files (*.csv)|*.csv"
        dlg.Title  = "Import Parameters from CSV"
        if dlg.ShowDialog():
            try:
                params_to_add = []
                with open(dlg.FileName, 'r') as f:
                    lines = f.readlines()
                if len(lines) < 2:
                    MessageBox.Show("CSV file is empty.", "Error",
                                    MessageBoxButton.OK, MessageBoxImage.Warning)
                    return
                for line in lines[1:]:
                    parts = line.strip().split(',')
                    if len(parts) >= 4:
                        params_to_add.append({
                            'Name': parts[0].strip(),
                            'Type': parts[1].strip() if len(parts) > 1 else 'Text',
                            'Group': parts[2].strip() if len(parts) > 2 else 'General',
                            'IsInstance': (parts[3].strip().lower() == 'instance'
                                           if len(parts) > 3 else True),
                            'Categories': (parts[6].strip().split(';')
                                           if len(parts) > 6 else [])
                        })
                if not params_to_add:
                    MessageBox.Show("No valid parameters found.", "Info",
                                    MessageBoxButton.OK, MessageBoxImage.Information)
                    return
                result = MessageBox.Show(
                    "Import {} parameters?".format(len(params_to_add)),
                    "Import", MessageBoxButton.YesNo, MessageBoxImage.Question)
                if result != MessageBoxResult.Yes:
                    return
                added  = 0
                errors = []
                for pdata in params_to_add:
                    try:
                        type_match = None
                        for label, val in self.param_types:
                            if label.lower() == pdata['Type'].lower():
                                type_match = val
                                break
                        if type_match is None:
                            for label, val in self.param_types:
                                if 'text' in label.lower():
                                    type_match = val
                                    break
                        group_match = None
                        for label, val in self.param_groups:
                            if label.lower() == pdata['Group'].lower():
                                group_match = val
                                break
                        if group_match is None:
                            group_match = self.param_groups[0][1]
                        cats = []
                        if pdata['Categories']:
                            for cn in pdata['Categories']:
                                cn = cn.strip()
                                for cat in self.all_categories:
                                    if cat.Name == cn:
                                        cats.append(cat)
                                        break
                        if not cats:
                            cats = self.all_categories[:1] if self.all_categories else []
                        if cats and type_match:
                            self._add_project_param_via_shared(
                                pdata['Name'], type_match, group_match,
                                pdata['IsInstance'], cats)
                            added += 1
                    except Exception as ex:
                        errors.append("{}: {}".format(pdata['Name'], str(ex)))
                msg = "Imported {} parameter(s).".format(added)
                if errors:
                    msg += "\n\nErrors:\n" + "\n".join(errors[:10])
                MessageBox.Show(msg, "Import Results",
                                MessageBoxButton.OK, MessageBoxImage.Information)
                self._load_parameters()
            except Exception as ex:
                MessageBox.Show("Error: " + str(ex), "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)

    # ── Purge Unused ──────────────────────────────────────────────────────────

    def OnPurgeUnused(self, sender, e):
        try:
            unused = []
            for p in self.parameters:
                if not p.Definition:
                    continue
                has_usage = False
                if p.Binding and p.Binding.Categories:
                    for cat in p.Binding.Categories:
                        try:
                            collector = (FilteredElementCollector(doc)
                                         .OfCategoryId(cat.Id)
                                         .WhereElementIsNotElementType())
                            for elem in collector:
                                param = elem.LookupParameter(p.Name)
                                if param and param.HasValue:
                                    val = param.AsValueString() or param.AsString()
                                    if val and val.strip():
                                        has_usage = True
                                        break
                            if has_usage:
                                break
                        except:
                            continue
                if not has_usage:
                    unused.append(p)
            if not unused:
                MessageBox.Show("No unused parameters found.", "Info",
                                MessageBoxButton.OK, MessageBoxImage.Information)
                return
            names = "\n".join(["  - " + p.Name for p in unused[:20]])
            if len(unused) > 20:
                names += "\n  ... and {} more".format(len(unused) - 20)
            result = MessageBox.Show(
                "Found {} unused parameter(s):\n\n{}\n\nDelete them?".format(
                    len(unused), names),
                "Purge Unused", MessageBoxButton.YesNo, MessageBoxImage.Question)
            if result == MessageBoxResult.Yes:
                t = Transaction(doc, "Purge Unused Parameters")
                t.Start()
                try:
                    count = 0
                    for p in unused:
                        try:
                            doc.ParameterBindings.Remove(p.Definition)
                            count += 1
                        except:
                            pass
                    t.Commit()
                    MessageBox.Show("Purged {} parameters.".format(count),
                                    "Success", MessageBoxButton.OK,
                                    MessageBoxImage.Information)
                    self._load_parameters()
                except:
                    t.RollBack()
                    raise
        except Exception as ex:
            MessageBox.Show("Error: " + str(ex), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def OnClose(self, sender, e):
        self.Close()


# ─── Entry point ──────────────────────────────────────────────────────────────

try:
    window = ParameterManagerWindow()
    window.ShowDialog()
except Exception as ex:
    forms.alert("Error launching Parameter Manager:\n\n" + str(ex),
                title="Error")