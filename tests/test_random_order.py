from collections import Counter
from dataclasses import asdict
from itertools import groupby
from pathlib import Path
import random
from unittest.mock import patch

import pytest

from windscribe_lab.antischema import AntiCensorship
from windscribe_lab.engine import Cancelled, Runner
from windscribe_lab.models import Location, Result, RunConfig, make_plan
from windscribe_lab.storage import Store
from windscribe_lab.ui import MainWindow
from test_engine import FakeClient


LOCATIONS = [Location(str(i), "Germany", "DE", "Frankfurt", f"Server {i}") for i in range(8)]


@pytest.mark.parametrize("managed", [False, True])
def test_shuffle_preserves_every_planned_test_and_settings_groups(managed):
    profiles = [asdict(AntiCensorship(routing=route)) for route in ("Auto", "Alternate")] if managed else None
    original = make_plan(LOCATIONS, {"tcp": [443, 80], "wireguard": [443]}, 2, profiles)
    with patch("windscribe_lab.models.random.shuffle", side_effect=random.Random(81).shuffle):
        shuffled = make_plan(LOCATIONS, {"tcp": [443, 80], "wireguard": [443]}, 2, profiles, random_order=True)
    assert shuffled != original
    assert Counter(shuffled) == Counter(original)
    if managed:
        assert [key for key, _ in groupby(shuffled, lambda route: route.anti_censorship)] == [AntiCensorship(**p) for p in profiles]


def test_default_and_legacy_configs_keep_order():
    assert not RunConfig().random_order
    old = asdict(RunConfig())
    old.pop("random_order")
    assert not RunConfig(**old).random_order
    with patch("windscribe_lab.models.random.shuffle", side_effect=AssertionError("Unexpected shuffle")):
        plan = make_plan(LOCATIONS, {"tcp": [443]})
    assert [route.location for route in plan] == LOCATIONS


def test_random_queue_survives_pause_reopen_and_resume_without_retesting(tmp_path, qapp):
    store = Store(tmp_path)
    with patch("windscribe_lab.models.random.shuffle", side_effect=random.Random(53).shuffle):
        plan = make_plan(LOCATIONS, {"tcp": [443, 80]}, random_order=True)
    run = store.create_run(RunConfig(random_order=True), plan, "live")
    visited = []
    def measure(client, route, config):
        if len(visited) == 3:
            raise Cancelled()
        visited.append(route)
        return Result("timeout")
    with patch("windscribe_lab.engine.Client", FakeClient), patch.object(Runner, "measure", side_effect=measure):
        Runner(store, run).run()
    assert store.run(run)["state"] == "paused" and visited == plan[:3]
    reopened = Store(tmp_path)
    assert reopened.run(run)["config"].random_order
    assert [route for _, route, _ in reopened.tasks(run)] == plan
    resumed = []
    def finish(client, route, config):
        resumed.append(route)
        return Result("timeout")
    with patch("windscribe_lab.engine.Client", FakeClient), patch.object(Runner, "measure", side_effect=finish):
        Runner(reopened, run).run()
    assert resumed == plan[3:]
    assert reopened.run(run)["state"] == "complete"
    assert all(result is not None for _, _, result in reopened.tasks(run))


def test_options_checkbox_persists_and_controls_new_run(tmp_path, qapp):
    assets = Path(__file__).resolve().parents[1] / "assets"
    window = MainWindow(tmp_path, assets, demo=True)
    assert not window.random_order.isChecked()
    original = make_plan(window.selected_locations(), window.selected_protocols())
    window.random_order.setChecked(True)
    # A deterministic permutation verifies the checkbox reaches plan creation.
    with patch("windscribe_lab.models.random.shuffle", side_effect=lambda plan: plan.reverse()), patch.object(window, "launch_runner"):
        window.start_run()
    assert window.store.run(window.active_run)["config"].random_order
    assert [route for _, route, _ in window.store.tasks(window.active_run)] == original[::-1]
    window.close()
    reopened = MainWindow(tmp_path, assets, demo=True)
    assert reopened.random_order.isChecked() and reopened.read_config().random_order
    reopened.close()
