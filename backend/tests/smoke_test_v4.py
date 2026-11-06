"""V4 integration smoke test. Run from backend: PYTHONPATH=. python tests/smoke_test_v4.py"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="cnc_mes_v4_") as tmp:
    db_path = Path(tmp) / "smoke.db"
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{db_path}",
        "TOKEN_SECRET": "smoke-test-token-secret-0123456789-abcdef",
        "EDGE_INGEST_TOKEN": "smoke-test-edge-token-0123456789-abcdef",
        "MES_ADMIN_USER": "admin", "MES_ADMIN_PASSWORD": "AdminPassword123!",
        "AUTO_CREATE_SCHEMA": "false", "PLANT_TIMEZONE": "Asia/Tokyo",
    })
    subprocess.run([sys.executable, "migrate.py"], cwd=BACKEND, env=env, check=True)
    os.environ.update(env)
    sys.path.insert(0, str(BACKEND))

    from fastapi.testclient import TestClient
    from app.db import Session, engine
    from app import models as m1, models_v2 as m2, models_v3 as m3
    from app.main import app

    client = TestClient(app)
    login = client.post("/api/v2/auth/login", json={"username": "admin", "password": "AdminPassword123!"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    with Session() as db:
        db.add_all([
            m1.Machine(code="CNC-01", name="CNC 一号机", controller="FANUC"),
            m1.Machine(code="CNC-02", name="CNC 二号机", controller="SIEMENS"),
        ])
        db.flush()
        order = m1.WorkOrder(order_no="WO-V4-001", product_code="PART-X", product_name="V4测试件", planned_qty=100)
        db.add(order); db.flush()
        op1 = m1.Operation(work_order_id=order.id, sequence=10, operation_code="OP10", operation_name="粗加工", standard_minutes=60)
        op2 = m1.Operation(work_order_id=order.id, sequence=20, operation_code="OP20", operation_name="精加工", standard_minutes=40)
        db.add_all([op1, op2, m2.Employee(employee_code="EMP-V4", full_name="Test Operator", department="CNC", skill_level="L2")])
        db.flush()
        db.add(m1.Report(work_order_id=order.id, operation_id=op1.id, machine_id=1, employee_code="EMP-V4", good_qty=50, scrap_qty=3, labor_minutes=120))
        now = datetime.utcnow()
        for index, value in enumerate([40.0, 45.0, 50.0]):
            db.add(m2.DeviceReading(machine_id=1, point_name="spindle_temp_c", numeric_value=value, quality="good", device_at=now - timedelta(hours=2-index), source="test"))
        db.commit()
        order_id, op1_id, op2_id = order.id, op1.id, op2.id

    for job in [
        {"job_no": "J-OP10", "work_order_id": order_id, "operation_id": op1_id, "priority": 8, "required_minutes": 120, "eligible_machine_codes": ["CNC-01", "CNC-02"]},
        {"job_no": "J-OP20", "work_order_id": order_id, "operation_id": op2_id, "priority": 8, "required_minutes": 90, "eligible_machine_codes": ["CNC-01", "CNC-02"]},
    ]:
        result = client.post("/api/v4/aps/jobs", json=job, headers=headers)
        assert result.status_code == 200, result.text

    generated = client.post("/api/v4/aps/generate", headers=headers, json={
        "horizon_start": "2026-11-09T08:00:00", "horizon_days": 5,
        "shift_start": "08:00", "shift_end": "17:00", "break_start": "12:00", "break_end": "13:00",
        "workdays": [0, 1, 2, 3, 4],
    })
    assert generated.status_code == 200, generated.text
    assert generated.json()["scheduled_jobs"] == 2, generated.json()
    assignments = generated.json()["assignments"]
    repeat_run = client.post("/api/v4/aps/generate", headers=headers, json={
        "horizon_start": "2026-11-09T08:00:00", "horizon_days": 5,
        "shift_start": "08:00", "shift_end": "17:00", "break_start": "12:00", "break_end": "13:00",
        "workdays": [0, 1, 2, 3, 4],
    })
    assert repeat_run.status_code == 200 and repeat_run.json()["scheduled_jobs"] == 0, repeat_run.text
    assert assignments[0]["start_at"] < assignments[1]["start_at"], assignments
    assert assignments[0]["end_at"] <= assignments[1]["start_at"], assignments
    # APS and V2 schedule rows share a factory-local wall-clock convention.
    assert assignments[0]["start_at"].startswith("2026-11-09T08:00:00"), assignments[0]
    with Session() as db:
        db.add(m2.SchedulePlan(plan_no="V4-CONFLICT-PLAN", work_order_id=order_id,
            machine_id=assignments[0]["machine_id"], start_at=datetime.fromisoformat(assignments[0]["start_at"]),
            end_at=datetime.fromisoformat(assignments[0]["end_at"]), planned_minutes=120, status="approved"))
        db.commit()
    conflict = client.post(f"/api/v4/aps/assignments/{assignments[0]['id']}/approve", headers=headers)
    assert conflict.status_code == 409, conflict.text
    with Session() as db:
        existing = db.query(m2.SchedulePlan).filter_by(plan_no="V4-CONFLICT-PLAN").first()
        existing.status = "cancelled"
        db.commit()
    approved = client.post(f"/api/v4/aps/assignments/{assignments[0]['id']}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    rejected = client.post(f"/api/v4/aps/assignments/{assignments[1]['id']}/reject?note=capacity-review", headers=headers)
    assert rejected.status_code == 200 and rejected.json()["job_status"] == "queued", rejected.text

    # Regression: generate must tolerate pre-existing approved SchedulePlan rows in the
    # window. An approved plan now exists (from the approval above), so this exercises the
    # occupied-window merge that previously raised NameError: _utc_naive_to_plant.
    regen = client.post("/api/v4/aps/generate", headers=headers, json={
        "horizon_start": "2026-11-09T08:00:00", "horizon_days": 5,
        "shift_start": "08:00", "shift_end": "17:00", "break_start": "12:00", "break_end": "13:00",
        "workdays": [0, 1, 2, 3, 4],
    })
    assert regen.status_code == 200, regen.text
    regen_body = regen.json()
    assert isinstance(regen_body["assignments"], list), regen_body
    approved_machines = {assignments[0]["machine_id"]}
    approved_window = (assignments[0]["start_at"], assignments[0]["end_at"])
    for a in regen_body["assignments"]:
        if a["machine_id"] in approved_machines:
            assert not (a["start_at"] < approved_window[1] and a["end_at"] > approved_window[0]), \
                f"new draft overlaps approved plan on machine {a['machine_id']}: {a}"

    threshold = client.post("/api/v4/maintenance/thresholds", headers=headers, json={
        "machine_code": "CNC-01", "point_name": "spindle_temp_c", "warning_value": 55,
        "critical_value": 75, "direction": "high", "unit": "C",
    })
    assert threshold.status_code == 200, threshold.text
    update_threshold = client.put(f"/api/v4/maintenance/thresholds/{threshold.json()['id']}", headers=headers, json={
        "machine_code": "CNC-01", "point_name": "spindle_temp_c", "warning_value": 55,
        "critical_value": 75, "direction": "high", "unit": "C", "enabled": True,
    })
    assert update_threshold.status_code == 200, update_threshold.text
    predictions = client.get("/api/v4/maintenance/predictions?window_hours=24", headers=headers)
    assert predictions.status_code == 200, predictions.text
    assert any(p["risk"] in {"predictive_warning", "predictive_critical"} for p in predictions.json()["predictions"]), predictions.json()

    analysis = client.post("/api/v4/ai/production-analysis/run?days=7", headers=headers)
    assert analysis.status_code == 200 and analysis.json()["model_type"] == "explainable_rules_v1", analysis.text
    event = client.post("/api/v4/traceability/events", headers=headers, json={
        "event_no": "TRACE-V4-001", "event_type": "operation_complete", "work_order_id": order_id,
        "operation_id": op1_id, "machine_id": 1, "employee_code": "EMP-V4", "product_code": "PART-X",
        "material_code": "AL6061", "material_lot": "LOT-TEST-01", "good_qty": 50, "reject_qty": 3,
    })
    assert event.status_code == 200, event.text
    trace = client.get(f"/api/v4/traceability/work-orders/{order_id}", headers=headers)
    assert trace.status_code == 200 and len(trace.json()) == 1, trace.text

    # Retention purge: admin-only; removes only rows whose data predates the cutoff.
    from datetime import timedelta as _td
    old_dt = datetime.utcnow() - _td(days=30)
    with Session() as db:
        db.add_all([
            m2.DeviceReading(machine_id=1, point_name="purge_probe", numeric_value=1.0,
                             quality="good", device_at=old_dt, received_at=old_dt, source="test"),
            m3.EdgeMessage(message_id="purge-old-msg", machine_id=1, payload_hash="0" * 64,
                           received_at=old_dt, source="test"),
            m3.MachineStatePeriod(machine_id=1, state="running", started_at=old_dt - _td(hours=1),
                                  ended_at=old_dt, reason="purge test", source="test",
                                  event_id="purge-period-1"),
            m2.DeviceReading(machine_id=1, point_name="purge_probe", numeric_value=2.0,
                             quality="good", device_at=datetime.utcnow(), source="test"),
        ])
        db.commit()
    purge = client.post("/api/v4/maintenance/purge", headers=headers, json={"days": 2})
    assert purge.status_code == 200, purge.text
    pb = purge.json()
    assert pb["purged_readings"] == 1 and pb["purged_edge_messages"] == 1 and pb["purged_state_periods"] == 1, pb
    probe = client.get("/api/v3/monitor/machines/1/readings?point_name=purge_probe", headers=headers)
    assert probe.status_code == 200 and len(probe.json()) == 1, probe.text

    created_user = client.post("/api/v2/auth/users", headers=headers, json={
        "username": "operator_v4", "password": "OperatorPass!234", "role": "operator"})
    assert created_user.status_code == 200, created_user.text
    operator_login = client.post("/api/v2/auth/login", json={"username": "operator_v4", "password": "OperatorPass!234"})
    operator_headers = {"Authorization": f"Bearer {operator_login.json()['access_token']}"}
    role_denied = client.post("/api/v4/aps/generate", headers=operator_headers, json={"horizon_start": "2026-11-09T08:00:00"})
    assert role_denied.status_code == 403, role_denied.text

    print("V4_SMOKE_TEST_PASSED")
    print({"aps_jobs": 2, "aps_precedence": "ok", "approval": "ok", "maintenance": [p["risk"] for p in predictions.json()["predictions"]], "ai_analysis": "ok", "traceability": "ok", "existing_plan_regression": "ok"})
    engine.dispose()  # release SQLite handles so the temp dir can be removed on Windows
