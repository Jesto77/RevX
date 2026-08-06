# -*- coding: utf-8 -*-
"""
Model Health Cleanup — pyRevit script
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
    Transaction, TransactionStatus, ElementId, StorageType, BuiltInCategory
)

import System.Windows.Controls as WpfControls
from pyrevit import revit

doc = revit.doc
INVALID_ID = ElementId.InvalidElementId

# =============================================================================
# REVIT VERSION DETECTION
# =============================================================================
try:
    _app = doc.Application
    REVIT_VERSION = int(_app.VersionNumber)
except Exception:
    REVIT_VERSION = 2024

# =============================================================================
# SAFE ID HELPER
# =============================================================================

def _id(elem_id):
    """Return integer value of an ElementId, safe for 2020-2026."""
    if elem_id is None:
        return None
    try:
        if elem_id == INVALID_ID:
            return None
    except Exception:
        pass
    try:
        # Revit 2024+ uses .Value (Int64), older uses int cast
        try:
            val = elem_id.Value
            return int(val) if val != -1 else None
        except AttributeError:
            val = int(elem_id)
            return val if val != -1 else None
    except Exception:
        return None


# =============================================================================
# SAFE COLLECTOR HELPER
# =============================================================================

def _safe_collect(collector_fn):
    """Wrap a collector call so a failure returns an empty list."""
    try:
        return list(collector_fn())
    except Exception:
        return []


# =============================================================================
# COLOURS
# =============================================================================
WHITE    = Brushes.White
TEXT_DARK  = SolidColorBrush(WpfColor.FromRgb(30, 30, 30))
TEXT_GREY  = SolidColorBrush(WpfColor.FromRgb(100, 100, 110))
HEADER_BG  = SolidColorBrush(WpfColor.FromRgb(245, 245, 245))
GREEN_BG   = SolidColorBrush(WpfColor.FromRgb(129, 199, 132))
RED_BG     = SolidColorBrush(WpfColor.FromRgb(229, 115, 115))
ROW_FG     = Brushes.White
BTN_BG     = SolidColorBrush(WpfColor.FromRgb(59, 130, 246))
PURGE_BG   = SolidColorBrush(WpfColor.FromRgb(220, 38, 38))

# =============================================================================
# CEILING COLLECTOR (version-safe)
# =============================================================================

def _collect_ceilings():
    """Collect ceiling elements regardless of Revit version."""
    # Revit 2024+ — Ceiling is a separate class
    results = []
    try:
        results = _safe_collect(
            lambda: FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_Ceilings)
                .WhereElementIsNotElementType()
        )
        if results:
            return results
    except Exception:
        pass
    # Fallback: class-based
    try:
        results = _safe_collect(
            lambda: FilteredElementCollector(doc).OfClass(DB.Ceiling)
        )
    except Exception:
        pass
    return results


def _collect_ceiling_types():
    """Collect ceiling types regardless of Revit version."""
    results = []
    try:
        results = _safe_collect(
            lambda: FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_Ceilings)
                .WhereElementIsElementType()
        )
        if results:
            return results
    except Exception:
        pass
    try:
        results = _safe_collect(
            lambda: FilteredElementCollector(doc).OfClass(DB.CeilingType)
        )
    except Exception:
        pass
    return results


# =============================================================================
# GROUP TYPE HELPER (version-safe, no category filter on GroupType)
# =============================================================================

def _collect_group_types_by_kind(kind_check_fn):
    """
    Collect GroupType elements filtered by a function that inspects each.
    Avoids crashing category-filtered GroupType collectors in 2026.
    """
    results = []
    try:
        all_gt = _safe_collect(
            lambda: FilteredElementCollector(doc).OfClass(DB.GroupType)
        )
        for gt in all_gt:
            try:
                if kind_check_fn(gt):
                    results.append(gt)
            except Exception:
                pass
    except Exception:
        pass
    return results


def _is_model_group_type(gt):
    try:
        cat = gt.Category
        if cat is None:
            return False
        return cat.Id == ElementId(BuiltInCategory.OST_IOSModelGroups)
    except Exception:
        return False


def _is_detail_group_type(gt):
    try:
        cat = gt.Category
        if cat is None:
            return False
        return cat.Id == ElementId(BuiltInCategory.OST_IOSDetailGroups)
    except Exception:
        return False


# =============================================================================
# ONE-PASS DATA COLLECTION
# =============================================================================

def _collect_data():
    data = {}

    # ── Family instances & types ──────────────────────────────────────────────
    data['fam_instances'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(FamilyInstance)
    )

    used_sym_ids = set()
    for inst in data['fam_instances']:
        try:
            tid = _id(inst.GetTypeId())
            if tid is not None:
                used_sym_ids.add(tid)
        except Exception:
            pass
    data['used_sym_ids'] = used_sym_ids

    data['all_symbols'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(FamilySymbol)
    )
    data['unused_symbols'] = [
        s for s in data['all_symbols'] if _id(s.Id) not in used_sym_ids
    ]

    used_fam_ids = set()
    for s in data['all_symbols']:
        if _id(s.Id) in used_sym_ids:
            try:
                used_fam_ids.add(_id(s.Family.Id))
            except Exception:
                pass
    data['used_fam_ids'] = used_fam_ids

    data['all_families'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(Family)
    )
    data['unused_families'] = [
        f for f in data['all_families'] if _id(f.Id) not in used_fam_ids
    ]
    data['in_place_families'] = [
        f for f in data['all_families'] if getattr(f, 'IsInPlace', False)
    ]

    # ── Generic models ────────────────────────────────────────────────────────
    data['generic_models'] = _safe_collect(
        lambda: FilteredElementCollector(doc)
            .OfClass(FamilyInstance)
            .OfCategory(BuiltInCategory.OST_GenericModel)
    )

    # ── Text types ────────────────────────────────────────────────────────────
    text_notes = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.TextNote)
    )
    used_text_ids = set()
    for t in text_notes:
        try:
            tid = _id(t.GetTypeId())
            if tid is not None:
                used_text_ids.add(tid)
        except Exception:
            pass
    all_text_types = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.TextNoteType)
    )
    data['unused_text_types'] = [
        tt for tt in all_text_types if _id(tt.Id) not in used_text_ids
    ]

    # ── Dimension types ───────────────────────────────────────────────────────
    dims = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.Dimension)
    )
    used_dim_ids = set()
    for d in dims:
        try:
            tid = _id(d.GetTypeId())
            if tid is not None:
                used_dim_ids.add(tid)
        except Exception:
            pass
    all_dim_types = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.DimensionType)
    )
    data['unused_dim_types'] = [
        dt for dt in all_dim_types if _id(dt.Id) not in used_dim_ids
    ]

    # ── Wall types ────────────────────────────────────────────────────────────
    walls = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.Wall)
    )
    used_wall_ids = set()
    for w in walls:
        try:
            tid = _id(w.GetTypeId())
            if tid is not None:
                used_wall_ids.add(tid)
        except Exception:
            pass
    all_wall_types = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.WallType)
    )
    data['unused_wall_types'] = [
        wt for wt in all_wall_types if _id(wt.Id) not in used_wall_ids
    ]

    # ── Floor types ───────────────────────────────────────────────────────────
    floors = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.Floor)
    )
    used_floor_ids = set()
    for f in floors:
        try:
            tid = _id(f.GetTypeId())
            if tid is not None:
                used_floor_ids.add(tid)
        except Exception:
            pass
    all_floor_types = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.FloorType)
    )
    data['unused_floor_types'] = [
        ft for ft in all_floor_types if _id(ft.Id) not in used_floor_ids
    ]

    # ── Roof types ────────────────────────────────────────────────────────────
    roofs = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.RoofBase)
    )
    used_roof_ids = set()
    for r in roofs:
        try:
            tid = _id(r.GetTypeId())
            if tid is not None:
                used_roof_ids.add(tid)
        except Exception:
            pass
    all_roof_types = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.RoofType)
    )
    data['unused_roof_types'] = [
        rt for rt in all_roof_types if _id(rt.Id) not in used_roof_ids
    ]

    # ── Ceiling types (version-safe) ──────────────────────────────────────────
    ceilings = _collect_ceilings()
    used_ceil_ids = set()
    for c in ceilings:
        try:
            tid = _id(c.GetTypeId())
            if tid is not None:
                used_ceil_ids.add(tid)
        except Exception:
            pass
    all_ceil_types = _collect_ceiling_types()
    data['unused_ceil_types'] = [
        ct for ct in all_ceil_types if _id(ct.Id) not in used_ceil_ids
    ]

    # ── Views & templates ─────────────────────────────────────────────────────
    data['all_views'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(View)
    )

    used_template_ids = set()
    for v in data['all_views']:
        try:
            tid = getattr(v, 'ViewTemplateId', INVALID_ID)
            tid_int = _id(tid)
            if tid_int is not None:
                used_template_ids.add(tid_int)
        except Exception:
            pass
    data['unused_templates'] = [
        v for v in data['all_views']
        if getattr(v, 'IsTemplate', False)
        and _id(v.Id) not in used_template_ids
    ]

    # ── View filters ──────────────────────────────────────────────────────────
    used_filter_ids = set()
    for v in data['all_views']:
        try:
            for fid in v.GetFilters():
                fid_int = _id(fid)
                if fid_int is not None:
                    used_filter_ids.add(fid_int)
        except Exception:
            pass
    data['all_filters'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(ParameterFilterElement)
    )
    data['unused_filters'] = [
        f for f in data['all_filters'] if _id(f.Id) not in used_filter_ids
    ]

    # ── Views on sheets ───────────────────────────────────────────────────────
    data['sheets'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(ViewSheet)
    )
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
    data['orphan_views'] = [
        v for v in data['all_views']
        if not getattr(v, 'IsTemplate', False)
        and not isinstance(v, ViewSheet)
        and _id(v.Id) not in placed_view_ids
    ]

    # ── Groups (version-safe category filtering) ──────────────────────────────
    all_groups = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(Group)
    )

    data['detail_groups'] = []
    data['model_groups_placed'] = []
    for g in all_groups:
        try:
            cat = g.Category
            if cat is None:
                continue
            cat_id = _id(cat.Id)
            detail_id = _id(ElementId(BuiltInCategory.OST_IOSDetailGroups))
            model_id  = _id(ElementId(BuiltInCategory.OST_IOSModelGroups))
            if cat_id == detail_id:
                data['detail_groups'].append(g)
            elif cat_id == model_id:
                data['model_groups_placed'].append(g)
        except Exception:
            pass

    # Unused group types (no category filter on GroupType collector)
    all_model_gt = _collect_group_types_by_kind(_is_model_group_type)
    used_model_gt_ids = set()
    for g in data['model_groups_placed']:
        try:
            tid = _id(g.GetTypeId())
            if tid is not None:
                used_model_gt_ids.add(tid)
        except Exception:
            pass
    data['unused_model_group_types'] = [
        gt for gt in all_model_gt if _id(gt.Id) not in used_model_gt_ids
    ]

    all_detail_gt = _collect_group_types_by_kind(_is_detail_group_type)
    used_detail_gt_ids = set()
    for g in data['detail_groups']:
        try:
            tid = _id(g.GetTypeId())
            if tid is not None:
                used_detail_gt_ids.add(tid)
        except Exception:
            pass
    data['unused_detail_group_types'] = [
        gt for gt in all_detail_gt if _id(gt.Id) not in used_detail_gt_ids
    ]

    # ── Line patterns ─────────────────────────────────────────────────────────
    data['line_patterns'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(LinePatternElement)
    )
    data['imported_patterns'] = [
        p for p in data['line_patterns']
        if "IMPORT" in getattr(p, 'Name', '').upper()
    ]

    # ── Warnings ──────────────────────────────────────────────────────────────
    try:
        data['warnings'] = list(doc.GetWarnings())
    except Exception:
        data['warnings'] = []

    # ── Links & imports ───────────────────────────────────────────────────────
    all_imports = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(ImportInstance)
    )
    data['cad_links']   = [i for i in all_imports if getattr(i, 'IsLinked', False)]
    data['cad_imports'] = [i for i in all_imports if not getattr(i, 'IsLinked', False)]
    data['revit_links'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(RevitLinkInstance)
    )

    # ── Levels / Grids ────────────────────────────────────────────────────────
    data['levels'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(Level)
    )
    data['grids'] = _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.Grid)
    )

    data['unpinned_levels']      = [l for l in data['levels']      if not getattr(l, 'Pinned', True)]
    data['unpinned_revit_links'] = [l for l in data['revit_links'] if not getattr(l, 'Pinned', True)]
    data['unpinned_cad_links']   = [i for i in data['cad_links']   if not getattr(i, 'Pinned', True)]

    # ── Lightweight reference scan (NO full-parameter walk) ───────────────────
    # Only scan element type IDs and a small set of known ID parameters
    # to avoid the expensive and crash-prone full parameter scan.
    referenced = set()

    _KNOWN_ID_PARAMS = [
        DB.BuiltInParameter.ELEM_TYPE_PARAM,
        DB.BuiltInParameter.MATERIAL_ID_PARAM,
        DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM,  # level ref example
    ]

    def _fast_scan(elem):
        try:
            tid = _id(elem.GetTypeId())
            if tid is not None:
                referenced.add(tid)
        except Exception:
            pass
        try:
            if hasattr(elem, 'LevelId'):
                lid = _id(elem.LevelId)
                if lid is not None:
                    referenced.add(lid)
        except Exception:
            pass
        # Scan material parameters only — much safer than all parameters
        try:
            for bip in [DB.BuiltInParameter.MATERIAL_ID_PARAM]:
                p = elem.get_Parameter(bip)
                if p and p.StorageType == StorageType.ElementId:
                    val = _id(p.AsElementId())
                    if val is not None:
                        referenced.add(val)
        except Exception:
            pass

    for elem in _safe_collect(
        lambda: FilteredElementCollector(doc).WhereElementIsNotElementType()
    ):
        _fast_scan(elem)

    for elem in _safe_collect(
        lambda: FilteredElementCollector(doc).WhereElementIsElementType()
    ):
        _fast_scan(elem)

    # Scan graphics styles for line pattern references (safe targeted scan)
    for gs in _safe_collect(
        lambda: FilteredElementCollector(doc).OfClass(DB.GraphicsStyle)
    ):
        try:
            gse = gs.GraphicsStyleCategory
            if gse is not None:
                lp_id = _id(gse.GetLinePatternId(DB.GraphicsStyleType.Projection))
                if lp_id is not None:
                    referenced.add(lp_id)
                lp_id = _id(gse.GetLinePatternId(DB.GraphicsStyleType.Cut))
                if lp_id is not None:
                    referenced.add(lp_id)
        except Exception:
            pass

    data['referenced'] = referenced

    # ── Unused fill patterns / materials / line patterns ──────────────────────
    data['unused_fill_patterns'] = [
        fp for fp in _safe_collect(
            lambda: FilteredElementCollector(doc).OfClass(DB.FillPatternElement)
        )
        if _id(fp.Id) not in referenced
    ]
    data['unused_materials'] = [
        m for m in _safe_collect(
            lambda: FilteredElementCollector(doc).OfClass(DB.Material)
        )
        if _id(m.Id) not in referenced
    ]
    data['unused_line_patterns'] = [
        lp for lp in data['line_patterns']
        if _id(lp.Id) not in referenced
        and "IMPORT" not in getattr(lp, 'Name', '').upper()
    ]

    # ── Arrowheads ────────────────────────────────────────────────────────────
    data['unused_arrowheads'] = []
    try:
        for ast in _safe_collect(
            lambda: FilteredElementCollector(doc)
                .OfClass(FamilySymbol)
                .OfCategory(BuiltInCategory.OST_GenericAnnotation)
        ):
            try:
                fname = getattr(ast, 'FamilyName', '') or ''
                if _id(ast.Id) not in referenced and 'Arrow' in fname:
                    data['unused_arrowheads'].append(ast)
            except Exception:
                pass
    except Exception:
        pass

    # ── Title blocks ──────────────────────────────────────────────────────────
    data['unused_titleblocks'] = []
    try:
        tblocks = _safe_collect(
            lambda: FilteredElementCollector(doc)
                .OfClass(FamilySymbol)
                .OfCategory(BuiltInCategory.OST_TitleBlocks)
        )
        used_tblocks = set()
        for s in data['sheets']:
            try:
                tid = _id(s.GetTypeId())
                if tid is not None:
                    used_tblocks.add(tid)
            except Exception:
                pass
        data['unused_titleblocks'] = [
            tb for tb in tblocks if _id(tb.Id) not in used_tblocks
        ]
    except Exception:
        pass

    return data


# =============================================================================
# LOAD DATA — wrapped so a partial failure doesn't kill the whole script
# =============================================================================
try:
    _DATA = _collect_data()
except Exception as _data_ex:
    from pyrevit import forms
    forms.alert(
        "Data collection error:\n{}\n\nScript will exit.".format(str(_data_ex)),
        title="Model Health"
    )
    raise SystemExit(1)


# =============================================================================
# HEALTH CHECK FUNCTIONS
# =============================================================================

def _safe_check(fn):
    try:
        return fn()
    except Exception as ex:
        return 999, "ERROR: {}".format(str(ex)[:40]), True, "?"


def count_purgeable():
    c = (len(_DATA.get('unused_symbols', [])) +
         len(_DATA.get('unused_families', [])) +
         len(_DATA.get('unused_text_types', [])) +
         len(_DATA.get('unused_dim_types', [])) +
         len(_DATA.get('unused_wall_types', [])) +
         len(_DATA.get('unused_floor_types', [])) +
         len(_DATA.get('unused_roof_types', [])) +
         len(_DATA.get('unused_ceil_types', [])) +
         len(_DATA.get('unused_templates', [])) +
         len(_DATA.get('unused_filters', [])) +
         len(_DATA.get('unused_fill_patterns', [])) +
         len(_DATA.get('unused_materials', [])) +
         len(_DATA.get('unused_line_patterns', [])) +
         len(_DATA.get('unused_arrowheads', [])) +
         len(_DATA.get('unused_titleblocks', [])) +
         len(_DATA.get('unused_model_group_types', [])) +
         len(_DATA.get('unused_detail_group_types', [])))
    return c, "PURGE UNUSED", c > 0, "= 0"


def count_in_place_families():
    c = len(_DATA.get('in_place_families', []))
    return c, "IN PLACE FAMILIES", c > 0, "= 0"


def count_generic_models():
    c = len(_DATA.get('generic_models', []))
    return c, "GENERIC MODELS", c > 0, "= 0"


def count_warnings():
    c = len(_DATA.get('warnings', []))
    return c, "WARNINGS", c >= 200, "< 200"


def count_imported_line_patterns():
    c = len(_DATA.get('imported_patterns', []))
    return c, "IMPORTED LINE PATTERNS", c > 0, "= 0"


def count_detail_groups():
    c = len(_DATA.get('detail_groups', []))
    return c, "DETAILS GROUPS", c > 0, "= 0"


def _off_target_workset(elem, target_name):
    try:
        ws = doc.GetWorksetTable().GetWorkset(elem.WorksetId)
        return ws.Name.strip() != target_name.strip()
    except Exception:
        return False   # default to OK if workset info unavailable


def count_levels_on_workset():
    target = "Shared Levels and Grids"
    c = sum(1 for lvl in _DATA.get('levels', []) if _off_target_workset(lvl, target))
    return c, "LEVELS NOT ON Shared Levels and Grids", c > 0, "= 0"


def count_grids_on_workset():
    target = "Shared Levels and Grids"
    c = sum(1 for g in _DATA.get('grids', []) if _off_target_workset(g, target))
    return c, "GRIDS NOT ON Shared Levels and Grids", c > 0, "= 0"


def count_revit_links_on_workset():
    target = "Shared Levels and Grids"
    c = sum(1 for lnk in _DATA.get('revit_links', []) if _off_target_workset(lnk, target))
    return c, "REVIT LINKS NOT ON Shared Levels and Grids", c > 0, "= 0"


def count_unused_view_templates():
    c = len(_DATA.get('unused_templates', []))
    return c, "VIEW TEMPLATES", c > 0, "= 0"


def count_unused_view_filters():
    c = len(_DATA.get('unused_filters', []))
    return c, "VIEW FILTERS", c > 0, "= 0"


def count_views_not_on_sheets():
    c = len(_DATA.get('orphan_views', []))
    return c, "VIEW NOT ON SHEETS", c > 0, "= 0"


def count_unpinned_levels():
    c = len(_DATA.get('unpinned_levels', []))
    return c, "LEVELS", c > 0, "= 0"


def count_unpinned_revit_links():
    c = len(_DATA.get('unpinned_revit_links', []))
    return c, "REVIT LINKS", c > 0, "= 0"


def count_unpinned_cad_links():
    c = len(_DATA.get('unpinned_cad_links', []))
    return c, "CAD", c > 0, "= 0"


def count_cad_links():
    c = len(_DATA.get('cad_links', []))
    return c, "CAD LINKS", c > 0, "= 0"


def count_revit_links_max3():
    c = len(_DATA.get('revit_links', []))
    return c, "REVIT LINKS", c > 3, "<= 3"


def count_imported_cad():
    c = len(_DATA.get('cad_imports', []))
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
# TRANSACTION HELPERS
# =============================================================================

def safe_delete(elements, tx_name="Delete"):
    if not elements:
        return 0
    ids = []
    for e in elements:
        try:
            eid = getattr(e, 'Id', None)
            if eid and eid != INVALID_ID:
                ids.append(eid)
        except Exception:
            pass
    if not ids:
        return 0

    unique_ids = list({_id(i): i for i in ids if _id(i) is not None}.values())
    id_list = System.Collections.Generic.List[ElementId](unique_ids)

    tx = Transaction(doc, tx_name)
    tx.Start()
    try:
        result = doc.Delete(id_list)
        status = tx.Commit()
        if status == TransactionStatus.Committed:
            try:
                return len(list(result))
            except Exception:
                return len(unique_ids)
    except Exception:
        try:
            tx.RollBack()
        except Exception:
            pass
    return 0


# =============================================================================
# PURGE ALL
# =============================================================================

def purge_all():
    results = {}
    results['VIEW TEMPLATES']        = safe_delete(_DATA.get('unused_templates', []),   "Purge View Templates")
    results['VIEW FILTERS']          = safe_delete(_DATA.get('unused_filters', []),      "Purge View Filters")
    results['DETAIL GROUPS']         = safe_delete(_DATA.get('detail_groups', []),       "Purge Detail Groups")
    results['MODEL GROUPS']          = safe_delete(_DATA.get('model_groups_placed', []), "Purge Model Groups")
    results['IMPORTED LINE PATTERNS']= safe_delete(_DATA.get('imported_patterns', []),   "Purge Imported Patterns")
    results['CAD LINKS']             = safe_delete(_DATA.get('cad_links', []),           "Purge CAD Links")
    results['IMPORTED CAD']          = safe_delete(_DATA.get('cad_imports', []),         "Purge CAD Imports")
    results['IN-PLACE FAMILIES']     = safe_delete(_DATA.get('in_place_families', []),   "Purge In-Place Families")
    results['GENERIC MODELS']        = safe_delete(_DATA.get('generic_models', []),      "Purge Generic Models")

    total = sum(results.values())
    summary = {k: v for k, v in results.items() if v > 0}
    return summary, total


# =============================================================================
# WPF DIALOG
# =============================================================================

class ModelHealthDialog(Window):
    def __init__(self, groups):
        self.groups = groups

        self.Title = "MODEL HEALTH"
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResize
        self.MinWidth  = 620
        self.MinHeight = 440
        self.Width  = 820
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

    # ── internal builders ─────────────────────────────────────────────────────

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
        for star in (5, 1, 3):
            col = WpfControls.ColumnDefinition()
            col.Width = GridLength(star, GridUnitType.Star)
            g.ColumnDefinitions.Add(col)
        if bg is not None:
            g.Background = bg

        def _tb(text, fg, ha, col_idx):
            tb = TextBlock()
            tb.Text = text
            tb.FontSize = 11
            tb.FontWeight = FontWeights.Bold
            tb.Foreground = fg
            tb.VerticalAlignment = VerticalAlignment.Center
            tb.HorizontalAlignment = ha
            WpfControls.Grid.SetColumn(tb, col_idx)
            g.Children.Add(tb)

        _tb(left,   l_fg, HorizontalAlignment.Left,   0)
        _tb(center, c_fg, HorizontalAlignment.Center, 1)
        _tb(right,  r_fg, HorizontalAlignment.Right,  2)
        return g

    def _column_headers(self):
        header = Border()
        header.CornerRadius = CornerRadius(4, 4, 0, 0)
        header.Padding = Thickness(14, 8, 14, 8)
        header.Margin  = Thickness(0, 0, 0, 2)
        header.Background = HEADER_BG
        header.BorderThickness = Thickness(0, 0, 0, 1)
        header.BorderBrush = SolidColorBrush(WpfColor.FromRgb(200, 200, 200))
        header.Child = self._build_grid(
            "CHECK", "ACTUAL", "TARGET",
            TEXT_DARK, TEXT_DARK, TEXT_DARK, HEADER_BG
        )
        return header

    def _groups_section(self):
        sv = ScrollViewer()
        sv.VerticalScrollBarVisibility = WpfControls.ScrollBarVisibility.Auto
        sv.MaxHeight = 600

        sp = StackPanel()
        for header_label, rows in self.groups:
            hdr = TextBlock()
            hdr.Text = header_label.upper()
            hdr.FontSize = 13
            hdr.FontWeight = FontWeights.Bold
            hdr.Foreground = TEXT_DARK
            hdr.Margin = Thickness(0, 8, 0, 4)
            sp.Children.Add(hdr)

            for count, label, is_dirty, target in rows:
                row = Border()
                row.CornerRadius = CornerRadius(4)
                row.Padding = Thickness(14, 10, 14, 10)
                row.Margin  = Thickness(0, 0, 0, 6)
                row.Background = RED_BG if is_dirty else GREEN_BG
                row.Child = self._build_grid(
                    label, str(count), target.upper(),
                    ROW_FG, ROW_FG, ROW_FG, None
                )
                sp.Children.Add(row)

        sv.Content = sp
        return sv

    def _footer(self):
        sp = StackPanel()
        sp.HorizontalAlignment = HorizontalAlignment.Right
        sp.Margin = Thickness(0, 12, 0, 0)
        sp.Orientation = Orientation.Horizontal

        btn_purge = Button()
        btn_purge.Content = "PURGE ALL"
        btn_purge.Width  = 120
        btn_purge.Height = 32
        btn_purge.Background = PURGE_BG
        btn_purge.Foreground = Brushes.White
        btn_purge.BorderThickness = Thickness(0)
        btn_purge.FontSize = 12
        btn_purge.FontWeight = FontWeights.Bold
        btn_purge.Cursor = System.Windows.Input.Cursors.Hand
        btn_purge.Margin = Thickness(0, 0, 10, 0)
        btn_purge.Click += self._on_purge

        btn_close = Button()
        btn_close.Content = "CLOSE"
        btn_close.Width  = 100
        btn_close.Height = 32
        btn_close.Background = BTN_BG
        btn_close.Foreground = Brushes.White
        btn_close.BorderThickness = Thickness(0)
        btn_close.FontSize = 12
        btn_close.FontWeight = FontWeights.Bold
        btn_close.Cursor = System.Windows.Input.Cursors.Hand
        btn_close.Click += lambda s, e: self.Close()

        sp.Children.Add(btn_purge)
        sp.Children.Add(btn_close)
        return sp

    def _on_purge(self, sender, e):
        from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult
        result = MessageBox.Show(
            "This will delete auto-removable items:\n"
            "- View templates, view filters\n"
            "- Detail groups, model groups\n"
            "- Imported line patterns, CAD links/imports\n"
            "- In-place families, generic models\n\n"
            "PURGE UNUSED must be done manually via Manage > Purge Unused.\n\n"
            "Continue?",
            "PURGE MODEL",
            MessageBoxButton.YesNo,
            MessageBoxImage.Warning
        )
        if result != MessageBoxResult.Yes:
            return

        summary, total = purge_all()

        msg = "PURGE COMPLETE\n\n"
        if total == 0:
            msg += "Nothing was deleted.\n\n"
        else:
            for k, v in sorted(summary.items(), key=lambda x: -x[1]):
                msg += "  - {}: {}\n".format(k, v)
            msg += "\nTOTAL DELETED: {}\n\n".format(total)
        msg += "Run Manage > Purge Unused in Revit to clear remaining items,\nthen re-run this script to verify."

        MessageBox.Show(msg, "PURGE RESULTS", MessageBoxButton.OK, MessageBoxImage.Information)
        self.Close()


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    groups = []
    for header_label, fns in GROUPS:
        rows = []
        for fn in fns:
            rows.append(_safe_check(fn))
        groups.append((header_label, rows))

    dlg = ModelHealthDialog(groups)
    dlg.ShowDialog()