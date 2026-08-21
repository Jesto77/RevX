# -*- coding: utf-8 -*-
"""Sheet Expor .

Selection -> Format -> Create workflow with custom file naming.
Exports PDF / DWG / DGN / DWF / NWC / IFC / IMG.

Compatible with Revit 2024, 2025, 2026, 2027 (IronPython 2.7, 3.x and CPython).
"""

__title__ = "Export"
__author__ = "Jesto Joy"
__doc__ = ("Batch export sheets and views to PDF, DWG, DGN, DWF, NWC, IFC and "
           "images with fully customisable file naming.")

import os
import re
import sys
import json
import codecs
import datetime
import traceback

import clr

clr.AddReference("System")
clr.AddReference("System.Core")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
clr.AddReference("System.Windows.Forms")

import System
from System import Guid, EventHandler
from System.Collections.Generic import List
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Windows import Visibility, RoutedEventHandler, FrameworkElement
from System.Windows.Media import VisualTreeHelper
from System.Windows.Controls import CheckBox
from System.Windows.Data import (CollectionViewSource, Binding,
                                  PropertyGroupDescription, CollectionViewGroup)
from System.Windows.Input import Cursors, Key
from System.Windows.Threading import DispatcherPriority
from System.Windows.Controls import TextChangedEventHandler
from System.Windows.Controls.Primitives import TextBoxBase
from System.Windows.Forms import (FolderBrowserDialog, DialogResult,
                                  OpenFileDialog, SaveFileDialog)

from Autodesk.Revit import DB
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSheet, View, ViewType, ElementId,
    BuiltInParameter, Transaction, TransactionGroup, StorageType,
    ViewSet, ImageExportOptions, ImageFileType, ImageResolution,
    ZoomFitType, ExportRange, FitDirectionType,
    DWGExportOptions, DGNExportOptions, DWFExportOptions,
    ExportUnit, ACADVersion, ViewSheetSet, ExportDWGSettings,
    ExportDGNSettings, DWFImageQuality,
)

from pyrevit import revit, forms, script, HOST_APP

# --------------------------------------------------------------------------- #
#  Environment
# --------------------------------------------------------------------------- #

doc = revit.doc
uidoc = revit.uidoc
# NOTE: pyrevit.revit exposes only doc/uidoc/docs/active_view - there is no
# `revit.uiapp`. HOST_APP is the supported accessor across all pyRevit builds.
uiapp = HOST_APP.uiapp
app = HOST_APP.app

if doc is None or uidoc is None:
    # `exitscript` on an OK-only alert does not stop execution, so exit here.
    forms.alert("No active Revit document.\n\nOpen a project and run "
                "Sheet Export again.", title="Sheet Export")
    sys.exit()

logger = script.get_logger()
output = script.get_output()

try:
    REVIT_VERSION = int(app.VersionNumber)
except Exception:
    REVIT_VERSION = int(HOST_APP.version)
XAML_FILE = "ui.xaml"
CURRENT_RULE_LABEL = "<current rule>"
# naming rules live here as plain .xml files - drop one in and it appears
RULES_DIR = os.path.join(
    os.getenv("APPDATA") or os.path.expanduser("~"),
    "pyRevit", "SheetExport", "NamingRules")

ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def eid_value(eid):
    """ElementId -> int, valid on every version (IntegerValue removed in 2024+)."""
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def sanitize(name, replace=True):
    if not name:
        return "Unnamed"
    if replace:
        name = ILLEGAL.sub("-", name)
    return name.strip().strip(".")


def unique_path(path, overwrite):
    """Return a free path; if overwrite, remove the existing one instead."""
    if not os.path.exists(path):
        return path
    if overwrite:
        try:
            os.remove(path)
            return path
        except Exception:
            pass
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s (%d)%s" % (base, i, ext)):
        i += 1
    return "%s (%d)%s" % (base, i, ext)


# --------------------------------------------------------------------------- #
#  Row view-model
# --------------------------------------------------------------------------- #

class ExportItem(INotifyPropertyChanged, object):
    """One sheet or view row in the Selection grid."""

    def __init__(self, element, is_sheet):
        self._handlers = []
        self._selected = False
        self._custom = ""

        self.Element = element
        self.IsSheet = is_sheet
        self.Id = element.Id
        self.IdValue = eid_value(element.Id)

        if is_sheet:
            self.Number = element.SheetNumber
            self.Name = element.Name
        else:
            self.Number = ""
            try:
                self.Name = element.Name
            except Exception:
                self.Name = "<unnamed>"

        self.Revision = self._revision(element, is_sheet)
        self.Size = self._size(element, is_sheet)
        self.SeriesRange = self._series_range(element, is_sheet)
        self.ViewTypeName = str(element.ViewType)
        self._search = (u"%s %s %s %s" % (self.Number, self.Name, self.Size,
                                          self.SeriesRange)).lower()

    # --- INotifyPropertyChanged -------------------------------------------- #
    def add_PropertyChanged(self, handler):
        self._handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)

    def _notify(self, prop):
        args = PropertyChangedEventArgs(prop)
        for h in list(self._handlers):
            try:
                h(self, args)
            except Exception:
                pass

    # --- bound properties --------------------------------------------------- #
    @property
    def IsSelected(self):
        return self._selected

    @IsSelected.setter
    def IsSelected(self, value):
        value = bool(value)
        if value != self._selected:
            self._selected = value
            self._notify("IsSelected")
            if SELECTION_CALLBACK[0]:
                SELECTION_CALLBACK[0]()

    @property
    def CustomFileName(self):
        return self._custom

    @CustomFileName.setter
    def CustomFileName(self, value):
        self._custom = value or ""
        self._notify("CustomFileName")

    # --- helpers ------------------------------------------------------------ #
    @staticmethod
    def _revision(element, is_sheet):
        if not is_sheet:
            return ""
        try:
            p = element.get_Parameter(BuiltInParameter.SHEET_CURRENT_REVISION)
            return p.AsString() or "" if p else ""
        except Exception:
            return ""

    @staticmethod
    def _size(element, is_sheet):
        """Sheet size, resolved from the pre-built title block map.

        Never query the database per row here: SIZE_MAP is filled with a single
        bulk collector in build_titleblock_map() before the rows are created.
        """
        if not is_sheet:
            return ""
        return SIZE_MAP.get(eid_value(element.Id), "-")

    @staticmethod
    def _series_range(element, is_sheet):
        """Sheet 'Series Range' parameter value, mirroring the Project Browser
        grouping. Views never carry this, so they always fall back to "".
        """
        if not is_sheet:
            return ""
        try:
            p = element.LookupParameter("Series Range")
            if p:
                val = ""
                if p.StorageType == StorageType.String:
                    val = p.AsString() or ""
                if not val:
                    val = p.AsValueString() or ""
                if val:
                    return val
        except Exception:
            pass
        return "(No Series Range)"

    def matches(self, term):
        return term in self._search


SELECTION_CALLBACK = [None]

# sheet-id -> paper size, built once in a single database pass
SIZE_MAP = {}

_SIZE_TOKENS = ("A0", "A1", "A2", "A3", "A4",
                "ARCH E", "ARCH D", "ARCH C", "ARCH B", "ARCH A",
                "ANSI E", "ANSI D", "ANSI C", "ANSI B", "ANSI A")


def build_titleblock_map():
    """Collect every title block once and map it to its owner sheet.

    One collector for the whole model instead of one per sheet - this is the
    difference between a window that opens instantly and one that stalls.
    """
    SIZE_MAP.clear()
    try:
        tblocks = FilteredElementCollector(doc) \
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks) \
            .WhereElementIsNotElementType().ToElements()
    except Exception:
        return

    for tb in tblocks:
        try:
            sheet_id = eid_value(tb.OwnerViewId)
            if sheet_id in SIZE_MAP:
                continue
            name = ""
            try:
                # type name carries the paper size (e.g. "A1 Metric")
                tsym = doc.GetElement(tb.GetTypeId())
                if tsym:
                    p = tsym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                    name = (p.AsString() if p else "") or ""
            except Exception:
                name = ""
            upper = name.upper()
            size = next((t for t in _SIZE_TOKENS if t in upper), name or "-")
            SIZE_MAP[sheet_id] = size
        except Exception:
            continue


# --------------------------------------------------------------------------- #
#  Parameter harvesting for custom naming
# --------------------------------------------------------------------------- #

BUILTIN_TOKENS = [
    "Sheet Number", "Sheet Name", "Current Revision", "Current Revision Date",
    "Current Revision Description", "View Name", "View Type", "Scale",
    "Project Number", "Project Name", "Client Name", "File Name (model)",
    "Date (yyyy-mm-dd)", "Date (yyyymmdd)", "Time (hhmm)", "Sheet Issue Date",
    "Drawn By", "Checked By", "Designed By", "Approved By",
]


def collect_parameters(items):
    """Union of instance parameter names across the sample plus built-in tokens."""
    names = set()
    none_storage = getattr(StorageType, "None")
    for item in items[:8]:
        try:
            for p in item.Element.Parameters:
                if p.Definition and p.StorageType != none_storage:
                    names.add(p.Definition.Name)
        except Exception:
            continue
    try:
        pinfo = doc.ProjectInformation
        for p in pinfo.Parameters:
            if p.Definition:
                names.add(p.Definition.Name)
    except Exception:
        pass
    extra = sorted(n for n in names if n not in BUILTIN_TOKENS)
    return BUILTIN_TOKENS + extra


def param_value(item, token):
    """Resolve one naming token for one row."""
    el = item.Element
    now = datetime.datetime.now()

    if token == "Sheet Number":
        return item.Number
    if token in ("Sheet Name", "View Name"):
        return item.Name
    if token == "Current Revision":
        return item.Revision
    if token == "View Type":
        return item.ViewTypeName
    if token == "Date (yyyy-mm-dd)":
        return now.strftime("%Y-%m-%d")
    if token == "Date (yyyymmdd)":
        return now.strftime("%Y%m%d")
    if token == "Time (hhmm)":
        return now.strftime("%H%M")
    if token == "File Name (model)":
        try:
            return os.path.splitext(os.path.basename(doc.PathName))[0] or "Model"
        except Exception:
            return "Model"
    if token == "Scale":
        try:
            return str(el.Scale)
        except Exception:
            return ""

    # element parameter, then project information
    for source in (el, doc.ProjectInformation):
        try:
            p = source.LookupParameter(token)
            if p:
                if p.StorageType == StorageType.String:
                    return p.AsString() or ""
                val = p.AsValueString()
                if val:
                    return val
                if p.StorageType == StorageType.Integer:
                    return str(p.AsInteger())
                if p.StorageType == StorageType.Double:
                    return str(round(p.AsDouble(), 3))
                if p.StorageType == StorageType.ElementId:
                    other = doc.GetElement(p.AsElementId())
                    return other.Name if other else ""
        except Exception:
            continue
    return ""


def build_name(item, tokens, separator, prefix, suffix,
               remove_spaces, upper, do_sanitize):
    parts = [param_value(item, t) for t in tokens]
    parts = [p for p in parts if p]
    sep = "" if separator == "none" else separator
    name = sep.join(parts)
    if prefix:
        name = prefix + name
    if suffix:
        name = name + suffix
    if remove_spaces:
        name = name.replace(" ", "")
    if upper:
        name = name.upper()
    # fall back to the sheet number / view name before sanitising, otherwise
    # an empty rule would silently produce "Unnamed" for every row
    if not name.strip():
        name = item.Number or item.Name
    if do_sanitize:
        name = sanitize(name, True)
    return name



# --------------------------------------------------------------------------- #
#  Custom File Name dialog (modal, opened from the Selection tab)
# --------------------------------------------------------------------------- #

DEFAULT_RULE = dict(tokens=["Sheet Number", "Sheet Name"], separator="_",
                    prefix="", suffix="", remove_spaces=False, upper=False,
                    do_sanitize=True)


class NamingWindow(forms.WPFWindow):
    """Builds a file-naming rule. Returns the rule via .result (None = cancel)."""

    XML_FILTER = "Sheet Export naming rule (*.xml)|*.xml|All files (*.*)|*.*"

    def __init__(self, available, rule, sample, start_dir):
        forms.WPFWindow.__init__(self, "naming.xaml")
        self.result = None
        self._sample = sample
        self._start_dir = start_dir
        self._available = list(available)

        for name in self._available:
            self.LstAvailable.Items.Add(name)
        for token in rule.get("tokens", []):
            self.LstSelected.Items.Add(token)
            if token not in self._available:
                self.LstAvailable.Items.Add(token)

        self.CmbSeparator.Text = rule.get("separator", "_")
        self.TxtPrefix.Text = rule.get("prefix", "")
        self.TxtSuffix.Text = rule.get("suffix", "")
        self.ChkRemoveSpaces.IsChecked = rule.get("remove_spaces", False)
        self.ChkUppercase.IsChecked = rule.get("upper", False)
        self.ChkSanitize.IsChecked = rule.get("do_sanitize", True)

        self.BtnAddParam.Click += self.on_add
        self.BtnRemoveParam.Click += self.on_remove
        self.BtnParamUp.Click += lambda s_, e_: self._move(-1)
        self.BtnParamDown.Click += lambda s_, e_: self._move(1)
        self.LstAvailable.MouseDoubleClick += self.on_add
        self.LstSelected.MouseDoubleClick += self.on_remove
        self.LstSelected.SelectionChanged += self.on_sel_changed
        self.TxtParamSearch.TextChanged += self.on_filter
        self.BtnSaveXml.Click += self.on_save_xml
        self.BtnLoadXml.Click += self.on_load_xml
        self.BtnApplyNaming.Click += self.on_apply
        self.BtnCancelNaming.Click += self.on_cancel

        for ctrl in (self.CmbSeparator,):
            ctrl.SelectionChanged += self.on_changed
        # the separator combo is editable: catch typed text as well as picks
        self.CmbSeparator.AddHandler(
            TextBoxBase.TextChangedEvent,
            TextChangedEventHandler(self.on_changed))
        for ctrl in (self.TxtPrefix, self.TxtSuffix):
            ctrl.TextChanged += self.on_changed
        for ctrl in (self.ChkRemoveSpaces, self.ChkUppercase, self.ChkSanitize):
            ctrl.Checked += self.on_changed
            ctrl.Unchecked += self.on_changed

        self.on_sel_changed(None, None)
        self._refresh_preview()

    # ---------------- rule ----------------
    def current_rule(self):
        sep_item = self.CmbSeparator.SelectedItem
        sep = str(sep_item) if sep_item is not None else (self.CmbSeparator.Text or "_")
        return dict(
            tokens=[str(t) for t in self.LstSelected.Items],
            separator=sep,
            prefix=self.TxtPrefix.Text or "",
            suffix=self.TxtSuffix.Text or "",
            remove_spaces=bool(self.ChkRemoveSpaces.IsChecked),
            upper=bool(self.ChkUppercase.IsChecked),
            do_sanitize=bool(self.ChkSanitize.IsChecked),
        )

    def _refresh_preview(self):
        rule = self.current_rule()
        if not rule["tokens"]:
            self.TxtNamePreview.Text = "(add at least one parameter)"
            self.BtnApplyNaming.IsEnabled = False
            return
        self.BtnApplyNaming.IsEnabled = True
        if self._sample is None:
            self.TxtNamePreview.Text = "(no sheets loaded)"
            return
        self.TxtNamePreview.Text = build_name(self._sample, **rule) + ".pdf"

    # ---------------- handlers ----------------
    def on_changed(self, sender, args):
        self._refresh_preview()

    def on_filter(self, sender, args):
        term = (self.TxtParamSearch.Text or "").strip().lower()
        self.LstAvailable.Items.Clear()
        for name in self._available:
            if not term or term in name.lower():
                self.LstAvailable.Items.Add(name)

    def on_add(self, sender, args):
        added = None
        for item in list(self.LstAvailable.SelectedItems):
            if item not in self.LstSelected.Items:
                self.LstSelected.Items.Add(item)
                added = item
        if added is not None:
            self.LstSelected.SelectedItem = added
        self.on_sel_changed(None, None)
        self._refresh_preview()

    def on_remove(self, sender, args):
        idx = self.LstSelected.SelectedIndex
        for item in list(self.LstSelected.SelectedItems):
            self.LstSelected.Items.Remove(item)
        if self.LstSelected.Items.Count:
            self.LstSelected.SelectedIndex = min(idx, self.LstSelected.Items.Count - 1)
        self.on_sel_changed(None, None)
        self._refresh_preview()

    def _move(self, delta):
        idx = self.LstSelected.SelectedIndex
        if idx < 0:
            return
        new = idx + delta
        if new < 0 or new >= self.LstSelected.Items.Count:
            return
        item = self.LstSelected.Items[idx]
        self.LstSelected.Items.RemoveAt(idx)
        self.LstSelected.Items.Insert(new, item)
        self.LstSelected.SelectedIndex = new
        self.LstSelected.Focus()
        self.on_sel_changed(None, None)
        self._refresh_preview()

    def on_sel_changed(self, sender, args):
        idx = self.LstSelected.SelectedIndex
        count = self.LstSelected.Items.Count
        self.BtnParamUp.IsEnabled = idx > 0
        self.BtnParamDown.IsEnabled = 0 <= idx < count - 1
        self.BtnRemoveParam.IsEnabled = idx >= 0

    def on_apply(self, sender, args):
        rule = self.current_rule()
        if not rule["tokens"]:
            forms.alert("Add at least one parameter first.", title="Custom File Name")
            return
        self.result = rule
        self.DialogResult = True
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()

    # ---------------- XML ----------------
    def on_save_xml(self, sender, args):
        rule = self.current_rule()
        if not rule["tokens"]:
            forms.alert("Add at least one parameter before saving the rule.",
                        title="Custom File Name")
            return
        dlg = SaveFileDialog()
        dlg.Filter = self.XML_FILTER
        dlg.Title = "Save naming rule"
        dlg.FileName = "SheetExport_Naming.xml"
        dlg.DefaultExt = "xml"
        dlg.AddExtension = True
        try:                                   # default to the rules folder
            if not os.path.isdir(RULES_DIR):
                os.makedirs(RULES_DIR)
            dlg.InitialDirectory = RULES_DIR
        except Exception:
            if self._start_dir and os.path.isdir(self._start_dir):
                dlg.InitialDirectory = self._start_dir
        if dlg.ShowDialog() != DialogResult.OK:
            return
        try:
            write_rule_xml(dlg.FileName, rule)
            forms.alert("Naming rule saved:\n%s" % dlg.FileName,
                        title="Custom File Name")
        except Exception as ex:
            forms.alert("Could not save the XML:\n%s" % ex, title="Custom File Name")

    def on_load_xml(self, sender, args):
        dlg = OpenFileDialog()
        dlg.Filter = self.XML_FILTER
        dlg.Title = "Load naming rule"
        if os.path.isdir(RULES_DIR):
            dlg.InitialDirectory = RULES_DIR
        elif self._start_dir and os.path.isdir(self._start_dir):
            dlg.InitialDirectory = self._start_dir
        if dlg.ShowDialog() != DialogResult.OK:
            return
        try:
            rule = read_rule_xml(dlg.FileName)
        except Exception as ex:
            forms.alert("Could not read the XML:\n%s" % ex, title="Custom File Name")
            return
        self.LstSelected.Items.Clear()
        for token in rule["tokens"]:
            self.LstSelected.Items.Add(token)
            if token not in self._available:
                self._available.append(token)
                self.LstAvailable.Items.Add(token)
        self.CmbSeparator.Text = rule["separator"]
        self.TxtPrefix.Text = rule["prefix"]
        self.TxtSuffix.Text = rule["suffix"]
        self.ChkRemoveSpaces.IsChecked = rule["remove_spaces"]
        self.ChkUppercase.IsChecked = rule["upper"]
        self.ChkSanitize.IsChecked = rule["do_sanitize"]
        self.on_sel_changed(None, None)
        self._refresh_preview()


def write_rule_xml(path, rule):
    def esc(v):
        return (str(v).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             '<SheetExportNaming version="1">',
             '  <Separator>%s</Separator>' % esc(rule["separator"]),
             '  <Prefix>%s</Prefix>' % esc(rule["prefix"]),
             '  <Suffix>%s</Suffix>' % esc(rule["suffix"]),
             '  <RemoveSpaces>%s</RemoveSpaces>' % rule["remove_spaces"],
             '  <Uppercase>%s</Uppercase>' % rule["upper"],
             '  <Sanitize>%s</Sanitize>' % rule["do_sanitize"],
             '  <Parameters>']
    for i, token in enumerate(rule["tokens"]):
        lines.append('    <Parameter order="%d">%s</Parameter>' % (i, esc(token)))
    lines += ['  </Parameters>', '</SheetExportNaming>', '']
    with codecs.open(path, "w", "utf-8") as fh:
        fh.write("\n".join(lines))


def read_rule_xml(path):
    import xml.etree.ElementTree as ET
    import re

    tokens = []
    separator = "_"
    prefix = ""
    suffix = ""
    remove_spaces = False
    upper = False
    do_sanitize = True

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as ex:
        raise ValueError("Invalid XML file format: %s" % ex)

    def strip_ns(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    def find_text_anywhere(target_tag, default=""):
        for elem in root.iter():
            if strip_ns(elem.tag) == target_tag and elem.text is not None and elem.text.strip():
                return elem.text.strip()
        return default

    def find_flag_anywhere(target_tag, default=False):
        val = find_text_anywhere(target_tag, str(default))
        return val.strip().lower() in ("true", "1", "yes")

    root_tag = strip_ns(root.tag)

    # 1. Standard RevX XML (<SheetExportNaming>)
    if root_tag == "SheetExportNaming":
        params_node = None
        for elem in root.iter():
            if strip_ns(elem.tag) == "Parameters":
                params_node = elem
                break
        if params_node is not None:
            def order_of(n):
                try:
                    return int(n.get("order", "0"))
                except Exception:
                    return 0
            param_elems = [e for e in params_node if strip_ns(e.tag) == "Parameter"]
            for node in sorted(param_elems, key=order_of):
                if node.text and node.text.strip():
                    tokens.append(node.text.strip())
        
        return dict(
            tokens=tokens,
            separator=find_text_anywhere("Separator", "_"),
            prefix=find_text_anywhere("Prefix", ""),
            suffix=find_text_anywhere("Suffix", ""),
            remove_spaces=find_flag_anywhere("RemoveSpaces"),
            upper=find_flag_anywhere("Uppercase"),
            do_sanitize=find_flag_anywhere("Sanitize", True)
        )

    # 2. ProSheets / DiRoots XML (<Profiles> / <Profile> / <SelectSheetParameters>)
    select_params_node = None
    for elem in root.iter():
        if strip_ns(elem.tag) == "SelectSheetParameters":
            select_params_node = elem
            break

    if select_params_node is not None:
        combine_params_node = None
        for elem in select_params_node.iter():
            if strip_ns(elem.tag) == "CombineParameters":
                combine_params_node = elem
                break
        
        sep_found = None
        if combine_params_node is not None:
            for p_model in combine_params_node:
                if strip_ns(p_model.tag) == "ParameterModel":
                    p_name = None
                    for child in p_model:
                        if strip_ns(child.tag) == "ParameterName" and child.text and child.text.strip():
                            p_name = child.text.strip()
                            break
                    if p_name:
                        tokens.append(p_name)
                    
                    for attr_k, attr_v in p_model.attrib.items():
                        if ("preserve" in attr_k.lower() or "space" in attr_k.lower()) and attr_v:
                            sep_found = attr_v

        if not sep_found:
            cp_name = find_text_anywhere("CombineParameterName", "")
            if cp_name and len(tokens) > 1:
                for candidate in ["-", "_", ".", " "]:
                    if candidate.join(tokens) == cp_name:
                        sep_found = candidate
                        break
                if not sep_found:
                    first_t = tokens[0]
                    if first_t in cp_name:
                        idx = cp_name.find(first_t) + len(first_t)
                        if idx < len(cp_name):
                            sep_found = cp_name[idx]

        if sep_found:
            separator = sep_found

        if tokens:
            return dict(
                tokens=tokens,
                separator=separator,
                prefix=prefix,
                suffix=suffix,
                remove_spaces=remove_spaces,
                upper=upper,
                do_sanitize=do_sanitize
            )

    # 3. ExportProfile XML (<NamingTemplate>)
    naming_template_text = find_text_anywhere("NamingTemplate", "")
    if naming_template_text:
        found_tokens = re.findall(r'\{(?:\w+:)?([^}]+)\}', naming_template_text)
        if found_tokens:
            tokens = [t.strip() for t in found_tokens]
            template_pattern = re.sub(r'\{(?:\w+:)?([^}]+)\}', 'TOKEN', naming_template_text)
            sep_match = re.search(r'TOKEN([^\w\s])TOKEN', template_pattern)
            if sep_match:
                separator = sep_match.group(1)

            return dict(
                tokens=tokens,
                separator=separator,
                prefix=prefix,
                suffix=suffix,
                remove_spaces=remove_spaces,
                upper=upper,
                do_sanitize=do_sanitize
            )

    # 4. Generic XML Fallback
    for elem in root.iter():
        tag = strip_ns(elem.tag)
        if tag in ("Parameter", "ParameterName", "Param", "Field", "Token"):
            if elem.text and elem.text.strip():
                txt = elem.text.strip()
                if txt not in tokens and "<" not in txt:
                    tokens.append(txt)

    return dict(
        tokens=tokens,
        separator=find_text_anywhere("Separator", "_"),
        prefix=find_text_anywhere("Prefix", ""),
        suffix=find_text_anywhere("Suffix", ""),
        remove_spaces=find_flag_anywhere("RemoveSpaces"),
        upper=find_flag_anywhere("Uppercase"),
        do_sanitize=find_flag_anywhere("Sanitize", True)
    )


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #

class SheetExportWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)

        self._all_items = []
        self._sheet_cache = None
        self._view_cache = None
        self._cancel = False
        self._busy = False
        self._profiles = {}
        self._rule = dict(DEFAULT_RULE)
        self._rule["tokens"] = list(DEFAULT_RULE["tokens"])
        self._param_cache = None
        self._loading_profiles = False
        self._syncing = False
        self._checkbox_click = False

        self.TxtVersion.Text = "  v1.0  |  Revit %s" % REVIT_VERSION
        try:
            self.TxtDocName.Text = os.path.basename(doc.PathName) or doc.Title
        except Exception:
            self.TxtDocName.Text = doc.Title

        SELECTION_CALLBACK[0] = self._on_selection_changed

        self._wire_events()
        self._load_defaults()
        self._load_items(force=True)
        self._load_profiles()

    # ------------------------------------------------------------------ #
    #  Wiring
    # ------------------------------------------------------------------ #
    def _wire_events(self):
        self.RbSheets.Checked += self.on_mode_changed
        self.RbViews.Checked += self.on_mode_changed
        self.TxtSearch.TextChanged += self.on_search
        self.ChkOnlyActive.Checked += self.on_search
        self.ChkOnlyActive.Unchecked += self.on_search
        self.ChkGroupSeries.Checked += self.on_search
        self.ChkGroupSeries.Unchecked += self.on_search
        self.CmbVSSet.SelectionChanged += self.on_vsset_changed
        self.BtnSaveVSSet.Click += self.on_save_vsset

        self.BtnSelAll.Click += lambda s, e: self._set_all(True)
        self.BtnSelNone.Click += lambda s, e: self._set_all(False)
        self.BtnSelInvert.Click += self.on_invert
        self.BtnResetNames.Click += self.on_reset_names
        self.BtnNaming.Click += self.on_open_naming
        self.BtnReload.Click += lambda s, e: self._load_items(force=True)

        self.BtnNext1.Click += lambda s, e: self._goto(1)
        self.BtnBack2.Click += lambda s, e: self._goto(0)
        self.BtnNext2.Click += lambda s, e: self._goto(2)
        self.BtnBack3.Click += lambda s, e: self._goto(1)


        self.BtnBrowse.Click += self.on_browse
        self.BtnOpenFolder.Click += self.on_open_folder
        self.BtnCreate.Click += self.on_create
        self.BtnCancelExport.Click += self.on_cancel

        self.BtnProfileSave.Click += self.on_profile_save
        self.BtnProfileBrowse.Click += self.on_profile_browse
        self.BtnProfileFolder.Click += self.on_profile_folder
        self.BtnProfileDelete.Click += self.on_profile_delete
        self.CmbProfile.SelectionChanged += self.on_profile_changed

        # shift/ctrl row selection drives the tick boxes
        self.GridItems.SelectionChanged += self.on_grid_selection_changed
        self.GridItems.PreviewKeyDown += self.on_grid_key
        self.GridItems.PreviewMouseLeftButtonDown += self.on_grid_mouse_down

        # per-series "select all" tick box lives in the group header
        # template, so it's wired by bubbling rather than by name
        self.GridItems.AddHandler(CheckBox.ClickEvent,
                                   RoutedEventHandler(self.on_series_checkbox_click))
        self.GridItems.AddHandler(FrameworkElement.LoadedEvent,
                                   RoutedEventHandler(self.on_series_header_loaded), True)

        # header "select all" checkbox lives in a column header template
        self.Loaded += self.on_loaded

    def on_loaded(self, sender, args):
        try:
            hdr = self.GridItems.Columns[0].Header
            hdr.Checked += lambda s, e: self._set_all(True)
            hdr.Unchecked += lambda s, e: self._set_all(False)
        except Exception:
            pass

    def _goto(self, index):
        self.Tabs.SelectedIndex = index

    # ------------------------------------------------------------------ #
    #  Data loading
    # ------------------------------------------------------------------ #
    def _load_items(self, force=False):
        sheets_mode = bool(self.RbSheets.IsChecked)

        if sheets_mode:
            if self._sheet_cache is None or force:
                build_titleblock_map()
                collected = FilteredElementCollector(doc) \
                    .OfClass(ViewSheet).WhereElementIsNotElementType().ToElements()
                rows = [ExportItem(s, True) for s in collected
                        if not s.IsTemplate and s.CanBePrinted]
                rows.sort(key=lambda r: self._natural(r.Number))
                self._sheet_cache = rows
            self._all_items = self._sheet_cache
        else:
            if self._view_cache is None or force:
                skip = (ViewType.DrawingSheet, ViewType.ProjectBrowser,
                        ViewType.SystemBrowser, ViewType.Internal, ViewType.Undefined)
                collected = FilteredElementCollector(doc) \
                    .OfClass(View).WhereElementIsNotElementType().ToElements()
                rows = [ExportItem(v, False) for v in collected
                        if not v.IsTemplate and v.ViewType not in skip and v.CanBePrinted]
                rows.sort(key=lambda r: (r.ViewTypeName, self._natural(r.Name)))
                self._view_cache = rows
            self._all_items = self._view_cache

        self._configure_columns(sheets_mode)
        self._refresh_filter()
        self._load_vssets()
        self._load_naming_params()
        self._update_status()

    @staticmethod
    def _natural(text):
        return [int(t) if t.isdigit() else t.lower()
                for t in re.split(r"(\d+)", text or "")]

    def _configure_columns(self, sheets_mode):
        cols = self.GridItems.Columns
        cols[1].Header = "Sheet Number" if sheets_mode else "View Type"
        cols[2].Header = "Sheet Name" if sheets_mode else "View Name"
        cols[3].Visibility = Visibility.Visible if sheets_mode else Visibility.Collapsed
        cols[4].Visibility = Visibility.Visible if sheets_mode else Visibility.Collapsed
        # a Binding becomes sealed once used, so always assign a fresh one
        cols[1].Binding = Binding("Number" if sheets_mode else "ViewTypeName")
        # grouping only makes sense for sheets - Views have no Series Range
        self.ChkGroupSeries.IsEnabled = sheets_mode
        if not sheets_mode:
            self.ChkGroupSeries.IsChecked = False

    def _refresh_filter(self):
        term = (self.TxtSearch.Text or "").strip().lower()
        only_active = bool(self.ChkOnlyActive.IsChecked)
        active_id = None
        if only_active:
            try:
                active_id = eid_value(uidoc.ActiveView.Id)
            except Exception:
                active_id = None

        rows = self._all_items
        if term:
            rows = [r for r in rows if r.matches(term)]
        if active_id is not None:
            rows = [r for r in rows if r.IdValue == active_id or r.IsSelected]

        sheets_mode = bool(self.RbSheets.IsChecked)
        group_on = sheets_mode and bool(self.ChkGroupSeries.IsChecked)

        if group_on:
            # groups appear / sort in source order, so pre-sort by
            # (Series Range, natural sheet number) before handing rows
            # to the CollectionView - this mirrors the Project Browser.
            rows = sorted(rows, key=lambda r: (self._natural(r.SeriesRange),
                                               self._natural(r.Number)))

        collection = ObservableCollection[object]()
        for r in rows:
            collection.Add(r)

        # swapping ItemsSource clears SelectedItems and raises SelectionChanged;
        # suppress the handler so existing ticks survive a search/filter
        self._syncing = True
        try:
            if group_on:
                cvs = CollectionViewSource()
                cvs.Source = collection
                cvs.GroupDescriptions.Add(PropertyGroupDescription("SeriesRange"))
                self.GridItems.ItemsSource = cvs.View
            else:
                self.GridItems.ItemsSource = collection
        finally:
            self._syncing = False
        self._checkbox_click = False
        self._visible_rows = rows

    def _load_vssets(self):
        self.CmbVSSet.Items.Clear()
        self.CmbVSSet.Items.Add("<All>")
        try:
            sets = FilteredElementCollector(doc).OfClass(ViewSheetSet).ToElements()
            for s in sorted(sets, key=lambda x: x.Name):
                self.CmbVSSet.Items.Add(s.Name)
        except Exception:
            pass
        self.CmbVSSet.SelectedIndex = 0

    def _load_naming_params(self):
        # fill the Custom File Name column straight away so the grid is never
        # blank - the user can still overtype any individual cell
        self._fill_names(only_empty=True)
        self._update_rule_summary()

    def _available_params(self):
        if self._param_cache is None:
            self._param_cache = collect_parameters(self._all_items)
        return self._param_cache

    def _naming_config(self):
        cfg = dict(self._rule)
        cfg["tokens"] = list(self._rule.get("tokens", []))
        return cfg

    def _update_rule_summary(self):
        rule = self._rule
        tokens = rule.get("tokens", [])
        if not tokens:
            self.TxtRuleSummary.Text = "no naming rule set"
            return
        sep = rule.get("separator", "_")
        sep = "" if sep == "none" else sep
        parts = sep.join("<%s>" % t for t in tokens)
        self.TxtRuleSummary.Text = "Rule:  %s%s%s" % (
            rule.get("prefix", ""), parts, rule.get("suffix", ""))

    # ------------------------------------------------------------------ #
    #  Custom File Name dialog
    # ------------------------------------------------------------------ #
    def on_open_naming(self, sender, args):
        sample = None
        chosen = self._checked_items()
        if chosen:
            sample = chosen[0]
        elif self._all_items:
            sample = self._all_items[0]

        dlg = NamingWindow(self._available_params(), self._naming_config(),
                           sample, self.TxtFolder.Text)
        try:
            dlg.Owner = self
        except Exception:
            pass
        dlg.ShowDialog()

        if dlg.result is None:
            return                       # cancelled - leave everything as it was
        self._rule = dlg.result
        self._profiles = self._rule_files()
        self._refresh_profile_combo()
        self._update_rule_summary()
        self._fill_names(only_empty=False)
        self.GridItems.Items.Refresh()
        self._update_status()

    def on_reset_names(self, sender, args):
        self._fill_names(only_empty=False)
        self.GridItems.Items.Refresh()

    def _fill_names(self, only_empty=True):
        """Write the current naming rule into the Custom File Name column."""
        if not self._all_items:
            return
        cfg = self._naming_config()
        if not cfg["tokens"]:
            return
        for item in self._all_items:
            if only_empty and item.CustomFileName:
                continue          # never clobber a manual edit
            item.CustomFileName = build_name(item, **cfg)

    # ------------------------------------------------------------------ #
    #  Selection tab handlers
    # ------------------------------------------------------------------ #
    def on_mode_changed(self, sender, args):
        if not self.IsLoaded:
            return
        self._load_items()

    def on_search(self, sender, args):
        if not self.IsLoaded:
            return
        self._refresh_filter()

    def on_vsset_changed(self, sender, args):
        if not self.IsLoaded or self.CmbVSSet.SelectedIndex <= 0:
            return
        name = self.CmbVSSet.SelectedItem
        try:
            target = None
            for s in FilteredElementCollector(doc).OfClass(ViewSheetSet).ToElements():
                if s.Name == name:
                    target = s
                    break
            if not target:
                return
            ids = set(eid_value(v.Id) for v in target.Views)
            SELECTION_CALLBACK[0] = None
            self._syncing = True
            try:
                for item in self._all_items:
                    item.IsSelected = item.IdValue in ids
                self.GridItems.UnselectAll()
            finally:
                self._syncing = False
                SELECTION_CALLBACK[0] = self._on_selection_changed
                self._on_selection_changed()
        except Exception as ex:
            logger.debug("V/S set filter failed: %s", ex)

    def on_save_vsset(self, sender, args):
        chosen = self._checked_items()
        if not chosen:
            forms.alert("Select at least one sheet or view first.", title="Sheet Export")
            return
        name = forms.ask_for_string(default="Sheet Export Set",
                                    prompt="Name for the new View/Sheet Set:",
                                    title="Save V/S Set")
        if not name:
            return
        try:
            with revit.Transaction("Save View/Sheet Set"):
                vs = ViewSet()
                for item in chosen:
                    vs.Insert(item.Element)
                pm = doc.PrintManager
                pm.PrintRange = DB.PrintRange.Select
                vss = pm.ViewSheetSetting
                vss.CurrentViewSheetSet.Views = vs
                vss.SaveAs(name)
            self._load_vssets()
            forms.alert("Saved View/Sheet Set '%s'." % name, title="Sheet Export")
        except Exception as ex:
            forms.alert("Could not save the set:\n%s" % ex, title="Sheet Export")

    def on_grid_mouse_down(self, sender, args):
        """Detect clicks that land on a tick box.

        Clicking a checkbox also moves the row highlight; without this the
        selection handler would immediately re-tick a box the user just cleared.
        """
        self._checkbox_click = False
        try:
            src = args.OriginalSource
            while src is not None:
                if isinstance(src, CheckBox):
                    self._checkbox_click = True
                    return
                src = VisualTreeHelper.GetParent(src)
        except Exception:
            self._checkbox_click = False

    def on_grid_selection_changed(self, sender, args):
        """Highlighting rows ticks them - ticks ACCUMULATE.

        Rows that leave the highlight are deliberately left ticked: selecting
        A1-A5, then A9-A12, must end up with all nine ticked. Unticking is done
        with the tick box itself, Space, or the Clear button.
        """
        if self._syncing:
            return
        if getattr(self, "_checkbox_click", False):
            # the user clicked the tick box itself - leave its value alone
            self._checkbox_click = False
            self._on_selection_changed()
            return
        self._syncing = True
        SELECTION_CALLBACK[0] = None
        try:
            for row in args.AddedItems:
                if isinstance(row, ExportItem):
                    row.IsSelected = True
        finally:
            SELECTION_CALLBACK[0] = self._on_selection_changed
            self._syncing = False
        self._on_selection_changed()

    def on_grid_key(self, sender, args):
        """Space toggles the ticks of every highlighted row."""
        if args.Key != Key.Space:
            return
        rows = [r for r in self.GridItems.SelectedItems
                if isinstance(r, ExportItem)]
        if not rows:
            return
        target = not all(r.IsSelected for r in rows)
        self._syncing = True
        SELECTION_CALLBACK[0] = None
        try:
            for row in rows:
                row.IsSelected = target
        finally:
            SELECTION_CALLBACK[0] = self._on_selection_changed
            self._syncing = False
        self._checkbox_click = False
        self._on_selection_changed()
        args.Handled = True

    def _set_all(self, state):
        SELECTION_CALLBACK[0] = None          # suppress per-row recount
        self._syncing = True
        try:
            for row in getattr(self, "_visible_rows", self._all_items):
                row.IsSelected = state
            # drop the blue highlight, otherwise re-selecting the same range
            # raises no SelectionChanged and looks like nothing happened
            self.GridItems.UnselectAll()
        finally:
            self._syncing = False
            SELECTION_CALLBACK[0] = self._on_selection_changed
        self._on_selection_changed()

    def on_invert(self, sender, args):
        SELECTION_CALLBACK[0] = None
        self._syncing = True
        try:
            for row in getattr(self, "_visible_rows", self._all_items):
                row.IsSelected = not row.IsSelected
            self.GridItems.UnselectAll()
        finally:
            self._syncing = False
            SELECTION_CALLBACK[0] = self._on_selection_changed
        self._on_selection_changed()

    def on_series_checkbox_click(self, sender, args):
        """Header tick box on a 'Series: ...' group - selects / clears every
        sheet in that series in one click, regardless of what state WPF's
        own tri-state cycling left the box in."""
        box = args.OriginalSource
        if not isinstance(box, CheckBox):
            return
        group = box.Tag
        if not isinstance(group, CollectionViewGroup):
            return
        items = [it for it in group.Items if isinstance(it, ExportItem)]
        if not items:
            return
        # toggle relative to the state BEFORE this click, ignoring whatever
        # WPF's built-in checked/unchecked/indeterminate cycle just set
        new_state = not all(it.IsSelected for it in items)

        SELECTION_CALLBACK[0] = None
        self._syncing = True
        try:
            for it in items:
                it.IsSelected = new_state
        finally:
            self._syncing = False
            SELECTION_CALLBACK[0] = self._on_selection_changed
        box.IsChecked = new_state
        self._on_selection_changed()
        args.Handled = True

    def on_series_header_loaded(self, sender, args):
        """A group header tick box was just realised (initial load, or
        scrolled back into view under virtualization) - give it the right
        checked / unchecked / indeterminate state straight away."""
        box = args.OriginalSource
        if isinstance(box, CheckBox) and isinstance(box.Tag, CollectionViewGroup):
            self._sync_one_group_checkbox(box)

    def _sync_one_group_checkbox(self, box):
        group = box.Tag
        items = [it for it in group.Items if isinstance(it, ExportItem)]
        if not items:
            state = False
        else:
            selected = sum(1 for it in items if it.IsSelected)
            if selected == 0:
                state = False
            elif selected == len(items):
                state = True
            else:
                state = None
        if box.IsChecked != state:
            box.IsChecked = state

    def _sync_group_checkboxes(self):
        """Walk the realised visual tree and refresh every series tick box.
        Cheap enough to call after any bulk selection change - only the
        currently-materialised (visible-ish) group headers are touched."""
        if not bool(self.ChkGroupSeries.IsChecked):
            return
        try:
            self._walk_for_series_checkboxes(self.GridItems)
        except Exception:
            pass

    def _walk_for_series_checkboxes(self, parent):
        count = VisualTreeHelper.GetChildrenCount(parent)
        for i in range(count):
            child = VisualTreeHelper.GetChild(parent, i)
            if isinstance(child, CheckBox) and isinstance(getattr(child, "Tag", None), CollectionViewGroup):
                self._sync_one_group_checkbox(child)
            self._walk_for_series_checkboxes(child)

    def _on_selection_changed(self):
        self._update_status()
        self._sync_group_checkboxes()

    def _checked_items(self):
        return [i for i in self._all_items if i.IsSelected]

    def _update_status(self):
        chosen = self._checked_items()
        sheets = sum(1 for i in chosen if i.IsSheet)
        views = len(chosen) - sheets
        msg = "%d sheets and %d views selected. Total: %d" % (sheets, views, len(chosen))
        self.TxtStatusSel.Text = msg
        self.TxtStatusFmt.Text = msg
        self.TxtStatusCreate.Text = msg

    # ------------------------------------------------------------------ #
    #  Naming handlers
    # ------------------------------------------------------------------ #
    def _resolve_filename(self, item, cfg):
        if item.CustomFileName:
            return sanitize(item.CustomFileName, cfg["do_sanitize"])
        return build_name(item, **cfg)

    # ------------------------------------------------------------------ #
    #  Create tab handlers
    # ------------------------------------------------------------------ #


    def on_browse(self, sender, args):
        dlg = FolderBrowserDialog()
        dlg.Description = "Choose the export folder"
        if self.TxtFolder.Text and os.path.isdir(self.TxtFolder.Text):
            dlg.SelectedPath = self.TxtFolder.Text
        if dlg.ShowDialog() == DialogResult.OK:
            self.TxtFolder.Text = dlg.SelectedPath

    def on_open_folder(self, sender, args):
        path = self.TxtFolder.Text
        if path and os.path.isdir(path):
            os.startfile(path)

    def on_cancel(self, sender, args):
        self._cancel = True
        self._log("Cancel requested - finishing current file...")

    def _log(self, message):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.LstLog.Items.Add("[%s] %s" % (stamp, message))
        self.LstLog.ScrollIntoView(self.LstLog.Items[self.LstLog.Items.Count - 1])
        self._pump()

    def _pump(self):
        """Keep the UI responsive during long exports."""
        try:
            self.Dispatcher.Invoke(System.Action(lambda: None),
                                   DispatcherPriority.Background)
        except Exception:
            pass

    def _set_progress(self, done, total, label):
        self.Progress.Maximum = max(total, 1)
        self.Progress.Value = done
        self.TxtProgress.Text = "%d / %d  %s" % (done, total, label)
        self._pump()

    def _output_dir(self, fmt):
        root = self.TxtFolder.Text
        if self.ChkSubfolderDate.IsChecked:
            root = os.path.join(root, datetime.datetime.now().strftime("%Y-%m-%d"))
        if self.ChkSubfolderPerFormat.IsChecked:
            root = os.path.join(root, fmt)
        if not os.path.isdir(root):
            os.makedirs(root)
        return root

    def _series_subdir(self, base_folder, item):
        """When 'sub-folder per series' is on, route this sheet's file into
        <base_folder>\\<Series Range>\\ instead of the flat base folder.
        Views never carry a Series Range, so they always stay in the base
        folder, and callers doing a combined/merged export never call this
        per item in the first place."""
        if not bool(self.ChkSubfolderPerSeries.IsChecked):
            return base_folder
        if not item.IsSheet or not item.SeriesRange:
            return base_folder
        target = os.path.join(base_folder, sanitize(item.SeriesRange, True))
        if not os.path.isdir(target):
            os.makedirs(target)
        return target

    @staticmethod
    def _rel_name(base_folder, item_folder, fname_with_ext):
        """File name for the log/status list - includes the series
        sub-folder when one was used, so the CSV log reads e.g.
        '01 - Master plan\\0000_COVER SHEET.pdf' instead of just the name."""
        if item_folder == base_folder:
            return fname_with_ext
        return os.path.join(os.path.relpath(item_folder, base_folder), fname_with_ext)

    def _selected_formats(self):
        pairs = [("PDF", self.ChkPDF), ("DWG", self.ChkDWG), ("DGN", self.ChkDGN),
                 ("DWF", self.ChkDWF), ("NWC", self.ChkNWC), ("IFC", self.ChkIFC),
                 ("IMG", self.ChkIMG)]
        return [name for name, box in pairs if box.IsChecked]

    # ------------------------------------------------------------------ #
    #  EXPORT
    # ------------------------------------------------------------------ #
    def on_create(self, sender, args):
        if self._busy:
            return

        formats = self._selected_formats()
        if not formats:
            forms.alert("Pick at least one export format on the Format tab.",
                        title="Sheet Export")
            self._goto(1)
            return

        # NWC and IFC are model/3D-view exports - they never use the sheet list
        MODEL_FORMATS = ("NWC", "IFC")
        sheet_formats = [f for f in formats if f not in MODEL_FORMATS]

        items = self._checked_items()
        if not items and sheet_formats:
            forms.alert("Nothing selected.\n\nTick at least one sheet or view "
                        "on the Selection tab for: %s"
                        % ", ".join(sheet_formats), title="Sheet Export")
            self._goto(0)
            return

        folder = self.TxtFolder.Text
        if not folder:
            forms.alert("Choose an output folder first.", title="Sheet Export")
            return
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except Exception as ex:
            forms.alert("Cannot create the output folder:\n%s" % ex,
                        title="Sheet Export")
            return

        self._busy = True
        self._cancel = False
        self.BtnCreate.IsEnabled = False
        self.BtnCancelExport.IsEnabled = True
        self.Cursor = Cursors.Wait
        self.LstLog.Items.Clear()

        cfg = self._naming_config()
        records = []
        started = datetime.datetime.now()

        self._log("Export started - %d item(s), format(s): %s"
                  % (len(items), ", ".join(formats)))
        if (bool(self.ChkSubfolderPerSeries.IsChecked)
                and ((("PDF" in formats) and bool(self.ChkCombinePDF.IsChecked))
                     or (("DWF" in formats) and bool(self.ChkDwfMerge.IsChecked)))):
            self._log("Note: 'sub-folder per series' has no effect on a "
                      "combined PDF or merged DWF - those produce one file "
                      "for the whole selection.")

        try:
            for fmt in formats:
                if self._cancel:
                    break
                handler = getattr(self, "_export_" + fmt.lower())
                handler(items, cfg, records)
        except Exception:
            self._log("FATAL: " + traceback.format_exc().splitlines()[-1])
            logger.error(traceback.format_exc())
        finally:
            self._busy = False
            self.BtnCreate.IsEnabled = True
            self.BtnCancelExport.IsEnabled = False
            self.Cursor = Cursors.Arrow

        elapsed = (datetime.datetime.now() - started).total_seconds()
        ok = sum(1 for r in records if r[3] == "OK")
        self._log("Finished in %.1f s - %d succeeded, %d failed."
                  % (elapsed, ok, len(records) - ok))
        self.TxtProgress.Text = "Done - %d file(s) in %.1f s" % (ok, elapsed)

        if self.ChkLog.IsChecked and records:
            self._write_log(records)
        if self.ChkOpenWhenDone.IsChecked and not self._cancel:
            try:
                os.startfile(folder)
            except Exception:
                pass

    def _write_log(self, records):
        path = os.path.join(self.TxtFolder.Text,
                            "SheetExport_%s.csv"
                            % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            with codecs.open(path, "w", "utf-8") as fh:
                fh.write("Format,Item,File,Status,Message\n")
                for rec in records:
                    fh.write(",".join('"%s"' % str(c).replace('"', "'")
                                      for c in rec) + "\n")
            self._log("Log written: %s" % path)
        except Exception as ex:
            self._log("Log failed: %s" % ex)

    # ---------------------------- PDF ---------------------------------- #
    def _export_pdf(self, items, cfg, records):
        """Native PDF export (Revit 2022+ PDFExportOptions) - no printer needed."""
        folder = self._output_dir("PDF")
        total = len(items)

        try:
            PDFExportOptions = DB.PDFExportOptions
        except AttributeError:
            self._log("PDF: native exporter unavailable on this build.")
            records.append(("PDF", "-", "-", "FAIL", "PDFExportOptions missing"))
            return

        combine = bool(self.ChkCombinePDF.IsChecked)

        # PDFExportOptions differs between releases: set every property
        # defensively so one unsupported member can never abort the export.
        unsupported = []

        def put(opt, name, value):
            if not hasattr(opt, name):
                if name not in unsupported:
                    unsupported.append(name)
                return False
            try:
                setattr(opt, name, value)
                return True
            except Exception as ex:
                if name not in unsupported:
                    unsupported.append("%s (%s)" % (name, ex))
                return False

        def base_options():
            opt = PDFExportOptions()
            put(opt, "ExportQuality", self._pdf_quality())
            put(opt, "ColorDepth", self._pdf_colors())
            put(opt, "RasterQuality", self._pdf_raster())
            put(opt, "ViewLinksInBlue", bool(self.ChkViewLinksBlue.IsChecked))
            put(opt, "HideReferencePlane", bool(self.ChkHideRefPlanes.IsChecked))
            put(opt, "HideScopeBoxes", bool(self.ChkHideScopeBoxes.IsChecked))
            put(opt, "HideCropBoundaries", bool(self.ChkHideCropBound.IsChecked))
            put(opt, "HideUnreferencedViewTags", bool(self.ChkHideUnrefTags.IsChecked))
            put(opt, "MaskCoincidentLines", bool(self.ChkMaskCoincident.IsChecked))
            put(opt, "ReplaceHalftoneWithThinLines",
                bool(self.ChkReplaceHalftone.IsChecked))
            put(opt, "RegionEdgesMaskCoincidentLines",
                bool(self.ChkRegionEdges.IsChecked))
            put(opt, "StopOnError", False)

            # NOTE: there is no HiddenLineViews on PDFExportOptions - that
            # property belongs to PrintParameters. The equivalent here is
            # AlwaysUseRaster (raster vs vector processing).
            put(opt, "AlwaysUseRaster", self.CmbHiddenLine.SelectedIndex == 1)

            # ---- paper size ----
            if self.ChkUseSheetSize.IsChecked:
                # Default = take the size from the title block
                try:
                    put(opt, "PaperFormat", DB.ExportPaperFormat.Default)
                except Exception:
                    pass
            else:
                fmt_name = str(self.CmbPaperSize.SelectedItem or "").replace(" ", "_")
                fmt = getattr(DB.ExportPaperFormat, fmt_name, None) \
                    or getattr(DB.ExportPaperFormat, fmt_name.replace("_", ""), None)
                if fmt is not None:
                    put(opt, "PaperFormat", fmt)

            # ---- orientation ----
            idx = self.CmbOrientation.SelectedIndex
            if idx > 0:
                put(opt, "PaperOrientation",
                    DB.PageOrientationType.Portrait if idx == 1
                    else DB.PageOrientationType.Landscape)
            else:
                put(opt, "PaperOrientation", DB.PageOrientationType.Auto)

            # ---- placement ----
            if self.RbCenter.IsChecked:
                put(opt, "PaperPlacement", DB.PaperPlacementType.Center)
            else:
                put(opt, "PaperPlacement", DB.PaperPlacementType.LowerLeft)
                # PDFExportOptions uses an explicit offset (in feet), not
                # PrintParameters' MarginType.
                ox, oy = self._pdf_offsets()
                put(opt, "OriginOffsetX", ox)
                put(opt, "OriginOffsetY", oy)

            # ---- zoom ----
            if self.RbFitToPage.IsChecked:
                put(opt, "ZoomType", DB.ZoomType.FitToPage)
            else:
                put(opt, "ZoomType", DB.ZoomType.Zoom)
                try:
                    pct = int(float(self.TxtZoom.Text or 100))
                except Exception:
                    pct = 100
                put(opt, "ZoomPercentage", max(1, min(1000, pct)))
            return opt

        if combine:
            name = sanitize(self.TxtCombinedName.Text or "Combined", True)
            opt = base_options()
            opt.Combine = True
            opt.FileName = name
            ids = List[ElementId]([i.Id for i in items])
            self._set_progress(0, 1, "PDF (combined)")
            target = os.path.join(folder, name + ".pdf")
            before_files = set(os.listdir(folder)) if os.path.exists(folder) else set()
            try:
                doc.Export(folder, ids, opt)
                if not os.path.exists(target) and os.path.exists(folder):
                    after_files = set(os.listdir(folder))
                    new_files = [f for f in (after_files - before_files) if f.lower().endswith(".pdf")]
                    if new_files:
                        new_pdf = max(new_files, key=lambda f: os.path.getmtime(os.path.join(folder, f)))
                        new_pdf_path = os.path.join(folder, new_pdf)
                        if new_pdf_path != target:
                            try:
                                if os.path.exists(target):
                                    os.remove(target)
                                os.rename(new_pdf_path, target)
                            except Exception:
                                pass
                records.append(("PDF", "%d items" % total,
                                os.path.join(folder, name + ".pdf"), "OK", ""))
                self._log("PDF combined -> %s.pdf" % name)
            except Exception as ex:
                records.append(("PDF", "combined", name, "FAIL", str(ex)))
                self._log("PDF combined FAILED: %s" % ex)
            self._set_progress(1, 1, "PDF (combined)")
            return

        # one file per sheet, named exactly as the Custom File Name column
        for index, item in enumerate(items, 1):
            if self._cancel:
                break
            fname = self._resolve_filename(item, cfg)
            item_folder = self._series_subdir(folder, item)
            self._set_progress(index - 1, total, "PDF  " + fname)
            try:
                opt = base_options()
                opt.Combine = False
                opt.FileName = fname
                target = os.path.join(item_folder, fname + ".pdf")
                if os.path.exists(target):
                    if self.ChkOverwrite.IsChecked:
                        try:
                            os.remove(target)
                        except Exception:
                            pass
                    else:
                        fname = os.path.splitext(
                            os.path.basename(unique_path(target, False)))[0]
                        opt.FileName = fname
                        target = os.path.join(item_folder, fname + ".pdf")

                before_files = set(os.listdir(item_folder)) if os.path.exists(item_folder) else set()

                ids = List[ElementId]([item.Id])
                doc.Export(item_folder, ids, opt)

                # Ensure exported PDF file is renamed to exact target name if Revit named it differently
                if not os.path.exists(target) and os.path.exists(item_folder):
                    after_files = set(os.listdir(item_folder))
                    new_files = [f for f in (after_files - before_files) if f.lower().endswith(".pdf")]
                    if new_files:
                        new_pdf = max(new_files, key=lambda f: os.path.getmtime(os.path.join(item_folder, f)))
                        new_pdf_path = os.path.join(item_folder, new_pdf)
                        if new_pdf_path != target:
                            try:
                                if os.path.exists(target):
                                    os.remove(target)
                                os.rename(new_pdf_path, target)
                            except Exception:
                                try:
                                    os.replace(new_pdf_path, target)
                                except Exception as ex_ren:
                                    self._log("PDF rename warning (%s -> %s): %s" % (new_pdf, fname + ".pdf", ex_ren))

                records.append(("PDF", item.Number or item.Name,
                                self._rel_name(folder, item_folder, fname + ".pdf"), "OK", ""))
            except Exception as ex:
                records.append(("PDF", item.Number or item.Name,
                                fname, "FAIL", str(ex)))
                self._log("PDF FAILED %s: %s" % (fname, ex))
            self._set_progress(index, total, "PDF  " + fname)
        if unsupported:
            self._log("PDF: settings not available on Revit %s (ignored): %s"
                      % (REVIT_VERSION, ", ".join(unsupported)))
        self._log("PDF: %d file(s) -> %s" % (total, folder))

    def _pdf_offsets(self):
        """Corner offset in feet for 'Offset from corner' placement."""
        presets = {0: (0.0, 0.0),          # No Margin
                   1: (0.0, 0.0)}          # Printer Limit -> let Revit decide
        idx = max(0, self.CmbMargin.SelectedIndex)
        if idx in presets:
            return presets[idx]
        try:                                # User Defined - value shown in mm
            mm = float(self.TxtMargin.Text or 0)
        except Exception:
            mm = 0.0
        feet = mm / 304.8
        return (feet, feet)

    def _pdf_quality(self):
        try:
            return [DB.PDFExportQualityType.DPI72, DB.PDFExportQualityType.DPI150,
                    DB.PDFExportQualityType.DPI300, DB.PDFExportQualityType.DPI600
                    ][self.CmbRaster.SelectedIndex]
        except Exception:
            return DB.PDFExportQualityType.DPI300

    def _pdf_colors(self):
        idx = self.CmbColors.SelectedIndex
        if idx == 1:
            return DB.ColorDepthType.GrayScale
        if idx == 2:
            return DB.ColorDepthType.BlackLine
        return DB.ColorDepthType.Color

    def _pdf_raster(self):
        return [DB.RasterQualityType.Low, DB.RasterQualityType.Medium,
                DB.RasterQualityType.High, DB.RasterQualityType.Presentation
                ][max(0, self.CmbRaster.SelectedIndex)]

    # ---------------------------- DWG ---------------------------------- #
    def _export_dwg(self, items, cfg, records):
        folder = self._output_dir("DWG")
        total = len(items)

        opt = None
        setup_name = self.CmbDwgSetup.SelectedItem
        if setup_name and setup_name != "<Default>":
            try:
                setup = ExportDWGSettings.FindByName(doc, str(setup_name))
                if setup:
                    opt = setup.GetDWGExportOptions()
            except Exception:
                opt = None
        if opt is None:
            opt = DWGExportOptions()

        def put(name, value):
            try:
                if hasattr(opt, name):
                    setattr(opt, name, value)
            except Exception as ex:
                self._log("DWG: could not set %s (%s)" % (name, ex))

        # "views on sheets as external references" is the inverse of merging
        merged = bool(self.ChkDwgMergeViews.IsChecked)
        if self.ChkDwgXrefViews.IsChecked:
            merged = False
        put("MergedViews", merged)
        put("SharedCoords", bool(self.ChkDwgSharedCoords.IsChecked))
        try:
            opt.FileVersion = [ACADVersion.R2000, ACADVersion.R2004,
                               ACADVersion.R2007, ACADVersion.R2010,
                               ACADVersion.R2013, ACADVersion.R2018
                               ][self.CmbDwgVersion.SelectedIndex]
        except Exception:
            pass

        for index, item in enumerate(items, 1):
            if self._cancel:
                break
            fname = self._resolve_filename(item, cfg)
            item_folder = self._series_subdir(folder, item)
            self._set_progress(index - 1, total, "DWG  " + fname)
            try:
                ids = List[ElementId]([item.Id])
                doc.Export(item_folder, fname, ids, opt)
                records.append(("DWG", item.Number or item.Name,
                                self._rel_name(folder, item_folder, fname + ".dwg"), "OK", ""))
            except Exception as ex:
                records.append(("DWG", item.Number or item.Name, fname, "FAIL", str(ex)))
                self._log("DWG FAILED %s: %s" % (fname, ex))
            self._set_progress(index, total, "DWG  " + fname)
        self._log("DWG: %d file(s) -> %s" % (total, folder))

    # ---------------------------- DGN ---------------------------------- #
    def _export_dgn(self, items, cfg, records):
        folder = self._output_dir("DGN")
        total = len(items)

        opt = None
        setup_name = self.CmbDgnSetup.SelectedItem
        if setup_name and setup_name != "<Default>":
            try:
                setup = ExportDGNSettings.FindByName(doc, str(setup_name))
                if setup:
                    opt = setup.GetDGNExportOptions()
            except Exception:
                opt = None
        if opt is None:
            opt = DGNExportOptions()
        try:
            opt.MergedViews = bool(self.ChkDgnMergeViews.IsChecked)
            opt.SharedCoords = bool(self.ChkDgnSharedCoords.IsChecked)
        except Exception:
            pass

        for index, item in enumerate(items, 1):
            if self._cancel:
                break
            fname = self._resolve_filename(item, cfg)
            item_folder = self._series_subdir(folder, item)
            self._set_progress(index - 1, total, "DGN  " + fname)
            try:
                ids = List[ElementId]([item.Id])
                doc.Export(item_folder, fname, ids, opt)
                records.append(("DGN", item.Number or item.Name,
                                self._rel_name(folder, item_folder, fname + ".dgn"), "OK", ""))
            except Exception as ex:
                records.append(("DGN", item.Number or item.Name, fname, "FAIL", str(ex)))
                self._log("DGN FAILED %s: %s" % (fname, ex))
            self._set_progress(index, total, "DGN  " + fname)
        self._log("DGN: %d file(s) -> %s" % (total, folder))

    # ---------------------------- DWF ---------------------------------- #
    def _export_dwf(self, items, cfg, records):
        """DWF / DWFx export.

        There is no Document.ExportDWF() - the correct overload is
        Document.Export(folder, name, ViewSet, DWFExportOptions).
        DWFx uses the derived DWFXExportOptions class.
        Some Revit builds also require an open transaction for DWF export.
        """
        folder = self._output_dir("DWF")
        is_dwfx = self.CmbDwfType.SelectedIndex == 1
        ext = ".dwfx" if is_dwfx else ".dwf"

        def make_options():
            if is_dwfx and hasattr(DB, "DWFXExportOptions"):
                opt = DB.DWFXExportOptions()
            else:
                opt = DWFExportOptions()
            for name, value in (
                    ("ExportObjectData", bool(self.ChkDwfModelGeom.IsChecked)),
                    ("ExportingAreas", bool(self.ChkDwfRoomsAreas.IsChecked)),
                    ("MergedViews", bool(self.ChkDwfMerge.IsChecked)),
                    ("CropBoxVisible", False)):
                try:
                    if hasattr(opt, name):
                        setattr(opt, name, value)
                except Exception:
                    pass
            try:
                opt.ImageQuality = [DWFImageQuality.Low, DWFImageQuality.Medium,
                                    DWFImageQuality.High
                                    ][self.CmbDwfQuality.SelectedIndex]
            except Exception:
                pass
            return opt

        def export_batch(rows, fname, out_folder):
            vs = ViewSet()
            for row in rows:
                if not row.Element.CanBePrinted:
                    continue
                vs.Insert(row.Element)
            if vs.IsEmpty:
                raise Exception("no printable views in selection")
            opt = make_options()
            # DWF export can require a modifiable document; roll back so the
            # model is never actually changed.
            trans = Transaction(doc, "DWF Export")
            trans.Start()
            try:
                ok = doc.Export(out_folder, fname, vs, opt)
            finally:
                trans.RollBack()
            if ok is False:
                raise Exception("Revit reported the DWF export as failed")

        # ---- merged: one file for everything ----
        if self.ChkDwfMerge.IsChecked:
            fname = sanitize(self.TxtCombinedName.Text or "Combined", True)
            self._set_progress(0, 1, "DWF (merged)")
            try:
                export_batch(items, fname, folder)
                records.append(("DWF", "%d items" % len(items),
                                fname + ext, "OK", ""))
                self._log("DWF merged -> %s%s  (Revit may append '-' to the name)"
                          % (fname, ext))
            except Exception as ex:
                records.append(("DWF", "merged", fname, "FAIL", str(ex)))
                self._log("DWF merged FAILED: %s" % ex)
            self._set_progress(1, 1, "DWF (merged)")
            return

        # ---- one file per sheet/view ----
        total = len(items)
        for index, item in enumerate(items, 1):
            if self._cancel:
                break
            fname = self._resolve_filename(item, cfg)
            item_folder = self._series_subdir(folder, item)
            self._set_progress(index - 1, total, "DWF  " + fname)
            try:
                export_batch([item], fname, item_folder)
                records.append(("DWF", item.Number or item.Name,
                                self._rel_name(folder, item_folder, fname + ext), "OK", ""))
            except Exception as ex:
                records.append(("DWF", item.Number or item.Name,
                                fname, "FAIL", str(ex)))
                self._log("DWF FAILED %s: %s" % (fname, ex))
            self._set_progress(index, total, "DWF  " + fname)
        self._log("DWF: %d file(s) -> %s" % (total, folder))

    # ---------------------------- NWC ---------------------------------- #
    def _export_nwc(self, items, cfg, records):
        """Navisworks export.

        NWC is a geometry export: it uses the 3D view picked on the NWC tab,
        NOT the sheets ticked on the Selection tab.
        """
        folder = self._output_dir("NWC")

        try:
            available = DB.OptionalFunctionalityUtils \
                .IsNavisworksExporterAvailable()
        except Exception:
            available = hasattr(DB, "NavisworksExportOptions")
        if not available:
            msg = "Navisworks exporter add-in is not installed."
            self._log("NWC: " + msg)
            records.append(("NWC", "-", "-", "FAIL", msg))
            return

        view = self._picked_3d_view(self.CmbNwc3DView)
        by_view = bool(self.RbNwcScopeView.IsChecked)

        if by_view and view is None:
            msg = ("No 3D view selected on the NWC tab. Create a 3D view "
                   "or switch the scope to 'Entire model'.")
            self._log("NWC: " + msg)
            records.append(("NWC", "-", "-", "FAIL", msg))
            return

        # Diagnose the classic "No suitable geometry found" case before we call
        # the exporter: a host model whose geometry lives entirely in links,
        # exported with ExportLinks off, produces exactly that error.
        if by_view:
            host = links = 0
            try:
                host = FilteredElementCollector(doc, view.Id) \
                    .WhereElementIsNotElementType() \
                    .WhereElementIsViewIndependent().GetElementCount()
                links = FilteredElementCollector(doc, view.Id) \
                    .OfClass(DB.RevitLinkInstance).GetElementCount()
            except Exception:
                pass

            # links are counted in `host` too - find the real model content
            real = max(0, host - links)
            self._log("NWC: view '%s' - %d element(s), %d link instance(s)"
                      % (view.Name, host, links))

            if host == 0:
                msg = ("View '%s' is empty. Check its view filters, phase, "
                       "section box and discipline." % view.Name)
                self._log("NWC: " + msg)
                records.append(("NWC", view.Name, "-", "FAIL", "empty view"))
                return

            if links and real <= 2 and not self.ChkNwcExportLinks.IsChecked:
                # geometry is in the links but we are told not to convert them
                self._log("NWC: geometry is in linked models - enabling "
                          "'Convert linked files' automatically.")
                self.ChkNwcExportLinks.IsChecked = True

        fname = sanitize(self.TxtNwcName.Text or
                         (view.Name if (by_view and view) else doc.Title), True)

        opt = DB.NavisworksExportOptions()

        def put(name, value):
            try:
                if hasattr(opt, name):
                    setattr(opt, name, value)
            except Exception as ex:
                self._log("NWC: could not set %s (%s)" % (name, ex))

        if by_view:
            put("ExportScope", DB.NavisworksExportScope.View)
            put("ViewId", view.Id)
        else:
            put("ExportScope", DB.NavisworksExportScope.Model)

        put("Coordinates", DB.NavisworksCoordinates.Shared
            if self.CmbNwcCoords.SelectedIndex == 0
            else DB.NavisworksCoordinates.Internal)
        put("ConvertElementProperties", bool(self.ChkNwcExportProps.IsChecked))
        put("ExportLinks", bool(self.ChkNwcExportLinks.IsChecked))
        put("ExportParts", bool(self.ChkNwcExportParts.IsChecked))
        put("ExportRoomAsAttribute", bool(self.ChkNwcRoomAsAttr.IsChecked))
        put("ExportRoomGeometry", bool(self.ChkNwcRoomGeometry.IsChecked))
        put("DivideFileIntoLevels", bool(self.ChkNwcDivideLevels.IsChecked))
        put("ExportElementIds", bool(self.ChkNwcElementIds.IsChecked))
        put("ExportUrls", bool(self.ChkNwcUrls.IsChecked))
        put("FindMissingMaterials", bool(self.ChkNwcMissingMats.IsChecked))
        put("ConvertLights", bool(self.ChkNwcLights.IsChecked))
        put("ConvertLinkedCADFormats", bool(self.ChkNwcLinkedCAD.IsChecked))
        try:
            put("FacetingFactor", float(self.TxtNwcFaceting.Text or 1.0))
        except Exception:
            pass
        try:
            put("Parameters", [DB.NavisworksParameters.All,
                               DB.NavisworksParameters.Elements,
                               getattr(DB.NavisworksParameters, "None")
                               ][self.CmbNwcParameters.SelectedIndex])
        except Exception:
            pass

        self._set_progress(0, 1, "NWC  " + fname)
        target = os.path.join(folder, fname + ".nwc")
        if os.path.isfile(target) and self.ChkOverwrite.IsChecked:
            try:
                os.remove(target)
            except Exception:
                pass

        def try_export():
            doc.Export(folder, fname, opt)
            return os.path.isfile(target)

        ok = False
        err = ""
        try:
            ok = try_export()

            # Documented Autodesk workaround: a view template or a Fine detail
            # level makes the NWC exporter bail with "no suitable geometry".
            # Retry once with those neutralised, then restore the view.
            if not ok and by_view:
                self._log("NWC: retrying with view template removed and "
                          "detail level set to Medium...")
                tg = TransactionGroup(doc, "NWC export workaround")
                tg.Start()
                try:
                    t = Transaction(doc, "Relax view for NWC")
                    t.Start()
                    try:
                        if view.ViewTemplateId != ElementId.InvalidElementId:
                            view.ViewTemplateId = ElementId.InvalidElementId
                        try:
                            view.DetailLevel = DB.ViewDetailLevel.Medium
                        except Exception:
                            pass
                        t.Commit()
                    except Exception:
                        t.RollBack()
                        raise
                    ok = try_export()
                finally:
                    # never keep the change - roll the whole group back
                    tg.RollBack()
                if ok:
                    self._log("NWC: succeeded after relaxing the view "
                              "(your view was NOT modified).")
        except Exception as ex:
            err = str(ex)
            self._log("NWC FAILED %s: %s" % (fname, ex))

        scope_label = view.Name if (by_view and view) else "model"
        if ok:
            records.append(("NWC", scope_label, fname + ".nwc", "OK", ""))
            self._log("NWC -> %s.nwc" % fname)
        else:
            records.append(("NWC", scope_label, fname, "FAIL",
                            err or "exporter produced no file"))
            if not err:
                self._log("NWC: the Navisworks exporter found no suitable "
                          "geometry. Common causes: geometry only in linked "
                          "files (tick 'Convert linked files'), a view "
                          "template forcing Detail Level, a phase filter "
                          "hiding everything, or uncategorised elements.")
        self._set_progress(1, 1, "NWC  " + fname)
        self._log("NWC: -> %s" % folder)

    # ---------------------------- IFC ---------------------------------- #
    def _export_ifc(self, items, cfg, records):
        """IFC export - one file for the model, filtered by the chosen 3D view."""
        folder = self._output_dir("IFC")
        try:
            IFCExportOptions = DB.IFCExportOptions
        except AttributeError:
            self._log("IFC: exporter unavailable.")
            records.append(("IFC", "-", "-", "FAIL", "IFC exporter missing"))
            return

        view = self._picked_3d_view(self.CmbIfcView)
        use_view = bool(self.ChkIfcCurrentView.IsChecked)
        if use_view and view is None:
            self._log("IFC: no 3D view selected - exporting the whole model.")
            use_view = False

        fname = sanitize(self.TxtIfcName.Text or doc.Title, True)

        versions = ["IFC2x2", "IFC2x3CV2", "IFC2x3BFM", "IFC4RV", "IFC4DTV"]
        vname = versions[max(0, self.CmbIfcVersion.SelectedIndex)]

        opt = IFCExportOptions()
        try:
            opt.FileVersion = getattr(DB.IFCVersion, vname)
        except Exception:
            self._log("IFC: version %s unavailable, using the default." % vname)
        try:
            opt.SpaceBoundaryLevel = self.CmbIfcSpaceBound.SelectedIndex
            opt.WallAndColumnSplitting = bool(self.ChkIfcSplitWalls.IsChecked)
            opt.ExportBaseQuantities = bool(self.ChkIfcExportBase.IsChecked)
        except Exception:
            pass
        if use_view:
            try:
                opt.FilterViewId = view.Id
                self._log("IFC: filtering by view '%s'" % view.Name)
            except Exception:
                pass

        self._set_progress(0, 1, "IFC  " + fname)
        try:
            # IFC export must run inside a transaction
            trans = Transaction(doc, "IFC Export")
            trans.Start()
            try:
                doc.Export(folder, fname, opt)
            finally:
                trans.RollBack()
            records.append(("IFC", view.Name if use_view else "model",
                            fname + ".ifc", "OK", ""))
            self._log("IFC -> %s.ifc" % fname)
        except Exception as ex:
            records.append(("IFC", fname, fname, "FAIL", str(ex)))
            self._log("IFC FAILED %s: %s" % (fname, ex))
        self._set_progress(1, 1, "IFC  " + fname)
        self._log("IFC: -> %s" % folder)

    # ---------------------------- IMG ---------------------------------- #
    def _export_img(self, items, cfg, records):
        folder = self._output_dir("IMG")
        total = len(items)

        ftypes = [ImageFileType.PNG, ImageFileType.JPEGLossless,
                  ImageFileType.TIFF, ImageFileType.BMP, ImageFileType.TARGA]
        ftype = ftypes[max(0, self.CmbImgType.SelectedIndex)]
        ext = [".png", ".jpg", ".tif", ".bmp", ".tga"][max(0, self.CmbImgType.SelectedIndex)]

        try:
            pixels = int(self.TxtImgPixels.Text or 2000)
        except Exception:
            pixels = 2000

        for index, item in enumerate(items, 1):
            if self._cancel:
                break
            fname = self._resolve_filename(item, cfg)
            item_folder = self._series_subdir(folder, item)
            self._set_progress(index - 1, total, "IMG  " + fname)
            try:
                opt = ImageExportOptions()
                opt.ZoomType = (ZoomFitType.FitToPage
                                if self.CmbImgFit.SelectedIndex == 0 else ZoomFitType.Zoom)
                opt.PixelSize = pixels
                opt.FitDirection = (FitDirectionType.Horizontal
                                    if self.CmbImgDirection.SelectedIndex == 0
                                    else FitDirectionType.Vertical)
                opt.ImageResolution = ImageResolution.DPI_300
                opt.ExportRange = ExportRange.SetOfViews
                opt.HLRandWFViewsFileType = ftype
                opt.ShadowViewsFileType = ftype
                opt.FilePath = os.path.join(item_folder, fname)
                ids = List[ElementId]([item.Id])
                opt.SetViewsAndSheets(ids)
                doc.ExportImage(opt)
                records.append(("IMG", item.Number or item.Name,
                                self._rel_name(folder, item_folder, fname + ext), "OK", ""))
            except Exception as ex:
                records.append(("IMG", item.Number or item.Name, fname, "FAIL", str(ex)))
                self._log("IMG FAILED %s: %s" % (fname, ex))
            self._set_progress(index, total, "IMG  " + fname)
        self._log("IMG: %d file(s) -> %s" % (total, folder))

    # ------------------------------------------------------------------ #
    #  Profiles
    # ------------------------------------------------------------------ #
    def _load_defaults(self):
        try:
            base = os.path.dirname(doc.PathName) if doc.PathName else ""
        except Exception:
            base = ""
        self.TxtFolder.Text = base or os.path.join(
            os.path.expanduser("~"), "Documents", "Sheet Export")
        self.TxtCombinedName.Text = sanitize(doc.Title or "Combined")
        self.TxtNwcName.Text = sanitize(doc.Title or "Model")
        self.TxtIfcName.Text = sanitize(doc.Title or "Model")

        # DWG / DGN setups from the model
        for combo, cls in ((self.CmbDwgSetup, ExportDWGSettings),
                           (self.CmbDgnSetup, ExportDGNSettings)):
            combo.Items.Clear()
            combo.Items.Add("<Default>")
            try:
                for s in FilteredElementCollector(doc).OfClass(cls).ToElements():
                    combo.Items.Add(s.Name)
            except Exception:
                pass
            combo.SelectedIndex = 0

        self._load_3d_views()

        for size in ("A0", "A1", "A2", "A3", "A4", "ANSI A", "ANSI B",
                     "ANSI C", "ANSI D", "ANSI E", "ARCH D", "ARCH E1", "Letter"):
            self.CmbPaperSize.Items.Add(size)
        self.CmbPaperSize.SelectedIndex = 1

    def _load_3d_views(self):
        """Fill the NWC / IFC 3D-view pickers.

        NWC and IFC export geometry, so they need a real 3D view - the sheets
        chosen on the Selection tab are irrelevant to them.
        """
        self._views3d = []
        try:
            for v in FilteredElementCollector(doc).OfClass(DB.View3D) \
                    .WhereElementIsNotElementType().ToElements():
                if not v.IsTemplate:
                    self._views3d.append(v)
        except Exception as ex:
            logger.debug("3D view scan failed: %s", ex)
        self._views3d.sort(key=lambda v: v.Name.lower())

        names = [v.Name for v in self._views3d]
        for combo in (self.CmbNwc3DView, self.CmbIfcView):
            combo.Items.Clear()
            for n in names:
                combo.Items.Add(n)

        active_name = None
        try:
            if uidoc.ActiveView.ViewType == ViewType.ThreeD:
                active_name = uidoc.ActiveView.Name
        except Exception:
            pass

        def pick(preferred):
            if preferred and preferred in names:
                return names.index(preferred)
            for i, n in enumerate(names):
                low = n.lower()
                if "navis" in low or "nwc" in low:
                    return i
            for i, n in enumerate(names):
                if n.strip().lower() in ("{3d}", "3d"):
                    return i
            return 0 if names else -1

        idx = pick(active_name)
        self.CmbNwc3DView.SelectedIndex = idx
        self.CmbIfcView.SelectedIndex = idx
        if not names:
            # startup path: LstLog exists but logging here is noise, so just
            # surface it on the NWC tab itself
            logger.debug("no 3D views found for NWC/IFC")

    def _picked_3d_view(self, combo):
        """Return the View3D chosen in the given combo, or None."""
        i = combo.SelectedIndex
        if 0 <= i < len(getattr(self, "_views3d", [])):
            return self._views3d[i]
        return None

    def _rule_files(self):
        """All *.xml naming rules in the rules folder."""
        out = {}
        try:
            if not os.path.isdir(RULES_DIR):
                os.makedirs(RULES_DIR)
            for fn in sorted(os.listdir(RULES_DIR)):
                if fn.lower().endswith(".xml"):
                    out[os.path.splitext(fn)[0]] = os.path.join(RULES_DIR, fn)
        except Exception as ex:
            logger.debug("rules folder unreadable: %s", ex)
        return out

    def _load_profiles(self):
        """Populate the header combo from the naming-rule XML files on disk."""
        self._profiles = self._rule_files()
        self._refresh_profile_combo()

    def _refresh_profile_combo(self, select=None):
        self._loading_profiles = True
        try:
            self.CmbProfile.Items.Clear()
            self.CmbProfile.Items.Add(CURRENT_RULE_LABEL)
            for name in sorted(self._profiles):
                self.CmbProfile.Items.Add(name)
            if select and select in self._profiles:
                self.CmbProfile.SelectedItem = select
            else:
                self.CmbProfile.SelectedIndex = 0
        finally:
            self._loading_profiles = False
        self._syncing = False
        self._checkbox_click = False

    def on_profile_changed(self, sender, args):
        """Switching the combo applies that rule immediately."""
        if not self.IsLoaded or getattr(self, "_loading_profiles", False):
            return
        name = self.CmbProfile.SelectedItem
        if not name or str(name) == CURRENT_RULE_LABEL:
            return
        path = self._profiles.get(str(name))
        if not path or not os.path.isfile(path):
            return
        try:
            self._rule = read_rule_xml(path)
        except Exception as ex:
            forms.alert("Could not read '%s':\n%s" % (name, ex),
                        title="Sheet Export")
            return
        self._update_rule_summary()
        self._fill_names(only_empty=False)
        self.GridItems.Items.Refresh()
        self._log("Naming rule '%s' applied." % name)

    def on_profile_save(self, sender, args):
        """Save the current rule as a new XML in the rules folder."""
        if not self._rule.get("tokens"):
            forms.alert("There is no naming rule to save.\n\nBuild one with "
                        "the 'Custom File Name...' button first.",
                        title="Sheet Export")
            return
        name = forms.ask_for_string(default="My Naming Rule",
                                    prompt="Name for this naming rule:",
                                    title="Save naming rule")
        if not name:
            return
        name = sanitize(name, True)
        try:
            if not os.path.isdir(RULES_DIR):
                os.makedirs(RULES_DIR)
            path = os.path.join(RULES_DIR, name + ".xml")
            if os.path.isfile(path) and not forms.alert(
                    "'%s' already exists. Overwrite?" % name,
                    yes=True, no=True):
                return
            write_rule_xml(path, self._rule)
        except Exception as ex:
            forms.alert("Could not save the rule:\n%s" % ex, title="Sheet Export")
            return
        self._profiles = self._rule_files()
        self._refresh_profile_combo(name)
        self._log("Naming rule saved as '%s'." % name)

    def on_profile_browse(self, sender, args):
        """Load a rule XML from anywhere and add it to the list."""
        dlg = OpenFileDialog()
        dlg.Filter = "Sheet Export naming rule (*.xml)|*.xml|All files (*.*)|*.*"
        dlg.Title = "Load naming rule"
        if dlg.ShowDialog() != DialogResult.OK:
            return
        try:
            self._rule = read_rule_xml(dlg.FileName)
        except Exception as ex:
            forms.alert("Could not read the XML:\n%s" % ex, title="Sheet Export")
            return
        label = os.path.splitext(os.path.basename(dlg.FileName))[0]
        self._profiles[label] = dlg.FileName
        self._refresh_profile_combo(label)
        self._update_rule_summary()
        self._fill_names(only_empty=False)
        self.GridItems.Items.Refresh()
        self._log("Naming rule loaded from %s" % dlg.FileName)

    def on_profile_folder(self, sender, args):
        try:
            if not os.path.isdir(RULES_DIR):
                os.makedirs(RULES_DIR)
            os.startfile(RULES_DIR)
        except Exception as ex:
            forms.alert("Could not open the rules folder:\n%s" % ex,
                        title="Sheet Export")

    def on_profile_delete(self, sender, args):
        name = self.CmbProfile.SelectedItem
        if not name or str(name) == CURRENT_RULE_LABEL:
            return
        name = str(name)
        path = self._profiles.get(name)
        if not path:
            return
        if not forms.alert("Delete naming rule '%s'?\n\n%s" % (name, path),
                           yes=True, no=True):
            return
        try:
            os.remove(path)
        except Exception as ex:
            forms.alert("Could not delete the file:\n%s" % ex, title="Sheet Export")
            return
        self._profiles = self._rule_files()
        self._refresh_profile_combo()


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    if REVIT_VERSION < 2024:
        forms.alert("This tool targets Revit 2024-2027.\n"
                    "Detected Revit %s - PDF export may fall back to the printer."
                    % REVIT_VERSION, title="Sheet Export")
    try:
        SheetExportWindow().ShowDialog()
    except Exception:
        logger.error(traceback.format_exc())
        forms.alert("Sheet Export failed to start:\n\n%s"
                    % traceback.format_exc(), title="Sheet Export")
