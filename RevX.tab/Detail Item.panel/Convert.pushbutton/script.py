# -*- coding: utf-8 -*-
"""One-click: take the selected element (as shown in the active view),
export it to a temporary CAD (DWG) file, bring that CAD file into a new
Detail Item family as native detail lines, save the family, and load it
back into the project at the original location.

The ONLY user-facing dialogs are:
  1. Name the new family
  2. Choose where to save the .rfa

Everything else (temp CAD export path, family template lookup, CAD import,
line conversion, load-back, cleanup) happens silently. Non-fatal problems
are written to the pyRevit output console instead of a blocking popup.
"""

__title__ = 'Element to\nDetail Item'
__author__ = 'Jesto Joy'
__doc__ = ('Select an element in a view and click this button. It exports '
           'the element to a temporary CAD file, converts that into a new '
           'Detail Item family, and loads it back into the project at the '
           'same location. You will only be asked for a family name and a '
           'save location.')

import os
import tempfile
import time

from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application

output = script.get_output()
logger = script.get_logger()


def log(msg):
    output.print_md(msg)


# --------------------------------------------------------------------------
# Silent family load options - suppresses the "overwrite?" dialog
# --------------------------------------------------------------------------
class SilentFamilyLoadOptions(DB.IFamilyLoadOptions):
    """Always overwrite family and its parameter values without prompting."""
    
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        """Called when family already exists in project."""
        overwriteParameterValues.Value = True
        return True
    
    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        """Called when a shared/nested family already exists."""
        source.Value = DB.FamilySource.Family
        overwriteParameterValues.Value = True
        return True


# --------------------------------------------------------------------------
# 1. Selection + active view
# --------------------------------------------------------------------------
def get_selection_and_view():
    selection = revit.get_selection()
    if not selection:
        log('**Aborted:** nothing was selected.')
        script.exit()

    view = uidoc.ActiveView
    if not view or not isinstance(view, DB.View):
        log('**Aborted:** no active graphical view.')
        script.exit()

    ids = List[DB.ElementId]([el.Id for el in selection])
    return ids, view


# --------------------------------------------------------------------------
# 2. Find a "single layer" DWG export setup if one exists, else default
# --------------------------------------------------------------------------
def get_dwg_export_options():
    settings = DB.FilteredElementCollector(doc).OfClass(DB.ExportDWGSettings)
    for s in settings:
        name = s.Name or ''
        if 'single' in name.lower() or 'oneline' in name.lower() or 'one layer' in name.lower():
            try:
                return s.GetDWGExportOptions()
            except Exception as ex:
                logger.debug('Could not read export setup {0}: {1}'.format(name, ex))

    options = DB.DWGExportOptions()
    options.MergedViews = True
    return options


# --------------------------------------------------------------------------
# 3. Isolate the selection in the view, export DWG to a temp file, restore
# --------------------------------------------------------------------------
def export_selection_to_dwg(view, ids):
    temp_dir = tempfile.gettempdir()
    file_stub = 'revx_detail_item_{0}'.format(int(time.time()))
    dwg_path = os.path.join(temp_dir, file_stub + '.dwg')

    export_options = get_dwg_export_options()

    temp_view_id = None
    with DB.Transaction(doc, 'Create temp export view') as t:
        t.Start()
        temp_view_id = view.Duplicate(DB.ViewDuplicateOption.Duplicate)
        temp_view = doc.GetElement(temp_view_id)
        if temp_view.ViewTemplateId != DB.ElementId.InvalidElementId:
            temp_view.ViewTemplateId = DB.ElementId.InvalidElementId

        temp_view.DisplayStyle = DB.DisplayStyle.Wireframe
        temp_view.DetailLevel = DB.ViewDetailLevel.Fine
        temp_view.IsolateElementsTemporary(ids)
        t.Commit()

    try:
        view_ids = List[DB.ElementId]([temp_view_id])
        doc.Export(temp_dir, file_stub, view_ids, export_options)
    finally:
        with DB.Transaction(doc, 'Delete temp export view') as t:
            t.Start()
            try:
                doc.Delete(temp_view_id)
            except Exception as ex:
                logger.debug('Could not delete temp export view: {0}'.format(ex))
            t.Commit()

    if not os.path.exists(dwg_path):
        log('**Aborted:** CAD export did not produce a file at `{0}`.'.format(dwg_path))
        script.exit()

    return dwg_path


# --------------------------------------------------------------------------
# 4. Locate a Detail Item.rft template for the running Revit version
# --------------------------------------------------------------------------
def find_detail_item_template():
    version = app.VersionNumber
    candidates = [
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\English\Detail Item.rft'.format(version),
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\English-Imperial\Detail Item.rft'.format(version),
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\English_I\Detail Item.rft'.format(version),
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\Metric\Detail Item.rft'.format(version),
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\English-Metric\Detail Item.rft'.format(version),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path

    log('**Aborted:** could not auto-locate `Detail Item.rft` for Revit {0}. '
        'Install it or edit `find_detail_item_template()` with the correct '
        'path for your machine.'.format(version))
    script.exit()


# --------------------------------------------------------------------------
# 5. Import the DWG into the family doc and convert to native detail curves
# --------------------------------------------------------------------------
def import_dwg_as_detail_curves(fam_doc, dwg_path):
    collector = DB.FilteredElementCollector(fam_doc).OfClass(DB.View)
    fam_view = None
    for v in collector:
        if not v.IsTemplate and v.ViewType == DB.ViewType.FloorPlan:
            fam_view = v
            break
    if not fam_view:
        for v in collector:
            if not v.IsTemplate:
                fam_view = v
                break
    if not fam_view:
        log('**Aborted:** no usable view found in the family template.')
        script.exit()

    import_options = DB.DWGImportOptions()
    import_options.ColorMode = DB.ImportColorMode.BlackAndWhite
    import_options.OrientToView = False
    import_options.Placement = DB.ImportPlacement.Origin
    import_options.ThisViewOnly = False
    import_options.VisibleLayersOnly = False

    created, skipped = 0, 0
    used_cad_fallback = False

    with DB.Transaction(fam_doc, 'Import CAD and build detail lines') as t:
        t.Start()

        try:
            lines_cat = fam_doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
            lines_cat.LineColor = DB.Color(0, 0, 0)
            black_style = lines_cat.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
        except Exception as ex:
            black_style = None
            logger.debug('Could not force OST_Lines to black: {0}'.format(ex))

        existing_ids = set(
            e.Id
            for e in DB.FilteredElementCollector(fam_doc).OfClass(DB.ImportInstance)
        )

        fam_doc.Import(dwg_path, import_options, fam_view)

        import_instance = None
        for e in DB.FilteredElementCollector(fam_doc).OfClass(DB.ImportInstance):
            if e.Id not in existing_ids:
                import_instance = e
                break

        if import_instance is None:
            t.RollBack()
            log('**Aborted:** the CAD file did not produce an import instance in the family.')
            script.exit()

        opt = DB.Options()
        opt.ComputeReferences = False
        opt.IncludeNonVisibleObjects = False

        curves = []
        geo = import_instance.get_Geometry(opt)
        if geo:
            for g in geo:
                if isinstance(g, DB.Curve):
                    curves.append(g)
                elif isinstance(g, DB.GeometryInstance):
                    for gi in g.GetInstanceGeometry():
                        if isinstance(gi, DB.Curve):
                            curves.append(gi)
                elif isinstance(g, DB.GeometryElement):
                    for gi in g:
                        if isinstance(gi, DB.Curve):
                            curves.append(gi)

        for c in curves:
            try:
                new_curve = fam_doc.FamilyCreate.NewDetailCurve(fam_view, c)
                if black_style is not None:
                    try:
                        new_curve.LineStyle = black_style
                    except Exception as ex:
                        logger.debug('Could not force black line style: {0}'.format(ex))
                created += 1
            except Exception as ex:
                skipped += 1
                logger.debug('Could not create detail curve: {0}'.format(ex))

        # Delete all FilledRegion elements to remove any hatches
        fill_ids = [
            e.Id for e in
            DB.FilteredElementCollector(fam_doc)
              .OfClass(DB.FilledRegion)
              .ToElements()
        ]
        for fid in fill_ids:
            try:
                fam_doc.Delete(fid)
            except Exception as ex:
                logger.debug('Could not delete FilledRegion {0}: {1}'.format(fid, ex))

        if created > 0:
            try:
                fam_doc.Delete(import_instance.Id)
            except Exception as ex:
                logger.debug('Could not delete CAD import instance: {0}'.format(ex))
        else:
            used_cad_fallback = True
            log('**Note:** could not convert the CAD geometry into native '
                'detail lines - keeping the CAD import itself (in black) '
                'as the family content instead.')

        t.Commit()

    return created, skipped, used_cad_fallback


# --------------------------------------------------------------------------
# 6. Load the saved .rfa into the project document (silently)
# --------------------------------------------------------------------------
def load_family_into_project(save_path):
    with DB.Transaction(doc, 'Load Detail Item Family') as t:
        t.Start()
        try:
            # Use the overload with IFamilyLoadOptions to suppress dialogs
            doc.LoadFamily(save_path, SilentFamilyLoadOptions())
        except Exception as ex:
            t.RollBack()
            log('**Aborted:** could not load family from `{0}`: {1}'.format(save_path, ex))
            script.exit()
        t.Commit()


# --------------------------------------------------------------------------
# 7. Place the loaded family instance at the view origin
# --------------------------------------------------------------------------
def place_instance_at_origin(fam_name, view):
    symbol = None
    for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        if s.Family.Name == fam_name:
            symbol = s
            break
    if not symbol:
        log('**Warning:** loaded family symbol `{0}` not found; skipped placement.'.format(fam_name))
        return

    with DB.Transaction(doc, 'Place Detail Item Instance') as t:
        t.Start()
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
        new_instance = doc.Create.NewFamilyInstance(DB.XYZ.Zero, symbol, view)

        try:
            ogs = DB.OverrideGraphicSettings()
            ogs.SetProjectionLineColor(DB.Color(0, 0, 0))
            view.SetElementOverrides(new_instance.Id, ogs)
        except Exception as ex:
            logger.debug('Could not apply black view override: {0}'.format(ex))

        t.Commit()

    try:
        uidoc.Selection.SetElementIds(List[DB.ElementId]())
    except Exception as ex:
        logger.debug('Could not clear selection: {0}'.format(ex))


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ids, view = get_selection_and_view()

    fam_name = forms.ask_for_string(
        default='New Detail Item',
        prompt='Enter a name for the new Detail Item family:',
        title='Element to Detail Item'
    )
    if not fam_name:
        script.exit()
    fam_name = fam_name.strip()

    save_folder = forms.pick_folder(title='Select a folder to save the new family')
    if not save_folder:
        script.exit()

    dwg_path = export_selection_to_dwg(view, ids)

    template_path = find_detail_item_template()
    fam_doc = app.NewFamilyDocument(template_path)

    created, skipped, used_cad_fallback = import_dwg_as_detail_curves(fam_doc, dwg_path)
    if created == 0 and not used_cad_fallback:
        fam_doc.Close(False)
        _cleanup_temp_file(dwg_path)
        script.exit()

    save_path = os.path.join(save_folder, '{0}.rfa'.format(fam_name))
    save_opts = DB.SaveAsOptions()
    save_opts.OverwriteExistingFile = True
    fam_doc.SaveAs(save_path, save_opts)

    fam_doc.Close(False)

    load_family_into_project(save_path)

    place_instance_at_origin(fam_name, view)

    _cleanup_temp_file(dwg_path)

    log('**Done:** family `{0}` created with {1} detail lines '
        '({2} skipped).'.format(fam_name, created, skipped))


def _cleanup_temp_file(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as ex:
        logger.debug('Could not remove temp CAD file {0}: {1}'.format(path, ex))


if __name__ == '__main__':
    main()