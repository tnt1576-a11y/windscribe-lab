"""Large resume runs in a subprocess so a native table deadlock has a deadline."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from windscribe_lab.models import Location, Result, Route, RunConfig
from windscribe_lab.ui import MainWindow

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("total,completed", [(900, 700), (10000, 9700)])
def test_saved_large_run_resumes_without_ui_deadlock_or_retesting_completed_rows(tmp_path, total, completed):
    code = "import runpy,sys;sys.path.insert(0,sys.argv[1]);runpy.run_path(sys.argv[2],run_name='__main__')"
    run = subprocess.run([sys.executable, "-c", code, str(ROOT), str(Path(__file__)),
                          str(tmp_path), str(total), str(completed)],
                         capture_output=True, text=True, encoding="utf-8", timeout=30,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout.strip().splitlines()[-1])
    assert payload["saved"] == completed + 2 and payload["total"] == total
    assert payload["heartbeats"] >= 3 and payload["max_ui_gap_s"] < 1.5
    assert max(payload["resume_s"]) < 2


def window_with_sparse_results(tmp_path):
    w = MainWindow(tmp_path, ROOT / "assets", demo=True)
    plan = [Route(Location(str(i), "Germany", "DE", "Frankfurt", f"Server {i}"), "tcp", port)
            for i, port in enumerate((443, 80, 587, 21))]
    w.active_run = w.store.create_run(w.read_config(), plan, "demo")
    for seq in (1, 3):
        w.store.record(w.active_run, seq, Result("verified", f"Evidence {seq}", latency_ms=40+seq,
                                               https_ok=2, tunnel_verified=True))
    w.reload_results()
    return w, plan


def find_row(w, nickname):
    return next(row for row in range(w.results.rowCount()) if nickname in w.results.item(row, 0).text())


def test_sorted_selection_and_route_actions_stay_correct_when_earlier_hole_is_filled(tmp_path, qapp):
    from PySide6.QtCore import Qt
    w, plan = window_with_sparse_results(tmp_path)
    w.results.sortItems(2, Qt.SortOrder.DescendingOrder)
    row = find_row(w, "Server 3")
    selected = w.results.item(row, 0)
    w.results.setCurrentCell(row, 0)
    assert "Evidence 3" in w.result_detail.text()
    w.store.record(w.active_run, 0, Result("https_only", "Evidence 0", https_ok=2))
    w.reload_results()
    assert w.results.rowCount() == 3 and w.results.item(selected.row(), 0) is selected
    assert w.results.item(w.results.currentRow(), 0) is selected
    with patch.object(w, "connect_route") as connect:
        w.connect_selected()
    connect.assert_called_once_with(plan[3])
    w.select_result()
    assert "Evidence 3" in w.result_detail.text()
    w.result_filter.setCurrentIndex(2)
    assert sum(not w.results.isRowHidden(row) for row in range(3)) == 2
    w.close()


def test_changed_result_updates_existing_cells_and_filter_without_replacing_items(tmp_path, qapp):
    w, _ = window_with_sparse_results(tmp_path)
    row = find_row(w, "Server 1")
    first, outcome = w.results.item(row, 0), w.results.item(row, 4)
    w.store.record(w.active_run, 1, Result("timeout", "Changed evidence", connect_s=30))
    w.reload_results()
    assert w.results.item(first.row(), 0) is first
    assert w.results.item(first.row(), 4) is outcome and outcome.text() == "Timed out"
    w.result_filter.setCurrentIndex(3)
    assert not w.results.isRowHidden(first.row())
    w.result_filter.setCurrentIndex(2)
    assert w.results.isRowHidden(first.row())
    w.close()


def test_session_switch_and_removed_result_clear_stale_rows(tmp_path, qapp):
    w, plan = window_with_sparse_results(tmp_path)
    first = w.active_run
    other = w.store.create_run(RunConfig(network="Other network"), [plan[0]], "demo")
    w.store.record(other, 0, Result("timeout", "Other session"))
    w.active_run = other
    w.reload_results()
    assert w.results.rowCount() == 1 and "Other network" in w.results_scope.text()
    assert not w.best_record()
    w.active_run = first
    w.reload_results()
    assert w.results.rowCount() == 2 and w.best_record()
    with w.store.connect() as conn:
        conn.execute("UPDATE tasks SET result=NULL WHERE run_id=? AND seq=3", (first,))
    w.reload_results()
    assert w.results.rowCount() == 1 and "Server 1" in w.results.item(0, 0).text()
    w.close()


def large_scenario(directory, total, completed):
    import time
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QFontDatabase, QFont
    from PySide6.QtWidgets import QApplication
    from windscribe_lab.antischema import AntiCensorship, KNOWN_PRESETS
    from windscribe_lab.models import make_plan
    from windscribe_lab.theme import apply_theme

    app = QApplication([])
    for font in ("segoeui.ttf", "seguisb.ttf", "seguisym.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    app.setFont(QFont("Segoe UI", 10))
    apply_theme(app)
    w = MainWindow(directory, ROOT / "assets", demo=True)
    profiles = [asdict(AntiCensorship("Enabled", KNOWN_PRESETS[9], "Auto", False)),
                asdict(AntiCensorship("Enabled", KNOWN_PRESETS[9], "Alternate", True))]
    cfg = RunConfig(anti_profiles=profiles)
    locations = [Location(str(i), "Germany", "DE", "Frankfurt", f"Server {i:03d}") for i in range(total // 50)]
    ports = {"tcp": [443, 587, 21, 22, 80, 123, 143, 3306, 8080, 54783, 1194],
             "wireguard": [443, 80, 53, 123, 1194, 65142], "udp": [443, 80, 53, 123, 1194, 54783],
             "ikev2": [500], "stealth": [443]}
    plan = make_plan(locations, ports, anti_profiles=profiles)
    assert len(plan) == total
    run = w.store.create_run(cfg, plan, "demo")
    value = json.dumps(asdict(Result("verified", "Saved evidence", latency_ms=64, https_ok=2, tunnel_verified=True)))
    with w.store.connect() as conn:
        conn.executemany("UPDATE tasks SET result=? WHERE run_id=? AND seq=?", [(value, run, i) for i in range(completed)])
    w.store.set_state(run, "paused")
    w.show()
    w.set_page(2)
    w.history_table.selectRow(0)
    w.open_history()
    assert w.results.rowCount() == completed
    w.results.sortItems(2, Qt.SortOrder.DescendingOrder)
    item = w.results.item(0, 0)
    w.results.setCurrentItem(item)
    heartbeats = [time.monotonic()]
    clock = QTimer()
    clock.setInterval(10)
    clock.timeout.connect(lambda: heartbeats.append(time.monotonic()))
    clock.start()
    durations, first_routes = [], []
    with patch("windscribe_lab.engine.Client", side_effect=AssertionError("Live CLI invoked")), \
         patch("windscribe_lab.engine.https_get", side_effect=AssertionError("Network invoked")):
        for epoch in range(2):
            start = time.monotonic()
            w.resume_run()
            durations.append(time.monotonic()-start)
            def observe(payload):
                if "result" in payload:
                    first_routes.append(payload["route"])
                    w.pause_run()
            w.runner.update.connect(observe)
            deadline = time.monotonic() + 10
            while w.runner.isRunning() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            app.processEvents()
            assert not w.runner.isRunning(), "Resume worker failed to pause"
            assert w.store.run(run)["state"] == "paused"
            assert len(w.records) == completed + epoch + 1
            assert first_routes[epoch] == plan[completed + epoch]
            assert w.results.item(item.row(), 0) is item
            assert w.results.item(w.results.currentRow(), 0) is item
    clock.stop()
    # Switching away and back must clear a large old item set safely as well.
    other = w.store.create_run(RunConfig(network="Other session"), [plan[0]], "demo")
    w.store.record(other, 0, Result("timeout", "Separate session"))
    w.active_run = other
    w.reload_results()
    assert w.results.rowCount() == 1 and not w.best_record()
    w.active_run = run
    w.reload_results()
    assert w.results.rowCount() == completed + 2
    with w.store.connect() as conn:
        saved = conn.execute("SELECT result FROM tasks WHERE run_id=? AND seq<? ORDER BY seq", (run, completed)).fetchall()
    assert all(row[0] == value for row in saved), "Completed evidence was overwritten"
    gaps = [b-a for a,b in zip(heartbeats, heartbeats[1:])]
    report = {"total": total, "saved": len(w.records), "resume_s": durations,
              "heartbeats": len(heartbeats), "max_ui_gap_s": max(gaps, default=0)}
    (directory / "resume-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    w.close()
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    large_scenario(Path(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
