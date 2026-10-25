from __future__ import annotations

import http.client
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import statistics
import subprocess
import threading
import time
from dataclasses import asdict

from PySide6.QtCore import QThread, Signal

from .models import (ClientStatus, PROTOCOLS, Result, Route, RunConfig, demo_result,
                     parse_status, route_matches, route_identity_matches)
from .storage import Store
from .antischema import AntiCensorship, SettingsError
from .anticensorship import SettingsController, WindowsHost
from .clienterrors import ClientError, ClientUnavailable, ClientTimeout

CLI_ATTEMPT_TIMEOUT = 12  # Windscribe's own GUI IPC connection deadline is 10s.
CLIENT_STARTUP_TIMEOUT = 60
CLIENT_READ_TIMEOUT = 30


class Cancelled(Exception):
    pass


class ConnectionRejected(ClientError):
    pass


def cli_response(output: str) -> str:
    """Remove only Windscribe's known, non-fatal logger-init diagnostic.

    The official logger prints this to stdout before the command payload when
    its log cannot rotate. Never extract arbitrary numbers from diagnostic text.
    This is applied only after a successful command exit.
    """
    lines = output.lstrip("\ufeff").splitlines()
    return "\n".join(line for line in lines if not line.startswith("spdlog init failed:")).strip()


def parse_ports(output: str, protocol: str) -> list[int]:
    error = f"Windscribe returned an unexpected {protocol} port response. Check the client in Settings."
    if not re.fullmatch(r"\s*[0-9]+(?:\s*,\s*[0-9]+)*\s*", output):
        raise ClientError(error)
    try:
        ports = [int(value) for value in output.split(",")]
    except ValueError as exc:
        raise ClientError(error) from exc
    if any(not 1 <= port <= 65535 for port in ports):
        raise ClientError(error)
    return list(dict.fromkeys(ports))


def run_process(args: list[str], timeout: float, cancel: threading.Event | None = None) -> tuple[int, str]:
    """Capture until the command exits, without waiting on inherited GUI pipes.

    Windscribe can launch its GUI as a child. communicate() waits for pipe EOF,
    which can outlive the CLI if that GUI inherits its standard output handle.
    Drain available bytes and use the CLI process exit as completion instead.
    """
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    except OSError as exc:
        raise ClientError(f"Cannot start {Path(args[0]).name}: {exc.strerror}") from exc
    deadline = time.monotonic() + timeout
    chunks, captured = [], 0
    fd = process.stdout.fileno()
    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes
        pipe_handle = msvcrt.get_osfhandle(fd)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel.PeekNamedPipe.restype = wintypes.BOOL
        def available():
            count = wintypes.DWORD()
            if not kernel.PeekNamedPipe(pipe_handle, None, 0, None, ctypes.byref(count), None):
                if ctypes.get_last_error() in (109, 233):  # Broken / disconnected pipe.
                    return 0
                raise ClientError("Cannot read command output pipe.")
            return count.value
    else:
        import select
        os.set_blocking(fd, False)
        def available():
            return 65536 if select.select([fd], [], [], 0)[0] else 0

    def drain():
        nonlocal captured
        while True:
            count = available()
            if not count:
                return
            try:
                data = os.read(fd, min(count, 65536))
            except BlockingIOError:
                return
            if not data:
                return
            captured += len(data)
            if captured > 1_000_000:
                raise ClientError("Command output exceeded the expected size.")
            chunks.append(data)
    try:
        while True:
            if cancel and cancel.is_set():
                raise Cancelled()
            drain()
            if process.poll() is not None:
                drain()
                return process.returncode, b"".join(chunks).decode("utf-8", errors="replace").strip()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ClientTimeout(f"{Path(args[0]).name} did not respond within {timeout:g}s.")
            if cancel:
                cancel.wait(min(.05, remaining))
            else:
                time.sleep(min(.05, remaining))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()


def resolve_cli(path: str) -> str:
    resolved = shutil.which(path.strip().strip('"'))
    if not resolved or Path(resolved).name.lower() not in ("windscribe-cli.exe", "windscribe-cli"):
        raise ClientError("Choose the official windscribe-cli executable from your Windscribe installation.")
    return resolved


class Client:
    def __init__(self, path: str, cancel: threading.Event | None = None, *, frontend=None):
        self.path = resolve_cli(path)
        self.cancel = cancel
        try:
            self.frontend = frontend if frontend is not None else (WindowsHost(self.path) if os.name == "nt" else None)
        except SettingsError as exc:
            raise ClientError(str(exc)) from exc

    def command(self, *args: str, timeout=CLI_ATTEMPT_TIMEOUT) -> str:
        if self.cancel and self.cancel.is_set():
            raise Cancelled()
        try:
            if self.frontend is not None and not self.frontend.main_window_ready():
                raise ClientUnavailable("Windscribe's main window is not ready. Open Windscribe if closed; no duplicate was launched.")
        except SettingsError as exc:
            raise ClientError(str(exc)) from exc
        try:
            code, out = run_process([self.path, *args], timeout, self.cancel)
        except ClientTimeout as exc:
            raise ClientTimeout(f"Windscribe command '{' '.join(args)}' did not respond within {timeout:g}s.") from exc
        if code:
            if any(message in out.casefold() for message in (
                "app did not start in time", "ipc communication error", "could not start application",
                "windscribe cli is already running",
            )):
                raise ClientUnavailable(f"Windscribe '{args[0]}' is waiting for the desktop client / IPC service.")
            # CLI account output stays in memory, never in persistent logs or reports.
            error = ConnectionRejected if args[0] == "connect" else ClientError
            raise error(f"Windscribe '{args[0]}' failed (exit {code}). Open the official client to check its state.")
        return cli_response(out)

    def status(self, timeout=CLI_ATTEMPT_TIMEOUT) -> ClientStatus:
        output = self.command("status", timeout=timeout)
        if not output:
            raise ClientUnavailable("Windscribe 'status' returned before its desktop state was available.")
        state = parse_status(output)
        if state.state == "unknown":
            raise ClientError("Unrecognized Windscribe status. Open Windscribe, sign in, and retry diagnostics.")
        return state

    def _retry_read(self, action, *, timeout, purpose, report=None):
        deadline = time.monotonic() + timeout
        last = None
        while True:
            if self.cancel and self.cancel.is_set():
                raise Cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ClientUnavailable(f"Windscribe {purpose} was not ready within {timeout:g}s. "
                                        "Open Windscribe and wait for startup/sign-in to finish, then retry.") from last
            try:
                value = action(min(CLI_ATTEMPT_TIMEOUT, remaining))
                if self.cancel and self.cancel.is_set():
                    raise Cancelled()
                return value
            except ClientUnavailable as exc:
                last = exc
                if report:
                    report(f"Waiting for Windscribe {purpose} ({max(0, timeout - (deadline - time.monotonic())):.0f}/{timeout:g}s)…")
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    self.sleep(min(.5, remaining))

    def wait_ready(self, timeout=CLIENT_STARTUP_TIMEOUT, report=None, require_login=False) -> ClientStatus:
        """Only status is retried; a mutating command is never blindly replayed."""
        def read(budget):
            state = self.status(timeout=budget)
            if state.login_state == "logging in" or (require_login and not state.logged_in):
                raise ClientUnavailable("Waiting for Windscribe to restore its signed-in session.")
            return state
        return self._retry_read(read, timeout=timeout,
                                purpose="desktop / CLI", report=report)

    def read_command(self, *args: str, timeout=CLIENT_READ_TIMEOUT, report=None):
        if not args or args[0] not in ("status", "ports", "amneziawg"):
            raise ValueError("Only read-only client requests may be retried.")
        def read(budget):
            output = self.command(*args, timeout=budget)
            if not output:
                raise ClientUnavailable(f"Windscribe '{args[0]}' data is not ready yet.")
            return output
        return self._retry_read(read, timeout=timeout, purpose=f"'{args[0]}' data", report=report)

    def ports(self) -> dict[str, list[int]]:
        result = {}
        for protocol in PROTOCOLS:
            out = self.read_command("ports", protocol)
            result[protocol] = parse_ports(out, protocol)
        return result

    def disconnect(self, timeout=30):
        self.wait_ready()
        deadline = time.monotonic() + timeout
        try:
            self.command("disconnect", "-n", timeout=min(CLI_ATTEMPT_TIMEOUT, timeout))
        except ClientUnavailable:
            pass  # The request may be accepted; reconcile state without replaying it.
        while time.monotonic() < deadline:
            try:
                if self.status(timeout=max(.1, min(4, deadline - time.monotonic()))).state == "disconnected":
                    return
            except ClientUnavailable:
                pass
            self.sleep(.4)
        raise ClientError("Windscribe could not disconnect cleanly. Testing stopped; check the official app.")

    def sleep(self, seconds):
        if self.cancel:
            if self.cancel.wait(seconds):
                raise Cancelled()
        else:
            time.sleep(seconds)

    def connect(self, location: str, protocol: str, port: int, timeout: int) -> ClientStatus:
        self.wait_ready()
        deadline = time.monotonic() + timeout
        try:
            self.command("connect", "-n", location, f"{protocol}:{port}", timeout=min(CLI_ATTEMPT_TIMEOUT, timeout))
        except ClientUnavailable:
            pass  # Poll the accepted operation; never send a second connect request.
        while time.monotonic() < deadline:
            try:
                state = self.status(timeout=max(.1, min(4, deadline - time.monotonic())))
            except ClientUnavailable:
                self.sleep(min(.5, max(0, deadline - time.monotonic())))
                continue
            if state.state == "connected" or state.connection_error:
                return state
            self.sleep(.5)
        raise TimeoutError("Connection deadline reached before Windscribe completed tunnel validation.")


def parse_ping(output: str, sent: int) -> tuple[float | None, float | None, float]:
    # Read per-reply RTTs only; destination-unreachable replies must not count.
    samples = []
    for line in output.splitlines():
        if "ttl=" not in line.lower():
            continue
        match = re.search(r"([=<])\s*(\d+(?:[.,]\d+)?)\s*ms", line, re.I)
        if match:
            samples.append(.5 if match[1] == "<" else float(match[2].replace(",", ".")))
    samples = samples[:sent]
    jitter = statistics.mean(abs(b - a) for a, b in zip(samples, samples[1:])) if len(samples) > 1 else None
    return (round(statistics.mean(samples), 1) if samples else None,
            round(jitter, 1) if jitter is not None else None,
            round((sent - len(samples)) / sent * 100, 1))


def ping(count: int, cancel: threading.Event):
    args = (["ping", "-4", "-n", str(count), "-w", "1500", "1.1.1.1"] if os.name == "nt"
            else ["ping", "-n", "-c", str(count), "-W", "2", "1.1.1.1"])
    try:
        _, output = run_process(args, count * 3 + 3, cancel)
        return parse_ping(output, count)
    except ClientError:
        return None, None, 100.0


def https_get(host: str, path: str, limit: int, cancel: threading.Event) -> tuple[bytes, float]:
    if cancel.is_set():
        raise Cancelled()
    start = time.monotonic()
    conn = http.client.HTTPSConnection(host, timeout=5, context=ssl.create_default_context())
    try:
        conn.request("GET", path, headers={"User-Agent": "WindscribeLab/1.1", "Cache-Control": "no-cache",
                                           "Accept-Encoding": "identity"})
        response = conn.getresponse()
        if response.status != 200:
            raise OSError(f"HTTPS returned {response.status}")
        chunks, length = [], 0
        deadline = start + 20
        while length < limit:
            if cancel.is_set():
                raise Cancelled()
            if time.monotonic() > deadline:
                raise OSError("HTTPS sample deadline reached")
            chunk = response.read(min(65536, limit - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        return b"".join(chunks), time.monotonic() - start
    finally:
        conn.close()


def check_https(cancel: threading.Event, expected_ip: str) -> tuple[int, bool, str]:
    matches, successes, errors = [], 0, []
    for host, path, parser in (
        ("www.cloudflare.com", "/cdn-cgi/trace", lambda text: dict(line.split("=", 1) for line in text.splitlines() if "=" in line)["ip"]),
        ("api.ipify.org", "/?format=json", lambda text: json.loads(text)["ip"]),
    ):
        try:
            raw, _ = https_get(host, path, 65536, cancel)
            observed = ipaddress.ip_address(parser(raw.decode("utf-8")))
            successes += 1
            matches.append(bool(expected_ip) and observed == ipaddress.ip_address(expected_ip))
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            errors.append(host)
    verified = successes == 2 and all(matches)
    if verified:
        detail = "Both HTTPS checks match the VPN IP reported by Windscribe."
    elif successes == 2:
        detail = "HTTPS works; VPN egress is unconfirmed. Check split tunneling or client IP reporting."
    else:
        detail = f"{successes}/2 HTTPS checks passed. Unavailable: {', '.join(errors)}."
    return successes, verified, detail


class Discovery(QThread):
    ready = Signal(dict)
    progress = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.cancel = threading.Event()

    def run(self):
        try:
            client = Client(self.path, self.cancel)
            status = client.wait_ready(report=self.progress.emit)
            ports = client.ports()
            payload = {"status": status, "ports": ports, "path": client.path}
            try:
                controller = SettingsController(client, Path("."))
                document = controller.inspect()
                payload.update(anti_current=asdict(document.current), anti_presets=controller.presets)
            except Exception as exc:
                payload["anti_error"] = str(exc)
            self.ready.emit(payload)
        except (ClientError, Cancelled) as exc:
            self.ready.emit({"error": str(exc) or "Diagnostics cancelled."})


class Runner(QThread):
    update = Signal(dict)
    finished_run = Signal(dict)

    def __init__(self, store: Store, run_id: str, manual=False):
        super().__init__()
        self.store, self.run_id, self.manual = store, run_id, manual
        self.cancel = threading.Event()

    def emit(self, phase: str, message: str, **kwargs):
        self.update.emit({"phase": phase, "message": message, **kwargs})

    def measure(self, client: Client, route: Route, config: RunConfig) -> Result:
        self.emit("Disconnect", "Preparing a clean connection", route=route)
        client.disconnect()
        self.emit("Connect", f"Connecting to {route.display}", route=route, budget=config.timeout)
        started = time.monotonic()
        try:
            state = client.connect(route.location.nickname, route.protocol, route.port, config.timeout)
        except ConnectionRejected as exc:
            return Result("connect_error", str(exc), round(time.monotonic() - started, 2))
        except TimeoutError as exc:
            return Result("timeout", str(exc), round(time.monotonic() - started, 2))
        duration = round(time.monotonic() - started, 2)
        if state.connection_error:
            return Result("connect_error", state.connection_error, duration)
        if not route_identity_matches(state, route):
            return Result("route_mismatch", "Windscribe reported a different location, protocol, port, or interference.", duration)
        self.emit("Settle", "Allowing the new route to settle", route=route, budget=config.settle)
        client.sleep(config.settle)
        self.emit("Verify HTTPS", "Checking two HTTPS endpoints and VPN egress", route=route)
        ok, verified, detail = check_https(self.cancel, state.vpn_ip)
        self.emit("Measure", "Measuring ICMP latency, jitter, and loss", route=route)
        latency, jitter, loss = ping(config.ping_count, self.cancel)
        outcome = "verified" if verified else "https_only" if ok == 2 else "partial" if ok else "no_internet"
        result = Result(outcome, detail, duration, latency, jitter, loss, ok, 2, verified)
        if config.download_mb and ok == 2:
            self.emit("Download", f"Downloading a capped {config.download_mb} MB sample", route=route)
            count = config.download_mb * 1_000_000
            try:
                data, elapsed = https_get("speed.cloudflare.com", f"/__down?bytes={count}", count + 1, self.cancel)
                result.download_bytes = len(data)
                if len(data) == count:
                    result.download_mbps = round(len(data) * 8 / elapsed / 1_000_000, 2)
                else:
                    result.detail += " Download sample was incomplete."
            except (OSError, http.client.HTTPException):
                result.detail += " Download sample unavailable."
        self.emit("Validate", "Confirming the route remained connected", route=route)
        final = client.wait_ready()
        if not route_identity_matches(final, route) or (state.vpn_ip and final.vpn_ip != state.vpn_ip):
            result.outcome, result.tunnel_verified = "unstable", False
            result.detail = "Connection changed or lost validation during measurement."
        if latency is None:
            result.detail += " ICMP unavailable or blocked; latency is unknown."
        return result

    def restore(self, client: Client, original: dict, config: RunConfig):
        if config.restore and original["state"] == "connected" and (original["protocol"] not in PROTOCOLS or not 1 <= original["port"] <= 65535):
            raise ClientError("The saved restore target is incomplete; no disconnect was issued.")
        self.emit("Restore" if config.restore else "Finish disconnect",
                  "Restoring the connection present before this session" if config.restore else "Disconnecting after testing")
        cleanup = Client(client.path)  # Independent of the user's cancellation flag.
        cleanup.disconnect(timeout=30)
        if config.restore and original["state"] == "connected":
            old = ClientStatus(**original)
            nickname = re.split(r"\s+-\s+", old.location)[-1]
            state = cleanup.connect(nickname, old.protocol, old.port, max(45, config.timeout))
            if (state.state != "connected" or state.protocol != old.protocol
                    or state.port != old.port or state.location.casefold() != old.location.casefold()):
                raise ClientError("The original connection could not be verified. Check Windscribe.")
        final = cleanup.status()
        if final.firewall != original["firewall"]:
            raise ClientError("Windscribe's firewall state differs from the start of the session. Review it in the official app.")

    def run(self):
        run = self.store.run(self.run_id)
        config, mode = run["config"], run["mode"]
        tasks = self.store.tasks(self.run_id)
        completed = sum(result is not None for _, _, result in tasks)
        touched, keep, client, original, controller = False, False, None, None, None
        active_anti, verification = None, "not_managed"
        owns_settings = False
        state, error, cleanup_error = "complete", "", ""
        try:
            config.validate()
            self.store.set_state(self.run_id, "running")
            if mode == "live":
                self.emit("Preflight", "Verifying official client capabilities")
                client = Client(config.cli_path, self.cancel)
                initial = client.wait_ready(report=lambda message: self.emit("Starting Windscribe", message))
                if not initial.logged_in:
                    raise ClientError("Sign in to the official Windscribe client first.")
                if initial.state not in ("connected", "disconnected"):
                    raise ClientError("Wait for Windscribe to finish its current connection change.")
                supported = client.ports()
                for _, route, result in tasks:
                    if result is None and route.port not in supported.get(route.protocol, []):
                        raise ClientError(f"This client does not advertise {route.protocol}:{route.port}. Refresh ports in Setup.")
                original = dict(run["original"] or asdict(initial))
                original["vpn_ip"] = ""  # Restoration does not need a stored public IP.
                original["public_ip"] = ""
                if run["original"] and original["state"] == "connected" and (original["protocol"] not in PROTOCOLS or not 1 <= original["port"] <= 65535):
                    original = asdict(initial)
                    original["vpn_ip"] = original["public_ip"] = ""
                    self.store.repair_original(self.run_id, run["original"], original)
                    self.emit("Preflight", "Incomplete legacy restore target replaced with the current connection.")
                self.store.set_original(self.run_id, original)
                managed = [route.anti_censorship for _, route, result in tasks if result is None and route.anti_censorship]
                if managed:
                    # Settings transactions finish atomically even when Pause is requested.
                    controller = SettingsController(Client(config.cli_path), self.store.path.parent,
                                                    lambda message: self.emit("Settings", message))
                    document = controller.inspect()
                    for profile in managed:
                        profile.validate()
                        if profile.protocol_tweaks == "Enabled" and profile.amnezia not in controller.presets:
                            raise ClientError("A requested Amnezia preset is no longer advertised by Windscribe.")
                    controller.begin(original)
                    owns_settings = True
            for seq, route, result in tasks:
                if result is not None:
                    continue
                if self.cancel.is_set():
                    raise Cancelled()
                self.emit("Preparing", route.display, route=route, completed=completed, total=len(tasks))
                if mode == "demo":
                    for phase in ("Disconnect", "Connect", "Settle", "Verify HTTPS", "Measure"):
                        self.emit(phase, f"SIMULATION · {route.display}", route=route)
                        if self.cancel.wait(.18):
                            raise Cancelled()
                    result = demo_result(route)
                else:
                    touched = True
                    if route.anti_censorship and route.anti_censorship != active_anti:
                        self.emit("Apply settings", route.anti_censorship.label, route=route)
                        verification = controller.apply(route.anti_censorship)
                        active_anti = route.anti_censorship
                        if self.cancel.is_set():
                            raise Cancelled()
                    result = self.measure(client, route, config)
                    if route.anti_censorship:
                        controller.verify(route.anti_censorship)
                        result.applied_anti_censorship = asdict(route.anti_censorship)
                        result.settings_verification = verification
                if mode == "demo" and route.anti_censorship:
                    result.applied_anti_censorship = asdict(route.anti_censorship)
                    result.settings_verification = "simulated"
                if self.cancel.is_set():
                    raise Cancelled()
                self.store.record(self.run_id, seq, result)
                completed += 1
                self.emit("Saved", result.detail, route=route, result=result, completed=completed, total=len(tasks))
                if self.manual or (config.stop_on_success and result.working):
                    keep = result.working
                    state = "connected" if keep else "complete"
                    break
            if self.cancel.is_set():
                raise Cancelled()
        except Cancelled:
            state = "paused"
            self.emit("Paused", "Completed results saved. The interrupted test will be retried on resume.")
        except Exception as exc:
            state, error = "error", str(exc)
            self.emit("Error", error)
        finally:
            if controller and owns_settings and not keep:
                try:
                    self.emit("Restore settings", "Restoring original anti-censorship settings")
                    controller.logger = lambda message: self.emit("Restore settings", message)
                    controller.restore(clear=False)
                except Exception as exc:
                    cleanup_error = str(exc)
                    self.emit("Attention", f"Settings recovery needs attention: {cleanup_error}")
            if mode == "live" and touched and not keep and client and original:
                try:
                    self.restore(client, original, config)
                except Exception as exc:
                    cleanup_error = "; ".join(filter(None, [cleanup_error, str(exc)]))
                    self.emit("Attention", f"Connection restoration needs attention: {cleanup_error}")
            if controller and owns_settings and not keep and not cleanup_error:
                try:
                    controller.clear_recovery()
                except Exception as exc:
                    cleanup_error = str(exc)
            if cleanup_error:
                state = "error"
            self.store.set_state(self.run_id, state)
            self.finished_run.emit({"state": state, "error": error, "cleanup_error": cleanup_error, "kept": keep,
                                    "settings_retained": bool(controller and owns_settings and keep)})


class SettingsRecovery(QThread):
    recovered = Signal(dict)

    def __init__(self, path: str, directory: Path, action="restore", reviewed_current=None):
        super().__init__()
        self.path, self.directory = path, directory
        self.action, self.reviewed_current = action, reviewed_current

    def run(self):
        try:
            client = Client(self.path)
            controller = SettingsController(client, self.directory)
            if self.action == "keep_current":
                archive = controller.keep_current(self.reviewed_current)
                self.recovered.emit({"ok": True, "kept_current": True, "archive": str(archive)})
                return
            original = controller.restore(clear=False, reviewed_current=self.reviewed_current)
            if original:
                # A kept route or crash may have left a different connection active.
                client.disconnect()
                if original["state"] == "connected":
                    nickname = re.split(r"\s+-\s+", original["location"])[-1]
                    state = client.connect(nickname, original["protocol"], original["port"], 30)
                    if (state.protocol != original["protocol"] or state.port != original["port"]
                            or state.location.casefold() != original["location"].casefold()):
                        raise ClientError("Settings restored, but the original connection could not be verified.")
                if client.status().firewall != original["firewall"]:
                    raise ClientError("Settings restored; review the changed firewall state in Windscribe. Recovery information retained.")
                controller.clear_recovery()
            self.recovered.emit({"ok": True})
        except Exception as exc:
            self.recovered.emit({"error": str(exc)})
