# -*- coding: utf-8 -*-
"""
DWG Export - Pattern and Blocks as SEPARATE DWG files
Author: Jesto Joy

Exports two DWG files per view:
  - ViewName_PAT.dwg  (pattern categories only, shape-reset)
  - ViewName_BLK.dwg  (all other categories + annotations)
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

EXPORT_SETUP_NAME  = None

TEMP_PAT_SUFFIX    = "_TMP_PAT"
TEMP_BLK_SUFFIX    = "_TMP_BLK"
TEMP_SHEET_PREFIX  = "TMP_DWG_"

PAT_FILE_SUFFIX    = "_PAT"
BLK_FILE_SUFFIX    = "_BLK"


# -----------------------------------------------------------------------------
# COMPATIBILITY
# -----------------------------------------------------------------------------

def eid_val(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


# -----------------------------------------------------------------------------
# HELPERS - SHAPE EDITOR
# -----------------------------------------------------------------------------

def get_element_shape_editor(el):
    try:
        editor = el.SlabShapeEditor
        if editor is not None:
            return editor
    except AttributeError:
        pass
    except Exception:
        pass

    try:
        editor = el.GetSlabShapeEditor()
        if editor is not None:
            return editor
    except AttributeError:
        pass
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
# HELPERS - VIEW RANGE
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
        new_bottom = min_z_offset - 1.0

        vr.SetOffset(DB.PlanViewPlane.BottomClipPlane, new_bottom)

        try:
            vr.SetOffset(DB.PlanViewPlane.ViewDepthPlane, new_bottom - 1.0)
        except Exception:
            pass

        pat_view.SetViewRange(vr)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# HELPERS - FILE / EXPORT
# -----------------------------------------------------------------------------

def safe_filename(name):
    bad = '\\/:*?"<>|'
    for ch in bad:
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
        return set([f for f in os.listdir(folder) if f.lower().endswith(".dwg")])
    except Exception:
        return set()


def get_newest_new_dwg(folder, before_files):
    after_files = list_dwgs(folder)
    new_files   = list(after_files - before_files)
    if not new_files:
        return None
    paths = [os.path.join(folder, f) for f in new_files]
    paths.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return paths[0]


def get_export_options(document, setup_name=None):
    if setup_name:
        try:
            names = [s.Name for s in
                     DB.FilteredElementCollector(document)
                       .OfClass(DB.ExportDWGSettings)]
            if setup_name in names:
                opts = DB.DWGExportOptions.GetPredefinedOptions(
                    document, setup_name)
            else:
                opts = DB.DWGExportOptions()
        except Exception:
            opts = DB.DWGExportOptions()
    else:
        opts = DB.DWGExportOptions()

    try:
        opts.MergedViews = True
    except Exception:
        pass

    return opts


# -----------------------------------------------------------------------------
# HELPERS - CATEGORIES / VIEWS
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
    try:
        target_view.Scale = source_view.Scale
    except Exception:
        pass

    try:
        target_view.CropBoxActive = source_view.CropBoxActive
    except Exception:
        pass

    try:
        target_view.CropBox = source_view.CropBox
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
    valid       = []
    pattern_ids = set()
    for bic in bic_list:
        cat = get_category(document, bic)
        if cat is not None:
            valid.append(cat)
            pattern_ids.add(eid_val(cat.Id))
    return valid, pattern_ids


# -----------------------------------------------------------------------------
# HELPERS - SHEETS / VIEWPORTS
# -----------------------------------------------------------------------------

def get_titleblock_type_id(document):
    tbs = (DB.FilteredElementCollector(document)
           .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
           .WhereElementIsElementType()
           .ToElements())
    if tbs:
        return tbs[0].Id
    return None


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
    u = (o.Min.U + o.Max.U) / 2.0
    v = (o.Min.V + o.Max.V) / 2.0
    return DB.XYZ(u, v, 0)


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


def export_sheet(document, folder, export_name, sheet_id, options):
    before      = list_dwgs(folder)
    target_path = os.path.join(folder, export_name + ".dwg")
    safe_delete(target_path)

    ids = List[DB.ElementId]()
    ids.Add(sheet_id)

    result = document.Export(folder, export_name, ids, options)

    if os.path.exists(target_path):
        return result, target_path

    new_dwg = get_newest_new_dwg(folder, before)
    if new_dwg and os.path.exists(new_dwg):
        try:
            safe_delete(target_path)
            os.rename(new_dwg, target_path)
            return result, target_path
        except Exception:
            return result, new_dwg

    return result, None


# -----------------------------------------------------------------------------
# HELPERS - VERIFY COPIES
# -----------------------------------------------------------------------------

def verify_and_hide_originals(document, pat_view, copied_elements_map, flat_copies):
    safe_to_hide  = List[DB.ElementId]()
    unsafe_copies = []

    for orig_id, c_id in copied_elements_map:
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
        return

    titleblock_type_id = get_titleblock_type_id(doc)
    if not titleblock_type_id:
        return

    selected_views = forms.select_views(
        title="Select plan views to export as SEPARATE PAT + BLK DWG files",
        filterfunc=lambda v: isinstance(v, DB.ViewPlan) and not v.IsTemplate
    )

    if not selected_views:
        return

    folder = forms.pick_folder(title="Choose export folder")
    if not folder:
        return

    options             = get_export_options(doc, EXPORT_SETUP_NAME)
    no_title_vp_type_id = get_no_title_viewport_type_id(doc)

    for source_view in selected_views:
        base_name = safe_filename(source_view.Name)

        flat_copies         = []
        copied_elements_map = []
        temp_ids            = []

        try:
            model_cats = collect_categories(
                doc, source_view, DB.CategoryType.Model)
            anno_cats  = collect_categories(
                doc, source_view, DB.CategoryType.Annotation)

            block_ids = set()
            for cat in model_cats:
                cid = eid_val(cat.Id)
                if cid not in pattern_ids:
                    block_ids.add(cid)

            with revit.Transaction(
                    "Create temp export objects: " + source_view.Name):

                # ── PATTERN VIEW ──────────────────────────────────────────
                pat_view = duplicate_view(
                    source_view, source_view.Name + TEMP_PAT_SUFFIX)
                prepare_overlay_view(source_view, pat_view)
                hide_categories_by_ids(pat_view, block_ids, model_cats)
                hide_categories(pat_view, anno_cats)

                # ── BLOCKS VIEW ───────────────────────────────────────────
                blk_view = duplicate_view(
                    source_view, source_view.Name + TEMP_BLK_SUFFIX)
                prepare_overlay_view(source_view, blk_view)
                try:
                    blk_view.DetailLevel = DB.ViewDetailLevel.Fine
                except Exception:
                    pass
                hide_categories_by_ids(blk_view, pattern_ids, model_cats)

                # ── SCAN & COPY SHAPE-EDITED ELEMENTS ─────────────────────
                for cat in pattern_cats:
                    try:
                        collector = (
                            DB.FilteredElementCollector(doc, source_view.Id)
                              .OfCategoryId(cat.Id)
                              .WhereElementIsNotElementType()
                        )
                        for el in collector:
                            if is_shape_edited(el):
                                try:
                                    ids_to_copy = List[DB.ElementId]()
                                    ids_to_copy.Add(el.Id)
                                    copied_ids = \
                                        DB.ElementTransformUtils.CopyElements(
                                            doc, ids_to_copy, DB.XYZ.Zero)
                                    for c_id in copied_ids:
                                        copied_elements_map.append(
                                            (el.Id, c_id))
                                except Exception:
                                    pass
                    except Exception:
                        pass

                # ── REGENERATE AFTER ALL COPIES ───────────────────────────
                if copied_elements_map:
                    try:
                        doc.Regenerate()
                    except Exception:
                        pass

                    # RESET SLAB SHAPE ON COPIES
                    for orig_id, c_id in copied_elements_map:
                        try:
                            copied_el = doc.GetElement(c_id)
                            if copied_el is None:
                                continue

                            editor = get_element_shape_editor(copied_el)
                            if editor is not None:
                                editor.ResetSlabShape()
                                flat_copies.append(c_id)
                        except Exception:
                            pass

                    # EXTEND VIEW RANGE IF COPIES BELOW LEVEL
                    if flat_copies:
                        min_z = get_min_z_of_copies(doc, flat_copies)
                        if min_z < 0:
                            extend_pat_view_range(pat_view, min_z)
                            try:
                                doc.Regenerate()
                            except Exception:
                                pass

                    # VERIFY COPIES VISIBLE THEN HIDE ORIGINALS
                    unsafe_copies = verify_and_hide_originals(
                        doc, pat_view, copied_elements_map, flat_copies)

                    # DELETE UNSAFE COPIES
                    for c_id in unsafe_copies:
                        try:
                            doc.Delete(c_id)
                            if c_id in flat_copies:
                                flat_copies.remove(c_id)
                        except Exception:
                            pass

                # ── PATTERN SHEET ─────────────────────────────────────────
                pat_sheet = create_temp_sheet(
                    doc, titleblock_type_id, base_name + "_PAT")

                try:
                    tb_cat = get_category(
                        doc, DB.BuiltInCategory.OST_TitleBlocks)
                    if tb_cat:
                        pat_sheet.SetCategoryHidden(tb_cat.Id, True)
                except Exception:
                    pass

                pat_center = get_sheet_center(pat_sheet)

                if not DB.Viewport.CanAddViewToSheet(
                        doc, pat_sheet.Id, pat_view.Id):
                    raise Exception(
                        "Pattern view cannot be added to pattern sheet.")

                vp_pat = DB.Viewport.Create(
                    doc, pat_sheet.Id, pat_view.Id, pat_center)

                if no_title_vp_type_id:
                    try:
                        vp_pat.ChangeTypeId(no_title_vp_type_id)
                    except Exception:
                        pass

                # ── BLOCKS SHEET ──────────────────────────────────────────
                blk_sheet = create_temp_sheet(
                    doc, titleblock_type_id, base_name + "_BLK")

                try:
                    tb_cat = get_category(
                        doc, DB.BuiltInCategory.OST_TitleBlocks)
                    if tb_cat:
                        blk_sheet.SetCategoryHidden(tb_cat.Id, True)
                except Exception:
                    pass

                blk_center = get_sheet_center(blk_sheet)

                if not DB.Viewport.CanAddViewToSheet(
                        doc, blk_sheet.Id, blk_view.Id):
                    raise Exception(
                        "Blocks view cannot be added to blocks sheet.")

                vp_blk = DB.Viewport.Create(
                    doc, blk_sheet.Id, blk_view.Id, blk_center)

                if no_title_vp_type_id:
                    try:
                        vp_blk.ChangeTypeId(no_title_vp_type_id)
                    except Exception:
                        pass

                doc.Regenerate()

                temp_ids = [
                    pat_sheet.Id, blk_sheet.Id,
                    pat_view.Id, blk_view.Id
                ] + flat_copies

            # ── EXPORT PATTERN DWG ────────────────────────────────────────
            pat_export_name = base_name + PAT_FILE_SUFFIX
            export_sheet(
                doc, folder, pat_export_name, pat_sheet.Id, options)

            # ── EXPORT BLOCKS DWG ─────────────────────────────────────────
            blk_export_name = base_name + BLK_FILE_SUFFIX
            export_sheet(
                doc, folder, blk_export_name, blk_sheet.Id, options)

        except Exception:
            pass

        finally:
            all_to_delete = list(temp_ids) + [
                c_id for _, c_id in copied_elements_map
                if c_id not in flat_copies
            ]

            if all_to_delete:
                with revit.Transaction(
                        "Delete temp export objects: " + source_view.Name):
                    for eid in all_to_delete:
                        try:
                            doc.Delete(eid)
                        except Exception:
                            pass


main()