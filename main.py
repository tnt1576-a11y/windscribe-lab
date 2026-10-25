"""Launch Windscribe Lab. No VPN changes occur until Start test is pressed."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from windscribe_lab.theme import apply_theme, app_icon
from windscribe_lab.ui import MainWindow


def main():
    parser = argparse.ArgumentParser(description="Windscribe Lab desktop connection tester")
    parser.add_argument("--demo", action="store_true", help="Isolated simulation; no VPN commands or network probes")
    parser.add_argument("--data-dir", type=Path, help="Override local data directory")
    args = parser.parse_args()
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    data = args.data_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "WindscribeLab" / ("simulation" if args.demo else "live")
    data.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Windscribe Lab")
    app.setOrganizationName("WindscribeLabIndependent")
    app.setFont(QFont("Segoe UI", 10))
    apply_theme(app)
    app.setWindowIcon(app_icon())
    # A system-wide VPN cannot be safely controlled by two copies of the tester.
    lock_dir = Path(os.environ.get("LOCALAPPDATA", str(data))) / "WindscribeLab"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_dir / ("simulation.lock" if args.demo else "live.lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "Windscribe Lab", "Another instance is already running in this mode.")
        return 1
    window = MainWindow(data, root / "assets", args.demo)
    window.show()
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
