# -*- coding: utf-8 -*-
"""Convert a selected line/element's curve geometry into a new Detail Item
family, let the user name it, and save it as an .rfa file.

Compatible with Revit 2023, 2024, 2025, 2026 and 2027.
The Revit API calls used here (Transaction, CurveElement, NewDetailCurve,
NewFamilyDocument, SaveAsOptions, LoadFamily) have been stable since well
before 2023, so no version branching is needed for the API itself. The only
thing that changes year-to-year is the folder name of the default family
templates, which is handled below by trying several likely paths before
falling back to a manual file picker.

Works under both the IronPython2 engine (default on older pyRevit/Revit
installs) and the CPython3 engine (used on newer pyRevit installs for
Revit 2025+) - no engine-specific syntax is used.
"""

__title__ = 'Line to\nDetail Item'
__author__ = 'Jesto Joy'
__doc__ = ('Select a detail line, model line, or any element with line '
           'geometry, then click this button to turn it into a new Detail '
           'Item family that you can name and save.')

import os

from pyrevit import revit, DB, forms, script

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application

logger = script.get_logger()


# --------------------------------------------------------------------------
# 1. Get selection and extract curve geometry
# --------------------------------------------------------------------------
def get_curves_from_selection():
    selection = revit.get_selection()
    if not selection:
        forms.alert('Select a line or an element before running this tool.',
                     exitscript=True)

    curves = []
    opt = DB.Options()
    opt.ComputeReferences = False
    opt.IncludeNonVisibleObjects = False

    for el in selection:
        try:
            # Detail lines / model lines are CurveElements
            if isinstance(el, DB.CurveElement):
                curves.append(el.GeometryCurve)
                continue

            # Anything else - pull curves out of its raw geometry
            geo = el.get_Geometry(opt)
            if not geo:
                continue
            for g in geo:
                if isinstance(g, DB.Curve):
                    curves.append(g)
                elif isinstance(g, DB.GeometryInstance):
                    inst_geo = g.GetInstanceGeometry()
                    for gi in inst_geo:
                        if isinstance(gi, DB.Curve):
                            curves.append(gi)
        except Exception as ex:
            logger.debug('Skipped element {0}: {1}'.format(el.Id, ex))

    if not curves:
        forms.alert('No line/curve geometry was found in the selection.',
                     exitscript=True)

    return curves


# --------------------------------------------------------------------------
# 2. Locate a "Detail Item.rft" template, or let the user browse for one
# --------------------------------------------------------------------------
def find_detail_item_template():
    version = app.VersionNumber  # e.g. "2023", "2024", "2025" ...
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
# 3. Build the curves in the family document at their ORIGINAL coordinates
#    (no shifting) so that placing the instance at the project origin later
#    reproduces the exact same position the source geometry had.
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
# Main
# --------------------------------------------------------------------------
def place_instance_at_origin(fam_name):
    """Find the newly loaded family's symbol and place one instance at the
    project origin. Because the detail curves were built at their original,
    un-shifted coordinates inside the family, placing the instance's origin
    at the project's 0,0,0 reproduces the exact original position/location
    the source geometry had."""
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
        doc.Create.NewFamilyInstance(DB.XYZ.Zero, symbol, view)
        t.Commit()


def main():
    curves = get_curves_from_selection()

    fam_name = forms.ask_for_string(
        default='New Detail Item',
        prompt='Enter a name for the new Detail Item family:',
        title='Line to Detail Item'
    )
    if not fam_name:
        script.exit()
    fam_name = fam_name.strip()

    template_path = find_detail_item_template()

    fam_doc = app.NewFamilyDocument(template_path)

    created, skipped = create_detail_curves(fam_doc, curves)
    if created == 0:
        fam_doc.Close(False)
        script.exit()

    save_folder = forms.pick_folder(title='Select a folder to save the new family')
    if save_folder:
        save_path = os.path.join(save_folder, '{0}.rfa'.format(fam_name))
        save_opts = DB.SaveAsOptions()
        save_opts.OverwriteExistingFile = True
        fam_doc.SaveAs(save_path, save_opts)

    # LoadFamily must be called while the target document (doc) has no open
    # transaction - it manages loading internally, so do NOT wrap this in a
    # Transaction of our own (that caused the InvalidOperationException).
    fam_doc.LoadFamily(doc)
    fam_doc.Close(False)

    place_instance_at_origin(fam_name)


if __name__ == '__main__':
    main()
