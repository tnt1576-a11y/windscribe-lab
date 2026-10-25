"""Independent reader for Windscribe's public EngineSettings serialization.

Schema: Desktop-App v2.24.13, enginesettings.cpp and nested types. No offset
searches: consume and validate the stream, retaining untouched bytes verbatim.
Decrypted settings can contain proxy/DNS credentials: never log or persist them.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import secrets
import struct
import zlib

MAX_BYTES = 1_048_576
KEY = (0x4572A4ACF31A31BA).to_bytes(8, "little")  # Public format key, not a user credential.
TWEAKS = {"Auto": 0, "Enabled": 1, "Disabled": 2}
ROUTING = {"Auto": 0, "Regular": 1, "Alternate": 2}
KNOWN_PRESETS = [
    "A - No Junk Russia Primary", "B - No Junk Russia Alt", "C - With Junk Russia Primary",
    "D - With Junk Russia Alt", "E - Baseline No Junk", "F - Baseline With Junk",
    "G - Popular Russia Website with Junk", "H - Iran 26 May 2026", "I - UAE and Iran Legacy",
    "J - Iran Junk Packets", "K - UAE",
]


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class AntiCensorship:
    protocol_tweaks: str = "Auto"
    amnezia: str = ""
    routing: str = "Auto"
    large_tls: bool = False

    def validate(self):
        if self.protocol_tweaks not in TWEAKS or self.routing not in ROUTING or type(self.large_tls) is not bool:
            raise SettingsError("Invalid anti-censorship mode or routing value.")
        if len(self.amnezia) > 256 or any(ord(c) < 32 for c in self.amnezia):
            raise SettingsError("Invalid Amnezia preset name.")
        if self.protocol_tweaks == "Enabled" and not self.amnezia:
            raise SettingsError("Choose an Amnezia configuration when protocol tweaks are Enabled.")

    @property
    def label(self):
        tweaks = self.amnezia if self.protocol_tweaks == "Enabled" else self.protocol_tweaks
        return f"{tweaks} / {self.routing} / TLS {'On' if self.large_tls else 'Off'}"


def checksum(data: bytes) -> int:
    """Qt qChecksum default: CRC-16/ISO-HDLC (X-25)."""
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x8408 if value & 1 else 0)
    return value ^ 0xFFFF


def decrypt(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_BYTES * 2:
        raise SettingsError("Unexpected registry value size/type.")
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise SettingsError("Invalid settings encoding.") from exc
    if len(raw) < 5 or raw[0] != 3 or raw[1] & ~7 or raw[1] & 6 not in (2, 4):
        raise SettingsError("Unsupported settings protection format; integrity protection is required.")
    flags, previous, decoded = raw[1], 0, bytearray()
    for i, byte in enumerate(raw[2:]):
        decoded.append(byte ^ previous ^ KEY[i % 8])
        previous = byte
    payload = bytes(decoded[1:])
    if flags & 2:
        if len(payload) < 2 or int.from_bytes(payload[:2], "big") != checksum(payload[2:]):
            raise SettingsError("Settings checksum did not match.")
        payload = payload[2:]
    elif flags & 4:
        if len(payload) < 20 or payload[:20] != hashlib.sha1(payload[20:]).digest():
            raise SettingsError("Settings integrity hash did not match.")
        payload = payload[20:]
    if flags & 1:
        if len(payload) < 4 or int.from_bytes(payload[:4], "big") > MAX_BYTES:
            raise SettingsError("Invalid compressed settings length.")
        expected = int.from_bytes(payload[:4], "big")
        try:
            inflater = zlib.decompressobj()
            result = inflater.decompress(payload[4:], MAX_BYTES + 1)
        except zlib.error as exc:
            raise SettingsError("Cannot decompress settings.") from exc
        if len(result) != expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
            raise SettingsError("Compressed settings failed length/stream validation.")
        payload = result
    if len(payload) > MAX_BYTES:
        raise SettingsError("Settings exceed the supported size.")
    return payload


def encrypt(data: bytes) -> str:
    compressed = len(data).to_bytes(4, "big") + zlib.compress(data, 9)
    plain = secrets.token_bytes(1) + checksum(compressed).to_bytes(2, "big") + compressed
    result, previous = bytearray(b"\x03\x03"), 0
    for i, byte in enumerate(plain):
        previous = byte ^ KEY[i % 8] ^ previous
        result.append(previous)
    return base64.b64encode(result).decode("ascii")


class Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise SettingsError("Settings stream is truncated.")
        value = self.data[self.pos:self.pos + count]
        self.pos += count
        return value

    def uint(self):
        return int.from_bytes(self.take(4), "big")

    def integer(self):
        return int.from_bytes(self.take(4), "big", signed=True)

    def boolean(self):
        value = self.take(1)[0]
        if value not in (0, 1):
            raise SettingsError("Invalid boolean in settings stream.")
        return bool(value)

    def string(self):
        count = self.uint()
        if count == 0xFFFFFFFF:
            return ""
        if count % 2 or count > MAX_BYTES:
            raise SettingsError("Invalid settings string length.")
        try:
            return self.take(count).decode("utf-16-be")
        except UnicodeError as exc:
            raise SettingsError("Invalid settings string.") from exc

    def count(self):
        count = self.uint()
        if count > 4096:
            raise SettingsError("Unexpected collection size in settings.")
        return count

    def version(self, accepted=(1,)):
        value = self.uint()
        if value not in accepted:
            raise SettingsError("An unsupported nested settings version was found.")
        return value

    def connection(self):
        self.version()
        self.integer()  # Protocol
        self.uint()  # Port
        self.boolean()

    def interface(self):
        version = self.version((1, 2))
        self.integer()
        for _ in range(3):
            self.string()
        self.integer()
        self.integer()
        self.boolean()
        self.string()
        if version == 1:
            self.boolean()
        self.integer()
        self.string()
        self.integer()
        if version == 1:
            self.integer()
            self.integer()
            self.string()
            self.boolean()
            self.boolean()


def qstring(text: str) -> bytes:
    value = text.encode("utf-16-be")
    return len(value).to_bytes(4, "big") + value


class EngineDocument:
    def __init__(self, plain: bytes):
        r = Reader(plain)
        if r.uint() != 0x7745C2AE:
            raise SettingsError("Unrecognized EngineSettings header.")
        self.version = r.version((11, 12, 13))
        r.string()  # Language
        r.integer()  # Update channel
        if self.version < 12:
            r.boolean()  # Deprecated ignore-SSL flag: preserve, never change.
        r.boolean()  # Terminate sockets
        r.boolean()  # LAN access
        r.version(); r.integer(); r.integer()  # Firewall
        r.connection()
        r.version(); r.boolean(); r.string()  # Legacy API resolution
        r.version(); r.integer(); r.string(); r.uint(); r.string(); r.string()  # Proxy
        r.version(); r.boolean(); r.integer()  # Packet size
        r.version(); r.boolean(); r.string(); r.boolean(); r.interface()  # MAC spoofing
        for _ in range(r.count()):
            r.interface()
        r.integer(); r.integer(); r.string(); r.boolean()  # DNS policy, adapter, config path, keepalive
        dns_version = r.version((1, 2, 3))
        r.integer(); r.string()
        if dns_version >= 2:
            r.boolean(); r.string()
            for _ in range(r.count()):
                r.string()
        if dns_version >= 3:
            r.string()  # Control D API key: consume without storing.
            for _ in range(r.count()):
                r.string(); r.string()
        r.integer()  # DNS manager
        for _ in range(r.count()):
            r.string(); r.connection()  # Network preferred protocols
        self.spans = {}
        start = r.pos
        tls = r.boolean()
        self.spans["large_tls"] = (start, r.pos)
        r.version(); r.boolean(); r.integer()  # Decoy traffic
        start = r.pos
        preset = r.string()
        self.spans["amnezia"] = (start, r.pos)
        start = r.pos
        routing = r.integer()
        self.spans["routing"] = (start, r.pos)
        start = r.pos
        tweaks = r.integer()
        self.spans["protocol_tweaks"] = (start, r.pos)
        ip_stack = r.integer()
        if self.version >= 13:
            r.string()  # Custom SNI: preserve exactly.
        if r.pos != len(plain) or routing not in (0, 1, 2) or tweaks not in (0, 1, 2) or ip_stack not in (0, 1, 2):
            raise SettingsError("Settings schema validation failed; no changes are permitted.")
        self.plain = plain
        self.current = AntiCensorship(list(TWEAKS)[tweaks], preset, list(ROUTING)[routing], tls)

    @classmethod
    def from_registry(cls, value: str):
        return cls(decrypt(value))

    def patch(self, target: AntiCensorship) -> bytes:
        target.validate()
        values = {"protocol_tweaks": struct.pack(">i", TWEAKS[target.protocol_tweaks]),
                  "routing": struct.pack(">i", ROUTING[target.routing]),
                  "large_tls": bytes([target.large_tls]), "amnezia": qstring(target.amnezia)}
        result = self.plain
        for key, (start, end) in sorted(self.spans.items(), key=lambda kv: kv[1][0], reverse=True):
            if getattr(target, key) != getattr(self.current, key):
                result = result[:start] + values[key] + result[end:]
        if EngineDocument(result).current != target:
            raise SettingsError("Settings patch failed round-trip verification.")
        return result
