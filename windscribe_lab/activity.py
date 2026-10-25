"""Run feedback driven by worker events; timers only animate and show elapsed time."""
import time

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QSizePolicy


class ActivityIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.tone = "idle"
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)
        self.setAccessibleName("Ready")

    def set_tone(self, tone):
        self.tone = tone
        self.setAccessibleName({"running": "In progress", "error": "Needs attention",
                                "success": "Complete", "warning": "Paused"}.get(tone, "Ready"))
        if tone == "running" and self.isVisible():
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def advance(self):
        self.angle = (self.angle + 15) % 360
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self.tone == "running":
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = {"running": "#a3b7ff", "success": "#76d9b5", "error": "#f79595",
                  "warning": "#eac477", "idle": "#626b7c"}
        painter.setPen(QPen(QColor("#35405a"), 2))
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.setPen(QPen(QColor(colors[self.tone]), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.tone == "running":
            painter.drawArc(QRectF(3, 3, 18, 18), -self.angle * 16, 110 * 16)
        elif self.tone == "success":
            painter.drawLine(7, 12, 10, 15)
            painter.drawLine(10, 15, 17, 8)
        elif self.tone == "error":
            painter.drawLine(12, 7, 12, 13)
            painter.drawPoint(12, 17)
        elif self.tone == "warning":
            painter.drawLine(9, 8, 9, 16)
            painter.drawLine(15, 8, 15, 16)
        else:
            painter.drawPoint(12, 12)


class RunActivity(QWidget):
    STAGES = ("Disconnect", "Connect", "Settle", "HTTPS", "Measure")
    PHASES = {"Disconnect": 0, "Connect": 1, "Settle": 2, "Verify HTTPS": 3,
              "Measure": 4, "Download": 4, "Validate": 4}
    TITLES = {"Preflight": "Checking client", "Starting Windscribe": "Waiting for Windscribe",
              "Preparing": "Preparing next test", "Disconnect": "Disconnecting",
              "Connect": "Connecting", "Settle": "Letting the route settle",
              "Verify HTTPS": "Checking HTTPS & VPN IP", "Measure": "Measuring latency & loss",
              "Download": "Measuring download speed", "Validate": "Confirming connection",
              "Apply settings": "Applying anti-censorship settings", "Settings": "Updating Windscribe",
              "Restore settings": "Restoring original settings", "Restore": "Restoring original connection",
              "Finish disconnect": "Disconnecting after tests",
              "Paused": "Pausing", "Error": "Test stopped", "Attention": "Restoration needs attention"}

    def __init__(self):
        super().__init__()
        self.setObjectName("runActivity")
        self.phase = ""
        self.budget = None
        self.started = None
        self.states = ["pending"] * 5
        self.stage = None
        self.active = False
        self.pausing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.indicator = ActivityIndicator()
        self.title = QLabel("Ready")
        self.title.setObjectName("activityTitle")
        head.addWidget(self.indicator)
        head.addWidget(self.title)
        head.addStretch()
        self.sequence = QLabel("")
        self.sequence.setObjectName("muted")
        head.addWidget(self.sequence)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        self.clock = QLabel("")
        self.clock.setObjectName("muted")
        layout.addLayout(head)
        self.target = QLabel("Choose servers and ports in Test setup.")
        self.target.setObjectName("activityTarget")
        self.target.setTextFormat(Qt.TextFormat.PlainText)
        self.target.setWordWrap(True)
        self.target.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.target)
        self.detail = QLabel("")
        self.detail.setObjectName("activityDetail")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        self.detail.hide()
        layout.addWidget(self.detail)
        self.rail = QWidget()
        self.rail.setObjectName("stageRail")
        row = QHBoxLayout(self.rail)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)
        self.steps = []
        for name in self.STAGES:
            step = QLabel(name)
            step.setObjectName("runStep")
            step.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.steps.append(step)
            row.addWidget(step, 1)
        self.stage_row = QWidget()
        self.stage_row.setObjectName("stageRail")
        stages = QHBoxLayout(self.stage_row)
        stages.setContentsMargins(0, 0, 0, 0)
        stages.setSpacing(12)
        stages.addWidget(self.rail, 1)
        stages.addWidget(self.clock)
        layout.addWidget(self.stage_row)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.paint_steps()

    def paint_steps(self):
        for i, (step, state) in enumerate(zip(self.steps, self.states)):
            prefix = {"done": "✓", "error": "!", "active": "●", "warning": "Ⅱ"}.get(state, str(i + 1))
            step.setText(f"{prefix}  {self.STAGES[i]}")
            step.setProperty("state", state)
            step.setAccessibleName(f"{self.STAGES[i]}: {state}")
            step.style().unpolish(step)
            step.style().polish(step)

    def set_detail(self, text, tone="normal"):
        self.detail.setText(text)
        self.detail.setToolTip(text)
        self.detail.setProperty("tone", tone)
        self.detail.style().unpolish(self.detail)
        self.detail.style().polish(self.detail)
        self.detail.setVisible(bool(text))

    def begin(self):
        self.phase = ""
        self.stage = None
        self.states = ["pending"] * 5
        self.pausing = False
        self.sequence.clear()
        self.set_detail("")
        self.set_phase("Preflight", "")

    def set_phase(self, phase, message="", budget=None):
        if phase == "Preparing":
            self.states = ["pending"] * 5
            self.stage = None
        new_stage = self.PHASES.get(phase)
        if new_stage is not None:
            if self.stage is not None and new_stage > self.stage:
                self.states[self.stage] = "done"
            self.stage = new_stage
            self.states[new_stage] = "active"
        if phase != self.phase:
            self.started = time.monotonic()
        self.phase, self.budget = phase, budget
        if phase in ("Restore", "Restore settings", "Finish disconnect", "Preflight"):
            self.sequence.clear()
        self.active = phase not in ("Error", "Attention", "Paused")
        self.title.setText(self.TITLES.get(phase, phase))
        if phase == "Settings" and message.startswith("Restarting"):
            self.title.setText("Restarting Windscribe for settings")
        if self.pausing and phase not in ("Restore", "Restore settings", "Finish disconnect", "Error", "Attention"):
            self.title.setText("Pausing after current step")
        self.stage_row.show()
        self.rail.setVisible(phase in self.PHASES or phase in ("Preparing", "Error", "Attention", "Paused"))
        if phase in ("Error", "Attention"):
            if self.stage is not None:
                self.states[self.stage] = "error"
            self.set_detail(message, "error")
            tone = "error"
        elif phase == "Paused":
            tone = "warning"
            self.set_detail("")
        else:
            tone = "running"
            self.set_detail(message if phase in ("Settings", "Starting Windscribe", "Restore settings") else "")
        self.indicator.set_tone(tone)
        self.paint_steps()
        if self.active and self.isVisible():
            self.timer.start()
        else:
            self.timer.stop()
        self.tick()

    def saved(self, result, outcome):
        self.active = False
        self.timer.stop()
        if self.stage is not None:
            self.states[self.stage] = "done"
        failed_stage = {"timeout": 1, "connect_error": 1, "route_mismatch": 1,
                        "https_only": 3, "partial": 3, "no_internet": 3, "unstable": 4}.get(result.outcome)
        if failed_stage is not None:
            self.states[failed_stage] = "error"
        self.title.setText(outcome)
        self.indicator.set_tone("success" if result.tunnel_verified else "warning")
        self.set_detail("")
        self.paint_steps()
        self.tick()

    def pause(self):
        self.pausing = True
        self.title.setText("Pausing after current step")
        self.indicator.set_tone("running")

    def finish(self, state, error="", remaining=0, saved=0):
        self.active = False
        self.pausing = False
        self.timer.stop()
        self.indicator.set_tone({"error": "error", "paused": "warning", "saved": "idle"}.get(state, "success"))
        self.title.setText({"complete": "Session complete", "paused": "Session paused",
                            "connected": "Working route left connected", "error": "Session needs attention",
                            "saved": "Saved session"}.get(state, state))
        if state != "connected":
            self.target.setText(f"{saved:,} saved" + (f"  ·  {remaining:,} remaining" if remaining else ""))
        self.sequence.clear()
        self.set_detail(error, "error" if error else "normal")
        self.clock.setText("")
        self.rail.hide()
        self.stage_row.hide()
        self.started = None

    def tick(self):
        if self.started is None:
            return
        elapsed = max(0, int(time.monotonic() - self.started))
        text = f"{elapsed}s elapsed"
        if self.budget is not None:
            text += f"  ·  {self.budget:g}s {'wait' if self.phase == 'Settle' else 'timeout'}"
        self.clock.setText(text)
        self.clock.setToolTip("Time in the current stage. Client readiness and cleanup can take additional time.")

    def showEvent(self, event):
        super().showEvent(event)
        if self.active:
            self.tick()
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
