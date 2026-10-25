"""Offline report generation. Filtering happens before any data reaches a file."""
from __future__ import annotations

import csv
import html
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

from . import __version__

OUTCOME_LABELS = {
    "verified": "Tunnel verified", "https_only": "HTTPS only", "connect_error": "Connection failed",
    "timeout": "Timed out", "partial": "Partial HTTPS", "no_internet": "No HTTPS",
    "route_mismatch": "Route mismatch", "unstable": "Unstable", "pending": "Pending",
}
WORKING = frozenset(("verified", "https_only"))


def select_report(snapshot: dict, working_only=False) -> dict:
    rows = snapshot["results"]
    selected = [row for row in rows if not working_only or row["outcome"] in WORKING]
    completed = sum(row["outcome"] != "pending" for row in rows)
    latencies = [row["latency_ms"] for row in selected if row["outcome"] in WORKING and row.get("latency_ms") is not None]
    return {**snapshot, "results": selected, "scope": "working" if working_only else "all",
            "summary": {"exported": len(selected), "planned": len(rows), "completed": completed,
                        "working": sum(row["outcome"] in WORKING for row in selected),
                        "verified": sum(bool(row.get("tunnel_verified")) for row in selected),
                        "servers": len({(row["country"], row["city"], row["server"]) for row in selected}),
                        "best_latency_ms": min(latencies) if latencies else None}}


def csv_value(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    # Keep spreadsheet applications from interpreting cell data as formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        value = "'" + value
    return value


def csv_text(rows):
    columns = list(dict.fromkeys(key for row in rows for key in row)) or ["country", "city", "server", "protocol", "port", "outcome"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows({key: csv_value(value) for key, value in row.items()} for row in rows)
    return output.getvalue()


def html_report(report: dict) -> str:
    config = report["config"]
    # HTML contains only selected rows and report context. No CLI path, entire
    # settings matrix, account data or excluded result payload is embedded.
    payload = {key: report[key] for key in ("session", "mode", "created", "scope", "summary", "results")}
    payload.update(network=config["network"], profile=config["profile"], notes=config["notes"],
                   labels=OUTCOME_LABELS, version=__version__)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                          ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        encoded = encoded.replace(char, escaped)
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    template = (root / "assets" / "report.html").read_text(encoding="utf-8")
    values = {"TITLE": html.escape(config["network"]), "DATA": encoded,
              "NONCE": secrets.token_urlsafe(24), "VERSION": __version__}
    return re.sub(r"@@(TITLE|DATA|NONCE|VERSION)@@", lambda match: values[match[1]], template)


def write_report(snapshot: dict, path: Path, working_only=False) -> dict:
    report = select_report(snapshot, working_only)
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        content, encoding = html_report(report), "utf-8"
    elif suffix == ".json":
        content, encoding = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), "utf-8"
    elif suffix == ".csv":
        content, encoding = csv_text(report["results"]), "utf-8-sig"
    else:
        raise ValueError("Choose an HTML, CSV or JSON report filename.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, newline="", dir=path.parent,
                                         prefix=".windscribe-report-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return report["summary"]
