from unittest.mock import patch, Mock

import pytest

from windscribe_lab.engine import Client, ClientError, cli_response, parse_ports
from windscribe_lab.models import PROTOCOLS

WARNING = ("spdlog init failed: rotating_file_sink: failed renaming "
           "C:/Synthetic/Windscribe2/cli.log to C:/Synthetic/Windscribe2/cli.1.log: permission denied")


def client():
    with patch("windscribe_lab.engine.resolve_cli", return_value="synthetic/windscribe-cli.exe"):
        return Client("synthetic", frontend=Mock(main_window_ready=lambda: True))


def test_all_protocols_accept_real_format_with_intermittent_stdout_log_warnings():
    replies = ["443, 80, 53, 123, 1194, 65142", "500", "443, 80, 53, 123, 1194, 54783",
               "443, 587, 21, 22, 80, 123, 143, 3306, 8080, 54783, 1194",
               "443, 587, 21, 22, 80, 123, 143, 3306, 8080, 54783, 8443", "443"]
    outputs = [(0, (WARNING + "\r\n" if index % 2 == 0 else "") + reply + "\r\n")
               for index, reply in enumerate(replies)]
    c = client()
    with patch("windscribe_lab.engine.run_process", side_effect=outputs) as process:
        ports = c.ports()
    assert ports["wireguard"] == [443, 80, 53, 123, 1194, 65142]
    assert sum(map(len, ports.values())) == 36
    assert [call.args[0][1:] for call in process.call_args_list] == [["ports", p] for p in PROTOCOLS]


def test_preset_list_and_status_share_the_warning_handling():
    c = client()
    with patch("windscribe_lab.engine.run_process", return_value=(0, WARNING + "\nA - Preset one\nK - Preset eleven\n")):
        assert c.read_command("amneziawg").splitlines() == ["A - Preset one", "K - Preset eleven"]
    with patch("windscribe_lab.engine.run_process", return_value=(0, WARNING + "\nLogin state: Logged in\nConnect state: Disconnected\nFirewall state: On\n")):
        state = c.status()
    assert state.logged_in and state.state == "disconnected" and state.firewall == "On"


def test_a_failed_command_remains_failed_even_with_a_plausible_payload():
    with patch("windscribe_lab.engine.run_process", return_value=(1, WARNING + "\n443, 80")):
        with pytest.raises(ClientError, match="failed"):
            client().read_command("ports", "wireguard")


def test_unknown_diagnostics_are_not_silently_removed_or_mined_for_ports():
    c = client()
    with patch("windscribe_lab.engine.run_process", return_value=(0, "Unknown version 2.24.13 error 443\n443, 80")):
        with pytest.raises(ClientError, match="unexpected wireguard port response"):
            c.ports()
    assert cli_response(WARNING) == ""


@pytest.mark.parametrize("response", ["0, 443", "443, 65536", "443, x", "2.24.13", "443\n80"])
def test_invalid_capabilities_never_pass(response):
    with pytest.raises(ClientError, match="unexpected"):
        parse_ports(response, "wireguard")


def test_valid_single_ports_wrapped_csv_and_duplicates():
    assert parse_ports("500", "ikev2") == [500]
    assert parse_ports("443,\r\n80, 443", "wireguard") == [443, 80]
    assert cli_response("\ufeff" + WARNING + "\r\n443, 80") == "443, 80"
