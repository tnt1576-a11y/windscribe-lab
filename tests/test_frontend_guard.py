import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from windscribe_lab.anticensorship import WindowsHost
from windscribe_lab.antischema import SettingsError
from windscribe_lab.engine import Client, ClientError, ClientUnavailable, Cancelled
from test_client_readiness import Clock, STATUS


def client(frontend, cancel=None):
    with patch("windscribe_lab.engine.resolve_cli", return_value="synthetic/windscribe-cli.exe"):
        return Client("synthetic", cancel, frontend=frontend)


@pytest.mark.parametrize("args", [("status",), ("ports", "wireguard"), ("connect", "-n", "Castle", "tcp:443"), ("disconnect", "-n")])
def test_no_cli_or_auto_launch_when_window_is_absent(args):
    frontend = Mock(main_window_ready=Mock(return_value=False))
    with patch("windscribe_lab.engine.run_process") as process:
        with pytest.raises(ClientUnavailable, match="no duplicate was launched"):
            client(frontend).command(*args)
    process.assert_not_called()
    frontend.start.assert_not_called()


def test_managed_restart_waits_for_window_before_first_cli_and_auto_signin(tmp_path):
    from test_anticensorship import controller
    ctrl, registry, host, old_client = controller(tmp_path)
    host.main_window_ready = Mock(side_effect=[False] * 8 + [True] * 3)
    clock = Clock()
    ctrl.client = c = client(host)
    progress = []
    ctrl.logger = progress.append
    replies = [(0, "Login state: Logged out\nFirewall state: Off"),
               (0, "Login state: Logging in\nFirewall state: Off"), (0, STATUS)]
    calls = []
    def cli(*args):
        calls.append((clock.now, args[0]))
        return replies.pop(0)
    with patch("windscribe_lab.engine.run_process", side_effect=cli), patch("windscribe_lab.engine.time.monotonic", clock.monotonic), patch.object(c, "sleep", clock.sleep):
        ctrl._ready()
    assert host.starts == 1
    assert len(calls) == 3 and calls[0][0] >= 4
    assert all(args[1:] == ["status"] for _, args in calls)
    assert registry.writes == 0 and progress


def test_window_wait_deadline_and_cancel_never_launch_cli():
    frontend, clock = Mock(main_window_ready=lambda: False), Clock()
    c = client(frontend)
    with patch("windscribe_lab.engine.time.monotonic", clock.monotonic), patch.object(c, "sleep", clock.sleep), patch("windscribe_lab.engine.run_process") as process:
        with pytest.raises(ClientUnavailable, match="within 3s"):
            c.wait_ready(timeout=3)
    assert clock.now == 3
    process.assert_not_called()
    flag = threading.Event()
    flag.set()
    with patch("windscribe_lab.engine.run_process") as process:
        with pytest.raises(Cancelled):
            client(frontend, flag).command("status")
    process.assert_not_called()


def test_frontend_inspection_error_is_reported_without_a_cli():
    c = client(Mock(main_window_ready=Mock(side_effect=SettingsError("Cannot verify frontend path"))))
    with patch("windscribe_lab.engine.run_process") as process:
        with pytest.raises(ClientError, match="Cannot verify frontend path"):
            c.wait_ready()
    process.assert_not_called()


def test_cli_contention_retries_read_only_without_restart():
    frontend = Mock(main_window_ready=lambda: True)
    c = client(frontend)
    with patch("windscribe_lab.engine.run_process", side_effect=[(1, "Windscribe CLI is already running"), (0, STATUS)]) as process, patch.object(c, "sleep"):
        assert c.wait_ready().logged_in
    assert [call.args[0][1:] for call in process.call_args_list] == [["status"], ["status"]]
    frontend.start.assert_not_called()


def fake_user(windows):
    user = Mock()
    def enumerate_windows(callback, parameter):
        for handle in windows:
            if not callback(handle, parameter):
                return False
        return True
    def pid(handle, pointer):
        pointer._obj.value = windows[handle][0]
        return 1
    def title(handle, buffer, length):
        buffer.value = windows[handle][1]
        return len(buffer.value)
    user.EnumWindows.side_effect = enumerate_windows
    user.GetWindowThreadProcessId.side_effect = pid
    user.GetWindowTextW.side_effect = title
    return user


def test_native_probe_requires_selected_pid_and_official_main_window_title():
    host = WindowsHost.__new__(WindowsHost)
    host.pids = Mock(return_value=[101])
    user = fake_user({1: (999, "Windscribe"), 2: (101, "Error"), 3: (101, "WINDSCRIBE")})
    with patch("windscribe_lab.anticensorship.ctypes.WinDLL", return_value=user):
        assert host.main_window_ready()
    user = fake_user({1: (999, "Windscribe"), 2: (101, "Error")})
    with patch("windscribe_lab.anticensorship.ctypes.WinDLL", return_value=user):
        assert not host.main_window_ready()
    for pids in ([], [101, 102]):
        host.pids.return_value = pids
        with patch("windscribe_lab.anticensorship.ctypes.WinDLL") as dll:
            assert not host.main_window_ready()
        dll.assert_not_called()


def test_native_probe_enumeration_failure_stops_without_guessing():
    host = WindowsHost.__new__(WindowsHost)
    host.pids = Mock(return_value=[101])
    user = fake_user({})
    user.EnumWindows.side_effect = None
    user.EnumWindows.return_value = False
    with patch("windscribe_lab.anticensorship.ctypes.WinDLL", return_value=user):
        with pytest.raises(SettingsError, match="no CLI command was sent"):
            host.main_window_ready()


def test_host_launch_is_not_repeated_when_owned_child_has_no_window_yet():
    host = WindowsHost.__new__(WindowsHost)
    host.exe, host._started_process = Path("synthetic/Windscribe.exe"), None
    host.pids = Mock(return_value=[])
    process = Mock(poll=Mock(return_value=None))
    with patch("windscribe_lab.anticensorship.subprocess.Popen", return_value=process) as launch:
        host.start()
        host.start()
    assert launch.call_count == 1
