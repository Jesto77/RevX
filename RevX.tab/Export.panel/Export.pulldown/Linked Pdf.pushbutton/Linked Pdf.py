# -*- coding: utf-8 -*-
"""
Export PDF from HOST file with LINKED MODELS (Flattened Patterns + Titleblock)
For each selected plan view in the host/sheet file:
- Scans all Revit Link Instances for shape-edited floors/roofs/toposolids
- Copies them into the host document at correct link-transform coordinates
- Flattens them (removes triangulation/split-line distortions)
- Hides the linked warped originals in export view
- Optionally places view on a temporary sheet with selected titleblock
- Exports as clean PDF matching view name
- Cleans up temporary host copies + views + sheets
Author: Jesto Joy (Modified for Linked Model PDF)
"""

from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import os
import traceback

doc = revit.doc
app = doc.Application

if not hasattr(DB, "PDFExportOptions"):
    forms.alert(
        "This script requires Revit 2022 or higher for native PDF export.",
        exitscript=True
    )

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

PATTERN_CATEGORIES = [
    DB.BuiltInCategory.OST_Floors,
    DB.BuiltInCategory.OST_Roofs,
    DB.BuiltInCategory.OST_Stairs,
]

for optional_cat in [
    "OST_Toposolid", "OST_Topography", "OST_Site",
    "OST_Hardscape", "OST_Subregions", "OST_TopographyLink"
]:
    try:
        PATTERN_CATEGORIES.append(getattr(DB.BuiltInCategory, optional_cat))
    except Exception:
        pass

TEMP_PDF_SUFFIX = "_TMP_PDF_EXP"
TEMP_SHEET_PREFIX = "TMP_PDF_SHEET_"

# Caches shape-edited status per (link path, element id) for the life of
# one script run, so re-processing the same link for a second selected
# view doesn't re-check every element from scratch.
_shape_edit_cache = {}


# -----------------------------------------------------------------------------
# TRANSACTION HANDLING & WARNING POPUP SUPPRESSION
# -----------------------------------------------------------------------------

class CustomFailuresPreprocessor(DB.IFailuresPreprocessor):
    """Suppresses all standard warning dialogs and prompts during transactions."""
    def PreprocessFailures(self, failuresAccessor):
        fail_list = failuresAccessor.GetFailureMessages()
        for f in fail_list:
            try:
                severity = f.GetSeverity()
                if severity == DB.FailureSeverity.Warning:
                    # Silently dismiss warning popups
                    failuresAccessor.DeleteWarning(f)
            except Exception:
                pass
        return DB.FailureProcessingResult.Continue


class SuppressedTransaction(object):
    """Custom transaction wrapper that forces Revit to ignore warning popups."""
    def __init__(self, document, name):
        self.tx = DB.Transaction(document, name)

    def __enter__(self):
        self.tx.Start()
        opts = self.tx.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(CustomFailuresPreprocessor())
        self.tx.SetFailureHandlingOptions(opts)
        return self.tx

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.tx.RollBack()
        else:
            try:
                self.tx.Commit()
            except Exception:
                self.tx.RollBack()


# -----------------------------------------------------------------------------
# HELPERS - BASIC
# -----------------------------------------------------------------------------

def eid_val(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def safe_filename(name):
    bad = '\\/:*?"<>|'
    for ch in bad:
        name = name.replace(ch, "_")
    return name.strip()


def get_element_shape_editor(el):
    try:
        editor = el.SlabShapeEditor
        if editor is not None:
            return editor
    except AttributeError:
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


def flatten_slab_editor(editor):
    if editor is None:
        return
    flattened = False
    try:
        vertices = editor.SlabShapeVertices
        if vertices:
            for v in vertices:
                try:
                    editor.ModifySubElement(v, 0.0)
                    flattened = True
                except Exception:
                    pass
    except Exception:
        pass

    if not flattened:
        try:
            editor.ResetSlabShape()
        except Exception:
            pass


def get_safe_paper_format(selected_paper):
    candidates_map = {
        "A0": ["ISO_A0", "A0"],
        "A1": ["ISO_A1", "A1"],
        "A2": ["ISO_A2", "A2"],
        "A3": ["ISO_A3", "A3"],
        "A4": ["ISO_A4", "A4"],
        "Letter": ["ANSI_A", "Letter", "NorthAmericanLetter"],
        "Legal": ["Legal", "NorthAmericanLegal"],
        "Ledger/Tabloid": ["ANSI_B", "Tabloid", "Ledger"]
    }
    names = candidates_map.get(selected_paper, ["ISO_A1"])
    for name in names:
        if hasattr(DB.ExportPaperFormat, name):
            return getattr(DB.ExportPaperFormat, name)
    if hasattr(DB.ExportPaperFormat, "Default"):
        return DB.ExportPaperFormat.Default
    return list(DB.ExportPaperFormat.GetValues(DB.ExportPaperFormat))[0]


# -----------------------------------------------------------------------------
# HELPERS - VIEW / OVERLAYS
# -----------------------------------------------------------------------------

def duplicate_view(view, new_name):
    try:
        new_id = view.Duplicate(DB.ViewDuplicateOption.WithDetailing)
    except Exception:
        new_id = view.Duplicate(DB.ViewDuplicateOption.Duplicate)
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
        target_view.DisplayStyle = source_view.DisplayStyle
        target_view.DetailLevel = source_view.DetailLevel
        target_view.CropBoxActive = source_view.CropBoxActive
        target_view.CropBox = source_view.CropBox
        target_view.CropBoxVisible = source_view.CropBoxVisible
    except Exception:
        pass

    try:
        filters = source_view.GetFilters()
        if filters:
            for f_id in filters:
                try:
                    if not target_view.IsFilterApplied(f_id):
                        target_view.AddFilter(f_id)
                    ogs = source_view.GetFilterOverrides(f_id)
                    if ogs:
                        target_view.SetFilterOverrides(f_id, ogs)
                    vis = source_view.GetFilterVisibility(f_id)
                    target_view.SetFilterVisibility(f_id, vis)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        for cat in doc.Settings.Categories:
            try:
                ogs = source_view.GetCategoryOverrides(cat.Id)
                if ogs:
                    target_view.SetCategoryOverrides(cat.Id, ogs)
            except Exception:
                pass
    except Exception:
        pass

    for param_id in [DB.BuiltInParameter.VIEW_PHASE, DB.BuiltInParameter.VIEW_PHASE_FILTER]:
        try:
            p_source = source_view.get_Parameter(param_id)
            if p_source and not p_source.IsReadOnly:
                p_target = target_view.get_Parameter(param_id)
                if p_target and not p_target.IsReadOnly:
                    p_target.Set(p_source.AsElementId())
        except Exception:
            pass


def extend_view_range(view, min_z_offset):
    try:
        vr = view.GetViewRange()
        new_bottom = min_z_offset - 1.0
        vr.SetOffset(DB.PlanViewPlane.BottomClipPlane, new_bottom)
        try:
            vr.SetOffset(DB.PlanViewPlane.ViewDepthPlane, new_bottom - 1.0)
        except Exception:
            pass
        view.SetViewRange(vr)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# HELPERS - LINKED MODEL PROCESSING
# -----------------------------------------------------------------------------

def get_link_instances_in_view(view):
    """Returns all RevitLinkInstance elements visible in the given view."""
    link_instances = []
    try:
        collector = DB.FilteredElementCollector(doc, view.Id)\
                      .OfClass(DB.RevitLinkInstance)
        for link in collector:
            try:
                # Skip hidden links
                if link.IsHidden(view):
                    continue
                link_doc = link.GetLinkDocument()
                if link_doc is not None:
                    link_instances.append(link)
            except Exception:
                pass
    except Exception:
        pass
    return link_instances


def get_view_visible_outline_in_link(view, link_instance):
    """Outline, in the LINK's own local coordinates, covering what the host view actually shows."""
    try:
        inv = link_instance.GetTotalTransform().Inverse
    except Exception:
        return None

    min_xy = max_xy = None
    try:
        if view.CropBoxActive:
            cb = view.CropBox
            cb_t = cb.Transform
            corners = [cb_t.OfPoint(DB.XYZ(cb.Min.X, cb.Min.Y, 0)),
                      cb_t.OfPoint(DB.XYZ(cb.Max.X, cb.Min.Y, 0)),
                      cb_t.OfPoint(DB.XYZ(cb.Min.X, cb.Max.Y, 0)),
                      cb_t.OfPoint(DB.XYZ(cb.Max.X, cb.Max.Y, 0))]
            xs = [p.X for p in corners]
            ys = [p.Y for p in corners]
            min_xy, max_xy = (min(xs), min(ys)), (max(xs), max(ys))
    except Exception:
        pass
    if min_xy is None:
        big = 1.0e6
        min_xy, max_xy = (-big, -big), (big, big)

    z_min, z_max = -1.0e6, 1.0e6
    try:
        vr = view.GetViewRange()
        top_lvl = doc.GetElement(vr.GetLevelId(DB.PlanViewPlane.TopClipPlane))
        bot_lvl = doc.GetElement(vr.GetLevelId(DB.PlanViewPlane.BottomClipPlane))
        if top_lvl is not None:
            z_max = top_lvl.Elevation + vr.GetOffset(DB.PlanViewPlane.TopClipPlane)
        if bot_lvl is not None:
            z_min = (bot_lvl.Elevation
                    + vr.GetOffset(DB.PlanViewPlane.BottomClipPlane) - 50.0)
    except Exception:
        pass

    try:
        corners3d = []
        for x in (min_xy[0], max_xy[0]):
            for y in (min_xy[1], max_xy[1]):
                for z in (z_min, z_max):
                    corners3d.append(inv.OfPoint(DB.XYZ(x, y, z)))
        xs = [p.X for p in corners3d]
        ys = [p.Y for p in corners3d]
        zs = [p.Z for p in corners3d]
        return DB.Outline(DB.XYZ(min(xs), min(ys), min(zs)),
                          DB.XYZ(max(xs), max(ys), max(zs)))
    except Exception:
        return None


def find_shape_edited_in_link(link_instance, pattern_bics, view):
    """Scans a linked document for shape-edited slabs inside view limits."""
    results = []
    link_doc = link_instance.GetLinkDocument()
    if link_doc is None:
        return results

    outline = get_view_visible_outline_in_link(view, link_instance)
    bb_filter = None
    if outline is not None:
        try:
            bb_filter = DB.BoundingBoxIntersectsFilter(outline)
        except Exception:
            bb_filter = None

    link_key = link_doc.PathName or eid_val(link_instance.Id)

    for bic in pattern_bics:
        try:
            collector = DB.FilteredElementCollector(link_doc)\
                          .OfCategory(bic)\
                          .WhereElementIsNotElementType()
            if bb_filter is not None:
                collector = collector.WherePasses(bb_filter)
        except Exception:
            continue

        for el in collector:
            cache_key = (link_key, eid_val(el.Id))
            cached = _shape_edit_cache.get(cache_key)
            if cached is None:
                cached = is_shape_edited(el)
                _shape_edit_cache[cache_key] = cached
            if cached:
                results.append((el, link_instance))

    return results


def copy_linked_element_to_host(link_instance, linked_element_id):
    """Copies a single element from a linked doc into the host doc using link transform."""
    try:
        link_doc = link_instance.GetLinkDocument()
        transform = link_instance.GetTotalTransform()

        source_ids = List[DB.ElementId]()
        source_ids.Add(linked_element_id)

        copy_opts = DB.CopyPasteOptions()
        try:
            class NoDupHandler(DB.IDuplicateTypeNamesHandler):
                def OnDuplicateTypeNamesFound(self, args):
                    return DB.DuplicateTypeAction.UseDestinationTypes
            copy_opts.SetDuplicateTypeNamesHandler(NoDupHandler())
        except Exception:
            pass

        copied_ids = DB.ElementTransformUtils.CopyElements(
            link_doc, source_ids, doc, transform, copy_opts)

        return copied_ids
    except Exception:
        return None


# -----------------------------------------------------------------------------
# HELPERS - HOST SHAPE-EDITED
# -----------------------------------------------------------------------------

def get_world_footprint(el, transform=None):
    """(xmin, ymin, xmax, ymax, top_z) of el's bounding box in world."""
    try:
        bb = el.get_BoundingBox(None)
    except Exception:
        bb = None
    if bb is None:
        return None
    corners = [bb.Min, DB.XYZ(bb.Max.X, bb.Min.Y, bb.Min.Z),
              DB.XYZ(bb.Min.X, bb.Max.Y, bb.Min.Z), bb.Max]
    if transform is not None:
        try:
            corners = [transform.OfPoint(p) for p in corners]
        except Exception:
            return None
    xs = [p.X for p in corners]
    ys = [p.Y for p in corners]
    zs = [p.Z for p in corners]
    return (min(xs), min(ys), max(xs), max(ys), max(zs))


def collect_host_shape_edited_candidates(source_view, pattern_cats):
    """Host, shape-edited elements visible in source_view."""
    out = []
    for cat in pattern_cats:
        try:
            collector = (
                DB.FilteredElementCollector(doc, source_view.Id)
                  .OfCategoryId(cat.Id)
                  .WhereElementIsNotElementType()
            )
            for el in collector:
                if is_shape_edited(el):
                    out.append({'kind': 'host', 'el': el,
                               'footprint': get_world_footprint(el)})
        except Exception:
            pass
    return out


def copy_and_flatten_host_elements(elements, source_view, temp_view):
    """Copies, flattens and overrides host shape-edited elements."""
    copied_elements_map = []
    flat_copies = []

    for el in elements:
        try:
            ids_to_copy = List[DB.ElementId]()
            ids_to_copy.Add(el.Id)

            if hasattr(el, "GetHostedSubregions"):
                for sub in el.GetHostedSubregions():
                    if sub and sub.Id:
                        ids_to_copy.Add(sub.Id)

            copied_ids = DB.ElementTransformUtils.CopyElements(
                doc, ids_to_copy, DB.XYZ.Zero)
            if copied_ids and copied_ids.Count > 0:
                host_c_id = copied_ids[0]
                copied_elements_map.append((el.Id, host_c_id))
                for c_idx in range(1, copied_ids.Count):
                    flat_copies.append(copied_ids[c_idx])
        except Exception:
            pass

    if copied_elements_map:
        doc.Regenerate()

        for orig_id, c_id in copied_elements_map:
            try:
                copied_el = doc.GetElement(c_id)
                if copied_el is None:
                    continue
                editor = get_element_shape_editor(copied_el)
                if editor is not None:
                    flatten_slab_editor(editor)
                    flat_copies.append(c_id)
                orig_ogs = source_view.GetElementOverrides(orig_id)
                if orig_ogs:
                    temp_view.SetElementOverrides(c_id, orig_ogs)
            except Exception:
                pass

        # Hide originals
        safe_to_hide = List[DB.ElementId]()
        for orig_id, c_id in copied_elements_map:
            if c_id in flat_copies:
                safe_to_hide.Add(orig_id)
        if safe_to_hide.Count > 0:
            try:
                temp_view.HideElements(safe_to_hide)
            except Exception:
                pass

    return copied_elements_map, flat_copies


# -----------------------------------------------------------------------------
# HELPERS - TITLEBLOCK
# -----------------------------------------------------------------------------

def collect_titleblocks(document):
    tbs = []
    collector = DB.FilteredElementCollector(document)\
                  .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)\
                  .WhereElementIsElementType()
    for tb in collector:
        try:
            fam_name = tb.Family.Name
            type_name = tb.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
            display = "{} : {}".format(fam_name, type_name)
            tbs.append((display, tb))
        except Exception:
            pass
    tbs.sort(key=lambda x: x[0].lower())
    return tbs


def get_titleblock_size(tb_symbol):
    try:
        width_p = tb_symbol.get_Parameter(DB.BuiltInParameter.SHEET_WIDTH)
        height_p = tb_symbol.get_Parameter(DB.BuiltInParameter.SHEET_HEIGHT)
        if width_p and height_p:
            return (width_p.AsDouble(), height_p.AsDouble())
    except Exception:
        pass
    return (2.75, 1.94)


def create_temp_sheet_with_view(document, source_view_id, tb_symbol, sheet_name):
    if not tb_symbol.IsActive:
        tb_symbol.Activate()
        document.Regenerate()

    sheet = DB.ViewSheet.Create(document, tb_symbol.Id)

    try:
        sheet.Name = sheet_name
    except Exception:
        pass

    import time
    unique_suffix = str(int(time.time() * 1000))[-6:]
    try:
        sheet.SheetNumber = "TMP-" + unique_suffix
    except Exception:
        pass

    tb_width, tb_height = get_titleblock_size(tb_symbol)
    center = DB.XYZ(tb_width / 2.0, tb_height / 2.0, 0)

    view_to_place = document.GetElement(source_view_id)
    viewport = None
    try:
        viewport = DB.Viewport.Create(document, sheet.Id, source_view_id, center)
    except Exception:
        pass

    if viewport and view_to_place:
        try:
            fit_view_to_sheet(document, view_to_place, viewport, tb_width, tb_height)
        except Exception:
            pass

    return sheet, viewport


def fit_view_to_sheet(document, view, viewport, sheet_width_ft, sheet_height_ft):
    try:
        crop = view.CropBox
        if crop is None:
            return

        model_w = abs(crop.Max.X - crop.Min.X)
        model_h = abs(crop.Max.Y - crop.Min.Y)
        if model_w <= 0 or model_h <= 0:
            return

        usable_w = sheet_width_ft * 0.80
        usable_h = sheet_height_ft * 0.80

        scale_w = model_w / usable_w
        scale_h = model_h / usable_h
        best_scale = max(scale_w, scale_h)

        standard_scales = [1, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000]
        chosen = standard_scales[-1]
        for s in standard_scales:
            if s >= best_scale:
                chosen = s
                break

        try:
            view.Scale = int(chosen)
        except Exception:
            pass

        document.Regenerate()

        try:
            sheet = document.GetElement(viewport.SheetId)
            new_center = DB.XYZ(sheet_width_ft / 2.0, sheet_height_ft / 2.0, 0)
            box_center = viewport.GetBoxCenter()
            move_vec = new_center - box_center
            DB.ElementTransformUtils.MoveElement(document, viewport.Id, move_vec)
        except Exception:
            pass
    except Exception:
        pass


def validate_pattern_categories(document, bic_list):
    valid = []
    valid_bics = []
    pattern_ids = set()
    for bic in bic_list:
        try:
            cat = DB.Category.GetCategory(document, bic)
            if cat is not None:
                valid.append(cat)
                valid_bics.append(bic)
                pattern_ids.add(eid_val(cat.Id))
        except Exception:
            pass
    return valid, valid_bics, pattern_ids


# -----------------------------------------------------------------------------
# UI - PDF SETTINGS DIALOG
# -----------------------------------------------------------------------------

class PDFSettingsForm(forms.WPFWindow):
    def __init__(self, xaml_source, titleblock_options):
        forms.WPFWindow.__init__(self, xaml_source)
        self._titleblock_options = titleblock_options

        self.paper_combo.ItemsSource = [
            "A0", "A1", "A2", "A3", "A4",
            "Letter", "Legal", "Ledger/Tabloid"
        ]
        self.orient_combo.ItemsSource = ["Landscape", "Portrait"]
        self.color_combo.ItemsSource = ["Color", "GrayScale", "BlackLine"]
        self.fit_combo.ItemsSource = ["Fit to Page", "Zoom 100%", "Zoom 75%", "Zoom 50%"]

        tb_display_names = ["<None - Export view directly>"] + [t[0] for t in titleblock_options]
        self.tb_combo.ItemsSource = tb_display_names

        self.paper_combo.SelectedItem = "A1"
        self.orient_combo.SelectedItem = "Landscape"
        self.color_combo.SelectedItem = "Color"
        self.fit_combo.SelectedItem = "Fit to Page"
        self.tb_combo.SelectedIndex = 0

        self.result = None

    # pylint: disable=unused-argument
    def ok_click(self, sender, args):
        tb_idx = self.tb_combo.SelectedIndex
        selected_tb = None
        if tb_idx > 0:
            selected_tb = self._titleblock_options[tb_idx - 1][1]

        self.result = {
            "paper": self.paper_combo.SelectedItem,
            "orient": self.orient_combo.SelectedItem,
            "color": self.color_combo.SelectedItem,
            "fit": self.fit_combo.SelectedItem,
            "titleblock": selected_tb,
            "tb_display": self.tb_combo.SelectedItem,
        }
        self.Close()

    def cancel_click(self, sender, args):
        self.result = None
        self.Close()


PDF_SETTINGS_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="PDF Export Settings (Linked Model Support)"
        Height="430" Width="420"
        WindowStartupLocation="CenterScreen"
        ResizeMode="NoResize"
        SizeToContent="Manual">
    <Grid Margin="15">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <TextBlock Grid.Row="0" Text="Paper Size:" FontWeight="Bold" Margin="0,0,0,3"/>
        <ComboBox Grid.Row="1" x:Name="paper_combo" Margin="0,0,0,10" Height="26"/>

        <TextBlock Grid.Row="2" Text="Orientation:" FontWeight="Bold" Margin="0,0,0,3"/>
        <ComboBox Grid.Row="3" x:Name="orient_combo" Margin="0,0,0,10" Height="26"/>

        <TextBlock Grid.Row="4" Text="Color Mode:" FontWeight="Bold" Margin="0,0,0,3"/>
        <ComboBox Grid.Row="5" x:Name="color_combo" Margin="0,0,0,10" Height="26"/>

        <TextBlock Grid.Row="6" Text="Fit / Zoom:" FontWeight="Bold" Margin="0,0,0,3"/>
        <ComboBox Grid.Row="7" x:Name="fit_combo" Margin="0,0,0,10" Height="26"/>

        <TextBlock Grid.Row="8" Text="Titleblock (auto-fit view inside):" FontWeight="Bold" Margin="0,0,0,3"/>
        <ComboBox Grid.Row="9" x:Name="tb_combo" Margin="0,0,0,10" Height="26"/>

        <StackPanel Grid.Row="11" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button Content="Export" Width="80" Height="28" Margin="0,0,8,0" Click="ok_click" IsDefault="True"/>
            <Button Content="Cancel" Width="80" Height="28" Click="cancel_click" IsCancel="True"/>
        </StackPanel>
    </Grid>
</Window>
"""


def show_pdf_settings_dialog(titleblock_options):
    import tempfile
    tmp_path = os.path.join(tempfile.gettempdir(), "pdf_settings_form.xaml")
    with open(tmp_path, "w") as f:
        f.write(PDF_SETTINGS_XAML)
    form = PDFSettingsForm(tmp_path, titleblock_options)
    form.ShowDialog()
    try:
        os.remove(tmp_path)
    except Exception:
        pass
    return form.result


# -----------------------------------------------------------------------------
# LINK SELECT WORKFLOW
# -----------------------------------------------------------------------------

def select_links_to_process(selected_views):
    """Asks the user which linked models should be processed."""
    by_id = {}
    for v in selected_views:
        for li in get_link_instances_in_view(v):
            by_id[eid_val(li.Id)] = li
    if not by_id:
        return set()

    name_to_id = {}
    for iv, li in by_id.items():
        try:
            nm = li.Name
        except Exception:
            nm = "Link {}".format(iv)
        while nm in name_to_id:
            nm = nm + " "
        name_to_id[nm] = iv

    chosen = forms.SelectFromList.show(
        sorted(name_to_id.keys()),
        title="SELECT THE LANDSCAPE LINKS",
        multiselect=True,
        button_name="Continue")
    if not chosen:
        return set()
    return set(name_to_id[n] for n in chosen)


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

def main():
    pattern_cats, pattern_bics, pattern_ids = validate_pattern_categories(
        doc, PATTERN_CATEGORIES)
    if not pattern_cats:
        forms.alert("Standard pattern categories could not be resolved.", exitscript=True)
        return

    selected_views = forms.select_views(
        title="Select plan views to export as PDF (Linked Model aware)",
        filterfunc=lambda v: isinstance(v, DB.ViewPlan) and not v.IsTemplate
    )
    if not selected_views:
        return

    selected_link_ids = select_links_to_process(selected_views)

    folder = forms.pick_folder(title="Choose export folder")
    if not folder:
        return

    titleblock_options = collect_titleblocks(doc)

    settings = show_pdf_settings_dialog(titleblock_options)
    if not settings:
        return

    selected_paper = settings["paper"]
    selected_orient = settings["orient"]
    selected_color = settings["color"]
    selected_fit = settings["fit"]
    selected_tb = settings["titleblock"]

    exported_count = 0

    for source_view in selected_views:
        base_name = safe_filename(source_view.Name)

        host_copied_map = []
        host_flat_copies = []
        link_host_copies = []           
        temp_view = None
        temp_sheet = None
        temp_viewport = None

        try:
            # ------------------------------
            # STEP 0: Collect candidates
            # ------------------------------
            host_candidates = collect_host_shape_edited_candidates(
                source_view, pattern_cats)

            all_link_instances = get_link_instances_in_view(source_view)
            link_instances = [li for li in all_link_instances
                              if eid_val(li.Id) in selected_link_ids]

            link_candidates = []
            for link_inst in link_instances:
                found = find_shape_edited_in_link(
                    link_inst, pattern_bics, source_view)
                try:
                    lt = link_inst.GetTotalTransform()
                except Exception:
                    lt = None
                for el, li in found:
                    link_candidates.append({
                        'kind': 'link', 'el': el, 'link_inst': li,
                        'footprint': get_world_footprint(el, lt)})

            kept = host_candidates + link_candidates
            kept_host_elements = [c['el'] for c in kept if c['kind'] == 'host']
            all_linked_shape_edited = [(c['el'], c['link_inst'])
                                       for c in kept if c['kind'] == 'link']

            # ------------------------------
            # STEP 1: Duplicate & Flatten Host Elements (Popup Free)
            # ------------------------------
            with SuppressedTransaction(doc, "Prep Host View + Slabs: " + source_view.Name):
                temp_view = duplicate_view(source_view, source_view.Name + TEMP_PDF_SUFFIX)
                detach_view_template(temp_view)
                prepare_overlay_view(source_view, temp_view)

                host_copied_map, host_flat_copies = copy_and_flatten_host_elements(
                    kept_host_elements, source_view, temp_view)

                doc.Regenerate()

            # ------------------------------
            # STEP 2: Copy & Flatten Linked Slabs (Popup Free)
            # ------------------------------
            if all_linked_shape_edited:
                with SuppressedTransaction(doc, "Copy & Flatten Linked Slabs: " + source_view.Name):
                    cats_to_hide_in_view = set()

                    for linked_el, link_inst in all_linked_shape_edited:
                        try:
                            copied_ids = copy_linked_element_to_host(link_inst, linked_el.Id)
                            if copied_ids and copied_ids.Count > 0:
                                for cid in copied_ids:
                                    link_host_copies.append(cid)

                                cat = linked_el.Category
                                if cat is not None:
                                    cats_to_hide_in_view.add(eid_val(cat.Id))
                        except Exception:
                            pass

                    doc.Regenerate()

                    # Flatten all host copies of linked slabs
                    for cid in link_host_copies:
                        try:
                            copied_el = doc.GetElement(cid)
                            if copied_el is None:
                                continue
                            editor = get_element_shape_editor(copied_el)
                            if editor is not None:
                                flatten_slab_editor(editor)
                        except Exception:
                            pass

                    doc.Regenerate()

                    # Apply invisibility overrides to original warped link elements in temp view
                    for linked_el, link_inst in all_linked_shape_edited:
                        try:
                            link_el_id = DB.LinkElementId(link_inst.Id, linked_el.Id)
                            invisible_ogs = DB.OverrideGraphicSettings()
                            try:
                                invisible_ogs.SetProjectionLineWeight(1)
                                invisible_ogs.SetProjectionLineColor(DB.Color(255, 255, 255))
                            except Exception:
                                pass
                            try:
                                invisible_ogs.SetCutLineWeight(1)
                                invisible_ogs.SetCutLineColor(DB.Color(255, 255, 255))
                            except Exception:
                                pass
                            try:
                                invisible_ogs.SetHalftone(True)
                                invisible_ogs.SetSurfaceTransparency(100)
                            except Exception:
                                pass
                            try:
                                invisible_ogs.SetSurfaceForegroundPatternVisible(False)
                                invisible_ogs.SetSurfaceBackgroundPatternVisible(False)
                                invisible_ogs.SetCutForegroundPatternVisible(False)
                                invisible_ogs.SetCutBackgroundPatternVisible(False)
                            except Exception:
                                pass

                            try:
                                temp_view.SetElementOverrides(link_el_id, invisible_ogs)
                            except Exception:
                                try:
                                    hide_ids = List[DB.ElementId]()
                                    hide_ids.Add(link_el_id)
                                    temp_view.HideElements(hide_ids)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    doc.Regenerate()

            # ------------------------------
            # STEP 3: Adjust view range if needed (Popup Free)
            # ------------------------------
            all_check_ids = list(host_flat_copies) + list(link_host_copies)
            if all_check_ids:
                min_z = 0.0
                for c_id in all_check_ids:
                    try:
                        el = doc.GetElement(c_id)
                        if el is None:
                            continue
                        bb = el.get_BoundingBox(None)
                        if bb is not None and bb.Min.Z < min_z:
                            min_z = bb.Min.Z
                    except Exception:
                        pass

                if min_z < 0:
                    with SuppressedTransaction(doc, "Extend View Range: " + source_view.Name):
                        extend_view_range(temp_view, min_z)
                        doc.Regenerate()

            # ------------------------------
            # STEP 4: Create temp sheet (Popup Free)
            # ------------------------------
            export_target_view_id = temp_view.Id
            if selected_tb is not None:
                with SuppressedTransaction(doc, "Create Temp Sheet: " + source_view.Name):
                    try:
                        temp_sheet, temp_viewport = create_temp_sheet_with_view(
                            doc, temp_view.Id, selected_tb,
                            TEMP_SHEET_PREFIX + base_name)
                        if temp_sheet is not None:
                            export_target_view_id = temp_sheet.Id
                    except Exception:
                        pass

            # ------------------------------
            # STEP 5: PDF Export & Pure Renaming
            # ------------------------------
            existing_pdfs = set()
            try:
                existing_pdfs = set(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pdf"))
            except Exception:
                pass

            pdf_opts = DB.PDFExportOptions()
            pdf_opts.Combine = False
            pdf_opts.FileName = base_name

            try:
                pdf_opts.RasterQuality = DB.RasterQualityType.Presentation
            except Exception:
                pass

            try:
                fit_map = {
                    "Fit to Page": (DB.ZoomType.FitToPage, 100),
                    "Zoom 100%": (DB.ZoomType.Zoom, 100),
                    "Zoom 75%": (DB.ZoomType.Zoom, 75),
                    "Zoom 50%": (DB.ZoomType.Zoom, 50),
                }
                zt, zp = fit_map.get(selected_fit, (DB.ZoomType.FitToPage, 100))
                pdf_opts.ZoomType = zt
                if zt == DB.ZoomType.Zoom:
                    pdf_opts.ZoomPercentage = zp
            except Exception:
                pass

            try:
                pdf_opts.PaperPlacement = DB.PaperPlacementType.Center
            except Exception:
                pass

            try:
                pdf_opts.HideCropBoundaries = True
                pdf_opts.HideReferencePlane = True
                pdf_opts.HideUnreferencedViewTags = True
                pdf_opts.HideScopeBoxes = True
            except Exception:
                pass

            try:
                pdf_opts.PaperFormat = get_safe_paper_format(selected_paper)
            except Exception:
                pass

            try:
                if selected_orient == "Portrait":
                    pdf_opts.PaperOrientation = DB.PageOrientationType.Portrait
                else:
                    pdf_opts.PaperOrientation = DB.PageOrientationType.Landscape
            except Exception:
                pass

            try:
                color_map = {
                    "Color": DB.ColorDepthType.Color,
                    "GrayScale": DB.ColorDepthType.GrayScale,
                    "BlackLine": DB.ColorDepthType.BlackLine
                }
                pdf_opts.ColorDepth = color_map.get(selected_color, DB.ColorDepthType.Color)
            except Exception:
                pass

            export_view_ids = List[DB.ElementId]()
            export_view_ids.Add(export_target_view_id)
            
            # Execute export
            doc.Export(folder, export_view_ids, pdf_opts)

            # Identify the newly created PDF file and force-rename it to match view's EXACT name
            final_target_pdf = os.path.join(folder, base_name + ".pdf")
            try:
                current_pdfs = set(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pdf"))
                new_pdfs = current_pdfs - existing_pdfs
                
                if new_pdfs:
                    exported_pdf = list(new_pdfs)[0]
                    if os.path.normpath(exported_pdf) != os.path.normpath(final_target_pdf):
                        if os.path.exists(final_target_pdf):
                            try:
                                os.remove(final_target_pdf)
                            except Exception:
                                pass
                        os.rename(exported_pdf, final_target_pdf)
            except Exception:
                pass

            if os.path.exists(final_target_pdf):
                exported_count += 1

        except Exception:
            pass

        finally:
            # ------------------------------
            # STEP 6: Clean up (Popup Free)
            # ------------------------------
            cleanup_ids = []
            if temp_sheet is not None:
                cleanup_ids.append(temp_sheet.Id)
            if temp_view is not None:
                cleanup_ids.append(temp_view.Id)
            cleanup_ids.extend(link_host_copies)
            cleanup_ids.extend([c_id for _, c_id in host_copied_map if c_id in host_flat_copies])

            if cleanup_ids:
                with SuppressedTransaction(doc, "Clean up Temp PDF Objects: " + source_view.Name):
                    for eid in cleanup_ids:
                        try:
                            doc.Delete(eid)
                        except Exception:
                            pass

    # Silent toast notification at the very end
    if exported_count > 0:
        forms.toast(
            "Export Complete!\nSuccessfully exported {} floor plan(s).".format(exported_count),
            title="Linked Model PDF Export"
        )


main()