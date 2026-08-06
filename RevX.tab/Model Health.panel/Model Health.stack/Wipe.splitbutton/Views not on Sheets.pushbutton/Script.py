import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import TaskDialog
from System.Windows.Forms import (
    Form, Button, Label, CheckedListBox,
    DialogResult, MessageBox,
    MessageBoxButtons, MessageBoxIcon, FormStartPosition,
    BorderStyle, FormBorderStyle, AnchorStyles, TextBox,
    Panel, FlatStyle, Keys, Control
)
from System.Drawing import Point, Size, Font, FontStyle, Color

# Get the current document
doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


def get_views_on_sheets(doc):
    """Get all view IDs that are placed on sheets."""
    views_on_sheets = set()
    
    # Get all sheets
    sheets = FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()
    
    for sheet in sheets:
        # Get all viewports on the sheet
        try:
            viewport_ids = sheet.GetAllViewports()
            for vp_id in viewport_ids:
                viewport = doc.GetElement(vp_id)
                if viewport:
                    views_on_sheets.add(str(viewport.ViewId))
        except:
            pass
    
    # Get all schedule instances (only exist when placed on a sheet)
    try:
        schedule_instances = FilteredElementCollector(doc)\
            .OfClass(ScheduleSheetInstance)\
            .ToElements()
        for sched_inst in schedule_instances:
            views_on_sheets.add(str(sched_inst.ScheduleId))
    except:
        pass
    
    return views_on_sheets


def get_deletable_views(doc):
    """Get all views that should be deleted (not on sheets, not 3D)."""
    views_on_sheets = get_views_on_sheets(doc)
    all_views = FilteredElementCollector(doc).OfClass(View).ToElements()
    
    views_to_delete = []
    
    # Debug counters
    debug_info = {
        'total_views': 0,
        'templates_skipped': 0,
        '3d_skipped': 0,
        'system_skipped': 0,
        'on_sheet_skipped': 0,
        'to_delete': 0
    }
    
    for view in all_views:
        try:
            debug_info['total_views'] += 1
            
            # Skip view templates
            if view.IsTemplate:
                debug_info['templates_skipped'] += 1
                continue
            
            # Skip 3D views (KEEP them)
            if view.ViewType == ViewType.ThreeD:
                debug_info['3d_skipped'] += 1
                continue
            
            # Skip system views
            if view.ViewType in [ViewType.SystemBrowser, ViewType.ProjectBrowser, 
                                  ViewType.Undefined, ViewType.Internal,
                                  ViewType.DrawingSheet]:
                debug_info['system_skipped'] += 1
                continue
            
            # Skip views on sheets
            if str(view.Id) in views_on_sheets:
                debug_info['on_sheet_skipped'] += 1
                continue
            
            views_to_delete.append(view)
            debug_info['to_delete'] += 1
        except:
            continue
    
    return views_to_delete, debug_info


# First, check what we find
views_to_delete, debug_info = get_deletable_views(doc)

# Show debug info
debug_msg = "=== VIEW ANALYSIS ===\n\n"
debug_msg += "Total Views in Project: {0}\n".format(debug_info['total_views'])
debug_msg += "View Templates (skipped): {0}\n".format(debug_info['templates_skipped'])
debug_msg += "3D Views (kept): {0}\n".format(debug_info['3d_skipped'])
debug_msg += "System Views (skipped): {0}\n".format(debug_info['system_skipped'])
debug_msg += "Views on Sheets (kept): {0}\n".format(debug_info['on_sheet_skipped'])
debug_msg += "\nViews Available to Delete: {0}\n".format(debug_info['to_delete'])

if len(views_to_delete) == 0:
    debug_msg += "\nNo views found that can be deleted.\nAll views are either on sheets, 3D views, or system views."
    MessageBox.Show(debug_msg, "View Cleanup - Analysis",
                    MessageBoxButtons.OK, MessageBoxIcon.Information)
else:
    # Only show form if there are views to delete
    
    class ViewCleanupForm(Form):
        def __init__(self, document, views_list):
            self.doc = document
            self.views_to_delete = views_list
            self.filtered_views = list(views_list)
            self.last_clicked_index = -1
            self.InitializeComponent()
            self.UpdateListBox()
        
        def InitializeComponent(self):
            self.Text = "View Cleanup - Delete Views Not on Sheets"
            self.Size = Size(650, 650)
            self.StartPosition = FormStartPosition.CenterScreen
            self.FormBorderStyle = FormBorderStyle.Sizable
            self.MinimumSize = Size(500, 450)
            self.TopMost = True  # Make sure form appears on top
            
            self.lblTitle = Label()
            self.lblTitle.Text = "Views Not on Sheets"
            self.lblTitle.Font = Font("Arial", 14, FontStyle.Bold)
            self.lblTitle.Location = Point(15, 15)
            self.lblTitle.Size = Size(400, 30)
            self.Controls.Add(self.lblTitle)
            
            self.lblInfo = Label()
            self.lblInfo.Text = "Note: All 3D views are automatically excluded and will be kept."
            self.lblInfo.ForeColor = Color.FromArgb(0, 100, 0)
            self.lblInfo.Font = Font("Arial", 9, FontStyle.Italic)
            self.lblInfo.Location = Point(15, 45)
            self.lblInfo.Size = Size(500, 20)
            self.Controls.Add(self.lblInfo)
            
            self.lblSearch = Label()
            self.lblSearch.Text = "Search:"
            self.lblSearch.Font = Font("Arial", 10)
            self.lblSearch.Location = Point(15, 75)
            self.lblSearch.Size = Size(55, 22)
            self.Controls.Add(self.lblSearch)
            
            self.txtSearch = TextBox()
            self.txtSearch.Location = Point(75, 73)
            self.txtSearch.Size = Size(450, 25)
            self.txtSearch.Font = Font("Arial", 10)
            self.txtSearch.TextChanged += self.OnSearchTextChanged
            self.Controls.Add(self.txtSearch)
            
            self.btnClearSearch = Button()
            self.btnClearSearch.Text = "X"
            self.btnClearSearch.Location = Point(530, 72)
            self.btnClearSearch.Size = Size(30, 25)
            self.btnClearSearch.Click += self.OnClearSearchClick
            self.Controls.Add(self.btnClearSearch)
            
            self.lblInstructions = Label()
            self.lblInstructions.Text = "SHIFT+Click: Select range | CTRL+Click: Toggle individual"
            self.lblInstructions.ForeColor = Color.Gray
            self.lblInstructions.Font = Font("Arial", 9)
            self.lblInstructions.Location = Point(15, 105)
            self.lblInstructions.Size = Size(400, 20)
            self.Controls.Add(self.lblInstructions)
            
            self.chkListViews = CheckedListBox()
            self.chkListViews.Location = Point(15, 130)
            self.chkListViews.Size = Size(605, 360)
            self.chkListViews.CheckOnClick = True
            self.chkListViews.BorderStyle = BorderStyle.FixedSingle
            self.chkListViews.Font = Font("Consolas", 9)
            self.chkListViews.Anchor = (AnchorStyles.Top | AnchorStyles.Bottom | 
                                         AnchorStyles.Left | AnchorStyles.Right)
            self.chkListViews.ItemCheck += self.OnItemCheck
            self.chkListViews.MouseDown += self.OnMouseDown
            self.Controls.Add(self.chkListViews)
            
            self.lblCount = Label()
            self.lblCount.Text = "Total: 0 | Checked: 0"
            self.lblCount.Font = Font("Arial", 10, FontStyle.Bold)
            self.lblCount.Location = Point(15, 500)
            self.lblCount.Size = Size(300, 25)
            self.lblCount.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
            self.Controls.Add(self.lblCount)
            
            self.pnlButtons = Panel()
            self.pnlButtons.Location = Point(15, 530)
            self.pnlButtons.Size = Size(605, 45)
            self.pnlButtons.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
            self.Controls.Add(self.pnlButtons)
            
            self.btnCheckAll = Button()
            self.btnCheckAll.Text = "Check All"
            self.btnCheckAll.Location = Point(0, 5)
            self.btnCheckAll.Size = Size(90, 35)
            self.btnCheckAll.Font = Font("Arial", 9)
            self.btnCheckAll.BackColor = Color.FromArgb(40, 167, 69)
            self.btnCheckAll.ForeColor = Color.White
            self.btnCheckAll.FlatStyle = FlatStyle.Flat
            self.btnCheckAll.Click += self.OnCheckAllClick
            self.pnlButtons.Controls.Add(self.btnCheckAll)
            
            self.btnUncheckAll = Button()
            self.btnUncheckAll.Text = "Uncheck All"
            self.btnUncheckAll.Location = Point(100, 5)
            self.btnUncheckAll.Size = Size(90, 35)
            self.btnUncheckAll.Font = Font("Arial", 9)
            self.btnUncheckAll.BackColor = Color.FromArgb(108, 117, 125)
            self.btnUncheckAll.ForeColor = Color.White
            self.btnUncheckAll.FlatStyle = FlatStyle.Flat
            self.btnUncheckAll.Click += self.OnUncheckAllClick
            self.pnlButtons.Controls.Add(self.btnUncheckAll)
            
            self.btnInvert = Button()
            self.btnInvert.Text = "Invert"
            self.btnInvert.Location = Point(200, 5)
            self.btnInvert.Size = Size(70, 35)
            self.btnInvert.Font = Font("Arial", 9)
            self.btnInvert.BackColor = Color.FromArgb(23, 162, 184)
            self.btnInvert.ForeColor = Color.White
            self.btnInvert.FlatStyle = FlatStyle.Flat
            self.btnInvert.Click += self.OnInvertClick
            self.pnlButtons.Controls.Add(self.btnInvert)
            
            self.btnDelete = Button()
            self.btnDelete.Text = "Delete Checked Views"
            self.btnDelete.Location = Point(360, 5)
            self.btnDelete.Size = Size(155, 35)
            self.btnDelete.Font = Font("Arial", 9, FontStyle.Bold)
            self.btnDelete.BackColor = Color.FromArgb(220, 53, 69)
            self.btnDelete.ForeColor = Color.White
            self.btnDelete.FlatStyle = FlatStyle.Flat
            self.btnDelete.Click += self.OnDeleteClick
            self.btnDelete.Enabled = False
            self.pnlButtons.Controls.Add(self.btnDelete)
            
            self.btnClose = Button()
            self.btnClose.Text = "Close"
            self.btnClose.Location = Point(525, 5)
            self.btnClose.Size = Size(80, 35)
            self.btnClose.Font = Font("Arial", 9)
            self.btnClose.BackColor = Color.FromArgb(52, 58, 64)
            self.btnClose.ForeColor = Color.White
            self.btnClose.FlatStyle = FlatStyle.Flat
            self.btnClose.Click += self.OnCloseClick
            self.pnlButtons.Controls.Add(self.btnClose)
        
        def RefreshViews(self):
            self.views_to_delete, _ = get_deletable_views(self.doc)
            self.filtered_views = list(self.views_to_delete)
            self.UpdateListBox()
        
        def UpdateListBox(self):
            self.chkListViews.Items.Clear()
            # Sort by view type then name
            sorted_views = sorted(self.filtered_views, key=lambda v: (str(v.ViewType), v.Name))
            self.filtered_views = sorted_views
            for view in self.filtered_views:
                view_type = str(view.ViewType).ljust(20)
                display_name = "[{0}] {1}".format(view_type, view.Name)
                self.chkListViews.Items.Add(display_name)
            self.last_clicked_index = -1
            self.UpdateCount()
        
        def UpdateCount(self):
            total = self.chkListViews.Items.Count
            checked = self.chkListViews.CheckedItems.Count
            self.lblCount.Text = "Total: {0} | Checked: {1}".format(total, checked)
            self.btnDelete.Enabled = checked > 0
        
        def OnSearchTextChanged(self, sender, args):
            search_text = self.txtSearch.Text.lower()
            if search_text:
                self.filtered_views = [v for v in self.views_to_delete 
                                        if search_text in v.Name.lower() or 
                                        search_text in str(v.ViewType).lower()]
            else:
                self.filtered_views = list(self.views_to_delete)
            self.UpdateListBox()
        
        def OnClearSearchClick(self, sender, args):
            self.txtSearch.Text = ""
        
        def OnMouseDown(self, sender, args):
            index = self.chkListViews.IndexFromPoint(args.Location)
            
            if index < 0 or index >= self.chkListViews.Items.Count:
                return
            
            if Control.ModifierKeys == Keys.Shift:
                if self.last_clicked_index >= 0 and self.last_clicked_index < self.chkListViews.Items.Count:
                    new_state = not self.chkListViews.GetItemChecked(index)
                    start_idx = min(self.last_clicked_index, index)
                    end_idx = max(self.last_clicked_index, index)
                    
                    for i in range(start_idx, end_idx + 1):
                        self.chkListViews.SetItemChecked(i, new_state)
                    
                    self.UpdateCount()
                else:
                    self.last_clicked_index = index
            elif Control.ModifierKeys == Keys.Control:
                self.last_clicked_index = index
            else:
                self.last_clicked_index = index
        
        def OnItemCheck(self, sender, args):
            self.BeginInvoke(System.Action(self.UpdateCount))
        
        def OnCheckAllClick(self, sender, args):
            for i in range(self.chkListViews.Items.Count):
                self.chkListViews.SetItemChecked(i, True)
            self.UpdateCount()
        
        def OnUncheckAllClick(self, sender, args):
            for i in range(self.chkListViews.Items.Count):
                self.chkListViews.SetItemChecked(i, False)
            self.UpdateCount()
        
        def OnInvertClick(self, sender, args):
            for i in range(self.chkListViews.Items.Count):
                current_state = self.chkListViews.GetItemChecked(i)
                self.chkListViews.SetItemChecked(i, not current_state)
            self.UpdateCount()
        
        def OnDeleteClick(self, sender, args):
            checked_indices = list(self.chkListViews.CheckedIndices)
            
            if not checked_indices:
                MessageBox.Show("No views checked.", "Warning",
                               MessageBoxButtons.OK, MessageBoxIcon.Warning)
                return
            
            selected_views = [self.filtered_views[i] for i in checked_indices]
            
            confirm_msg = "Are you sure you want to delete {0} view(s)?\n\n".format(len(selected_views))
            if len(selected_views) <= 15:
                confirm_msg += "Views to delete:\n"
                for view in selected_views:
                    confirm_msg += "  - [{0}] {1}\n".format(view.ViewType, view.Name)
            else:
                confirm_msg += "First 15 views:\n"
                for view in selected_views[:15]:
                    confirm_msg += "  - [{0}] {1}\n".format(view.ViewType, view.Name)
                confirm_msg += "  ... and {0} more\n".format(len(selected_views) - 15)
            
            confirm_msg += "\n3D views are automatically preserved.\nThis action cannot be undone!"
            
            result = MessageBox.Show(confirm_msg, "Confirm Deletion",
                                     MessageBoxButtons.YesNo, MessageBoxIcon.Warning)
            
            if result == DialogResult.Yes:
                self.DeleteViews(selected_views)
        
        def DeleteViews(self, views_to_delete):
            deleted_count = 0
            deleted_names = []
            failed_views = []
            
            t = Transaction(self.doc, "Delete Views Not on Sheets")
            t.Start()
            
            try:
                for view in views_to_delete:
                    try:
                        view_name = view.Name
                        view_type = str(view.ViewType)
                        view_id = view.Id
                    except:
                        continue
                    
                    sub_t = SubTransaction(self.doc)
                    try:
                        sub_t.Start()
                        
                        if self.doc.GetElement(view_id) is None:
                            sub_t.RollBack()
                            failed_views.append("{0}: No longer exists".format(view_name))
                            continue
                        
                        deleted_ids = self.doc.Delete(view_id)
                        
                        if deleted_ids and deleted_ids.Count > 0:
                            sub_t.Commit()
                            deleted_count += 1
                            deleted_names.append("[{0}] {1}".format(view_type, view_name))
                        else:
                            sub_t.RollBack()
                            failed_views.append("{0}: Could not be deleted".format(view_name))
                            
                    except Exception as e:
                        if sub_t.HasStarted() and not sub_t.HasEnded():
                            sub_t.RollBack()
                        error_msg = str(e)
                        short_error = error_msg[:80] + "..." if len(error_msg) > 80 else error_msg
                        failed_views.append("{0}: {1}".format(view_name, short_error))
                
                t.Commit()
                
            except Exception as e:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                MessageBox.Show("Main transaction failed: {0}".format(str(e)), "Error",
                               MessageBoxButtons.OK, MessageBoxIcon.Error)
                return
            
            result_msg = "========== DELETION SUMMARY ==========\n\n"
            result_msg += "Successfully deleted: {0} view(s)\n".format(deleted_count)
            
            if deleted_names:
                result_msg += "\n--- Deleted Views ---\n"
                for name in deleted_names[:15]:
                    result_msg += "  [OK] {0}\n".format(name)
                if len(deleted_names) > 15:
                    result_msg += "  ... and {0} more\n".format(len(deleted_names) - 15)
            
            if failed_views:
                result_msg += "\n--- Failed: {0} ---\n".format(len(failed_views))
                for msg in failed_views[:10]:
                    result_msg += "  [!] {0}\n".format(msg)
                if len(failed_views) > 10:
                    result_msg += "  ... and {0} more\n".format(len(failed_views) - 10)
            
            result_msg += "\n========================================"
            
            MessageBox.Show(result_msg, "Deletion Complete",
                           MessageBoxButtons.OK, MessageBoxIcon.Information)
            
            self.RefreshViews()
            self.txtSearch.Text = ""
        
        def OnCloseClick(self, sender, args):
            self.Close()
    
    # Run the form
    form = ViewCleanupForm(doc, views_to_delete)
    form.ShowDialog()