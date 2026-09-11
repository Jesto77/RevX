# -*- coding: utf-8 -*-
"""
Export & Merge DWG (one button)
For each selected plan view: exports it as a merged-view DWG (Pattern +
Blocks). Because Revit's multi-view merged export links the second view
in as an external reference instead of inlining it, this step actually
leaves TWO physical DWG files on disk for that one view (a master file
+ an xref file). This tool then automatically imports both of those
files into a blank scratch document at their original real-world
coordinates and re-exports them as a single standalone DWG with no
external references, deleting the two originals afterward - so you end
up with one flattened file per view, with no manual second step. Your
live project is never touched by the merge step.
Author: Jesto Joy
"""

from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import clr
import os
import traceback

doc = revit.doc
app = doc.Application
output = script.get_output()

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

EXPORT_SETUP_NAME = None

TEMP_PAT_SUFFIX = "_TMP_PAT"
TEMP_BLK_SUFFIX = "_TMP_BLK"

GREY_RGB = (128, 128, 128)

# Set to False to preserve all original Revit view colors, planting greens,
# and material hatches exactly as drawn in your project.
APPLY_GREY_OVERRIDE = False

# Marker written to every flat-copy element's Comments parameter
COPY_MARKER = "RevX_DWG_TEMP_COPY"


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
# HELPERS - GREY OVERRIDE (OPTIONAL)
# -----------------------------------------------------------------------------

def build_grey_override():
    grey = DB.Color(GREY_RGB[0], GREY_RGB[1], GREY_RGB[2])
    ogs = DB.OverrideGraphicSettings()

    try:
        ogs.SetProjectionLineColor(grey)
    except Exception:
        pass
    try:
        ogs.SetCutLineColor(grey)
    except Exception:
        pass
    try:
        ogs.SetSurfaceForegroundPatternColor(grey)
    except Exception:
        pass
    try:
        ogs.SetCutForegroundPatternColor(grey)
    except Exception:
        pass

    return ogs


def apply_grey_override_to_all_categories_and_subcategories(view, document, ogs):
    for cat in document.Settings.Categories:
        try:
            if cat.get_AllowsVisibilityControl(view):
                view.SetCategoryOverrides(cat.Id, ogs)
        except Exception:
            pass

        try:
            subcats = cat.SubCategories
            if subcats:
                for subcat in subcats:
                    try:
                        if subcat.get_AllowsVisibilityControl(view):
                            view.SetCategoryOverrides(subcat.Id, ogs)
                    except Exception:
                        pass
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

    opts.MergedViews = True

    # Use true RGB color so original view colors and hatches come through exactly
    try:
        opts.Colors = DB.ExportColorMode.TrueColorPerView
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


def detach_view_template(view):
    try:
        view.ViewTemplateId = DB.ElementId.InvalidElementId
    except Exception:
        pass


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
# HELPERS - EXPORT
# -----------------------------------------------------------------------------

def export_views_raw(document, folder, export_name, candidate_names, view_ids, options):
    for name in [export_name] + list(candidate_names):
        safe_delete(os.path.join(folder, safe_filename(name) + ".dwg"))

    before = list_dwgs(folder)

    ids = List[DB.ElementId]()
    for vid in view_ids:
        ids.Add(vid)

    export_ok = document.Export(folder, export_name, ids, options)

    after      = list_dwgs(folder)
    new_files  = sorted(after - before)
    return [os.path.join(folder, f) for f in new_files], export_ok


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
# HELPERS - MERGE STEP
# -----------------------------------------------------------------------------

def get_scratch_plan_view_family_type(document):
    for vft in DB.FilteredElementCollector(document).OfClass(DB.ViewFamilyType):
        if vft.ViewFamily == DB.ViewFamily.FloorPlan:
            return vft.Id
    return None


def get_scratch_level(document):
    levels = DB.FilteredElementCollector(document).OfClass(DB.Level).ToElements()
    if levels:
        return levels[0]
    return None


def hide_all_categories_scratch(view, document):
    for cat in document.Settings.Categories:
        try:
            if cat.get_AllowsVisibilityControl(view):
                view.SetCategoryHidden(cat.Id, True)
        except Exception:
            pass


def import_dwg_to_scratch(document, view, path):
    options = DB.DWGImportOptions()
    try:
        options.Placement = DB.ImportPlacement.Origin
    except Exception:
        pass
    try:
        options.ThisViewOnly = True
    except Exception:
        pass
    try:
        # Preserve original RGB colors on import
        options.ColorMode = DB.ImportColorMode.Preserved
    except Exception:
        pass

    ref = clr.Reference[DB.ElementId]()
    document.Import(path, options, view, ref)
    return ref.Value


def merge_two_dwgs(paths, save_path, original_options):
    folder     = os.path.dirname(save_path)
    final_name = safe_filename(os.path.splitext(os.path.basename(save_path))[0])

    scratch_doc = app.NewProjectDocument(DB.UnitSystem.Metric)

    try:
        vft_id = get_scratch_plan_view_family_type(scratch_doc)
        level  = get_scratch_level(scratch_doc)
        if not vft_id or not level:
            output.print_md("**Merge stopped:** the blank scratch document has no "
                             "floor plan view type / level to host the merge.")
            return None

        pat_path = None
        blk_path = None
        for path in paths:
            filename = os.path.basename(path).lower()
            if "blk" in filename or "temp_blk" in filename:
                blk_path = path
            else:
                pat_path = path

        if (not pat_path or not blk_path) and len(paths) >= 2:
            pat_path = paths[0]
            blk_path = paths[1]

        t = DB.Transaction(scratch_doc, "Merge DWGs - import")
        t.Start()
        try:
            # Copy DWG Export Settings from source project to scratch project
            try:
                source_settings = DB.FilteredElementCollector(doc).OfClass(DB.ExportDWGSettings).ToElementIds()
                if source_settings.Count > 0:
                    copy_opts = DB.CopyPasteOptions()
                    DB.ElementTransformUtils.CopyElements(doc, source_settings, scratch_doc, DB.Transform.Identity, copy_opts)
            except Exception:
                pass

            temp_view = DB.ViewPlan.Create(scratch_doc, vft_id, level.Id)
            hide_all_categories_scratch(temp_view, scratch_doc)

            pat_id = None
            blk_id = None

            # 1. Import Pattern (Floors, Topography, etc.)
            if pat_path:
                pat_id = import_dwg_to_scratch(scratch_doc, temp_view, pat_path)

            # 2. Import Blocks (Walls, planting, details, annotations, etc.)
            if blk_path:
                blk_id = import_dwg_to_scratch(scratch_doc, temp_view, blk_path)

            # 3. Correct Draw Order Alignment (Pattern bottom, Blocks top)
            if pat_id and blk_id:
                try:
                    DB.DetailElementOrderUtils.SendToBack(scratch_doc, temp_view, pat_id)
                    DB.DetailElementOrderUtils.BringToFront(scratch_doc, temp_view, blk_id)
                except Exception:
                    pass

            scratch_doc.Regenerate()
            t.Commit()
        except Exception:
            t.RollBack()
            raise

        exp_options = None
        if EXPORT_SETUP_NAME:
            try:
                names = [s.Name for s in DB.FilteredElementCollector(scratch_doc).OfClass(DB.ExportDWGSettings)]
                if EXPORT_SETUP_NAME in names:
                    exp_options = DB.DWGExportOptions.GetPredefinedOptions(scratch_doc, EXPORT_SETUP_NAME)
            except Exception:
                pass

        if not exp_options:
            exp_options = DB.DWGExportOptions()
            try:
                exp_options.FileVersion = original_options.FileVersion
            except Exception:
                pass
            try:
                exp_options.LineScaling = original_options.LineScaling
            except Exception:
                pass
            try:
                exp_options.TargetUnit = original_options.TargetUnit
            except Exception:
                pass

        # Preserve exact RGB TrueColors on export
        exp_options.Colors = DB.ExportColorMode.TrueColorPerView
        exp_options.MergedViews = False

        ids = List[DB.ElementId]()
        ids.Add(temp_view.Id)

        before      = list_dwgs(folder)
        target_path = os.path.join(folder, final_name + ".dwg")
        safe_delete(target_path)

        scratch_doc.Export(folder, final_name, ids, exp_options)

        if os.path.exists(target_path):
            return target_path

        new_dwg = get_newest_new_dwg(folder, before)
        if new_dwg and os.path.exists(new_dwg):
            try:
                safe_delete(target_path)
                os.rename(new_dwg, target_path)
                return target_path
            except Exception:
                return new_dwg

        return None

    except Exception:
        output.print_md("**Merge FAILED** —")
        output.print_code(traceback.format_exc())
        return None

    finally:
        try:
            scratch_doc.Close(False)
        except Exception:
            pass


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

def main():
    pattern_cats, pattern_ids = validate_pattern_categories(
        doc, PATTERN_CATEGORIES)
    if not pattern_cats:
        output.print_md("**Stopped:** none of the pattern categories "
                         "(Floors/Roofs/Stairs/Toposolid/Topography) "
                         "resolved to a valid Category in this document/version.")
        return

    selected_views = forms.select_views(
        title="Select plan views to export as DWG",
        filterfunc=lambda v: isinstance(v, DB.ViewPlan) and not v.IsTemplate
    )

    if not selected_views:
        output.print_md("**Stopped:** no views were selected.")
        return

    folder = forms.pick_folder(title="Choose export folder")
    if not folder:
        output.print_md("**Stopped:** no export folder was chosen.")
        return

    options  = get_export_options(doc, EXPORT_SETUP_NAME)
    grey_ogs = build_grey_override()

    for source_view in selected_views:
        base_name = safe_filename(source_view.Name)

        flat_copies         = []
        copied_elements_map = []
        temp_ids            = []

        try:
            with revit.Transaction(
                    "Create temp export views: " + source_view.Name):

                # PATTERN VIEW - floors/roofs/stairs/toposolid
                pat_view = duplicate_view(
                    source_view, source_view.Name + TEMP_PAT_SUFFIX)
                detach_view_template(pat_view)
                prepare_overlay_view(source_view, pat_view)

                model_cats = collect_categories(
                    doc, pat_view, DB.CategoryType.Model)
                anno_cats  = collect_categories(
                    doc, pat_view, DB.CategoryType.Annotation)

                block_ids = set()
                for cat in model_cats:
                    cid = eid_val(cat.Id)
                    if cid not in pattern_ids:
                        block_ids.add(cid)

                hide_categories_by_ids(pat_view, block_ids, model_cats)
                hide_categories(pat_view, anno_cats)

                # BLOCKS VIEW - everything else
                blk_view = duplicate_view(
                    source_view, source_view.Name + TEMP_BLK_SUFFIX)
                detach_view_template(blk_view)
                prepare_overlay_view(source_view, blk_view)
                try:
                    blk_view.DetailLevel = DB.ViewDetailLevel.Fine
                except Exception:
                    pass
                hide_categories_by_ids(blk_view, pattern_ids, model_cats)

                # GREY OVERRIDE - Only active if APPLY_GREY_OVERRIDE is True
                if APPLY_GREY_OVERRIDE:
                    apply_grey_override_to_all_categories_and_subcategories(pat_view, doc, grey_ogs)
                    apply_grey_override_to_all_categories_and_subcategories(blk_view, doc, grey_ogs)

                # SCAN & COPY SHAPE-EDITED ELEMENTS
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

                # REGENERATE AFTER ALL COPIES
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

                doc.Regenerate()

                temp_ids = [pat_view.Id, blk_view.Id] + flat_copies

            # EXPORT
            view_ids_to_export = [pat_view.Id, blk_view.Id]
            raw_paths, export_ok = export_views_raw(
                doc, folder, base_name,
                [pat_view.Name, blk_view.Name],
                view_ids_to_export, options)

            if not raw_paths:
                output.print_md(
                    "**{}**: Export() returned {} but no DWG file was found "
                    "in `{}` afterward.".format(
                        source_view.Name, export_ok, folder))
                continue

            # MERGE
            final_target = os.path.join(folder, base_name + ".dwg")
            merged_path = merge_two_dwgs(raw_paths, final_target, options)

            if merged_path:
                output.print_md("**{}**: merged file exported to `{}`".format(
                    source_view.Name, merged_path))
                for p in raw_paths:
                    try:
                        if os.path.exists(p) and os.path.abspath(p) != os.path.abspath(merged_path):
                            os.remove(p)
                    except Exception:
                        output.print_md("Could not delete intermediate file: `{}`".format(p))
            else:
                output.print_md(
                    "**{}**: merge FAILED - the original exported file(s) "
                    "are still on disk, nothing was deleted:".format(source_view.Name))
                for p in raw_paths:
                    output.print_md("- `{}`".format(p))

        except Exception:
            output.print_md("**{}**: FAILED —".format(source_view.Name))
            output.print_code(traceback.format_exc())

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