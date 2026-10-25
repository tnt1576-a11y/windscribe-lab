from pathlib import Path

from windscribe_lab.models import Result
from windscribe_lab.ui import MainWindow


def test_ui_selection_history_filter_and_best_route(tmp_path, qapp):
    w = MainWindow(tmp_path, Path(__file__).resolve().parents[1] / "assets", demo=True)
    assert len(w.selected_locations()) == 3
    assert sum(len(v) for v in w.selected_protocols().values()) == 3
    w.preset_443()
    assert len(w.selected_protocols()) == 4
    w.location_search.setText("Frankfurt")
    assert any(not item.isHidden() for item in w.location_items.values())
    w.select_locations(False)
    assert not w.selected_locations()
    w.select_locations(True)
    assert all(loc.city == "Frankfurt" for loc in w.selected_locations())
    from windscribe_lab.models import make_plan
    cfg = w.read_config()
    w.active_run = w.store.create_run(cfg, make_plan(w.selected_locations(), {"tcp": [443, 80]}), "demo")
    w.store.record(w.active_run, 0, Result("verified", latency_ms=50, loss_pct=0, https_ok=2, tunnel_verified=True))
    w.store.record(w.active_run, 1, Result("timeout"))
    w.reload_results()
    assert w.results.rowCount() == 2
    assert w.best_record()[1].latency_ms == 50
    w.result_filter.setCurrentIndex(3)
    assert sum(not w.results.isRowHidden(row) for row in range(2)) == 1
    w.save_preferences(quiet=True)
    assert (tmp_path / "preferences.json").exists()
    w.close()


def test_anti_matrix_ui_counts_deduplicates_and_persists(tmp_path, qapp):
    import pytest
    from windscribe_lab.antischema import SettingsError
    w = MainWindow(tmp_path, Path(__file__).resolve().parents[1] / "assets", demo=True)
    w.anti_tweaks.setCurrentText("Enabled")
    w.anti_preset.setCurrentIndex(9)
    w.anti_routing.setCurrentText("Alternate")
    w.anti_tls.setCurrentText("On")
    w.add_anti()
    w.add_anti()
    assert len(w.anti_profiles) == 1
    assert w.read_config().anti_profiles[0]["large_tls"]
    w.all_anti()
    assert len(w.anti_profiles) == 78
    assert "702 tests" in w.plan_caption.text()
    w.save_preferences(quiet=True)
    w.close()
    reopened = MainWindow(tmp_path, Path(__file__).resolve().parents[1] / "assets", demo=True)
    assert len(reopened.anti_profiles) == 78 and reopened.anti_enabled.isChecked()
    reopened.clear_anti()
    with pytest.raises(SettingsError):
        reopened.read_config()
    reopened.anti_enabled.setChecked(False)
    reopened.close()


def test_validation_is_inline_and_preserves_the_plan(tmp_path, qapp):
    from unittest.mock import patch
    w = MainWindow(tmp_path, Path(__file__).resolve().parents[1] / "assets", demo=True)
    w.show()
    w.select_locations(False)
    with patch("windscribe_lab.ui.QMessageBox.warning") as popup:
        w.start_run()
    popup.assert_not_called()
    assert w.active_run is None and w.runner is None
    assert w.notice.text() and w.notice.property("tone") == "error"
    assert not w.notice.isHidden()
    assert w.selected_protocols()
    w.close()


def test_filtered_empty_state_returns_to_populated_results(tmp_path, qapp):
    from windscribe_lab.models import make_plan
    w = MainWindow(tmp_path, Path(__file__).resolve().parents[1] / "assets", demo=True)
    w.show()
    assert not w.results.empty_label.isHidden()
    w.active_run = w.store.create_run(w.read_config(), make_plan(w.selected_locations(), {"tcp": [443]}), "demo")
    w.store.record(w.active_run, 0, Result("timeout"))
    w.reload_results()
    assert w.results.empty_label.isHidden()
    w.result_search.setText("does not exist")
    assert not w.results.empty_label.isHidden()
    assert w.results.empty_label.text() == "No matching results"
    w.result_search.clear()
    assert w.results.empty_label.isHidden()
    w.close()


def test_close_waits_for_simulated_cleanup_without_another_prompt(tmp_path, qapp):
    import time
    from unittest.mock import patch
    w = MainWindow(tmp_path, Path(__file__).resolve().parents[1] / "assets", demo=True)
    w.show()
    w.start_run()
    with patch("windscribe_lab.ui.QMessageBox.question") as popup:
        w.close()
        assert w.closing and w.runner.cancel.is_set()
        until = time.monotonic() + 3
        while w.isVisible() and time.monotonic() < until:
            qapp.processEvents()
            time.sleep(.01)
    popup.assert_not_called()
    assert not w.runner.isRunning() and not w.isVisible()
    assert w.store.run(w.active_run)["state"] == "paused"
