# -*- coding: utf-8 -*-
"""
RevitBot Auto-Start Hook
Shows a small floating mascot button in the bottom-right corner.
Clicking it opens the full RevitBot chat window.
"""

import clr
import os
import sys

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

import System.Windows.Forms as WinForms
import System.Drawing as Drawing

script_dir = os.path.dirname(os.path.abspath(__file__))
pushbutton_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(script_dir))),
    "OpenBot.pushbutton"
)

if pushbutton_dir not in sys.path:
    sys.path.insert(0, pushbutton_dir)

_mascot_form = None


def show_mascot():
    """Show a small floating mascot button in the bottom-right corner."""
    global _mascot_form

    if _mascot_form is not None:
        try:
            if _mascot_form.Visible:
                _mascot_form.Focus()
                return
            else:
                _mascot_form = None
        except:
            _mascot_form = None

    form = WinForms.Form()
    form.Text = "RevitBot"
    form.Size = Drawing.Size(60, 60)
    form.FormBorderStyle = WinForms.FormBorderStyle.None
    form.StartPosition = WinForms.FormStartPosition.Manual
    form.TopMost = True
    form.ShowInTaskbar = False
    form.BackColor = Drawing.Color.FromArgb(0, 119, 182)

    # Position bottom-right
    try:
        screen = WinForms.Screen.PrimaryScreen
        form.Location = Drawing.Point(
            screen.WorkingArea.Right - 70,
            screen.WorkingArea.Bottom - 70
        )
    except:
        form.Location = Drawing.Point(1200, 660)

    # Bot label
    label = WinForms.Label()
    label.Text = "RB"
    label.ForeColor = Drawing.Color.White
    label.Font = Drawing.Font("Segoe UI", 16.0, Drawing.FontStyle.Bold)
    label.TextAlign = Drawing.ContentAlignment.MiddleCenter
    label.Dock = WinForms.DockStyle.Fill
    label.Cursor = WinForms.Cursors.Hand
    form.Controls.Add(label)

    # Click to open chat
    def on_click(sender, e):
        open_chat()

    label.Click += on_click
    form.Click += on_click

    # Drag to move
    _drag_offset = [None]

    def on_mouse_down(sender, e):
        if e.Button == WinForms.MouseButtons.Left:
            _drag_offset[0] = e.Location

    def on_mouse_move(sender, e):
        if _drag_offset[0] is not None and e.Button == WinForms.MouseButtons.Left:
            form.Location = Drawing.Point(
                form.Location.X + e.X - _drag_offset[0].X,
                form.Location.Y + e.Y - _drag_offset[0].Y
            )

    def on_mouse_up(sender, e):
        _drag_offset[0] = None

    label.MouseDown += on_mouse_down
    label.MouseMove += on_mouse_move
    label.MouseUp += on_mouse_up
    form.MouseDown += on_mouse_down
    form.MouseMove += on_mouse_move
    form.MouseUp += on_mouse_up

    form.Show()
    _mascot_form = form


def open_chat():
    """Open the full RevitBot chat window."""
    try:
        doc = __revit__.ActiveUIDocument.Document
        uidoc = __revit__.ActiveUIDocument

        from script import RevitBotForm
        _chat_form = RevitBotForm(doc, uidoc)
        _chat_form.ShowDialog()
    except Exception as ex:
        WinForms.MessageBox.Show(
            "RevitBot Error:\n\n{}".format(str(ex)),
            "RevitBot",
            WinForms.MessageBoxButtons.OK,
            WinForms.MessageBoxIcon.Error
        )


# ── Register auto-start ──
try:
    show_mascot()
except:
    pass
