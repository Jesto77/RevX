# -*- coding: utf-8 -*-
"""
pyRevit pushbutton script: opens Outlook Web App inbox in Google Chrome.
Works across all Revit versions.
"""

import os
import subprocess
import sys
import webbrowser

OUTLOOK_URL = "https://outlook.office.com/mail/inbox"


def find_chrome_exe():
    """
    Searches for google chrome executable in common Windows locations.
    Returns the full path string if found, otherwise None.
    """
    # List of typical Chrome install paths (per-user and system-wide)
    possible_paths = [
        # System-wide (64-bit Windows)
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        # System-wide (32-bit Windows or 32-bit install on 64-bit)
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # Per-user installation (most common in enterprise environments)
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        # Older per-user location (pre Windows 10)
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Google\Chrome\Application\chrome.exe"),
        # Possible custom install paths could be added here
    ]

    for path in possible_paths:
        if os.path.isfile(path):
            return path
    return None


def open_in_chrome(url):
    """
    Opens the given URL in Google Chrome.
    Falls back to the system's default browser if Chrome is not found.
    """
    chrome = find_chrome_exe()
    if chrome:
        try:
            # Use subprocess to open Chrome with the URL
            subprocess.Popen([chrome, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            # If subprocess fails (unlikely), fall back to default browser
            print("Failed to launch Chrome: {}".format(e))
    # Fallback: use the default web browser
    print("Chrome not found; opening with system default browser.")
    webbrowser.open(url)


if __name__ == "__main__":
    open_in_chrome(OUTLOOK_URL)