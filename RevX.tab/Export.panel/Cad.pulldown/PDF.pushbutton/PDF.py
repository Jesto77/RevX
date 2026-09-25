# -*- coding: utf-8 -*-
"""
Export PDF (with Flattened Patterns + Titleblock Support)
For each selected plan view: creates a temporary view, flattens shape-edited
slabs to eliminate distorted patterns and triangulation lines, then exports
directly to PDF matching the view name. Optionally places view on a temporary
sheet with selected titleblock and fits view automatically.
Author: Jesto Joy (Modified for PDF)
"""

from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import os
import traceback

doc = revit.doc
app = doc.Application
output = script.get_output()

# Ensure Revit version supports Native PDF Export (Revit 2022+)
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

# -----------------------------------------------------------------------------
# COMPATIBILITY & HELPERS
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


# -----------------------------------------------------------------------------
# HELPERS - SHAPE EDITOR FLATTENING
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# HELPERS - VIEW RANGE & OVERLAYS
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

    try:
        collector = DB.FilteredElementCollector(doc, source_view.Id)\
                      .WhereElementIsNotElementType()
        for el in collector:
            try:
                e_ogs = source_view.GetElementOverrides(el.Id)
                if e_ogs:
                    target_view.SetElementOverrides(el.Id, e_ogs)
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


def verify_and_hide_originals(document, target_view, copied_elements_map, flat_copies):
    safe_to_hide = List[DB.ElementId]()
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
            bb = copied_el.get_BoundingBox(target_view)
            if bb is not None:
                safe_to_hide.Add(orig_id)
            else:
                unsafe_copies.append(c_id)
        except Exception:
            unsafe_copies.append(c_id)

    if safe_to_hide.Count > 0:
        try:
            target_view.HideElements(safe_to_hide)
        except Exception:
            pass
    return unsafe_copies


def validate_pattern_categories(document, bic_list):
    valid = []
    pattern_ids = set()
    for bic in bic_list:
        try:
            cat = DB.Category.GetCategory(document, bic)
            if cat is not None:
                valid.append(cat)
                pattern_ids.add(eid_val(cat.Id))
        except Exception:
            pass
    return valid, pattern_ids


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
# HELPERS - TITLEBLOCK
# -----------------------------------------------------------------------------

def collect_titleblocks(document):
    """Returns list of tuples (display_name, FamilySymbol) for all TB types."""
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
    """Returns (width, height) in feet from titleblock family symbol parameters."""
    try:
        width_p = tb_symbol.get_Parameter(DB.BuiltInParameter.SHEET_WIDTH)
        height_p = tb_symbol.get_Parameter(DB.BuiltInParameter.SHEET_HEIGHT)
        if width_p and height_p:
            return (width_p.AsDouble(), height_p.AsDouble())
    except Exception:
        pass
    return (2.75, 1.94)  # ~A1 default fallback


def create_temp_sheet_with_view(document, source_view_id, tb_symbol, sheet_name):
    """Creates a temporary sheet, places the view fit-to-sheet, returns sheet."""
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
    """Adjusts view scale so it fits nicely on sheet with margins."""
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

        # Preset defaults
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
        Title="PDF Export Settings"
        Height="430" Width="400"
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
# MAIN
# -----------------------------------------------------------------------------

def main():
    pattern_cats, pattern_ids = validate_pattern_categories(doc, PATTERN_CATEGORIES)
    if not pattern_cats:
        output.print_md("**Stopped:** Standard pattern categories could not be resolved.")
        return

    selected_views = forms.select_views(
        title="Select plan views to export as PDF",
        filterfunc=lambda v: isinstance(v, DB.ViewPlan) and not v.IsTemplate
    )
    if not selected_views:
        output.print_md("**Stopped:** No views selected.")
        return

    folder = forms.pick_folder(title="Choose export folder")
    if not folder:
        output.print_md("**Stopped:** No export folder selected.")
        return

    titleblock_options = collect_titleblocks(doc)

    settings = show_pdf_settings_dialog(titleblock_options)
    if not settings:
        output.print_md("**Stopped:** PDF settings cancelled.")
        return

    selected_paper = settings["paper"]
    selected_orient = settings["orient"]
    selected_color = settings["color"]
    selected_fit = settings["fit"]
    selected_tb = settings["titleblock"]
    tb_display = settings["tb_display"]

    output.print_md(
        "**PDF Settings:** {} | {} | {} | {} | TB: {}".format(
            selected_paper, selected_orient, selected_color, selected_fit, tb_display))

    for source_view in selected_views:
        base_name = safe_filename(source_view.Name)
        flat_copies = []
        copied_elements_map = []
        temp_ids = []
        temp_view = None
        temp_sheet = None
        temp_viewport = None

        try:
            # Step 1: Prep flat view
            with revit.Transaction("Prep View Slabs: " + source_view.Name):
                temp_view = duplicate_view(source_view, source_view.Name + TEMP_PDF_SUFFIX)
                detach_view_template(temp_view)
                prepare_overlay_view(source_view, temp_view)

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

                                    if hasattr(el, "GetHostedSubregions"):
                                        for sub in el.GetHostedSubregions():
                                            if sub and sub.Id:
                                                ids_to_copy.Add(sub.Id)

                                    copied_ids = DB.ElementTransformUtils.CopyElements(doc, ids_to_copy, DB.XYZ.Zero)
                                    if copied_ids and copied_ids.Count > 0:
                                        host_c_id = copied_ids[0]
                                        copied_elements_map.append((el.Id, host_c_id))
                                        for c_idx in range(1, copied_ids.Count):
                                            flat_copies.append(copied_ids[c_idx])
                                except Exception:
                                    pass
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

                    if flat_copies:
                        min_z = get_min_z_of_copies(doc, flat_copies)
                        if min_z < 0:
                            extend_view_range(temp_view, min_z)
                            doc.Regenerate()

                    unsafe_copies = verify_and_hide_originals(doc, temp_view, copied_elements_map, flat_copies)

                    for c_id in unsafe_copies:
                        try:
                            doc.Delete(c_id)
                            if c_id in flat_copies:
                                flat_copies.remove(c_id)
                        except Exception:
                            pass

                temp_ids = [c_id for _, c_id in copied_elements_map if c_id in flat_copies]
                doc.Regenerate()

            # Step 2: If TB selected, create temp sheet
            export_target_view_id = temp_view.Id
            if selected_tb is not None:
                with revit.Transaction("Create Temp Sheet: " + source_view.Name):
                    try:
                        temp_sheet, temp_viewport = create_temp_sheet_with_view(
                            doc, temp_view.Id, selected_tb,
                            TEMP_SHEET_PREFIX + base_name)
                        if temp_sheet is not None:
                            export_target_view_id = temp_sheet.Id
                    except Exception:
                        output.print_md("Warning: Failed to create temp sheet — exporting view directly.")
                        output.print_code(traceback.format_exc())

            # Step 3: PDF Export config
            pdf_opts = DB.PDFExportOptions()
            pdf_opts.Combine = False
            pdf_opts.FileName = base_name

            try:
                pdf_opts.RasterQuality = DB.RasterQualityType.Presentation
            except Exception:
                pass

            # Fit / Zoom mode
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
            doc.Export(folder, export_view_ids, pdf_opts)

            final_target_pdf = os.path.join(folder, base_name + ".pdf")
            if os.path.exists(final_target_pdf):
                output.print_md("**{}**: PDF exported: `{}`".format(source_view.Name, final_target_pdf))
            else:
                output.print_md("**{}**: Export finished. Check folder: `{}`".format(source_view.Name, folder))

        except Exception:
            output.print_md("**{}**: PDF Export FAILED —".format(source_view.Name))
            output.print_code(traceback.format_exc())

        finally:
            # Cleanup temp sheet + viewport
            cleanup_ids = []
            if temp_sheet is not None:
                cleanup_ids.append(temp_sheet.Id)
            if temp_view is not None:
                cleanup_ids.append(temp_view.Id)
            cleanup_ids.extend(temp_ids)

            if cleanup_ids:
                with revit.Transaction("Clean up Temp PDF Objects: " + source_view.Name):
                    for eid in cleanup_ids:
                        try:
                            doc.Delete(eid)
                        except Exception:
                            pass


main()