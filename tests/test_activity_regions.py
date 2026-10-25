from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt

from windscribe_lab.activity import RunActivity
from windscribe_lab.catalog import parse_catalog
from windscribe_lab.models import Location, Result, make_plan
from windscribe_lab.regions import CONTINENTS, continent_for
from windscribe_lab.ui import MainWindow

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def test_continent_toggle_includes_hidden_members_and_preserves_other_regions(tmp_path, qapp):
    w = MainWindow(tmp_path, ASSETS, demo=True)
    w.select_locations(False)
    asia = w.continent_items["Asia"]
    asia.setCheckState(0, Qt.CheckState.Checked)
    asian_ids = {loc.id for loc in w.locations if continent_for(loc.code) == "Asia"}
    assert {loc.id for loc in w.selected_locations()} == asian_ids
    w.location_search.setText("Frankfurt")
    europe = w.continent_items["Europe"]
    europe.setCheckState(0, Qt.CheckState.Checked)
    european_ids = {loc.id for loc in w.locations if continent_for(loc.code) == "Europe"}
    assert {loc.id for loc in w.selected_locations()} == asian_ids | european_ids
    assert europe.checkState(0) == Qt.CheckState.Checked
    europe.setCheckState(0, Qt.CheckState.Unchecked)
    assert {loc.id for loc in w.selected_locations()} == asian_ids
    # "Select visible" remains a filtered operation, unlike the continent checkbox.
    w.select_locations(True)
    assert all(continent_for(loc.code) == "Asia" or loc.city == "Frankfurt" for loc in w.selected_locations())
    assert europe.checkState(0) == Qt.CheckState.PartiallyChecked
    assert "Asia" in asia.text(0)
    w.close()


def test_continent_search_and_empty_selection_survive_save_and_catalog_rebuild(tmp_path, qapp):
    w = MainWindow(tmp_path, ASSETS, demo=True)
    w.location_search.setText("South America")
    assert not w.continent_items["South America"].isHidden()
    assert w.continent_items["Europe"].isHidden()
    w.select_locations(False)
    w.select_locations(True)
    assert w.selected_locations()
    assert all(continent_for(loc.code) == "South America" for loc in w.selected_locations())
    w.select_locations(False)
    w.populate_locations([])
    assert not w.selected_locations()
    w.save_preferences(quiet=True)
    w.close()
    reopened = MainWindow(tmp_path, ASSETS, demo=True)
    assert not reopened.selected_locations()
    reopened.close()


def test_country_checkbox_updates_continent_and_plan(tmp_path, qapp):
    w = MainWindow(tmp_path, ASSETS, demo=True)
    w.select_locations(False)
    german = next(loc for loc in w.locations if loc.code == "DE")
    country = w.location_items[german.id].parent()
    country.setCheckState(0, Qt.CheckState.Checked)
    assert {loc.id for loc in w.selected_locations()} == {loc.id for loc in w.locations if loc.code == "DE"}
    assert w.continent_items["Europe"].checkState(0) == Qt.CheckState.PartiallyChecked
    assert f"{len(w.selected_locations())} selected" == w.location_count.text()
    w.close()


def test_unknown_country_stays_available_and_existing_catalog_format_is_compatible(tmp_path, qapp):
    w = MainWindow(tmp_path, ASSETS, demo=True)
    unknown = Location("future", "New region", "XX", "Future city", "New server")
    w.locations.append(unknown)
    w.populate_locations([unknown.id])
    assert "Other" in w.continent_items
    assert unknown in w.selected_locations()
    w.close()
    codes = [code for value in CONTINENTS.values() for code in value.split()]
    assert len(codes) == len(set(codes))
    import json
    locations = parse_catalog(json.loads((ASSETS / "server_catalog.json").read_text(encoding="utf-8")))
    assert all(continent_for(loc.code) != "Other" for loc in locations)


def test_worker_events_render_stages_time_pause_cleanup_and_error_without_stale_route(tmp_path, qapp):
    w = MainWindow(tmp_path, ASSETS, demo=True)
    w.show()
    plan = make_plan(w.selected_locations(), {"tcp": [443]})
    w.active_run = w.store.create_run(w.read_config(), plan, "demo")
    route = plan[0]
    w.run_activity.begin()
    w.runner_update(dict(phase="Preparing", message="Preparing", route=route, completed=0, total=len(plan)))
    w.runner_update(dict(phase="Disconnect", message="Disconnect", route=route))
    w.runner_update(dict(phase="Connect", message="Connecting", route=route, budget=30))
    a = w.run_activity
    assert a.states == ["done", "active", "pending", "pending", "pending"]
    assert a.sequence.text() == f"Test 1 / {len(plan)}"
    with patch("windscribe_lab.activity.time.monotonic", return_value=a.started + 9):
        a.tick()
    assert a.clock.text() == "9s elapsed  ·  30s timeout"
    assert a.indicator.timer.isActive()
    a.pause()
    assert "Pausing" in a.title.text()
    w.runner_update(dict(phase="Restore", message="Restore"))
    assert a.rail.isHidden() and a.title.text() == "Restoring original connection"
    assert route.location.nickname not in a.target.text()
    w.runner_done(dict(state="error", cleanup_error="Original connection could not be verified.", error=""))
    assert a.indicator.tone == "error"
    assert not a.timer.isActive() and not a.indicator.timer.isActive()
    assert a.clock.text() == ""
    assert "remaining" in a.target.text() and route.location.nickname not in a.target.text()
    assert a.detail.text() == "Original connection could not be verified."
    w.close()


def test_early_connection_failure_never_marks_untested_stages_done_and_hidden_animation_stops(qapp):
    a = RunActivity()
    a.show()
    a.begin()
    a.set_phase("Disconnect")
    a.set_phase("Connect", budget=25)
    a.saved(Result("timeout"), "Timed out")
    assert a.states == ["done", "error", "pending", "pending", "pending"]
    assert not a.indicator.timer.isActive()
    a.set_phase("Preparing")
    assert a.states == ["pending"] * 5
    a.set_phase("Connect")
    assert a.indicator.timer.isActive()
    a.hide()
    assert not a.indicator.timer.isActive() and not a.timer.isActive()
    a.show()
    assert a.indicator.timer.isActive() and a.timer.isActive()
    a.finish("paused", saved=1, remaining=2)
    assert a.title.text() == "Session paused" and a.rail.isHidden()
    assert a.target.text() == "1 saved  ·  2 remaining"
    a.target.setText("Amsterdam · Stroopwafel / WireGuard : 443")
    a.finish("connected", saved=1, remaining=2)
    assert a.target.text() == "Amsterdam · Stroopwafel / WireGuard : 443"
    a.close()
