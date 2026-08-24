# -*- coding: utf-8 -*-

__title__ = "Revit ID"
__author__ = "Jesto Joy"
__doc__ = "Opens the Revit ID Loop SharePoint List in your default browser."

import webbrowser

SHAREPOINT_URL = "https://tangentlandscapedxb.sharepoint.com/sites/REVITTEAM/Lists/REVIT%20ID%20LOOP/AllItems.aspx?ct=1787573898949&or=Teams%2DHL"


def open_revit_id_loop():
    webbrowser.open(SHAREPOINT_URL)


if __name__ == "__main__":
    open_revit_id_loop()