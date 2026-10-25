import sys
import threading
import time
from unittest.mock import patch, Mock

import pytest

from windscribe_lab.engine import (Client, Cancelled, ClientError, ClientUnavailable,
                                    ClientTimeout, CLI_ATTEMPT_TIMEOUT, run_process)

STATUS = "Login state: Logged in\nFirewall state: On\nConnect state: Disconnected\nProtocol: WireGuard:443\n"


def client(cancel=None):
    with patch("windscribe_lab.engine.resolve_cli", return_value="synthetic/windscribe-cli.exe"):
        return Client("synthetic", cancel, frontend=Mock(main_window_ready=lambda: True))


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def test_delayed_startup_retries_only_status_and_allows_more_than_six_seconds():
    c, clock, calls = client(), Clock(), []
    def command(args, timeout, cancel):
        calls.append((args[1:], timeout))
        if len(calls) == 1:
            clock.sleep(10)
            return 1, "Aborting: app did not start in time"
        clock.sleep(7)
        return 0, STATUS
    progress = []
    with patch("windscribe_lab.engine.run_process", side_effect=command), patch("windscribe_lab.engine.time.monotonic", clock.monotonic), patch.object(c, "sleep", clock.sleep):
        state = c.wait_ready(report=progress.append)
    assert state.logged_in and state.state == "disconnected"
    assert [args for args, _ in calls] == [["status"], ["status"]]
    assert all(timeout >= 10 for _, timeout in calls)
    assert progress and clock.now > 6


def test_readiness_deadline_is_bounded_and_cancellation_is_immediate():
    clock, c, calls = Clock(), client(), []
    def unavailable(args, timeout, cancel):
        calls.append(timeout)
        clock.sleep(timeout)
        raise ClientTimeout("slow startup")
    with patch("windscribe_lab.engine.run_process", side_effect=unavailable), patch("windscribe_lab.engine.time.monotonic", clock.monotonic), patch.object(c, "sleep", clock.sleep):
        with pytest.raises(ClientUnavailable, match="within 25s"):
            c.wait_ready(timeout=25)
    assert clock.now == 25 and len(calls) < 5
    flag = threading.Event()
    flag.set()
    with patch("windscribe_lab.engine.run_process") as process:
        with pytest.raises(Cancelled):
            client(flag).wait_ready()
    process.assert_not_called()


def test_empty_ipc_response_is_retried_but_permanent_error_is_not():
    c = client()
    with patch("windscribe_lab.engine.run_process", side_effect=[(0, ""), (0, STATUS)]) as process, patch.object(c, "sleep"):
        assert c.wait_ready().logged_in
    assert process.call_count == 2
    with patch("windscribe_lab.engine.run_process", return_value=(1, "No ports available for the specified protocol.")) as process:
        with pytest.raises(ClientError, match="failed"):
            c.read_command("ports", "invalid")
    assert process.call_count == 1


def test_read_retry_rejects_mutations_and_does_not_replay_a_timed_out_connect():
    c = client()
    with pytest.raises(ValueError, match="read-only"):
        c.read_command("connect", "best", "tcp:443")
    connected = STATUS.replace("Disconnected", "Connected: Frankfurt - Castle")
    with patch.object(c, "wait_ready"), patch("windscribe_lab.engine.run_process", side_effect=[ClientTimeout("uncertain ack"), (0, connected)]) as process:
        assert c.connect("Castle", "wireguard", 443, 30).state == "connected"
    assert [call.args[0][1] for call in process.call_args_list] == ["connect", "status"]


def test_port_and_preset_reads_retry_transient_ipc_failure():
    c = client()
    with patch("windscribe_lab.engine.run_process", side_effect=[(1, "Aborting: IPC communication error"), (0, "A - Preset")]) as process, patch.object(c, "sleep"):
        assert c.read_command("amneziawg") == "A - Preset"
    assert process.call_count == 2


def test_output_capture_finishes_when_parent_exits_with_child_still_holding_pipe():
    source = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(4)'],stdout=sys.stdout,stderr=sys.stderr,close_fds=False); "
        "print('CLI READY',flush=True)"
    )
    started = time.monotonic()
    code, output = run_process([sys.executable, "-c", source], timeout=2)
    assert code == 0 and output == "CLI READY"
    assert time.monotonic() - started < 2


def test_a_real_command_can_take_longer_than_the_original_six_second_limit():
    code, output = run_process([sys.executable, "-c", "import time;time.sleep(6.2);print('READY')"], CLI_ATTEMPT_TIMEOUT)
    assert code == 0 and output == "READY"


def test_capture_drains_large_output_without_pipe_deadlock():
    code, output = run_process([sys.executable, "-c", "import sys;sys.stdout.write('x'*200000)"], 3)
    assert code == 0 and len(output) == 200000
