"""Chess Analysis desktop app: the analysis page in its own window.

Uses the Edge WebView2 runtime that ships with Windows 10/11, so the .exe is
small. Everything (rules + Stockfish) is bundled: it works offline.
"""
import os
import sys

import webview


def resource_dir() -> str:
    # PyInstaller unpacks bundled files to sys._MEIPASS
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def main():
    page = os.path.join(resource_dir(), "index.html")
    webview.create_window("Chess Analysis", page, width=1240, height=880,
                          min_size=(420, 600), background_color="#0e1016")
    # http_server: serve the files from a local server so the engine can load
    webview.start(http_server=True, private_mode=False)


if __name__ == "__main__":
    main()
