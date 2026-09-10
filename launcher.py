"""TuneVault Windows launcher.

The v2 UI accidentally omitted QSplitter from main.py's widget imports.
This compatibility launcher injects the missing Qt class before constructing
MainWindow, without changing the v2 UI itself.
"""

from PySide6.QtWidgets import QSplitter

import main as tunevault_main

# main.py references QSplitter when building the library page.
tunevault_main.QSplitter = QSplitter


if __name__ == "__main__":
    raise SystemExit(tunevault_main.main())
