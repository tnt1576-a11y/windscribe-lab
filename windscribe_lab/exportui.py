"""Compact export options for a frozen session snapshot."""
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QDialog, QComboBox, QLabel, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton

from .reporting import select_report, write_report


class ExportJob(QThread):
    ready = Signal(dict)

    def __init__(self, snapshot, path: Path, working_only=False):
        super().__init__()
        self.snapshot, self.path, self.working_only = snapshot, path, working_only

    def run(self):
        try:
            summary = write_report(self.snapshot, self.path, self.working_only)
            self.ready.emit({"summary": summary, "path": str(self.path)})
        except Exception as exc:
            self.ready.emit({"error": f"Export failed: {exc}"})


class ExportDialog(QDialog):
    FORMATS = (("HTML report", ".html", "HTML report (*.html *.htm)"),
               ("CSV spreadsheet", ".csv", "CSV report (*.csv)"),
               ("JSON data", ".json", "JSON report (*.json)"))

    def __init__(self, snapshot, parent=None):
        super().__init__(parent)
        self.snapshot = snapshot
        self.setWindowTitle("Export report")
        self.setMinimumWidth(450)
        self.resize(480, 290)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        title = QLabel(snapshot["config"]["network"])
        title.setObjectName("cardTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        form = QFormLayout()
        form.setSpacing(12)
        self.format_combo = QComboBox()
        self.format_combo.setAccessibleName("Report format")
        self.format_combo.addItems([value[0] for value in self.FORMATS])
        form.addRow("Format", self.format_combo)
        self.scope_combo = QComboBox()
        self.scope_combo.setAccessibleName("Results to export")
        self.scope_combo.addItems(["All tests", "Working servers only"])
        self.scope_combo.currentIndexChanged.connect(self.update_summary)
        form.addRow("Include", self.scope_combo)
        layout.addLayout(form)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.save_button = QPushButton("Save report…")
        self.save_button.setObjectName("primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.update_summary()

    @property
    def working_only(self):
        return self.scope_combo.currentIndex() == 1

    def update_summary(self):
        summary = select_report(self.snapshot, self.working_only)["summary"]
        suffix = "Verified and HTTPS-only results." if self.working_only else "Includes failed and pending tests."
        self.summary.setText(f"{summary['exported']:,} tests · {summary['servers']:,} servers\n{suffix}")
        self.save_button.setEnabled(summary["exported"] > 0)
