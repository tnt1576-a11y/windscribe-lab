from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QSignalBlocker, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import __version__
from .catalog import parse_catalog, refresh_catalog
from .engine import Discovery, Runner, SettingsRecovery
from .antischema import AntiCensorship, KNOWN_PRESETS, SettingsError
from .anticensorship import RECOVERY_FILE, SettingsController
from .engine import Client
from .models import PROTOCOLS, Result, Route, RunConfig, make_plan
from .storage import Store
from .theme import MUTED, line_icon, app_icon
from .widgets import Notice, ResultTable
from .activity import RunActivity
from .regions import CONTINENTS, continent_for
from .exportui import ExportDialog, ExportJob

OUTCOMES = {
    "connect_error": ("Connection failed", "#f79595"),
    "verified": ("Tunnel verified", "#72edbf"), "https_only": ("HTTPS only", "#eac477"),
    "timeout": ("Timed out", "#f79595"), "partial": ("Partial HTTPS", "#eac477"),
    "no_internet": ("No HTTPS", "#f79595"), "route_mismatch": ("Route mismatch", "#f79595"),
    "unstable": ("Unstable", "#f79595"),
}


def label(text: str, name="", wrap=False) -> QLabel:
    w = QLabel(text)
    w.setObjectName(name)
    w.setWordWrap(wrap)
    w.setTextFormat(Qt.TextFormat.PlainText)
    return w


def button(text, callback=None, name="") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        b.clicked.connect(callback)
    return b


def card() -> tuple[QFrame, QVBoxLayout]:
    w = QFrame()
    w.setObjectName("card")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)
    return w, layout


def table(headers: list[str], empty_text="No results yet") -> QTableWidget:
    w = ResultTable(len(headers), empty_text)
    w.setHorizontalHeaderLabels(headers)
    w.verticalHeader().hide()
    w.verticalHeader().setDefaultSectionSize(38)
    w.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    w.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    w.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    w.setAlternatingRowColors(True)
    w.setShowGrid(False)
    w.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    w.horizontalHeader().setStretchLastSection(True)
    w.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return w


class SortItem(QTableWidgetItem):
    def __lt__(self, other):
        a, b = self.data(Qt.ItemDataRole.UserRole + 1), other.data(Qt.ItemDataRole.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return self.text().casefold() < other.text().casefold()


class CatalogJob(QThread):
    ready = Signal(dict)

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            self.ready.emit({"locations": refresh_catalog(self.path)})
        except Exception as exc:
            self.ready.emit({"error": f"Catalog refresh failed: {exc}. The existing catalog is retained."})


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, asset_dir: Path, demo=False):
        super().__init__()
        self.setWindowTitle("Windscribe Lab" + (" · Simulation" if demo else ""))
        self.setWindowIcon(app_icon())
        self.resize(1360, 900)
        self.setMinimumSize(1100, 740)
        self.data_dir, self.asset_dir = data_dir, asset_dir
        self.store = Store(data_dir)
        self.demo = demo
        self.runner = self.discovery = self.catalog_job = None
        self.recovery = None
        self.export_job = None
        self.anti_profiles = []
        self.anti_presets = list(KNOWN_PRESETS)
        self.anti_current = None
        self.active_run = None
        self.ports_verified = False
        self.closing = False
        self.records = []
        self._rendered_run = None
        self._rendered_results = {}
        self._result_row_items = {}
        self._results_by_seq = {}
        self.ports = {k: list(v[1]) for k, v in PROTOCOLS.items()}
        self.locations = self.load_catalog()
        self.preferences = self.load_preferences()
        self.build_ui()
        self.apply_preferences()
        self.populate_locations()
        self.refresh_history()
        self.update_plan_count()
        self.set_page(0)
        self.log("Simulation ready." if demo else "Ready.")
        if not demo and (self.data_dir / RECOVERY_FILE).exists():
            self.notice.setText("Saved settings available. Test setup → Anti-censorship → Review recovery.")
        if not demo:
            QTimer.singleShot(300, self.check_client)

    def load_catalog(self):
        paths = [self.data_dir / "catalog.json", self.asset_dir / "server_catalog.json"]
        for path in paths:
            try:
                return parse_catalog(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, KeyError):
                pass
        return []

    def load_preferences(self):
        try:
            return json.loads((self.data_dir / "preferences.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(184)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 24, 12, 16)
        side.setSpacing(6)
        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 0, 0)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(30, 30))
        brand.addWidget(icon)
        brand.addWidget(label("Windscribe Lab", "brand"))
        side.addLayout(brand)
        side.addSpacing(28)
        self.nav_group = QButtonGroup(self)
        for idx, (text, icon_name) in enumerate((("Tests", "results"), ("Test setup", "setup"),
                                                 ("History", "history"), ("Settings", "settings"))):
            b = button(text, lambda checked=False, i=idx: self.set_page(i), "nav")
            b.setIcon(line_icon(icon_name))
            b.setIconSize(QSize(18, 18))
            b.setCheckable(True)
            self.nav_group.addButton(b, idx)
            side.addWidget(b)
        side.addStretch()
        self.client_badge = label("Simulation" if self.demo else "Checking client", "muted", True)
        self.client_badge.setContentsMargins(8, 0, 0, 0)
        side.addWidget(self.client_badge)
        self.client_caption = label("" if self.demo else "Windscribe CLI", "muted", True)
        self.client_caption.setContentsMargins(8, 0, 0, 0)
        side.addWidget(self.client_caption)
        side.addSpacing(12)
        side.addWidget(button(f"v{__version__}  ·  About", self.show_about, "ghost"))
        outer.addWidget(sidebar)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(24, 20, 24, 16)
        body.setSpacing(16)
        head = QHBoxLayout()
        self.page_title = label("Tests", "title")
        head.addWidget(self.page_title)
        head.addStretch()
        self.mode_badge = label("Simulation" if self.demo else "Live", "badge")
        head.addWidget(self.mode_badge)
        self.export_button = button("Export", self.export_report)
        self.export_button.setIcon(line_icon("export"))
        self.export_button.setEnabled(False)
        head.addWidget(self.export_button)
        body.addLayout(head)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        self.build_overview()
        self.build_setup()
        self.build_history()
        self.build_diagnostics()
        self.notice = Notice()
        body.addWidget(self.notice)
        outer.addWidget(content, 1)

    def build_overview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        live, ll = card()
        live.setObjectName("command")
        self.run_activity = RunActivity()
        self.phase_label = self.run_activity.title
        self.target_label = self.run_activity.target
        self.setup_button = button("Configure", lambda: self.set_page(1))
        self.resume_button = button("Resume", self.resume_run)
        self.resume_button.setIcon(line_icon("resume"))
        self.resume_button.setEnabled(False)
        self.resume_button.hide()
        self.pause_button = button("Pause", self.pause_run, "danger")
        self.pause_button.setIcon(line_icon("pause"))
        self.pause_button.setEnabled(False)
        self.pause_button.hide()
        self.start_button = button("Start test", self.start_run, "primary")
        self.start_button.setIcon(line_icon("play", primary=True))
        for b in (self.setup_button, self.resume_button, self.pause_button, self.start_button):
            self.run_activity.actions.addWidget(b)
        ll.addWidget(self.run_activity)
        progressline = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress_caption = label("No active session", "muted")
        progressline.addWidget(self.progress, 1)
        progressline.addWidget(self.progress_caption)
        ll.addLayout(progressline)
        layout.addWidget(live)
        summary = QHBoxLayout()
        summary.setSpacing(12)
        stats, stat_layout = card()
        stat_layout.setContentsMargins(16, 12, 16, 12)
        stat_row = QHBoxLayout()
        self.metrics = []
        for title in ("Tested", "Verified", "Best latency", "Working"):
            group = QVBoxLayout()
            group.setSpacing(3)
            group.addWidget(label(title, "muted"))
            metric = label("—", "metric")
            group.addWidget(metric)
            self.metrics.append(metric)
            stat_row.addLayout(group, 1)
        stat_layout.addLayout(stat_row)
        summary.addWidget(stats, 5)
        best, bl = card()
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(4)
        bestline = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(3)
        names.addWidget(label("Recommended route", "muted"))
        self.best_name = label("No verified route yet", "cardTitle", True)
        self.best_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        names.addWidget(self.best_name)
        self.best_info = label("", "muted", True)
        names.addWidget(self.best_info)
        bestline.addLayout(names, 1)
        self.best_button = button("Connect", self.connect_best)
        self.best_button.setEnabled(False)
        bestline.addWidget(self.best_button)
        bl.addLayout(bestline)
        summary.addWidget(best, 4)
        layout.addLayout(summary)
        results_card, rl = card()
        rl.setContentsMargins(0, 12, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(16, 0, 16, 0)
        toolbar.addWidget(label("Results", "cardTitle"))
        self.results_scope = label("", "muted")
        self.results_scope.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(self.results_scope, 1)
        self.result_search = QLineEdit()
        self.result_search.setPlaceholderText("Search results")
        self.result_search.setMaximumWidth(200)
        self.result_search.setClearButtonEnabled(True)
        self.result_search.textChanged.connect(self.filter_results)
        toolbar.addWidget(self.result_search)
        self.result_filter = QComboBox()
        self.result_filter.addItems(["All results", "Working", "Verified", "Failures"])
        self.result_filter.currentIndexChanged.connect(self.filter_results)
        toolbar.addWidget(self.result_filter)
        rl.addLayout(toolbar)
        self.results = table(["Server", "Protocol", "Port", "Anti-censorship", "Result", "Latency", "Loss", "Jitter", "Connect", "HTTPS"])
        self.results.setMinimumHeight(160)
        self.results.setSortingEnabled(True)
        self.results.itemSelectionChanged.connect(self.select_result)
        self.results.doubleClicked.connect(self.connect_selected)
        self.results.horizontalHeader().setStretchLastSection(False)
        self.results.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results.horizontalHeader().setMinimumSectionSize(52)
        rl.addWidget(self.results, 1)
        self.result_detail = label("", "muted", True)
        self.result_detail.setContentsMargins(16, 0, 16, 12)
        self.result_detail.hide()
        rl.addWidget(self.result_detail)
        layout.addWidget(results_card, 1)
        self.pages.addWidget(page)

    def build_setup(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setup_tabs = QTabWidget()
        layout.addWidget(self.setup_tabs, 1)
        routes = QWidget()
        route_layout = QHBoxLayout(routes)
        route_layout.setContentsMargins(0, 0, 0, 0)
        location_card, lc = card()
        title_line = QHBoxLayout()
        title_line.addWidget(label("Servers", "cardTitle"))
        title_line.addStretch()
        self.refresh_button = button("Refresh", self.refresh_catalog, "small")
        self.refresh_button.setEnabled(not self.demo)
        title_line.addWidget(self.refresh_button)
        lc.addLayout(title_line)
        self.location_search = QLineEdit()
        self.location_search.setPlaceholderText("Search servers")
        self.location_search.setClearButtonEnabled(True)
        self.location_search.textChanged.connect(self.filter_locations)
        lc.addWidget(self.location_search)
        selection_line = QHBoxLayout()
        selection_line.addWidget(button("Select visible", lambda: self.select_locations(True), "small"))
        selection_line.addWidget(button("Clear", lambda: self.select_locations(False), "small"))
        self.location_count = label("0 selected", "muted")
        selection_line.addWidget(self.location_count)
        lc.addLayout(selection_line)
        self.location_tree = QTreeWidget()
        self.location_tree.setHeaderHidden(True)
        self.location_tree.setUniformRowHeights(True)
        self.location_tree.setAccessibleName("Servers grouped by continent and country")
        self.location_tree.setToolTip("Check a continent or country to select all its servers, including filtered servers.")
        self.location_tree.itemChanged.connect(self.location_changed)
        lc.addWidget(self.location_tree, 1)
        self.catalog_caption = label(f"{len(self.locations)} servers · Public catalog", "muted")
        self.catalog_caption.setToolTip("Availability depends on your Windscribe account.")
        lc.addWidget(self.catalog_caption)
        route_layout.addWidget(location_card, 2)
        protocol_card, pc = card()
        protocol_title = QHBoxLayout()
        protocol_title.addWidget(label("Protocols & ports", "cardTitle"))
        protocol_title.addStretch()
        protocol_title.addWidget(button("443 preset", self.preset_443, "small"))
        protocol_title.addWidget(button("All ports", self.all_ports, "small"))
        pc.addLayout(protocol_title)
        self.ports_caption = label("Client verification pending", "muted")
        pc.addWidget(self.ports_caption)
        self.protocol_container = QWidget()
        self.protocol_container.setObjectName("protocolContainer")
        self.protocol_layout = QVBoxLayout(self.protocol_container)
        self.protocol_layout.setContentsMargins(0, 0, 0, 0)
        self.protocol_layout.setSpacing(16)
        self.protocol_checks = {}
        self.port_checks = {}
        self.build_protocols()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.protocol_container)
        pc.addWidget(scroll, 1)
        route_layout.addWidget(protocol_card, 3)
        self.setup_tabs.addTab(routes, "Servers && ports")
        self.build_anti_tab()
        details = QScrollArea()
        details.setWidgetResizable(True)
        settings_page = QWidget()
        sl = QHBoxLayout(settings_page)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setAlignment(Qt.AlignmentFlag.AlignTop)
        measure, ml = card()
        ml.addWidget(label("Measurement", "cardTitle"))
        form = QFormLayout()
        form.setSpacing(14)
        self.network_input = QLineEdit("My network")
        self.network_input.setPlaceholderText("e.g. Home · Irancell")
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(5, 180)
        self.timeout_input.setValue(30)
        self.timeout_input.setSuffix(" seconds")
        self.timeout_input.setToolTip("Connection window after client readiness. Disconnect, settle, HTTPS and measurements take additional time.")
        self.timeout_input.valueChanged.connect(self.update_plan_count)
        self.settle_input = QSpinBox()
        self.settle_input.setRange(0, 30)
        self.settle_input.setValue(3)
        self.settle_input.setSuffix(" seconds")
        self.settle_input.valueChanged.connect(self.update_plan_count)
        self.pings_input = QSpinBox()
        self.pings_input.setRange(2, 20)
        self.pings_input.setValue(5)
        self.pings_input.valueChanged.connect(self.update_plan_count)
        self.trials_input = QSpinBox()
        self.trials_input.setRange(1, 5)
        self.trials_input.valueChanged.connect(self.update_plan_count)
        self.download_input = QComboBox()
        self.download_input.addItems(["Off", "1 MB per connection", "5 MB per connection", "10 MB per connection"])
        self.download_input.currentIndexChanged.connect(self.update_plan_count)
        for caption, widget in (("Network label", self.network_input), ("Connect timeout", self.timeout_input),
                                ("Settle delay", self.settle_input), ("ICMP samples", self.pings_input),
                                ("Repeat each route", self.trials_input), ("Download sample", self.download_input)):
            form.addRow(caption, widget)
        ml.addLayout(form)
        self.stop_success = QCheckBox("Stop at first working route")
        self.stop_success.setToolTip("Leave the working route and its tested settings active.")
        self.restore_check = QCheckBox("Restore original connection")
        self.restore_check.setChecked(True)
        ml.addWidget(self.stop_success)
        ml.addWidget(self.restore_check)
        ml.addStretch()
        sl.addWidget(measure, 1)
        profile, pl = card()
        pl.addWidget(label("Session details", "cardTitle"))
        pf = QFormLayout()
        pf.setSpacing(12)
        self.profile_input = QLineEdit("Current settings")
        pf.addRow("Profile name", self.profile_input)
        self.setting_inputs = {}
        for key, title, placeholder in (("sni", "SNI (note)", "As configured"), ("mtu", "MTU (note)", "Auto")):
            text = QLineEdit()
            text.setPlaceholderText(placeholder)
            text.setToolTip("Session note only. Does not change Windscribe settings.")
            self.setting_inputs[key] = text
            pf.addRow(title, text)
        pl.addLayout(pf)
        self.notes_input = QPlainTextEdit()
        self.notes_input.setPlaceholderText("Notes (optional)")
        self.notes_input.setMaximumHeight(95)
        pl.addWidget(self.notes_input)
        pl.addStretch()
        sl.addWidget(profile, 1)
        details.setWidget(settings_page)
        self.setup_tabs.addTab(details, "Options")
        bottom, bt = card()
        bt.setContentsMargins(16, 12, 16, 12)
        bar = QHBoxLayout()
        self.plan_caption = label("", "", True)
        bar.addWidget(self.plan_caption, 1)
        self.save_button = button("Save setup", self.save_preferences)
        bar.addWidget(self.save_button)
        self.setup_start = button("Start test", self.start_run, "primary")
        self.setup_start.setIcon(line_icon("play", primary=True))
        bar.addWidget(self.setup_start)
        bt.addLayout(bar)
        layout.addWidget(bottom)
        self.pages.addWidget(page)

    def build_anti_tab(self):
        page, layout = card()
        top = QHBoxLayout()
        top.addWidget(label("Settings combinations", "cardTitle"))
        top.addStretch()
        self.anti_enabled = QCheckBox("Test these settings")
        self.anti_enabled.setToolTip("Apply each combination to all selected routes. Windscribe restarts when settings change.")
        self.anti_enabled.toggled.connect(self.update_plan_count)
        top.addWidget(self.anti_enabled)
        layout.addLayout(top)
        self.anti_status = label("11 sample presets · Simulation" if self.demo else "Checking client compatibility…", "muted", True)
        layout.addWidget(self.anti_status)
        form = QGridLayout()
        self.anti_tweaks = QComboBox()
        self.anti_tweaks.addItems(["Auto", "Enabled", "Disabled"])
        self.anti_preset = QComboBox()
        self.anti_preset.addItems(self.anti_presets)
        self.anti_preset.setMinimumContentsLength(22)
        self.anti_preset.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.anti_preset.setEnabled(False)
        self.anti_tweaks.currentTextChanged.connect(lambda text: self.anti_preset.setEnabled(text == "Enabled"))
        self.anti_routing = QComboBox()
        self.anti_routing.addItems(["Auto", "Regular", "Alternate"])
        self.anti_tls = QComboBox()
        self.anti_tls.addItems(["Off", "On"])
        self.anti_tls.setToolTip("Large TLS affects Windscribe API requests. Amnezia does not apply to IKEv2.")
        for col, (caption, widget) in enumerate((("Protocol tweaks", self.anti_tweaks), ("Amnezia configuration", self.anti_preset),
                                                  ("Server routing", self.anti_routing), ("Large TLS", self.anti_tls))):
            form.addWidget(label(caption, "muted"), 0, col)
            form.addWidget(widget, 1, col)
        form.addWidget(button("Add", self.add_anti), 1, 4)
        form.setColumnStretch(1, 3)
        for col in (0, 2, 3):
            form.setColumnStretch(col, 1)
        layout.addLayout(form)
        actions = QHBoxLayout()
        actions.addWidget(button("Use current", self.add_current_anti, "small"))
        actions.addWidget(button("All combinations", self.all_anti, "small"))
        actions.addWidget(button("Remove selected", self.remove_anti, "small"))
        actions.addWidget(button("Clear", self.clear_anti, "small"))
        actions.addStretch()
        self.anti_count = label("0 combinations", "muted")
        actions.addWidget(self.anti_count)
        layout.addLayout(actions)
        self.anti_table = table(["Protocol tweaks", "Amnezia configuration", "Server routing", "Large TLS"], "Add a settings combination")
        self.anti_table.setMinimumHeight(135)
        self.anti_table.horizontalHeader().setStretchLastSection(False)
        self.anti_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.anti_table, 1)
        recovery = QHBoxLayout()
        recovery.addStretch()
        self.recover_button = button("Review recovery", self.recover_settings, "small")
        pending = not self.demo and (self.data_dir / RECOVERY_FILE).exists()
        self.recover_button.setVisible(pending)
        self.recover_button.setEnabled(pending)
        recovery.addWidget(self.recover_button)
        layout.addLayout(recovery)
        self.setup_tabs.addTab(page, "Anti-censorship")

    def add_anti(self):
        profile = AntiCensorship(self.anti_tweaks.currentText(),
                                  self.anti_preset.currentText() if self.anti_tweaks.currentText() == "Enabled" else "",
                                  self.anti_routing.currentText(), self.anti_tls.currentText() == "On")
        if profile not in self.anti_profiles:
            self.anti_profiles.append(profile)
        self.anti_enabled.setChecked(True)
        self.render_anti()

    def add_current_anti(self):
        if self.anti_current is None:
            self.notice.setText("Check the client in Settings to read its current configuration.")
            return
        profile = AntiCensorship(**self.anti_current)
        if profile not in self.anti_profiles:
            self.anti_profiles.append(profile)
        self.anti_enabled.setChecked(True)
        self.render_anti()

    def all_anti(self):
        profiles = []
        for mode, preset in [("Auto", ""), ("Disabled", "")] + [("Enabled", preset) for preset in self.anti_presets]:
            for routing in ("Auto", "Regular", "Alternate"):
                for tls in (False, True):
                    profiles.append(AntiCensorship(mode, preset, routing, tls))
        self.anti_profiles = profiles
        self.anti_enabled.setChecked(True)
        self.render_anti()

    def remove_anti(self):
        row = self.anti_table.currentRow()
        if 0 <= row < len(self.anti_profiles):
            self.anti_profiles.pop(row)
        self.render_anti()

    def clear_anti(self):
        self.anti_profiles = []
        self.render_anti()

    def render_anti(self):
        self.anti_table.setRowCount(len(self.anti_profiles))
        for row, profile in enumerate(self.anti_profiles):
            values = [profile.protocol_tweaks, profile.amnezia or "—", profile.routing,
                      "On" if profile.large_tls else "Off"]
            for col, text in enumerate(values):
                self.anti_table.setItem(row, col, QTableWidgetItem(text))
        self.anti_count.setText(f"{len(self.anti_profiles)} combinations")
        self.update_plan_count()

    def recover_settings(self):
        if self.demo or self.busy() or not (self.data_dir / RECOVERY_FILE).exists():
            return
        try:
            controller = SettingsController(Client(self.cli_input.text()), self.data_dir)
            summary = controller.recovery_summary()
        except Exception as exc:
            self.show_error(str(exc))
            return
        current = AntiCensorship(**summary["current"])
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Review Windscribe settings recovery")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText("Current settings differ from the saved run." if summary["conflict"] else "A settings recovery backup is pending.")
        dialog.setInformativeText(
            f"Original: {AntiCensorship(**summary['original']).label}\n"
            f"Last tested: {AntiCensorship(**summary['expected']).label}\n"
            f"Current: {current.label}\n\n"
            "Restore original: restores the saved settings and connection; Windscribe may restart.\n\n"
            "Keep current: leaves Windscribe untouched and archives the encrypted original backup so you can start a new run."
        )
        restore_button = dialog.addButton("Restore original", QMessageBox.ButtonRole.AcceptRole)
        keep_button = dialog.addButton("Keep current settings", QMessageBox.ButtonRole.ActionRole)
        cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        if dialog.clickedButton() not in (restore_button, keep_button):
            return
        action = "keep_current" if dialog.clickedButton() == keep_button else "restore"
        self.recovery = SettingsRecovery(self.cli_input.text(), self.data_dir, action, current)
        self.recovery.recovered.connect(self.recovery_done)
        self.recovery.finished.connect(lambda: self.set_busy(False))
        self.set_busy(True, operation="recovery")
        self.recovery.start()
        self.notice.setText("Keeping current settings and archiving the original backup…" if action == "keep_current" else
                            "Restoring original Windscribe settings and connection…")

    def recovery_done(self, payload):
        if payload.get("error"):
            self.show_error(payload["error"])
        elif payload.get("kept_current"):
            self.notice.setText("Current Windscribe settings kept. The encrypted original backup was archived. You can start a new managed run.")
        else:
            self.notice.setText("Original anti-censorship settings and connection restored.")

    def build_protocols(self):
        selected = self.selected_protocols() if self.port_checks else {"wireguard": [443], "tcp": [443], "stealth": [443]}
        while self.protocol_layout.count():
            item = self.protocol_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.protocol_checks, self.port_checks = {}, {}
        for protocol, (title, _) in PROTOCOLS.items():
            group = QWidget()
            group.setObjectName("protocolGroup")
            gl = QVBoxLayout(group)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(8)
            enable = QCheckBox(title)
            enable.setChecked(bool(selected.get(protocol)))
            enable.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            gl.addWidget(enable)
            ports = QGridLayout()
            ports.setHorizontalSpacing(12)
            self.port_checks[protocol] = {}
            for index, port in enumerate(self.ports[protocol]):
                cb = QCheckBox(str(port))
                cb.setChecked(port in selected.get(protocol, []))
                cb.setEnabled(enable.isChecked())
                cb.toggled.connect(self.update_plan_count)
                self.port_checks[protocol][port] = cb
                ports.addWidget(cb, index // 5, index % 5)
            gl.addLayout(ports)
            self.protocol_checks[protocol] = enable
            enable.toggled.connect(lambda enabled, p=protocol: self.protocol_toggled(p, enabled))
            self.protocol_layout.addWidget(group)
        self.protocol_layout.addStretch()

    def protocol_toggled(self, protocol, enabled):
        for cb in self.port_checks[protocol].values():
            cb.setEnabled(enabled)
        if enabled and not any(cb.isChecked() for cb in self.port_checks[protocol].values()):
            next(iter(self.port_checks[protocol].values())).setChecked(True)
        self.update_plan_count()

    def preset_443(self):
        for protocol, checks in self.port_checks.items():
            self.protocol_checks[protocol].setChecked(protocol in ("wireguard", "tcp", "stealth", "wstunnel"))
            for port, cb in checks.items():
                cb.setChecked(port == 443)
        self.update_plan_count()

    def all_ports(self):
        for protocol, checks in self.port_checks.items():
            self.protocol_checks[protocol].setChecked(True)
            for cb in checks.values():
                cb.setChecked(True)
        self.update_plan_count()

    def populate_locations(self, preserve=None):
        chosen = set(preserve if preserve is not None else self.preferences.get("locations", []))
        if preserve is None and "locations" not in self.preferences:
            for code in ("DE", "NL", "GB"):
                candidate = next((l for l in self.locations if l.code == code), None)
                if candidate:
                    chosen.add(candidate.id)
        blocker = QSignalBlocker(self.location_tree)
        self.location_tree.clear()
        groups = {}
        self.continent_items = {}
        self.location_items = {}
        order = {name: i for i, name in enumerate(CONTINENTS)}
        for loc in sorted(self.locations, key=lambda l: (order.get(continent_for(l.code), 99), l.country, l.city, l.nickname)):
            continent = continent_for(loc.code)
            if continent not in self.continent_items:
                top = QTreeWidgetItem(self.location_tree, [continent])
                top.setData(0, Qt.ItemDataRole.UserRole, continent)
                top.setFlags(top.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
                top.setCheckState(0, Qt.CheckState.Unchecked)
                top.setExpanded(False)
                self.continent_items[continent] = top
            if loc.country not in groups:
                parent = QTreeWidgetItem(self.continent_items[continent], [f"{loc.country}  ·  {loc.code}"])
                parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
                parent.setCheckState(0, Qt.CheckState.Unchecked)
                groups[loc.country] = parent
            child = QTreeWidgetItem(groups[loc.country], [f"{loc.city}  /  {loc.nickname}"])
            child.setData(0, Qt.ItemDataRole.UserRole, loc.id)
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Checked if loc.id in chosen else Qt.CheckState.Unchecked)
            self.location_items[loc.id] = child
            if loc.id in chosen:
                groups[loc.country].setExpanded(True)
                self.continent_items[continent].setExpanded(True)
        del blocker
        self.filter_locations()
        self.update_plan_count()

    def filter_locations(self):
        query = self.location_search.text().strip().casefold()
        for loc in self.locations:
            child = self.location_items.get(loc.id)
            if child:
                child.setHidden(query not in f"{continent_for(loc.code)} {loc.country} {loc.code} {loc.city} {loc.nickname}".casefold())
        for i in range(self.location_tree.topLevelItemCount()):
            parent = self.location_tree.topLevelItem(i)
            for j in range(parent.childCount()):
                country = parent.child(j)
                country.setHidden(all(country.child(k).isHidden() for k in range(country.childCount())))
                if query and not country.isHidden():
                    country.setExpanded(True)
            parent.setHidden(all(parent.child(j).isHidden() for j in range(parent.childCount())))
            if query and not parent.isHidden():
                parent.setExpanded(True)

    def location_changed(self, item, column):
        self.update_plan_count()

    def select_locations(self, selected):
        blocker = QSignalBlocker(self.location_tree)
        for item in self.location_items.values():
            if not selected or not item.isHidden():
                item.setCheckState(0, Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
        del blocker
        self.update_plan_count()

    def selected_locations(self):
        return [loc for loc in self.locations if loc.id in getattr(self, "location_items", {})
                and self.location_items[loc.id].checkState(0) == Qt.CheckState.Checked]

    def selected_protocols(self):
        return {p: [port for port, cb in checks.items() if cb.isChecked()]
                for p, checks in self.port_checks.items() if self.protocol_checks[p].isChecked()}

    def update_plan_count(self, *_):
        if not hasattr(self, "plan_caption"):
            return
        locations = len(self.selected_locations())
        combos = sum(len(v) for v in self.selected_protocols().values())
        profiles = len(self.anti_profiles) if self.anti_enabled.isChecked() else 1
        count = locations * combos * self.trials_input.value() * profiles
        mb = [0, 1, 5, 10][self.download_input.currentIndex()] * count
        self.location_count.setText(f"{locations} selected")
        for name, item in getattr(self, "continent_items", {}).items():
            members = [loc for loc in self.locations if continent_for(loc.code) == name]
            selected = sum(self.location_items[loc.id].checkState(0) == Qt.CheckState.Checked for loc in members)
            item.setText(0, f"{name}  ·  {selected}/{len(members)}")
        estimate = round(count * (self.timeout_input.value() + self.settle_input.value() + self.pings_input.value() * 2 + 12) / 60)
        self.plan_caption.setText(f"{count:,} tests  ·  {locations} servers  ·  {combos} routes  ·  {profiles} settings")
        self.plan_caption.setToolTip(f"{self.trials_input.value()} repeat(s). Rough planning estimate ~{max(1, estimate)} minutes; "
                                     "client startup, settings restarts and cleanup can add time. "
                                     f"Download budget ≤ {mb:,} MB plus connectivity checks.")

    def read_config(self):
        if self.anti_enabled.isChecked() and not self.anti_profiles:
            raise SettingsError("Add at least one anti-censorship combination, or turn automatic settings testing off.")
        settings = {}
        for key, widget in self.setting_inputs.items():
            settings[key] = widget.currentText() if isinstance(widget, QComboBox) else widget.text().strip()
        settings["mtu"] = settings["mtu"] or "Auto"
        return RunConfig(self.cli_input.text().strip(), self.network_input.text().strip(), self.profile_input.text().strip(),
                         self.notes_input.toPlainText().strip(), self.timeout_input.value(), self.settle_input.value(),
                         self.pings_input.value(), self.trials_input.value(), [0, 1, 5, 10][self.download_input.currentIndex()],
                         self.stop_success.isChecked(), self.restore_check.isChecked(), settings,
                         [asdict(profile) for profile in self.anti_profiles] if self.anti_enabled.isChecked() else [])

    def save_preferences(self, checked=False, quiet=False):
        try:
            data = {"config": asdict(self.read_config()), "locations": [l.id for l in self.selected_locations()],
                    "protocols": self.selected_protocols(), "anti_profiles": [asdict(p) for p in self.anti_profiles],
                    "anti_enabled": self.anti_enabled.isChecked()}
            path = self.data_dir / "preferences.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
            if not quiet:
                self.notice.setText("Setup saved locally.")
        except (OSError, SettingsError) as exc:
            self.show_error(f"Could not save setup: {exc}")

    def apply_preferences(self):
        data = self.preferences
        try:
            cfg = RunConfig(**data.get("config", {}))
            cfg.validate()
        except (TypeError, ValueError, SettingsError):
            cfg = RunConfig()
        self.cli_input.setText(cfg.cli_path)
        self.network_input.setText(cfg.network)
        self.profile_input.setText(cfg.profile)
        self.notes_input.setPlainText(cfg.notes)
        self.timeout_input.setValue(cfg.timeout)
        self.settle_input.setValue(cfg.settle)
        self.pings_input.setValue(cfg.ping_count)
        self.trials_input.setValue(cfg.trials)
        self.download_input.setCurrentIndex([0, 1, 5, 10].index(cfg.download_mb))
        self.stop_success.setChecked(cfg.stop_on_success)
        self.restore_check.setChecked(cfg.restore)
        try:
            self.anti_profiles = [AntiCensorship(**value) for value in data.get("anti_profiles", cfg.anti_profiles)]
            for profile in self.anti_profiles:
                profile.validate()
        except (SettingsError, TypeError):
            self.anti_profiles = []
        self.anti_enabled.setChecked(bool(self.anti_profiles) and data.get("anti_enabled", bool(cfg.anti_profiles)))
        self.render_anti()
        for key, widget in self.setting_inputs.items():
            value = cfg.settings.get(key, "")
            if isinstance(widget, QComboBox):
                index = widget.findText(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            else:
                widget.setText(value)
        if "protocols" in data:
            for p, checks in self.port_checks.items():
                self.protocol_checks[p].setChecked(bool(data["protocols"].get(p)))
                for port, cb in checks.items():
                    cb.setChecked(port in data["protocols"].get(p, []))

    def build_history(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        w, lay = card()
        lay.setContentsMargins(0, 12, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 0, 16, 0)
        bar.addWidget(label("Sessions", "cardTitle"))
        bar.addStretch()
        bar.addWidget(button("Refresh", self.refresh_history, "small"))
        self.history_open = button("Open session", self.open_history)
        self.history_open.setEnabled(False)
        bar.addWidget(self.history_open)
        lay.addLayout(bar)
        self.history_table = table(["Started", "Network", "Profile", "Mode", "State", "Progress"], "No sessions yet")
        self.history_table.doubleClicked.connect(self.open_history)
        self.history_table.itemSelectionChanged.connect(lambda: self.history_open.setEnabled(self.history_table.currentRow() >= 0 and not self.busy()))
        self.history_table.horizontalHeader().setStretchLastSection(False)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.history_table, 1)
        layout.addWidget(w, 1)
        self.pages.addWidget(page)

    def refresh_history(self):
        rows = self.store.runs()
        self.history_table.setRowCount(len(rows))
        for idx, run in enumerate(rows):
            cfg = json.loads(run["config"])
            try:
                started = datetime.fromisoformat(run["created"]).astimezone().strftime("%d %b %Y  %H:%M")
            except ValueError:
                started = run["created"]
            values = [started, cfg["network"], cfg["profile"], "SIMULATED" if run["mode"] == "demo" else "LIVE",
                      run["state"].capitalize(), f"{run['completed']} / {run['total']}"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, run["id"])
                self.history_table.setItem(idx, col, item)

    def open_history(self, *_):
        if self.busy():
            return
        row = self.history_table.currentRow()
        if row < 0:
            self.notice.setText("Select a session in the history table first.")
            return
        self.active_run = self.history_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.reload_results()
        self.set_page(0)
        tasks = self.store.tasks(self.active_run)
        saved = sum(result is not None for _, _, result in tasks)
        self.run_activity.finish("saved", remaining=len(tasks) - saved, saved=saved)

    def build_diagnostics(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        client, cl = card()
        cl.addWidget(label("Windscribe client", "cardTitle"))
        path_line = QHBoxLayout()
        self.cli_input = QLineEdit(RunConfig().cli_path)
        self.cli_input.setPlaceholderText("Path to windscribe-cli.exe")
        self.cli_input.setAccessibleName("Official Windscribe CLI path")
        self.cli_input.editingFinished.connect(self.invalidate_client)
        path_line.addWidget(self.cli_input, 1)
        self.browse_button = button("Browse", self.browse_cli)
        path_line.addWidget(self.browse_button)
        self.check_button = button("Check client", self.check_client)
        self.check_button.setToolTip("Read client status, ports, and supported settings. Does not change the connection.")
        self.check_button.setEnabled(not self.demo)
        path_line.addWidget(self.check_button)
        cl.addLayout(path_line)
        self.diagnostic_text = label("Client checks disabled in simulation." if self.demo else "Checking client…", "muted", True)
        cl.addWidget(self.diagnostic_text)
        layout.addWidget(client)
        log_card, lg = card()
        log_header = QHBoxLayout()
        log_header.addWidget(label("Activity", "cardTitle"))
        log_header.addStretch()
        log_header.addWidget(button("Copy", self.copy_log, "small"))
        lg.addLayout(log_header)
        self.activity = QPlainTextEdit()
        self.activity.setObjectName("activity")
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(1500)
        self.activity.setMinimumHeight(150)
        self.activity.setFont(QFont("Cascadia Mono", 10))
        lg.addWidget(self.activity, 1)
        layout.addWidget(log_card, 1)
        self.pages.addWidget(page)

    def set_page(self, index):
        self.pages.setCurrentIndex(index)
        self.nav_group.button(index).setChecked(True)
        self.page_title.setText(("Tests", "Test setup", "History", "Settings")[index])
        if index == 2:
            self.refresh_history()

    def busy(self):
        return any(job and job.isRunning() for job in (self.runner, self.discovery, self.catalog_job, self.recovery))

    def invalidate_client(self):
        self.ports_verified = False
        if not self.demo:
            self.client_badge.setText("Check client path")

    def browse_cli(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose official Windscribe CLI", self.cli_input.text(), "Windscribe CLI (windscribe-cli.exe)")
        if path:
            self.cli_input.setText(path)
            self.invalidate_client()

    def check_client(self):
        if self.demo or self.busy():
            return
        self.check_button.setEnabled(False)
        self.client_badge.setText("Checking client")
        self.diagnostic_text.setText("Reading status and ports…")
        self.discovery = Discovery(self.cli_input.text())
        self.discovery.ready.connect(self.discovery_ready)
        self.discovery.progress.connect(self.discovery_progress)
        self.discovery.finished.connect(self.discovery_finished)
        self.set_busy(True, operation="discovery")
        self.discovery.start()

    def discovery_finished(self):
        self.set_busy(False)
        if self.closing:
            self.close()

    def discovery_progress(self, message):
        self.diagnostic_text.setText(message)
        self.notice.setText(message)
        self.log(message)

    def discovery_ready(self, payload):
        if "error" in payload:
            self.ports_verified = False
            self.client_badge.setText("Client unavailable")
            self.client_caption.setText("Check Settings")
            self.diagnostic_text.setText(payload["error"])
            self.log(payload["error"])
            self.notice.show_message(payload["error"], "error")
        else:
            status = payload["status"]
            self.ports, self.ports_verified = payload["ports"], True
            self.build_protocols()
            self.update_plan_count()
            self.client_badge.setText("Client ready" if status.logged_in else "Sign in to Windscribe")
            self.client_caption.setText(f"{status.state.capitalize()} · firewall {status.firewall}")
            self.diagnostic_text.setText(f"{'Signed in' if status.logged_in else 'Signed out'} · {status.state.capitalize()} · "
                                         f"Firewall {status.firewall} · {sum(len(p) for p in self.ports.values())} protocol/port pairs")
            self.ports_caption.setText(f"{sum(len(p) for p in self.ports.values())} protocol/port pairs verified")
            self.log("Client ready. Ports loaded.")
            if self.notice.text().startswith("Waiting for Windscribe"):
                self.notice.setText("Saved settings available. Review recovery in Anti-censorship." if (self.data_dir / RECOVERY_FILE).exists() else "")
            if payload.get("anti_current"):
                self.anti_current = payload["anti_current"]
                self.anti_presets = payload["anti_presets"]
                selected = self.anti_preset.currentText()
                self.anti_preset.clear()
                self.anti_preset.addItems(self.anti_presets)
                self.anti_preset.setCurrentIndex(max(0, self.anti_preset.findText(selected)))
                self.anti_status.setText(f"Windscribe 2.24.13 · {len(self.anti_presets)} Amnezia presets")
                self.anti_status.setToolTip("Current: " + AntiCensorship(**self.anti_current).label)
            else:
                self.anti_status.setText("Automatic settings unavailable: " + payload.get("anti_error", "Check client compatibility."))

    def refresh_catalog(self):
        if self.demo or self.busy():
            return
        self.catalog_job = CatalogJob(self.data_dir / "catalog.json")
        self.catalog_job.ready.connect(self.catalog_ready)
        self.catalog_job.finished.connect(lambda: self.set_busy(False))
        self.catalog_job.start()
        self.set_busy(True, operation="catalog")
        self.notice.setText("Refreshing the public server catalog…")

    def catalog_ready(self, payload):
        if "error" in payload:
            self.notice.setText(payload["error"])
            return
        selected = [loc.id for loc in self.selected_locations()]
        self.locations = payload["locations"]
        self.populate_locations(selected)
        self.catalog_caption.setText(f"{len(self.locations)} servers · Updated just now")
        self.notice.setText("Server catalog refreshed. Account availability may vary.")

    def confirm_live(self, cfg: RunConfig, count: int, resume=False, manual=False):
        if self.demo:
            return True
        action = "Connect" if manual else "Resume tests" if resume else "Start tests"
        ending = ("The working route will stay connected." if manual or cfg.stop_on_success else
                  "Your original connection will be restored." if cfg.restore else
                  "The VPN will be disconnected when testing ends.")
        lines = ["Testing interrupts your VPN connection.", ending]
        if cfg.anti_profiles:
            lines.append("Settings changes restart Windscribe. Firewall protection may briefly lapse unless Always On is enabled.")
        lines.append("Connectivity checks use Cloudflare and ipify.")
        dialog = QMessageBox(self)
        dialog.setWindowTitle(action)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText("Connect to this route?" if manual else f"{'Resume' if resume else 'Start'} {count:,} tests?")
        dialog.setInformativeText("\n\n".join(lines))
        accept = dialog.addButton(action, QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        return dialog.clickedButton() == accept

    def start_run(self):
        if self.busy():
            return
        try:
            cfg = self.read_config()
            cfg.validate()
            plan = make_plan(self.selected_locations(), self.selected_protocols(), cfg.trials, cfg.anti_profiles)
            if not self.confirm_live(cfg, len(plan)):
                return
            self.save_preferences(quiet=True)
            self.active_run = self.store.create_run(cfg, plan, "demo" if self.demo else "live")
            self.launch_runner()
        except (ValueError, OSError, SettingsError) as exc:
            self.show_error(str(exc))

    def resume_run(self):
        if self.busy() or not self.active_run:
            return
        run = self.store.run(self.active_run)
        if (run["mode"] == "demo") != self.demo:
            self.show_error("Open this session in the matching live or simulation mode.")
            return
        count = sum(result is None for _, _, result in self.store.tasks(self.active_run))
        if count and self.confirm_live(run["config"], count, resume=True):
            self.launch_runner()

    def launch_runner(self, manual=False):
        self.runner = Runner(self.store, self.active_run, manual=manual)
        self.runner.update.connect(self.runner_update)
        self.runner.finished_run.connect(self.runner_done)
        self.runner.finished.connect(self.thread_finished)
        self.set_page(0)
        self.reload_results()
        self.set_busy(True)
        self.run_activity.begin()
        self.target_label.setText("Simulation" if self.demo else "Reading client status and supported ports")
        self.runner.start()

    def thread_finished(self):
        self.set_busy(False)
        self.reload_results()
        self.refresh_history()
        if self.closing:
            self.close()

    def pause_run(self):
        if self.runner and self.runner.isRunning():
            self.runner.cancel.set()
            self.pause_button.setEnabled(False)
            self.run_activity.pause()

    def runner_update(self, payload):
        if "result" in payload:
            result = payload["result"]
            self.run_activity.saved(result, OUTCOMES.get(result.outcome, (result.outcome,))[0])
        else:
            self.run_activity.set_phase(payload["phase"], payload["message"], payload.get("budget"))
        if payload.get("route"):
            self.target_label.setText(payload["route"].display)
        elif payload["phase"] in ("Restore", "Restore settings"):
            self.target_label.setText("Returning to your saved connection and settings")
        elif payload["phase"] == "Finish disconnect":
            self.target_label.setText("Finishing with the VPN disconnected")
        self.log(payload["message"])
        if "result" in payload:
            self.reload_results()
        if "completed" in payload:
            self.progress.setMaximum(max(1, payload["total"]))
            self.progress.setValue(payload["completed"])
            self.progress_caption.setText(f"{payload['completed']:,} / {payload['total']:,} tested")
            if payload["phase"] == "Preparing":
                self.run_activity.sequence.setText(f"Test {payload['completed'] + 1:,} / {payload['total']:,}")

    def runner_done(self, payload):
        tasks = self.store.tasks(self.active_run)
        saved = sum(result is not None for _, _, result in tasks)
        error = payload["cleanup_error"] or payload["error"]
        self.run_activity.finish(payload["state"], error, len(tasks) - saved, saved)
        if payload["cleanup_error"]:
            self.phase_label.setText("Restoration needs attention")
            self.notice.setText("")
            self.log(f"Restoration needs attention: {payload['cleanup_error']}")
        elif payload["error"]:
            self.notice.setText("")
        else:
            self.notice.setText("Working settings kept. Review recovery to restore."
                                if payload.get("settings_retained") else "")

    def set_busy(self, busy, operation="run"):
        for widget in (self.start_button, self.setup_start, self.setup_tabs, self.save_button,
                       self.cli_input, self.browse_button):
            widget.setEnabled(not busy)
        self.pause_button.setEnabled(busy and operation == "run")
        self.pause_button.setVisible(busy and operation == "run")
        self.start_button.setVisible(not (busy and operation == "run"))
        self.check_button.setEnabled(not busy and not self.demo)
        self.refresh_button.setEnabled(not busy and not self.demo)
        self.resume_button.setEnabled(not busy and bool(self.active_run) and any(r is None for _, _, r in self.store.tasks(self.active_run)))
        self.resume_button.setVisible(self.resume_button.isEnabled())
        self.best_button.setEnabled(not busy and self.best_record() is not None)
        self.recover_button.setEnabled(not busy and not self.demo and (self.data_dir / RECOVERY_FILE).exists())
        self.recover_button.setVisible(not self.demo and (self.data_dir / RECOVERY_FILE).exists())
        self.history_open.setEnabled(not busy and self.history_table.currentRow() >= 0)

    def reload_results(self):
        if not self.active_run:
            return
        tasks = self.store.tasks(self.active_run)
        completed = {seq: (route, result) for seq, route, result in tasks if result is not None}
        self.records = list(completed.values())
        self._results_by_seq = completed
        run = self.store.run(self.active_run)
        cfg = run["config"]
        self.mode_badge.setText("Simulation" if run["mode"] == "demo" else "Live")
        self.results_scope.setText(f"{cfg.network}  /  {cfg.profile}")
        self.results_scope.setToolTip("Anti-censorship settings are recorded per route. SNI / MTU notes remain user-declared.")
        reset = self._rendered_run != self.active_run or not completed.keys() >= self._rendered_results.keys()
        changed = [seq for seq, record in completed.items() if reset or self._rendered_results.get(seq) != record]
        if reset or changed:
            result_signals = QSignalBlocker(self.results)
            sorting = self.results.isSortingEnabled()
            updates = self.results.updatesEnabled()
            self.results.setUpdatesEnabled(False)
            self.results.setSortingEnabled(False)
            try:
                if reset:
                    self._result_row_items.clear()
                    # Replacing thousands of existing Python-backed items via
                    # setItem can hang inside Qt. Clear once on a session switch.
                    self.results.clearContents()
                    self.results.setRowCount(0)
                    self.result_detail.hide()
                next_row = self.results.rowCount()
                new_rows = sum(seq not in self._result_row_items for seq in changed)
                self.results.setRowCount(next_row + new_rows)
                for seq in changed:
                    first = self._result_row_items.get(seq)
                    row = first.row() if first is not None else next_row
                    if first is None:
                        next_row += 1
                    route, result = completed[seq]
                    self.update_result_row(row, seq, route, result)
                    self._result_row_items[seq] = self.results.item(row, 0)
                self._rendered_run = self.active_run
                self._rendered_results = completed
            finally:
                self.results.setSortingEnabled(sorting)
                self.results.setUpdatesEnabled(updates)
                del result_signals
        self.filter_results()
        total = len(tasks)
        verified = sum(r.tunnel_verified for _, r in self.records)
        working = sum(r.working for _, r in self.records)
        latencies = [r.latency_ms for _, r in self.records if r.working and r.latency_ms is not None]
        self.metrics[0].setText(str(len(self.records)))
        self.metrics[1].setText(str(verified))
        self.metrics[2].setText(f"{min(latencies):g} ms" if latencies else "—")
        self.metrics[3].setText(f"{working / len(self.records) * 100:.0f}%" if self.records else "—")
        best = self.best_record()
        if best:
            route, result = best
            self.best_name.setText(route.location.display)
            self.best_info.setText(f"{PROTOCOLS[route.protocol][0]} : {route.port}  ·  "
                                   f"{result.latency_ms:g} ms" if result.latency_ms is not None else
                                   f"{PROTOCOLS[route.protocol][0]} : {route.port}  ·  ICMP unavailable")
            self.best_info.setToolTip(route.anti_censorship.label if route.anti_censorship else "Current Windscribe settings")
        else:
            self.best_name.setText("No verified route yet")
            self.best_info.setText("")
        busy = self.busy()
        self.best_button.setEnabled(bool(best) and not busy)
        self.resume_button.setEnabled(len(self.records) < total and not busy)
        self.resume_button.setVisible(self.resume_button.isEnabled())
        self.export_button.setEnabled(not self.exporting())
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(len(self.records))
        self.progress_caption.setText(f"{len(self.records):,} / {total:,} tested")

    def update_result_row(self, row, seq, route, result):
        outcome, color = OUTCOMES.get(result.outcome, (result.outcome, MUTED))
        num = lambda value, suffix: "—" if value is None else f"{value:g}{suffix}"
        anti = route.anti_censorship
        short_anti = (f"{anti.amnezia.split(' - ')[0] if anti.protocol_tweaks == 'Enabled' else anti.protocol_tweaks} · {anti.routing} · TLS {'On' if anti.large_tls else 'Off'}" if anti else "As configured")
        values = [route.location.display, PROTOCOLS[route.protocol][0], str(route.port), short_anti, outcome,
                  num(result.latency_ms, " ms"), num(result.loss_pct, "%"), num(result.jitter_ms, " ms"),
                  num(result.connect_s, " s"), f"{result.https_ok}/{result.https_total}"]
        numeric = {2: route.port, 5: result.latency_ms, 6: result.loss_pct, 7: result.jitter_ms, 8: result.connect_s, 9: result.https_ok}
        tooltip = (f"{route.location.country} · trial {route.trial}\n{anti.label if anti else 'As configured'}\n"
                   f"Settings: {result.settings_verification}\n{result.detail}" +
                   (f"\nDownload sample: {result.download_mbps:g} Mbps" if result.download_mbps is not None else ""))
        for col, value in enumerate(values):
            item = self.results.item(row, col)
            fresh = item is None
            if fresh:
                item = SortItem(value)
            else:
                item.setText(value)
            item.setToolTip(tooltip)
            item.setData(Qt.ItemDataRole.UserRole, seq)
            if col in numeric:
                item.setData(Qt.ItemDataRole.UserRole + 1, numeric[col] if numeric[col] is not None else float("inf"))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col == 4:
                item.setForeground(QColor(color))
            if fresh:
                self.results.setItem(row, col, item)

    def best_record(self):
        candidates = [(route, result) for route, result in self.records if result.tunnel_verified]
        return min(candidates, key=lambda v: (v[1].loss_pct if v[1].loss_pct is not None else 100,
                                              v[1].latency_ms if v[1].latency_ms is not None else float("inf"),
                                              v[1].connect_s or float("inf"))) if candidates else None

    def filter_results(self):
        query = self.result_search.text().casefold()
        kind = self.result_filter.currentIndex()
        for row in range(self.results.rowCount()):
            item = self.results.item(row, 0)
            if not item:
                continue
            route, result = self._results_by_seq[item.data(Qt.ItemDataRole.UserRole)]
            text = f"{route.display} {route.location.country} {result.outcome} {OUTCOMES.get(result.outcome, ('',))[0]}".casefold()
            visible = query in text and (kind == 0 or kind == 1 and result.working or
                                         kind == 2 and result.tunnel_verified or kind == 3 and not result.working)
            self.results.setRowHidden(row, not visible)
        self.results.update_empty()

    def select_result(self):
        row = self.results.currentRow()
        if row >= 0 and self.results.item(row, 0):
            route, result = self._results_by_seq[self.results.item(row, 0).data(Qt.ItemDataRole.UserRole)]
            speed = f" Download sample: {result.download_mbps:g} Mbps." if result.download_mbps is not None else ""
            self.result_detail.setText(f"Trial {route.trial} · Settings: {result.settings_verification} · {result.detail}{speed}")
            self.result_detail.show()

    def connect_best(self):
        record = self.best_record()
        if record:
            self.connect_route(record[0])

    def connect_selected(self, *_):
        row = self.results.currentRow()
        if row < 0:
            return
        route, result = self._results_by_seq[self.results.item(row, 0).data(Qt.ItemDataRole.UserRole)]
        if result.working:
            self.connect_route(route)
        else:
            self.notice.setText("This route did not pass both HTTPS checks. Resume or run a new plan to retest it.")

    def connect_route(self, route):
        if self.busy():
            return
        cfg = RunConfig(**asdict(self.store.run(self.active_run)["config"]))
        cfg.cli_path = self.cli_input.text().strip()
        cfg.stop_on_success = True
        cfg.anti_profiles = [asdict(route.anti_censorship)] if route.anti_censorship else []
        if self.confirm_live(cfg, 1, manual=True):
            self.active_run = self.store.create_run(cfg, [route], "demo" if self.demo else "live")
            self.launch_runner(manual=True)

    def export_report(self):
        if not self.active_run or self.exporting():
            return
        snapshot = self.store.report_snapshot(self.active_run)
        options = ExportDialog(snapshot, self)
        if options.exec() != ExportDialog.DialogCode.Accepted:
            return
        _, extension, name_filter = options.FORMATS[options.format_combo.currentIndex()]
        picker = QFileDialog(self, "Save report")
        picker.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        picker.setNameFilter(name_filter)
        picker.setDefaultSuffix(extension.lstrip("."))
        picker.selectFile(f"WindscribeLab-{self.active_run[:8]}{'-working' if options.working_only else ''}{extension}")
        if not picker.exec():
            return
        path = Path(picker.selectedFiles()[0])
        if path.suffix.lower() not in ((".html", ".htm") if extension == ".html" else (extension,)):
            self.show_error(f"Use a {extension} filename for the selected format.")
            return
        self.export_job = ExportJob(snapshot, path, options.working_only)
        self.export_job.ready.connect(self.export_ready)
        self.export_job.finished.connect(self.export_finished)
        self.export_button.setEnabled(False)
        self.notice.setText("Saving report…")
        self.export_job.start()

    def exporting(self):
        return bool(self.export_job and self.export_job.isRunning())

    def export_ready(self, payload):
        if "error" in payload:
            self.show_error(payload["error"])
        else:
            self.notice.setText(f"Exported {payload['summary']['exported']:,} tests: {payload['path']}")

    def export_finished(self):
        self.export_button.setEnabled(bool(self.active_run))
        if self.closing:
            self.close()

    def log(self, message):
        self.activity.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {message}")

    def copy_log(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.activity.toPlainText())
        self.notice.setText("Activity copied.")

    def show_about(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("About Windscribe Lab")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"Windscribe Lab {__version__}")
        dialog.setInformativeText("Independent Windscribe connection tester.\nResults stay on this computer.")
        dialog.setDetailedText(
            "Uses your installed official Windscribe client.\n"
            "Connectivity checks: Cloudflare, ipify, and 1.1.1.1. These services see the request IP.\n"
            "Optional download samples use Cloudflare.\n"
            "Automatic anti-censorship settings require Windscribe 2.24.13.\n"
            f"Local data: {self.data_dir}\n\n"
            "See README and THIRD_PARTY.md for behavior, limitations, and licenses."
        )
        dialog.exec()

    def show_error(self, message):
        self.notice.show_message(message, "error")
        self.log(message)

    def closeEvent(self, event):
        if self.busy() or self.exporting():
            self.closing = True
            if self.runner and self.runner.isRunning():
                self.runner.cancel.set()
            if self.discovery and self.discovery.isRunning():
                self.discovery.cancel.set()
            self.notice.setText("Finishing cleanup before closing…")
            event.ignore()
            QTimer.singleShot(600, self.close)
            return
        if not self.anti_enabled.isChecked() or self.anti_profiles:
            self.save_preferences(quiet=True)
        event.accept()
