# -*- coding: utf-8 -*-
"""
DWG Export - All views merged into ONE DWG
- All lines and HATCH PATTERNS in Grey 253 (host + links)
- Hatches exported as PATTERN LINES (not solid fills)
- Nothing hidden - all floors/topos visible as-is
Author: Jesto Joy
"""

from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import os

doc = revit.doc

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

PATTERN_CATEGORIES = [
    DB.BuiltInCategory.OST_Floors,
    DB.BuiltInCategory.OST_Roofs,
    DB.BuiltInCategory.OST_Stairs,
]
try:
    PATTERN_CATEGORIES.append(DB.BuiltInCategory.OST_Toposolid)
except Exception:
    pass
try:
    PATTERN_CATEGORIES.append(DB.BuiltInCategory.OST_Topography)
except Exception:
    pass

TEMP_PAT_SUFFIX    = "_TMP_PAT"
TEMP_BLK_SUFFIX    = "_TMP_BLK"
TEMP_SHEET_PREFIX  = "TMP_DWG_"

# -----------------------------------------------------------------------------
# COLORS
# -----------------------------------------------------------------------------

GREY_R = 253
GREY_G = 253
GREY_B = 253
GREY_TRUE_COLOR = (GREY_R << 16) | (GREY_G << 8) | GREY_B
GREY_253       = DB.Color(GREY_R, GREY_G, GREY_B)
GREY_ACI_INDEX = 9

# -----------------------------------------------------------------------------
# COMPAT
# -----------------------------------------------------------------------------

def eid_val(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue

# -----------------------------------------------------------------------------
# MATERIAL OVERRIDE - HOST + ALL LINKED DOCUMENTS
# -----------------------------------------------------------------------------

# Store originals per (doc_hash, material_id) so we can restore in each doc
_original_material_data = {}   # (doc_pathkey, material_int_id) -> dict


def _doc_key(document):
    """Unique key for a document (handles workshared / links safely)."""
    try:
        return document.PathName or str(id(document))
    except Exception:
        return str(id(document))


def _force_materials_grey_in_doc(document):
    """
    Change every material in *document* to grey.
    Removes solid-fill patterns so hatches export as lines.
    Stores originals for restoration.
    Returns True if any transaction was performed here.
    """
    try:
        if document is None or document.IsLinked and document.IsReadOnly:
            # If it's a link we can't modify, skip
            pass
    except Exception:
        pass

    materials = []
    try:
        materials = list(
            DB.FilteredElementCollector(document)
              .OfClass(DB.Material)
              .ToElements()
        )
    except Exception:
        return False

    if not materials:
        return False

    dkey = _doc_key(document)

    # Materials in linked docs can only be modified if we open the link
    # in a separate transaction on that document. Try, but catch errors.
    tx_started = False
    tx = None
    try:
        tx = DB.Transaction(document, "Force materials grey")
        tx.Start()
        tx_started = True
    except Exception:
        tx_started = False

    try:
        for mat in materials:
            try:
                mid = eid_val(mat.Id)
                key = (dkey, mid)
                _original_material_data[key] = {
                    'Color': mat.Color,
                    'SurfaceForegroundPatternColor': None,
                    'SurfaceBackgroundPatternColor': None,
                    'CutForegroundPatternColor'    : None,
                    'CutBackgroundPatternColor'    : None,
                    'SurfaceForegroundPatternId'   : None,
                    'SurfaceBackgroundPatternId'   : None,
                    'CutForegroundPatternId'       : None,
                    'CutBackgroundPatternId'       : None,
                }

                for attr in ('SurfaceForegroundPatternColor',
                             'SurfaceBackgroundPatternColor',
                             'CutForegroundPatternColor',
                             'CutBackgroundPatternColor'):
                    try:
                        _original_material_data[key][attr] = \
                            getattr(mat, attr)
                    except Exception:
                        pass

                for attr in ('SurfaceForegroundPatternId',
                             'SurfaceBackgroundPatternId',
                             'CutForegroundPatternId',
                             'CutBackgroundPatternId'):
                    try:
                        _original_material_data[key][attr] = \
                            getattr(mat, attr)
                    except Exception:
                        pass

                try:
                    mat.Color = GREY_253
                except Exception:
                    pass

                for attr in ('SurfaceForegroundPatternColor',
                             'SurfaceBackgroundPatternColor',
                             'CutForegroundPatternColor',
                             'CutBackgroundPatternColor'):
                    try:
                        setattr(mat, attr, GREY_253)
                    except Exception:
                        pass

                # Strip solid fills so patterns export as lines
                for id_attr in ('SurfaceForegroundPatternId',
                                'CutForegroundPatternId',
                                'SurfaceBackgroundPatternId',
                                'CutBackgroundPatternId'):
                    try:
                        pat_id = getattr(mat, id_attr)
                        if pat_id and eid_val(pat_id) != -1:
                            pat_el = document.GetElement(pat_id)
                            if pat_el:
                                fp = pat_el.GetFillPattern()
                                if fp and fp.IsSolidFill:
                                    try:
                                        setattr(mat, id_attr,
                                                DB.ElementId.InvalidElementId)
                                    except Exception:
                                        pass
                    except Exception:
                        pass
            except Exception:
                pass

        if tx_started and tx is not None:
            tx.Commit()
        return True

    except Exception:
        if tx_started and tx is not None:
            try:
                tx.RollBack()
            except Exception:
                pass
        return False


def _restore_materials_in_doc(document):
    """Restore materials in a given document."""
    if document is None:
        return

    dkey = _doc_key(document)

    keys_here = [k for k in _original_material_data.keys() if k[0] == dkey]
    if not keys_here:
        return

    tx_started = False
    tx = None
    try:
        tx = DB.Transaction(document, "Restore materials")
        tx.Start()
        tx_started = True
    except Exception:
        tx_started = False

    try:
        for key in keys_here:
            data = _original_material_data.get(key)
            if not data:
                continue
            try:
                mat = document.GetElement(DB.ElementId(key[1]))
                if mat is None:
                    continue

                if data.get('Color'):
                    try:
                        mat.Color = data['Color']
                    except Exception:
                        pass

                for attr in ('SurfaceForegroundPatternColor',
                             'SurfaceBackgroundPatternColor',
                             'CutForegroundPatternColor',
                             'CutBackgroundPatternColor'):
                    val = data.get(attr)
                    if val is not None:
                        try:
                            setattr(mat, attr, val)
                        except Exception:
                            pass

                for attr in ('SurfaceForegroundPatternId',
                             'SurfaceBackgroundPatternId',
                             'CutForegroundPatternId',
                             'CutBackgroundPatternId'):
                    val = data.get(attr)
                    if val is not None:
                        try:
                            setattr(mat, attr, val)
                        except Exception:
                            pass
            except Exception:
                pass

        if tx_started and tx is not None:
            tx.Commit()

    except Exception:
        if tx_started and tx is not None:
            try:
                tx.RollBack()
            except Exception:
                pass

    # Remove restored keys
    for key in keys_here:
        _original_material_data.pop(key, None)


def force_all_materials_grey():
    """
    Process host document and every linked Revit document.
    """
    processed_docs = []

    # Host
    _force_materials_grey_in_doc(doc)
    processed_docs.append(doc)

    # Linked Revit documents
    try:
        link_instances = (
            DB.FilteredElementCollector(doc)
              .OfClass(DB.RevitLinkInstance)
              .ToElements()
        )
        seen = set()
        for link in link_instances:
            try:
                link_doc = link.GetLinkDocument()
                if link_doc is None:
                    continue
                lkey = _doc_key(link_doc)
                if lkey in seen:
                    continue
                seen.add(lkey)
                _force_materials_grey_in_doc(link_doc)
                processed_docs.append(link_doc)
            except Exception:
                pass
    except Exception:
        pass

    return processed_docs


def restore_all_materials(processed_docs):
    for d in processed_docs:
        try:
            _restore_materials_in_doc(d)
        except Exception:
            pass
    _original_material_data.clear()

# -----------------------------------------------------------------------------
# DWG EXPORT SETTINGS - GREY LAYERS
# -----------------------------------------------------------------------------

def build_grey_export_settings(document):
    setup_name = "TMP_GREY253_EXPORT"

    existing = None
    for s in DB.FilteredElementCollector(document).OfClass(DB.ExportDWGSettings):
        if s.Name == setup_name:
            existing = s
            break
    if existing is not None:
        try:
            document.Delete(existing.Id)
        except Exception:
            pass

    settings    = DB.ExportDWGSettings.Create(document, setup_name)
    dwg_options = settings.GetDWGExportOptions()

    try:
        dwg_options.Colors = DB.ExportColorMode.TrueColorPerView
    except Exception:
        try:
            dwg_options.Colors = DB.ExportColorMode.IndexColors
        except Exception:
            pass

    try:
        dwg_options.MergedViews = True
    except Exception:
        pass

    # Export hatches as PATTERN LINES not solid fills
    try:
        dwg_options.HatchPackaging = DB.ACAExportOptions.HatchPatterns
    except Exception:
        pass

    try:
        dwg_options.SolidsExportOption = DB.SolidGeometry.Polymesh
    except Exception:
        pass

    # Grey the layer table
    try:
        layer_table = dwg_options.GetExportLayerTable()
        for key in layer_table.GetKeys():
            try:
                info = layer_table.GetExportLayerInfo(key)
                try:
                    info.ColorNumber    = GREY_ACI_INDEX
                except Exception:
                    pass
                try:
                    info.ColorValue     = GREY_TRUE_COLOR
                except Exception:
                    pass
                try:
                    info.CutColorNumber = GREY_ACI_INDEX
                except Exception:
                    pass
                try:
                    info.CutColorValue  = GREY_TRUE_COLOR
                except Exception:
                    pass
                layer_table.SetExportLayerInfo(key, info)
            except Exception:
                pass
        dwg_options.SetExportLayerTable(layer_table)
    except Exception:
        pass

    try:
        settings.SetDWGExportOptions(dwg_options)
    except Exception:
        pass

    return settings


def get_grey_export_options(document, grey_settings):
    opts = None
    try:
        opts = DB.DWGExportOptions.GetPredefinedOptions(
            document, grey_settings.Name)
    except Exception:
        opts = None
    if opts is None:
        opts = grey_settings.GetDWGExportOptions()
    if opts is None:
        opts = DB.DWGExportOptions()

    try:
        opts.Colors = DB.ExportColorMode.TrueColorPerView
    except Exception:
        pass
    try:
        opts.MergedViews = True
    except Exception:
        pass
    try:
        opts.HatchPackaging = DB.ACAExportOptions.HatchPatterns
    except Exception:
        pass

    try:
        layer_table = opts.GetExportLayerTable()
        for key in layer_table.GetKeys():
            try:
                info = layer_table.GetExportLayerInfo(key)
                try:
                    info.ColorNumber    = GREY_ACI_INDEX
                except Exception:
                    pass
                try:
                    info.ColorValue     = GREY_TRUE_COLOR
                except Exception:
                    pass
                try:
                    info.CutColorNumber = GREY_ACI_INDEX
                except Exception:
                    pass
                try:
                    info.CutColorValue  = GREY_TRUE_COLOR
                except Exception:
                    pass
                layer_table.SetExportLayerInfo(key, info)
            except Exception:
                pass
        opts.SetExportLayerTable(layer_table)
    except Exception:
        pass

    return opts

# -----------------------------------------------------------------------------
# VIEW GREY OVERRIDES
# -----------------------------------------------------------------------------

def make_grey_override():
    ogs = DB.OverrideGraphicSettings()
    for setter in (
        "SetProjectionLineColor",
        "SetCutLineColor",
        "SetSurfaceForegroundPatternColor",
        "SetSurfaceBackgroundPatternColor",
        "SetCutForegroundPatternColor",
        "SetCutBackgroundPatternColor",
        "SetProjectionFillColor",
        "SetCutFillColor",
    ):
        try:
            getattr(ogs, setter)(GREY_253)
        except Exception:
            pass
    try:
        ogs.SetHalftone(False)
    except Exception:
        pass
    return ogs


def _apply_ogs(view, cat_id, ogs):
    try:
        view.SetCategoryOverrides(cat_id, ogs)
    except Exception:
        pass


def apply_grey_to_all_categories(view):
    ogs = make_grey_override()
    for cat in doc.Settings.Categories:
        _apply_ogs(view, cat.Id, ogs)
        try:
            for sub in cat.SubCategories:
                _apply_ogs(view, sub.Id, ogs)
        except Exception:
            pass


def apply_grey_to_all_elements(view):
    ogs = make_grey_override()
    try:
        for el in (DB.FilteredElementCollector(doc, view.Id)
                     .WhereElementIsNotElementType()
                     .ToElements()):
            try:
                view.SetElementOverrides(el.Id, ogs)
            except Exception:
                pass
    except Exception:
        pass


def apply_grey_to_links(view):
    ogs = make_grey_override()

    try:
        for link in (DB.FilteredElementCollector(doc, view.Id)
                       .OfClass(DB.RevitLinkInstance)
                       .ToElements()):
            try:
                view.SetElementOverrides(link.Id, ogs)
            except Exception:
                pass
            try:
                link_doc = link.GetLinkDocument()
                if link_doc is not None:
                    for cat in link_doc.Settings.Categories:
                        _apply_ogs(view, cat.Id, ogs)
                        try:
                            for sub in cat.SubCategories:
                                _apply_ogs(view, sub.Id, ogs)
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                cat = link.Category
                if cat:
                    _apply_ogs(view, cat.Id, ogs)
                    try:
                        for sub in cat.SubCategories:
                            _apply_ogs(view, sub.Id, ogs)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    try:
        for imp in (DB.FilteredElementCollector(doc, view.Id)
                      .OfClass(DB.ImportInstance)
                      .ToElements()):
            try:
                view.SetElementOverrides(imp.Id, ogs)
            except Exception:
                pass
            try:
                cat = imp.Category
                if cat:
                    _apply_ogs(view, cat.Id, ogs)
                    try:
                        for sub in cat.SubCategories:
                            _apply_ogs(view, sub.Id, ogs)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    try:
        for ct in (DB.FilteredElementCollector(doc)
                     .OfClass(DB.CADLinkType)
                     .ToElements()):
            try:
                cat = ct.Category
                if cat:
                    _apply_ogs(view, cat.Id, ogs)
                    try:
                        for sub in cat.SubCategories:
                            _apply_ogs(view, sub.Id, ogs)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass


def apply_all_grey_overrides(view):
    apply_grey_to_all_categories(view)
    apply_grey_to_all_elements(view)
    apply_grey_to_links(view)

# -----------------------------------------------------------------------------
# SHAPE EDITOR HELPERS
# -----------------------------------------------------------------------------

def get_element_shape_editor(el):
    try:
        editor = el.SlabShapeEditor
        if editor is not None:
            return editor
    except Exception:
        pass
    try:
        editor = el.GetSlabShapeEditor()
        if editor is not None:
            return editor
    except Exception:
        pass
    return None


def is_shape_edited(el):
    editor = get_element_shape_editor(el)
    if editor is not None:
        try:
            return editor.IsEnabled
        except Exception:
            pass
    return False

# -----------------------------------------------------------------------------
# VIEW RANGE HELPERS
# -----------------------------------------------------------------------------

def get_min_z_of_copies(document, copy_ids):
    min_z = 0.0
    for c_id in copy_ids:
        try:
            el = document.GetElement(c_id)
            if el is None:
                continue
            bb = el.get_BoundingBox(None)
            if bb is not None and bb.Min.Z < min_z:
                min_z = bb.Min.Z
        except Exception:
            pass
    return min_z


def extend_pat_view_range(pat_view, min_z_offset):
    try:
        vr = pat_view.GetViewRange()
        nb = min_z_offset - 1.0
        vr.SetOffset(DB.PlanViewPlane.BottomClipPlane, nb)
        try:
            vr.SetOffset(DB.PlanViewPlane.ViewDepthPlane, nb - 1.0)
        except Exception:
            pass
        pat_view.SetViewRange(vr)
    except Exception:
        pass

# -----------------------------------------------------------------------------
# FILE HELPERS
# -----------------------------------------------------------------------------

def safe_filename(name):
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()


def safe_delete(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def list_dwgs(folder):
    try:
        return set(f for f in os.listdir(folder) if f.lower().endswith(".dwg"))
    except Exception:
        return set()

# -----------------------------------------------------------------------------
# CATEGORY / VIEW HELPERS
# -----------------------------------------------------------------------------

def get_category(document, bic):
    try:
        return DB.Category.GetCategory(document, bic)
    except Exception:
        return None


def collect_categories(document, view, category_type):
    cats = []
    for c in document.Settings.Categories:
        try:
            if (c.CategoryType == category_type
                    and c.get_AllowsVisibilityControl(view)):
                cats.append(c)
        except Exception:
            pass
    return cats


def duplicate_view(view, new_name):
    new_id   = view.Duplicate(DB.ViewDuplicateOption.Duplicate)
    new_view = doc.GetElement(new_id)
    test_name = new_name
    i = 1
    while True:
        try:
            new_view.Name = test_name
            break
        except Exception:
            i += 1
            test_name = "{}_{}".format(new_name, i)
    return new_view


def prepare_overlay_view(source_view, target_view):
    for attr in ("Scale", "CropBoxActive", "CropBox"):
        try:
            setattr(target_view, attr, getattr(source_view, attr))
        except Exception:
            pass
    try:
        target_view.CropBoxVisible = False
    except Exception:
        pass
    try:
        p = target_view.get_Parameter(
            DB.BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
        if p and not p.IsReadOnly:
            p.Set(0)
    except Exception:
        pass


def hide_categories(view, categories):
    for cat in categories:
        try:
            view.SetCategoryHidden(cat.Id, True)
        except Exception:
            pass


def hide_categories_by_ids(view, ids_to_hide, categories):
    for cat in categories:
        try:
            if eid_val(cat.Id) in ids_to_hide:
                view.SetCategoryHidden(cat.Id, True)
        except Exception:
            pass


def validate_pattern_categories(document, bic_list):
    valid, pattern_ids = [], set()
    for bic in bic_list:
        cat = get_category(document, bic)
        if cat is not None:
            valid.append(cat)
            pattern_ids.add(eid_val(cat.Id))
    return valid, pattern_ids

# -----------------------------------------------------------------------------
# SHEET HELPERS
# -----------------------------------------------------------------------------

def get_titleblock_type_id(document):
    tbs = (DB.FilteredElementCollector(document)
           .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
           .WhereElementIsElementType()
           .ToElements())
    return tbs[0].Id if tbs else None


def get_unique_sheet_number(document, prefix):
    existing = set()
    for s in DB.FilteredElementCollector(document).OfClass(DB.ViewSheet):
        try:
            existing.add(s.SheetNumber)
        except Exception:
            pass
    i = 1
    while True:
        num = "{}{}".format(prefix, i)
        if num not in existing:
            return num
        i += 1


def create_temp_sheet(document, titleblock_type_id, name_hint):
    sheet = DB.ViewSheet.Create(document, titleblock_type_id)
    sheet.SheetNumber = get_unique_sheet_number(document, TEMP_SHEET_PREFIX)
    try:
        sheet.Name = "TMP {}".format(name_hint)
    except Exception:
        pass
    return sheet


def get_sheet_center(sheet):
    o = sheet.Outline
    return DB.XYZ(
        (o.Min.U + o.Max.U) / 2.0,
        (o.Min.V + o.Max.V) / 2.0,
        0
    )


def get_no_title_viewport_type_id(document):
    vp_cat = get_category(document, DB.BuiltInCategory.OST_Viewports)
    if not vp_cat:
        return None
    for et in DB.FilteredElementCollector(document).WhereElementIsElementType():
        try:
            if et.Category and et.Category.Id == vp_cat.Id:
                n = et.Name or ""
                if "No Title" in n or "No title" in n:
                    return et.Id
        except Exception:
            pass
    return None

# -----------------------------------------------------------------------------
# COPY VERIFICATION
# -----------------------------------------------------------------------------

def verify_and_hide_originals(document, pat_view, copied_map, flat_copies):
    safe_to_hide  = List[DB.ElementId]()
    unsafe_copies = []

    for orig_id, c_id in copied_map:
        if c_id not in flat_copies:
            unsafe_copies.append(c_id)
            continue
        copied_el = document.GetElement(c_id)
        if copied_el is None:
            unsafe_copies.append(c_id)
            continue
        try:
            bb = copied_el.get_BoundingBox(pat_view)
            if bb is not None:
                safe_to_hide.Add(orig_id)
            else:
                unsafe_copies.append(c_id)
        except Exception:
            unsafe_copies.append(c_id)

    if safe_to_hide.Count > 0:
        try:
            pat_view.HideElements(safe_to_hide)
        except Exception:
            pass

    return unsafe_copies

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

def main():
    pattern_cats, pattern_ids = validate_pattern_categories(
        doc, PATTERN_CATEGORIES)
    if not pattern_cats:
        forms.alert("No valid pattern categories found.", exitscript=True)
        return

    titleblock_type_id = get_titleblock_type_id(doc)
    if not titleblock_type_id:
        forms.alert("No title block found in project.", exitscript=True)
        return

    selected_views = forms.select_views(
        title="Select plan views (all merged into ONE DWG)",
        filterfunc=lambda v: isinstance(v, DB.ViewPlan) and not v.IsTemplate
    )
    if not selected_views:
        return

    folder = forms.pick_folder(title="Choose export folder")
    if not folder:
        return

    export_name = forms.ask_for_string(
        default="MergedExport_Grey253",
        prompt="Name for the single merged DWG file:",
        title="DWG file name") or "MergedExport_Grey253"
    export_name = safe_filename(export_name)

    no_title_vp_type_id = get_no_title_viewport_type_id(doc)

    # ── STEP 1: Force materials in HOST + LINKED docs to grey ────────────
    # (uses its own transactions per document)
    processed_docs = force_all_materials_grey()

    # ── STEP 2: Grey export settings in host ─────────────────────────────
    grey_settings = None
    with revit.Transaction("Create grey DWG export settings"):
        try:
            grey_settings = build_grey_export_settings(doc)
        except Exception:
            grey_settings = None

    if grey_settings is None:
        restore_all_materials(processed_docs)
        forms.alert("Could not build grey export settings.", exitscript=True)
        return

    options = get_grey_export_options(doc, grey_settings)

    all_flat_copies         = []
    all_copied_elements_map = []
    all_temp_view_ids       = []
    single_sheet            = None

    try:
        with revit.Transaction("Build merged temp sheet"):

            single_sheet = create_temp_sheet(
                doc, titleblock_type_id, export_name)

            try:
                tb_cat = get_category(
                    doc, DB.BuiltInCategory.OST_TitleBlocks)
                if tb_cat:
                    single_sheet.SetCategoryHidden(tb_cat.Id, True)
            except Exception:
                pass

            sheet_center = get_sheet_center(single_sheet)

            for source_view in selected_views:
                model_cats = collect_categories(
                    doc, source_view, DB.CategoryType.Model)
                anno_cats  = collect_categories(
                    doc, source_view, DB.CategoryType.Annotation)

                block_ids = set()
                for cat in model_cats:
                    cid = eid_val(cat.Id)
                    if cid not in pattern_ids:
                        block_ids.add(cid)

                # PATTERN VIEW
                pat_view = duplicate_view(
                    source_view, source_view.Name + TEMP_PAT_SUFFIX)
                prepare_overlay_view(source_view, pat_view)
                hide_categories_by_ids(pat_view, block_ids, model_cats)
                hide_categories(pat_view, anno_cats)
                apply_all_grey_overrides(pat_view)

                # BLOCKS VIEW
                blk_view = duplicate_view(
                    source_view, source_view.Name + TEMP_BLK_SUFFIX)
                prepare_overlay_view(source_view, blk_view)
                try:
                    blk_view.DetailLevel = DB.ViewDetailLevel.Fine
                except Exception:
                    pass
                hide_categories_by_ids(blk_view, pattern_ids, model_cats)
                apply_all_grey_overrides(blk_view)

                # Shape-edited copies
                per_view_map = []
                for cat in pattern_cats:
                    try:
                        for el in (
                            DB.FilteredElementCollector(doc, source_view.Id)
                              .OfCategoryId(cat.Id)
                              .WhereElementIsNotElementType()):
                            if is_shape_edited(el):
                                try:
                                    ids_to_copy = List[DB.ElementId]()
                                    ids_to_copy.Add(el.Id)
                                    copied_ids = \
                                        DB.ElementTransformUtils.CopyElements(
                                            doc, ids_to_copy, DB.XYZ.Zero)
                                    for c_id in copied_ids:
                                        per_view_map.append((el.Id, c_id))
                                        all_copied_elements_map.append(
                                            (el.Id, c_id))
                                except Exception:
                                    pass
                    except Exception:
                        pass

                per_view_flats = []
                if per_view_map:
                    try:
                        doc.Regenerate()
                    except Exception:
                        pass

                    for orig_id, c_id in per_view_map:
                        try:
                            copied_el = doc.GetElement(c_id)
                            if copied_el is None:
                                continue
                            editor = get_element_shape_editor(copied_el)
                            if editor is not None:
                                editor.ResetSlabShape()
                                per_view_flats.append(c_id)
                                all_flat_copies.append(c_id)
                        except Exception:
                            pass

                    if per_view_flats:
                        min_z = get_min_z_of_copies(doc, per_view_flats)
                        if min_z < 0:
                            extend_pat_view_range(pat_view, min_z)
                            try:
                                doc.Regenerate()
                            except Exception:
                                pass

                    unsafe_copies = verify_and_hide_originals(
                        doc, pat_view, per_view_map, per_view_flats)

                    ogs = make_grey_override()
                    for c_id in per_view_flats:
                        try:
                            pat_view.SetElementOverrides(c_id, ogs)
                        except Exception:
                            pass

                    for c_id in unsafe_copies:
                        try:
                            doc.Delete(c_id)
                            if c_id in per_view_flats:
                                per_view_flats.remove(c_id)
                            if c_id in all_flat_copies:
                                all_flat_copies.remove(c_id)
                        except Exception:
                            pass

                try:
                    doc.Regenerate()
                except Exception:
                    pass
                apply_all_grey_overrides(pat_view)
                apply_all_grey_overrides(blk_view)

                if DB.Viewport.CanAddViewToSheet(
                        doc, single_sheet.Id, pat_view.Id):
                    vp1 = DB.Viewport.Create(
                        doc, single_sheet.Id, pat_view.Id, sheet_center)
                    if no_title_vp_type_id:
                        try:
                            vp1.ChangeTypeId(no_title_vp_type_id)
                        except Exception:
                            pass

                if DB.Viewport.CanAddViewToSheet(
                        doc, single_sheet.Id, blk_view.Id):
                    vp2 = DB.Viewport.Create(
                        doc, single_sheet.Id, blk_view.Id, sheet_center)
                    if no_title_vp_type_id:
                        try:
                            vp2.ChangeTypeId(no_title_vp_type_id)
                        except Exception:
                            pass

                all_temp_view_ids.append(pat_view.Id)
                all_temp_view_ids.append(blk_view.Id)

            doc.Regenerate()

        # ── EXPORT ────────────────────────────────────────────────────
        before      = list_dwgs(folder)
        target_path = os.path.join(folder, export_name + ".dwg")
        safe_delete(target_path)

        ids = List[DB.ElementId]()
        ids.Add(single_sheet.Id)
        doc.Export(folder, export_name, ids, options)

        if not os.path.exists(target_path):
            after = list_dwgs(folder)
            new_files = list(after - before)
            if new_files:
                paths = [os.path.join(folder, f) for f in new_files]
                paths.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                newest = paths[0]
                try:
                    os.rename(newest, target_path)
                except Exception:
                    target_path = newest

        if os.path.exists(target_path):
            forms.alert(
                "Exported to:\n{}\n\n"
                "All hatches (host + links) are pattern lines in Grey 253.\n"
                "For BLACK background: AutoCAD > Options > Display > "
                "Colors > 2D model space uniform > Black.".format(
                    target_path))

    except Exception as ex:
        try:
            forms.alert("Export failed:\n{}".format(str(ex)))
        except Exception:
            pass

    finally:
        # Cleanup temp sheet, views, copies
        cleanup_ids = []
        if single_sheet is not None:
            cleanup_ids.append(single_sheet.Id)
        cleanup_ids.extend(all_temp_view_ids)
        cleanup_ids.extend(all_flat_copies)
        cleanup_ids.extend(
            c_id for _, c_id in all_copied_elements_map
            if c_id not in all_flat_copies
        )

        if cleanup_ids:
            with revit.Transaction("Delete merged temp export objects"):
                for eid in cleanup_ids:
                    try:
                        doc.Delete(eid)
                    except Exception:
                        pass

        # RESTORE materials in host + all links
        restore_all_materials(processed_docs)

        try:
            with revit.Transaction("Cleanup grey export settings"):
                doc.Delete(grey_settings.Id)
        except Exception:
            pass


main()