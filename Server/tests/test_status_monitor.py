import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.status_monitor import StatusMonitor


def _make_message(topic: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(topic=topic, payload=json.dumps(payload).encode("utf-8"))


def test_snapshot_event_marks_node_online(monkeypatch):
    monitor = StatusMonitor(timeout=30)

    snapshot_msg = _make_message(
        "ul/node-1/evt/status",
        {"event": "snapshot"},
    )
    monitor._on_message(monitor.client, None, snapshot_msg)

    info = monitor.snapshot().get("node-1")
    assert info is not None
    assert info["online"] is True
    assert info["last_snapshot"] is not None

    # Age the snapshot beyond the timeout to ensure it no longer counts.
    node_snapshot_time = info["last_snapshot"]
    assert node_snapshot_time is not None
    monitor._last_snapshot["node-1"] = node_snapshot_time - 31

    aged = monitor.snapshot().get("node-1")
    assert aged is not None
    assert aged["online"] is False
