# -*- coding: utf-8 -*-
"""Ramp Slope Calculator for pyRevit."""

__title__ = 'Ramp\nCalculator'
__author__ = 'Your Name'
__doc__ = 'Ramp Slope Calculator with unit conversions (%, ratio, degrees)'

import os
import math
import clr

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xaml')
clr.AddReference('System')

from System import Windows
from System.Windows import Window, MessageBox, MessageBoxButton, MessageBoxImage
from System.Windows.Markup import XamlReader
from System.IO import StreamReader
from System.Windows.Media import SolidColorBrush, Color

try:
    from pyrevit import forms
    PYREVIT_AVAILABLE = True
except ImportError:
    PYREVIT_AVAILABLE = False


class RampCalculatorWindow(object):
    """Main WPF Window Controller."""

    def __init__(self):
        xaml_file = os.path.join(
            os.path.dirname(__file__),
            'RampCalculator.xaml'
        )

        stream = StreamReader(xaml_file)
        try:
            self.window = XamlReader.Load(stream.BaseStream)
        finally:
            stream.Close()

        self._get_controls()
        self._wire_events()
        self._update_unit_labels(None, None)

    def _get_controls(self):
        w = self.window

        self.txt_rise = w.FindName('TxtRise')
        self.txt_run = w.FindName('TxtRun')
        self.txt_slope_len = w.FindName('TxtSlopeLen')
        self.txt_ratio = w.FindName('TxtRatio')
        self.cmb_preset = w.FindName('CmbPreset')
        self.cmb_unit = w.FindName('CmbUnit')

        self.lbl_rise_unit = w.FindName('LblRiseUnit')
        self.lbl_run_unit = w.FindName('LblRunUnit')
        self.lbl_slope_unit = w.FindName('LblSlopeUnit')

        self.btn_calculate = w.FindName('BtnCalculate')
        self.btn_reset = w.FindName('BtnReset')

        self.results_panel = w.FindName('ResultsPanel')
        self.lbl_res_rise = w.FindName('LblResRise')
        self.lbl_res_run = w.FindName('LblResRun')
        self.lbl_res_slope = w.FindName('LblResSlope')
        self.lbl_res_gradient = w.FindName('LblResGradient')
        self.lbl_res_angle = w.FindName('LblResAngle')
        self.lbl_res_ratio = w.FindName('LblResRatio')
        self.lbl_res_rise_unit = w.FindName('LblResRiseUnit')
        self.lbl_res_run_unit = w.FindName('LblResRunUnit')
        self.lbl_res_slope_unit = w.FindName('LblResSlopeUnit')

        self.compliance_border = w.FindName('ComplianceBorder')
        self.lbl_compliance_icon = w.FindName('LblComplianceIcon')
        self.lbl_compliance_title = w.FindName('LblComplianceTitle')
        self.lbl_compliance_desc = w.FindName('LblComplianceDesc')

        self.txt_conv_pct = w.FindName('TxtConvPct')
        self.btn_conv_pct = w.FindName('BtnConvPct')
        self.pct_results = w.FindName('PctResults')
        self.lbl_pct_pct = w.FindName('LblPctPct')
        self.lbl_pct_ratio = w.FindName('LblPctRatio')
        self.lbl_pct_deg = w.FindName('LblPctDeg')

        self.txt_ratio_rise = w.FindName('TxtRatioRise')
        self.txt_ratio_run = w.FindName('TxtRatioRun')
        self.btn_conv_ratio = w.FindName('BtnConvRatio')
        self.ratio_results = w.FindName('RatioResults')
        self.lbl_ratio_pct = w.FindName('LblRatioPct')
        self.lbl_ratio_ratio = w.FindName('LblRatioRatio')
        self.lbl_ratio_deg = w.FindName('LblRatioDeg')

        self.txt_conv_deg = w.FindName('TxtConvDeg')
        self.btn_conv_deg = w.FindName('BtnConvDeg')
        self.deg_results = w.FindName('DegResults')
        self.lbl_deg_pct = w.FindName('LblDegPct')
        self.lbl_deg_ratio = w.FindName('LblDegRatio')
        self.lbl_deg_deg = w.FindName('LblDegDeg')

        self.btn_close = w.FindName('BtnClose')

    def _wire_events(self):
        self.btn_calculate.Click += self.calculate_ramp
        self.btn_reset.Click += self.reset_ramp
        self.cmb_unit.SelectionChanged += self._update_unit_labels
        self.cmb_preset.SelectionChanged += self._on_preset_selected

        self.btn_conv_pct.Click += self.convert_from_percentage
        self.btn_conv_ratio.Click += self.convert_from_ratio
        self.btn_conv_deg.Click += self.convert_from_degrees
        self.btn_close.Click += self.close_window

    def _on_preset_selected(self, sender, args):
        item = self.cmb_preset.SelectedItem
        if item and hasattr(item, 'Tag') and item.Tag:
            val = str(item.Tag)
            if val != 'custom':
                self.txt_ratio.Text = val

    def _get_selected_unit(self):
        item = self.cmb_unit.SelectedItem
        if item is None:
            return 'mm'
        try:
            return str(item.Content)
        except:
            return str(item)

    def _update_unit_labels(self, sender, args):
        unit = self._get_selected_unit()
        try:
            self.lbl_rise_unit.Text = unit
            self.lbl_run_unit.Text = unit
            self.lbl_slope_unit.Text = unit
            self.lbl_res_rise_unit.Text = unit
            self.lbl_res_run_unit.Text = unit
            self.lbl_res_slope_unit.Text = unit
        except:
            pass

    def _parse_input_value(self, textbox):
        val_str = textbox.Text.strip()
        if not val_str:
            return None, None

        if ':' in val_str or '/' in val_str:
            sep = ':' if ':' in val_str else '/'
            parts = val_str.split(sep)
            try:
                r_rise = float(parts[0])
                r_run = float(parts[1])
                if r_rise > 0:
                    return None, (r_run / r_rise)
            except:
                pass

        try:
            return float(val_str), None
        except:
            return None, None

    def _format_num(self, n):
        if n is None:
            return '-'
        if abs(n - round(n)) < 0.0001:
            return str(int(round(n)))
        return '{0:.2f}'.format(n)

    def _show_message(self, title, message, is_error=False):
        icon = MessageBoxImage.Error if is_error else MessageBoxImage.Information
        MessageBox.Show(message, title, MessageBoxButton.OK, icon)

    def calculate_ramp(self, sender, args):
        rise_val, rise_ratio = self._parse_input_value(self.txt_rise)
        run_val, run_ratio = self._parse_input_value(self.txt_run)
        slope_val, slope_ratio = self._parse_input_value(self.txt_slope_len)
        ratio_val, ratio_from_txt = self._parse_input_value(self.txt_ratio)

        ratio_x = ratio_val or ratio_from_txt or rise_ratio or run_ratio or slope_ratio

        rise = rise_val
        run = run_val
        slope_len = slope_val

        try:
            if rise is not None and ratio_x is not None and run is None:
                run = rise * ratio_x
                slope_len = math.sqrt(rise**2 + run**2)

            elif run is not None and ratio_x is not None and rise is None:
                rise = run / ratio_x
                slope_len = math.sqrt(rise**2 + run**2)

            elif slope_len is not None and ratio_x is not None and rise is None and run is None:
                rise = slope_len / math.sqrt(1 + ratio_x**2)
                run = rise * ratio_x

            elif rise is not None and run is not None:
                slope_len = math.sqrt(rise**2 + run**2)
                ratio_x = run / rise if rise > 0 else 0

            elif rise is not None and slope_len is not None:
                if slope_len <= rise:
                    self._show_message('Invalid Input', 'Slope length must be greater than Rise.', is_error=True)
                    return
                run = math.sqrt(slope_len**2 - rise**2)
                ratio_x = run / rise if rise > 0 else 0

            elif run is not None and slope_len is not None:
                if slope_len <= run:
                    self._show_message('Invalid Input', 'Slope length must be greater than Run.', is_error=True)
                    return
                rise = math.sqrt(slope_len**2 - run**2)
                ratio_x = run / rise if rise > 0 else 0

            else:
                self._show_message(
                    'Input Required',
                    'Please enter at least two values:\n• Rise + Ratio (e.g. 450 mm & 1:12)\n• Rise + Run\n• Rise + Slope Length',
                    is_error=True
                )
                return

        except Exception as ex:
            self._show_message('Calculation Error', str(ex), is_error=True)
            return

        if run == 0:
            self._show_message('Invalid Input', 'Run cannot be zero.', is_error=True)
            return

        self.txt_rise.Text = self._format_num(rise)
        self.txt_run.Text = self._format_num(run)
        self.txt_slope_len.Text = self._format_num(slope_len)
        self.txt_ratio.Text = '1:{0}'.format(self._format_num(ratio_x))

        gradient_pct = (rise / run) * 100.0
        angle_deg = math.degrees(math.atan(rise / run))

        unit = self._get_selected_unit()
        self.lbl_res_rise.Text = self._format_num(rise)
        self.lbl_res_run.Text = self._format_num(run)
        self.lbl_res_slope.Text = self._format_num(slope_len)
        self.lbl_res_rise_unit.Text = unit
        self.lbl_res_run_unit.Text = unit
        self.lbl_res_slope_unit.Text = unit
        self.lbl_res_gradient.Text = '{0:.2f}'.format(gradient_pct)
        self.lbl_res_angle.Text = '{0:.2f}'.format(angle_deg)
        self.lbl_res_ratio.Text = '1 : {0:.2f}'.format(ratio_x)

        self.results_panel.Visibility = Windows.Visibility.Visible
        self._update_compliance(ratio_x, gradient_pct)

    def _update_compliance(self, ratio_run, pct):
        green_bg = SolidColorBrush(Color.FromRgb(220, 252, 231))
        green_border = SolidColorBrush(Color.FromRgb(16, 185, 129))
        yellow_bg = SolidColorBrush(Color.FromRgb(254, 243, 199))
        yellow_border = SolidColorBrush(Color.FromRgb(245, 158, 11))
        red_bg = SolidColorBrush(Color.FromRgb(254, 226, 226))
        red_border = SolidColorBrush(Color.FromRgb(239, 68, 68))

        if ratio_run >= 20:
            title = 'Excellent - Meets Best Practice (1:20 or gentler)'
            desc = 'Gradient 1:{0:.1f} ({1:.2f}%) is comfortable for wheelchair users.'.format(ratio_run, pct)
            icon = 'OK'
            self.compliance_border.Background = green_bg
            self.compliance_border.BorderBrush = green_border
        elif ratio_run >= 12:
            title = 'Compliant - Meets ADA/DDA Minimum (1:12)'
            desc = 'Gradient 1:{0:.1f} ({1:.2f}%) meets ADA/DDA slope requirements.'.format(ratio_run, pct)
            icon = 'OK'
            self.compliance_border.Background = green_bg
            self.compliance_border.BorderBrush = green_border
        elif ratio_run >= 8:
            title = 'Steep - May Not Meet Accessibility Standards'
            desc = 'Gradient 1:{0:.1f} ({1:.2f}%) is steeper than ADA (1:12).'.format(ratio_run, pct)
            icon = '!'
            self.compliance_border.Background = yellow_bg
            self.compliance_border.BorderBrush = yellow_border
        else:
            title = 'Too Steep - Non-Compliant'
            desc = 'Gradient 1:{0:.1f} ({1:.2f}%) exceeds safe limits.'.format(ratio_run, pct)
            icon = 'X'
            self.compliance_border.Background = red_bg
            self.compliance_border.BorderBrush = red_border

        self.lbl_compliance_icon.Text = icon
        self.lbl_compliance_title.Text = title
        self.lbl_compliance_desc.Text = desc
        self.compliance_border.Visibility = Windows.Visibility.Visible

    def reset_ramp(self, sender, args):
        self.txt_rise.Text = ''
        self.txt_run.Text = ''
        self.txt_slope_len.Text = ''
        self.txt_ratio.Text = ''
        self.cmb_preset.SelectedIndex = 0
        self.results_panel.Visibility = Windows.Visibility.Collapsed
        self.compliance_border.Visibility = Windows.Visibility.Collapsed

    def convert_from_percentage(self, sender, args):
        val, _ = self._parse_input_value(self.txt_conv_pct)
        if val is None or val <= 0:
            self._show_message('Invalid Input', 'Please enter a valid positive percentage.', is_error=True)
            return

        ratio = 100.0 / val
        degrees = math.degrees(math.atan(val / 100.0))

        self.lbl_pct_pct.Text = '{0:.2f}%'.format(val)
        self.lbl_pct_ratio.Text = '1 : {0:.2f}'.format(ratio)
        self.lbl_pct_deg.Text = '{0:.2f}°'.format(degrees)
        self.pct_results.Visibility = Windows.Visibility.Visible

    def convert_from_ratio(self, sender, args):
        rise, _ = self._parse_input_value(self.txt_ratio_rise)
        run_v, ratio_x = self._parse_input_value(self.txt_ratio_run)

        run = run_v or ratio_x
        if rise is None or run is None or rise <= 0 or run <= 0:
            self._show_message('Invalid Input', 'Please enter valid numbers for rise and run.', is_error=True)
            return

        pct = (rise / run) * 100.0
        degrees = math.degrees(math.atan(rise / run))
        normalized = run / rise

        self.lbl_ratio_pct.Text = '{0:.2f}%'.format(pct)
        self.lbl_ratio_ratio.Text = '1 : {0:.2f}'.format(normalized)
        self.lbl_ratio_deg.Text = '{0:.2f}°'.format(degrees)
        self.ratio_results.Visibility = Windows.Visibility.Visible

    def convert_from_degrees(self, sender, args):
        deg, _ = self._parse_input_value(self.txt_conv_deg)
        if deg is None or deg <= 0 or deg >= 90:
            self._show_message('Invalid Input', 'Please enter an angle between 0 and 90 degrees.', is_error=True)
            return

        radians = math.radians(deg)
        pct = math.tan(radians) * 100.0
        ratio = 1.0 / math.tan(radians)

        self.lbl_deg_pct.Text = '{0:.2f}%'.format(pct)
        self.lbl_deg_ratio.Text = '1 : {0:.2f}'.format(ratio)
        self.lbl_deg_deg.Text = '{0:.2f}°'.format(deg)
        self.deg_results.Visibility = Windows.Visibility.Visible

    def close_window(self, sender, args):
        self.window.Close()

    def show(self):
        self.window.ShowDialog()


def main():
    try:
        calculator = RampCalculatorWindow()
        calculator.show()
    except Exception as ex:
        if PYREVIT_AVAILABLE:
            forms.alert('Error launching Ramp Calculator:\n{0}'.format(str(ex)), title='Error', exitscript=True)
        else:
            MessageBox.Show(str(ex), 'Error', MessageBoxButton.OK, MessageBoxImage.Error)


if __name__ == '__main__':
    main()