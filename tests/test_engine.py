import threading
from unittest.mock import patch

import pytest

from windscribe_lab.engine import Cancelled, ClientError, Runner, check_https, run_process
from windscribe_lab.models import ClientStatus, Location, Result, Route, RunConfig
from windscribe_lab.storage import Store
from windscribe_lab.antischema import AntiCensorship, KNOWN_PRESETS, SettingsError

LOC = Location("1", "Germany", "DE", "Frankfurt", "Castle")


def session(tmp_path, mode="live", config=None):
    store = Store(tmp_path)
    run = store.create_run(config or RunConfig(), [Route(LOC, "tcp", 443), Route(LOC, "tcp", 80)], mode)
    return store, run


class FakeClient:
    instances = []
    advertised = [443, 80]
    disconnected = 0
    connections = []

    def __init__(self, path, cancel=None):
        self.path, self.cancel = path, cancel
        FakeClient.instances.append(self)

    def status(self):
        return ClientStatus("connected", "London - Crumpets", "wireguard", 443, True, "On", "203.0.113.4")

    def wait_ready(self, timeout=60, report=None):
        return self.status()

    def ports(self):
        return {"tcp": self.advertised}

    def disconnect(self, timeout=16):
        FakeClient.disconnected += 1

    def connect(self, location, protocol, port, timeout):
        FakeClient.connections.append((location, protocol, port))
        return self.status()


@pytest.fixture(autouse=True)
def reset_client():
    FakeClient.instances, FakeClient.connections = [], []
    FakeClient.disconnected = 0
    FakeClient.advertised = [443, 80]


def test_preflight_invalid_port_cannot_touch_connection(tmp_path, qapp):
    store, run = session(tmp_path)
    FakeClient.advertised = [443]
    with patch("windscribe_lab.engine.Client", FakeClient):
        Runner(store, run).run()
    assert store.run(run)["state"] == "error"
    assert FakeClient.disconnected == 0
    assert all(result is None for _, _, result in store.tasks(run))


def test_completed_tests_saved_original_route_restored_without_storing_ip(tmp_path, qapp):
    store, run = session(tmp_path)
    with patch("windscribe_lab.engine.Client", FakeClient), patch.object(Runner, "measure", return_value=Result("verified", https_ok=2, tunnel_verified=True)):
        Runner(store, run).run()
    assert store.run(run)["state"] == "complete"
    assert all(result.working for _, _, result in store.tasks(run))
    assert FakeClient.connections == [("Crumpets", "wireguard", 443)]
    assert store.run(run)["original"]["vpn_ip"] == ""


def test_pause_during_measurement_keeps_task_pending_and_restores(tmp_path, qapp):
    store, run = session(tmp_path)
    with patch("windscribe_lab.engine.Client", FakeClient), patch.object(Runner, "measure", side_effect=Cancelled):
        Runner(store, run).run()
    assert store.run(run)["state"] == "paused"
    assert all(result is None for _, _, result in store.tasks(run))
    assert FakeClient.connections == [("Crumpets", "wireguard", 443)]


def test_stop_on_success_leaves_tunnel_and_remaining_queue(tmp_path, qapp):
    store, run = session(tmp_path, config=RunConfig(stop_on_success=True))
    with patch("windscribe_lab.engine.Client", FakeClient), patch.object(Runner, "measure", return_value=Result("verified", https_ok=2, tunnel_verified=True)):
        Runner(store, run).run()
    assert store.run(run)["state"] == "connected"
    assert store.tasks(run)[0][2].working and store.tasks(run)[1][2] is None
    assert FakeClient.disconnected == 0


def test_firewall_state_change_is_reported_after_results_saved(tmp_path, qapp):
    class ChangedFirewallClient(FakeClient):
        def status(self):
            state = super().status()
            if self.cancel is None:
                state.firewall = "Off"
            return state

    store, run = session(tmp_path)
    worker = Runner(store, run)
    finished = []
    worker.finished_run.connect(finished.append)
    with patch("windscribe_lab.engine.Client", ChangedFirewallClient), patch.object(Runner, "measure", return_value=Result("verified", https_ok=2, tunnel_verified=True)):
        worker.run()
    assert "firewall" in finished[0]["cleanup_error"]
    assert all(result.working for _, _, result in store.tasks(run))


def test_demo_never_constructs_client_or_probes_network(tmp_path, qapp):
    store, run = session(tmp_path, "demo")
    with patch("windscribe_lab.engine.Client", side_effect=AssertionError("CLI called")), patch("windscribe_lab.engine.https_get", side_effect=AssertionError("Network called")):
        Runner(store, run).run()
    assert store.run(run)["state"] == "complete"
    assert len([r for _, _, r in store.tasks(run) if r]) == 2


@pytest.mark.parametrize("expected,observed,verified", [("203.0.113.1", "203.0.113.1", True), ("203.0.113.1", "203.0.113.2", False), ("", "203.0.113.1", False)])
def test_https_egress_match_is_required(expected, observed, verified):
    def response(host, path, limit, cancel):
        return ((f"ip={observed}\n" if host == "www.cloudflare.com" else '{"ip":"' + observed + '"}').encode(), .1)
    with patch("windscribe_lab.engine.https_get", side_effect=response):
        ok, actual, _ = check_https(threading.Event(), expected)
    assert ok == 2 and actual == verified


def test_partial_https_is_not_verified():
    with patch("windscribe_lab.engine.https_get", side_effect=[(b"ip=203.0.113.1\n", .1), OSError("blocked")]):
        ok, verified, detail = check_https(threading.Event(), "203.0.113.1")
    assert ok == 1 and not verified and "api.ipify.org" in detail


def test_subprocess_timeout_and_cancel_are_bounded():
    import sys
    with pytest.raises(ClientError, match="did not respond"):
        run_process([sys.executable, "-c", "import time;time.sleep(30)"], .2)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        run_process([sys.executable, "-c", "import time;time.sleep(30)"], 5, cancel)


class FakeSettingsController:
    instances = []
    fail_begin = False
    def __init__(self, *args):
        self.presets = KNOWN_PRESETS
        self.applied, self.restored = [], 0
        self.__class__.instances.append(self)
    def inspect(self):
        return None
    def begin(self, original):
        if self.fail_begin:
            raise SettingsError("Existing recovery backup")
    def apply(self, profile):
        self.applied.append(profile)
        return "registry_readback_after_restart"
    def verify(self, profile):
        assert self.applied[-1] == profile
    def restore(self, clear=True):
        self.restored += 1
    def clear_recovery(self):
        self.cleared = True


def managed_session(tmp_path, stop=False, mode="live"):
    from dataclasses import asdict
    from windscribe_lab.models import make_plan
    store = Store(tmp_path)
    profiles = [AntiCensorship("Enabled", KNOWN_PRESETS[9], "Auto", False),
                AntiCensorship("Enabled", KNOWN_PRESETS[9], "Alternate", True)]
    cfg = RunConfig(stop_on_success=stop, anti_profiles=[asdict(p) for p in profiles])
    run = store.create_run(cfg, make_plan([LOC], {"tcp": [443, 80]}, anti_profiles=cfg.anti_profiles), mode)
    return store, run, profiles


def test_runner_groups_profiles_and_records_verified_settings(tmp_path, qapp):
    store, run, profiles = managed_session(tmp_path)
    FakeSettingsController.instances, FakeSettingsController.fail_begin = [], False
    with patch("windscribe_lab.engine.Client", FakeClient), patch("windscribe_lab.engine.SettingsController", FakeSettingsController), patch.object(Runner, "measure", side_effect=lambda *args: Result("verified", https_ok=2, tunnel_verified=True)):
        Runner(store, run).run()
    controller = FakeSettingsController.instances[0]
    assert controller.applied == profiles and controller.restored == 1
    assert all(r.settings_verification == "registry_readback_after_restart" and r.applied_anti_censorship for _, _, r in store.tasks(run))


def test_managed_pause_restores_settings_and_leaves_interrupted_route_pending(tmp_path, qapp):
    store, run, profiles = managed_session(tmp_path)
    FakeSettingsController.instances, FakeSettingsController.fail_begin = [], False
    with patch("windscribe_lab.engine.Client", FakeClient), patch("windscribe_lab.engine.SettingsController", FakeSettingsController), patch.object(Runner, "measure", side_effect=Cancelled):
        Runner(store, run).run()
    assert FakeSettingsController.instances[0].restored == 1
    assert store.run(run)["state"] == "paused"
    assert all(r is None for _, _, r in store.tasks(run))


def test_existing_recovery_does_not_trigger_unrequested_restore(tmp_path, qapp):
    store, run, profiles = managed_session(tmp_path)
    FakeSettingsController.instances, FakeSettingsController.fail_begin = [], True
    with patch("windscribe_lab.engine.Client", FakeClient), patch("windscribe_lab.engine.SettingsController", FakeSettingsController):
        Runner(store, run).run()
    assert FakeSettingsController.instances[0].restored == 0
    assert FakeClient.disconnected == 0 and store.run(run)["state"] == "error"
    FakeSettingsController.fail_begin = False


def test_managed_stop_on_success_retains_working_settings(tmp_path, qapp):
    store, run, profiles = managed_session(tmp_path, stop=True)
    FakeSettingsController.instances, FakeSettingsController.fail_begin = [], False
    worker = Runner(store, run)
    done = []
    worker.finished_run.connect(done.append)
    with patch("windscribe_lab.engine.Client", FakeClient), patch("windscribe_lab.engine.SettingsController", FakeSettingsController), patch.object(Runner, "measure", return_value=Result("verified", https_ok=2, tunnel_verified=True)):
        worker.run()
    assert FakeSettingsController.instances[0].restored == 0
    assert done[0]["settings_retained"] and store.run(run)["state"] == "connected"


def test_managed_demo_never_reads_or_writes_windscribe_settings(tmp_path, qapp):
    store, run, profiles = managed_session(tmp_path, mode="demo")
    with patch("windscribe_lab.engine.Client", side_effect=AssertionError("CLI called")), patch("windscribe_lab.engine.SettingsController", side_effect=AssertionError("Settings accessed")):
        Runner(store, run).run()
    assert all(r.settings_verification == "simulated" for _, _, r in store.tasks(run))
