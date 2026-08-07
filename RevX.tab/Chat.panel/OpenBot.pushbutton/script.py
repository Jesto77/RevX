# -*- coding: utf-8 -*-
"""RevitBot - Chat UI with Groq AI integration via background thread."""

import clr
import os
import sys
import traceback
import System

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
        self.TopMost = True

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

        # Key TextBox
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
                self.status_label.Text = "Nothing on clipboard. Copy the key first."
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
        self._pending_view_id = None

        # Thread result fields
        self._ai_done = False
        self._ai_result = None
        self._ai_error = None
        self._poll_timer = None

        # ── Form setup ───────────────────────────────────────────────
        self.Text = "RevitBot"
        # Height reduced since quick buttons panel is removed
        self.Size = Drawing.Size(480, 520)
        self.BackColor = Drawing.Color.FromArgb(30, 30, 46)
        self.MaximizeBox = False
        self.TopMost = True
        self.StartPosition = WinForms.FormStartPosition.CenterScreen

        # ── Header ───────────────────────────────────────────────────
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

        self.status_lbl = WinForms.Label()
        self.status_lbl.Text = "Ready"
        self.status_lbl.ForeColor = Drawing.Color.FromArgb(0, 200, 83)
        self.status_lbl.Font = Drawing.Font("Segoe UI", 9.0)
        self.status_lbl.Location = Drawing.Point(100, 16)
        self.status_lbl.AutoSize = True
        header.Controls.Add(self.status_lbl)

        # Key button
        key_btn = WinForms.Button()
        key_btn.Text = "Key"
        key_btn.Size = Drawing.Size(60, 28)
        key_btn.Location = Drawing.Point(408, 6)
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
        clear_btn.Location = Drawing.Point(348, 6)
        clear_btn.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        clear_btn.ForeColor = Drawing.Color.FromArgb(180, 180, 180)
        clear_btn.FlatStyle = WinForms.FlatStyle.Flat
        clear_btn.Font = Drawing.Font("Segoe UI", 9.0)
        clear_btn.Click += self._on_clear
        header.Controls.Add(clear_btn)

        # ── Chat Display ─────────────────────────────────────────────
        # Taller now that quick buttons are removed — more space for chat
        self.chat_box = WinForms.RichTextBox()
        self.chat_box.BackColor = Drawing.Color.FromArgb(20, 20, 36)
        self.chat_box.ForeColor = Drawing.Color.FromArgb(220, 220, 220)
        self.chat_box.Font = Drawing.Font("Segoe UI", 10.0)
        self.chat_box.Size = Drawing.Size(462, 390)
        self.chat_box.Location = Drawing.Point(8, 48)
        self.chat_box.ReadOnly = True
        self.chat_box.BorderStyle = WinForms.BorderStyle.None
        self.chat_box.ScrollBars = WinForms.RichTextBoxScrollBars.Vertical
        self.chat_box.Text = ""
        self.Controls.Add(self.chat_box)

        # ── Thinking indicator ───────────────────────────────────────
        self.thinking_lbl = WinForms.Label()
        self.thinking_lbl.Text = ""
        self.thinking_lbl.ForeColor = Drawing.Color.FromArgb(255, 193, 7)
        self.thinking_lbl.Font = Drawing.Font("Segoe UI", 9.0)
        self.thinking_lbl.Location = Drawing.Point(8, 444)
        self.thinking_lbl.AutoSize = True
        self.Controls.Add(self.thinking_lbl)

        # ── Input box ────────────────────────────────────────────────
        self.input_box = WinForms.TextBox()
        self.input_box.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        self.input_box.ForeColor = Drawing.Color.FromArgb(224, 224, 224)
        self.input_box.Font = Drawing.Font("Segoe UI", 11.0)
        self.input_box.Size = Drawing.Size(360, 26)
        self.input_box.Location = Drawing.Point(8, 466)
        self.input_box.BorderStyle = WinForms.BorderStyle.FixedSingle
        self.Controls.Add(self.input_box)

        # ── Send button ──────────────────────────────────────────────
        self.send_btn = WinForms.Button()
        self.send_btn.Text = "Send"
        self.send_btn.Size = Drawing.Size(100, 26)
        self.send_btn.Location = Drawing.Point(372, 466)
        self.send_btn.BackColor = Drawing.Color.FromArgb(0, 119, 182)
        self.send_btn.ForeColor = Drawing.Color.White
        self.send_btn.FlatStyle = WinForms.FlatStyle.Flat
        self.send_btn.Font = Drawing.Font("Segoe UI", 10.0, Drawing.FontStyle.Bold)
        self.send_btn.Click += self._on_send
        self.Controls.Add(self.send_btn)

        # ── Enter key ────────────────────────────────────────────────
        self.input_box.KeyUp += self._on_key_up

        # ── Welcome message ──────────────────────────────────────────
        self._add_msg(
            "Bot",
            "Hey! I'm RevitBot.\n\n"
            "I can run Revit commands or answer any question about Revit, BIM, and AEC.\n\n"
            "Type 'help' to see available commands, or just ask me anything!\n\n"
            "To enable AI chat, click the Key button and add your free Groq API key."
        )

        self._update_status()

    # ── Message Display ─────────────────────────────────────────────────

    def _add_msg(self, sender, text):
        """Add a color-coded message to the RichTextBox."""
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

        # Header
        self.chat_box.SelectionColor = hdr_color
        self.chat_box.SelectionFont = Drawing.Font(
            "Segoe UI", 10.0, Drawing.FontStyle.Bold)
        self.chat_box.AppendText(sender + ":  ")

        # Body
        body_color = (
            Drawing.Color.FromArgb(230, 230, 240)
            if sender == "AI"
            else Drawing.Color.FromArgb(220, 220, 220)
        )
        self.chat_box.SelectionColor = body_color
        self.chat_box.SelectionFont = Drawing.Font("Segoe UI", 10.0)
        self.chat_box.AppendText(text)
        self.chat_box.AppendText("\n\n")

        # Scroll to bottom
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

    # ── Send ────────────────────────────────────────────────────────────

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
        self._add_msg("You", msg)

        # Try local commands first
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

            # Pending view to open
            if self.engine._pending_view_id is not None:
                view_id = self.engine._pending_view_id
                self.engine._pending_view_id = None
                self._pending_view_id = view_id
                self._add_msg("Bot", "Closing chat to open the view...")
                self.DialogResult = WinForms.DialogResult.OK
                self.Close()
                return

            self.input_box.Focus()
            return

        # No local command — needs AI
        if not self.engine.has_api_key():
            self._add_msg(
                "Bot",
                "I don't recognise that as a command, and AI chat is not set up yet.\n\n"
                "Click the 'Key' button to add your free Groq API key — "
                "then I can answer any question!"
            )
            self.input_box.Focus()
            return

        self._start_ai_query(msg)

    # ── Background AI Query ─────────────────────────────────────────────

    def _start_ai_query(self, message):
        """Run the AI HTTP call on a background thread.
        A WinForms Timer polls for completion so the UI never blocks."""

        self.is_busy = True
        self.input_box.Enabled = False
        self.send_btn.Enabled = False
        self.thinking_lbl.Text = "Thinking..."
        self.status_lbl.Text = "Thinking..."
        self.status_lbl.ForeColor = Drawing.Color.FromArgb(255, 193, 7)

        self._ai_done   = False
        self._ai_result = None
        self._ai_error  = None

        def do_work():
            try:
                result = self.engine.query_ai(message)
                self._ai_result = result
                self._ai_error  = None
            except Exception as ex:
                self._ai_result = None
                self._ai_error  = str(ex)
            self._ai_done = True

        thread = System.Threading.Thread(
            System.Threading.ThreadStart(do_work))
        thread.IsBackground = True
        thread.Start()

        self._poll_timer = WinForms.Timer()
        self._poll_timer.Interval = 250
        self._poll_timer.Tick += self._on_poll
        self._poll_timer.Start()

    def _on_poll(self, sender, e):
        """Timer tick — check if background thread finished. Runs on UI thread."""
        if not self._ai_done:
            return

        if self._poll_timer:
            self._poll_timer.Stop()
            self._poll_timer.Dispose()
            self._poll_timer = None

        if self._ai_error:
            self._add_msg("[ERR]", self._ai_error)
        elif self._ai_result:
            text, msg_type = self._ai_result
            self._add_msg("AI" if msg_type == "ai" else "Bot", text)
        else:
            self._add_msg("[ERR]", "No response received.")

        self._set_idle()

    def _set_idle(self):
        """Reset UI to ready state."""
        self.is_busy = False
        self.input_box.Enabled = True
        self.send_btn.Enabled = True
        self.thinking_lbl.Text = ""
        self._update_status()
        self.input_box.Focus()

    # ── Event Handlers ───────────────────────────────────────────────────

    def _on_send(self, sender, e):
        self._send()

    def _on_key_up(self, sender, e):
        if e.KeyCode == WinForms.Keys.Enter:
            self._send()

    def _on_key_btn(self, sender, e):
        self.TopMost = False
        dlg = ApiKeyDialog(self.engine)
        result = dlg.ShowDialog(self)
        self.TopMost = True
        if result == WinForms.DialogResult.OK and dlg.saved:
            self._update_status()
            self._add_msg("Bot", "API key saved! AI chat is now enabled. Ask me anything!")

    def _on_clear(self, sender, e):
        self.chat_box.Text = ""
        self._add_msg("Bot", "Chat cleared. How can I help?")


# ── Entry Point ──────────────────────────────────────────────────────────

try:
    doc   = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument
    form  = RevitBotForm(doc, uidoc)
    form.ShowDialog()

    # After dialog closes — activate pending view if any
    if hasattr(form, '_pending_view_id') and form._pending_view_id is not None:
        try:
            view_id = form._pending_view_id
            uidoc.ActiveViewId = view_id
        except Exception:
            try:
                uidoc.ActiveView = doc.GetElement(view_id)
            except Exception:
                try:
                    command_id = RevitCommandId.LookupPostableCommand(
                        PostableCommand.ActivateView)
                    uidoc.Document.Application.PostCommand(command_id)
                except Exception:
                    pass

except Exception as ex:
    WinForms.MessageBox.Show(
        "RevitBot Error:\n\n{}".format(traceback.format_exc()),
        "RevitBot Error",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Error
    )