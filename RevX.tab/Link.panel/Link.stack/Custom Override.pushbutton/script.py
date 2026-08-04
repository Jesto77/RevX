# -*- coding: utf-8 -*-
"""Copy RVT Link display overrides from one view to one or more views.

Copies:
- Link visibility type (Custom)
- ALL category overrides (Model, Annotation, Analytical, Import)
  including visibility, halftone, line weight, color, pattern, transparency
- Category visibility (on/off)

Handles views with or without view templates:
- If target has no template (or template doesn't control RVT Links): writes to view
- If target has a template controlling RVT Links: writes to the template directly

Requires Revit 2018+ (View.GetLinkOverrides / SetLinkOverrides API).
"""

from pyrevit import revit, DB, forms, script
import Autodesk.Revit.DB as RDB

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()


# ─────────────────────────────────────────────────────────────────────
# SAFE ELEMENT ID TO INT
# ─────────────────────────────────────────────────────────────────────

def eid_to_int(element_id):
    """Convert ElementId to int - handles both old and new Revit API."""
    try:
        return element_id.IntegerValue
    except AttributeError:
        pass
    try:
        return element_id.Value
    except AttributeError:
        pass
    try:
        return int(element_id.ToString())
    except Exception:
        pass
    return id(element_id)


# ─────────────────────────────────────────────────────────────────────
# FIND LinkVisibilityType ENUM
# ─────────────────────────────────────────────────────────────────────

def find_link_visibility_type():
    try:
        lvt = DB.LinkVisibilityType
        _ = lvt.Custom
        return lvt
    except AttributeError:
        pass
    try:
        lvt = RDB.LinkVisibilityType
        _ = lvt.Custom
        return lvt
    except AttributeError:
        pass
    try:
        s = RDB.RevitLinkGraphicsSettings()
        return type(s.LinkVisibilityType)
    except Exception:
        pass
    try:
        import clr
        clr.AddReference("RevitAPI")
        import Autodesk
        lvt = Autodesk.Revit.DB.LinkVisibilityType
        _ = lvt.Custom
        return lvt
    except Exception:
        pass
    return None


LinkVisibilityType = find_link_visibility_type()

if LinkVisibilityType is None:
    forms.alert("Cannot find LinkVisibilityType enum. Script cannot proceed.",
                exitscript=True)


# ─────────────────────────────────────────────────────────────────────
# SAFE NAME HELPER
# ─────────────────────────────────────────────────────────────────────

def safe_name(element):
    if element is None:
        return "<None>"
    try:
        name_param = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if name_param and name_param.HasValue:
            return name_param.AsString()
    except Exception:
        pass
    try:
        return element.Name
    except Exception:
        pass
    try:
        return DB.Element.Name.__get__(element)
    except Exception:
        pass
    try:
        return element.get_Name()
    except Exception:
        pass
    try:
        name_param = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if name_param and name_param.HasValue:
            return name_param.AsString()
    except Exception:
        pass
    return "Id:{}".format(element.Id)


# ─────────────────────────────────────────────────────────────────────
# CORE HELPERS
# ─────────────────────────────────────────────────────────────────────

def is_custom(settings):
    if settings is None:
        return False
    try:
        return settings.LinkVisibilityType == LinkVisibilityType.Custom
    except Exception:
        try:
            return int(settings.LinkVisibilityType) == 2
        except Exception:
            return False


def make_custom_settings():
    s = RDB.RevitLinkGraphicsSettings()
    try:
        s.LinkVisibilityType = LinkVisibilityType.Custom
    except Exception:
        try:
            s.LinkVisibilityType = LinkVisibilityType(2)
        except Exception:
            pass
    return s


def link_label(link):
    return safe_name(link)


def get_link_type(link_inst):
    try:
        tid = link_inst.GetTypeId()
        if tid and tid != DB.ElementId.InvalidElementId:
            return doc.GetElement(tid)
    except Exception:
        pass
    return None


def get_linked_doc(link_inst):
    try:
        return link_inst.GetLinkDocument()
    except Exception:
        return None


def get_all_link_instances():
    return list(
        DB.FilteredElementCollector(doc)
          .OfClass(DB.RevitLinkInstance)
          .WhereElementIsNotElementType()
          .ToElements()
    )


def get_selectable_views():
    excluded_types = (
        DB.ViewType.Schedule,
        DB.ViewType.DrawingSheet,
        DB.ViewType.Legend,
        DB.ViewType.ProjectBrowser,
        DB.ViewType.SystemBrowser,
        DB.ViewType.Internal,
        DB.ViewType.Undefined,
    )
    return [
        v for v in DB.FilteredElementCollector(doc)
                     .OfClass(DB.View)
                     .WhereElementIsNotElementType()
                     .ToElements()
        if not v.IsTemplate and v.ViewType not in excluded_types
    ]


# ─────────────────────────────────────────────────────────────────────
# TEMPLATE HELPERS
# ─────────────────────────────────────────────────────────────────────

def get_rvt_link_param_id():
    bip = getattr(DB.BuiltInParameter, "VIS_GRAPHICS_RVT_LINKS", None)
    if bip is not None:
        return DB.ElementId(bip)
    return None


RVT_LINK_PARAM_ID = get_rvt_link_param_id()


def template_controls_link_overrides(view):
    if view.ViewTemplateId == DB.ElementId.InvalidElementId:
        return False
    template = doc.GetElement(view.ViewTemplateId)
    if template is None:
        return False
    if RVT_LINK_PARAM_ID is None:
        return None
    non_controlled = template.GetNonControlledTemplateParameterIds()
    return RVT_LINK_PARAM_ID not in non_controlled


def get_settings_owner(view):
    """Return the element that actually owns the link override data.
    If view has a template controlling RVT Links -> returns the template.
    Otherwise -> returns the view itself."""
    if template_controls_link_overrides(view) is True:
        return doc.GetElement(view.ViewTemplateId)
    return view


# ─────────────────────────────────────────────────────────────────────
# FULL LINK OVERRIDE COPY
# ─────────────────────────────────────────────────────────────────────

def copy_full_link_overrides(source_view, target_view_or_template, link_inst):
    """
    Copy all link display settings from source to target.
    target_view_or_template can be either a View or a View Template.
    """
    result = {
        "link_override_copied": False,
        "categories_copied": 0,
        "categories_errored": 0,
        "cat_errors": [],
        "overall_error": None
    }

    link_id = link_inst.Id
    link_type = get_link_type(link_inst)

    # Step 1: Copy link-level settings
    for eid in [link_id] + ([link_type.Id] if link_type else []):
        try:
            s = source_view.GetLinkOverrides(eid)
            if s is not None and is_custom(s):
                target_view_or_template.SetLinkOverrides(eid, s)
                result["link_override_copied"] = True
                break
        except Exception:
            continue

    if not result["link_override_copied"]:
        try:
            cs = make_custom_settings()
            target_view_or_template.SetLinkOverrides(link_id, cs)
            result["link_override_copied"] = True
        except Exception as e:
            result["overall_error"] = str(e)
            return result

    # Step 2: Copy per-category overrides
    linked_doc = get_linked_doc(link_inst)
    if linked_doc is None:
        result["overall_error"] = "Could not access linked document"
        return result

    all_cats = []
    try:
        for cat in linked_doc.Settings.Categories:
            all_cats.append(cat)
            try:
                if cat.SubCategories:
                    for sub in cat.SubCategories:
                        all_cats.append(sub)
            except Exception:
                pass
    except Exception as e:
        result["overall_error"] = "Could not read linked doc categories: {}".format(e)
        return result

    for cat in all_cats:
        try:
            cat_id = cat.Id
            override_copied = False

            try:
                src_ovr = source_view.GetCategoryOverrides(cat_id)
                src_hidden = source_view.GetCategoryHidden(cat_id)

                target_view_or_template.SetCategoryOverrides(cat_id, src_ovr)
                target_view_or_template.SetCategoryHidden(cat_id, src_hidden)
                override_copied = True
            except Exception:
                pass

            if override_copied:
                result["categories_copied"] += 1

        except Exception as e:
            result["categories_errored"] += 1
            err = str(e)
            if err not in result["cat_errors"]:
                result["cat_errors"].append(err)

    return result


# ─────────────────────────────────────────────────────────────────────
# SOURCE OVERRIDE CHECK
# ─────────────────────────────────────────────────────────────────────

def get_real_source_override(owner_view, link_inst):
    candidates = []
    candidates.append(("INSTANCE", link_inst.Id, link_label(link_inst)))

    link_type = get_link_type(link_inst)
    if link_type:
        candidates.append(("TYPE", link_type.Id, safe_name(link_type)))

    for kind, eid, label in candidates:
        try:
            s = owner_view.GetLinkOverrides(eid)
            if s is not None:
                return kind, eid, label, s
        except Exception:
            pass

    return None, None, None, None


# ─────────────────────────────────────────────────────────────────────
# STEP 1 – Pick link(s)
# ─────────────────────────────────────────────────────────────────────

links = get_all_link_instances()
if not links:
    forms.alert("No linked Revit models found.", exitscript=True)

link_map = {}
for l in links:
    lbl = link_label(l)
    if lbl in link_map:
        lbl = "{} (Id:{})".format(lbl, l.Id)
    link_map[lbl] = l

selected_link_names = forms.SelectFromList.show(
    sorted(link_map.keys()),
    title="Select Link(s) to Copy Settings For",
    multiselect=True,
    button_name="Select Link(s)",
)
if not selected_link_names:
    script.exit()

selected_links = [link_map[n] for n in selected_link_names]


# ─────────────────────────────────────────────────────────────────────
# STEP 2 – Pick source view
# ─────────────────────────────────────────────────────────────────────

all_views = get_selectable_views()
view_map = {}
for v in all_views:
    key = "{} - {}".format(v.ViewType, safe_name(v))
    if key in view_map:
        key = "{} (Id:{})".format(key, v.Id)
    view_map[key] = v

source_key = forms.SelectFromList.show(
    sorted(view_map.keys()),
    title="Select SOURCE View (copy FROM)",
    multiselect=False,
    button_name="Select Source View",
)
if not source_key:
    script.exit()

source_view = view_map[source_key]
source_owner = get_settings_owner(source_view)
source_is_tpl = source_owner.Id != source_view.Id


# ─────────────────────────────────────────────────────────────────────
# STEP 3 – Validate source
# ─────────────────────────────────────────────────────────────────────

valid_links = []
missing_links = []

for link in selected_links:
    kind, eid, label, settings = get_real_source_override(source_owner, link)

    if settings is None:
        missing_links.append(link)
    elif not is_custom(settings):
        missing_links.append(link)
    else:
        valid_links.append(link)

if not valid_links:
    forms.alert(
        "No Custom overrides found on the source view.\n\n"
        "Open Visibility/Graphics for '{}', go to Revit Links tab, "
        "set the link to 'Custom', configure your category overrides, "
        "then re-run.".format(safe_name(source_view)),
        exitscript=True,
    )

if missing_links:
    names = "\n".join("  - " + link_label(l) for l in missing_links)
    if not forms.alert(
        "These links have no Custom override:\n\n{}\n\n"
        "Skip them and continue?".format(names),
        yes=True, no=True
    ):
        script.exit()


# ─────────────────────────────────────────────────────────────────────
# STEP 4 – Pick target view(s)
# ─────────────────────────────────────────────────────────────────────

target_keys = forms.SelectFromList.show(
    sorted(k for k in view_map if k != source_key),
    title="Select TARGET View(s) (copy TO)",
    multiselect=True,
    button_name="Select Target Views",
)
if not target_keys:
    script.exit()

target_views = [view_map[k] for k in target_keys]


# ─────────────────────────────────────────────────────────────────────
# STEP 5 – Resolve target owners (view or its template)
#           Group by unique owner to avoid writing to same template twice
# ─────────────────────────────────────────────────────────────────────

target_owner_map = {}

for v in target_views:
    owner = get_settings_owner(v)
    owner_key = eid_to_int(owner.Id)

    if owner_key not in target_owner_map:
        target_owner_map[owner_key] = {
            "owner": owner,
            "is_template": owner.Id != v.Id,
            "views": []
        }
    target_owner_map[owner_key]["views"].append(safe_name(v))


# ─────────────────────────────────────────────────────────────────────
# STEP 6 – Apply
# ─────────────────────────────────────────────────────────────────────

success_count = 0
fail_count = 0
template_count = 0
view_count = 0

t = DB.Transaction(doc, "Copy RVT Link Display Settings + Category Overrides")
t.Start()

try:
    for owner_key, info in target_owner_map.items():
        owner = info["owner"]
        is_tpl = info["is_template"]
        view_names = info["views"]

        for link in valid_links:
            try:
                r = copy_full_link_overrides(source_owner, owner, link)
                if r["overall_error"]:
                    fail_count += len(view_names)
                else:
                    success_count += len(view_names)
                    if is_tpl:
                        template_count += 1
                    else:
                        view_count += len(view_names)
            except Exception:
                fail_count += len(view_names)

    t.Commit()

except Exception as ex:
    t.RollBack()
    forms.alert("Transaction failed:\n\n{}".format(ex))
    script.exit()


# ─────────────────────────────────────────────────────────────────────
# STEP 7 – Toast notification
# ─────────────────────────────────────────────────────────────────────

if fail_count == 0:
    msg = "Link overrides copied to {} view(s).".format(success_count)
    if template_count > 0:
        msg += " ({} via template)".format(template_count)
    forms.toaster.send_toast(msg, title="Copy Link Overrides")
else:
    forms.toaster.send_toast(
        "Copied: {} | Failed: {}".format(success_count, fail_count),
        title="Copy Link Overrides"
    )