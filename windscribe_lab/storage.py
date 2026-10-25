from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path

from .models import Result, Route, RunConfig, utc_now


class Store:
    """Each operation owns its SQLite connection, including worker operations."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "sessions.db"
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created TEXT NOT NULL, config TEXT NOT NULL,
                    mode TEXT NOT NULL, state TEXT NOT NULL, original TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
                    route TEXT NOT NULL, result TEXT, PRIMARY KEY(run_id, seq)
                );
            """)
            conn.execute("UPDATE runs SET state='interrupted' WHERE state='running'")

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def create_run(self, config: RunConfig, plan: list[Route], mode: str) -> str:
        run_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,NULL)",
                         (run_id, utc_now(), json.dumps(asdict(config)), mode, "ready"))
            conn.executemany("INSERT INTO tasks VALUES(?,?,?,NULL)",
                             [(run_id, i, json.dumps(r.to_dict())) for i, r in enumerate(plan)])
        return run_id

    def run(self, run_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Session no longer exists.")
        value = dict(row)
        value["config"] = RunConfig(**json.loads(value["config"]))
        value["original"] = json.loads(value["original"]) if value["original"] else None
        return value

    def set_state(self, run_id: str, state: str):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET state=? WHERE id=?", (state, run_id))

    def set_original(self, run_id: str, original: dict):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET original=? WHERE id=? AND original IS NULL", (json.dumps(original), run_id))

    def repair_original(self, run_id: str, previous: dict, replacement: dict):
        """Repair only an incomplete legacy baseline, using a fresh preflight snapshot."""
        from .models import PROTOCOLS
        valid = lambda value: (value.get("state") != "connected" or
                               value.get("protocol") in PROTOCOLS and 1 <= value.get("port", 0) <= 65535 and bool(value.get("location")))
        if valid(previous) or not valid(replacement):
            raise ValueError("Cannot replace a valid restore target or use an incomplete replacement.")
        with self.connect() as conn:
            changed = conn.execute("UPDATE runs SET original=? WHERE id=? AND original=?",
                                   (json.dumps(replacement), run_id, json.dumps(previous))).rowcount
            if changed != 1:
                raise ValueError("Saved restore target changed during preflight. Retry the session.")

    def record(self, run_id: str, seq: int, result: Result):
        with self.connect() as conn:
            conn.execute("UPDATE tasks SET result=? WHERE run_id=? AND seq=?",
                         (json.dumps(asdict(result)), run_id, seq))

    def tasks(self, run_id: str) -> list[tuple[int, Route, Result | None]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        return [(r["seq"], Route.from_dict(json.loads(r["route"])),
                 Result(**json.loads(r["result"])) if r["result"] else None) for r in rows]

    def runs(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT r.*, COUNT(t.seq) total,
                SUM(CASE WHEN t.result IS NOT NULL THEN 1 ELSE 0 END) completed
                FROM runs r JOIN tasks t ON r.id=t.run_id GROUP BY r.id ORDER BY r.created DESC, r.rowid DESC""").fetchall()
        return [dict(r) for r in rows]

    def report_snapshot(self, run_id: str):
        run = self.run(run_id)
        cfg = run["config"]
        records = []
        for seq, route, result in self.tasks(run_id):
            row = {"session": run_id, "mode": run["mode"], "network": cfg.network,
                   "profile": cfg.profile, "declared_settings": json.dumps(cfg.settings, sort_keys=True),
                   "settings_verified": False, "notes": cfg.notes, "trial": route.trial,
                   "requested_anti_censorship": json.dumps(asdict(route.anti_censorship)) if route.anti_censorship else "",
                   "anti_censorship_verified": bool(result and run["mode"] == "live" and result.settings_verification.startswith("registry_readback")),
                   "country": route.location.country, "city": route.location.city,
                   "server": route.location.nickname, "protocol": route.protocol, "port": route.port,
                   **(asdict(result) if result else {"outcome": "pending"})}
            records.append(row)
        return {"session": run_id, "mode": run["mode"], "created": run["created"],
                "config": asdict(cfg), "results": records}

    def export(self, run_id: str, path: Path, working_only=False):
        from .reporting import write_report
        return write_report(self.report_snapshot(run_id), path, working_only)
