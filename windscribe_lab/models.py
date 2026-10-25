from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .antischema import AntiCensorship

PROTOCOLS = {
    "wireguard": ("WireGuard", [443, 80, 53, 123, 1194, 65142]),
    "ikev2": ("IKEv2", [500]),
    "udp": ("OpenVPN UDP", [443, 80, 53, 123, 1194, 54783]),
    "tcp": ("OpenVPN TCP", [443, 587, 21, 22, 80, 123, 143, 3306, 8080, 54783, 1194]),
    "stealth": ("Stealth", [443, 587, 21, 22, 80, 123, 143, 3306, 8080, 54783, 8443]),
    "wstunnel": ("WStunnel", [443]),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Location:
    id: str
    country: str
    code: str
    city: str
    nickname: str
    pro: bool = False

    @property
    def display(self) -> str:
        return f"{self.city} · {self.nickname}"


@dataclass(frozen=True)
class Route:
    location: Location
    protocol: str
    port: int
    trial: int = 1
    anti_censorship: AntiCensorship | None = None

    @property
    def display(self) -> str:
        suffix = f" / {self.anti_censorship.label}" if self.anti_censorship else ""
        return f"{self.location.display} / {PROTOCOLS[self.protocol][0]} : {self.port}{suffix}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Route:
        anti = AntiCensorship(**value["anti_censorship"]) if value.get("anti_censorship") else None
        return cls(Location(**value["location"]), value["protocol"], int(value["port"]), int(value.get("trial", 1)), anti)


@dataclass
class RunConfig:
    cli_path: str = r"C:\Program Files\Windscribe\windscribe-cli.exe"
    network: str = "My network"
    profile: str = "Current Windscribe settings"
    notes: str = ""
    timeout: int = 30
    settle: int = 3
    ping_count: int = 5
    trials: int = 1
    download_mb: int = 0
    stop_on_success: bool = False
    restore: bool = True
    settings: dict = field(default_factory=lambda: {
        "censorship": "As configured in Windscribe", "amnezia": "As configured",
        "routing": "As configured", "sni": "", "tls_padding": "As configured", "mtu": "Auto",
    })
    anti_profiles: list[dict] = field(default_factory=list)

    def validate(self) -> None:
        if not 5 <= self.timeout <= 180 or not 0 <= self.settle <= 30:
            raise ValueError("Connection timeout or settle delay is out of range.")
        if not 2 <= self.ping_count <= 20 or not 1 <= self.trials <= 5:
            raise ValueError("Use 2–20 pings and 1–5 trials.")
        if self.download_mb not in (0, 1, 5, 10):
            raise ValueError("Download sample must be off, 1 MB, 5 MB, or 10 MB.")
        if not self.network.strip() or not self.profile.strip():
            raise ValueError("Give this network and settings profile a name.")
        if len(self.anti_profiles) > 512:
            raise ValueError("Choose fewer than 512 anti-censorship combinations.")
        for profile in self.anti_profiles:
            AntiCensorship(**profile).validate()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


def make_plan(locations: list[Location], protocols: dict[str, list[int]], trials: int = 1,
              anti_profiles: list[dict] | None = None) -> list[Route]:
    if not locations or not any(protocols.values()):
        raise ValueError("Select at least one location and one protocol / port.")
    if not 1 <= trials <= 5:
        raise ValueError("Use 1–5 trials.")
    plan = []
    profiles = list(dict.fromkeys(AntiCensorship(**value) for value in anti_profiles)) if anti_profiles else [None]
    estimate = len(dict.fromkeys(locations)) * sum(len(set(ports)) for ports in protocols.values()) * trials * len(profiles)
    if estimate > 10000:
        raise ValueError("This plan exceeds 10,000 tests. Choose fewer locations, ports, repeats, or settings combinations.")
    for profile in profiles:
        if profile:
            profile.validate()
    for trial in range(1, trials + 1):
        for protocol, ports in protocols.items():
            if protocol not in PROTOCOLS:
                raise ValueError(f"Unknown protocol: {protocol}")
            for location in dict.fromkeys(locations):
                for port in dict.fromkeys(ports):
                    if not isinstance(port, int) or not 1 <= port <= 65535:
                        raise ValueError("Invalid port.")
                    for profile in profiles:
                        plan.append(Route(location, protocol, port, trial, profile))
    # Group settings to avoid restarting Windscribe for every server/port.
    if anti_profiles:
        plan.sort(key=lambda route: profiles.index(route.anti_censorship))
    if len(plan) > 10000:
        raise ValueError("This plan exceeds 10,000 tests. Choose fewer locations or ports.")
    return plan


@dataclass
class ClientStatus:
    state: str = "unknown"
    location: str = ""
    protocol: str = ""
    port: int = 0
    logged_in: bool = False
    firewall: str = "unknown"
    vpn_ip: str = ""
    pending: bool = False
    interference: bool = False
    login_state: str = "unknown"
    connection_error: str = ""
    public_ip: str = ""


def parse_status(output: str) -> ClientStatus:
    result = ClientStatus()
    for line in output.splitlines():
        starred = line.lstrip().startswith("*")
        line = line.lstrip().lstrip("* ")
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "login state":
            result.logged_in = value.lower() == "logged in"
            result.login_state = value.lower()
        elif key == "firewall state":
            result.firewall = value
        elif key == "connect state":
            result.pending = starred or value.startswith("*")
            result.interference = "[network interference]" in value.lower()
            value = value.lstrip("* ")
            state, _, location = value.partition(":")
            result.state = state.strip().lower()
            result.location = re.sub(r"\s*\[Network interference\].*", "", location, flags=re.I).strip()
            if result.state == "error" or result.state.startswith("disconnected due to"):
                result.connection_error = value
                result.state, result.location = "disconnected", ""
        elif key == "protocol":
            match = re.fullmatch(r"([a-zA-Z][a-zA-Z0-9]*)\s*:\s*([0-9]+)", value)
            if match:
                result.protocol, result.port = match[1].lower(), int(match[2])
        elif key == "vpn ip":
            result.vpn_ip = value
        elif key in ("public ip", "ip address"):
            result.public_ip = value
    if result.state == "unknown" and result.login_state in ("logged out", "logging in"):
        result.state = "disconnected"
    return result


def route_matches(status: ClientStatus, route: Route) -> bool:
    return route_identity_matches(status, route) and not status.pending and not status.interference


def route_identity_matches(status: ClientStatus, route: Route) -> bool:
    # A connected flag is insufficient: invalid ports can silently fall back.
    nick = route.location.nickname.casefold()
    location = re.split(r"\s+-\s+", status.location)[-1].casefold()
    return (status.state == "connected"
            and status.protocol == route.protocol and status.port == route.port and location == nick)


@dataclass
class Result:
    outcome: str
    detail: str = ""
    connect_s: float | None = None
    latency_ms: float | None = None
    jitter_ms: float | None = None
    loss_pct: float | None = None
    https_ok: int = 0
    https_total: int = 2
    tunnel_verified: bool = False
    download_mbps: float | None = None
    download_bytes: int = 0
    timestamp: str = field(default_factory=utc_now)
    applied_anti_censorship: dict | None = None
    settings_verification: str = "not_managed"

    @property
    def working(self) -> bool:
        return self.outcome in ("verified", "https_only")


def demo_result(route: Route) -> Result:
    seed = int(hashlib.sha256(json.dumps(route.to_dict(), sort_keys=True).encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    if seed % 7 == 0:
        return Result("timeout", "SIMULATED · handshake did not complete", connect_s=30)
    latency = rng.uniform(38, 195)
    return Result("verified", "SIMULATED · matching VPN IP and two HTTPS checks", round(rng.uniform(1.4, 6), 1),
                  round(latency, 1), round(rng.uniform(1, 12), 1), 0 if seed % 4 else 20,
                  2, 2, True)
