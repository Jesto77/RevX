# -*- coding: utf-8 -*-
"""RevitBot - Chat UI with Groq AI integration via background thread."""

import clr
import os
import sys
import traceback

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *

import System.Windows.Forms as WinForms
import System.Drawing as Drawing
import System.Threading

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from revit_tools import RevitTools
from chat_engine import ChatEngine
from chat_engine import COMMAND_PATTERNS


# ── API Key Setup Dialog ────────────────────────────────────────────────

class ApiKeyDialog(WinForms.Form):
    def __init__(self, engine):
        self.engine = engine
        self.saved = False
        self.Text = "RevitBot - API Key Setup"
        self.Size = Drawing.Size(440, 280)
        self.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.MinimizeBox = False
        self.StartPosition = WinForms.FormStartPosition.CenterScreen
        self.BackColor = Drawing.Color.FromArgb(30, 30, 46)
        self.TopMost = False

        # Instructions
        info_label = WinForms.Label()
        info_label.Text = "Enter your free Groq API key to enable AI chat."
        info_label.ForeColor = Drawing.Color.FromArgb(200, 200, 220)
        info_label.Font = Drawing.Font("Segoe UI", 10.0)
        info_label.Location = Drawing.Point(16, 12)
        info_label.AutoSize = True
        self.Controls.Add(info_label)

        # Get key link
        link_label = WinForms.LinkLabel()
        link_label.Text = "Get a free key at: console.groq.com"
        link_label.LinkColor = Drawing.Color.FromArgb(0, 180, 216)
        link_label.Font = Drawing.Font("Segoe UI", 10.0)
        link_label.Location = Drawing.Point(16, 36)
        link_label.AutoSize = True
        link_label.LinkClicked += self._on_link_click
        self.Controls.Add(link_label)

        # Steps
        steps_label = WinForms.Label()
        steps_label.Text = "Steps: 1) Sign up at Groq  2) Go to API Keys  3) Create key  4) Paste below"
        steps_label.ForeColor = Drawing.Color.FromArgb(140, 140, 160)
        steps_label.Font = Drawing.Font("Segoe UI", 8.0)
        steps_label.Location = Drawing.Point(16, 58)
        steps_label.AutoSize = True
        self.Controls.Add(steps_label)

        # Key TextBox (multiline for easy paste)
        self.key_box = WinForms.TextBox()
        self.key_box.Size = Drawing.Size(400, 50)
        self.key_box.Location = Drawing.Point(16, 82)
        self.key_box.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        self.key_box.ForeColor = Drawing.Color.FromArgb(224, 224, 224)
        self.key_box.Font = Drawing.Font("Consolas", 10.0)
        self.key_box.BorderStyle = WinForms.BorderStyle.FixedSingle
        self.key_box.Multiline = True
        self.key_box.ScrollBars = WinForms.ScrollBars.Vertical
        self.key_box.Text = self.engine.api_key if self.engine.api_key else ""
        self.Controls.Add(self.key_box)

        # Paste button
        paste_btn = WinForms.Button()
        paste_btn.Text = "Paste from Clipboard"
        paste_btn.Size = Drawing.Size(150, 26)
        paste_btn.Location = Drawing.Point(16, 138)
        paste_btn.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        paste_btn.ForeColor = Drawing.Color.FromArgb(0, 180, 216)
        paste_btn.FlatStyle = WinForms.FlatStyle.Flat
        paste_btn.Font = Drawing.Font("Segoe UI", 9.0)
        paste_btn.Click += self._on_paste
        self.Controls.Add(paste_btn)

        # Status
        self.status_label = WinForms.Label()
        self.status_label.Text = ""
        self.status_label.ForeColor = Drawing.Color.FromArgb(0, 200, 83)
        self.status_label.Font = Drawing.Font("Segoe UI", 9.0)
        self.status_label.Location = Drawing.Point(16, 170)
        self.status_label.AutoSize = True
        self.Controls.Add(self.status_label)

        # Save button
        save_btn = WinForms.Button()
        save_btn.Text = "Save Key"
        save_btn.Size = Drawing.Size(120, 32)
        save_btn.Location = Drawing.Point(130, 200)
        save_btn.BackColor = Drawing.Color.FromArgb(0, 119, 182)
        save_btn.ForeColor = Drawing.Color.White
        save_btn.FlatStyle = WinForms.FlatStyle.Flat
        save_btn.Font = Drawing.Font("Segoe UI", 10.0, Drawing.FontStyle.Bold)
        save_btn.Click += self._on_save
        self.Controls.Add(save_btn)

        # Cancel button
        cancel_btn = WinForms.Button()
        cancel_btn.Text = "Cancel"
        cancel_btn.Size = Drawing.Size(80, 32)
        cancel_btn.Location = Drawing.Point(260, 200)
        cancel_btn.BackColor = Drawing.Color.FromArgb(60, 60, 80)
        cancel_btn.ForeColor = Drawing.Color.FromArgb(180, 180, 180)
        cancel_btn.FlatStyle = WinForms.FlatStyle.Flat
        cancel_btn.Font = Drawing.Font("Segoe UI", 9.0)
        cancel_btn.Click += self._on_cancel
        self.Controls.Add(cancel_btn)

        # Enter key
        self.key_box.KeyUp += self._on_key_up

    def _on_link_click(self, sender, e):
        System.Diagnostics.Process.Start("https://console.groq.com/keys")

    def _on_paste(self, sender, e):
        try:
            clip_text = WinForms.Clipboard.GetText()
            if clip_text and len(clip_text) > 0:
                self.key_box.Text = clip_text.strip()
            else:
                self.status_label.ForeColor = Drawing.Color.FromArgb(255, 193, 7)
                self.status_label.Text = "Nothing on clipboard. Copy the key first, then click Paste."
        except Exception:
            self.status_label.ForeColor = Drawing.Color.FromArgb(244, 67, 54)
            self.status_label.Text = "Could not read clipboard. Try Ctrl+V in the text box."

    def _on_save(self, sender, e):
        key = self.key_box.Text.strip()
        if len(key) < 10:
            self.status_label.ForeColor = Drawing.Color.FromArgb(244, 67, 54)
            self.status_label.Text = "Key too short. Please paste the full key."
            return
        if self.engine.save_api_key(key):
            self.saved = True
            self.status_label.ForeColor = Drawing.Color.FromArgb(0, 200, 83)
            self.status_label.Text = "Key saved! AI chat is now enabled."
            self.DialogResult = WinForms.DialogResult.OK
            self.Close()
        else:
            self.status_label.ForeColor = Drawing.Color.FromArgb(244, 67, 54)
            self.status_label.Text = "Error saving key."

    def _on_cancel(self, sender, e):
        self.DialogResult = WinForms.DialogResult.Cancel
        self.Close()

    def _on_key_up(self, sender, e):
        if e.KeyCode == WinForms.Keys.Enter:
            self._on_save(None, None)


# ── Main Chat Form ─────────────────────────────────────────────────────

class RevitBotForm(WinForms.Form):
    def __init__(self, doc, uidoc):
        self.doc = doc
        self.uidoc = uidoc
        self.tools = RevitTools(doc, uidoc)
        self.engine = ChatEngine(self.tools)
        self.is_busy = False
        # Thread result fields (written by bg thread, read by UI timer)
        self._ai_done = False
        self._ai_result = None
        self._ai_error = None
        self._poll_timer = None

        # Form setup
        self.Text = "RevitBot"
        self.Size = Drawing.Size(480, 560)
        self.BackColor = Drawing.Color.FromArgb(30, 30, 46)
        self.MaximizeBox = False
        self.TopMost = False
        self.StartPosition = WinForms.FormStartPosition.CenterScreen

        # ── Header Panel ──
        header = WinForms.Panel()
        header.BackColor = Drawing.Color.FromArgb(13, 27, 42)
        header.Size = Drawing.Size(480, 40)
        header.Location = Drawing.Point(0, 0)
        self.Controls.Add(header)

        title_lbl = WinForms.Label()
        title_lbl.Text = "RevitBot"
        title_lbl.ForeColor = Drawing.Color.FromArgb(0, 180, 216)
        title_lbl.Font = Drawing.Font("Segoe UI", 14.0, Drawing.FontStyle.Bold)
        title_lbl.Location = Drawing.Point(12, 8)
        title_lbl.AutoSize = True
        header.Controls.Add(title_lbl)

        status_lbl = WinForms.Label()
        status_lbl.Text = "Ready"
        status_lbl.ForeColor = Drawing.Color.FromArgb(0, 200, 83)
        status_lbl.Font = Drawing.Font("Segoe UI", 9.0)
        status_lbl.Location = Drawing.Point(100, 16)
        status_lbl.AutoSize = True
        header.Controls.Add(status_lbl)
        self.status_lbl = status_lbl

        # Key button
        key_btn = WinForms.Button()
        key_btn.Text = "Key"
        key_btn.Size = Drawing.Size(60, 28)
        key_btn.Location = Drawing.Point(380, 6)
        key_btn.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        key_btn.ForeColor = Drawing.Color.FromArgb(0, 180, 216)
        key_btn.FlatStyle = WinForms.FlatStyle.Flat
        key_btn.Font = Drawing.Font("Segoe UI", 9.0, Drawing.FontStyle.Bold)
        key_btn.Click += self._on_key_btn
        header.Controls.Add(key_btn)

        # Clear button
        clear_btn = WinForms.Button()
        clear_btn.Text = "Clear"
        clear_btn.Size = Drawing.Size(55, 28)
        clear_btn.Location = Drawing.Point(320, 6)
        clear_btn.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        clear_btn.ForeColor = Drawing.Color.FromArgb(180, 180, 180)
        clear_btn.FlatStyle = WinForms.FlatStyle.Flat
        clear_btn.Font = Drawing.Font("Segoe UI", 9.0)
        clear_btn.Click += self._on_clear
        header.Controls.Add(clear_btn)

        # ── Chat Display (RichTextBox) ──
        self.chat_box = WinForms.RichTextBox()
        self.chat_box.BackColor = Drawing.Color.FromArgb(20, 20, 36)
        self.chat_box.ForeColor = Drawing.Color.FromArgb(220, 220, 220)
        self.chat_box.Font = Drawing.Font("Segoe UI", 10.0)
        self.chat_box.Size = Drawing.Size(460, 340)
        self.chat_box.Location = Drawing.Point(8, 48)
        self.chat_box.ReadOnly = True
        self.chat_box.BorderStyle = WinForms.BorderStyle.FixedSingle
        self.chat_box.ScrollBars = WinForms.RichTextBoxScrollBars.Vertical
        self.chat_box.Text = ""
        self.Controls.Add(self.chat_box)

        # ── Quick Buttons ──
        btn_panel = WinForms.Panel()
        btn_panel.BackColor = Drawing.Color.FromArgb(13, 27, 42)
        btn_panel.Size = Drawing.Size(480, 36)
        btn_panel.Location = Drawing.Point(0, 394)
        self.Controls.Add(btn_panel)

        quick_cmds = [
            ("NWC", "export nwc"),
            ("Save", "save"),
            ("Fill", "create filled region"),
            ("Sheet", "create sheet"),
            ("Room", "create room"),
            ("PDF", "export pdf"),
            ("IFC", "export ifc"),
            ("Info", "doc info"),
            ("Help", "help"),
        ]
        x = 6
        for label, cmd in quick_cmds:
            btn = WinForms.Button()
            btn.Text = label
            btn.Size = Drawing.Size(48, 26)
            btn.Location = Drawing.Point(x, 5)
            btn.BackColor = Drawing.Color.FromArgb(27, 40, 56)
            btn.ForeColor = Drawing.Color.FromArgb(170, 170, 221)
            btn.FlatStyle = WinForms.FlatStyle.Flat
            btn.FlatAppearance.BorderColor = Drawing.Color.FromArgb(0, 119, 182)
            btn.Font = Drawing.Font("Segoe UI", 8.0)
            btn.Tag = cmd
            btn.Click += self._on_quick
            btn_panel.Controls.Add(btn)
            x += 50

        # ── Thinking indicator label ──
        self.thinking_lbl = WinForms.Label()
        self.thinking_lbl.Text = ""
        self.thinking_lbl.ForeColor = Drawing.Color.FromArgb(255, 193, 7)
        self.thinking_lbl.Font = Drawing.Font("Segoe UI", 9.0)
        self.thinking_lbl.Location = Drawing.Point(8, 432)
        self.thinking_lbl.AutoSize = True
        self.Controls.Add(self.thinking_lbl)

        # ── Input ──
        self.input_box = WinForms.TextBox()
        self.input_box.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        self.input_box.ForeColor = Drawing.Color.FromArgb(224, 224, 224)
        self.input_box.Font = Drawing.Font("Segoe UI", 11.0)
        self.input_box.Size = Drawing.Size(360, 26)
        self.input_box.Location = Drawing.Point(8, 452)
        self.input_box.BorderStyle = WinForms.BorderStyle.FixedSingle
        self.Controls.Add(self.input_box)

        # ── Send ──
        self.send_btn = WinForms.Button()
        self.send_btn.Text = "Send"
        self.send_btn.Size = Drawing.Size(90, 26)
        self.send_btn.Location = Drawing.Point(372, 452)
        self.send_btn.BackColor = Drawing.Color.FromArgb(0, 119, 182)
        self.send_btn.ForeColor = Drawing.Color.White
        self.send_btn.FlatStyle = WinForms.FlatStyle.Flat
        self.send_btn.Font = Drawing.Font("Segoe UI", 10.0, Drawing.FontStyle.Bold)
        self.send_btn.Click += self._on_send
        self.Controls.Add(self.send_btn)

        # ── Wire Enter key ──
        self.input_box.KeyUp += self._on_key_up

        # Welcome message
        self._add_msg("Bot", "Hey! I'm RevitBot.\n\n"
                     "I can run Revit commands instantly, or chat with AI about anything.\n\n"
                     "Type 'help' for commands, or ask me any question!\n\n"
                     "To enable AI chat, click the Key button and add your free Groq API key.")
        self._update_status()

    # ── Message Display ─────────────────────────────────────────────────

    def _add_msg(self, sender, text):
        self.chat_box.SelectionStart = self.chat_box.TextLength
        self.chat_box.SelectionLength = 0
        if sender == "You":
            hdr_color = Drawing.Color.FromArgb(0, 180, 216)
        elif sender == "Bot":
            hdr_color = Drawing.Color.FromArgb(0, 200, 83)
        elif sender == "AI":
            hdr_color = Drawing.Color.FromArgb(168, 85, 247)
        elif sender == "[OK]":
            hdr_color = Drawing.Color.FromArgb(255, 193, 7)
        elif sender == "[ERR]":
            hdr_color = Drawing.Color.FromArgb(244, 67, 54)
        else:
            hdr_color = Drawing.Color.FromArgb(180, 180, 200)
        self.chat_box.SelectionColor = hdr_color
        self.chat_box.SelectionFont = Drawing.Font("Segoe UI", 10.0, Drawing.FontStyle.Bold)
        self.chat_box.AppendText(sender + ":  ")
        body_color = Drawing.Color.FromArgb(220, 220, 220)
        if sender == "AI":
            body_color = Drawing.Color.FromArgb(230, 230, 240)
        self.chat_box.SelectionColor = body_color
        self.chat_box.SelectionFont = Drawing.Font("Segoe UI", 10.0)
        self.chat_box.AppendText(text)
        self.chat_box.AppendText("\n\n")
        self.chat_box.SelectionStart = self.chat_box.TextLength
        self.chat_box.ScrollToCaret()

    # ── Status ──────────────────────────────────────────────────────────

    def _update_status(self):
        if self.engine.has_api_key():
            self.status_lbl.Text = "AI Ready"
            self.status_lbl.ForeColor = Drawing.Color.FromArgb(0, 200, 83)
        else:
            self.status_lbl.Text = "No AI Key"
            self.status_lbl.ForeColor = Drawing.Color.FromArgb(255, 193, 7)

    # ── Send Message ────────────────────────────────────────────────────


    # Interactive actions mapping
    INTERACTIVE_ACTIONS = {
        "export_nwc", "export_ifc", "export_dwg", "export_pdf",
        "save_file", "create_sheet", "create_room", "create_level",
        "create_grid", "create_section", "create_callout",
        "create_dimension", "create_text_note", "create_filled_region",
        "create_filled_region_pick", "create_room_tag",
        "create_workset", "create_view_filter",
        "rename_sheet", "rename_sheet_number",
        "delete_sheet", "delete_view", "delete_selected", "delete_elements_by_category",
        "open_view", "zoom_to_fit", "enable_worksharing"
    }
    INFO_ACTIONS = {
        "list_views", "list_sheets", "list_levels", "list_families",
        "list_worksets", "list_materials", "list_categories", "doc_info"
    }

    def _confirm_dialog(self, title, message):
        result = WinForms.MessageBox.Show(
            self,
            message, title,
            WinForms.MessageBoxButtons.YesNo,
            WinForms.MessageBoxIcon.Question
        )
        return result == WinForms.DialogResult.Yes

    # ── Send Message ────────────────────────────────────────────────────

    def _send(self):
        if self.is_busy:
            return
        msg = self.input_box.Text
        if msg is None:
            return
        msg = msg.strip()
        if len(msg) == 0:
            return
        self.input_box.Text = ""
        # Display user message
        self._add_msg("You", msg)

        # Try to match command name for interaction
        cmd_name = None
        msg_lower = msg.lower()
        for pattern, name in COMMAND_PATTERNS:
            if re.match(pattern, msg_lower):
                cmd_name = name
                break

        # Interactive confirmation and file path selection
        if cmd_name in self.INTERACTIVE_ACTIONS:
            confirm_text = "Proceed with: {}?".format(cmd_name.replace("_", " ").title())
            if cmd_name == "export_nwc":
                confirm_text = ("NWC Export Settings:\n"
                                "  Export Scope: Model\n"
                                "  Coordinates: Shared\n"
                                "  Convert Properties: True\n"
                                "  Export Links: True\n"
                                "  Room as Space: True\n"
                                "  URLs: True\n\nIs this okay?")
            elif cmd_name == "export_ifc":
                confirm_text = "Export IFC: File Version IFC4\nProceed?"
            elif cmd_name == "export_dwg":
                confirm_text = "Export DWG: Shared Coords = True, Merged Views = True\nProceed?"
            elif cmd_name == "export_pdf":
                confirm_text = "Export PDF: Combine = True\nProceed?"
            elif cmd_name == "save_file":
                confirm_text = "Save file? (Will ask for path next)"
            elif cmd_name == "delete_sheet":
                confirm_text = "Delete sheet? This cannot be undone."
            elif cmd_name == "delete_view":
                confirm_text = "Delete view? This cannot be undone."
            elif cmd_name == "delete_selected":
                confirm_text = "Delete selected elements? This cannot be undone."
            elif cmd_name == "delete_elements_by_category":
                confirm_text = "Delete all elements in category? This cannot be undone."
            elif cmd_name == "rename_sheet_number":
                confirm_text = "Renumber sheet?"
            elif cmd_name == "rename_sheet":
                confirm_text = "Rename sheet?"
            elif cmd_name == "open_view":
                confirm_text = "Open/activate view?"

            if not self._confirm_dialog("Confirm Action", confirm_text):
                self._add_msg("Bot", "Action cancelled.")
                self.input_box.Focus()
                return

            selected_path = None
            needs_path = cmd_name in ["save_file", "export_nwc", "export_ifc", "export_dwg", "export_pdf"]
            if needs_path:
                path_msg = ("Use default file path?\n"
                            "(Yes = default path, No = choose custom path)")
                use_default = WinForms.MessageBox.Show(
                    self,
                    path_msg, "File Path",
                    WinForms.MessageBoxButtons.YesNo,
                    WinForms.MessageBoxIcon.Question
                )
                if use_default == WinForms.DialogResult.No:
                    dlg = WinForms.SaveFileDialog()
                    dlg.Owner = self
                    filters = {
                        "save_file": "Revit Project (*.rvt)|*.rvt",
                        "export_nwc": "Navisworks Cache (*.nwc)|*.nwc",
                        "export_pdf": "PDF (*.pdf)|*.pdf",
                        "export_dwg": "DWG (*.dwg)|*.dwg",
                        "export_ifc": "IFC (*.ifc)|*.ifc"
                    }
                    dlg.Filter = filters.get(cmd_name, "All files (*.*)|*.*")
                    dlg.Title = "Select file location"
                    dlg.OverwritePrompt = True
                    if dlg.ShowDialog() == WinForms.DialogResult.OK:
                        selected_path = dlg.FileName
                    else:
                        self._add_msg("Bot", "Action cancelled - no path selected.")
                        self.input_box.Focus()
                        return
                else:
                    folder = None
                    if cmd_name == "save_file" and self.doc.PathName and len(self.doc.PathName) > 2:
                        folder = os.path.dirname(self.doc.PathName)
                        selected_path = self.doc.PathName
                    else:
                        doc_path = self.doc.PathName
                        folder = os.path.dirname(doc_path) if doc_path and len(doc_path) > 2 else os.path.join(os.path.expanduser("~"), "Desktop")
                        filename = os.path.splitext(os.path.basename(self.doc.Title or "Untitled"))[0]
                        ext = {
                            "export_nwc": ".nwc",
                            "export_pdf": ".pdf",
                            "export_dwg": ".dwg",
                            "export_ifc": ".ifc"
                        }.get(cmd_name, "")
                        selected_path = os.path.join(folder, filename + ext)
                if selected_path:
                    msg = msg + " " + selected_path

            try:
                local_result = self.engine.process_message(msg)
            except Exception as ex:
                self._add_msg("[ERR]", "Internal error: {}".format(str(ex)))
                self.input_box.Focus()
                return

            if local_result is not None:
                for text, msg_type in local_result:
                    if msg_type == "tool":
                        self._add_msg("[OK]", text)
                    elif msg_type == "error":
                        self._add_msg("[ERR]", text)
                    else:
                        self._add_msg("Bot", text)
                if self.engine._pending_view_id is not None:
                    view_id = self.engine._pending_view_id
                    self.engine._pending_view_id = None
                    try:
                        self.uidoc.ActiveViewId = view_id
                    except:
                        try:
                            self.uidoc.ActiveView = self.doc.GetElement(view_id)
                        except:
                            try:
                                command_id = RevitCommandId.LookupPostableCommand(PostableCommand.ActivateView)
                                self.uidoc.Document.Application.PostCommand(command_id)
                            except:
                                pass
                    self._add_msg("Bot", "View activated. Bot stays open.")
                self.input_box.Focus()
                return

        elif cmd_name in self.INFO_ACTIONS:
            try:
                local_result = self.engine.process_message(msg)
            except Exception as ex:
                self._add_msg("[ERR]", "Internal error: {}".format(str(ex)))
                self.input_box.Focus()
                return
            if local_result is not None:
                for text, msg_type in local_result:
                    if msg_type == "tool":
                        self._add_msg("[OK]", text)
                    elif msg_type == "error":
                        self._add_msg("[ERR]", text)
                    else:
                        self._add_msg("Bot", text)
            self.input_box.Focus()
            return

        try:
            local_result = self.engine.process_message(msg)
        except Exception as ex:
            self._add_msg("[ERR]", "Internal error: {}".format(str(ex)))
            self.input_box.Focus()
            return

        if local_result is not None:
            for text, msg_type in local_result:
                if msg_type == "tool":
                    self._add_msg("[OK]", text)
                elif msg_type == "error":
                    self._add_msg("[ERR]", text)
                else:
                    self._add_msg("Bot", text)
            if self.engine._pending_view_id is not None:
                view_id = self.engine._pending_view_id
                self.engine._pending_view_id = None
                try:
                    self.uidoc.ActiveViewId = view_id
                except:
                    try:
                        self.uidoc.ActiveView = self.doc.GetElement(view_id)
                    except:
                        try:
                            command_id = RevitCommandId.LookupPostableCommand(PostableCommand.ActivateView)
                            self.uidoc.Document.Application.PostCommand(command_id)
                        except:
                            pass
                self._add_msg("Bot", "View activated. Bot stays open.")
            self.input_box.Focus()
            return

        # No local command matched - needs AI
        if not self.engine.has_api_key():
            self._add_msg("Bot", "I don't know that command, and AI chat is not set up yet.\n\n"
                         "Click the 'Key' button above to add your free Groq API key,\n"
                         "then I can answer any question!")
            self.input_box.Focus()
            return
        self._start_ai_query(msg)

# ── Entry Point ─────────────────────────────────────────────────────────

try:
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument

    def show_bot():
        form = RevitBotForm(doc, uidoc)
        form.StartPosition = WinForms.FormStartPosition.Manual
        form.Location = Drawing.Point(900, 400)
        form.Size = Drawing.Size(400, 500)
        form.Show()

    bot_thread = System.Threading.Thread(System.Threading.ThreadStart(show_bot))
    bot_thread.IsBackground = True
    bot_thread.Start()
except Exception as ex:
    WinForms.MessageBox.Show(
        "RevitBot Error:\n\n{}".format(traceback.format_exc()),
        "RevitBot Error",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Error
    )
