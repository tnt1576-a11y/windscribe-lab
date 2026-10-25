import pytest

from windscribe_lab.models import Location, Route, RunConfig, make_plan, parse_status, route_matches
from windscribe_lab.engine import parse_ping

LOC = Location("1", "Germany", "DE", "Frankfurt", "Sausage Party")
STATUS = """Internet connectivity: available
Login state: Logged in
Firewall state: On
Connect state: Connected: Frankfurt - Sausage Party
Protocol: WireGuard:443
VPN IP: 203.0.113.5
"""


def test_parse_status_uses_fields_not_connected_substring():
    status = parse_status(STATUS)
    assert status.logged_in and status.state == "connected" and status.firewall == "On"
    assert route_matches(status, Route(LOC, "wireguard", 443))
    wrong_port = Route(LOC, "wireguard", 80)
    assert not route_matches(status, wrong_port)


@pytest.mark.parametrize("state", ["Disconnected", "Connecting", "Disconnecting", "*Connected", "Connected: Frankfurt - Sausage Party [Network interference]"])
def test_pending_interfered_or_not_connected_never_pass(state):
    text = STATUS.replace("Connected: Frankfurt - Sausage Party", state)
    assert not route_matches(parse_status(text), Route(LOC, "wireguard", 443))


def test_different_nickname_never_passes():
    assert not route_matches(parse_status(STATUS.replace("Sausage Party", "Castle")), Route(LOC, "wireguard", 443))


def test_matrix_deduplicates_and_records_trials():
    plan = make_plan([LOC, LOC], {"wireguard": [443, 443, 80], "tcp": [443]}, 2)
    assert len(plan) == 6
    assert [route.trial for route in plan] == [1, 1, 1, 2, 2, 2]
    assert Route.from_dict(plan[0].to_dict()) == plan[0]


@pytest.mark.parametrize("locations,protocols", [([], {"tcp": [443]}), ([LOC], {}), ([LOC], {"bad": [443]}), ([LOC], {"tcp": [0]})])
def test_invalid_plans_are_rejected(locations, protocols):
    with pytest.raises(ValueError):
        make_plan(locations, protocols)


def test_config_validates_measurement_and_network_context():
    RunConfig().validate()
    with pytest.raises(ValueError):
        RunConfig(download_mb=100).validate()
    assert RunConfig(network="home").fingerprint != RunConfig(network="mobile").fingerprint


def test_ping_ignores_unreachable_and_aggregate_lines():
    text = """Reply from 1.1.1.1: bytes=32 time=40ms TTL=58
Reply from 192.0.2.1: Destination host unreachable.
Reply from 1.1.1.1: bytes=32 time=60ms TTL=58
Minimum = 40ms, Maximum = 60ms, Average = 50ms
"""
    assert parse_ping(text, 4) == (50.0, 20.0, 50.0)
    assert parse_ping("Request timed out.", 3) == (None, None, 100.0)


def test_ping_less_than_one_and_localized_numbers():
    assert parse_ping("Reply: bytes=32 time<1ms TTL=60\nReply: bytes=32 time=1,5ms TTL=60", 2) == (1.0, 1.0, 0.0)
