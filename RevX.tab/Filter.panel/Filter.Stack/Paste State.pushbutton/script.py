# -*- coding: utf-8 -*-
"""Paste State - Apply ALL filter overrides to active view (Template‑aware)."""

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('RevitServices')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *

clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from System.Windows.Forms import (
    DialogResult, MessageBox, MessageBoxButtons, MessageBoxIcon
)
from System.Drawing import Color as DrawingColor

import json
import os

app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.path.join(PANEL_DIR, "filter_data")
TEMP_FILE = os.path.join(DATA_DIR, "filter_state.json")

print("\n" + "="*80)
print("PASTE FILTER STATE (TEMPLATE‑AWARE)")
print("="*80)

def safe_int(value, default=0):
    try: return int(value)
    except: return default

def safe_float(value, default=0.0):
    try: return float(value)
    except: return default

def create_color(color_dict):
    """Create a Revit Color from a dictionary (supports int 0-255 or float 0.0-1.0)."""
    if not color_dict:
        return None
    try:
        r_val = g_val = b_val = None
        for key, val in color_dict.items():
            key_low = key.lower()
            if key_low in ('r', 'red'):
                r_val = val
            elif key_low in ('g', 'green'):
                g_val = val
            elif key_low in ('b', 'blue'):
                b_val = val
        
        if None in (r_val, g_val, b_val):
            print("      WARNING: Color dict missing R/G/B keys: {}".format(color_dict.keys()))
            return None
        
        if isinstance(r_val, float) or (isinstance(r_val, int) and r_val <= 1.0):
            r = int(round(safe_float(r_val) * 255))
            g = int(round(safe_float(g_val) * 255))
            b = int(round(safe_float(b_val) * 255))
        else:
            r = safe_int(r_val)
            g = safe_int(g_val)
            b = safe_int(b_val)
        
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        
        color = Color(r, g, b)
        print("      Created color: RGB({},{},{})".format(r, g, b))
        return color
    except Exception as e:
        print("      ERROR creating color: {}".format(str(e)))
        return None

def find_filter_by_name(target_doc, filter_name):
    collector = FilteredElementCollector(target_doc)
    filters = collector.OfClass(ParameterFilterElement).ToElements()
    for f in filters:
        if f.Name == filter_name:
            return f
        if f.Name.lower() == filter_name.lower():
            return f
    return None

def apply_all_overrides(target, filter_elem, override_data):
    """
    Apply all overrides to a View OR ViewTemplate.
    target can be a View or a ViewTemplate.
    """
    try:
        # Add filter if not already present
        existing_filters = list(target.GetFilters())
        if filter_elem.Id not in existing_filters:
            target.AddFilter(filter_elem.Id)
        
        # Enable / visibility
        target.SetIsFilterEnabled(filter_elem.Id, override_data.get('is_enabled', True))
        try:
            target.SetFilterVisibility(filter_elem.Id, override_data.get('filter_visible', True))
        except:
            pass  # Some templates may not support visibility
        
        # Get current overrides
        overrides = target.GetFilterOverrides(filter_elem.Id)
        
        print("    Applying overrides...")
        
        # Helper for color properties
        def apply_color(prop_name, setter_method):
            if prop_name in override_data:
                color = create_color(override_data[prop_name])
                if color:
                    try:
                        setter_method(color)
                        print("      {}: Applied".format(prop_name))
                    except Exception as e:
                        print("      {} FAILED: {}".format(prop_name, str(e)))
        
        # === CUT LINE ===
        if 'CutLineWeight' in override_data:
            val = safe_int(override_data['CutLineWeight'])
            if val > 0:
                try:
                    overrides.SetCutLineWeight(val)
                    print("      CutLineWeight: {}".format(val))
                except: pass
        
        apply_color('CutLineColor', overrides.SetCutLineColor)
        
        if 'CutLinePatternId' in override_data:
            val = safe_int(override_data['CutLinePatternId'])
            if val > 0:
                try:
                    overrides.SetCutLinePatternId(ElementId(val))
                    print("      CutLinePatternId: {}".format(val))
                except: pass
        
        # === PROJECTION LINE ===
        if 'ProjectionLineWeight' in override_data:
            val = safe_int(override_data['ProjectionLineWeight'])
            if val > 0:
                try:
                    overrides.SetProjectionLineWeight(val)
                    print("      ProjectionLineWeight: {}".format(val))
                except: pass
        
        apply_color('ProjectionLineColor', overrides.SetProjectionLineColor)
        
        if 'ProjectionLinePatternId' in override_data:
            val = safe_int(override_data['ProjectionLinePatternId'])
            if val > 0:
                try:
                    overrides.SetProjectionLinePatternId(ElementId(val))
                    print("      ProjectionLinePatternId: {}".format(val))
                except: pass
        
        # === CUT FOREGROUND ===
        if 'CutForegroundPatternId' in override_data:
            val = safe_int(override_data['CutForegroundPatternId'])
            if val > 0:
                try:
                    overrides.SetCutForegroundPatternId(ElementId(val))
                    print("      CutForegroundPatternId: {}".format(val))
                except: pass
        
        apply_color('CutForegroundPatternColor', overrides.SetCutForegroundPatternColor)
        
        # === CUT BACKGROUND ===
        if 'CutBackgroundPatternId' in override_data:
            val = safe_int(override_data['CutBackgroundPatternId'])
            if val > 0:
                try:
                    overrides.SetCutBackgroundPatternId(ElementId(val))
                    print("      CutBackgroundPatternId: {}".format(val))
                except: pass
        
        apply_color('CutBackgroundPatternColor', overrides.SetCutBackgroundPatternColor)
        
        # === SURFACE FOREGROUND ===
        if 'SurfaceForegroundPatternId' in override_data:
            val = safe_int(override_data['SurfaceForegroundPatternId'])
            if val > 0:
                try:
                    overrides.SetSurfaceForegroundPatternId(ElementId(val))
                    print("      SurfaceForegroundPatternId: {}".format(val))
                except: pass
        
        apply_color('SurfaceForegroundPatternColor', overrides.SetSurfaceForegroundPatternColor)
        
        # === SURFACE BACKGROUND ===
        if 'SurfaceBackgroundPatternId' in override_data:
            val = safe_int(override_data['SurfaceBackgroundPatternId'])
            if val > 0:
                try:
                    overrides.SetSurfaceBackgroundPatternId(ElementId(val))
                    print("      SurfaceBackgroundPatternId: {}".format(val))
                except: pass
        
        apply_color('SurfaceBackgroundPatternColor', overrides.SetSurfaceBackgroundPatternColor)
        
        # === PATTERN VISIBILITY ===
        if 'IsSurfaceForegroundPatternVisible' in override_data:
            try:
                overrides.SetSurfaceForegroundPatternVisible(bool(override_data['IsSurfaceForegroundPatternVisible']))
            except: pass
        
        if 'IsCutForegroundPatternVisible' in override_data:
            try:
                overrides.SetCutForegroundPatternVisible(bool(override_data['IsCutForegroundPatternVisible']))
            except: pass
        
        if 'IsSurfaceBackgroundPatternVisible' in override_data:
            try:
                overrides.SetSurfaceBackgroundPatternVisible(bool(override_data['IsSurfaceBackgroundPatternVisible']))
            except: pass
        
        if 'IsCutBackgroundPatternVisible' in override_data:
            try:
                overrides.SetCutBackgroundPatternVisible(bool(override_data['IsCutBackgroundPatternVisible']))
            except: pass
        
        # === TRANSPARENCY ===
        if 'Transparency' in override_data:
            try:
                overrides.SetSurfaceTransparency(safe_int(override_data['Transparency']))
                print("      Transparency: {}".format(override_data['Transparency']))
            except: pass
        
        # === HALFTONE ===
        if 'Halftone' in override_data:
            try:
                overrides.SetHalftone(bool(override_data['Halftone']))
                print("      Halftone: {}".format(override_data['Halftone']))
            except: pass
        
        # === DETAIL LEVEL ===
        if 'DetailLevel' in override_data and override_data['DetailLevel'] != 'Undefined':
            try:
                dl = override_data['DetailLevel']
                if 'Coarse' in dl:
                    overrides.SetDetailLevel(ViewDetailLevel.Coarse)
                elif 'Medium' in dl:
                    overrides.SetDetailLevel(ViewDetailLevel.Medium)
                elif 'Fine' in dl:
                    overrides.SetDetailLevel(ViewDetailLevel.Fine)
                print("      DetailLevel: {}".format(dl))
            except: pass
        
        # Commit overrides
        target.SetFilterOverrides(filter_elem.Id, overrides)
        print("      ✓ Overrides applied")
        return True
        
    except Exception as e:
        print("    ✗ Error applying overrides: {}".format(str(e)))
        import traceback
        traceback.print_exc()
        return False

def main():
    if not os.path.exists(TEMP_FILE):
        MessageBox.Show("No saved state found!\n\nRun Copy State first.\n\nExpected:\n{}".format(TEMP_FILE), 
                       "Error", MessageBoxButtons.OK, MessageBoxIcon.Warning)
        return
    
    with open(TEMP_FILE, 'r') as f:
        data = json.load(f)
    
    source_view = data.get('source_view', 'Unknown')
    source_doc = data.get('source_doc', 'Unknown')
    filters_data = data.get('filters', [])
    
    if not filters_data:
        MessageBox.Show("No filters in saved state!", "Error", MessageBoxButtons.OK, MessageBoxIcon.Warning)
        return
    
    active_view = uidoc.ActiveView
    
    if isinstance(active_view, ViewSchedule):
        MessageBox.Show("Schedules don't support filters!", "Error", MessageBoxButtons.OK, MessageBoxIcon.Warning)
        return
    
    # --- Decide target (view or template) ---
    target = active_view
    target_type = "VIEW"
    template_element = None
    if active_view.ViewTemplateId and active_view.ViewTemplateId != ElementId.InvalidElementId:
        template_element = doc.GetElement(active_view.ViewTemplateId)
        if template_element:
            msg = ("The active view '{}' is controlled by View Template '{}'.\n\n"
                   "How do you want to apply the filter overrides?\n\n"
                   "• [YES]   = Detach the template and apply to THIS VIEW only.\n"
                   "• [NO]    = Apply to the VIEW TEMPLATE (affects all views using it).\n"
                   "• [CANCEL]= Do nothing.").format(active_view.Name, template_element.Name)
            result = MessageBox.Show(msg, "View Template Detected", 
                                     MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question)
            if result == DialogResult.Cancel:
                return
            elif result == DialogResult.Yes:
                # Detach template (will be done inside transaction)
                target = active_view
                target_type = "VIEW (template detached)"
            else:  # No -> modify template
                target = template_element
                target_type = "VIEW TEMPLATE"
        else:
            # Invalid template ID, ignore
            pass
    else:
        target = active_view
        target_type = "VIEW"
    
    # --- Confirm paste ---
    target_name = target.Name if hasattr(target, 'Name') else active_view.Name
    msg_confirm = "PASTE FILTER STATE\n\n"
    msg_confirm += "FROM: {} ({})\n".format(source_view, source_doc)
    msg_confirm += "TO: {} ({})\n".format(target_name, doc.Title)
    msg_confirm += "Target: {}\n\n".format(target_type)
    msg_confirm += "{} filters to paste\n\n".format(len(filters_data))
    msg_confirm += "Continue?"
    
    if MessageBox.Show(msg_confirm, "Confirm Paste", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes:
        return
    
    print("\n" + "="*80)
    print("APPLYING FILTERS TO: {}".format(target_type))
    print("="*80)
    
    successful = 0
    skipped = 0
    
    # Start transaction (handles both detach and override application)
    with Transaction(doc, "Paste Filter State") as t:
        t.Start()
        try:
            # If we chose to detach the template, do it now
            if target_type == "VIEW (template detached)" and active_view.ViewTemplateId != ElementId.InvalidElementId:
                active_view.ViewTemplateId = ElementId.InvalidElementId
                print("View template detached.")
                target = active_view  # ensure target is the view
                target_type = "VIEW"
            
            # Apply each filter
            for filter_data in filters_data:
                filter_name = filter_data.get('name', 'Unknown')
                print("\n  Filter: '{}'".format(filter_name))
                
                filter_elem = find_filter_by_name(doc, filter_name)
                if filter_elem:
                    if apply_all_overrides(target, filter_elem, filter_data.get('overrides', {})):
                        successful += 1
                else:
                    print("    SKIP - Not found in project")
                    skipped += 1
            
            t.Commit()
            print("\n" + "="*80)
            print("TRANSACTION COMMITTED SUCCESSFULLY")
            print("="*80)
        except Exception as e:
            t.RollBack()
            print("\nERROR: Transaction failed - {}".format(str(e)))
            import traceback
            traceback.print_exc()
            MessageBox.Show("Failed to apply filters.\nError: {}".format(str(e)), "Error", MessageBoxButtons.OK, MessageBoxIcon.Error)
            return
    
    # Refresh view if we modified a view (not a template)
    if target_type == "VIEW":
        try:
            uidoc.RefreshActiveView()
            print("View refreshed.")
        except:
            pass
    
    summary = "Paste Complete!\n\nApplied: {}/{} filters with ALL overrides".format(successful, len(filters_data))
    if skipped > 0:
        summary += "\n\nSkipped: {} - not found in project".format(skipped)
        summary += "\n\nUse 'Transfer Project Standards' to copy filter definitions."
    if target_type == "VIEW TEMPLATE":
        summary += "\n\nNote: Overrides were applied to the VIEW TEMPLATE. All views using this template will be affected."
    
    print("\n" + summary)
    MessageBox.Show(summary, "Complete", MessageBoxButtons.OK, MessageBoxIcon.Information)

if __name__ == '__main__':
    main()