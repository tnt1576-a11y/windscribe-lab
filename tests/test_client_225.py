"""Regression transcripts from Desktop-App v2.25.8 CLI help and strings.cpp."""
from dataclasses import asdict
from unittest.mock import patch

import pytest

from windscribe_lab.anticensorship import SettingsController, PRESET_HELP, parse_presets
from windscribe_lab.antischema import AntiCensorship, EngineDocument, KNOWN_PRESETS, SettingsError
from windscribe_lab.engine import ClientError, Discovery
from windscribe_lab.models import Location, Route, parse_status, route_matches, route_identity_matches
from test_anticensorship import controller, fake_protector, untouched
from test_cli_responses import client


LOCATION = Location("test", "USA", "US", "Dallas", "Ranch")
CONNECTED = """Login:       Logged in
Connection:  Connected to Dallas - Ranch
Protocol:    WireGuard:443
IP:          203.0.113.5 (VPN)
Firewall:    Always On
Data usage:  1.00 GiB / Unlimited
Internet:    Available
"""


@pytest.mark.parametrize("protocol,port", [("WireGuard", 443), ("IKEv2", 500), ("UDP", 53),
                                         ("TCP", 80), ("Stealth", 443), ("WStunnel", 443)])
def test_modern_status_matches_exact_route(protocol, port):
    status = parse_status(CONNECTED.replace("WireGuard:443", f"{protocol}:{port}"))
    assert status.logged_in and status.login_state == "logged in"
    assert status.firewall == "Always On" and status.vpn_ip == "203.0.113.5" and not status.public_ip
    assert route_matches(status, Route(LOCATION, protocol.lower(), port))
    assert not route_matches(status, Route(LOCATION, protocol.lower(), port + 1))
    assert not route_matches(status, Route(Location("other", "USA", "US", "Dallas", "Other"), protocol.lower(), port))


@pytest.mark.parametrize("suffix,pending,interference", [
    (" (checking the connection)", True, False),
    (" (network interference detected)", False, True),
])
def test_modern_unverified_connections_keep_route_but_cannot_pass(suffix, pending, interference):
    status = parse_status(CONNECTED.replace("Dallas - Ranch", "Dallas - Ranch" + suffix)
                          .replace("IP:          203.0.113.5 (VPN)\n", ""))
    assert status.pending == pending and status.interference == interference
    assert status.vpn_ip == ""
    assert route_identity_matches(status, Route(LOCATION, "wireguard", 443))
    assert not route_matches(status, Route(LOCATION, "wireguard", 443))


@pytest.mark.parametrize("connection,state,location,error", [
    ("Connecting to Dallas - Ranch", "connecting", "Dallas - Ranch", False),
    ("Connecting", "connecting", "", False),
    ("Connected", "connected", "", False),
    ("Disconnecting", "disconnecting", "", False),
    ("Disconnected", "disconnected", "", False),
    ("Error: Location does not exist or is disabled", "disconnected", "", True),
    ("Disconnected due to reaching WireGuard key limit.", "disconnected", "", True),
])
def test_modern_connection_states(connection, state, location, error):
    status = parse_status(f"Login: Logged in\nConnection: {connection}\nFirewall: On\nIP: 203.0.113.8 (public)")
    assert (status.state, status.location, bool(status.connection_error)) == (state, location, error)
    assert status.public_ip == "203.0.113.8" and status.vpn_ip == ""
    assert not route_matches(status, Route(LOCATION, "wireguard", 443))


@pytest.mark.parametrize("login", ["Logged out", "Logging in", "Error: Session expired", "Unknown error"])
def test_modern_account_states_without_connection(login):
    with patch("windscribe_lab.engine.run_process", return_value=(0, f"Login: {login}\nFirewall: Off")):
        status = client().status()
    assert status.state == "disconnected" and not status.logged_in


@pytest.mark.parametrize("code", [0, 1])
def test_cross_session_error_is_actionable_even_with_exit_zero(code):
    with patch("windscribe_lab.engine.run_process", return_value=(code,
               "Windscribe is already running in another user session on this computer. Quit Windscribe in that session, then try again.")):
        with pytest.raises(ClientError, match="same session"):
            client().status()


@pytest.mark.parametrize("footer", ["", "\n\n" + PRESET_HELP])
def test_presets_accept_both_versions_without_including_help(footer):
    assert parse_presets("\n".join(KNOWN_PRESETS) + footer) == KNOWN_PRESETS
    assert parse_presets(("\n".join(KNOWN_PRESETS) + footer).replace("\n", "\r\n")) == KNOWN_PRESETS


@pytest.mark.parametrize("output", ["", PRESET_HELP, "A - Test\nUnknown diagnostic", "A - Test\n\n" + PRESET_HELP + " changed"])
def test_unknown_preset_responses_are_rejected(output):
    with pytest.raises(SettingsError):
        parse_presets(output)


@pytest.mark.parametrize("version", [(2, 24, 13), (2, 25, 8)])
@pytest.mark.parametrize("mode", ["Auto", "Enabled", "Disabled"])
def test_supported_versions_apply_and_restore_with_fresh_controller(tmp_path, version, mode):
    ctrl, registry, host, cli = controller(tmp_path)
    host.version = lambda: version
    cli.command = lambda *args: "\n".join(KNOWN_PRESETS) + "\n\n" + PRESET_HELP
    original = EngineDocument.from_registry(registry.read())
    ctrl.begin({"state": "disconnected"})
    assert ctrl.journal["version"] == list(version)
    target = AntiCensorship(mode, KNOWN_PRESETS[0] if mode == "Enabled" else "", "Alternate", True)
    ctrl.apply(target)
    changed = EngineDocument.from_registry(registry.read())
    assert target.matches(changed.current) and untouched(changed) == untouched(original)
    if mode != "Enabled":
        assert changed.current.amnezia == original.current.amnezia
    fresh = SettingsController(cli, tmp_path, registry=registry, host=host, protector=fake_protector)
    assert fresh.restore() == {"state": "disconnected"}
    assert EngineDocument.from_registry(registry.read()).plain == original.plain
    assert not fresh.path.exists()


def test_recovery_from_previous_supported_release_uses_current_schema(tmp_path):
    ctrl, registry, host, cli = controller(tmp_path)
    original = EngineDocument.from_registry(registry.read()).plain
    ctrl.begin({})
    ctrl.apply(AntiCensorship("Auto", "", "Alternate", True))
    host.version = lambda: (2, 25, 8)
    fresh = SettingsController(cli, tmp_path, registry=registry, host=host, protector=fake_protector)
    fresh.restore()
    assert EngineDocument.from_registry(registry.read()).plain == original


def test_unknown_update_keeps_recovery_and_does_not_write(tmp_path):
    ctrl, registry, host, cli = controller(tmp_path)
    ctrl.begin({})
    backup = ctrl.path.read_bytes()
    host.version = lambda: (2, 26, 0)
    with pytest.raises(SettingsError, match="not been verified"):
        ctrl.apply(AntiCensorship("Auto", "", "Alternate", True))
    with pytest.raises(SettingsError, match="version changed"):
        ctrl.restore()
    assert ctrl.path.read_bytes() == backup and registry.writes == 0 and host.stops == 0


def test_discovery_reports_actual_supported_version(tmp_path, qapp):
    ctrl, registry, host, cli = controller(tmp_path)
    host.version = lambda: (2, 25, 8)
    cli.ports = lambda: {"wireguard": [443]}
    cli.command = lambda *args: "\n".join(KNOWN_PRESETS) + "\n\n" + PRESET_HELP
    payloads = []
    discovery = Discovery("synthetic")
    discovery.ready.connect(payloads.append)
    with patch("windscribe_lab.engine.Client", return_value=cli), patch("windscribe_lab.engine.SettingsController", return_value=ctrl):
        discovery.run()
    assert payloads[0]["anti_version"] == "2.25.8"
    assert payloads[0]["anti_presets"] == KNOWN_PRESETS
    assert payloads[0]["anti_current"] == asdict(EngineDocument.from_registry(registry.read()).current)
    assert registry.writes == 0 and host.stops == 0
