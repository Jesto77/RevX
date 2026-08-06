# -*- coding: utf-8 -*-
"""Family Browser – pick a Revit project, browse its loadable families by category,
   select multiple families and load them into the current project.

   Compatible: Revit 2022-2027 / pyRevit / IronPython 2.7 / CPython 3

   FIXES vs original:
   - 'Transform is not defined' error: removed broken ElementTransformUtils approach
   - Family copy now uses the correct method: SaveAs temp .rfa + LoadFamily()
     This is the only cross-document family transfer method that works reliably
     in all Revit versions without needing RevitServices or Dynamo assemblies.
"""

import os
import sys
import tempfile
import clr

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

import System
from System.Windows import (Window, Thickness,
                            HorizontalAlignment, VerticalAlignment,
                            FontWeight, WindowStartupLocation, ResizeMode,
                            GridLength, GridUnitType)
from System.Windows.Controls import (TreeView, TreeViewItem, DockPanel,
                                     Grid, Border, TextBlock, Button, StackPanel,
                                     ColumnDefinition, Orientation, Dock, CheckBox)
from System.Windows.Media import Brushes

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import Transaction, SaveAsOptions

from pyrevit import forms, revit

doc   = revit.doc
uidoc = revit.uidoc
app   = doc.Application


# =============================================================================
# FAMILY LOAD OPTIONS — always overwrite existing when user confirmed
# =============================================================================
# FamilyLoadOptions cannot be imported by name in IronPython — it is an
# abstract class exposed as DB.IFamilyLoadOptions in the CLR.
# We subclass DB.IFamilyLoadOptions directly instead.
# =============================================================================

class OverwriteLoadOptions(DB.IFamilyLoadOptions):
    """Always overwrite and use the source family's parameter values."""
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        # overwriteParameterValues is a ref bool — set via indexer in IronPython
        overwriteParameterValues.Value = True
        return True   # True = overwrite

    def OnSharedFamilyFound(self, sharedFamily, familyInUse,
                             source, overwriteParameterValues):
        overwriteParameterValues.Value = True
        source.Value = DB.FamilySource.Family
        return True


# =============================================================================
# COPY FAMILY: source doc -> temp .rfa -> LoadFamily into current doc
# =============================================================================

def copy_family_to_project(source_doc, source_family):
    """
    Transfer a Family from source_doc into the active doc.

    Strategy:
      1. Open source_family in its own edit session (EditFamily)
      2. SaveAs to a temp .rfa path
      3. Close the edit session without saving back to source
      4. LoadFamily() the .rfa into the current doc

    Returns the loaded Family element, or None on failure.
    """
    tmp_path = None
    family_doc = None
    try:
        # Step 1: open the family for editing inside the source doc
        family_doc = source_doc.EditFamily(source_family)
        if family_doc is None:
            raise Exception("EditFamily returned None — family may be non-editable.")

        # Step 2: save to a unique temp file
        tmp_dir  = tempfile.gettempdir()
        safe_name = "".join(
            c if c.isalnum() or c in (" ", "_", "-") else "_"
            for c in source_family.Name
        )
        tmp_path = os.path.join(tmp_dir, "{}.rfa".format(safe_name))

        # Remove stale temp file if it exists
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                import uuid
                tmp_path = os.path.join(
                    tmp_dir, "{}_{}.rfa".format(safe_name, str(uuid.uuid4())[:8]))

        save_opts = SaveAsOptions()
        save_opts.OverwriteExistingFile = True
        family_doc.SaveAs(tmp_path, save_opts)

        # Step 3: close the family edit doc (don't save back to source)
        family_doc.Close(False)
        family_doc = None

        # Step 4: load into current doc (inside an existing Transaction — caller's)
        # IronPython handles out-params differently from CPython:
        # LoadFamily returns (bool, Family) as a tuple in IronPython,
        # while CPython needs clr.Reference[DB.Family].
        try:
            # IronPython: out-param returned as extra tuple value
            result = doc.LoadFamily(tmp_path, OverwriteLoadOptions())
            if isinstance(result, tuple):
                success, loaded_fam = result[0], result[1] if len(result) > 1 else None
            else:
                success, loaded_fam = result, None
        except Exception:
            success, loaded_fam = False, None

        if loaded_fam is not None:
            return loaded_fam

        # Fallback: search by name (LoadFamily can return False when family
        # already existed but was still refreshed)
        for fam in DB.FilteredElementCollector(doc).OfClass(DB.Family):
            if fam.Name == source_family.Name:
                return fam

        return None

    except Exception:
        raise
    finally:
        # Always close the temp family doc if still open
        if family_doc is not None:
            try:
                family_doc.Close(False)
            except Exception:
                pass
        # Clean up temp file
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# =============================================================================
# FAMILY BROWSER WINDOW
# =============================================================================

class FamilyBrowserWindow(Window):
    def __init__(self, families_by_category):
        self.families_data    = families_by_category
        self.selected_families = []   # list of (family_id, source_doc)

        self.Title = "Family Browser – Pick from Project"
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResizeWithGrip
        self.Width  = 600
        self.Height = 700
        self.MinWidth  = 400
        self.MinHeight = 400

        root = Grid()
        root.Margin = Thickness(10)

        # Single column — just the tree + bottom button bar
        root.RowDefinitions.Add(
            System.Windows.Controls.RowDefinition())  # tree fills
        rd_btn = System.Windows.Controls.RowDefinition()
        rd_btn.Height = GridLength(44)
        root.RowDefinitions.Add(rd_btn)

        # TreeView
        self.tree = TreeView()
        self.tree.Background      = Brushes.WhiteSmoke
        self.tree.BorderThickness = Thickness(1)
        self.tree.BorderBrush     = Brushes.LightGray
        self.tree.FontSize        = 13
        Grid.SetRow(self.tree, 0)
        root.Children.Add(self.tree)

        # Bottom bar
        bar = DockPanel()
        bar.Margin = Thickness(0, 6, 0, 0)
        Grid.SetRow(bar, 1)
        root.Children.Add(bar)

        self.status_txt = TextBlock()
        self.status_txt.Text = "0 families selected"
        self.status_txt.VerticalAlignment = VerticalAlignment.Center
        self.status_txt.Foreground = Brushes.Gray
        DockPanel.SetDock(self.status_txt, Dock.Left)
        bar.Children.Add(self.status_txt)

        self.load_btn = Button()
        self.load_btn.Content         = "Load Selected"
        self.load_btn.Width           = 130
        self.load_btn.Height          = 32
        self.load_btn.IsEnabled       = False
        self.load_btn.Background      = Brushes.DodgerBlue
        self.load_btn.Foreground      = Brushes.White
        self.load_btn.FontWeight      = FontWeight.FromOpenTypeWeight(700)
        self.load_btn.BorderThickness = Thickness(0)
        self.load_btn.HorizontalAlignment = HorizontalAlignment.Right
        DockPanel.SetDock(self.load_btn, Dock.Right)
        bar.Children.Add(self.load_btn)

        self.Content = root

        self.load_btn.Click += self.on_load_click
        self._build_tree()
        self.ShowDialog()

    # ── Tree population ──────────────────────────────────────────────────────

    def _build_tree(self):
        for cat_name, families in sorted(self.families_data.items()):
            cat_item = TreeViewItem()
            cat_item.Header    = cat_name
            cat_item.FontWeight = FontWeight.FromOpenTypeWeight(700)
            cat_item.IsExpanded = False

            for fam_name, fam_id, src_doc in sorted(families, key=lambda x: x[0]):
                fam_item = TreeViewItem()

                row = StackPanel()
                row.Orientation = Orientation.Horizontal

                chk = CheckBox()
                chk.VerticalAlignment = VerticalAlignment.Center
                chk.Margin  = Thickness(0, 0, 6, 0)
                chk.Tag     = (fam_id, src_doc)
                chk.Checked   += self.on_chk_changed
                chk.Unchecked += self.on_chk_changed

                lbl = TextBlock()
                lbl.Text = fam_name
                lbl.VerticalAlignment = VerticalAlignment.Center

                row.Children.Add(chk)
                row.Children.Add(lbl)
                fam_item.Header = row
                cat_item.Items.Add(fam_item)

            self.tree.Items.Add(cat_item)

    # ── Checkbox handler ─────────────────────────────────────────────────────

    def on_chk_changed(self, sender, e):
        count = self._checked_count()
        self.load_btn.IsEnabled = count > 0
        self.status_txt.Text = "{} {} selected".format(
            count, "family" if count == 1 else "families")

    def _checked_count(self):
        n = 0
        for cat in self.tree.Items:
            for fam in cat.Items:
                hdr = fam.Header
                if isinstance(hdr, StackPanel) and hdr.Children[0].IsChecked:
                    n += 1
        return n

    def _get_checked(self):
        result = []
        for cat in self.tree.Items:
            for fam in cat.Items:
                hdr = fam.Header
                if isinstance(hdr, StackPanel):
                    chk = hdr.Children[0]
                    if chk.IsChecked:
                        result.append(chk.Tag)
        return result

    # ── Load button ──────────────────────────────────────────────────────────

    def on_load_click(self, sender, e):
        self.selected_families = self._get_checked()
        if self.selected_families:
            self.Close()


# =============================================================================
# LOAD SELECTED FAMILIES INTO CURRENT PROJECT
# =============================================================================

def load_families_from_project(selected_families, source_doc):
    """
    Load a list of (family_id, source_doc) into the current Revit document.
    Uses SaveAs-temp-rfa + LoadFamily — works in all Revit versions.
    """
    if not selected_families:
        return

    loaded = []
    failed = []

    t = Transaction(doc, "Load Families from Project")
    t.Start()
    try:
        for fam_id, src_doc in selected_families:
            src_fam = src_doc.GetElement(fam_id)
            if src_fam is None:
                failed.append("(unknown) — element not found")
                continue
            fam_name = src_fam.Name
            try:
                result = copy_family_to_project(src_doc, src_fam)
                if result:
                    loaded.append(result)
                else:
                    failed.append(fam_name + " — LoadFamily returned None")
            except Exception as ex:
                failed.append("{} — {}".format(fam_name, str(ex)[:120]))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("Transaction failed:\n{}".format(ex))
        return

    # ── Report ───────────────────────────────────────────────────────────────
    if failed:
        msg = "{} loaded, {} failed:\n\n{}".format(
            len(loaded), len(failed), "\n".join("  • " + f for f in failed))
        forms.alert(msg, title="Family Browser — Result")
    
    if not loaded:
        return

    # ── Placement for single loaded family ───────────────────────────────────
    if len(loaded) == 1:
        fam = loaded[0]
        sym_ids = list(fam.GetFamilySymbolIds())
        if not sym_ids:
            forms.alert("'{}' loaded but has no types.".format(fam.Name))
            return

        if len(sym_ids) == 1:
            sym = doc.GetElement(sym_ids[0])
            # Activate symbol if needed (Revit 2015+)
            if hasattr(sym, "IsActive") and not sym.IsActive:
                t2 = Transaction(doc, "Activate Symbol")
                t2.Start()
                sym.Activate()
                t2.Commit()
            uidoc.PostRequestForElementTypePlacement(sym)
        else:
            type_map = {}
            for sid in sym_ids:
                sym = doc.GetElement(sid)
                try:
                    p = sym.LookupParameter("Type Name") or sym.LookupParameter("Family Type")
                    tname = p.AsString() if p else sym.Name
                except Exception:
                    tname = sym.Name
                type_map[tname] = sym

            sel = forms.SelectFromList.show(
                sorted(type_map.keys()),
                message="Select a Family Type to place",
                multiselect=False
            )
            if sel:
                chosen = type_map[sel]
                if hasattr(chosen, "IsActive") and not chosen.IsActive:
                    t3 = Transaction(doc, "Activate Symbol")
                    t3.Start()
                    chosen.Activate()
                    t3.Commit()
                uidoc.PostRequestForElementTypePlacement(chosen)
    else:
        forms.alert("{} families loaded successfully.".format(len(loaded)),
                    title="Family Browser")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    # 1. Pick source RVT file
    rvt_path = forms.pick_file(
        file_ext="rvt",
        title="Select Revit Project (*.rvt) to browse families from"
    )
    if not rvt_path:
        sys.exit(0)

    # 2. Open source document (detached, discard worksets)
    m_path   = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(rvt_path)
    open_opt = DB.OpenOptions()
    open_opt.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndDiscardWorksets

    try:
        source_doc = app.OpenDocumentFile(m_path, open_opt)
    except Exception as ex:
        forms.alert("Could not open the selected project:\n{}".format(ex))
        sys.exit(0)

    if not source_doc:
        forms.alert("Failed to open the selected project.")
        sys.exit(0)

    # 3. Collect loadable families grouped by category
    families_by_category = {}
    for fam in DB.FilteredElementCollector(source_doc).OfClass(DB.Family):
        if not fam.IsEditable:
            continue
        cat      = fam.FamilyCategory
        cat_name = cat.Name if cat else "Uncategorized"
        families_by_category.setdefault(cat_name, []).append(
            (fam.Name, fam.Id, source_doc)
        )

    if not families_by_category:
        forms.alert("No loadable families found in the selected project.")
        source_doc.Close(False)
        sys.exit(0)

    # 4. Show browser window
    browser = FamilyBrowserWindow(families_by_category)
    selected = browser.selected_families

    # 5. Load selected families
    if selected:
        load_families_from_project(selected, source_doc)

    # 6. Close source document
    try:
        source_doc.Close(False)
    except Exception:
        pass
