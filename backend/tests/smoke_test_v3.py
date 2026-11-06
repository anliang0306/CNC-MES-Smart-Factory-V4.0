"""Standalone V3 integration smoke test using a disposable SQLite database.
Run: cd backend && python tests/smoke_test_v3.py
"""
from __future__ import annotations
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile

BACKEND = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="cnc_mes_v3_") as tmp:
    db_path = Path(tmp) / "smoke.db"
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{db_path}",
        "TOKEN_SECRET": "smoke-test-token-secret-0123456789-abcdef",
        "EDGE_INGEST_TOKEN": "smoke-test-edge-token-0123456789-abcdef",
        "MES_ADMIN_USER": "admin",
        "MES_ADMIN_PASSWORD": "AdminPassword123!",
        "AUTO_CREATE_SCHEMA": "false",
    })
    subprocess.run([sys.executable, "migrate.py"], cwd=BACKEND, env=env, check=True)
    os.environ.update(env)
    sys.path.insert(0, str(BACKEND))
    sys.path.insert(0, str(BACKEND.parent))

    from fastapi.testclient import TestClient
    from app.db import engine, Session
    from app import models_v2 as m2, models_v3 as m3
    from app.main import app
    from edge_gateway.adapters.mock import MockAdapter
    from edge_gateway.outbox import Outbox
    from edge_gateway.__main__ import flush_outbox

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    login = client.post("/api/v2/auth/login", json={"username": "admin", "password": "AdminPassword123!"})
    assert login.status_code == 200, login.text
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    edge_headers = {"X-Edge-Token": env["EDGE_INGEST_TOKEN"]}

    machine = client.post("/api/v1/machines", headers=user_headers, json={
        "code": "CNC-SMOKE", "name": "V3冒烟测试机", "controller": "FANUC", "status": "unknown"
    })
    assert machine.status_code == 200, machine.text
    machine_id = machine.json()["id"]

    rule = client.post("/api/v3/alarms/rules", headers=user_headers, json={
        "rule_code": "LOAD-HI", "machine_code": "CNC-SMOKE", "point_name": "spindle_load_pct",
        "operator": ">", "threshold": 80, "severity": "high", "title": "主轴负载过高",
        "detail_template": "{machine} load={value}; limit={threshold}", "cooldown_seconds": 300,
    })
    assert rule.status_code == 200, rule.text

    start = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(minutes=10)
    def send(message_id: str, at: datetime, state: str, good: int, reject: int, load: float):
        return client.post("/api/v3/edge/ingest", headers=edge_headers, json={
            "message_id": message_id, "machine_code": "CNC-SMOKE", "timestamp": at.isoformat(),
            "state": state, "state_reason": "smoke test", "metrics": [
                {"name": "good_count_total", "value": good, "quality": "good"},
                {"name": "reject_count_total", "value": reject, "quality": "good"},
                {"name": "ideal_cycle_seconds", "value": 30, "quality": "good"},
                {"name": "spindle_load_pct", "value": load, "quality": "good"},
            ]
        })

    first = send("smoke-message-0001", start, "ACTIVE", 100, 0, 45)
    assert first.status_code == 200, first.text
    duplicate_payload = {
        "message_id": "smoke-message-0001", "machine_code": "CNC-SMOKE", "timestamp": start.isoformat(),
        "state": "ACTIVE", "state_reason": "smoke test", "metrics": [
            {"name": "good_count_total", "value": 100, "quality": "good"},
            {"name": "reject_count_total", "value": 0, "quality": "good"},
            {"name": "ideal_cycle_seconds", "value": 30, "quality": "good"},
            {"name": "spindle_load_pct", "value": 45, "quality": "good"},
        ]
    }
    duplicate = client.post("/api/v3/edge/ingest", headers=edge_headers, json=duplicate_payload)
    assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True, duplicate.text
    conflict_payload = dict(duplicate_payload)
    conflict_payload["metrics"] = [dict(row) for row in duplicate_payload["metrics"]]
    conflict_payload["metrics"][0]["value"] = 999
    reused_id = client.post("/api/v3/edge/ingest", headers=edge_headers, json=conflict_payload)
    assert reused_id.status_code == 409, reused_id.text
    second = send("smoke-message-0002", start + timedelta(minutes=10), "idle", 110, 1, 95)
    assert second.status_code == 200, second.text

    alarms = client.get("/api/v3/alarms/events", headers=user_headers)
    assert alarms.status_code == 200 and any(row["title"] == "主轴负载过高" for row in alarms.json())
    bad_quality = client.post("/api/v3/edge/ingest", headers=edge_headers, json={
        "message_id": "smoke-message-badq1", "machine_code": "CNC-SMOKE", "timestamp": (start + timedelta(minutes=10, seconds=1)).isoformat(),
        "metrics": [{"name": "spindle_load_pct", "value": 99, "quality": "bad"}]
    })
    assert bad_quality.status_code == 200, bad_quality.text
    alarms_after_bad_quality = client.get("/api/v3/alarms/events", headers=user_headers)
    assert len(alarms_after_bad_quality.json()) == len(alarms.json()), alarms_after_bad_quality.text
    oee = client.get("/api/v3/oee/machines/" + str(machine_id), headers=user_headers, params={
        "start": start.isoformat(), "end": (start + timedelta(minutes=10)).isoformat()
    })
    assert oee.status_code == 200, oee.text
    result = oee.json()
    assert result["good_qty"] == 10 and result["reject_qty"] == 1, result
    assert result["availability_pct"] == 100.0 and result["performance_pct"] == 55.0, result
    assert result["oee_pct"] == 50.0 and result["data_complete"] is True and result["shift_calendar_applied"] is False, result

    heartbeat = client.post("/api/v3/edge/heartbeat", headers=edge_headers, json={
        "gateway_id": "GW-SMOKE", "version": "3.0.0", "ip_address": "127.0.0.1"
    })
    assert heartbeat.status_code == 200, heartbeat.text
    overview = client.get("/api/v3/monitor/overview", headers=user_headers)
    assert overview.status_code == 200 and overview.json()["total_machines"] == 1

    # A still-open running period must stop accruing after 90s without valid telemetry.
    stale_machine = client.post("/api/v1/machines", headers=user_headers, json={
        "code": "CNC-STALE", "name": "断线计时保护测试机", "controller": "FANUC", "status": "unknown"
    })
    assert stale_machine.status_code == 200, stale_machine.text
    stale_start = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(hours=1)
    stale_event = client.post("/api/v3/edge/ingest", headers=edge_headers, json={
        "message_id": "stale-period-000001", "machine_code": "CNC-STALE", "timestamp": stale_start.isoformat(),
        "state": "running", "metrics": [{"name": "spindle_load_pct", "value": 50, "quality": "good"}]
    })
    assert stale_event.status_code == 200, stale_event.text
    stale_oee = client.get(f"/api/v3/oee/machines/{stale_machine.json()['id']}", headers=user_headers, params={
        "start": stale_start.isoformat(), "end": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)).isoformat()
    })
    assert stale_oee.status_code == 200 and stale_oee.json()["operating_seconds"] < 3700, stale_oee.text

    bad_token = client.post("/api/v3/edge/heartbeat", json={"gateway_id": "GW-BAD"})
    assert bad_token.status_code == 401

    mock = asyncio.run(MockAdapter().read({"machine_code": "CNC-MOCK"}))
    assert mock["state"] in {"running", "idle"} and len(mock["metrics"]) >= 5
    outbox_path = Path(tmp) / "edge-outbox.db"
    queue = Outbox(str(outbox_path))
    queue.enqueue(mock)
    assert queue.count() == 1 and queue.pending()[0]["message_id"] == mock["message_id"]
    queue = Outbox(str(outbox_path))  # new instance simulates gateway restart
    assert queue.count() == 1

    class FakeResponse:
        def raise_for_status(self):
            return None
    class FakeClient:
        async def post(self, *args, **kwargs):
            return FakeResponse()
    class FakeFailClient:
        async def post(self, *args, **kwargs):
            raise RuntimeError("simulated API outage")
    queue.enqueue(mock)
    failed_delivery = asyncio.run(flush_outbox(FakeFailClient(), "http://mes.invalid", "test-edge-token", queue))
    assert failed_delivery == 0 and queue.count() == 1 and queue.pending()[0]["attempts"] == 1
    delivered = asyncio.run(flush_outbox(FakeClient(), "http://mes.invalid", "test-edge-token", queue))
    assert delivered == 1 and queue.count() == 0

    # 死信：4xx 为永久性错误，应直接转入死信且不阻塞队尾消息
    class FakePermaClient:
        async def post(self, *args, **kwargs):
            exc = RuntimeError("404: 设备编码不存在")
            exc.response = type("R", (), {"status_code": 404})()
            raise exc
    mock2 = asyncio.run(MockAdapter().read({"machine_code": "CNC-MOCK"}))
    q_perm = Outbox(str(Path(tmp) / "outbox-perm.db"))
    q_perm.enqueue(mock)
    q_perm.enqueue(mock2)
    d1 = asyncio.run(flush_outbox(FakePermaClient(), "http://mes.invalid", "test-edge-token", q_perm, max_attempts=3))
    assert d1 == 0 and q_perm.pending() == [] and len(q_perm.dead_letters()) == 2, q_perm.stats()
    # 死信消息不再参与重投
    d2 = asyncio.run(flush_outbox(FakeClient(), "http://mes.invalid", "test-edge-token", q_perm))
    assert d2 == 0 and len(q_perm.dead_letters()) == 2
    # 瞬时错误重试到上限后才转死信
    q_tr = Outbox(str(Path(tmp) / "outbox-tr.db"))
    q_tr.enqueue(mock)
    asyncio.run(flush_outbox(FakeFailClient(), "http://mes.invalid", "test-edge-token", q_tr, max_attempts=2))
    assert len(q_tr.pending()) == 1, q_tr.stats()
    asyncio.run(flush_outbox(FakeFailClient(), "http://mes.invalid", "test-edge-token", q_tr, max_attempts=2))
    assert q_tr.pending() == [] and len(q_tr.dead_letters()) == 1, q_tr.stats()

    # 登录限流：连续 5 次失败后第 6 次返回 429
    for i in range(5):
        rl = client.post("/api/v2/auth/login", json={"username": "rl-target", "password": "wrong-pass-12345"})
        assert rl.status_code == 401, rl.text
    rl_blocked = client.post("/api/v2/auth/login", json={"username": "rl-target", "password": "wrong-pass-12345"})
    assert rl_blocked.status_code == 429, rl_blocked.text

    # V1 遗留接口角色检查：viewer 可读，但写操作必须 403
    viewer = client.post("/api/v2/auth/users", headers=user_headers, json={
        "username": "viewer3", "password": "ViewerPass!2345", "role": "viewer"})
    assert viewer.status_code == 200, viewer.text
    vlogin = client.post("/api/v2/auth/login", json={"username": "viewer3", "password": "ViewerPass!2345"})
    vheaders = {"Authorization": f"Bearer {vlogin.json()['access_token']}"}
    assert client.get("/api/v1/machines", headers=vheaders).status_code == 200
    v403 = client.post("/api/v1/machines", headers=vheaders, json={"code": "CNC-VIEW", "name": "viewer试写", "controller": "X"})
    assert v403.status_code == 403, v403.text

    # OEE 班次日历：2026-11-09 是周一；班次 08:00-17:00 休息 12:00-13:00（工厂时区未设 → UTC）
    mid = stale_machine.json()["id"]
    base = datetime(2026, 11, 9)
    with Session() as db:
        db.add_all([
            m3.MachineStatePeriod(machine_id=mid, state="planned_stop",
                started_at=base + timedelta(hours=8), ended_at=base + timedelta(hours=9),
                reason="smoke", source="smoke", event_id="shift-planned-stop"),
            m3.MachineStatePeriod(machine_id=mid, state="running",
                started_at=base + timedelta(hours=9), ended_at=base + timedelta(hours=12),
                reason="smoke", source="smoke", event_id="shift-run-1"),
            m3.MachineStatePeriod(machine_id=mid, state="idle",
                started_at=base + timedelta(hours=12), ended_at=base + timedelta(hours=13),
                reason="smoke", source="smoke", event_id="shift-idle"),
            m3.MachineStatePeriod(machine_id=mid, state="running",
                started_at=base + timedelta(hours=13), ended_at=base + timedelta(hours=16),
                reason="smoke", source="smoke", event_id="shift-run-2"),
            m3.MachineStatePeriod(machine_id=mid, state="idle",
                started_at=base + timedelta(hours=16), ended_at=base + timedelta(hours=17),
                reason="smoke", source="smoke", event_id="shift-idle-2"),
        ])
        db.add_all([
            m2.DeviceReading(machine_id=mid, point_name="good_count_total", numeric_value=200.0,
                             quality="good", device_at=base + timedelta(hours=8, minutes=5), source="smoke"),
            m2.DeviceReading(machine_id=mid, point_name="good_count_total", numeric_value=210.0,
                             quality="good", device_at=base + timedelta(hours=16, minutes=30), source="smoke"),
            m2.DeviceReading(machine_id=mid, point_name="reject_count_total", numeric_value=10.0,
                             quality="good", device_at=base + timedelta(hours=8, minutes=5), source="smoke"),
            m2.DeviceReading(machine_id=mid, point_name="reject_count_total", numeric_value=20.0,
                             quality="good", device_at=base + timedelta(hours=16, minutes=30), source="smoke"),
            m2.DeviceReading(machine_id=mid, point_name="ideal_cycle_seconds", numeric_value=10.0,
                             quality="good", device_at=base + timedelta(hours=8, minutes=5), source="smoke"),
        ])
        db.commit()
    oee_shift = client.get(f"/api/v3/oee/machines/{mid}", headers=user_headers, params={
        "start": "2026-11-09T00:00:00", "end": "2026-11-10T00:00:00",
        "shift_start": "08:00", "shift_end": "17:00", "break_start": "12:00", "break_end": "13:00",
        "workdays": [0, 1, 2, 3, 4],
    })
    assert oee_shift.status_code == 200, oee_shift.text
    s = oee_shift.json()
    assert s["shift_calendar_applied"] is True, s
    assert s["planned_seconds"] == 25200.0, s      # 8h 班次 - 1h 计划停机
    assert s["operating_seconds"] == 21600.0, s    # 班内 3h + 3h 运行
    assert s["availability_pct"] == 85.71, s
    assert s["good_qty"] == 10 and s["reject_qty"] == 10, s
    assert s["data_complete"] is True, s
    oee_legacy = client.get(f"/api/v3/oee/machines/{mid}", headers=user_headers, params={
        "start": "2026-11-09T00:00:00", "end": "2026-11-10T00:00:00"})
    l = oee_legacy.json()
    assert l["shift_calendar_applied"] is False and l["planned_seconds"] == 82800.0, l
    bad_cal = client.get(f"/api/v3/oee/machines/{mid}", headers=user_headers, params={
        "start": "2026-11-09T00:00:00", "shift_start": "08:00"})
    assert bad_cal.status_code == 400, bad_cal.text

    print("V3_SMOKE_TEST_PASSED")
    print({"machine_id": machine_id, "oee": result["oee_pct"], "alarm_events": len(alarms.json()),
           "dead_letter": "ok", "rate_limit": "ok", "v1_roles": "ok", "oee_shift": s["availability_pct"]})
    engine.dispose()  # release SQLite handles so the temp dir can be removed on Windows
