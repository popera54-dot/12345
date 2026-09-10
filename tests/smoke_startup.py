import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSplitter

import app


def main():
    qt = QApplication.instance() or QApplication([])
    assert app.QSplitter is QSplitter, "QSplitter import is broken"
    window = app.MainWindow()
    assert window.windowTitle() == "TuneVault"
    assert window.pages.count() == 4
    assert window.save_button.isEnabled() is False
    window.close()
    qt.quit()
    print("TuneVault startup smoke test: OK")


if __name__ == "__main__":
    main()
