# -*- coding: utf-8 -*-
"""
Model Health Checklist — pyRevit script
========================================
White background. One line per check. Group headers.
Whole line RED if dirty, GREEN if clean.
Each row shows: LABEL | COUNT | TARGET

Compatible: Revit 2020–2027 / pyRevit / IronPython 2.7
"""

import sys
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")

import System
from System.Windows import (
    Window, Thickness, VerticalAlignment, HorizontalAlignment,
    WindowStartupLocation, ResizeMode, FontWeights, CornerRadius,
    GridLength, GridUnitType
)
from System.Windows.Controls import (
    StackPanel, TextBlock, Button, Border, ScrollViewer, Orientation
)
from System.Windows.Media import SolidColorBrush, Color as WpfColor, Brushes

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector, View, ViewSheet, Viewport, ParameterFilterElement,
    Level, RevitLinkInstance, ImportInstance,
    LinePatternElement, Family, FamilySymbol, FamilyInstance, Group,
    ElementId, StorageType, BuiltInCategory
)

import System.Windows.Controls as WpfControls
from pyrevit import revit

doc = revit.doc
INVALID_ID = ElementId.InvalidElementId

# =============================================================================
# SAFE ID HELPER (works across all Revit versions)
# =============================================================================

def _id(elem_id):
    """Return integer value of an ElementId, safe for 2020-2026."""
    if elem_id is None or elem_id == INVALID_ID:
        return None
    try:
        return int(elem_id)
    except Exception:
        return None


# =============================================================================
# COLOURS
# =============================================================================
WHITE       = Brushes.White
TEXT_DARK   = SolidColorBrush(WpfColor.FromRgb(30, 30, 30))
TEXT_GREY   = SolidColorBrush(WpfColor.FromRgb(100, 100, 110))
HEADER_BG   = SolidColorBrush(WpfColor.FromRgb(245, 245, 245))
GREEN_BG    = SolidColorBrush(WpfColor.FromRgb(129, 199, 132))
RED_BG      = SolidColorBrush(WpfColor.FromRgb(229, 115, 115))
ROW_FG      = Brushes.White
BTN_BG      = SolidColorBrush(WpfColor.FromRgb(59, 130, 246))

# =============================================================================
# ONE-PASS DATA COLLECTION (for speed)
# =============================================================================

def _collect_data():
    data = {}

    # Family instances & types
    data['fam_instances'] = list(FilteredElementCollector(doc).OfClass(FamilyInstance))
    used_sym_ids = set()
    for inst in data['fam_instances']:
        tid = _id(inst.GetTypeId())
        if tid is not None:
            used_sym_ids.add(tid)
    data['used_sym_ids'] = used_sym_ids

    data['all_symbols'] = list(FilteredElementCollector(doc).OfClass(FamilySymbol))
    data['unused_symbols'] = [s for s in data['all_symbols'] if _id(s.Id) not in used_sym_ids]

    used_fam_ids = set()
    for s in data['all_symbols']:
        if _id(s.Id) in used_sym_ids:
            try:
                used_fam_ids.add(_id(s.Family.Id))
            except Exception:
                pass
    data['used_fam_ids'] = used_fam_ids

    data['all_families'] = list(FilteredElementCollector(doc).OfClass(Family))
    data['unused_families'] = [f for f in data['all_families'] if _id(f.Id) not in used_fam_ids]
    data['in_place_families'] = [f for f in data['all_families'] if getattr(f, 'IsInPlace', False)]

    # Generic models
    data['generic_models'] = list(FilteredElementCollector(doc).OfClass(FamilyInstance).OfCategory(BuiltInCategory.OST_GenericModel))

    # Text types
    text_notes = list(FilteredElementCollector(doc).OfClass(DB.TextNote))
    used_text_ids = set()
    for t in text_notes:
        tid = _id(t.GetTypeId())
        if tid is not None:
            used_text_ids.add(tid)
    all_text_types = list(FilteredElementCollector(doc).OfClass(DB.TextNoteType))
    data['unused_text_types'] = [tt for tt in all_text_types if _id(tt.Id) not in used_text_ids]

    # Dimension types
    dims = list(FilteredElementCollector(doc).OfClass(DB.Dimension))
    used_dim_ids = set()
    for d in dims:
        tid = _id(d.GetTypeId())
        if tid is not None:
            used_dim_ids.add(tid)
    all_dim_types = list(FilteredElementCollector(doc).OfClass(DB.DimensionType))
    data['unused_dim_types'] = [dt for dt in all_dim_types if _id(dt.Id) not in used_dim_ids]

    # Wall types
    walls = list(FilteredElementCollector(doc).OfClass(DB.Wall))
    used_wall_ids = set()
    for w in walls:
        tid = _id(w.GetTypeId())
        if tid is not None:
            used_wall_ids.add(tid)
    all_wall_types = list(FilteredElementCollector(doc).OfClass(DB.WallType))
    data['unused_wall_types'] = [wt for wt in all_wall_types if _id(wt.Id) not in used_wall_ids]

    # Floor types
    floors = list(FilteredElementCollector(doc).OfClass(DB.Floor))
    used_floor_ids = set()
    for f in floors:
        tid = _id(f.GetTypeId())
        if tid is not None:
            used_floor_ids.add(tid)
    all_floor_types = list(FilteredElementCollector(doc).OfClass(DB.FloorType))
    data['unused_floor_types'] = [ft for ft in all_floor_types if _id(ft.Id) not in used_floor_ids]

    # Roof types
    roofs = list(FilteredElementCollector(doc).OfClass(DB.RoofBase))
    used_roof_ids = set()
    for r in roofs:
        tid = _id(r.GetTypeId())
        if tid is not None:
            used_roof_ids.add(tid)
    all_roof_types = list(FilteredElementCollector(doc).OfClass(DB.RoofType))
    data['unused_roof_types'] = [rt for rt in all_roof_types if _id(rt.Id) not in used_roof_ids]

    # Ceiling types
    ceilings = list(FilteredElementCollector(doc).OfClass(DB.Ceiling))
    used_ceil_ids = set()
    for c in ceilings:
        tid = _id(c.GetTypeId())
        if tid is not None:
            used_ceil_ids.add(tid)
    all_ceil_types = list(FilteredElementCollector(doc).OfClass(DB.CeilingType))
    data['unused_ceil_types'] = [ct for ct in all_ceil_types if _id(ct.Id) not in used_ceil_ids]

    # Views & templates
    data['all_views'] = list(FilteredElementCollector(doc).OfClass(View))
    used_template_ids = set()
    for v in data['all_views']:
        try:
            tid = getattr(v, 'ViewTemplateId', INVALID_ID)
            tid_int = _id(tid)
            if tid_int is not None:
                used_template_ids.add(tid_int)
        except Exception:
            pass
    data['unused_templates'] = [v for v in data['all_views'] if getattr(v, 'IsTemplate', False) and _id(v.Id) not in used_template_ids]

    # View filters
    used_filter_ids = set()
    for v in data['all_views']:
        try:
            for fid in v.GetFilters():
                fid_int = _id(fid)
                if fid_int is not None:
                    used_filter_ids.add(fid_int)
        except Exception:
            pass
    data['all_filters'] = list(FilteredElementCollector(doc).OfClass(ParameterFilterElement))
    data['unused_filters'] = [f for f in data['all_filters'] if _id(f.Id) not in used_filter_ids]

    # Views on sheets
    data['sheets'] = list(FilteredElementCollector(doc).OfClass(ViewSheet))
    placed_view_ids = set()
    for s in data['sheets']:
        try:
            for vp_id in s.GetAllViewports():
                try:
                    vp = doc.GetElement(vp_id)
                    if vp:
                        vid = getattr(vp, 'ViewId', None)
                        vid_int = _id(vid)
                        if vid_int is not None:
                            placed_view_ids.add(vid_int)
                except Exception:
                    pass
        except Exception:
            pass
    data['orphan_views'] = [v for v in data['all_views']
                            if not getattr(v, 'IsTemplate', False)
                            and not isinstance(v, ViewSheet)
                            and _id(v.Id) not in placed_view_ids]

    # Detail groups (placed instances)
    data['detail_groups'] = list(FilteredElementCollector(doc).OfClass(Group).OfCategory(BuiltInCategory.OST_IOSDetailGroups))
    # Model groups (placed instances)
    data['model_groups_placed'] = list(FilteredElementCollector(doc).OfClass(Group).OfCategory(BuiltInCategory.OST_IOSModelGroups))

    # Unused group types
    used_model_group_type_ids = set()
    for g in data['model_groups_placed']:
        tid = _id(g.GetTypeId())
        if tid is not None:
            used_model_group_type_ids.add(tid)
    data['unused_model_group_types'] = [gt for gt in FilteredElementCollector(doc).OfClass(DB.GroupType).OfCategory(BuiltInCategory.OST_IOSModelGroups)
                                        if _id(gt.Id) not in used_model_group_type_ids]

    used_detail_group_type_ids = set()
    for g in data['detail_groups']:
        tid = _id(g.GetTypeId())
        if tid is not None:
            used_detail_group_type_ids.add(tid)
    data['unused_detail_group_types'] = [gt for gt in FilteredElementCollector(doc).OfClass(DB.GroupType).OfCategory(BuiltInCategory.OST_IOSDetailGroups)
                                         if _id(gt.Id) not in used_detail_group_type_ids]

    # Line patterns (imported)
    data['line_patterns'] = list(FilteredElementCollector(doc).OfClass(LinePatternElement))
    data['imported_patterns'] = [p for p in data['line_patterns'] if "IMPORT" in getattr(p, 'Name', '').upper()]

    # Warnings
    try:
        data['warnings'] = doc.GetWarnings()
    except Exception:
        data['warnings'] = []

    # Links
    data['cad_links'] = [i for i in FilteredElementCollector(doc).OfClass(ImportInstance) if getattr(i, 'IsLinked', False)]
    data['cad_imports'] = [i for i in FilteredElementCollector(doc).OfClass(ImportInstance) if not getattr(i, 'IsLinked', False)]
    data['revit_links'] = list(FilteredElementCollector(doc).OfClass(RevitLinkInstance))

    # Levels / Grids
    data['levels'] = list(FilteredElementCollector(doc).OfClass(Level))
    data['grids'] = list(FilteredElementCollector(doc).OfClass(DB.Grid))

    # Unpinned
    data['unpinned_levels'] = [l for l in data['levels'] if not getattr(l, 'Pinned', False)]
    data['unpinned_revit_links'] = [l for l in data['revit_links'] if not getattr(l, 'Pinned', False)]
    data['unpinned_cad_links'] = [i for i in data['cad_links'] if not getattr(i, 'Pinned', False)]

    # --- Reference map for patterns / materials / arrowheads / titleblocks ---
    referenced = set()
    def _scan_refs(elem):
        try:
            tid = _id(elem.GetTypeId())
            if tid is not None:
                referenced.add(tid)
            if hasattr(elem, 'OwnerViewId'):
                oid = _id(elem.OwnerViewId)
                if oid is not None:
                    referenced.add(oid)
            if hasattr(elem, 'LevelId'):
                lid = _id(elem.LevelId)
                if lid is not None:
                    referenced.add(lid)
            for p in elem.Parameters:
                if p.StorageType == StorageType.ElementId:
                    try:
                        val = _id(p.AsElementId())
                        if val is not None:
                            referenced.add(val)
                    except Exception:
                        pass
        except Exception:
            pass

    for elem in FilteredElementCollector(doc).WhereElementIsNotElementType():
        _scan_refs(elem)
    for elem in FilteredElementCollector(doc).WhereElementIsElementType():
        _scan_refs(elem)

    for gs in FilteredElementCollector(doc).OfClass(DB.GraphicsStyle):
        try:
            lp = gs.get_Parameter(BuiltInParameter.LINE_PATTERN)
            if lp and lp.AsElementId():
                lp_id = _id(lp.AsElementId())
                if lp_id is not None:
                    referenced.add(lp_id)
        except Exception:
            pass

    data['referenced'] = referenced

    data['unused_fill_patterns'] = [fp for fp in FilteredElementCollector(doc).OfClass(DB.FillPatternElement) if _id(fp.Id) not in referenced]
    data['unused_materials'] = [m for m in FilteredElementCollector(doc).OfClass(DB.Material) if _id(m.Id) not in referenced]
    data['unused_line_patterns'] = [lp for lp in data['line_patterns'] if _id(lp.Id) not in referenced and "IMPORT" not in getattr(lp, 'Name', '').upper()]

    # Arrowheads
    data['unused_arrowheads'] = []
    try:
        for ast in FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(BuiltInCategory.OST_GenericAnnotation):
            try:
                if _id(ast.Id) not in referenced and ast.FamilyName and 'Arrow' in ast.FamilyName:
                    data['unused_arrowheads'].append(ast)
            except Exception:
                pass
    except Exception:
        pass

    # Title blocks
    data['unused_titleblocks'] = []
    try:
        tblocks = list(FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(BuiltInCategory.OST_TitleBlocks))
        used_tblocks = set()
        for s in data['sheets']:
            tid = _id(s.GetTypeId())
            if tid is not None:
                used_tblocks.add(tid)
        data['unused_titleblocks'] = [tb for tb in tblocks if _id(tb.Id) not in used_tblocks]
    except Exception:
        pass

    return data


_DATA = _collect_data()


# =============================================================================
# HEALTH CHECKS  (fast, using cached _DATA)
# =============================================================================

def count_purgeable():
    c = (len(_DATA['unused_symbols']) + len(_DATA['unused_families']) +
         len(_DATA['unused_text_types']) + len(_DATA['unused_dim_types']) +
         len(_DATA['unused_wall_types']) + len(_DATA['unused_floor_types']) +
         len(_DATA['unused_roof_types']) + len(_DATA['unused_ceil_types']) +
         len(_DATA['unused_templates']) + len(_DATA['unused_filters']) +
         len(_DATA['unused_fill_patterns']) + len(_DATA['unused_materials']) +
         len(_DATA['unused_line_patterns']) + len(_DATA['unused_arrowheads']) +
         len(_DATA['unused_titleblocks']) + len(_DATA['unused_model_group_types']) +
         len(_DATA['unused_detail_group_types']))
    return c, "PURGE UNUSED", c > 0, "= 0"


def count_in_place_families():
    c = len(_DATA['in_place_families'])
    return c, "IN PLACE FAMILIES", c > 0, "= 0"


def count_generic_models():
    c = len(_DATA['generic_models'])
    return c, "GENERIC MODELS", c > 0, "= 0"


def count_warnings():
    c = len(_DATA['warnings'])
    return c, "WARNINGS", c >= 200, "< 200"


def count_imported_line_patterns():
    c = len(_DATA['imported_patterns'])
    return c, "IMPORTED LINE PATTERNS", c > 0, "= 0"


def count_detail_groups():
    c = len(_DATA['detail_groups'])
    return c, "DETAILS GROUPS", c > 0, "= 0"


def _off_target_workset(elem, target_name):
    try:
        ws = doc.GetWorksetTable().GetWorkset(elem.WorksetId)
        return ws.Name != target_name
    except Exception:
        return True


def count_levels_on_workset():
    target = "Shared Levels and Grids"
    c = sum(1 for lvl in _DATA['levels'] if _off_target_workset(lvl, target))
    return c, "LEVELS NOT ON Shared Levels and Grids", c > 0, "= 0"


def count_grids_on_workset():
    target = "Shared Levels and Grids"
    c = sum(1 for g in _DATA['grids'] if _off_target_workset(g, target))
    return c, "GRIDS NOT ON Shared Levels and Grids", c > 0, "= 0"


def count_revit_links_on_workset():
    target = "Shared Levels and Grids"
    c = sum(1 for lnk in _DATA['revit_links'] if _off_target_workset(lnk, target))
    return c, "REVIT LINKS NOT ON Shared Levels and Grids", c > 0, "= 0"


def count_unused_view_templates():
    c = len(_DATA['unused_templates'])
    return c, "VIEW TEMPLATES", c > 0, "= 0"


def count_unused_view_filters():
    c = len(_DATA['unused_filters'])
    return c, "VIEW FILTERS", c > 0, "= 0"


def count_views_not_on_sheets():
    c = len(_DATA['orphan_views'])
    return c, "VIEW NOT ON SHEETS", c > 0, "= 0"


def count_unpinned_levels():
    c = len(_DATA['unpinned_levels'])
    return c, "LEVELS", c > 0, "= 0"


def count_unpinned_revit_links():
    c = len(_DATA['unpinned_revit_links'])
    return c, "REVIT LINKS", c > 0, "= 0"


def count_unpinned_cad_links():
    c = len(_DATA['unpinned_cad_links'])
    return c, "CAD", c > 0, "= 0"


def count_cad_links():
    c = len(_DATA['cad_links'])
    return c, "CAD", c > 0, "= 0"


def count_revit_links_max3():
    c = len(_DATA['revit_links'])
    return c, "REVIT LINKS", c > 3, "<= 3"


def count_imported_cad():
    c = len(_DATA['cad_imports'])
    return c, "IMPORTED CAD", c > 0, "= 0"


GROUPS = [
    ("HEALTH", [
        count_in_place_families,
        count_generic_models,
        count_warnings,
        count_imported_line_patterns,
        count_purgeable,
        count_detail_groups,
    ]),
    ("WORKSETS", [
        count_levels_on_workset,
        count_grids_on_workset,
        count_revit_links_on_workset,
    ]),
    ("UNUSED", [
        count_unused_view_templates,
        count_unused_view_filters,
        count_views_not_on_sheets,
    ]),
    ("UNPINNED", [
        count_unpinned_levels,
        count_unpinned_revit_links,
        count_unpinned_cad_links,
    ]),
    ("LINKS", [
        count_cad_links,
        count_revit_links_max3,
        count_imported_cad,
    ]),
]


# =============================================================================
# WPF DIALOG
# =============================================================================
class ModelHealthDialog(Window):
    def __init__(self, groups):
        self.groups = groups
        self.all_clean = all(not is_dirty for _, rows in groups for _, _, is_dirty, _ in rows)

        self.Title = "MODEL HEALTH"
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResize
        self.MinWidth = 620
        self.MinHeight = 440
        self.Width = 820
        self.Height = 820
        self.Topmost = True
        self.Background = WHITE

        root = StackPanel()
        root.Margin = Thickness(16)

        root.Children.Add(self._top_label())
        root.Children.Add(self._column_headers())
        root.Children.Add(self._groups_section())
        root.Children.Add(self._footer())

        self.Content = root
        self.ShowDialog()

    def _top_label(self):
        sp = StackPanel()
        sp.Margin = Thickness(0, 0, 0, 16)

        t1 = TextBlock()
        t1.Text = "MODEL HEALTH CHECKLIST"
        t1.FontSize = 16
        t1.FontWeight = FontWeights.Bold
        t1.Foreground = TEXT_DARK

        t2 = TextBlock()
        t2.Text = "GREEN = CLEAN  |  RED = NEEDS CLEANUP"
        t2.FontSize = 11
        t2.Foreground = TEXT_GREY
        t2.Margin = Thickness(0, 2, 0, 0)

        sp.Children.Add(t1)
        sp.Children.Add(t2)
        return sp

    def _build_grid(self, left, center, right, l_fg, c_fg, r_fg, bg):
        g = WpfControls.Grid()
        g.ColumnDefinitions.Add(WpfControls.ColumnDefinition())
        g.ColumnDefinitions.Add(WpfControls.ColumnDefinition())
        g.ColumnDefinitions.Add(WpfControls.ColumnDefinition())
        g.ColumnDefinitions[0].Width = GridLength(5, GridUnitType.Star)
        g.ColumnDefinitions[1].Width = GridLength(1, GridUnitType.Star)
        g.ColumnDefinitions[2].Width = GridLength(3, GridUnitType.Star)
        g.Background = bg

        lb = TextBlock()
        lb.Text = left
        lb.FontSize = 11
        lb.FontWeight = FontWeights.Bold
        lb.Foreground = l_fg
        lb.VerticalAlignment = VerticalAlignment.Center
        lb.HorizontalAlignment = HorizontalAlignment.Left
        WpfControls.Grid.SetColumn(lb, 0)
        g.Children.Add(lb)

        cb = TextBlock()
        cb.Text = center
        cb.FontSize = 11
        cb.FontWeight = FontWeights.Bold
        cb.Foreground = c_fg
        cb.VerticalAlignment = VerticalAlignment.Center
        cb.HorizontalAlignment = HorizontalAlignment.Center
        WpfControls.Grid.SetColumn(cb, 1)
        g.Children.Add(cb)

        rb = TextBlock()
        rb.Text = right
        rb.FontSize = 11
        rb.FontWeight = FontWeights.Bold
        rb.Foreground = r_fg
        rb.VerticalAlignment = VerticalAlignment.Center
        rb.HorizontalAlignment = HorizontalAlignment.Right
        WpfControls.Grid.SetColumn(rb, 2)
        g.Children.Add(rb)

        return g

    def _column_headers(self):
        header = Border()
        header.CornerRadius = CornerRadius(4, 4, 0, 0)
        header.Padding = Thickness(14, 8, 14, 8)
        header.Margin = Thickness(0, 0, 0, 2)
        header.Background = HEADER_BG
        header.BorderThickness = Thickness(0, 0, 0, 1)
        header.BorderBrush = SolidColorBrush(WpfColor.FromRgb(200, 200, 200))
        header.Child = self._build_grid("CHECK", "ACTUAL", "TARGET", TEXT_DARK, TEXT_DARK, TEXT_DARK, HEADER_BG)
        return header

    def _groups_section(self):
        sv = ScrollViewer()
        sv.VerticalScrollBarVisibility = System.Windows.Controls.ScrollBarVisibility.Auto
        sv.MaxHeight = 600

        sp = StackPanel()

        for header_label, rows in self.groups:
            header = TextBlock()
            header.Text = header_label.upper()
            header.FontSize = 13
            header.FontWeight = FontWeights.Bold
            header.Foreground = TEXT_DARK
            header.Margin = Thickness(0, 8, 0, 4)
            sp.Children.Add(header)

            for count, label, is_dirty, target in rows:
                row = Border()
                row.CornerRadius = CornerRadius(4)
                row.Padding = Thickness(14, 10, 14, 10)
                row.Margin = Thickness(0, 0, 0, 6)
                row.Background = RED_BG if is_dirty else GREEN_BG
                row.Child = self._build_grid(label, str(count), target.upper(), ROW_FG, ROW_FG, ROW_FG, None)
                sp.Children.Add(row)

        sv.Content = sp
        return sv

    def _footer(self):
        sp = StackPanel()
        sp.HorizontalAlignment = HorizontalAlignment.Right
        sp.Margin = Thickness(0, 12, 0, 0)

        btn = Button()
        btn.Content = "CLOSE"
        btn.Width = 100
        btn.Height = 32
        btn.Background = BTN_BG
        btn.Foreground = Brushes.White
        btn.BorderThickness = Thickness(0)
        btn.FontSize = 12
        btn.FontWeight = FontWeights.Bold
        btn.Cursor = System.Windows.Input.Cursors.Hand
        btn.Click += lambda s, e: self.Close()

        sp.Children.Add(btn)
        return sp


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    groups = []
    for header_label, fns in GROUPS:
        rows = []
        for fn in fns:
            try:
                count, label, is_dirty, target = fn()
            except Exception as ex:
                count = 999
                label = "ERROR"
                is_dirty = True
                target = "?"
            rows.append((count, label, is_dirty, target))
        groups.append((header_label, rows))

    ModelHealthDialog(groups)
