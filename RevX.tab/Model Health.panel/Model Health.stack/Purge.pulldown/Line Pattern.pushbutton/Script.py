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
    SelectionMode, DialogResult, MessageBox,
    MessageBoxButtons, MessageBoxIcon, FormStartPosition,
    BorderStyle, FormBorderStyle, AnchorStyles, TextBox,
    Panel, FlatStyle, Keys, Control
)
from System.Drawing import Point, Size, Font, FontStyle, Color

# Get the current document
doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

class LinePatternManagerForm(Form):
    def __init__(self, document):
        self.doc = document
        self.line_patterns = []
        self.filtered_patterns = []
        self.last_clicked_index = -1
        self.InitializeComponent()
        self.LoadLinePatterns()
    
    def InitializeComponent(self):
        self.Text = "Line Pattern Manager"
        self.Size = Size(500, 600)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize = Size(400, 400)
        
        self.lblTitle = Label()
        self.lblTitle.Text = "Line Patterns in Project"
        self.lblTitle.Font = Font("Arial", 14, FontStyle.Bold)
        self.lblTitle.Location = Point(15, 15)
        self.lblTitle.Size = Size(350, 30)
        self.Controls.Add(self.lblTitle)
        
        self.lblSearch = Label()
        self.lblSearch.Text = "Search:"
        self.lblSearch.Font = Font("Arial", 10)
        self.lblSearch.Location = Point(15, 55)
        self.lblSearch.Size = Size(55, 22)
        self.Controls.Add(self.lblSearch)
        
        self.txtSearch = TextBox()
        self.txtSearch.Location = Point(75, 53)
        self.txtSearch.Size = Size(300, 25)
        self.txtSearch.Font = Font("Arial", 10)
        self.txtSearch.TextChanged += self.OnSearchTextChanged
        self.Controls.Add(self.txtSearch)
        
        self.btnClearSearch = Button()
        self.btnClearSearch.Text = "X"
        self.btnClearSearch.Location = Point(380, 52)
        self.btnClearSearch.Size = Size(30, 25)
        self.btnClearSearch.Click += self.OnClearSearchClick
        self.Controls.Add(self.btnClearSearch)
        
        self.lblInstructions = Label()
        self.lblInstructions.Text = "SHIFT+Click: Select range | CTRL+Click: Toggle individual"
        self.lblInstructions.ForeColor = Color.Gray
        self.lblInstructions.Font = Font("Arial", 9)
        self.lblInstructions.Location = Point(15, 85)
        self.lblInstructions.Size = Size(400, 20)
        self.Controls.Add(self.lblInstructions)
        
        self.chkListPatterns = CheckedListBox()
        self.chkListPatterns.Location = Point(15, 110)
        self.chkListPatterns.Size = Size(455, 380)
        self.chkListPatterns.CheckOnClick = True
        self.chkListPatterns.BorderStyle = BorderStyle.FixedSingle
        self.chkListPatterns.Font = Font("Arial", 10)
        self.chkListPatterns.Anchor = (AnchorStyles.Top | AnchorStyles.Bottom | 
                                        AnchorStyles.Left | AnchorStyles.Right)
        self.chkListPatterns.ItemCheck += self.OnItemCheck
        self.chkListPatterns.MouseDown += self.OnMouseDown
        self.Controls.Add(self.chkListPatterns)
        
        self.lblCount = Label()
        self.lblCount.Text = "Total: 0 | Checked: 0"
        self.lblCount.Font = Font("Arial", 10, FontStyle.Bold)
        self.lblCount.Location = Point(15, 500)
        self.lblCount.Size = Size(250, 25)
        self.lblCount.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.lblCount)
        
        self.pnlButtons = Panel()
        self.pnlButtons.Location = Point(15, 525)
        self.pnlButtons.Size = Size(455, 45)
        self.pnlButtons.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        self.Controls.Add(self.pnlButtons)
        
        self.btnCheckAll = Button()
        self.btnCheckAll.Text = "Check All"
        self.btnCheckAll.Location = Point(0, 5)
        self.btnCheckAll.Size = Size(85, 35)
        self.btnCheckAll.Font = Font("Arial", 9)
        self.btnCheckAll.BackColor = Color.FromArgb(40, 167, 69)
        self.btnCheckAll.ForeColor = Color.White
        self.btnCheckAll.FlatStyle = FlatStyle.Flat
        self.btnCheckAll.Click += self.OnCheckAllClick
        self.pnlButtons.Controls.Add(self.btnCheckAll)
        
        self.btnUncheckAll = Button()
        self.btnUncheckAll.Text = "Uncheck All"
        self.btnUncheckAll.Location = Point(95, 5)
        self.btnUncheckAll.Size = Size(85, 35)
        self.btnUncheckAll.Font = Font("Arial", 9)
        self.btnUncheckAll.BackColor = Color.FromArgb(108, 117, 125)
        self.btnUncheckAll.ForeColor = Color.White
        self.btnUncheckAll.FlatStyle = FlatStyle.Flat
        self.btnUncheckAll.Click += self.OnUncheckAllClick
        self.pnlButtons.Controls.Add(self.btnUncheckAll)
        
        self.btnInvert = Button()
        self.btnInvert.Text = "Invert"
        self.btnInvert.Location = Point(190, 5)
        self.btnInvert.Size = Size(65, 35)
        self.btnInvert.Font = Font("Arial", 9)
        self.btnInvert.BackColor = Color.FromArgb(23, 162, 184)
        self.btnInvert.ForeColor = Color.White
        self.btnInvert.FlatStyle = FlatStyle.Flat
        self.btnInvert.Click += self.OnInvertClick
        self.pnlButtons.Controls.Add(self.btnInvert)
        
        self.btnDelete = Button()
        self.btnDelete.Text = "Delete Checked"
        self.btnDelete.Location = Point(265, 5)
        self.btnDelete.Size = Size(110, 35)
        self.btnDelete.Font = Font("Arial", 9, FontStyle.Bold)
        self.btnDelete.BackColor = Color.FromArgb(220, 53, 69)
        self.btnDelete.ForeColor = Color.White
        self.btnDelete.FlatStyle = FlatStyle.Flat
        self.btnDelete.Click += self.OnDeleteClick
        self.btnDelete.Enabled = False
        self.pnlButtons.Controls.Add(self.btnDelete)
        
        self.btnClose = Button()
        self.btnClose.Text = "Close"
        self.btnClose.Location = Point(385, 5)
        self.btnClose.Size = Size(70, 35)
        self.btnClose.Font = Font("Arial", 9)
        self.btnClose.BackColor = Color.FromArgb(52, 58, 64)
        self.btnClose.ForeColor = Color.White
        self.btnClose.FlatStyle = FlatStyle.Flat
        self.btnClose.Click += self.OnCloseClick
        self.pnlButtons.Controls.Add(self.btnClose)
    
    def LoadLinePatterns(self):
        collector = FilteredElementCollector(self.doc).OfClass(LinePatternElement)
        self.line_patterns = sorted(collector.ToElements(), key=lambda x: x.Name)
        self.filtered_patterns = list(self.line_patterns)
        self.UpdateListBox()
    
    def UpdateListBox(self):
        self.chkListPatterns.Items.Clear()
        for pattern in self.filtered_patterns:
            self.chkListPatterns.Items.Add(pattern.Name)
        self.last_clicked_index = -1
        self.UpdateCount()
    
    def UpdateCount(self):
        total = self.chkListPatterns.Items.Count
        checked = self.chkListPatterns.CheckedItems.Count
        self.lblCount.Text = "Total: {0} | Checked: {1}".format(total, checked)
        self.btnDelete.Enabled = checked > 0
    
    def OnSearchTextChanged(self, sender, args):
        search_text = self.txtSearch.Text.lower()
        if search_text:
            self.filtered_patterns = [p for p in self.line_patterns 
                                       if search_text in p.Name.lower()]
        else:
            self.filtered_patterns = list(self.line_patterns)
        self.UpdateListBox()
    
    def OnClearSearchClick(self, sender, args):
        self.txtSearch.Text = ""
    
    def OnMouseDown(self, sender, args):
        index = self.chkListPatterns.IndexFromPoint(args.Location)
        
        if index < 0 or index >= self.chkListPatterns.Items.Count:
            return
        
        if Control.ModifierKeys == Keys.Shift:
            if self.last_clicked_index >= 0 and self.last_clicked_index < self.chkListPatterns.Items.Count:
                new_state = not self.chkListPatterns.GetItemChecked(index)
                start_idx = min(self.last_clicked_index, index)
                end_idx = max(self.last_clicked_index, index)
                
                for i in range(start_idx, end_idx + 1):
                    self.chkListPatterns.SetItemChecked(i, new_state)
                
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
        for i in range(self.chkListPatterns.Items.Count):
            self.chkListPatterns.SetItemChecked(i, True)
        self.UpdateCount()
    
    def OnUncheckAllClick(self, sender, args):
        for i in range(self.chkListPatterns.Items.Count):
            self.chkListPatterns.SetItemChecked(i, False)
        self.UpdateCount()
    
    def OnInvertClick(self, sender, args):
        for i in range(self.chkListPatterns.Items.Count):
            current_state = self.chkListPatterns.GetItemChecked(i)
            self.chkListPatterns.SetItemChecked(i, not current_state)
        self.UpdateCount()
    
    def OnDeleteClick(self, sender, args):
        checked_indices = list(self.chkListPatterns.CheckedIndices)
        
        if not checked_indices:
            MessageBox.Show("No line patterns checked.", "Warning",
                           MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        
        selected_patterns = [self.filtered_patterns[i] for i in checked_indices]
        selected_names = [p.Name for p in selected_patterns]
        
        confirm_msg = "Are you sure you want to delete {0} line pattern(s)?\n\n".format(len(selected_names))
        if len(selected_names) <= 15:
            confirm_msg += "Patterns to delete:\n"
            for name in selected_names:
                confirm_msg += "  - {0}\n".format(name)
        else:
            confirm_msg += "First 15 patterns:\n"
            for name in selected_names[:15]:
                confirm_msg += "  - {0}\n".format(name)
            confirm_msg += "  ... and {0} more\n".format(len(selected_names) - 15)
        
        confirm_msg += "\nNote: System patterns and patterns in use cannot be deleted.\nThis action cannot be undone!"
        
        result = MessageBox.Show(confirm_msg, "Confirm Deletion",
                                 MessageBoxButtons.YesNo, MessageBoxIcon.Warning)
        
        if result == DialogResult.Yes:
            self.DeletePatterns(selected_patterns)
    
    def DeletePatterns(self, patterns_to_delete):
        deleted_count = 0
        deleted_names = []
        failed_patterns = []
        in_use_patterns = []
        system_patterns = []
        
        # Main transaction
        t = Transaction(self.doc, "Delete Line Patterns")
        t.Start()
        
        try:
            for pattern in patterns_to_delete:
                pattern_name = pattern.Name
                pattern_id = pattern.Id
                
                # Use SubTransaction to isolate failures
                sub_t = SubTransaction(self.doc)
                try:
                    sub_t.Start()
                    
                    # Validate element still exists
                    if self.doc.GetElement(pattern_id) is None:
                        sub_t.RollBack()
                        failed_patterns.append("{0}: Element no longer exists".format(pattern_name))
                        continue
                    
                    # Attempt deletion
                    deleted_ids = self.doc.Delete(pattern_id)
                    
                    if deleted_ids and deleted_ids.Count > 0:
                        sub_t.Commit()
                        deleted_count += 1
                        deleted_names.append(pattern_name)
                    else:
                        sub_t.RollBack()
                        failed_patterns.append("{0}: Could not be deleted".format(pattern_name))
                        
                except Exception as e:
                    if sub_t.HasStarted() and not sub_t.HasEnded():
                        sub_t.RollBack()
                    
                    error_msg = str(e)
                    
                    if "system" in error_msg.lower() or "built-in" in error_msg.lower():
                        system_patterns.append(pattern_name)
                    elif ("in use" in error_msg.lower() or 
                          "cannot" in error_msg.lower() or 
                          "referenced" in error_msg.lower() or
                          "not valid" in error_msg.lower()):
                        in_use_patterns.append(pattern_name)
                    else:
                        short_error = error_msg[:80] + "..." if len(error_msg) > 80 else error_msg
                        failed_patterns.append("{0}: {1}".format(pattern_name, short_error))
            
            t.Commit()
            
        except Exception as e:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            MessageBox.Show("Main transaction failed: {0}".format(str(e)), "Error",
                           MessageBoxButtons.OK, MessageBoxIcon.Error)
            return
        
        # Build result message
        result_msg = "========== DELETION SUMMARY ==========\n\n"
        result_msg += "Successfully deleted: {0}\n".format(deleted_count)
        
        if deleted_names:
            result_msg += "\n--- Deleted Patterns ---\n"
            for name in deleted_names[:15]:
                result_msg += "  [OK] {0}\n".format(name)
            if len(deleted_names) > 15:
                result_msg += "  ... and {0} more\n".format(len(deleted_names) - 15)
        
        if system_patterns:
            result_msg += "\n--- System Patterns (Cannot Delete): {0} ---\n".format(len(system_patterns))
            for name in system_patterns[:10]:
                result_msg += "  [X] {0}\n".format(name)
            if len(system_patterns) > 10:
                result_msg += "  ... and {0} more\n".format(len(system_patterns) - 10)
        
        if in_use_patterns:
            result_msg += "\n--- In Use or Protected: {0} ---\n".format(len(in_use_patterns))
            for name in in_use_patterns[:10]:
                result_msg += "  [X] {0}\n".format(name)
            if len(in_use_patterns) > 10:
                result_msg += "  ... and {0} more\n".format(len(in_use_patterns) - 10)
        
        if failed_patterns:
            result_msg += "\n--- Failed: {0} ---\n".format(len(failed_patterns))
            for msg in failed_patterns[:10]:
                result_msg += "  [!] {0}\n".format(msg)
            if len(failed_patterns) > 10:
                result_msg += "  ... and {0} more\n".format(len(failed_patterns) - 10)
        
        result_msg += "\n========================================"
        
        MessageBox.Show(result_msg, "Deletion Complete",
                       MessageBoxButtons.OK, MessageBoxIcon.Information)
        
        self.LoadLinePatterns()
        self.txtSearch.Text = ""
    
    def OnCloseClick(self, sender, args):
        self.Close()


# Run the form
form = LinePatternManagerForm(doc)
form.ShowDialog()