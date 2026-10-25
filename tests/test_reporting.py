import csv
from html.parser import HTMLParser
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from windscribe_lab.exportui import ExportDialog
from windscribe_lab.models import Location, Result, RunConfig, make_plan
from windscribe_lab.reporting import select_report, write_report
from windscribe_lab.storage import Store


def session(tmp_path, network="Home", notes=""):
    store = Store(tmp_path)
    plan = make_plan([Location("1", "Germany", "DE", "Frankfurt", "Castle")], {"tcp": [443, 80, 587, 21]})
    run = store.create_run(RunConfig(network=network, notes=notes), plan, "demo")
    store.record(run, 0, Result("verified", "Matching egress", latency_ms=45, https_ok=2, tunnel_verified=True))
    store.record(run, 1, Result("https_only", "VPN IP unconfirmed", https_ok=2))
    store.record(run, 2, Result("timeout", "EXCLUDED_FAILED_PAYLOAD"))
    return store, run


class DataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.data = ""
        self.external_resources = []
    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.inside = tag == "script" and attrs.get("id") == "report-data"
        if tag in ("script", "link", "img", "iframe") and ("src" in attrs or "href" in attrs):
            self.external_resources.append(attrs)
    def handle_data(self, data):
        if self.inside:
            self.data += data
    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False


@pytest.mark.parametrize("suffix", [".html", ".csv", ".json"])
def test_working_only_is_physically_filtered_for_every_format(tmp_path, suffix):
    store, run = session(tmp_path)
    path = tmp_path / ("working" + suffix)
    summary = store.export(run, path, working_only=True)
    content = path.read_text(encoding="utf-8-sig")
    assert "EXCLUDED_FAILED_PAYLOAD" not in content
    assert summary == dict(exported=2, planned=4, completed=3, working=2, verified=1, servers=1, best_latency_ms=45)
    if suffix == ".html":
        parser = DataParser()
        parser.feed(content)
        report = json.loads(parser.data)
        assert not parser.external_resources
        assert "cli_path" not in content and "anti_profiles" not in content
    elif suffix == ".json":
        report = json.loads(content)
    else:
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert {row["outcome"] for row in rows} == {"verified", "https_only"}
        return
    assert report["scope"] == "working"
    assert {row["outcome"] for row in report["results"]} == {"verified", "https_only"}
    assert not report["results"][1]["tunnel_verified"]


def test_all_report_preserves_failures_and_pending_and_snapshot_does_not_change(tmp_path):
    store, run = session(tmp_path)
    snapshot = store.report_snapshot(run)
    store.record(run, 3, Result("verified", https_ok=2, tunnel_verified=True))
    path = tmp_path / "snapshot.json"
    write_report(snapshot, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["results"][-1]["outcome"] == "pending"
    assert data["summary"]["completed"] == 3 and data["summary"]["planned"] == 4
    assert select_report(snapshot, True)["summary"]["exported"] == 2


def test_html_escapes_user_data_in_title_and_script_and_keeps_unicode(tmp_path):
    malicious = '</script><script src="https://evil.invalid/x">alert(1)</script>&\u2028تست'
    store, run = session(tmp_path, network=malicious, notes=malicious)
    path = tmp_path / "safe.html"
    store.export(run, path)
    content = path.read_text(encoding="utf-8")
    assert malicious not in content
    assert "&lt;/script&gt;" in content and "\\u003c/script\\u003e" in content
    parser = DataParser()
    parser.feed(content)
    assert not parser.external_resources
    report = json.loads(parser.data)
    assert report["network"] == malicious and report["notes"] == malicious
    assert "connect-src 'none'" in content and "nonce-" in content
    assert "innerHTML" not in content and "fetch(" not in content


def test_failed_atomic_write_preserves_existing_report_and_removes_partial_file(tmp_path):
    store, run = session(tmp_path)
    target = tmp_path / "report.html"
    target.write_text("previous report", encoding="utf-8")
    with patch("windscribe_lab.reporting.os.replace", side_effect=OSError("locked")):
        with pytest.raises(OSError, match="locked"):
            store.export(run, target)
    assert target.read_text() == "previous report"
    assert not list(tmp_path.glob(".windscribe-report-*.tmp"))


def test_zero_working_results_and_missing_latency_do_not_invent_measurements(tmp_path, qapp):
    store, run = session(tmp_path)
    snapshot = store.report_snapshot(run)
    snapshot["results"] = [snapshot["results"][1]]
    assert select_report(snapshot, True)["summary"]["best_latency_ms"] is None
    snapshot["results"] = [dict(snapshot["results"][0], outcome="timeout", tunnel_verified=False)]
    dialog = ExportDialog(snapshot)
    assert dialog.save_button.isEnabled()
    dialog.scope_combo.setCurrentIndex(1)
    assert not dialog.save_button.isEnabled() and "0 tests" in dialog.summary.text()
    path = tmp_path / "empty.html"
    write_report(snapshot, path, True)
    parser = DataParser()
    parser.feed(path.read_text(encoding="utf-8"))
    assert json.loads(parser.data)["results"] == []
    dialog.close()


def test_export_dialog_defaults_html_and_reports_scope_counts(tmp_path, qapp):
    store, run = session(tmp_path)
    dialog = ExportDialog(store.report_snapshot(run))
    assert dialog.format_combo.currentText() == "HTML report"
    assert "4 tests" in dialog.summary.text()
    dialog.scope_combo.setCurrentIndex(1)
    assert dialog.working_only and "2 tests" in dialog.summary.text()
    assert "HTTPS-only" in dialog.summary.text()
    dialog.close()


def test_csv_formulas_remain_neutralized_in_filtered_export(tmp_path):
    store, run = session(tmp_path, network="\t=cmd()", notes="+formula")
    path = tmp_path / "filtered.csv"
    store.export(run, path, True)
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["network"] == "'\t=cmd()" and rows[0]["notes"] == "'+formula"


def test_export_options_save_a_filtered_snapshot_in_background(tmp_path, qapp):
    import time
    from windscribe_lab.ui import MainWindow
    w = MainWindow(tmp_path, Path(__file__).resolve().parents[1] / "assets", demo=True)
    # Use the same Store; constructing another Store during an active test is avoided.
    plan = make_plan(w.selected_locations(), {"tcp": [443]})
    w.active_run = w.store.create_run(w.read_config(), plan, "demo")
    w.store.record(w.active_run, 0, Result("verified", https_ok=2, tunnel_verified=True))
    w.store.record(w.active_run, 1, Result("timeout", "NEVER_EXPORT_THIS_FAILURE"))
    w.reload_results()
    target = tmp_path / "ui-working.html"
    def accept_working(dialog):
        dialog.scope_combo.setCurrentIndex(1)
        return ExportDialog.DialogCode.Accepted
    with patch.object(ExportDialog, "exec", accept_working), patch("windscribe_lab.ui.QFileDialog") as picker:
        picker.return_value.exec.return_value = True
        picker.return_value.selectedFiles.return_value = [str(target)]
        w.export_report()
    deadline = time.monotonic() + 3
    while w.exporting() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.01)
    qapp.processEvents()
    assert not w.exporting() and target.exists()
    parser = DataParser()
    parser.feed(target.read_text(encoding="utf-8"))
    assert len(json.loads(parser.data)["results"]) == 1
    assert "NEVER_EXPORT_THIS_FAILURE" not in target.read_text(encoding="utf-8")
    assert w.export_button.isEnabled() and "Exported 1 tests" in w.notice.text()
    w.close()


def test_close_waits_for_atomic_report_save_and_keeps_ui_responsive(tmp_path, qapp):
    import threading
    import time
    from windscribe_lab.exportui import ExportJob
    from windscribe_lab.ui import MainWindow
    w = MainWindow(tmp_path, Path(__file__).resolve().parents[1] / "assets", demo=True)
    w.show()
    snapshot = {"session": "synthetic", "mode": "demo", "created": "2026-10-25T10:00:00+00:00",
                "config": {"network": "Test", "profile": "Test", "notes": ""}, "results": []}
    target = tmp_path / "finish-on-close.html"
    gate, started = threading.Event(), threading.Event()
    def delayed(*args):
        started.set()
        assert gate.wait(3)
        return write_report(*args)
    with patch("windscribe_lab.exportui.write_report", side_effect=delayed):
        w.export_job = ExportJob(snapshot, target)
        w.export_job.finished.connect(w.export_finished)
        w.export_job.start()
        assert started.wait(2)
        w.close()
        assert w.isVisible() and w.closing
        gate.set()
        deadline = time.monotonic() + 3
        while w.isVisible() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.01)
    assert target.exists() and not w.isVisible() and not w.exporting()
