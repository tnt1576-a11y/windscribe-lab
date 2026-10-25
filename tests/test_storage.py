import csv
import json

from windscribe_lab.models import Location, Result, RunConfig, make_plan
from windscribe_lab.storage import Store


def create(store, network="Network"):
    plan = make_plan([Location("1", "Germany", "DE", "Frankfurt", "Castle")], {"tcp": [443, 80]})
    return store.create_run(RunConfig(network=network), plan, "demo")


def test_history_retains_repeated_tests_and_unfinished_queue(tmp_path):
    store = Store(tmp_path)
    first, second = create(store), create(store)
    store.record(first, 0, Result("verified", https_ok=2, tunnel_verified=True))
    assert store.tasks(first)[0][2].working
    assert store.tasks(first)[1][2] is None
    assert store.tasks(second)[0][2] is None
    assert len(store.runs()) == 2
    store.set_state(first, "running")
    restarted = Store(tmp_path)
    assert restarted.run(first)["state"] == "interrupted"
    assert restarted.tasks(first)[0][2].working


def test_export_preserves_context_pending_and_protects_csv(tmp_path):
    store = Store(tmp_path)
    run = create(store, "=cmd()")
    store.record(run, 0, Result("https_only", "VPN egress unknown", https_ok=2))
    path = tmp_path / "results.csv"
    store.export(run, path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["network"] == "'=cmd()"
    assert rows[0]["mode"] == "demo"
    assert rows[0]["settings_verified"] == "False"
    assert rows[1]["outcome"] == "pending"
    store.export(run, tmp_path / "results.json")
    data = json.loads((tmp_path / "results.json").read_text())
    assert data["config"]["network"] == "=cmd()"
    assert len(data["results"]) == 2
