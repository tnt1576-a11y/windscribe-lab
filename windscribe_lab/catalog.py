from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
import urllib.request

from .models import Location

CATALOG_URL = "https://assets.windscribe.com/serverlist/mob-v2/1/0"


def parse_catalog(raw: dict) -> list[Location]:
    if isinstance(raw.get("locations"), list):
        return [Location(**row) for row in raw["locations"]]
    locations = []
    for region in raw.get("data", []):
        if region.get("status", 1) != 1:
            continue
        for group in region.get("groups", []):
            if group.get("status", 1) != 1 or not group.get("nick") or not group.get("city"):
                continue
            locations.append(Location(str(group["id"]), region["name"], region["country_code"],
                                      group["city"], group["nick"], bool(group.get("pro", 1))))
    if not locations:
        raise ValueError("Windscribe returned no usable server locations.")
    return locations


def refresh_catalog(path: Path) -> list[Location]:
    request = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "WindscribeLab/1.1", "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=15) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Server list exceeds the expected size.")
    result = parse_catalog(json.loads(raw))
    payload = {"source": CATALOG_URL, "locations": [asdict(loc) for loc in result]}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return result
