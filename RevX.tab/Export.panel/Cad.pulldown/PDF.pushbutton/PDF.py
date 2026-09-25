# -*- coding: utf-8 -*-
"""
Export PDF (with Flattened Patterns)
For each selected plan view: creates a temporary view, flattens shape-edited
slabs to eliminate distorted patterns and triangulation lines, then exports
directly to PDF matching the view name. Preset A1 / Landscape / Color.
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
    """Duplicates overrides, crop settings, scale, filters, and phases from source view."""
    try:
        target_view.Scale = source_view.Scale
        target_view.DisplayStyle = source_view.DisplayStyle
        target_view.DetailLevel = source_view.DetailLevel
        target_view.CropBoxActive = source_view.CropBoxActive
        target_view.CropBox = source_view.CropBox
        target_view.CropBoxVisible = source_view.CropBoxVisible
    except Exception:
        pass

    # Copy view filters
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

    # Copy Category-level Overrides
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

    # Copy Element-level Overrides
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

    # Phase Settings
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
    """Dynamically looks up matching Revit API ExportPaperFormat enums to prevent TypeErrors."""
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
            
    # Universal fallbacks
    if hasattr(DB.ExportPaperFormat, "Default"):
        return DB.ExportPaperFormat.Default
    return list(DB.ExportPaperFormat.GetValues(DB.ExportPaperFormat))[0]


# -----------------------------------------------------------------------------
# UI - PDF SETTINGS DIALOG
# -----------------------------------------------------------------------------

class PDFSettingsForm(forms.WPFWindow):
    """Single-dialog PDF settings form: paper size, orientation, color mode."""

    def __init__(self, xaml_source):
        forms.WPFWindow.__init__(self, xaml_source)
        # Preload combo values
        self.paper_combo.ItemsSource = [
            "A0", "A1", "A2", "A3", "A4",
            "Letter", "Legal", "Ledger/Tabloid"
        ]
        self.orient_combo.ItemsSource = ["Landscape", "Portrait"]
        self.color_combo.ItemsSource = ["Color", "GrayScale", "BlackLine"]

        # Preset defaults
        self.paper_combo.SelectedItem = "A1"
        self.orient_combo.SelectedItem = "Landscape"
        self.color_combo.SelectedItem = "Color"

        self.result = None

    # pylint: disable=unused-argument
    def ok_click(self, sender, args):
        self.result = {
            "paper": self.paper_combo.SelectedItem,
            "orient": self.orient_combo.SelectedItem,
            "color": self.color_combo.SelectedItem,
        }
        self.Close()

    def cancel_click(self, sender, args):
        self.result = None
        self.Close()


PDF_SETTINGS_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="PDF Export Settings"
        Height="260" Width="340"
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
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <TextBlock Grid.Row="0" Text="Paper Size:" FontWeight="Bold" Margin="0,0,0,3"/>
        <ComboBox Grid.Row="1" x:Name="paper_combo" Margin="0,0,0,10" Height="26"/>

        <TextBlock Grid.Row="2" Text="Orientation:" FontWeight="Bold" Margin="0,0,0,3"/>
        <ComboBox Grid.Row="3" x:Name="orient_combo" Margin="0,0,0,10" Height="26"/>

        <TextBlock Grid.Row="4" Text="Color Mode:" FontWeight="Bold" Margin="0,0,0,3"/>
        <ComboBox Grid.Row="5" x:Name="color_combo" Margin="0,0,0,10" Height="26"/>

        <StackPanel Grid.Row="7" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button Content="Export" Width="80" Height="28" Margin="0,0,8,0"
                    Click="ok_click" IsDefault="True"/>
            <Button Content="Cancel" Width="80" Height="28"
                    Click="cancel_click" IsCancel="True"/>
        </StackPanel>
    </Grid>
</Window>
"""


def show_pdf_settings_dialog():
    """Show single dialog for PDF settings. Returns dict or None."""
    import tempfile
    tmp_path = os.path.join(tempfile.gettempdir(), "pdf_settings_form.xaml")
    with open(tmp_path, "w") as f:
        f.write(PDF_SETTINGS_XAML)
    form = PDFSettingsForm(tmp_path)
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

    # Select Views
    selected_views = forms.select_views(
        title="Select plan views to export as PDF",
        filterfunc=lambda v: isinstance(v, DB.ViewPlan) and not v.IsTemplate
    )
    if not selected_views:
        output.print_md("**Stopped:** No views selected.")
        return

    # Target Folder
    folder = forms.pick_folder(title="Choose export folder")
    if not folder:
        output.print_md("**Stopped:** No export folder selected.")
        return

    # Single PDF Settings Dialog (preset A1 / Landscape / Color)
    settings = show_pdf_settings_dialog()
    if not settings:
        output.print_md("**Stopped:** PDF settings cancelled.")
        return

    selected_paper = settings["paper"]
    selected_orient = settings["orient"]
    selected_color = settings["color"]

    output.print_md(
        "**PDF Settings chosen:** Size: {} | Orientation: {} | Palette: {}".format(
            selected_paper, selected_orient, selected_color))

    for source_view in selected_views:
        base_name = safe_filename(source_view.Name)
        flat_copies = []
        copied_elements_map = []
        temp_ids = []
        temp_view = None

        try:
            # Step 1: Duplicate view and flatten shape-edited slabs
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

                temp_ids = [temp_view.Id] + [c_id for _, c_id in copied_elements_map if c_id in flat_copies]
                doc.Regenerate()

            # Step 2: Export Clean View directly to PDF
            pdf_opts = DB.PDFExportOptions()
            pdf_opts.Combine = False
            pdf_opts.FileName = base_name

            # Setup options within try-catch blocks to guarantee execution across versions
            try:
                pdf_opts.RasterQuality = DB.RasterQualityType.Presentation
            except Exception:
                pass
            try:
                pdf_opts.ZoomType = DB.ZoomType.Zoom
                pdf_opts.ZoomPercentage = 100
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

            # Safe Paper format configuration (prevents AttributeError)
            try:
                pdf_opts.PaperFormat = get_safe_paper_format(selected_paper)
            except Exception:
                pass

            # Orientation
            try:
                if selected_orient == "Portrait":
                    pdf_opts.PaperOrientation = DB.PageOrientationType.Portrait
                else:
                    pdf_opts.PaperOrientation = DB.PageOrientationType.Landscape
            except Exception:
                pass

            # Color Configuration
            try:
                color_map = {
                    "Color": DB.ColorDepthType.Color,
                    "GrayScale": DB.ColorDepthType.GrayScale,
                    "BlackLine": DB.ColorDepthType.BlackLine
                }
                pdf_opts.ColorDepth = color_map.get(selected_color, DB.ColorDepthType.Color)
            except Exception:
                pass

            # Native Revit PDF Export Call
            export_view_ids = List[DB.ElementId]()
            export_view_ids.Add(temp_view.Id)

            doc.Export(folder, export_view_ids, pdf_opts)

            final_target_pdf = os.path.join(folder, base_name + ".pdf")
            if os.path.exists(final_target_pdf):
                output.print_md("**{}**: PDF exported successfully: `{}`".format(
                    source_view.Name, final_target_pdf))
            else:
                output.print_md("**{}**: Export finished. Check folder: `{}`".format(
                    source_view.Name, folder))

        except Exception:
            output.print_md("**{}**: PDF Export FAILED —".format(source_view.Name))
            output.print_code(traceback.format_exc())

        finally:
            # Step 3: Clean up temporary flat components and cloned views
            all_to_delete = list(temp_ids) + [
                c_id for _, c_id in copied_elements_map
                if c_id not in flat_copies
            ]
            if all_to_delete:
                with revit.Transaction("Clean up Temp PDF Objects: " + source_view.Name):
                    for eid in all_to_delete:
                        try:
                            doc.Delete(eid)
                        except Exception:
                            pass


main()