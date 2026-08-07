# -*- coding: utf-8 -*-
"""RevitBot - Revit Tools Module. All Revit API tool functions for the chat bot."""

import clr
import os
import System
from System.Collections.Generic import List

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI.Selection import *


class RevitTools(object):
    """All the Revit tool functions the bot can execute."""

    def __init__(self, doc, uidoc):
        self.doc = doc
        self.uidoc = uidoc
        self.app = doc.Application

    # ─── Helper: Refresh Revit UI after changes ─────────────────────────

    def _refresh(self):
        try:
            self.uidoc.RefreshActiveView()
        except:
            pass
        try:
            self.doc.Regenerate()
        except:
            pass

    # ─── EXPORT NWC ───────────────────────────────────────────────────────

    def export_nwc(self, output_path=None):
        try:
            if not self.doc.PathName or len(self.doc.PathName) < 2:
                return False, ("Cannot export NWC - file is not saved yet.\n"
                               "Save the file first, then export.")
            if not output_path:
                folder = os.path.dirname(self.doc.PathName)
                if not folder or not os.path.exists(folder):
                    folder = os.path.expanduser("~\\Desktop")
                filename = os.path.splitext(os.path.basename(self.doc.Title))[0]
                output_path = os.path.join(folder, filename + ".nwc")
            folder = os.path.dirname(output_path)
            if not os.path.exists(folder):
                os.makedirs(folder)
            try:
                navisworks_export_options = NavisworksExportOptions()
                navisworks_export_options.ExportScope = NavisworksExportScope.Model
                navisworks_export_options.ViewId = self.doc.ActiveView.Id
                navisworks_export_options.Coordinates = NavisworksCoordinates.Shared
                navisworks_export_options.ConvertElementProperties = True
                navisworks_export_options.ExportLinks = True
                navisworks_export_options.ExportRoomAsAttribute = True
                navisworks_export_options.ExportUrls = True
                result = self.doc.Export(folder, os.path.basename(output_path), navisworks_export_options)
                return True, "NWC exported successfully to:\n{}".format(output_path)
            except Exception as inner_ex:
                inner_msg = str(inner_ex).lower()
                if "navisworks" in inner_msg or "nwc" in inner_msg:
                    return False, ("NWC export failed. Make sure the Navisworks exporter "
                                   "is installed.\nError: {}".format(str(inner_ex)))
                return False, "Error exporting NWC: {}".format(str(inner_ex))
        except Exception as ex:
            return False, "Error exporting NWC: {}".format(str(ex))

    # ─── EXPORT IFC ───────────────────────────────────────────────────────

    def export_ifc(self, output_path=None):
        try:
            if not output_path:
                folder = os.path.dirname(self.doc.PathName)
                if not folder or not os.path.exists(folder):
                    folder = os.path.expanduser("~\\Desktop")
                filename = os.path.splitext(os.path.basename(self.doc.Title))[0]
                output_path = os.path.join(folder, filename + ".ifc")
            folder = os.path.dirname(output_path)
            if not os.path.exists(folder):
                os.makedirs(folder)
            ifc_export_options = IFCExportOptions()
            ifc_export_options.FileVersion = IFCVersion.IFC4
            result = self.doc.Export(folder, os.path.basename(output_path), ifc_export_options)
            return True, "IFC exported successfully to:\n{}".format(output_path)
        except Exception as ex:
            return False, "Error exporting IFC: {}".format(str(ex))

    # ─── EXPORT DWG ───────────────────────────────────────────────────────

    def export_dwg(self, output_path=None):
        try:
            if not output_path:
                folder = os.path.dirname(self.doc.PathName)
                if not folder or not os.path.exists(folder):
                    folder = os.path.expanduser("~\\Desktop")
                filename = os.path.splitext(os.path.basename(self.doc.Title))[0]
                output_path = os.path.join(folder, filename + ".dwg")
            folder = os.path.dirname(output_path)
            if not os.path.exists(folder):
                os.makedirs(folder)
            dwg_export_options = DWGExportOptions()
            dwg_export_options.MergedViews = True
            dwg_export_options.SharedCoords = True
            views = List[ElementId]()
            views.Add(self.doc.ActiveView.Id)
            result = self.doc.Export(folder, os.path.basename(output_path), views, dwg_export_options)
            return True, "DWG exported successfully to:\n{}".format(output_path)
        except Exception as ex:
            return False, "Error exporting DWG: {}".format(str(ex))

    # ─── EXPORT PDF ───────────────────────────────────────────────────────

    def export_pdf(self, output_path=None):
        try:
            if not output_path:
                folder = os.path.dirname(self.doc.PathName)
                if not folder or not os.path.exists(folder):
                    folder = os.path.expanduser("~\\Desktop")
                filename = os.path.splitext(os.path.basename(self.doc.Title))[0]
                output_path = os.path.join(folder, filename + ".pdf")
            folder = os.path.dirname(output_path)
            if not os.path.exists(folder):
                os.makedirs(folder)
            try:
                pdf_export_options = PDFExportOptions()
                pdf_export_options.FileName = os.path.splitext(os.path.basename(output_path))[0]
                pdf_export_options.Combine = True
                views = List[ElementId]()
                views.Add(self.doc.ActiveView.Id)
                self.doc.Export(folder, pdf_export_options, views)
                return True, "PDF exported to:\n{}".format(output_path)
            except:
                print_mgr = self.doc.PrintManager
                print_mgr.SelectNewPrintDriver("Microsoft Print to PDF")
                print_mgr.PrintToFileName = output_path
                print_mgr.SubmitPrint()
                return True, "Printed to PDF:\n{}".format(output_path)
        except Exception as ex:
            return False, "Error exporting PDF: {}".format(str(ex))

    # ─── SAVE / SAVE AS ──────────────────────────────────────────────────

    def save_file(self, save_path=None):
        try:
            if not self.doc.IsModified and not save_path:
                return True, "Document is already up to date. No changes to save."
            if save_path:
                folder = os.path.dirname(save_path)
                if folder and not os.path.exists(folder):
                    os.makedirs(folder)
                model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(save_path)
                save_as_options = SaveAsOptions()
                save_as_options.OverwriteExistingFile = True
                save_as_options.Compact = True
                save_as_options.MaximumBackups = 3
                self.doc.SaveAs(model_path, save_as_options)
                return True, "File saved to:\n{}".format(save_path)
            else:
                self.doc.Save()
                return True, "File saved successfully."
        except Exception as ex:
            return False, "Error saving file: {}".format(str(ex))

    # ─── CREATE SHEETS (supports count) ──────────────────────────────────

    def create_sheet(self, count=1, sheet_number=None, sheet_name=None):
        try:
            tb_collector = FilteredElementCollector(self.doc).OfClass(FamilySymbol)
            title_blocks = [tb for tb in tb_collector
                           if tb.Category and tb.Category.Id.IntegerValue == int(BuiltInCategory.OST_TitleBlocks)]
            if not title_blocks:
                tb_id = ElementId.InvalidElementId
            else:
                tb_id = title_blocks[0].Id
            existing_sheets = list(FilteredElementCollector(self.doc).OfClass(ViewSheet))
            existing_count = len(existing_sheets)
            count = max(1, min(count, 100))
            t = Transaction(self.doc, "RevitBot: Create {} Sheet(s)".format(count))
            t.Start()
            try:
                created = []
                for i in range(count):
                    s_num = sheet_number if sheet_number else "A-{}".format(existing_count + i + 1)
                    s_name = sheet_name if sheet_name else "New Sheet"
                    sheet = ViewSheet.Create(self.doc, tb_id)
                    sheet.SheetNumber = s_num
                    sheet.Name = s_name
                    created.append(s_num)
                t.Commit()
                self._refresh()
                return True, "Created {} sheet(s):\n{}".format(count, "\n".join(created))
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── RENAME SHEET ────────────────────────────────────────────────────

    def rename_sheet(self, sheet_number=None, new_name=None):
        try:
            if not sheet_number:
                return False, "Specify a sheet number. Example: 'rename sheet A-1 to Floor Plan'"
            if not new_name:
                return False, "Specify a new name. Example: 'rename sheet A-1 to Floor Plan'"
            all_sheets = list(FilteredElementCollector(self.doc).OfClass(ViewSheet))
            target = None
            for s in all_sheets:
                if s.SheetNumber.lower() == sheet_number.lower():
                    target = s
                    break
            if not target:
                for s in all_sheets:
                    if sheet_number.lower() in s.SheetNumber.lower():
                        target = s
                        break
            if not target:
                sheet_info = ["  {} - {}".format(s.SheetNumber, s.Name) for s in all_sheets[:10]]
                return False, ("Sheet '{}' not found. Existing sheets:\n{}".format(
                    sheet_number, "\n".join(sheet_info)))
            old_name = target.Name
            t = Transaction(self.doc, "RevitBot: Rename Sheet")
            t.Start()
            try:
                target.Name = new_name
                t.Commit()
                self._refresh()
                return True, "Sheet renamed:\n  {} - '{}' -> '{}'".format(
                    target.SheetNumber, old_name, new_name)
            except Exception as ex:
                t.RollBack()
                return False, "Error renaming sheet: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── RENAME SHEET NUMBER ─────────────────────────────────────────────

    def rename_sheet_number(self, old_number=None, new_number=None):
        try:
            if not old_number or not new_number:
                return False, "Specify old and new numbers. Example: 'renumber sheet A-1 to A-2'"
            all_sheets = list(FilteredElementCollector(self.doc).OfClass(ViewSheet))
            target = None
            for s in all_sheets:
                if s.SheetNumber.lower() == old_number.lower():
                    target = s
                    break
            if not target:
                return False, "Sheet '{}' not found.".format(old_number)
            t = Transaction(self.doc, "RevitBot: Renumber Sheet")
            t.Start()
            try:
                target.SheetNumber = new_number
                t.Commit()
                self._refresh()
                return True, "Sheet renumbered: {} -> {}".format(old_number, new_number)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── DELETE SHEET BY NUMBER ──────────────────────────────────────────

    def delete_sheet(self, sheet_number=None):
        try:
            if not sheet_number:
                return False, "Specify a sheet number. Example: 'delete sheet A-1'"
            all_sheets = list(FilteredElementCollector(self.doc).OfClass(ViewSheet))
            target = None
            for s in all_sheets:
                if s.SheetNumber.lower() == sheet_number.lower():
                    target = s
                    break
            if not target:
                for s in all_sheets:
                    if sheet_number.lower() in s.SheetNumber.lower():
                        target = s
                        break
            if not target:
                sheet_info = ["  {} - {}".format(s.SheetNumber, s.Name) for s in all_sheets[:10]]
                return False, ("Sheet '{}' not found. Existing sheets:\n{}".format(
                    sheet_number, "\n".join(sheet_info)))
            sheet_name = target.Name
            sheet_num = target.SheetNumber
            t = Transaction(self.doc, "RevitBot: Delete Sheet")
            t.Start()
            try:
                self.doc.Delete(target.Id)
                t.Commit()
                self._refresh()
                return True, "Deleted sheet: {} - {}".format(sheet_num, sheet_name)
            except Exception as ex:
                t.RollBack()
                return False, "Error deleting sheet: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── DELETE VIEW BY NAME ─────────────────────────────────────────────

    def delete_view(self, view_name=None):
        try:
            if not view_name:
                return False, "Specify a view name. Example: 'delete view Section 1'"
            all_views = list(FilteredElementCollector(self.doc).OfClass(View))
            non_template = [v for v in all_views if not v.IsTemplate]
            target = None
            for v in non_template:
                if v.Name.lower() == view_name.lower():
                    target = v
                    break
            if not target:
                for v in non_template:
                    if view_name.lower() in v.Name.lower():
                        target = v
                        break
            if not target:
                return False, "View '{}' not found. Type 'list views' to see all.".format(view_name)
            v_name = target.Name
            v_type = str(target.ViewType)
            t = Transaction(self.doc, "RevitBot: Delete View")
            t.Start()
            try:
                self.doc.Delete(target.Id)
                t.Commit()
                self._refresh()
                return True, "Deleted view: {} ({})".format(v_name, v_type)
            except Exception as ex:
                t.RollBack()
                return False, "Error deleting view: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── DELETE ELEMENTS BY CATEGORY ─────────────────────────────────────

    def delete_elements_by_category(self, category_name=None):
        try:
            if not category_name:
                return False, "Specify a category. Example: 'delete all walls'"
            categories = self.doc.Settings.Categories
            target_cat = None
            for cat in categories:
                if cat.Name.lower() == category_name.lower():
                    target_cat = cat
                    break
            if not target_cat:
                cat_list = [c.Name for c in categories]
                return False, ("Category '{}' not found. Available:\n{}".format(
                    category_name, "\n".join(sorted(cat_list)[:20])))
            collector = FilteredElementCollector(self.doc)\
                .OfCategory(target_cat.Id)\
                .WhereElementIsNotElementType()
            elements = list(collector)
            if not elements:
                return True, "No {} elements found to delete.".format(category_name)
            count = len(elements)
            t = Transaction(self.doc, "RevitBot: Delete {} {}(s)".format(count, category_name))
            t.Start()
            try:
                deleted = 0
                for elem in elements:
                    try:
                        self.doc.Delete(elem.Id)
                        deleted += 1
                    except:
                        pass
                t.Commit()
                self._refresh()
                return True, "Deleted {} {} element(s).".format(deleted, category_name)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── DELETE SELECTED ─────────────────────────────────────────────────

    def delete_selected(self):
        try:
            selected = self.uidoc.Selection.GetElementIds()
            if not selected or len(selected) == 0:
                return False, ("No elements selected.\n\n"
                               "Instead of selecting, you can type:\n"
                               "  'delete sheet A-1'\n"
                               "  'delete view Section 1'\n"
                               "  'delete all walls'\n"
                               "  'delete all doors'")
            count = len(selected)
            t = Transaction(self.doc, "RevitBot: Delete Elements")
            t.Start()
            try:
                for eid in selected:
                    self.doc.Delete(eid)
                t.Commit()
                self._refresh()
                return True, "Deleted {} elements.".format(count)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE ROOM ─────────────────────────────────────────────────────

    def create_room(self, count=1, level_name=None, room_name=None):
        try:
            all_levels = list(FilteredElementCollector(self.doc).OfClass(Level))
            target_level = None
            for lvl in all_levels:
                if level_name and level_name.lower() in lvl.Name.lower():
                    target_level = lvl
                    break
            if not target_level and len(all_levels) > 0:
                target_level = all_levels[0]
            if not target_level:
                return False, "No levels found in the project."
            count = max(1, min(count, 200))
            t = Transaction(self.doc, "RevitBot: Create {} Room(s)".format(count))
            t.Start()
            try:
                phase = self.doc.Phases.get_Item(0)
                created = 0
                for i in range(count):
                    try:
                        room = self.doc.Create.NewRoom(target_level, phase)
                        if room_name:
                            room.Name = room_name
                        created += 1
                    except:
                        pass
                t.Commit()
                self._refresh()
                return True, "Created {} room(s) on level '{}'.".format(
                    created, target_level.Name)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE LEVEL ────────────────────────────────────────────────────

    def create_level(self, count=1, elevation=0, name=None):
        try:
            all_levels = list(FilteredElementCollector(self.doc).OfClass(Level))
            if not elevation:
                max_elev = 0
                for lvl in all_levels:
                    if lvl.Elevation > max_elev:
                        max_elev = lvl.Elevation
                elevation = max_elev + 10.0
            count = max(1, min(count, 100))
            t = Transaction(self.doc, "RevitBot: Create {} Level(s)".format(count))
            t.Start()
            try:
                created = []
                for i in range(count):
                    elev = elevation + (i * 10.0)
                    level = Level.Create(self.doc, elev)
                    if name:
                        try:
                            level.Name = name + " " + str(i + 1)
                        except:
                            pass
                    created.append("{} (Elev: {})".format(level.Name, elev))
                t.Commit()
                self._refresh()
                return True, "Created {} level(s):\n{}".format(
                    count, "\n".join(created))
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE GRID ─────────────────────────────────────────────────────

    def create_grid(self, count=1, start_x=0, start_y=0, end_x=0, end_y=30):
        try:
            count = max(1, min(count, 50))
            t = Transaction(self.doc, "RevitBot: Create {} Grid(s)".format(count))
            t.Start()
            try:
                created = []
                for i in range(count):
                    offset = i * 10.0
                    start = XYZ(start_x + offset, start_y, 0)
                    end = XYZ(end_x + offset, end_y, 0)
                    line = Line.CreateBound(start, end)
                    grid = Grid.Create(self.doc, line)
                    created.append("Grid '{}'".format(grid.Name))
                t.Commit()
                self._refresh()
                return True, "Created {} grid(s):\n{}".format(
                    count, "\n".join(created))
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE SECTION ──────────────────────────────────────────────────

    def create_section(self):
        try:
            all_types = list(FilteredElementCollector(self.doc).OfClass(ViewFamilyType))
            section_type = None
            for vft in all_types:
                if vft.ViewFamily == ViewFamily.Section:
                    section_type = vft
                    break
            if not section_type:
                return False, "No section view type found."
            t = Transaction(self.doc, "RevitBot: Create Section")
            t.Start()
            try:
                section_box = BoundingBoxXYZ()
                section_box.Min = XYZ(-1, -1, -1)
                section_box.Max = XYZ(20, 20, 20)
                section_view = ViewSection.CreateSection(
                    self.doc, section_type.Id, section_box)
                t.Commit()
                self._refresh()
                return True, "Section view created: {}".format(section_view.Name)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE CALLOUT ──────────────────────────────────────────────────

    def create_callout(self):
        try:
            active_view = self.doc.ActiveView
            if not active_view:
                return False, "No active view."
            all_types = list(FilteredElementCollector(self.doc).OfClass(ViewFamilyType))
            callout_type = None
            for vft in all_types:
                if vft.ViewFamily == ViewFamily.DetailView:
                    callout_type = vft
                    break
            if not callout_type:
                return False, "No callout view type found."
            t = Transaction(self.doc, "RevitBot: Create Callout")
            t.Start()
            try:
                bbox = BoundingBoxXYZ()
                bbox.Min = XYZ(0, 0, 0)
                bbox.Max = XYZ(10, 10, 0)
                callout = ViewSection.CreateCallout(
                    self.doc, active_view.Id, callout_type.Id, bbox)
                t.Commit()
                self._refresh()
                return True, "Callout created: {}".format(callout.Name)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE DIMENSION ────────────────────────────────────────────────

    def create_dimension(self):
        try:
            active_view = self.doc.ActiveView
            if not active_view:
                return False, "No active view."
            self.uidoc.RefreshActiveView()
            try:
                refs = self.uidoc.Selection.PickObjects(
                    ObjectType.Element,
                    "Pick two references for dimensioning"
                )
            except:
                return False, "Reference picking cancelled."
            if len(refs) < 2:
                return False, "Need at least 2 references for a dimension."
            ref1 = refs[0]
            ref2 = refs[1]
            line = Line.CreateBound(
                self.doc.GetElement(ref1.ElementId).Location.Point,
                self.doc.GetElement(ref2.ElementId).Location.Point
            )
            t = Transaction(self.doc, "RevitBot: Create Dimension")
            t.Start()
            try:
                dim = self.doc.Create.NewDimension(
                    active_view, line, ref1, ref2)
                t.Commit()
                self._refresh()
                return True, "Dimension created between selected references."
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE TEXT NOTE ────────────────────────────────────────────────

    def create_text_note(self, text="RevitBot was here", x=0, y=0):
        try:
            active_view = self.doc.ActiveView
            if not active_view:
                return False, "No active view."
            t = Transaction(self.doc, "RevitBot: Create Text Note")
            t.Start()
            try:
                position = XYZ(x, y, 0)
                text_note = TextNote.Create(
                    self.doc, active_view.Id, position, text,
                    self.doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType))
                t.Commit()
                self._refresh()
                return True, "Text note created: '{}'".format(text)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE FILLED REGION (PICK LINES) ───────────────────────────────

    def create_filled_region_pick_lines(self):
        try:
            active_view = self.doc.ActiveView
            if not active_view:
                return False, "No active view found."
            fr_types = list(FilteredElementCollector(self.doc).OfClass(FilledRegionType))
            if not fr_types:
                return False, "No filled region types found in this document."
            fr_type = fr_types[0]
            self.uidoc.RefreshActiveView()
            try:
                picked_refs = self.uidoc.Selection.PickObjects(
                    ObjectType.Element,
                    "Pick lines for filled region boundary (Press ESC when done)"
                )
            except Exception:
                return False, "Line picking cancelled or no lines selected."
            if not picked_refs or len(picked_refs) == 0:
                return False, "No lines were picked."
            curves = []
            for ref in picked_refs:
                elem = self.doc.GetElement(ref.ElementId)
                if isinstance(elem, DetailLine):
                    curves.append(elem.GeometryCurve)
                elif isinstance(elem, ModelCurve):
                    curves.append(elem.GeometryCurve)
                elif hasattr(elem, 'GeometryCurve') and elem.GeometryCurve:
                    curves.append(elem.GeometryCurve)
            if not curves:
                return False, "No valid curves found from the picked lines."
            curve_loop = CurveLoop()
            for curve in curves:
                try:
                    curve_loop.Append(curve)
                except:
                    pass
            curve_loops = List[CurveLoop]()
            curve_loops.Add(curve_loop)
            t = Transaction(self.doc, "RevitBot: Create Filled Region")
            t.Start()
            try:
                filled_region = FilledRegion.Create(
                    self.doc, fr_type.Id, active_view.Id, curve_loops)
                t.Commit()
                self._refresh()
                return True, "Filled region created! View: {}".format(active_view.Name)
            except Exception as ex:
                t.RollBack()
                return False, "Error creating filled region: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE FILLED REGION (RECTANGLE) ────────────────────────────────

    def create_filled_region_rect(self, x=0, y=0, width=10, height=10):
        try:
            active_view = self.doc.ActiveView
            if not active_view:
                return False, "No active view."
            fr_types = list(FilteredElementCollector(self.doc).OfClass(FilledRegionType))
            if not fr_types:
                return False, "No filled region types found."
            fr_type = fr_types[0]
            p1 = XYZ(x, y, 0)
            p2 = XYZ(x + width, y, 0)
            p3 = XYZ(x + width, y + height, 0)
            p4 = XYZ(x, y + height, 0)
            lines = [
                Line.CreateBound(p1, p2),
                Line.CreateBound(p2, p3),
                Line.CreateBound(p3, p4),
                Line.CreateBound(p4, p1),
            ]
            curve_loop = CurveLoop()
            for line in lines:
                curve_loop.Append(line)
            curve_loops = List[CurveLoop]()
            curve_loops.Add(curve_loop)
            t = Transaction(self.doc, "RevitBot: Create Filled Region")
            t.Start()
            try:
                filled_region = FilledRegion.Create(
                    self.doc, fr_type.Id, active_view.Id, curve_loops)
                t.Commit()
                self._refresh()
                return True, "Rectangular filled region created! Size: {} x {}".format(width, height)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE ROOM TAGS ────────────────────────────────────────────────

    def create_room_tag(self):
        try:
            active_view = self.doc.ActiveView
            if not active_view:
                return False, "No active view."
            rooms = list(FilteredElementCollector(self.doc, active_view.Id)
                .OfCategory(BuiltInCategory.OST_Rooms)
                .WhereElementIsNotElementType())
            t = Transaction(self.doc, "RevitBot: Create Room Tags")
            t.Start()
            try:
                count = 0
                for room in rooms:
                    if room.Location:
                        point = room.Location.Point
                        tag = self.doc.Create.NewRoomTag(
                            room, UV(point.X, point.Y), active_view.Id)
                        count += 1
                t.Commit()
                self._refresh()
                return True, "Created {} room tags.".format(count)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE WORKSET ──────────────────────────────────────────────────

    def create_workset(self, workset_name="New Workset"):
        try:
            if not self.doc.IsWorkshared:
                return False, "This document is not workshared. Enable worksharing first."
            t = Transaction(self.doc, "RevitBot: Create Workset")
            t.Start()
            try:
                workset = Workset.Create(self.doc, workset_name)
                t.Commit()
                return True, "Workset '{}' created.".format(workset_name)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── OPEN / ACTIVATE VIEW ────────────────────────────────────────────

    def open_view(self, view_name=None):
        try:
            if not view_name:
                return False, "Specify a view name. Example: 'open view Level 1'", None
            all_views = list(FilteredElementCollector(self.doc).OfClass(View))
            non_template = [v for v in all_views if not v.IsTemplate]
            target_view = None
            for v in non_template:
                if v.Name.lower() == view_name.lower():
                    target_view = v
                    break
            if not target_view:
                for v in non_template:
                    if view_name.lower() in v.Name.lower():
                        target_view = v
                        break
            if not target_view:
                for v in non_template:
                    if view_name.lower() in str(v.ViewType).lower():
                        target_view = v
                        break
            if not target_view:
                view_names = [v.Name for v in non_template[:15]]
                return False, ("View '{}' not found. Available:\n{}".format(
                    view_name, "\n".join(view_names))), None
            return True, ("Found view: {} ({})\n"
                          "Closing chat to open the view...").format(
                              target_view.Name, target_view.ViewType), target_view.Id
        except Exception as ex:
            return False, "Error: {}".format(str(ex)), None

    # ─── ZOOM TO FIT ─────────────────────────────────────────────────────

    def zoom_to_fit(self):
        try:
            command_id = RevitCommandId.LookupPostableCommand(
                PostableCommand.ZoomToFit)
            self.uidoc.Document.Application.PostCommand(command_id)
            return True, "Zoomed to fit."
        except:
            return False, "Could not zoom to fit. Try keyboard shortcut ZF."

    # ─── SET PARAMETER ───────────────────────────────────────────────────

    def set_parameter(self, param_name=None, param_value=None):
        try:
            selected = self.uidoc.Selection.GetElementIds()
            if not selected or len(selected) == 0:
                return False, "No elements selected. Select elements first."
            if not param_name:
                return False, "Specify a parameter name."
            results = []
            t = Transaction(self.doc, "RevitBot: Set Parameter")
            t.Start()
            try:
                for eid in selected:
                    elem = self.doc.GetElement(eid)
                    param = elem.LookupParameter(param_name)
                    if not param:
                        for p in elem.Parameters:
                            if p.Definition.Name.lower() == param_name.lower():
                                param = p
                                break
                    if param and not param.IsReadOnly:
                        if param.StorageType == StorageType.String:
                            param.Set(param_value)
                            results.append("Set '{}' on element {}".format(param_name, eid.IntegerValue))
                        elif param.StorageType == StorageType.Double:
                            param.Set(float(param_value))
                            results.append("Set '{}' = {} on element {}".format(param_name, param_value, eid.IntegerValue))
                        elif param.StorageType == StorageType.Integer:
                            param.Set(int(param_value))
                            results.append("Set '{}' = {} on element {}".format(param_name, param_value, eid.IntegerValue))
                    elif param and param.IsReadOnly:
                        results.append("'{}' is read-only on element {}".format(param_name, eid.IntegerValue))
                    else:
                        results.append("'{}' not found on element {}".format(param_name, eid.IntegerValue))
                t.Commit()
                self._refresh()
                return True, "Parameter results:\n{}".format("\n".join(results))
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── GET PARAMETER ───────────────────────────────────────────────────

    def get_parameter(self, param_name=None):
        try:
            selected = self.uidoc.Selection.GetElementIds()
            if not selected or len(selected) == 0:
                return False, "No elements selected."
            if not param_name:
                return False, "Specify a parameter name."
            results = []
            for eid in selected:
                elem = self.doc.GetElement(eid)
                param = elem.LookupParameter(param_name)
                if not param:
                    for p in elem.Parameters:
                        if p.Definition.Name.lower() == param_name.lower():
                            param = p
                            break
                if param:
                    val = ""
                    if param.StorageType == StorageType.String:
                        val = param.AsString()
                    elif param.StorageType == StorageType.Double:
                        val = str(param.AsDouble())
                    elif param.StorageType == StorageType.Integer:
                        val = str(param.AsInteger())
                    elif param.StorageType == StorageType.ElementId:
                        val = str(param.AsElementId().IntegerValue)
                    results.append("{} -> {}: {}".format(elem.Name, param_name, val))
                else:
                    results.append("'{}' not found on {}".format(param_name, elem.Name))
            return True, "Parameter values:\n{}".format("\n".join(results))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── SELECT ALL OF CATEGORY ──────────────────────────────────────────

    def select_all_of_category(self, category_name=None):
        try:
            if not category_name:
                return False, "Specify a category name."
            categories = self.doc.Settings.Categories
            target_cat = None
            for cat in categories:
                if cat.Name.lower() == category_name.lower():
                    target_cat = cat
                    break
            if not target_cat:
                cat_list = [c.Name for c in categories]
                return False, ("Category '{}' not found. Available:\n{}".format(
                    category_name, "\n".join(sorted(cat_list)[:20])))
            collector = FilteredElementCollector(self.doc)\
                .OfCategory(target_cat.Id)\
                .WhereElementIsNotElementType()
            ids = List[ElementId]()
            for elem in collector:
                ids.Add(elem.Id)
            self.uidoc.Selection.SetElementIds(ids)
            return True, "Selected {} elements of category '{}'.".format(len(ids), category_name)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── LIST VIEWS ──────────────────────────────────────────────────────

    def list_views(self):
        try:
            all_views = list(FilteredElementCollector(self.doc).OfClass(View))
            non_template = [v for v in all_views if not v.IsTemplate]
            result_lines = ["Views in project ({} total):".format(len(non_template))]
            for v in non_template[:30]:
                result_lines.append("  {} - {} (ID: {})".format(
                    v.ViewType, v.Name, v.Id.IntegerValue))
            if len(non_template) > 30:
                result_lines.append("  ... and {} more".format(len(non_template) - 30))
            return True, "\n".join(result_lines)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── LIST SHEETS ─────────────────────────────────────────────────────

    def list_sheets(self):
        try:
            all_sheets = list(FilteredElementCollector(self.doc).OfClass(ViewSheet))
            result_lines = ["Sheets in project ({} total):".format(len(all_sheets))]
            for s in all_sheets:
                result_lines.append("  {} - {} (ID: {})".format(
                    s.SheetNumber, s.Name, s.Id.IntegerValue))
            return True, "\n".join(result_lines)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── LIST LEVELS ─────────────────────────────────────────────────────

    def list_levels(self):
        try:
            all_levels = list(FilteredElementCollector(self.doc).OfClass(Level))
            result_lines = ["Levels:"]
            for l in all_levels:
                result_lines.append("  {} - Elevation: {} (ID: {})".format(
                    l.Name, l.Elevation, l.Id.IntegerValue))
            return True, "\n".join(result_lines)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── LIST FAMILIES ───────────────────────────────────────────────────

    def list_families(self, category_name=None):
        try:
            all_families = list(FilteredElementCollector(self.doc).OfClass(Family))
            if category_name:
                all_families = [f for f in all_families
                           if f.FamilyCategory and
                           f.FamilyCategory.Name.lower() == category_name.lower()]
            result_lines = ["Families ({} total):".format(len(all_families))]
            for f in all_families[:30]:
                cat = f.FamilyCategory.Name if f.FamilyCategory else "No Category"
                result_lines.append("  {} [{}]".format(f.Name, cat))
            if len(all_families) > 30:
                result_lines.append("  ... and {} more".format(len(all_families) - 30))
            return True, "\n".join(result_lines)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── LIST WORKSETS ───────────────────────────────────────────────────

    def list_worksets(self):
        try:
            if not self.doc.IsWorkshared:
                return False, "This document is not workshared."
            worksets = FilteredWorksetCollector(self.doc).OfWorksetKind(
                WorksetKind.UserWorkset)
            result_lines = ["Worksets:"]
            for ws in worksets:
                result_lines.append("  {} (Open: {})".format(ws.Name, ws.IsOpen))
            return True, "\n".join(result_lines)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── LIST MATERIALS ──────────────────────────────────────────────────

    def list_materials(self):
        try:
            all_materials = list(FilteredElementCollector(self.doc).OfClass(Material))
            result_lines = ["Materials ({} total):".format(len(all_materials))]
            for m in all_materials:
                result_lines.append("  {} (ID: {})".format(m.Name, m.Id.IntegerValue))
            return True, "\n".join(result_lines)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── LIST CATEGORIES ─────────────────────────────────────────────────

    def list_categories(self):
        try:
            categories = self.doc.Settings.Categories
            result_lines = ["Categories ({} total):".format(len(list(categories)))]
            for c in sorted(categories, key=lambda x: x.Name):
                result_lines.append("  {}".format(c.Name))
            return True, "\n".join(result_lines)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── DOCUMENT INFO ───────────────────────────────────────────────────

    def get_document_info(self):
        try:
            doc = self.doc
            info = [
                "Document Information:",
                "  Title: {}".format(doc.Title),
                "  Path: {}".format(doc.PathName),
                "  Is Modified: {}".format(doc.IsModified),
                "  Is Workshared: {}".format(doc.IsWorkshared),
                "  Is Family: {}".format(doc.IsFamilyDocument),
            ]
            try:
                active_view_name = doc.ActiveView.Name if doc.ActiveView else "None"
            except:
                active_view_name = "Unknown"
            info.append("  Active View: {}".format(active_view_name))
            all_elements = list(FilteredElementCollector(doc).WhereElementIsNotElementType())
            info.append("  Total Elements: {}".format(len(all_elements)))
            all_views = list(FilteredElementCollector(doc).OfClass(View))
            view_count = len([v for v in all_views if not v.IsTemplate])
            info.append("  Total Views: {}".format(view_count))
            all_sheets = list(FilteredElementCollector(doc).OfClass(ViewSheet))
            info.append("  Total Sheets: {}".format(len(all_sheets)))
            all_levels = list(FilteredElementCollector(doc).OfClass(Level))
            level_names = [l.Name for l in all_levels]
            info.append("  Levels: {}".format(", ".join(level_names)))
            return True, "\n".join(info)
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── ENABLE WORKSHARING ──────────────────────────────────────────────

    def enable_worksharing(self):
        try:
            if self.doc.IsWorkshared:
                return True, "Worksharing is already enabled."
            t = Transaction(self.doc, "RevitBot: Enable Worksharing")
            t.Start()
            try:
                self.doc.EnableWorksharing("Shared Levels and Grids", "Workset1")
                t.Commit()
                return True, "Worksharing enabled successfully."
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── BATCH SET PARAMETER ─────────────────────────────────────────────

    def batch_set_parameter(self, category_name=None, param_name=None, param_value=None):
        try:
            if not all([category_name, param_name, param_value]):
                return False, "Need category, parameter name, and value."
            categories = self.doc.Settings.Categories
            target_cat = None
            for cat in categories:
                if cat.Name.lower() == category_name.lower():
                    target_cat = cat
                    break
            if not target_cat:
                return False, "Category '{}' not found.".format(category_name)
            elements = list(FilteredElementCollector(self.doc)
                .OfCategory(target_cat.Id)
                .WhereElementIsNotElementType())
            if not elements:
                return False, "No elements found in category '{}'.".format(category_name)
            t = Transaction(self.doc, "RevitBot: Batch Set Parameter")
            t.Start()
            try:
                count = 0
                for elem in elements:
                    param = elem.LookupParameter(param_name)
                    if param and not param.IsReadOnly:
                        if param.StorageType == StorageType.String:
                            param.Set(param_value)
                            count += 1
                        elif param.StorageType == StorageType.Double:
                            param.Set(float(param_value))
                            count += 1
                        elif param.StorageType == StorageType.Integer:
                            param.Set(int(param_value))
                            count += 1
                t.Commit()
                self._refresh()
                return True, "Set '{}' = '{}' on {} elements of '{}'.".format(
                    param_name, param_value, count, category_name)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))

    # ─── CREATE VIEW FILTER ──────────────────────────────────────────────

    def create_view_filter(self, filter_name=None):
        try:
            if not filter_name:
                return False, "Specify a filter name."
            active_view = self.doc.ActiveView
            if not active_view:
                return False, "No active view."
            categories = self.doc.Settings.Categories
            target_cat = None
            for cat in categories:
                if cat.Name.lower() == "walls":
                    target_cat = cat
                    break
            t = Transaction(self.doc, "RevitBot: Create View Filter")
            t.Start()
            try:
                cat_ids = List[ElementId]()
                if target_cat:
                    cat_ids.Add(target_cat.Id)
                parameter_filter = ParameterFilterElement
                filter_element = parameter_filter.Create(
                    self.doc, filter_name, cat_ids)
                override = OverrideGraphicSettings()
                override.SetProjectionLineColor(DBColor(255, 0, 0))
                override.SetCutLineColor(DBColor(255, 0, 0))
                active_view.AddFilter(filter_element.Id)
                active_view.SetFilterOverrides(filter_element.Id, override)
                t.Commit()
                self._refresh()
                return True, "View filter '{}' created and applied.".format(filter_name)
            except Exception as ex:
                t.RollBack()
                return False, "Error: {}".format(str(ex))
        except Exception as ex:
            return False, "Error: {}".format(str(ex))
