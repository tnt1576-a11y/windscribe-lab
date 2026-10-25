"""Opt-in, version-gated Windows settings transactions with protected recovery."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from .antischema import AntiCensorship, EngineDocument, SettingsError, encrypt

SUPPORTED_VERSION = (2, 24, 13)
REGISTRY_PATH = r"Software\Windscribe\Windscribe2"
RECOVERY_FILE = "settings-recovery.dpapi"


def dpapi(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise SettingsError("Automatic Windscribe settings control requires Windows.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        call = crypt.CryptUnprotectData
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        call = crypt.CryptProtectData
        args = (ctypes.byref(source), "Windscribe Lab settings recovery", None, None, None, 1, ctypes.byref(output))
    call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                     ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    call.restype = wintypes.BOOL
    if not call(*args):
        raise SettingsError("Windows could not protect/unprotect the settings recovery backup.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class Registry:
    def read(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_READ) as key:
                value, kind = winreg.QueryValueEx(key, "engineSettings")
            if kind != winreg.REG_SZ:
                raise SettingsError("Windscribe settings have an unsupported registry type.")
            return value
        except OSError as exc:
            raise SettingsError("Cannot read Windscribe's current-user engineSettings value.") from exc

    def write(self, value: str, expected: str):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            current, kind = winreg.QueryValueEx(key, "engineSettings")
            if kind != winreg.REG_SZ or current != expected:
                raise SettingsError("Settings changed before the write; no overwrite was performed.")
            winreg.SetValueEx(key, "engineSettings", 0, winreg.REG_SZ, value)
            winreg.FlushKey(key)
        if self.read() != value:
            raise SettingsError("Registry write could not be verified. Recovery backup retained.")


class WindowsHost:
    """Restart only the selected frontend, gracefully. Never terminate VPN helpers."""
    def __init__(self, cli_path: str):
        if os.name != "nt":
            raise SettingsError("Automatic settings control requires Windows.")
        self.exe = Path(cli_path).resolve().with_name("Windscribe.exe")
        self._started_process = None
        if not self.exe.is_file():
            raise SettingsError("Windscribe.exe must be beside the selected official CLI.")

    def version(self):
        lib = ctypes.WinDLL("version", use_last_error=True)
        lib.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        lib.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        lib.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        lib.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        size = lib.GetFileVersionInfoSizeW(str(self.exe), None)
        if not size:
            raise SettingsError("Cannot verify the installed Windscribe version.")
        data = ctypes.create_string_buffer(size)
        if not lib.GetFileVersionInfoW(str(self.exe), 0, size, data):
            raise SettingsError("Cannot read Windscribe's version resource.")
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if not lib.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 16:
            raise SettingsError("Invalid Windscribe version resource.")
        words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
        if words[0] != 0xFEEF04BD:
            raise SettingsError("Invalid Windscribe version signature.")
        return words[2] >> 16, words[2] & 0xFFFF, words[3] >> 16

    def pids(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD),
                        ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                        ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                        ("name", wintypes.WCHAR * 260)]

        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        session = wintypes.DWORD()
        if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise SettingsError("Cannot establish the current Windows session.")
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise SettingsError("Cannot enumerate the Windscribe frontend.")
        result, entry = [], Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        try:
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                if entry.name.casefold() == "windscribe.exe":
                    other = wintypes.DWORD()
                    if kernel.ProcessIdToSessionId(entry.pid, ctypes.byref(other)) and other.value == session.value:
                        handle = kernel.OpenProcess(0x1000, False, entry.pid)
                        if not handle:
                            raise SettingsError("Cannot verify a Windscribe process path; refusing restart.")
                        try:
                            text, count = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                            if not kernel.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(count)):
                                raise SettingsError("Cannot verify the frontend executable path.")
                            if Path(text.value).resolve() == self.exe:
                                result.append(entry.pid)
                        finally:
                            kernel.CloseHandle(handle)
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        return result

    def main_window_ready(self):
        """Match the official CLI's startup condition without running the CLI.

        Windows CLI checks for the main window, not just a live process. Calling
        it before that window exists starts a second GUI and can show Windscribe's
        single-instance error. This query never activates or messages a window.
        """
        pids = self.pids()
        if len(pids) != 1:
            return False
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.EnumWindows.restype = wintypes.BOOL
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowThreadProcessId.restype = wintypes.DWORD
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowTextW.restype = ctypes.c_int
        found = False

        @callback_type
        def visit(window, unused):
            nonlocal found
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(window, ctypes.byref(pid))
            if pid.value == pids[0]:
                title = ctypes.create_unicode_buffer(128)
                if user.GetWindowTextW(window, title, len(title)) and title.value.casefold() == "windscribe":
                    found = True
                    return False
            return True

        enumerated = user.EnumWindows(visit, 0)
        if not enumerated and not found:
            raise SettingsError("Cannot verify Windscribe's main window; no CLI command was sent.")
        return found

    def stop(self):
        pids = self.pids()
        if len(pids) > 1:
            raise SettingsError("Multiple matching Windscribe frontends are running; refusing to choose one.")
        if not pids:
            return
        system = Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"
        result = subprocess.run([str(system), "/PID", str(pids[0])], capture_output=True,
                                timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 15
        while self.pids() and time.monotonic() < deadline:
            time.sleep(.2)
        if self.pids():
            raise SettingsError("Windscribe did not exit gracefully. No settings were written; close it and retry. There is no force-kill fallback.")

    def start(self):
        if self._started_process is not None and self._started_process.poll() is None:
            return
        if self.pids():
            return
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 4  # Show without stealing focus.
        self._started_process = subprocess.Popen([str(self.exe)], cwd=self.exe.parent, startupinfo=startup,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class SettingsController:
    def __init__(self, client, directory: Path, logger=lambda text: None, *, registry=None, host=None, protector=dpapi):
        self.client, self.path, self.logger = client, directory / RECOVERY_FILE, logger
        self.registry = registry or Registry()
        self.host = host or WindowsHost(client.path)
        self.protector = protector
        self.journal = None
        self.presets = []

    def inspect(self, load_presets=True):
        if self.host.version() != SUPPORTED_VERSION:
            raise SettingsError("Automatic anti-censorship control currently supports Windscribe 2.24.13 only. Other versions require a verified schema adapter.")
        doc = EngineDocument.from_registry(self.registry.read())
        if not load_presets:
            return doc
        output = self.client.read_command("amneziawg", report=self.logger)
        presets = [line.strip() for line in output.splitlines() if line.strip()]
        if not presets or len(presets) > 128 or any(len(p) > 256 or " - " not in p for p in presets):
            raise SettingsError("Cannot verify the installed client's Amnezia configurations.")
        self.presets = presets
        return doc

    def _save(self):
        raw = json.dumps(self.journal).encode("utf-8")
        protected = self.protector(raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("wb") as handle:
            handle.write(protected)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(self.path)
        restored = self.protector(self.path.read_bytes(), decrypt=True)
        if restored != raw:
            raise SettingsError("Recovery backup verification failed. No settings change is allowed.")

    def begin(self, connection: dict):
        if self.path.exists():
            raise SettingsError("An original-settings recovery backup exists. Use Anti-censorship → Review settings recovery before a new managed run.")
        doc = self.inspect()
        doc.current.validate()  # Ensure the baseline can be restored before changing it.
        self.journal = {"format": 1, "version": list(SUPPORTED_VERSION), "original_cipher": self.registry.read(),
                        "original": asdict(doc.current), "expected": asdict(doc.current),
                        "phase": "prepared", "connection": connection}
        # Do not save potentially changed settings from between the two reads.
        if EngineDocument.from_registry(self.journal["original_cipher"]).plain != doc.plain:
            raise SettingsError("Windscribe settings changed during backup preparation. Retry.")
        self._save()

    def _load(self):
        if self.journal is None:
            if not self.path.exists():
                return False
            raw = self.path.read_bytes()
            if len(raw) > 3_000_000:
                raise SettingsError("Unexpected recovery backup size.")
            try:
                self.journal = json.loads(self.protector(raw, decrypt=True))
            except (ValueError, UnicodeError) as exc:
                raise SettingsError("Cannot read the protected recovery backup.") from exc
            if self.journal.get("format") != 1 or self.journal.get("version") != list(SUPPORTED_VERSION):
                raise SettingsError("Unsupported recovery backup version.")
            backup = EngineDocument.from_registry(self.journal["original_cipher"])
            if asdict(backup.current) != self.journal["original"]:
                raise SettingsError("Recovery backup validation failed.")
        return True

    def _ready(self):
        self.host.start()
        self.logger("Waiting for the restarted Windscribe window and signed-in session.")
        status = self.client.wait_ready(report=self.logger, require_login=True)
        if not status.logged_in:
            raise SettingsError("Windscribe restarted but is signed out. Sign in in the official app; recovery backup retained.")
        if self.journal and self.journal.get("connection", {}).get("firewall") in ("On", "Always On"):
            if status.firewall == "Off":
                self.client.command("firewall", "on")
            if self.client.status().firewall not in ("On", "Always On"):
                raise SettingsError("Windscribe's original firewall protection could not be re-enabled.")

    def _replace(self, target: AntiCensorship, restoring=False, reviewed_current=None):
        doc = self.inspect(load_presets=not restoring)
        original = AntiCensorship(**self.journal["original"])
        expected = AntiCensorship(**self.journal["expected"])
        allowed = (expected, original, reviewed_current)
        if reviewed_current is not None and doc.current != reviewed_current:
            raise SettingsError("Settings changed after the recovery review. Review the new values before proceeding.")
        if doc.current not in allowed:
            raise SettingsError("Anti-censorship settings changed outside this test. Refusing to overwrite them; recovery backup retained.")
        if not restoring and target.protocol_tweaks == "Enabled" and target.amnezia not in self.presets:
            raise SettingsError("The selected Amnezia configuration is not available in the installed client.")
        if doc.current == target:
            return "registry_readback_existing"
        target.validate()
        self.client.disconnect()
        self.logger("Restarting the selected Windscribe frontend to load anti-censorship settings.")
        self.host.stop()
        try:
            # Graceful exit can save other preferences. Parse again and retain all of them.
            value = self.registry.read()
            doc = EngineDocument.from_registry(value)
            if doc.current not in allowed:
                raise SettingsError("Settings changed while Windscribe was closing; no overwrite performed.")
            if self.host.pids():
                raise SettingsError("Windscribe restarted before settings could be written. Retry.")
            plain = doc.patch(target)
            self.journal["expected"] = asdict(target)
            self.journal["phase"] = "restore_pending" if restoring else "write_pending"
            self._save()  # Journal the planned value before the atomic registry write.
            self.registry.write(encrypt(plain), expected=value)
            if EngineDocument.from_registry(self.registry.read()).current != target:
                raise SettingsError("Settings readback did not match the requested combination.")
        finally:
            self._ready()  # Relaunch even if the write failed.
        if EngineDocument.from_registry(self.registry.read()).current != target:
            raise SettingsError("Windscribe did not retain the requested settings after restart.")
        self.journal["phase"] = "restored" if restoring else "applied"
        self._save()
        return "registry_readback_after_restart"

    def apply(self, target: AntiCensorship):
        if not self._load():
            raise SettingsError("A verified settings backup is required before applying a combination.")
        return self._replace(target)

    def verify(self, target):
        if EngineDocument.from_registry(self.registry.read()).current != target:
            raise SettingsError("Anti-censorship settings changed during measurement. Result was not accepted.")

    def clear_recovery(self):
        if self.journal and asdict(EngineDocument.from_registry(self.registry.read()).current) != self.journal["original"]:
            raise SettingsError("Original settings have not been verified; recovery backup retained.")
        self.path.unlink(missing_ok=True)
        self.journal = None

    def recovery_summary(self):
        if not self._load():
            raise SettingsError("No active settings recovery backup was found.")
        current = EngineDocument.from_registry(self.registry.read()).current
        return {"original": self.journal["original"], "expected": self.journal["expected"],
                "current": asdict(current), "conflict": asdict(current) not in (self.journal["original"], self.journal["expected"])}

    def keep_current(self, reviewed_current: AntiCensorship):
        if not self._load():
            raise SettingsError("No active recovery backup was found.")
        if EngineDocument.from_registry(self.registry.read()).current != reviewed_current:
            raise SettingsError("Settings changed after the recovery review. Review the new values before proceeding.")
        data = self.path.read_bytes()
        archive_dir = self.path.parent / "recovery-archives"
        archive_dir.mkdir(exist_ok=True)
        archive = archive_dir / f"settings-{uuid.uuid4().hex}.dpapi"
        with archive.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if archive.read_bytes() != data or self.path.read_bytes() != data:
            raise SettingsError("Recovery archive verification failed. The active backup was kept.")
        self.path.unlink()
        self.journal = None
        return archive

    def restore(self, clear=True, reviewed_current=None):
        if not self._load():
            return None
        if self.host.version() != SUPPORTED_VERSION:
            raise SettingsError("The Windscribe version changed. Recovery backup retained; restore requires the supported version.")
        original = AntiCensorship(**self.journal["original"])
        self._replace(original, restoring=True, reviewed_current=reviewed_current)
        connection = self.journal["connection"]
        if clear:
            self.clear_recovery()
        self.logger("Original anti-censorship settings restored and verified.")
        return connection
