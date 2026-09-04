# -*- coding: utf-8 -*-
"""Select an imported/linked CAD file (DWG/DXF/DGN/SAT) placed in the model,
extract its line/curve geometry (walking nested block instances the same
way Explode reveals them), rebuild it as native Revit detail lines inside a
new Detail Item family, let the user name it, and save it as an .rfa file.

Compatible with Revit 2023, 2024, 2025, 2026 and 2027 (IronPython & CPython3).
"""

__title__ = 'CAD to\nDetail Item'
__author__ = 'Jesto Joy'
__doc__ = ('Select an imported/linked CAD file, then click this button to '
           'explode its line geometry into a new native Detail Item family '
           'that you can name and save.')

import os
import re

from pyrevit import revit, DB, forms, script

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application

logger = script.get_logger()


# --------------------------------------------------------------------------
# Family Load Options (handles overwrite if family already loaded)
# --------------------------------------------------------------------------
class FamilyLoadOption(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True


# --------------------------------------------------------------------------
# 1. Get selection and extract curve geometry from CAD import(s)
# --------------------------------------------------------------------------
def get_curves_from_cad_selection():
    selection = revit.get_selection()
    if not selection:
        forms.alert('Select one or more imported/linked CAD files '
                    '(DWG/DXF/DGN/SAT) before running this tool.',
                    exitscript=True)

    cad_elements = [el for el in selection if isinstance(el, DB.ImportInstance)]
    if not cad_elements:
        forms.alert('The current selection does not contain an imported or '
                    'linked CAD file. Select the CAD import in the view '
                    'and try again.', exitscript=True)

    opt = DB.Options()
    opt.ComputeReferences = False
    opt.IncludeNonVisibleObjects = False

    curves = []

    def walk(geo, depth=0):
        if geo is None or depth > 24:
            return
        for g in geo:
            try:
                if isinstance(g, DB.PolyLine):
                    coords = list(g.GetCoordinates())
                    for i in range(len(coords) - 1):
                        p0, p1 = coords[i], coords[i + 1]
                        if p0.DistanceTo(p1) > 1e-9:
                            curves.append(DB.Line.CreateBound(p0, p1))
                elif isinstance(g, DB.Curve):
                    if g.Length > 1e-9:
                        curves.append(g)
                elif isinstance(g, DB.GeometryInstance):
                    walk(g.GetInstanceGeometry(), depth + 1)
            except Exception as ex:
                logger.debug('Skipped a geometry object: {0}'.format(ex))

    for el in cad_elements:
        try:
            geo = el.get_Geometry(opt)
            walk(geo)
        except Exception as ex:
            logger.debug('Could not read geometry from {0}: {1}'.format(el.Id, ex))

    if not curves:
        forms.alert('No line/curve geometry could be extracted from the '
                    'selected CAD file(s). It may only contain fills, '
                    'text, hatches, or 3D solids with no line work.',
                    exitscript=True)

    return curves


# --------------------------------------------------------------------------
# 2. Shift geometry near the family origin
# --------------------------------------------------------------------------
def compute_reference_point(curves):
    min_x = min_y = min_z = 1e18
    max_x = max_y = max_z = -1e18
    found = False
    for c in curves:
        try:
            pts = [c.GetEndPoint(0), c.GetEndPoint(1)]
        except Exception:
            continue
        for p in pts:
            found = True
            min_x = min(min_x, p.X); max_x = max(max_x, p.X)
            min_y = min(min_y, p.Y); max_y = max(max_y, p.Y)
            min_z = min(min_z, p.Z); max_z = max(max_z, p.Z)
    if not found:
        return DB.XYZ.Zero
    return DB.XYZ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0)


def shift_curve(c, ref_point):
    try:
        transform = DB.Transform.CreateTranslation(ref_point.Negate())
        return c.CreateTransformed(transform)
    except Exception as ex:
        logger.debug('Could not shift a curve: {0}'.format(ex))
        return None


# --------------------------------------------------------------------------
# 3. Locate a "Detail Item.rft" template
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

    picked = forms.pick_file(file_ext='rft',
                             title='Select the Detail Item.rft template')
    if not picked:
        forms.alert('A Detail Item family template is required.',
                    exitscript=True)
    return picked


# --------------------------------------------------------------------------
# 4. Build curves in family document
# --------------------------------------------------------------------------
def create_detail_curves(fam_doc, curves):
    collector = DB.FilteredElementCollector(fam_doc).OfClass(DB.View)
    view = None
    for v in collector:
        if not v.IsTemplate and v.ViewType == DB.ViewType.FloorPlan:
            view = v
            break
    if not view:
        for v in collector:
            if not v.IsTemplate:
                view = v
                break
    if not view:
        forms.alert('No usable view was found in the family template.',
                    exitscript=True)

    created, skipped = 0, 0
    with DB.Transaction(fam_doc, 'Create Detail Lines') as t:
        t.Start()
        for c in curves:
            try:
                fam_doc.FamilyCreate.NewDetailCurve(view, c)
                created += 1
            except Exception as ex:
                skipped += 1
                logger.debug('Could not create detail curve: {0}'.format(ex))
        t.Commit()

    return created, skipped


# --------------------------------------------------------------------------
# 5. Place Instance
# --------------------------------------------------------------------------
def place_instance_at_point(fam_name, insertion_point):
    symbol = None
    collector = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)
    for s in collector:
        if s.Family.Name == fam_name:
            symbol = s
            break
    if not symbol:
        return

    view = uidoc.ActiveView

    with DB.Transaction(doc, 'Place Detail Item Instance') as t:
        t.Start()
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
        doc.Create.NewFamilyInstance(insertion_point, symbol, view)
        t.Commit()


# --------------------------------------------------------------------------
# 6. Safe SaveAs (handles locked files and name collisions)
# --------------------------------------------------------------------------
def safe_save_family(fam_doc, folder, base_name):
    """Saves the family, auto-incrementing the name if the file is locked by
    another process or Revit instance."""
    save_opts = DB.SaveAsOptions()
    save_opts.OverwriteExistingFile = True

    candidate_name = base_name
    counter = 1

    while counter <= 100:
        save_path = os.path.join(folder, '{0}.rfa'.format(candidate_name))
        try:
            fam_doc.SaveAs(save_path, save_opts)
            return candidate_name
        except (DB.FileAccessException, Exception) as ex:
            # If locked or access denied, try appending a numerical suffix
            candidate_name = '{0}_{1}'.format(base_name, counter)
            counter += 1

    forms.alert('Could not save family file to the chosen folder because files '
                'with that name are currently locked by Revit or another program.',
                title='Save Warning')
    return base_name


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    curves = get_curves_from_cad_selection()

    ref_point = compute_reference_point(curves)
    shifted_curves = []
    for c in curves:
        sc = shift_curve(c, ref_point)
        if sc is not None:
            shifted_curves.append(sc)

    if not shifted_curves:
        forms.alert('Could not process the extracted CAD geometry.',
                    exitscript=True)

    fam_name = forms.ask_for_string(
        default='New Detail Item',
        prompt='Enter a name for the new Detail Item family:',
        title='CAD to Detail Item'
    )
    if not fam_name:
        script.exit()

    # Sanitize family name for file system
    fam_name = re.sub(r'[\\/*?:"<>|]', '', fam_name.strip())
    if not fam_name:
        fam_name = 'Detail_Item'

    template_path = find_detail_item_template()
    fam_doc = app.NewFamilyDocument(template_path)

    try:
        created, skipped = create_detail_curves(fam_doc, shifted_curves)
        if created == 0:
            forms.alert('No detail curves could be created from the geometry.',
                        exitscript=True)

        save_folder = forms.pick_folder(title='Select a folder to save the new family')
        if save_folder:
            fam_name = safe_save_family(fam_doc, save_folder, fam_name)

        # Load into document with overload that supports overwriting
        fam_doc.LoadFamily(doc, FamilyLoadOption())

    finally:
        # ALWAYS ensure fam_doc is closed to release file locks
        try:
            fam_doc.Close(False)
        except Exception:
            pass

    place_instance_at_point(fam_name, ref_point)


if __name__ == '__main__':
    main()