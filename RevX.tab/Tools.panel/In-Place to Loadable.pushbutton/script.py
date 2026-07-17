# -*- coding: utf-8 -*-
"""
Convert In-Place Family to Loadable Family
========================================
Picks an in-place family instance, builds a loadable .rfa from its geometry,
saves it to a temp folder, loads it back, and places it in the same location.

Uses FreeFormElement geometry extraction (Revit 2018+).

Compatible: Revit 2018-2026+  |  IronPython 2.7 / CPython 3.x (pyRevit)

Changelog vs prior version
--------------------------
* ElementId.IntegerValue  → version-safe get_element_id_value() helper
  (IntegerValue removed / throws on 64-bit IDs in Revit 2024+/2026)
* DisplayUnitSystem       → version-safe is_metric_project() with ForgeTypeId
  fallback (DisplayUnitSystem deprecated Revit 2022, gone in some builds)
* OpenDocumentFile(str)   → wrapped in try/except; ModelPath overload tried first
* IFamilyLoadOptions      → extra guard added for CPython 3 / newer pyRevit
* FamilyManager.Types     → safe iteration via list() conversion
* ElementId(int)          → never constructed directly; all ids come from API
* rename_family_type      → safe iteration + commit guard
"""

__title__ = "In-Place to Loadable"
__author__ = "Jesto"
__doc__ = (
    "Pick an in-place family. Converts it to a loadable .rfa "
    "(same category & name), saves to temp, and places it "
    "in the exact same location."
)

import os
import re
import sys
import tempfile
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    FamilyInstance,
    FreeFormElement,
    Options,
    Solid,
    GeometryInstance,
    ViewDetailLevel,
    Transaction,
    SaveAsOptions,
    XYZ,
    ElementId,
    LocationPoint,
    LocationCurve,
    StorageType,
    IFamilyLoadOptions,
)
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms, revit, script

doc   = revit.doc
uidoc = revit.uidoc
app   = revit.doc.Application

TEMP_ROOT      = os.path.join(tempfile.gettempdir(), "pyRevit_InPlaceConvert")
MIN_SOLID_VOLUME = 1e-9


# =============================================================================
# VERSION-SAFE HELPERS
# =============================================================================

def get_element_id_value(eid):
    """
    Return the numeric value of an ElementId as a plain Python int.

    Revit 2024 deprecated  ElementId.IntegerValue (Int32) in favour of
    ElementId.Value (Int64).  Both are tried so the script works on
    Revit 2018-2026+.
    """
    if eid is None:
        return -1
    # Revit 2024+ preferred property (Int64)
    try:
        return int(eid.Value)
    except AttributeError:
        pass
    # Legacy / Revit ≤ 2023 (Int32)
    try:
        return int(eid.IntegerValue)
    except AttributeError:
        pass
    return -1


def is_metric_project():
    """
    Detect whether the project uses metric units.

    DisplayUnitSystem was deprecated in Revit 2022 and removed in some
    later builds.  We fall back to inspecting a length unit ForgeTypeId
    when the old enum is unavailable.
    """
    # --- Try the legacy enum first (Revit 2018-2025) -------------------------
    try:
        return doc.DisplayUnitSystem == DB.DisplayUnitSystem.METRIC
    except Exception:
        pass

    # --- ForgeTypeId / UnitUtils fallback (Revit 2022+) ----------------------
    try:
        length_spec = DB.SpecTypeId.Length
        disp_units  = doc.GetUnits().GetFormatOptions(length_spec).GetUnitTypeId()
        metric_ids  = {
            DB.UnitTypeId.Meters,
            DB.UnitTypeId.Centimeters,
            DB.UnitTypeId.Millimeters,
        }
        return disp_units in metric_ids
    except Exception:
        pass

    # Default: assume metric
    return True


# =============================================================================
# SELECTION FILTER
# =============================================================================

class InPlaceFamilyFilter(ISelectionFilter):
    def AllowElement(self, element):
        if not isinstance(element, FamilyInstance):
            return False
        try:
            return element.Symbol.Family.IsInPlace
        except Exception:
            return False

    def AllowReference(self, reference, point):
        return False


# =============================================================================
# FAMILY LOAD OPTIONS  —  fixed for IronPython bool-ref quirks + CPython 3
# =============================================================================

class OverwriteFamilyLoadOptions(IFamilyLoadOptions):
    """
    Always overwrite.  Handles the IronPython bool-by-ref pattern in
    multiple ways so it works across IronPython 2.7, CPython 3, and
    different Revit/pyRevit host versions.
    """

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        for setter in (
            lambda: setattr(overwriteParameterValues, "Value", True),
            lambda: overwriteParameterValues.__setitem__(0, True),
            lambda: overwriteParameterValues.set_Item(0, True),
        ):
            try:
                setter()
                break
            except Exception:
                pass
        return True

    def OnSharedFamilyFound(
        self, sharedFamily, familyInUse, source, overwriteParameterValues
    ):
        for setter in (
            lambda: setattr(overwriteParameterValues, "Value", True),
            lambda: overwriteParameterValues.__setitem__(0, True),
            lambda: overwriteParameterValues.set_Item(0, True),
        ):
            try:
                setter()
                break
            except Exception:
                pass
        return True


# =============================================================================
# UTILITIES
# =============================================================================

def sanitize_filename(name):
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name)
    return name or "InPlaceFamily"


def _param_string(element, bip):
    if element is None:
        return None
    try:
        p = element.get_Parameter(bip)
        if p is None or not p.HasValue:
            return None
        v = p.AsString()
        if v and v.strip():
            return v.strip()
    except Exception:
        pass
    return None


def _element_name(element):
    if element is None:
        return None
    try:
        n = element.Name
        if n and str(n).strip():
            return str(n).strip()
    except Exception:
        pass
    return None


def get_family_name(family, instance=None):
    for candidate in [
        lambda: _element_name(family),
        lambda: _param_string(family,   DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME),
        lambda: _element_name(instance),
        lambda: _param_string(instance, DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME),
    ]:
        try:
            n = candidate()
            if n:
                return n
        except Exception:
            pass
    return "InPlaceFamily"


def get_type_name(symbol, instance=None):
    for candidate in [
        lambda: _element_name(symbol),
        lambda: _param_string(symbol,   DB.BuiltInParameter.SYMBOL_NAME_PARAM),
        lambda: _param_string(symbol,   DB.BuiltInParameter.ALL_MODEL_TYPE_NAME),
        lambda: _param_string(instance, DB.BuiltInParameter.ELEM_TYPE_PARAM),
        lambda: _param_string(instance, DB.BuiltInParameter.SYMBOL_NAME_PARAM),
        lambda: _param_string(instance, DB.BuiltInParameter.ALL_MODEL_TYPE_NAME),
        lambda: get_family_name(symbol.Family if symbol else None, instance),
    ]:
        try:
            n = candidate()
            if n:
                return n
        except Exception:
            pass
    return "Type 1"


def get_category_name(category):
    try:
        n = category.Name
        if n and str(n).strip():
            return str(n).strip()
    except Exception:
        pass
    return "Generic Models"


def ensure_temp_folder():
    if not os.path.isdir(TEMP_ROOT):
        os.makedirs(TEMP_ROOT)


def get_rfa_save_info(family_name):
    ensure_temp_folder()
    base = sanitize_filename(family_name)
    loadable_name = base + "_Loadable"
    return os.path.join(TEMP_ROOT, loadable_name + ".rfa"), loadable_name


def find_family_template(category):
    template_root = app.FamilyTemplatePath
    if not template_root or not os.path.isdir(template_root):
        return None, "Family template folder not found:\n{}".format(template_root)

    cat_name = get_category_name(category)
    metric   = is_metric_project()

    candidates = [cat_name]
    if cat_name.endswith("s"):
        candidates.append(cat_name[:-1])
    else:
        candidates.append(cat_name + "s")

    found = []
    for dirpath, _, filenames in os.walk(template_root):
        for fname in filenames:
            if not fname.lower().endswith(".rft"):
                continue
            lower = fname.lower()
            if metric and "metric" not in lower:
                continue
            if not metric and lower.startswith("metric "):
                continue
            for cand in candidates:
                stem = os.path.splitext(fname)[0].lower()
                if cand.lower() in stem or stem in cand.lower():
                    found.append(os.path.join(dirpath, fname))
                    break

    if found:
        found.sort(key=lambda p: (len(os.path.basename(p)), len(p)))
        return found[0], None

    # Generic Model fallback
    generic_names = ["metric generic model", "generic model"]
    for dirpath, _, filenames in os.walk(template_root):
        for fname in filenames:
            lower = fname.lower()
            if not lower.endswith(".rft"):
                continue
            for g in generic_names:
                if g in lower:
                    if metric     and "metric" not in lower:
                        continue
                    if not metric and lower.startswith("metric "):
                        continue
                    return os.path.join(dirpath, fname), (
                        "No template found for '{}'. Using Generic Model.".format(cat_name)
                    )

    return None, (
        "Could not find a family template for '{}'.\n"
        "Check Revit Options > File Locations.".format(cat_name)
    )


def collect_solids(element):
    opts = Options()
    opts.DetailLevel            = ViewDetailLevel.Fine
    opts.IncludeNonVisibleObjects = True

    solids = []
    try:
        geom = element.get_Geometry(opts)
    except Exception:
        return solids

    if geom is None:
        return solids

    def add_solid(s):
        try:
            if s and s.Faces.Size > 0 and s.Volume > MIN_SOLID_VOLUME:
                solids.append(s)
        except Exception:
            pass

    def walk(geom_elem, depth=0):
        if geom_elem is None or depth > 8:
            return
        for go in geom_elem:
            if isinstance(go, Solid):
                add_solid(go)
            elif isinstance(go, GeometryInstance):
                try:
                    for igo in go.GetInstanceGeometry():
                        if isinstance(igo, Solid):
                            add_solid(igo)
                        elif isinstance(igo, GeometryInstance) and depth < 8:
                            walk([igo], depth + 1)
                except Exception:
                    pass
                try:
                    for sgo in go.GetSymbolGeometry():
                        if isinstance(sgo, Solid):
                            tr = go.Transform
                            add_solid(DB.SolidUtils.CreateTransformed(sgo, tr))
                except Exception:
                    pass

    walk(geom)
    return solids


def get_instance_transform(fi):
    try:
        return fi.GetTransform()
    except Exception:
        pass

    loc = fi.Location
    if isinstance(loc, LocationPoint):
        pt    = loc.Point
        angle = 0.0
        try:
            angle = loc.Rotation
        except Exception:
            pass
        if abs(angle) > 1e-9:
            return DB.Transform.CreateRotationAtPoint(XYZ.BasisZ, angle, pt)
        return DB.Transform.CreateTranslation(pt)

    if isinstance(loc, LocationCurve):
        try:
            mid = loc.Curve.Evaluate(0.5, True)
            return DB.Transform.CreateTranslation(mid)
        except Exception:
            pass

    try:
        bb = fi.get_BoundingBox(None)
        center = XYZ(
            (bb.Min.X + bb.Max.X) * 0.5,
            (bb.Min.Y + bb.Max.Y) * 0.5,
            (bb.Min.Z + bb.Max.Z) * 0.5,
        )
        return DB.Transform.CreateTranslation(center)
    except Exception:
        return DB.Transform.Identity


def solids_to_family_space(solids, instance_transform):
    try:
        inverse = instance_transform.Inverse
    except Exception:
        inverse = DB.Transform.Identity

    local = []
    for s in solids:
        try:
            local.append(DB.SolidUtils.CreateTransformed(s, inverse))
        except Exception:
            pass
    return local


def copy_parameter_value(src_param, tgt_param):
    if src_param is None or tgt_param is None:
        return
    if src_param.IsReadOnly or tgt_param.IsReadOnly:
        return
    if src_param.StorageType != tgt_param.StorageType:
        return
    try:
        st = src_param.StorageType
        if   st == StorageType.Double:    tgt_param.Set(src_param.AsDouble())
        elif st == StorageType.Integer:   tgt_param.Set(src_param.AsInteger())
        elif st == StorageType.String:    tgt_param.Set(src_param.AsString())
        elif st == StorageType.ElementId: tgt_param.Set(src_param.AsElementId())
    except Exception:
        pass


def copy_instance_parameters(source, target):
    skip = {
        "Type", "Type Id", "Family", "Family Name", "Family and Type",
        "Host", "Level", "Moves With Nearby Elements",
    }
    for sp in source.Parameters:
        try:
            name = sp.Definition.Name
            if name in skip or sp.IsReadOnly:
                continue
            tp = target.LookupParameter(name)
            if tp is not None:
                copy_parameter_value(sp, tp)
        except Exception:
            pass


def get_level(fi):
    try:
        lvl_id = fi.LevelId
        if lvl_id and get_element_id_value(lvl_id) != get_element_id_value(ElementId.InvalidElementId):
            return doc.GetElement(lvl_id)
    except Exception:
        pass
    return None


def rename_family_type(fam_doc, type_name):
    """
    Rename the first (and usually only) type in the freshly-created family.

    FamilyManager.Types returns a FamilyTypeSet whose iteration behaviour
    differs between IronPython 2.7 and CPython 3.  Converting to list()
    first is the safest approach across all versions.
    """
    try:
        fm = fam_doc.FamilyManager
        all_types = list(fm.Types)          # safe across IP2/CP3
        if not all_types:
            return
        fm.CurrentType = all_types[0]
        fm.RenameCurrentType(type_name)
    except Exception:
        pass


def set_family_category(fam_doc, target_category):
    """
    Force the family document's category to match the original in-place
    family's category.

    This is the key fix for categories like Hardscape, Site, Entourage, etc.
    that have no matching .rft template — they open as Generic Models and
    need their category reassigned inside a transaction on the family doc.

    Strategy:
      1. Try fam_doc.OwnerFamily.FamilyCategory = <category from Settings>
         This is the cleanest API (Revit 2015+).
      2. Fall back to iterating fam_doc.Settings.Categories to match by name.
    Returns a warning string if reassignment failed, or None on success.
    """
    target_name = get_category_name(target_category)

    # Check if the category is already correct — skip if so
    try:
        current = fam_doc.OwnerFamily.FamilyCategory
        if current is not None and get_category_name(current) == target_name:
            return None  # already correct, nothing to do
    except Exception:
        pass

    # Build a lookup of all family-assignable categories in the family doc
    def find_category_in_fam_doc():
        try:
            cats = fam_doc.Settings.Categories
            # Direct name match
            for cat in cats:
                try:
                    if cat.Name == target_name:
                        return cat
                except Exception:
                    pass
            # Case-insensitive fallback
            tl = target_name.lower()
            for cat in cats:
                try:
                    if cat.Name.lower() == tl:
                        return cat
                except Exception:
                    pass
        except Exception:
            pass
        return None

    matched_cat = find_category_in_fam_doc()
    if matched_cat is None:
        return (
            "Could not find category '{}' in the family template's category list. "
            "The family was saved as Generic Models — change the category manually "
            "in the Family Editor.".format(target_name)
        )

    try:
        fam_doc.OwnerFamily.FamilyCategory = matched_cat
        return None
    except Exception as ex:
        return (
            "Category '{}' found but could not be assigned: {}. "
            "Change the category manually in the Family Editor.".format(target_name, ex)
        )


def build_loadable_family(fi, rfa_path, type_name, category):
    """
    Create, populate, and save a loadable .rfa outside any project transaction.
    Returns (rfa_path, warning_message).

    The category is always force-assigned inside the family document after
    the template is opened, so categories with no matching .rft (e.g.
    Hardscape, Site, Entourage) are correctly tagged instead of falling
    back to Generic Models.
    """
    template_path, template_warn = find_family_template(category)
    if template_path is None:
        raise Exception(template_warn)

    world_solids = collect_solids(fi)
    if not world_solids:
        raise Exception(
            "No solid geometry found on the in-place family.\n\n"
            "The family may be empty, view-specific, or use geometry "
            "the API cannot extract."
        )

    instance_tr  = get_instance_transform(fi)
    local_solids = solids_to_family_space(world_solids, instance_tr)
    if not local_solids:
        raise Exception("Failed to transform geometry into family coordinates.")

    fam_doc      = None
    category_warn = None
    try:
        fam_doc = app.NewFamilyDocument(template_path)
        if fam_doc is None:
            raise Exception("Revit could not open the family template.")

        with Transaction(fam_doc, "Build FreeForm Geometry") as ft:
            ft.Start()

            # ---- Force the correct category BEFORE adding geometry ----------
            # This must happen inside a transaction on the family document.
            # It is the fix for Hardscape and any other category whose name
            # doesn't match an .rft file, causing the Generic Model fallback.
            category_warn = set_family_category(fam_doc, category)

            created = 0
            for solid in local_solids:
                try:
                    FreeFormElement.Create(fam_doc, solid)
                    created += 1
                except Exception:
                    pass
            if created == 0:
                raise Exception(
                    "Could not create FreeForm geometry in the new family."
                )
            rename_family_type(fam_doc, type_name)
            fam_doc.Regenerate()
            ft.Commit()

        save_opts = SaveAsOptions()
        save_opts.OverwriteExistingFile = True
        fam_doc.SaveAs(rfa_path, save_opts)
    finally:
        if fam_doc is not None:
            try:
                fam_doc.Close(False)
            except Exception:
                pass

    if not os.path.isfile(rfa_path):
        raise Exception("Family file was not created:\n{}".format(rfa_path))

    # Merge any template-search warning with the category-assignment warning
    warnings = " | ".join(
        w for w in [template_warn, category_warn] if w
    ) or None
    return rfa_path, warnings


# =============================================================================
# FAMILY LOADING
# =============================================================================

def _all_loadable_families():
    """Return {name.lower(): Family} for all non-in-place families in doc."""
    result = {}
    for f in FilteredElementCollector(doc).OfClass(DB.Family):
        try:
            if not f.IsInPlace:
                n = get_family_name(f)
                if n:
                    result[n.strip().lower()] = f
        except Exception:
            pass
    return result


def _open_fam_doc_safe(rfa_path):
    """
    Open an .rfa as a family document using whichever overload works.

    Revit 2026 removed the plain OpenDocumentFile(string) overload.
    The ModelPath-based overload is tried first (works 2020+), then the
    legacy string overload as a fallback for older builds.
    """
    # Attempt 1: ModelPath overload (preferred, Revit 2020+)
    try:
        model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(rfa_path)
        open_opts  = DB.OpenOptions()
        return app.OpenDocumentFile(model_path, open_opts)
    except Exception:
        pass

    # Attempt 2: Plain string overload (Revit 2018-2025, removed in some 2026 builds)
    try:
        return app.OpenDocumentFile(rfa_path)
    except Exception:
        pass

    return None


def _load_family_into_doc(rfa_path):
    """
    Core loader — tries every IronPython / CPython-compatible LoadFamily
    signature.  Returns the loaded DB.Family on success, raises on failure.
    """
    rfa_path    = os.path.abspath(rfa_path)
    if not os.path.isfile(rfa_path):
        raise Exception("RFA file not found:\n{}".format(rfa_path))

    loaded_name = os.path.splitext(os.path.basename(rfa_path))[0]
    opts        = OverwriteFamilyLoadOptions()
    errors      = []

    # ---- Attempt 1: doc.LoadFamily(path, opts) — IronPython tuple return ----
    try:
        result = doc.LoadFamily(rfa_path, opts)
        if isinstance(result, tuple):
            success = bool(result[0]) if len(result) > 0 else False
            fam     = result[1]        if len(result) > 1 else None
        else:
            success = bool(result)
            fam     = None

        if success or fam is not None:
            if fam is not None and not fam.IsInPlace:
                return fam
            hit = _all_loadable_families().get(loaded_name.lower())
            if hit is not None:
                return hit
    except Exception as ex:
        errors.append("LoadFamily(path, opts): {}".format(ex))

    # ---- Attempt 2: doc.LoadFamily(path) — no options arg ------------------
    try:
        result = doc.LoadFamily(rfa_path)
        if isinstance(result, tuple):
            success = bool(result[0]) if len(result) > 0 else False
            fam     = result[1]        if len(result) > 1 else None
        else:
            success = bool(result)
            fam     = None

        if success or fam is not None:
            if fam is not None and not fam.IsInPlace:
                return fam
            hit = _all_loadable_families().get(loaded_name.lower())
            if hit is not None:
                return hit
    except Exception as ex:
        errors.append("LoadFamily(path): {}".format(ex))

    # ---- Attempt 3: open the RFA then push via family doc ------------------
    fam_doc = None
    try:
        fam_doc = _open_fam_doc_safe(rfa_path)
        if fam_doc is not None:
            result = fam_doc.LoadFamily(doc, opts)
            if isinstance(result, DB.Family):
                fam = result
            elif isinstance(result, tuple):
                fam = result[1] if len(result) > 1 else None
            else:
                fam = None

            if fam is not None and not fam.IsInPlace:
                return fam
            hit = _all_loadable_families().get(loaded_name.lower())
            if hit is not None:
                return hit
    except Exception as ex:
        errors.append("OpenDoc+LoadFamily: {}".format(ex))
    finally:
        if fam_doc is not None:
            try:
                fam_doc.Close(False)
            except Exception:
                pass

    # ---- Final check: maybe it loaded despite errors -----------------------
    hit = _all_loadable_families().get(loaded_name.lower())
    if hit is not None:
        return hit

    raise Exception(
        "All LoadFamily strategies failed.\n"
        "File: {}\n\nErrors:\n{}".format(
            rfa_path, "\n".join(errors) if errors else "None"
        )
    )


def load_and_get_symbol(rfa_path, type_name):
    """Load .rfa and return (Family, FamilySymbol)."""
    family = _load_family_into_doc(rfa_path)

    if family.IsInPlace:
        raise Exception("Loaded family is in-place — internal error.")

    symbol = None
    for sym_id in family.GetFamilySymbolIds():
        sym = doc.GetElement(sym_id)
        if sym is None:
            continue
        if get_type_name(sym) == type_name:
            symbol = sym
            break

    # Fall back to first symbol if named match not found
    if symbol is None:
        for sym_id in family.GetFamilySymbolIds():
            sym = doc.GetElement(sym_id)
            if sym:
                symbol = sym
                break

    if symbol is None:
        raise Exception(
            "Loaded family '{}' has no symbols/types.".format(get_family_name(family))
        )

    # NOTE: do NOT activate symbol here — this function is called inside a
    # transaction and activate_symbol opens its own transaction (nested = error).
    return family, symbol


# =============================================================================
# PLACEMENT
# =============================================================================

class PlacementInfo(object):
    def __init__(self, fi):
        self.level    = get_level(fi)
        self.host     = None
        try:
            h = fi.Host
            if h is not None and get_element_id_value(h.Id) != get_element_id_value(ElementId.InvalidElementId):
                self.host = h
        except Exception:
            pass

        self.point        = None
        self.rotation     = 0.0
        self.curve        = None
        self.fallback_point = None
        self.face_ref     = None
        self.face_point   = None

        loc = fi.Location
        if isinstance(loc, LocationPoint):
            self.point = loc.Point
            try:
                self.rotation = loc.Rotation
            except Exception:
                self.rotation = 0.0
        elif isinstance(loc, LocationCurve):
            self.curve = loc.Curve

        try:
            bb = fi.get_BoundingBox(None)
            if bb is not None:
                self.fallback_point = XYZ(
                    (bb.Min.X + bb.Max.X) * 0.5,
                    (bb.Min.Y + bb.Max.Y) * 0.5,
                    (bb.Min.Z + bb.Max.Z) * 0.5,
                )
        except Exception:
            pass

        try:
            ref = fi.HostFace
            if ref is not None:
                self.face_ref   = ref
                self.face_point = self.point or self.fallback_point
        except Exception:
            pass

        self.instance = fi


def _apply_rotation(new_fi, info):
    if not info.rotation or abs(info.rotation) < 1e-9:
        return
    pt = info.point or info.fallback_point
    if pt is None:
        return
    try:
        axis = DB.Line.CreateBound(pt, XYZ(pt.X, pt.Y, pt.Z + 1.0))
        DB.ElementTransformUtils.RotateElement(doc, new_fi.Id, axis, info.rotation)
    except Exception:
        pass


def place_family_instance(info, symbol):
    """
    Try every placement strategy in order.  Collects error text so that
    if everything fails the message explains *why*.
    """
    level  = info.level
    new_fi = None
    errors = []

    # S1: face-hosted
    if info.face_ref is not None and info.face_point is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.face_ref, info.face_point, XYZ.BasisX, symbol
            )
        except Exception as ex:
            errors.append("S1 face-hosted: {}".format(ex))

    # S2: host + point + level
    if new_fi is None and info.host is not None and info.point is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.point, symbol, info.host, level, StructuralType.NonStructural
            )
        except Exception as ex:
            errors.append("S2 host+point+level: {}".format(ex))

    # S3: host + point
    if new_fi is None and info.host is not None and info.point is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.point, symbol, info.host, StructuralType.NonStructural
            )
        except Exception as ex:
            errors.append("S3 host+point: {}".format(ex))

    # S4: point + level
    if new_fi is None and info.point is not None and level is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.point, symbol, level, StructuralType.NonStructural
            )
        except Exception as ex:
            errors.append("S4 point+level: {}".format(ex))

    # S5: point only
    if new_fi is None and info.point is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.point, symbol, StructuralType.NonStructural
            )
        except Exception as ex:
            errors.append("S5 point only: {}".format(ex))

    # S6: curve + level
    if new_fi is None and info.curve is not None and level is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.curve, symbol, level, StructuralType.NonStructural
            )
        except Exception as ex:
            errors.append("S6 curve+level: {}".format(ex))

    # S7: curve only
    if new_fi is None and info.curve is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.curve, symbol, StructuralType.NonStructural
            )
        except Exception as ex:
            errors.append("S7 curve only: {}".format(ex))

    # S8: fallback centre + level
    if new_fi is None and info.fallback_point is not None and level is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.fallback_point, symbol, level, StructuralType.NonStructural
            )
        except Exception as ex:
            errors.append("S8 fallback+level: {}".format(ex))

    # S9: fallback centre only
    if new_fi is None and info.fallback_point is not None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                info.fallback_point, symbol, StructuralType.NonStructural
            )
        except Exception as ex:
            errors.append("S9 fallback only: {}".format(ex))

    # S10: absolute last resort — origin
    if new_fi is None:
        try:
            new_fi = doc.Create.NewFamilyInstance(
                XYZ(0, 0, 0), symbol, StructuralType.NonStructural
            )
            errors.append("S10 WARNING: placed at origin (0,0,0) — move manually")
        except Exception as ex:
            errors.append("S10 origin: {}".format(ex))

    if new_fi is None:
        raise Exception(
            "All placement strategies failed.\n\n" + "\n".join(errors)
        )

    _apply_rotation(new_fi, info)
    copy_instance_parameters(info.instance, new_fi)

    warnings = [e for e in errors if "WARNING" in e]
    if warnings:
        new_fi._placement_warnings = warnings
    return new_fi


# =============================================================================
# MAIN
# =============================================================================

def main():
    ok = forms.alert(
        "IN-PLACE TO LOADABLE FAMILY\n"
        "===========================================\n\n"
        "Pick one in-place family instance.\n\n"
        "The tool will:\n"
        "  1. Extract its 3D geometry\n"
        "  2. Build a loadable .rfa (same category & name)\n"
        "  3. Save it to a temp folder\n"
        "  4. Load and place it in the same location\n"
        "  5. Optionally delete the original in-place family\n\n"
        "Note: geometry becomes non-parametric FreeForm solids.\n"
        "Materials may need to be re-applied in the family.",
        ok=True,
        cancel=True,
        title="In-Place to Loadable",
    )
    if not ok:
        script.exit()

    # --- Selection ---
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            InPlaceFamilyFilter(),
            "Select an IN-PLACE family instance",
        )
    except OperationCanceledException:
        script.exit()

    fi = doc.GetElement(ref.ElementId)
    if fi is None or not isinstance(fi, FamilyInstance):
        forms.alert("Invalid selection.", exitscript=True)

    family = fi.Symbol.Family
    if not family.IsInPlace:
        forms.alert("Selected element is not an in-place family.", exitscript=True)

    family_name   = get_family_name(family, fi)
    type_name     = get_type_name(fi.Symbol, fi)
    category      = family.FamilyCategory
    category_name = get_category_name(category)

    delete_original = forms.alert(
        "Delete the original in-place family after placing the loadable one?\n\n"
        "Family  :  {}\n"
        "Type    :  {}\n"
        "Category:  {}\n\n"
        "Yes = delete original  |  No = keep both".format(
            family_name, type_name, category_name
        ),
        ok=True,
        cancel=True,
        yes=True,
        no=True,
        title="Replace in-place family?",
    )

    rfa_path, loaded_family_name = get_rfa_save_info(family_name)

    # --- Build .rfa (NO open project transaction allowed here) ---
    try:
        rfa_path, template_warn = build_loadable_family(fi, rfa_path, type_name, category)
    except Exception as ex:
        forms.alert(
            "Geometry extraction / RFA build failed:\n\n{}\n\n{}".format(
                str(ex), traceback.format_exc()
            ),
            title="In-Place to Loadable — Error",
        )
        script.exit()

    # Snapshot location before any transaction
    placement   = PlacementInfo(fi)
    original_id = fi.Id

    # --- STEP A: Load family (its own transaction) ---------------------------
    loaded_family = None
    symbol        = None
    err           = None

    with Transaction(doc, "Load Family") as t_load:
        t_load.Start()
        try:
            loaded_family, symbol = load_and_get_symbol(rfa_path, type_name)
            t_load.Commit()
        except Exception as ex:
            err = traceback.format_exc()
            t_load.RollBack()

    if err:
        forms.alert(
            "RFA saved to:\n{}\n\n"
            "But loading the family failed:\n\n{}".format(rfa_path, err),
            title="In-Place to Loadable — Load Failure",
        )
        script.exit()

    # --- STEP B: Activate the symbol (separate transaction) ------------------
    if not symbol.IsActive:
        with Transaction(doc, "Activate Symbol") as t_act:
            t_act.Start()
            try:
                symbol.Activate()
                doc.Regenerate()
                t_act.Commit()
            except Exception as ex:
                t_act.RollBack()
                forms.alert(
                    "Could not activate family symbol:\n{}".format(ex),
                    title="In-Place to Loadable — Error",
                )
                script.exit()

    # --- STEP C: Place + (optionally) Delete ---------------------------------
    new_id              = None
    err                 = None
    placement_warnings  = []

    with Transaction(doc, "Place + Cleanup") as t:
        t.Start()
        try:
            new_fi = place_family_instance(placement, symbol)

            # Version-safe ID retrieval (IntegerValue removed in Revit 2026)
            new_id = get_element_id_value(new_fi.Id)

            placement_warnings = getattr(new_fi, "_placement_warnings", [])

            if new_fi.Symbol.Family.IsInPlace:
                raise Exception(
                    "Placed instance belongs to an in-place family — aborting."
                )

            if delete_original:
                doc.Delete(original_id)

            t.Commit()
        except Exception as ex:
            err                = traceback.format_exc()
            placement_warnings = []
            t.RollBack()

    if err:
        forms.alert(
            "Family loaded and symbol activated, but placement failed:\n\n{}".format(err),
            title="In-Place to Loadable — Placement Failure",
        )
        script.exit()

    loadable_name = get_family_name(loaded_family) if loaded_family else loaded_family_name

    msg_lines = [
        "Conversion complete!",
        "=====================================",
        "Loadable family : {}".format(loadable_name),
        "Type            : {}".format(type_name),
        "Category        : {}".format(category_name),
        "Saved to        : {}".format(rfa_path),
        "New instance Id : {}".format(new_id),
        "Original in-place: {}".format("Deleted" if delete_original else "Kept"),
    ]
    if loadable_name != family_name:
        msg_lines.append(
            "\nNote: '_Loadable' suffix added to avoid clashing "
            "with the in-place family name."
        )
    if template_warn:
        msg_lines.append("\nNote: " + template_warn)
    if placement_warnings:
        msg_lines.append("\nWARNING: " + "; ".join(placement_warnings))

    forms.alert("\n".join(msg_lines), title="In-Place to Loadable — Done")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        forms.alert(
            "Unexpected error:\n{}\n\n{}".format(str(e), traceback.format_exc()),
            title="In-Place to Loadable — Error",
        )