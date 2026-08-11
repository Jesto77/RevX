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

try:
    from chat_engine import (
        ChatEngine, MODE_INFORMATIONS, MODE_TASKS, MODE_TOOLS,
    )
except ImportError as import_ex:
    raise ImportError(
        "RevitBot v2 needs the NEW chat_engine.py — copy the updated "
        "chat_engine.py into this pushbutton folder next to script.py. "
        "(original error: {})".format(import_ex)
    )


# ── Mode themes ────────────────────────────────────────────────────────
# Each mode gets its own vibrant, high-contrast colour scheme.
MODE_THEMES = {
    MODE_INFORMATIONS: {
        "accent": (0, 180, 216),      # vivid cyan
        "on_accent": (14, 22, 30),
        "header_bg": (13, 27, 42),
        "form_bg": (30, 30, 46),
        "chat_bg": (20, 20, 36),
        "input_bg": (27, 40, 56),
        "idle_bg": (24, 24, 38),
        "announce": (
            "INFORMATIONS mode (cyan) — ask me anything about THIS model: "
            "revisions, sheets, warnings, counts, lists, links, rooms, phases...\n"
            "I read live project data to answer. For actions or exports, "
            "switch to Tasks or Tools."
        ),
    },
    MODE_TASKS: {
        "accent": (0, 200, 83),       # vivid green
        "on_accent": (10, 26, 16),
        "header_bg": (10, 36, 22),
        "form_bg": (22, 40, 30),
        "chat_bg": (15, 30, 22),
        "input_bg": (18, 44, 30),
        "idle_bg": (20, 34, 26),
        "announce": (
            "TASKS mode (green) — I can act on the model: create sheets / rooms / "
            "levels / grids, rename or delete sheets and views, open views, "
            "enable worksharing...\n"
            "For lookups use Informations; for exports use Tools."
        ),
    },
    MODE_TOOLS: {
        "accent": (255, 138, 0),      # vivid orange
        "on_accent": (30, 18, 6),
        "header_bg": (40, 22, 8),
        "form_bg": (44, 32, 24),
        "chat_bg": (34, 25, 18),
        "input_bg": (50, 34, 22),
        "idle_bg": (38, 28, 20),
        "announce": (
            "TOOLS mode (orange) — project tools live here: export NWC / IFC / "
            "DWG / PDF and save.\n"
            "For model info use Informations; for model changes use Tasks."
        ),
    },
}


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
        self.mode = None
        self._pending_view_id = None

        # Thread result fields
        self._ai_done = False
        self._ai_result = None
        self._ai_error = None
        self._poll_timer = None

        # ── Form setup ───────────────────────────────────────────────
        self.Text = "RevitBot v2 — Modes"
        # Taller to fit the Informations / Tasks / Tools mode bar
        self.Size = Drawing.Size(480, 566)
        self.BackColor = Drawing.Color.FromArgb(30, 30, 46)
        self.MaximizeBox = True
        self.MinimumSize = Drawing.Size(420, 480)
        self.TopMost = True
        self.StartPosition = WinForms.FormStartPosition.CenterScreen
        self.Resize += self._on_resize

        # ── Header ───────────────────────────────────────────────────
        # Colours are applied by _apply_mode_theme() once a mode is set.
        self.header = WinForms.Panel()
        self.header.Size = Drawing.Size(480, 40)
        self.header.Location = Drawing.Point(0, 0)
        self.Controls.Add(self.header)

        self.title_lbl = WinForms.Label()
        self.title_lbl.Text = "RevitBot"
        self.title_lbl.Font = Drawing.Font("Segoe UI", 14.0, Drawing.FontStyle.Bold)
        self.title_lbl.Location = Drawing.Point(12, 8)
        self.title_lbl.AutoSize = True
        self.header.Controls.Add(self.title_lbl)

        self.status_lbl = WinForms.Label()
        self.status_lbl.Text = "Ready"
        self.status_lbl.ForeColor = Drawing.Color.FromArgb(0, 200, 83)
        self.status_lbl.Font = Drawing.Font("Segoe UI", 9.0)
        self.status_lbl.Location = Drawing.Point(100, 16)
        self.status_lbl.AutoSize = True
        self.header.Controls.Add(self.status_lbl)

        # Key button
        self.key_btn = WinForms.Button()
        self.key_btn.Text = "Key"
        self.key_btn.Size = Drawing.Size(60, 28)
        self.key_btn.Location = Drawing.Point(408, 6)
        self.key_btn.FlatStyle = WinForms.FlatStyle.Flat
        self.key_btn.Font = Drawing.Font("Segoe UI", 9.0, Drawing.FontStyle.Bold)
        self.key_btn.Click += self._on_key_btn
        self.header.Controls.Add(self.key_btn)

        # Clear button
        self.clear_btn = WinForms.Button()
        self.clear_btn.Text = "Clear"
        self.clear_btn.Size = Drawing.Size(55, 28)
        self.clear_btn.Location = Drawing.Point(348, 6)
        self.clear_btn.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        self.clear_btn.ForeColor = Drawing.Color.FromArgb(180, 180, 180)
        self.clear_btn.FlatStyle = WinForms.FlatStyle.Flat
        self.clear_btn.Font = Drawing.Font("Segoe UI", 9.0)
        self.clear_btn.Click += self._on_clear
        self.header.Controls.Add(self.clear_btn)

        # ── Category Bar (Informations / Tasks / Tools) ──────────────
        self.info_btn = WinForms.Button()
        self.info_btn.Text = "Informations"
        self.info_btn.Size = Drawing.Size(150, 28)
        self.info_btn.Location = Drawing.Point(8, 46)
        self._style_cat_button(self.info_btn)
        self.info_btn.Click += self._on_info_cat
        self.Controls.Add(self.info_btn)

        self.tasks_btn = WinForms.Button()
        self.tasks_btn.Text = "Tasks"
        self.tasks_btn.Size = Drawing.Size(150, 28)
        self.tasks_btn.Location = Drawing.Point(164, 46)
        self._style_cat_button(self.tasks_btn)
        self.tasks_btn.Click += self._on_tasks_cat
        self.Controls.Add(self.tasks_btn)

        self.tools_btn = WinForms.Button()
        self.tools_btn.Text = "Tools"
        self.tools_btn.Size = Drawing.Size(150, 28)
        self.tools_btn.Location = Drawing.Point(320, 46)
        self._style_cat_button(self.tools_btn)
        self.tools_btn.Click += self._on_tools_cat
        self.Controls.Add(self.tools_btn)

        # ── Chat Display ─────────────────────────────────────────────
        # Taller now that quick buttons are removed — more space for chat
        self.chat_box = WinForms.RichTextBox()
        self.chat_box.BackColor = Drawing.Color.FromArgb(20, 20, 36)
        self.chat_box.ForeColor = Drawing.Color.FromArgb(220, 220, 220)
        self.chat_box.Font = Drawing.Font("Segoe UI", 10.0)
        self.chat_box.Size = Drawing.Size(462, 390)
        self.chat_box.Location = Drawing.Point(8, 88)
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
        self.thinking_lbl.Location = Drawing.Point(8, 484)
        self.thinking_lbl.AutoSize = True
        self.Controls.Add(self.thinking_lbl)

        # ── Input box ────────────────────────────────────────────────
        self.input_box = WinForms.TextBox()
        self.input_box.BackColor = Drawing.Color.FromArgb(27, 40, 56)
        self.input_box.ForeColor = Drawing.Color.FromArgb(224, 224, 224)
        self.input_box.Font = Drawing.Font("Segoe UI", 11.0)
        self.input_box.Size = Drawing.Size(360, 26)
        self.input_box.Location = Drawing.Point(8, 506)
        self.input_box.BorderStyle = WinForms.BorderStyle.FixedSingle
        self.Controls.Add(self.input_box)

        # ── Send button ──────────────────────────────────────────────
        self.send_btn = WinForms.Button()
        self.send_btn.Text = "Send"
        self.send_btn.Size = Drawing.Size(100, 26)
        self.send_btn.Location = Drawing.Point(372, 506)
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
            "Hey! I'm RevitBot v2 (mode system active).\n\n"
            "I work in three modes — click a button above to switch:\n"
            "  Informations — model lookups, lists and doc info.\n"
            "  Tasks — create, rename, delete, open views.\n"
            "  Tools — exports (NWC / IFC / DWG / PDF) and save.\n\n"
            "Type 'help' to see the commands for each mode, or ask me anything!\n\n"
            "To enable AI chat, click the Key button and add your free Groq API key."
        )

        self._update_status()

        # Start in Informations mode — paint the theme and announce once
        self._set_mode(MODE_INFORMATIONS, announce=True)

        # Lay out controls relative to the actual client area
        self._layout_controls()

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

    # ── Mode Bar (Informations / Tasks / Tools) ─────────────────────────
    def _style_cat_button(self, btn):
        """Static styling for a mode button (colours come from the theme)."""
        btn.FlatStyle = WinForms.FlatStyle.Flat
        btn.Font = Drawing.Font("Segoe UI", 9.0, Drawing.FontStyle.Bold)

    def _rgb(self, triple):
        return Drawing.Color.FromArgb(triple[0], triple[1], triple[2])

    def _on_info_cat(self, sender, e):
        self._set_mode(MODE_INFORMATIONS)

    def _on_tasks_cat(self, sender, e):
        self._set_mode(MODE_TASKS)

    def _on_tools_cat(self, sender, e):
        self._set_mode(MODE_TOOLS)

    def _set_mode(self, mode, announce=False):
        """Switch chat scope: informations / tasks / tools.

        Silent by default — the whole window repaints in the mode's
        colour, which is feedback enough. announce=True only at startup."""
        if mode == self.mode:
            return
        self.mode = mode
        self.engine.set_mode(mode)
        self._apply_mode_theme()
        if announce:
            self._add_msg("Bot", MODE_THEMES[mode]["announce"])
        self.input_box.Focus()

    def _apply_mode_theme(self):
        """Repaint every themed control with the active mode's colours."""
        theme = MODE_THEMES[self.mode]
        accent = self._rgb(theme["accent"])

        self.BackColor = self._rgb(theme["form_bg"])
        self.header.BackColor = self._rgb(theme["header_bg"])
        self.title_lbl.ForeColor = accent
        self.chat_box.BackColor = self._rgb(theme["chat_bg"])
        self.input_box.BackColor = self._rgb(theme["input_bg"])
        self.send_btn.BackColor = accent
        self.send_btn.ForeColor = self._rgb(theme["on_accent"])
        self.key_btn.BackColor = self._rgb(theme["input_bg"])
        self.key_btn.ForeColor = accent

        # Active mode button is filled with its accent; idle ones are outlined
        for m, btn in ((MODE_INFORMATIONS, self.info_btn),
                       (MODE_TASKS, self.tasks_btn),
                       (MODE_TOOLS, self.tools_btn)):
            t = MODE_THEMES[m]
            if m == self.mode:
                btn.BackColor = self._rgb(t["accent"])
                btn.ForeColor = self._rgb(t["on_accent"])
            else:
                btn.BackColor = self._rgb(t["idle_bg"])
                btn.ForeColor = self._rgb(t["accent"])

    # ── Layout (responsive resize) ──────────────────────────────────────
    def _on_resize(self, sender, e):
        self._layout_controls()

    def _layout_controls(self):
        """Stretch every control to fit the current window size.

        Runs on resize/maximize so the header, chat area, input box and
        Send button all grow with the window instead of staying fixed."""
        # Guard: Resize can fire before all controls exist
        if not hasattr(self, "chat_box"):
            return

        margin = 8
        gap = 6
        client_w = self.ClientSize.Width
        client_h = self.ClientSize.Height

        # Header spans the full width
        self.header.Size = Drawing.Size(client_w, 40)

        # Header buttons stay pinned to the right edge
        self.key_btn.Location = Drawing.Point(client_w - margin - 60, 6)
        self.clear_btn.Location = Drawing.Point(client_w - margin - 60 - 4 - 55, 6)

        # Mode buttons: three equal columns across the window
        btn_w = (client_w - 2 * margin - 2 * gap) // 3
        self.info_btn.SetBounds(margin, 46, btn_w, 28)
        self.tasks_btn.SetBounds(margin + btn_w + gap, 46, btn_w, 28)
        tools_x = margin + 2 * (btn_w + gap)
        self.tools_btn.SetBounds(tools_x, 46, client_w - margin - tools_x, 28)

        # Chat stretches to fill all remaining vertical space
        chat_top = 84
        input_h = 26
        input_y = client_h - margin - input_h
        think_y = input_y - 22
        chat_h = think_y - 6 - chat_top
        if chat_h < 60:
            chat_h = 60
        self.chat_box.SetBounds(margin, chat_top, client_w - 2 * margin, chat_h)
        self.thinking_lbl.Location = Drawing.Point(margin, think_y)

        # Input stretches, Send stays pinned bottom-right
        send_w = 100
        self.input_box.SetBounds(
            margin, input_y, client_w - 2 * margin - send_w - gap, input_h)
        self.send_btn.SetBounds(
            client_w - margin - send_w, input_y, send_w, input_h)

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

        self._ai_done = False
        self._ai_result = None
        self._ai_error = None

        def do_work():
            try:
                result = self.engine.query_ai(message)
                self._ai_result = result
                self._ai_error = None
            except Exception as ex:
                self._ai_result = None
                self._ai_error = str(ex)
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
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument
    form = RevitBotForm(doc, uidoc)
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
