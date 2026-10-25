"""Small presentation components; no client, storage or network actions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTableWidget


class Notice(QLabel):
    def __init__(self):
        super().__init__()
        self.setObjectName("notice")
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAccessibleName("Status message")
        self.hide()

    def setText(self, text):
        self.show_message(text)

    def show_message(self, text, tone="info"):
        super().setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(bool(text))


class ResultTable(QTableWidget):
    def __init__(self, columns, empty_text="No results yet"):
        super().__init__(0, columns)
        self.empty_text = empty_text
        self.empty_label = QLabel(empty_text, self.viewport())
        self.empty_label.setObjectName("empty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def setRowCount(self, count):
        super().setRowCount(count)
        self.update_empty()

    def update_empty(self):
        empty = not any(not self.isRowHidden(row) for row in range(self.rowCount()))
        self.empty_label.setText(self.empty_text if self.rowCount() == 0 else "No matching results")
        self.empty_label.setVisible(empty)
        self.empty_label.setGeometry(self.viewport().rect().adjusted(16,16,-16,-16))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_empty()
