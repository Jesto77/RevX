# -*- coding: utf-8 -*-
"""Select an imported/linked CAD file (DWG/DXF/DGN/SAT) placed in the model,
extract its line/curve geometry (walking nested block instances the same
way Explode reveals them), rebuild it as native Revit detail lines inside a
new Detail Item family, let the user name it, and save it as an .rfa file.

This does NOT call Revit's UI "Explode" command (there is no public API for
it) - instead it walks the CAD import's own geometry tree directly, which
produces the same practical result: every line/arc/polyline segment inside
it, at every nesting level, converted into native Curve objects.

Compatible with Revit 2023, 2024, 2025, 2026 and 2027. The API calls used
here (Options, GeometryInstance, PolyLine, Transform, FamilyCreate,
NewFamilyDocument, SaveAsOptions, LoadFamily) have been stable since well
before 2023, so no version branching is needed. Works under both the
IronPython2 engine and the CPython3 engine - no engine-specific syntax.

COORDINATE HANDLING: CAD files are frequently placed at large real-world
coordinates (state-plane, survey points, etc.). Building family geometry
that far from the family's own origin risks Revit's "far from origin"
instability, so all curves are shifted to be centered near the family
origin before being built, then the final instance is placed back at that
same reference point in the project - reproducing the original position
without ever putting far-away geometry inside the family itself.
"""

__title__ = 'CAD to\nDetail Item'
__author__ = 'Jesto Joy'
__doc__ = ('Select an imported/linked CAD file, then click this button to '
           'explode its line geometry into a new native Detail Item family '
           'that you can name and save.')

import os

from pyrevit import revit, DB, forms, script

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application

logger = script.get_logger()


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
        # CAD imports commonly nest several levels of block-reference
        # GeometryInstances - walk all of them, not just the top level.
        if geo is None or depth > 24:
            return
        for g in geo:
            try:
                if isinstance(g, DB.PolyLine):
                    # Most DWG/DXF polylines come through as PolyLine, NOT
                    # as a Curve subtype - break each into Line segments.
                    coords = list(g.GetCoordinates())
                    for i in range(len(coords) - 1):
                        p0, p1 = coords[i], coords[i + 1]
                        if p0.DistanceTo(p1) > 1e-9:
                            curves.append(DB.Line.CreateBound(p0, p1))
                elif isinstance(g, DB.Curve):
                    if g.Length > 1e-9:
                        curves.append(g)
                elif isinstance(g, DB.GeometryInstance):
                    # GetInstanceGeometry() returns geometry already
                    # transformed into the outer coordinate system, so
                    # curves collected at any depth are directly usable.
                    walk(g.GetInstanceGeometry(), depth + 1)
                # Solid / Mesh / Point geometry intentionally skipped -
                # a Detail Item only needs 2D line work.
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
# 2. Shift geometry near the family origin (see COORDINATE HANDLING above)
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
    """Returns a new curve translated by -ref_point, or None if this
    particular curve can't be transformed (degenerate/unsupported type) -
    callers should skip those rather than fail the whole conversion."""
    try:
        transform = DB.Transform.CreateTranslation(ref_point.Negate())
        return c.CreateTransformed(transform)
    except Exception as ex:
        logger.debug('Could not shift a curve: {0}'.format(ex))
        return None


# --------------------------------------------------------------------------
# 3. Locate a "Detail Item.rft" template, or let the user browse for one
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
# 4. Build the (already-shifted) curves in the family document
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
def place_instance_at_point(fam_name, insertion_point):
    """Find the newly loaded family's symbol and place one instance at
    insertion_point (the SAME reference point the geometry was shifted by
    before being built) - reproducing the CAD content's original position
    in the project, without ever putting far-from-origin geometry inside
    the family itself."""
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
    fam_name = fam_name.strip()

    template_path = find_detail_item_template()

    fam_doc = app.NewFamilyDocument(template_path)

    created, skipped = create_detail_curves(fam_doc, shifted_curves)
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

    place_instance_at_point(fam_name, ref_point)


if __name__ == '__main__':
    main()