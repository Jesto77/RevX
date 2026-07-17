# -*- coding: utf-8 -*-
"""Copy State - Debug color saving."""

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('RevitServices')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *

clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from System.Windows.Forms import (
    Form, CheckedListBox, Button, Label, 
    DialogResult, MessageBox, MessageBoxButtons, 
    MessageBoxIcon, FormBorderStyle, FormStartPosition
)
from System.Drawing import Point, Size, Color, Font, FontStyle

import json
import os

app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.path.join(PANEL_DIR, "filter_data")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

TEMP_FILE = os.path.join(DATA_DIR, "filter_state.json")

print("\n" + "="*80)
print("COPY FILTER STATE - COLOR DEBUG")
print("="*80)

def safe_int(value, default=-1):
    try: return int(value)
    except: return default

def get_element_id_value(element_id):
    if element_id is None or element_id == ElementId.InvalidElementId:
        return -1
    try: return element_id.IntegerValue
    except:
        try: return int(str(element_id))
        except: return -1

def debug_color(color, name):
    """Debug color extraction."""
    print("    {} Color object: {}".format(name, color))
    if color is None:
        print("      -> Is None")
        return None
    try:
        print("      -> IsValid: {}".format(color.IsValid))
        if color.IsValid:
            print("      -> Red: {}".format(color.Red))
            print("      -> Green: {}".format(color.Green))
            print("      -> Blue: {}".format(color.Blue))
            return {
                'red': int(color.Red),
                'green': int(color.Green),
                'blue': int(color.Blue)
            }
    except Exception as e:
        print("      -> Error: {}".format(str(e)))
    return None

def get_all_overrides(view, filter_id, filter_name):
    """Get ALL overrides with detailed color debugging."""
    override_dict = {}
    
    try:
        overrides = view.GetFilterOverrides(filter_id)
        
        print("\n  Filter: '{}'".format(filter_name))
        
        # Cut Line
        try:
            override_dict['CutLineWeight'] = safe_int(overrides.CutLineWeight)
            print("    CutLineWeight: {}".format(override_dict['CutLineWeight']))
        except: pass
        
        color_dict = debug_color(overrides.CutLineColor, "CutLine")
        if color_dict:
            override_dict['CutLineColor'] = color_dict
        
        try:
            override_dict['CutLinePatternId'] = get_element_id_value(overrides.CutLinePatternId)
            print("    CutLinePatternId: {}".format(override_dict['CutLinePatternId']))
        except: pass
        
        # Projection Line
        try:
            override_dict['ProjectionLineWeight'] = safe_int(overrides.ProjectionLineWeight)
            print("    ProjectionLineWeight: {}".format(override_dict['ProjectionLineWeight']))
        except: pass
        
        color_dict = debug_color(overrides.ProjectionLineColor, "ProjectionLine")
        if color_dict:
            override_dict['ProjectionLineColor'] = color_dict
        
        try:
            override_dict['ProjectionLinePatternId'] = get_element_id_value(overrides.ProjectionLinePatternId)
            print("    ProjectionLinePatternId: {}".format(override_dict['ProjectionLinePatternId']))
        except: pass
        
        # Cut Foreground
        try:
            override_dict['CutForegroundPatternId'] = get_element_id_value(overrides.CutForegroundPatternId)
            print("    CutForegroundPatternId: {}".format(override_dict['CutForegroundPatternId']))
        except: pass
        
        color_dict = debug_color(overrides.CutForegroundPatternColor, "CutForegroundPattern")
        if color_dict:
            override_dict['CutForegroundPatternColor'] = color_dict
        
        # Cut Background
        try:
            override_dict['CutBackgroundPatternId'] = get_element_id_value(overrides.CutBackgroundPatternId)
            print("    CutBackgroundPatternId: {}".format(override_dict['CutBackgroundPatternId']))
        except: pass
        
        color_dict = debug_color(overrides.CutBackgroundPatternColor, "CutBackgroundPattern")
        if color_dict:
            override_dict['CutBackgroundPatternColor'] = color_dict
        
        # Surface Foreground
        try:
            override_dict['SurfaceForegroundPatternId'] = get_element_id_value(overrides.SurfaceForegroundPatternId)
            print("    SurfaceForegroundPatternId: {}".format(override_dict['SurfaceForegroundPatternId']))
            if override_dict['SurfaceForegroundPatternId'] > 0:
                elem = doc.GetElement(overrides.SurfaceForegroundPatternId)
                if elem:
                    print("      -> Pattern Name: '{}'".format(elem.Name))
        except: pass
        
        color_dict = debug_color(overrides.SurfaceForegroundPatternColor, "SurfaceForegroundPattern")
        if color_dict:
            override_dict['SurfaceForegroundPatternColor'] = color_dict
        
        # Surface Background
        try:
            override_dict['SurfaceBackgroundPatternId'] = get_element_id_value(overrides.SurfaceBackgroundPatternId)
            print("    SurfaceBackgroundPatternId: {}".format(override_dict['SurfaceBackgroundPatternId']))
        except: pass
        
        color_dict = debug_color(overrides.SurfaceBackgroundPatternColor, "SurfaceBackgroundPattern")
        if color_dict:
            override_dict['SurfaceBackgroundPatternColor'] = color_dict
        
        # Transparency
        try:
            override_dict['Transparency'] = safe_int(overrides.Transparency)
            print("    Transparency: {}".format(override_dict['Transparency']))
        except: pass
        
        # Halftone
        try:
            override_dict['Halftone'] = bool(overrides.Halftone)
            print("    Halftone: {}".format(override_dict['Halftone']))
        except: pass
        
        # Visibility
        try:
            override_dict['IsSurfaceForegroundPatternVisible'] = bool(overrides.IsSurfaceForegroundPatternVisible)
            override_dict['IsCutForegroundPatternVisible'] = bool(overrides.IsCutForegroundPatternVisible)
        except: pass
        
    except Exception as e:
        print("  Error: {}".format(str(e)))
    
    return override_dict

class FilterSelectionForm(Form):
    def __init__(self, filters_list, view_name, doc_name, template_info):
        self.filters_list = filters_list
        self.selected_filters = []
        
        self.Text = "Copy Filter State"
        self.Width = 650
        self.Height = 550
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.MinimizeBox = False
        
        y_pos = 15
        
        lbl = Label()
        lbl.Text = "Source View: {}".format(view_name)
        lbl.Location = Point(15, y_pos)
        lbl.Size = Size(610, 25)
        lbl.Font = Font(lbl.Font, FontStyle.Bold)
        self.Controls.Add(lbl)
        y_pos += 28
        
        lbl = Label()
        lbl.Text = "Document: {}".format(doc_name)
        lbl.Location = Point(15, y_pos)
        lbl.Size = Size(610, 25)
        self.Controls.Add(lbl)
        y_pos += 28
        
        if template_info:
            lbl = Label()
            lbl.Text = template_info
            lbl.Location = Point(15, y_pos)
            lbl.Size = Size(610, 25)
            lbl.ForeColor = Color.Blue
            self.Controls.Add(lbl)
            y_pos += 35
        
        lbl = Label()
        lbl.Text = "Found {} filters - Select to copy:".format(len(self.filters_list))
        lbl.Location = Point(15, y_pos)
        lbl.Size = Size(610, 25)
        self.Controls.Add(lbl)
        y_pos += 30
        
        self.checked_list = CheckedListBox()
        self.checked_list.Location = Point(15, y_pos)
        self.checked_list.Size = Size(610, 310)
        self.checked_list.CheckOnClick = True
        
        for filter_info in self.filters_list:
            status = "ON" if filter_info['is_enabled'] else "OFF"
            text = "{} [{}]".format(filter_info['name'], status)
            self.checked_list.Items.Add(text, True)
        
        self.Controls.Add(self.checked_list)
        y_pos += 320
        
        btn = Button()
        btn.Text = "Select All"
        btn.Location = Point(15, y_pos)
        btn.Size = Size(120, 35)
        btn.Click += self.SelectAll_Click
        self.Controls.Add(btn)
        
        btn = Button()
        btn.Text = "Deselect All"
        btn.Location = Point(145, y_pos)
        btn.Size = Size(120, 35)
        btn.Click += self.DeselectAll_Click
        self.Controls.Add(btn)
        
        btn = Button()
        btn.Text = "COPY STATE"
        btn.Location = Point(390, y_pos)
        btn.Size = Size(120, 50)
        btn.BackColor = Color.FromArgb(52, 152, 219)
        btn.ForeColor = Color.White
        btn.Font = Font(btn.Font, FontStyle.Bold)
        btn.Click += self.Copy_Click
        self.Controls.Add(btn)
        
        btn = Button()
        btn.Text = "Cancel"
        btn.Location = Point(520, y_pos)
        btn.Size = Size(105, 50)
        btn.Click += self.Cancel_Click
        self.Controls.Add(btn)
    
    def SelectAll_Click(self, sender, e):
        for i in range(self.checked_list.Items.Count):
            self.checked_list.SetItemChecked(i, True)
    
    def DeselectAll_Click(self, sender, e):
        for i in range(self.checked_list.Items.Count):
            self.checked_list.SetItemChecked(i, False)
    
    def Copy_Click(self, sender, e):
        self.selected_filters = []
        for i in range(self.checked_list.Items.Count):
            if self.checked_list.GetItemChecked(i):
                self.selected_filters.append(self.filters_list[i])
        
        if not self.selected_filters:
            MessageBox.Show("Please select at least one filter.", "No Selection", MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        
        self.DialogResult = DialogResult.OK
        self.Close()
    
    def Cancel_Click(self, sender, e):
        self.DialogResult = DialogResult.Cancel
        self.Close()

def main():
    active_view = uidoc.ActiveView
    
    if isinstance(active_view, ViewSchedule):
        MessageBox.Show("Schedules don't support filters!", "Error", MessageBoxButtons.OK, MessageBoxIcon.Warning)
        return
    
    template_info = ""
    if active_view.ViewTemplateId and active_view.ViewTemplateId != ElementId.InvalidElementId:
        try:
            template = doc.GetElement(active_view.ViewTemplateId)
            if template:
                template_info = "View Template: {}".format(template.Name)
        except: pass
    
    filter_ids = []
    try:
        view_filters = active_view.GetFilters()
        if view_filters:
            for fid in view_filters:
                if fid not in filter_ids:
                    filter_ids.append(fid)
    except: pass
    
    if not filter_ids:
        MessageBox.Show("No filters found!", "Error", MessageBoxButtons.OK, MessageBoxIcon.Information)
        return
    
    print("\nView: '{}'".format(active_view.Name))
    print("Found {} filters".format(len(filter_ids)))
    
    filters_list = []
    for fid in filter_ids:
        filter_elem = doc.GetElement(fid)
        if not filter_elem:
            continue
        
        overrides = get_all_overrides(active_view, fid, filter_elem.Name)
        
        is_enabled = True
        try: is_enabled = active_view.GetIsFilterEnabled(fid)
        except: pass
        
        filter_visible = True
        try: filter_visible = active_view.GetFilterVisibility(fid)
        except: pass
        
        overrides['is_enabled'] = bool(is_enabled)
        overrides['filter_visible'] = bool(filter_visible)
        
        filters_list.append({
            'name': str(filter_elem.Name),
            'is_enabled': bool(is_enabled),
            'overrides': overrides
        })
    
    if not filters_list:
        MessageBox.Show("Could not read filters!", "Error", MessageBoxButtons.OK, MessageBoxIcon.Error)
        return
    
    form = FilterSelectionForm(filters_list, active_view.Name, doc.Title, template_info)
    result = form.ShowDialog()
    
    if result == DialogResult.OK and form.selected_filters:
        data = {
            'source_view': str(active_view.Name),
            'source_doc': str(doc.Title),
            'template_info': str(template_info),
            'filters': form.selected_filters
        }
        
        with open(TEMP_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        
        print("\n" + "="*80)
        print("SAVED JSON:")
        print("="*80)
        print(json.dumps(data, indent=2))
        
        MessageBox.Show("Saved! Check console for JSON content.", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information)

if __name__ == '__main__':
    main()