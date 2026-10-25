import base64
from dataclasses import asdict, replace
import json
import struct

import pytest
from PySide6.QtCore import QByteArray, QDataStream, QIODevice

from windscribe_lab.antischema import (
    AntiCensorship, EngineDocument, KNOWN_PRESETS, SettingsError, checksum, decrypt, encrypt,
)
from windscribe_lab.anticensorship import SettingsController
from windscribe_lab.models import ClientStatus, Location, RunConfig, make_plan, Route


def fixture_stream(version=13, dns=3, interface=2, anti=None, language="en"):
    """Synthetic Qt-produced stream, following the public v2.24.13 schema."""
    anti = anti or AntiCensorship("Enabled", KNOWN_PRESETS[9], "Auto", False)
    buffer = QByteArray()
    s = QDataStream(buffer, QIODevice.OpenModeFlag.WriteOnly)
    integer, boolean, string = s.writeInt32, s.writeBool, s.writeQString
    def connection():
        integer(1); integer(5); s.writeUInt32(443); boolean(False)
    def netinterface():
        integer(interface); integer(-1)
        for text in ("No Interface", "synthetic-guid", "synthetic-network"):
            string(text)
        integer(0); integer(0); boolean(False); string("Synthetic adapter")
        if interface == 1:
            boolean(False)
        integer(100); string("00:00:00:00:00:00"); integer(1470)
        if interface == 1:
            integer(0); integer(0); string(""); boolean(False); boolean(False)
    s.writeUInt32(0x7745C2AE); integer(version); string(language); integer(0)
    if version < 12:
        boolean(False)
    boolean(True); boolean(False)
    integer(1); integer(2); integer(0)  # Firewall
    connection()
    integer(1); boolean(True); string("")
    integer(1); integer(0); string("proxy.test.invalid"); s.writeUInt32(8080)
    string("synthetic-user"); string("synthetic-proxy-value")
    integer(1); boolean(True); integer(1470)
    integer(1); boolean(False); string("00:00:00:00:00:00"); boolean(False)
    netinterface(); s.writeUInt32(1); netinterface()
    integer(2); integer(1); string("C:/synthetic-configs"); boolean(False)
    integer(dns); integer(0); string("https://synthetic.invalid/dns-query")
    if dns >= 2:
        boolean(False); string(""); s.writeUInt32(1); string("synthetic.invalid")
    if dns >= 3:
        string("synthetic-controld-value"); s.writeUInt32(1); string("test-device"); string("https://dns.invalid")
    integer(0); s.writeUInt32(1); string("test-network"); connection()
    boolean(anti.large_tls)
    integer(1); boolean(False); integer(0)
    string(anti.amnezia)
    integer({"Auto": 0, "Regular": 1, "Alternate": 2}[anti.routing])
    integer({"Auto": 0, "Enabled": 1, "Disabled": 2}[anti.protocol_tweaks])
    integer(0)
    if version >= 13:
        string("preserve-sni.test.invalid")
    return bytes(buffer)


def untouched(doc):
    chunks, cursor = [], 0
    for start, end in sorted(doc.spans.values()):
        chunks.append(doc.plain[cursor:start])
        cursor = end
    chunks.append(doc.plain[cursor:])
    return b"".join(chunks)


@pytest.mark.parametrize("version", [11, 12, 13])
@pytest.mark.parametrize("dns", [1, 2, 3])
@pytest.mark.parametrize("interface", [1, 2])
def test_qt_schema_roundtrip_preserves_all_unrelated_bytes(version, dns, interface):
    original = EngineDocument(fixture_stream(version, dns, interface))
    assert original.patch(original.current) == original.plain
    for target in (AntiCensorship("Enabled", KNOWN_PRESETS[10], "Alternate", True),
                   AntiCensorship("Auto", "", "Regular", False), AntiCensorship("Disabled", "", "Auto", True)):
        changed = EngineDocument(original.patch(target))
        assert changed.current == target
        assert untouched(changed) == untouched(original)
        assert EngineDocument(changed.patch(original.current)).current == original.current


def test_integrity_and_compression_are_validated():
    assert checksum(b"123456789") == 0x906E
    plain = fixture_stream()
    cipher = encrypt(plain)
    assert decrypt(cipher) == plain
    damaged = bytearray(base64.b64decode(cipher))
    damaged[-3] ^= 0x10
    with pytest.raises(SettingsError, match="checksum"):
        decrypt(base64.b64encode(damaged).decode())
    with pytest.raises(SettingsError):
        decrypt("not a settings value")


@pytest.mark.parametrize("offset,value", [(0, 0), (4, 99)])
def test_unknown_header_and_schema_are_rejected(offset, value):
    data = bytearray(fixture_stream())
    data[offset:offset + 4] = struct.pack(">I", value)
    with pytest.raises(SettingsError):
        EngineDocument(bytes(data))


def test_truncated_or_extra_settings_never_patch():
    for raw in (fixture_stream()[:-1], fixture_stream() + b"extra"):
        with pytest.raises(SettingsError):
            EngineDocument(raw)


class FakeRegistry:
    def __init__(self, data):
        self.value = encrypt(data)
        self.writes = 0
    def read(self):
        return self.value
    def write(self, value, expected):
        assert expected == self.value
        self.writes += 1
        self.value = value


class FakeHost:
    def __init__(self):
        self.running, self.stops, self.starts = True, 0, 0
        self.on_stop = lambda: None
        self.on_start = lambda: None
    def version(self):
        return (2, 24, 13)
    def pids(self):
        return [101] if self.running else []
    def stop(self):
        self.stops += 1
        self.on_stop()
        self.running = False
    def start(self):
        self.starts += 1
        self.running = True
        self.on_start()


class FakeClient:
    path = "synthetic/windscribe-cli.exe"
    def __init__(self):
        self.disconnects = 0
    def command(self, *args):
        assert args == ("amneziawg",)
        return "\n".join(KNOWN_PRESETS)
    def status(self, timeout=3):
        return ClientStatus("disconnected", logged_in=True)
    def wait_ready(self, timeout=60, report=None, require_login=False):
        return self.status()
    def read_command(self, *args, **kwargs):
        return self.command(*args)
    def disconnect(self):
        self.disconnects += 1


def fake_protector(data, decrypt=False):
    # Test-only reversible stand-in; production always calls Windows DPAPI.
    return bytes(value ^ 0xA5 for value in data)


def controller(tmp_path):
    registry, host, client = FakeRegistry(fixture_stream()), FakeHost(), FakeClient()
    ctrl = SettingsController(client, tmp_path, registry=registry, host=host, protector=fake_protector)
    return ctrl, registry, host, client


def test_apply_restart_verify_and_restore_transaction(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    original = EngineDocument.from_registry(registry.read())
    ctrl.begin({"state": "disconnected"})
    assert ctrl.path.exists() and registry.writes == 0
    target = AntiCensorship("Enabled", KNOWN_PRESETS[10], "Alternate", True)
    assert ctrl.apply(target) == "registry_readback_after_restart"
    assert (host.stops, host.starts, registry.writes) == (1, 1, 1)
    ctrl.verify(target)
    assert untouched(EngineDocument.from_registry(registry.read())) == untouched(original)
    ctrl.restore()
    assert EngineDocument.from_registry(registry.read()).current == original.current
    assert registry.writes == 2 and not ctrl.path.exists()


@pytest.mark.parametrize("mode", ["Auto", "Disabled"])
def test_inactive_manual_preset_is_preserved_and_does_not_block_testing(tmp_path, mode):
    ctrl, registry, host, client = controller(tmp_path)
    original = EngineDocument.from_registry(registry.read()).current
    ctrl.begin({"state": "disconnected"})
    target = AntiCensorship(mode, "", "Alternate", True)
    ctrl.apply(target)
    actual = EngineDocument.from_registry(registry.read()).current
    assert actual == replace(target, amnezia=original.amnezia)
    ctrl.verify(target)
    assert ctrl.apply(target) == "registry_readback_existing"
    assert host.starts == host.stops == registry.writes == 1
    ctrl.restore()
    assert EngineDocument.from_registry(registry.read()).current == original
    assert not ctrl.path.exists()


def normalize_preset_on_start(registry):
    """Windscribe 2.24.13 replaces missing/stale saved presets on GUI startup."""
    doc = EngineDocument.from_registry(registry.read())
    if doc.current.amnezia not in KNOWN_PRESETS:
        registry.value = encrypt(doc.patch(replace(doc.current, amnezia=KNOWN_PRESETS[0])))


@pytest.mark.parametrize("mode", ["Auto", "Disabled"])
@pytest.mark.parametrize("saved_preset", ["", "Old - Removed preset"])
def test_startup_preset_normalization_allows_apply_verify_and_recovery(tmp_path, mode, saved_preset):
    ctrl, registry, host, client = controller(tmp_path)
    original = AntiCensorship(mode, saved_preset, "Auto", False)
    registry.value = encrypt(fixture_stream(anti=original))
    host.on_start = lambda: normalize_preset_on_start(registry)
    ctrl.begin({"state": "disconnected"})
    target = AntiCensorship(mode, "", "Alternate", True)
    ctrl.apply(target)
    actual = EngineDocument.from_registry(registry.read()).current
    assert actual == replace(target, amnezia=KNOWN_PRESETS[0])
    assert ctrl.journal["expected"] == asdict(actual)
    ctrl.verify(target)
    assert not ctrl.recovery_summary()["conflict"]
    ctrl.restore()
    assert EngineDocument.from_registry(registry.read()).current == replace(original, amnezia=KNOWN_PRESETS[0])
    assert not ctrl.path.exists()


def test_legacy_auto_failure_backup_can_restore_without_false_conflict(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    original = EngineDocument.from_registry(registry.read()).current
    ctrl.begin({"state": "disconnected"})
    legacy_target = AntiCensorship("Auto", "", "Alternate", True)
    ctrl.journal.update(expected=asdict(legacy_target), phase="write_pending")
    ctrl._save()
    registry.value = encrypt(fixture_stream(anti=replace(legacy_target, amnezia=KNOWN_PRESETS[0])))
    recovered = SettingsController(client, tmp_path, registry=registry, host=host, protector=fake_protector)
    assert not recovered.recovery_summary()["conflict"]
    recovered.restore()
    assert EngineDocument.from_registry(registry.read()).current == original
    assert not recovered.path.exists()


@pytest.mark.parametrize("change", [dict(protocol_tweaks="Disabled"), dict(routing="Regular"), dict(large_tls=False)])
def test_auto_still_rejects_changes_to_active_settings(tmp_path, change):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({})
    target = AntiCensorship("Auto", "", "Alternate", True)
    ctrl.apply(target)
    actual = EngineDocument.from_registry(registry.read()).current
    registry.value = encrypt(fixture_stream(anti=replace(actual, **change)))
    with pytest.raises(SettingsError, match="changed during measurement"):
        ctrl.verify(target)
    assert ctrl.recovery_summary()["conflict"]
    with pytest.raises(SettingsError, match="outside this test"):
        ctrl.restore()
    assert ctrl.path.exists()


def test_enabled_still_rejects_startup_preset_changes(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({})
    target = AntiCensorship("Enabled", KNOWN_PRESETS[10], "Alternate", True)
    def replace_preset():
        doc = EngineDocument.from_registry(registry.read())
        registry.value = encrypt(doc.patch(replace(doc.current, amnezia=KNOWN_PRESETS[0])))
    host.on_start = replace_preset
    with pytest.raises(SettingsError, match="did not retain"):
        ctrl.apply(target)
    with pytest.raises(SettingsError, match="changed during measurement"):
        ctrl.verify(target)
    assert ctrl.path.exists() and ctrl.recovery_summary()["conflict"]


@pytest.mark.parametrize("change", [dict(protocol_tweaks="Disabled"), dict(routing="Regular"), dict(large_tls=False)])
def test_auto_still_rejects_active_settings_changed_on_startup(tmp_path, change):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({})
    def change_setting():
        doc = EngineDocument.from_registry(registry.read())
        registry.value = encrypt(doc.patch(replace(doc.current, **change)))
    host.on_start = change_setting
    with pytest.raises(SettingsError, match="did not retain"):
        ctrl.apply(AntiCensorship("Auto", "", "Alternate", True))
    assert ctrl.path.exists()


def test_recovery_review_still_detects_an_inactive_preset_changed_after_review(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    registry.value = encrypt(fixture_stream(anti=AntiCensorship("Auto", KNOWN_PRESETS[0], "Auto", False)))
    ctrl.begin({})
    reviewed = EngineDocument.from_registry(registry.read()).current
    registry.value = encrypt(fixture_stream(anti=replace(reviewed, amnezia=KNOWN_PRESETS[1])))
    with pytest.raises(SettingsError, match="after the recovery review"):
        ctrl.keep_current(reviewed)
    with pytest.raises(SettingsError, match="after the recovery review"):
        ctrl.restore(reviewed_current=reviewed)
    assert ctrl.path.exists() and registry.writes == host.stops == 0


def test_auto_run_reaches_measurement_saves_results_and_restores(tmp_path, qapp):
    from unittest.mock import patch
    from windscribe_lab.engine import Runner
    from windscribe_lab.models import Result
    from windscribe_lab.storage import Store

    registry, host = FakeRegistry(fixture_stream()), FakeHost()
    original = EngineDocument.from_registry(registry.read()).current
    host.on_start = lambda: normalize_preset_on_start(registry)
    class RunnerClient(FakeClient):
        def __init__(self, path, cancel=None):
            super().__init__()
        def ports(self):
            return {"tcp": [443, 80]}
        def disconnect(self, timeout=16):
            super().disconnect()
    def settings(client, directory, logger):
        return SettingsController(client, directory, logger, registry=registry, host=host, protector=fake_protector)
    profiles = [asdict(AntiCensorship("Auto", "", "Alternate", True))]
    config = RunConfig(anti_profiles=profiles)
    store = Store(tmp_path)
    plan = make_plan([Location("1", "Germany", "DE", "Frankfurt", "Castle")], {"tcp": [443, 80]}, 1, profiles)
    run = store.create_run(config, plan, "live")
    finished = []
    runner = Runner(store, run)
    runner.finished_run.connect(finished.append)
    with patch("windscribe_lab.engine.Client", RunnerClient), patch("windscribe_lab.engine.SettingsController", settings), patch.object(Runner, "measure", return_value=Result("verified", https_ok=2, tunnel_verified=True)) as measure:
        runner.run()
    assert store.run(run)["state"] == "complete", finished
    assert measure.call_count == 2
    assert all(result and result.applied_anti_censorship == profiles[0] for _, _, result in store.tasks(run))
    assert EngineDocument.from_registry(registry.read()).current == original
    assert not (tmp_path / "settings-recovery.dpapi").exists()


def test_shutdown_saves_and_external_unrelated_edits_are_preserved(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({"state": "disconnected"})
    host.on_stop = lambda: setattr(registry, "value", encrypt(fixture_stream(language="fa")))
    target = AntiCensorship("Disabled", "", "Regular", True)
    ctrl.apply(target)
    after = EngineDocument.from_registry(registry.read())
    assert untouched(after) == untouched(EngineDocument(fixture_stream(language="fa")))
    host.on_stop = lambda: None
    ctrl.restore()
    restored = EngineDocument.from_registry(registry.read())
    assert untouched(restored) == untouched(after)


def test_crash_backup_restores_with_new_controller(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    original = EngineDocument.from_registry(registry.read()).current
    ctrl.begin({"state": "disconnected"})
    ctrl.apply(AntiCensorship("Auto", "", "Alternate", True))
    new = SettingsController(client, tmp_path, registry=registry, host=host, protector=fake_protector)
    assert new.restore() == {"state": "disconnected"}
    assert EngineDocument.from_registry(registry.read()).current == original


def test_external_anti_setting_change_is_never_overwritten(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({"state": "disconnected"})
    external = AntiCensorship("Enabled", KNOWN_PRESETS[0], "Regular", True)
    registry.value = encrypt(fixture_stream(anti=external))
    with pytest.raises(SettingsError, match="outside this test"):
        ctrl.apply(AntiCensorship("Auto", "", "Auto", False))
    assert registry.writes == 0 and host.stops == 0 and ctrl.path.exists()


def test_version_and_backup_failures_prevent_writes(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    host.version = lambda: (2, 25, 0)
    with pytest.raises(SettingsError, match="2.24.13 only"):
        ctrl.begin({})
    assert registry.writes == 0 and host.stops == 0
    host.version = lambda: (2, 24, 13)
    ctrl.protector = lambda data, **kw: b"broken"
    with pytest.raises(SettingsError, match="backup verification"):
        ctrl.begin({})
    assert registry.writes == 0 and host.stops == 0


def test_existing_backup_is_never_replaced(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({})
    original_backup = ctrl.path.read_bytes()
    with pytest.raises(SettingsError, match="backup exists"):
        ctrl.begin({})
    assert ctrl.path.read_bytes() == original_backup and registry.writes == 0


def test_invalid_preset_is_rejected_before_restart(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({})
    with pytest.raises(SettingsError, match="not available"):
        ctrl.apply(AntiCensorship("Enabled", "Z - Unknown", "Regular", True))
    assert registry.writes == 0 and host.stops == 0


def test_settings_matrix_is_grouped_and_serialized():
    profiles = [asdict(AntiCensorship("Enabled", KNOWN_PRESETS[9], "Auto", False)),
                asdict(AntiCensorship("Enabled", KNOWN_PRESETS[9], "Alternate", True))]
    locations = [Location("1", "Germany", "DE", "Frankfurt", "Castle")]
    plan = make_plan(locations, {"tcp": [443, 80]}, 2, profiles)
    assert len(plan) == 8
    assert all(route.anti_censorship == AntiCensorship(**profiles[0]) for route in plan[:4])
    assert Route.from_dict(plan[-1].to_dict()) == plan[-1]
    assert RunConfig(anti_profiles=profiles).anti_profiles == profiles


def test_recovery_review_can_keep_current_without_touching_the_client(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({"state": "disconnected"})
    external = AntiCensorship("Enabled", KNOWN_PRESETS[8], "Auto", False)
    registry.value = encrypt(fixture_stream(anti=external))
    original_file, current_cipher = ctrl.path.read_bytes(), registry.read()
    summary = ctrl.recovery_summary()
    assert summary["conflict"] and summary["current"] == asdict(external)
    archive = ctrl.keep_current(external)
    assert archive.read_bytes() == original_file and not ctrl.path.exists()
    assert registry.read() == current_cipher and registry.writes == 0
    assert client.disconnects == 0 and host.stops == 0


def test_recovery_review_rejects_changes_after_confirmation(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    ctrl.begin({})
    reviewed = EngineDocument.from_registry(registry.read()).current
    registry.value = encrypt(fixture_stream(anti=AntiCensorship("Auto", "", "Regular", True)))
    with pytest.raises(SettingsError, match="after the recovery review"):
        ctrl.keep_current(reviewed)
    with pytest.raises(SettingsError, match="after the recovery review"):
        ctrl.restore(reviewed_current=reviewed)
    assert ctrl.path.exists() and registry.writes == 0 and host.stops == 0


def test_explicitly_reviewed_conflict_can_restore_original_fields(tmp_path):
    ctrl, registry, host, client = controller(tmp_path)
    original = EngineDocument.from_registry(registry.read()).current
    ctrl.begin({})
    external = AntiCensorship("Enabled", KNOWN_PRESETS[8], "Auto", False)
    registry.value = encrypt(fixture_stream(anti=external, language="fa"))
    unaffected = untouched(EngineDocument.from_registry(registry.read()))
    ctrl.restore(reviewed_current=external)
    actual = EngineDocument.from_registry(registry.read())
    assert actual.current == original and untouched(actual) == unaffected
    assert not ctrl.path.exists()
