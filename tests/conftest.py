import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    # AppContainer test sessions do not enumerate system fonts automatically.
    for font in ("segoeui.ttf", "seguisb.ttf", "seguisym.ttf"):
        path = "C:/Windows/Fonts/" + font
        if os.path.isfile(path):
            QFontDatabase.addApplicationFont(path)
    yield app
