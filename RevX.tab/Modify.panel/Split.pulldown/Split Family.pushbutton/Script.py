# -*- coding: utf-8 -*-
"""
Family Splitter (Revit 2023 - 2027)
Splits a family with multiple nested components or solid forms into individual 
loadable .rfa family instances named with 'TYPE'.
Full 'Edit Family' functionality is preserved.
"""

import clr
import os
import tempfile
import shutil
import System

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')

import Autodesk.Revit.DB as DB
from Autodesk.Revit.UI.Selection import ObjectType
from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon, DialogResult

# ---------------------------------------------------------
# Revit Document References
# ---------------------------------------------------------
app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document


class FamilyLoadOption(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        overwriteParameterValues = True
        return True


def get_concrete_forms_and_instances(fam_doc):
    """
    Collects solid geometry forms and nested family instances safely
    without triggering abstract GenericForm API exceptions.
    """
    all_elems = DB.FilteredElementCollector(fam_doc).WhereElementIsNotElementType().ToElements()
    valid_forms = []
    
    concrete_form_types = (
        DB.Extrusion,
        DB.Sweep,
        DB.Revolution,
        DB.Blend,
        DB.SweptBlend,
        DB.FreeFormElement,
        DB.FamilyInstance
    )
    
    for elem in all_elems:
        if isinstance(elem, concrete_form_types):
            # Exclude non-solid or void forms if applicable
            if hasattr(elem, "IsSolid") and not elem.IsSolid:
                continue
            valid_forms.append(elem)
            
    return valid_forms


def split_family():
    # 1. Pick Family Instance
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, "Select a Family Instance to split:")
        parent_inst = doc.GetElement(ref.ElementId)
    except Exception:
        return  # User canceled selection

    if not isinstance(parent_inst, DB.FamilyInstance):
        return

    # 2. Ask user for deletion confirmation
    dialog_res = MessageBox.Show(
        "Do you want to delete the original family after splitting?",
        "Split Family",
        MessageBoxButtons.YesNoCancel,
        MessageBoxIcon.Question
    )
    if dialog_res == DialogResult.Cancel:
        return
    delete_original = (dialog_res == DialogResult.Yes)

    # 3. Retrieve Family Definition and Placement Context
    symbol = parent_inst.Symbol
    parent_family = symbol.Family
    parent_fam_name = parent_family.Name
    clean_fam_name = "".join([c for c in parent_fam_name if c.isalnum() or c in (' ', '_', '-')]).strip()

    # Get Parent Placement Transform and Level
    parent_tf = parent_inst.GetTransform()
    parent_origin = parent_tf.Origin
    parent_rotation = 0.0
    if isinstance(parent_inst.Location, DB.LocationPoint):
        parent_rotation = parent_inst.Location.Rotation

    level_id = parent_inst.LevelId
    if level_id == DB.ElementId.InvalidElementId:
        level_id = doc.ActiveView.GenLevel.Id if doc.ActiveView.GenLevel else DB.ElementId.InvalidElementId

    level = doc.GetElement(level_id) if level_id != DB.ElementId.InvalidElementId else None

    # 4. Open Family Document in Background to Inspect Elements
    temp_dir = tempfile.mkdtemp()
    try:
        fam_doc = doc.EditFamily(parent_family)
        forms_and_instances = get_concrete_forms_and_instances(fam_doc)
        total_items = len(forms_and_instances)

        if total_items <= 1:
            fam_doc.Close(False)
            return

        # Save a master .rfa copy to the temp folder
        master_rfa_path = os.path.join(temp_dir, "Master_{0}.rfa".format(clean_fam_name))
        save_opt = DB.SaveAsOptions()
        save_opt.OverwriteExistingFile = True
        fam_doc.SaveAs(master_rfa_path, save_opt)
        fam_doc.Close(False)

        tg = DB.TransactionGroup(doc, "Split Family to Individual Types")
        tg.Start()

        created_instances = []

        # 5. Extract Each Form/Type into its own .rfa Family
        for i in range(total_items):
            type_number = i + 1
            # Naming convention: FamilyName_TYPE_#
            type_family_name = "{0}_TYPE_{1}".format(clean_fam_name, type_number)
            type_symbol_name = "TYPE {0}".format(type_number)
            type_rfa_path = os.path.join(temp_dir, "{0}.rfa".format(type_family_name))

            # Open fresh master copy
            item_doc = app.OpenDocumentFile(master_rfa_path)
            item_elements = get_concrete_forms_and_instances(item_doc)

            # Transaction inside the Family Document: isolate current form & rename type
            t_fam = DB.Transaction(item_doc, "Isolate Type")
            t_fam.Start()
            
            # Delete all forms except current index
            for idx, form_elem in enumerate(item_elements):
                if idx != i:
                    try:
                        item_doc.Delete(form_elem.Id)
                    except Exception:
                        pass

            # Update / Create Family Type name inside the family
            fam_mgr = item_doc.FamilyManager
            try:
                if fam_mgr.CurrentType:
                    fam_mgr.RenameCurrentType(type_symbol_name)
                else:
                    fam_mgr.NewType(type_symbol_name)
            except Exception:
                pass

            t_fam.Commit()

            item_doc.SaveAs(type_rfa_path, save_opt)
            item_doc.Close(False)

            # 6. Load New Type Family into Project
            t_load = DB.Transaction(doc, "Load Family {0}".format(type_family_name))
            t_load.Start()
            
            ref_fam = clr.Reference[DB.Family]()
            loaded = doc.LoadFamily(type_rfa_path, FamilyLoadOption(), ref_fam)
            loaded_fam = ref_fam.Value if loaded else None

            if not loaded_fam:
                # Find if already registered
                collector = DB.FilteredElementCollector(doc).OfClass(DB.Family)
                for f in collector:
                    if f.Name == type_family_name:
                        loaded_fam = f
                        break

            if loaded_fam:
                sym_id = list(loaded_fam.GetFamilySymbolIds())[0]
                new_sym = doc.GetElement(sym_id)
                if not new_sym.IsActive:
                    new_sym.Activate()
                    doc.Regenerate()

                # Place new instance at exact original location
                if level:
                    new_inst = doc.Create.NewFamilyInstance(
                        parent_origin, 
                        new_sym, 
                        level, 
                        DB.Structure.StructuralType.NonStructural
                    )
                else:
                    new_inst = doc.Create.NewFamilyInstance(
                        parent_origin, 
                        new_sym, 
                        DB.Structure.StructuralType.NonStructural
                    )

                # Match rotation
                if parent_rotation != 0.0:
                    axis = DB.Line.CreateBound(parent_origin, parent_origin + DB.XYZ.BasisZ)
                    DB.ElementTransformUtils.RotateElement(doc, new_inst.Id, axis, parent_rotation)

                # Match elevation / offset parameters
                for param_id in (DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
                                 DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM):
                    src_p = parent_inst.get_Parameter(param_id)
                    dst_p = new_inst.get_Parameter(param_id)
                    if src_p and dst_p and not dst_p.IsReadOnly:
                        dst_p.Set(src_p.AsDouble())

                created_instances.append(new_inst)

            t_load.Commit()

        # 7. Delete Original Family if requested
        if delete_original and created_instances:
            t_del = DB.Transaction(doc, "Delete Original Family")
            t_del.Start()
            doc.Delete(parent_inst.Id)
            t_del.Commit()

        tg.Assimilate()
        uidoc.RefreshActiveView()

    finally:
        # Cleanup temporary directory
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


# Execute
split_family()