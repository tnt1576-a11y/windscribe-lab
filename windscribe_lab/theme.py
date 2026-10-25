"""Shared desktop visual tokens and original vector icons."""
from pathlib import Path
import sys
from PySide6.QtCore import Qt, QRect, QRectF, QPointF
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPalette, QPen, QPixmap

BG = "#101216"
PANEL = "#181c23"
MINT = "#76d9b5"
ACCENT = "#a3b7ff"
TEXT = "#edf0f7"
MUTED = "#a0a8b8"

STYLESHEET = """
QWidget { background: #101216; color: #edf0f7; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #101216; }
QLabel, QWidget#protocolContainer, QWidget#protocolGroup { background: transparent; }
QWidget#sidebar { background: #13161b; border-right: 1px solid #282d37; }
QFrame#card { background: #181c23; border: 1px solid #2b303b; border-radius: 10px; }
QFrame#command { background: #1b2030; border: 1px solid #303b56; border-radius: 10px; }
QWidget#runActivity, QWidget#stageRail { background: transparent; }
QLabel#activityTitle { font-size: 17px; font-weight: 600; }
QLabel#activityTarget { color: #bec9e2; font-size: 12px; }
QLabel#activityDetail { color: #a0a8b8; font-size: 12px; }
QLabel#activityDetail[tone="error"] { color: #ffb5be; }
QLabel#runStep { color: #7e899e; background: #171c28; border: 1px solid #2c354a; border-radius: 5px; padding: 6px 8px; font-size: 12px; }
QLabel#runStep[state="active"] { color: #dae2ff; background: #2c395a; border-color: #7189cb; font-weight: 600; }
QLabel#runStep[state="done"] { color: #92d9c1; background: #1c2c2c; border-color: #32534b; }
QLabel#runStep[state="error"] { color: #ffb5be; background: #34242d; border-color: #62404d; }
QFrame#card QLabel, QFrame#command QLabel { background: transparent; border: none; }
QLabel#title { font-size: 23px; font-weight: 600; }
QLabel#cardTitle, QLabel#brand { font-size: 15px; font-weight: 600; }
QLabel#metric { font-size: 22px; font-weight: 600; }
QLabel#muted, QLabel#subtitle { color: #a0a8b8; font-size: 12px; }
QLabel#caption { color: #a0a8b8; font-size: 11px; font-weight: 600; }
QLabel#badge { color: #b3c4ff; background: #232c44; border: 1px solid #364362; border-radius: 5px; padding: 4px 8px; font-size: 12px; }
QLabel#empty { color: #828c9f; font-size: 14px; }
QLabel#notice { color: #bac8ea; background: #1b2334; border: 1px solid #35415d; border-radius: 7px; padding: 9px 12px; }
QLabel#notice[tone="error"] { color: #ffb5be; background: #302029; border-color: #603541; }
QLabel#notice[tone="warning"] { color: #e7bd77; background: #2b261d; border-color: #51422c; }
QPushButton { background: #232832; border: 1px solid #363d4b; border-radius: 6px; padding: 7px 12px; font-weight: 500; }
QPushButton:hover { background: #2b3240; border-color: #56627b; }
QPushButton:pressed { background: #343e50; }
QPushButton:focus { border-color: #819fff; }
QPushButton:disabled { color: #626b7c; background: #1b1f27; border-color: #2a303b; }
QPushButton#primary { color: #131b35; background: #a3b7ff; border: 1px solid #a3b7ff; font-weight: 600; padding: 8px 19px; }
QPushButton#primary:hover { background: #bac9ff; border-color: #bac9ff; }
QPushButton#primary:pressed { background: #8fa6ef; }
QPushButton#primary:focus { border-color: #edf0f7; }
QPushButton#primary:disabled { color: #7584a9; background: #343e58; border-color: #343e58; }
QPushButton#danger { background: #34242d; color: #ffb5be; border-color: #62404d; }
QPushButton#danger:hover { background: #47303b; }
QPushButton#danger:disabled { background: #1b1f27; color: #626b7c; border-color: #2a303b; }
QPushButton#nav { text-align: left; background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 10px 12px; color: #a0a8b8; }
QPushButton#nav:checked { color: #c4d1ff; background: #252d43; border-color: #35415d; }
QPushButton#nav:hover { background: #202630; color: #edf0f7; }
QPushButton#nav:focus { border-color: #819fff; }
QPushButton#small { padding: 5px 9px; font-size: 12px; }
QPushButton#ghost { background: transparent; border-color: transparent; color: #a0a8b8; padding: 5px 8px; }
QPushButton#ghost:hover { background: #252b36; color: #edf0f7; }
QPushButton#ghost:focus { border-color: #819fff; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background: #12151b; border: 1px solid #343b49; border-radius: 6px; padding: 7px 9px; selection-background-color: #35446e; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #515e76; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus { border-color: #819fff; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: #626b7c; background: #1b1f27; border-color: #2a303b; }
QPlainTextEdit#activity { font-family: 'Consolas'; font-size: 12px; }
QComboBox { padding-right: 28px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url("ASSET_ROOT/chevron-down.png"); width: 16px; height: 16px; }
QComboBox QAbstractItemView { background: #1d232d; border: 1px solid #424e65; selection-background-color: #35446e; padding: 4px; }
QSpinBox { padding-right: 20px; }
QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 21px; height: 16px; background: #252d3c; border-top-right-radius: 5px; }
QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 21px; height: 16px; background: #252d3c; border-bottom-right-radius: 5px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #38445c; }
QSpinBox::up-arrow { image: url("ASSET_ROOT/chevron-up.png"); width: 12px; height: 12px; }
QSpinBox::down-arrow { image: url("ASSET_ROOT/chevron-down.png"); width: 12px; height: 12px; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox:disabled { color: #626b7c; }
QCheckBox::indicator { width: 15px; height: 15px; background: #12151b; border: 1px solid #58657e; border-radius: 4px; }
QCheckBox::indicator:hover { border-color: #a3b7ff; }
QCheckBox::indicator:checked { background: #a3b7ff; border-color: #a3b7ff; image: url("ASSET_ROOT/check.png"); }
QCheckBox::indicator:focus { border: 2px solid #c4d1ff; }
QCheckBox::indicator:disabled { background: #222832; border-color: #343b49; }
QTableWidget, QTreeWidget { background: #181c23; alternate-background-color: #1b2028; border: none; gridline-color: #2b303b; selection-background-color: #2a3556; selection-color: #edf0f7; outline: none; }
QTableWidget::item { padding: 6px 9px; border-bottom: 1px solid #272d37; }
QTreeWidget::item { padding: 4px 6px; min-height: 23px; }
QTableWidget::item:hover, QTreeWidget::item:hover { background: #242c3b; }
QTableWidget::item:selected, QTreeWidget::item:selected { background: #2a3556; color: #edf0f7; }
QTableWidget:focus, QTreeWidget:focus { border: 1px solid #819fff; }
QTreeWidget::indicator { width: 15px; height: 15px; border: 1px solid #58657e; border-radius: 4px; background: #12151b; }
QTreeWidget::indicator:checked { background: #a3b7ff; border-color: #a3b7ff; image: url("ASSET_ROOT/check.png"); }
QTreeWidget::indicator:indeterminate { background: #a3b7ff; border-color: #a3b7ff; image: url("ASSET_ROOT/mixed.png"); }
QHeaderView::section { background: #1c212a; color: #a0a8b8; border: none; border-bottom: 1px solid #343b49; padding: 9px; font-size: 12px; font-weight: 500; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #444e62; min-height: 28px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #181c23; height: 8px; }
QScrollBar::handle:horizontal { background: #444e62; min-width: 28px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QProgressBar { background: #30394d; border: none; border-radius: 2px; height: 4px; }
QProgressBar::chunk { background: #a3b7ff; border-radius: 2px; }
QTabWidget::pane { border: none; padding-top: 12px; }
QTabBar::tab { background: transparent; color: #a0a8b8; padding: 10px 16px; border-bottom: 2px solid #303744; }
QTabBar::tab:selected { color: #c4d1ff; border-bottom: 2px solid #a3b7ff; }
QTabBar::tab:hover { background: #1c222c; }
QTabBar::tab:disabled { color: #626b7c; }
QScrollArea { border: none; background: transparent; }
QToolTip { background: #252d3c; color: #edf0f7; border: 1px solid #53627d; padding: 7px; }
QSplitter::handle { background: #101216; width: 8px; }
QMessageBox QLabel { min-width: 340px; }
"""
_assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets"
STYLESHEET = STYLESHEET.replace("ASSET_ROOT", _assets.as_posix())

def apply_theme(app):
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, "#12151b"),
                        (QPalette.ColorRole.AlternateBase, "#1b2028"), (QPalette.ColorRole.Button, "#232832"),
                        (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Text, TEXT),
                        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, "#2a3556"),
                        (QPalette.ColorRole.HighlightedText, TEXT), (QPalette.ColorRole.PlaceholderText, MUTED),
                        (QPalette.ColorRole.ToolTipBase, "#252d3c"), (QPalette.ColorRole.ToolTipText, TEXT),
                        (QPalette.ColorRole.Light, "#58657e"), (QPalette.ColorRole.Mid, "#343b49"),
                        (QPalette.ColorRole.Dark, "#12151b")):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#626b7c"))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)

class LineIcon(QIconEngine):
    def __init__(self, name, primary=False):
        super().__init__()
        self.name, self.primary = name, primary

    def clone(self):
        return LineIcon(self.name, self.primary)

    def pixmap(self, size, mode, state):
        pix = QPixmap(size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pix

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        color = "#131b35" if self.primary else (ACCENT if state == QIcon.State.On else MUTED)
        if mode == QIcon.Mode.Disabled:
            color = "#626b7c"
        painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.name == "results":
            painter.drawRoundedRect(QRectF(4,4,16,16),3,3)
            for y,end in ((9,16),(13,14),(17,17)):
                painter.drawLine(8,y,end,y)
        elif self.name == "setup":
            for x,y in ((6,9),(12,15),(18,7)):
                painter.drawLine(x,4,x,y-2)
                painter.drawLine(x,y+2,x,20)
                painter.drawEllipse(QRectF(x-2,y-2,4,4))
        elif self.name == "history":
            painter.drawArc(QRectF(4,4,16,16),-40*16,300*16)
            painter.drawPolyline([QPointF(x,y) for x,y in ((3,4),(3,9),(8,9))])
            painter.drawPolyline([QPointF(x,y) for x,y in ((12,7),(12,12),(16,14))])
        elif self.name == "settings":
            painter.drawEllipse(QRectF(5,5,14,14))
            painter.drawEllipse(QRectF(9,9,6,6))
            for x1,y1,x2,y2 in ((12,2,12,5),(12,19,12,22),(2,12,5,12),(19,12,22,12),(5,5,7,7),(17,17,19,19),(5,19,7,17),(17,7,19,5)):
                painter.drawLine(x1,y1,x2,y2)
        elif self.name in ("play","resume"):
            painter.drawPolygon([QPointF(8,5),QPointF(19,12),QPointF(8,19)])
        elif self.name == "pause":
            painter.drawLine(8,5,8,19)
            painter.drawLine(16,5,16,19)
        elif self.name == "export":
            painter.drawPolyline([QPointF(x,y) for x,y in ((4,15),(4,20),(20,20),(20,15))])
            painter.drawLine(12,4,12,15)
            painter.drawPolyline([QPointF(x,y) for x,y in ((8,8),(12,4),(16,8))])
        painter.restore()

def line_icon(name, primary=False):
    return QIcon(LineIcon(name, primary))

def app_icon() -> QIcon:
    pix = QPixmap(128,128)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#293656"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(3,3,122,122),30,30)
    p.setPen(QPen(QColor(ACCENT),8,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
    p.drawPolyline([QPointF(x,y) for x,y in ((27,68),(43,68),(54,40),(71,91),(83,61),(102,61))])
    p.end()
    return QIcon(pix)
