# -*- coding: utf-8 -*-
"""Pick a family instance in the active view, then choose a new
category from an alphabetical list to re-assign the family to.

Workflow:
  1. Click the button.
  2. Pick a family instance (loadable family) in the open view.
  3. A list of valid categories appears (alphabetical order).
  4. Pick one, press 'Apply' -> the family is opened in the
     background, its category changed, and reloaded into the project.

Compatible with Revit 2018 through 2026+ (IronPython & CPython engines).
"""

__title__ = "Category"
__author__ = "Jesto Joy"
__doc__ = "Pick a family instance in the view, then choose a new " \
          "category (alphabetical list) to assign to its family."

# ------------------------------------------------------------------ imports
from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    FamilyInstance,
    Transaction,
    IFamilyLoadOptions,
    FamilySource,
    CategoryType,
    FailureSeverity,
    FailureProcessingResult,
)
from Autodesk.Revit.DB.Events import FailuresProcessingEventArgs
from Autodesk.Revit.UI.Events import DialogBoxShowingEventArgs
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

logger = script.get_logger()
output = script.get_output()

doc = revit.doc
uidoc = revit.uidoc
uiapp = uidoc.Application
app = doc.Application


# ------------------------------------------------------------- compat utils
def get_id_value(element_id):
    """ElementId integer value on any Revit version.
    Revit 2024+ -> .Value | Revit <=2023 -> .IntegerValue"""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


# --------------------------------------------------------- selection filter
class FamilyInstanceFilter(ISelectionFilter):
    """Only allow picking loadable (non in-place) family instances."""

    def AllowElement(self, element):
        try:
            if isinstance(element, FamilyInstance):
                fam = element.Symbol.Family
                return fam.IsEditable and not fam.IsInPlace
        except Exception:
            pass
        return False

    def AllowReference(self, reference, position):
        return False


# --------------------------------------------------------- load options stub
class OverwriteFamilyLoadOptions(IFamilyLoadOptions):
    """Always overwrite the existing family definition."""

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse,
                            source, overwriteParameterValues):
        source = FamilySource.Family
        overwriteParameterValues = True
        return True


# ----------------------------------------------- warning/dialog suppression
# Hidden warning dialogs and failure processing during EditFamily/LoadFamily
# are a major source of slowdown. Auto-dismiss them while we work.

def _on_dialog_showing(sender, args):
    """Auto-dismiss any dialog Revit tries to pop during the operation."""
    try:
        args.OverrideResult(1)   # 1 == OK / default button
    except Exception:
        pass


def _on_failures_processing(sender, args):
    """Delete warnings instantly instead of letting Revit collect/show them."""
    try:
        fa = args.GetFailuresAccessor()
        failures = fa.GetFailureMessages()
        if not failures:
            return
        for failure in failures:
            if failure.GetSeverity() == FailureSeverity.Warning:
                fa.DeleteWarning(failure)
        args.SetProcessingResult(FailureProcessingResult.Continue)
    except Exception:
        pass


class SuppressWarnings(object):
    """Context manager: silence dialogs + warnings during family reload."""

    def __enter__(self):
        try:
            uiapp.DialogBoxShowing += _on_dialog_showing
        except Exception:
            pass
        try:
            app.FailuresProcessing += _on_failures_processing
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            uiapp.DialogBoxShowing -= _on_dialog_showing
        except Exception:
            pass
        try:
            app.FailuresProcessing -= _on_failures_processing
        except Exception:
            pass
        return False


# ------------------------------------------------------------ category list
def get_valid_family_categories(target_doc):
    """Model categories a loadable family may be assigned to,
    sorted alphabetically."""
    cats = []
    for cat in target_doc.Settings.Categories:
        try:
            if cat.CategoryType != CategoryType.Model:
                continue
            if not cat.AllowsBoundParameters:
                continue
            if cat.IsTagCategory:
                continue
            cats.append(cat)
        except Exception:
            continue
    return sorted(cats, key=lambda c: c.Name.lower())   # alphabetical


class CategoryOption(forms.TemplateListItem):
    @property
    def name(self):
        return self.item.Name


# ------------------------------------------------------- core change routine
def change_family_category(project_doc, family, new_category_name):
    """Open *family* in background, switch category, reload into project."""
    fam_doc = None
    try:
        fam_doc = project_doc.EditFamily(family)
        owner_fam = fam_doc.OwnerFamily

        new_cat = None
        for cat in fam_doc.Settings.Categories:
            if cat.Name == new_category_name:
                new_cat = cat
                break
        if new_cat is None:
            return False, "Category '{}' not available for this family." \
                          .format(new_category_name)

        t = Transaction(fam_doc, "Change Family Category")
        t.Start()
        try:
            owner_fam.FamilyCategory = new_cat
            t.Commit()
        except Exception as cat_err:
            if t.HasStarted():
                t.RollBack()
            return False, "Revit rejected this category change: {}" \
                          .format(cat_err)

        fam_doc.LoadFamily(project_doc, OverwriteFamilyLoadOptions())
        return True, "OK"

    except Exception as err:
        return False, str(err)

    finally:
        if fam_doc is not None:
            try:
                fam_doc.Close(False)   # close without saving
            except Exception:
                pass


# ------------------------------------------------------------------- main
if doc.IsFamilyDocument:
    forms.alert("Run this tool in a project document, "
                "then pick a family instance in the view.",
                exitscript=True)

# ---- STEP 1: pick a family instance in the open view
try:
    reference = uidoc.Selection.PickObject(
        ObjectType.Element,
        FamilyInstanceFilter(),
        "Pick a family instance to change its category")
except OperationCanceledException:
    script.exit()
except Exception:
    # some versions raise a generic exception on ESC
    script.exit()

picked_element = doc.GetElement(reference.ElementId)
family = picked_element.Symbol.Family

# IMPORTANT: cache everything we need as plain values NOW.
# After LoadFamily() overwrites the family, the original 'family'
# element reference becomes invalid and accessing .Name / .Id on it
# throws InvalidObjectException.
family_name = family.Name
current_cat = family.FamilyCategory
current_cat_name = current_cat.Name if current_cat else "<none>"
current_cat_id_val = get_id_value(current_cat.Id) if current_cat else None

# ---- STEP 2: choose new category (alphabetical)
categories = get_valid_family_categories(doc)

# don't list the category it already has
categories = [c for c in categories
              if current_cat_id_val is None or
              get_id_value(c.Id) != current_cat_id_val]

if not categories:
    forms.alert("No valid target categories found.", exitscript=True)

picked_cat = forms.SelectFromList.show(
    [CategoryOption(c) for c in categories],
    title="'{}'  (current: {})  -  select new category"
          .format(family_name, current_cat_name),
    button_name="Apply",
    multiselect=False,
)
if not picked_cat:
    script.exit()

# cache the picked category name too (plain string, always safe)
new_cat_name = picked_cat.Name

# ---- STEP 3: apply
# NOTE: after this call the original 'family' reference may be invalid
# (LoadFamily overwrites it). Only use the cached string values below.
# SuppressWarnings auto-dismisses hidden dialogs/warnings that otherwise
# stall the EditFamily/LoadFamily round-trip.
with SuppressWarnings():
    ok, msg = change_family_category(doc, family, new_cat_name)

if ok:
    forms.alert(
        "Family '{}' changed:\n\n{}  ->  {}".format(
            family_name, current_cat_name, new_cat_name),
        title="Success")
else:
    forms.alert(
        "Could not change family '{}':\n\n{}".format(family_name, msg),
        title="Failed")
