from __future__ import annotations
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
from uuid import uuid4
import json
import math
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from .db import get_db
from . import models as m1
from . import models_v2 as m2
from . import models_v3 as m3
from . import models_v4 as m4
from .auth import get_current_user, require_roles, write_audit

router = APIRouter(prefix="/api/v4", tags=["MES V4.0 - APS / AI / Maintenance / Traceability"])


def _plant_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("PLANT_TIMEZONE", "UTC"))
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(503, "PLANT_TIMEZONE 不是有效的 IANA 时区") from exc


def _as_plant_local(dt: datetime) -> datetime:
    """Convert input timestamps to factory-local naive wall clock for shift math.

    Naive API timestamps are interpreted in PLANT_TIMEZONE; aware timestamps retain
    their explicit offset and are converted to the configured plant timezone.
    """
    tz = _plant_timezone()
    aware = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt
    return aware.astimezone(tz).replace(tzinfo=None)


def _as_utc_naive(dt: datetime) -> datetime:
    tz = _plant_timezone()
    aware = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


class APSJobIn(BaseModel):
    job_no: str = Field(min_length=3, max_length=60)
    work_order_id: int
    operation_id: int
    priority: int = Field(default=5, ge=1, le=10)
    due_at: datetime | None = None
    release_at: datetime | None = None
    required_minutes: int = Field(gt=0, le=100000)
    eligible_machine_codes: list[str] = Field(min_length=1, max_length=20)


class APSGenerateIn(BaseModel):
    horizon_start: datetime
    horizon_days: int = Field(default=7, ge=1, le=90)
    shift_start: str = "08:00"
    shift_end: str = "17:00"
    break_start: str = "12:00"
    break_end: str = "13:00"
    workdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    max_jobs: int = Field(default=200, ge=1, le=1000)


class ThresholdIn(BaseModel):
    machine_code: str = ""
    point_name: str = Field(min_length=1, max_length=100)
    warning_value: float
    critical_value: float
    direction: str = "high"
    unit: str = ""
    enabled: bool = True


class PurgeIn(BaseModel):
    days: int = Field(default=30, ge=1, le=3650)


@router.post("/maintenance/purge")
def purge_old_data(x: PurgeIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin"))):
    """管理端手动数据保留清理：删除超过保留期的设备读数、边缘消息与已结束状态段。

    工单、排程、审计与追溯等台账数据不受影响；被清理历史窗口内的 OEE 查询
    会因数据缺失标记 data_complete=false。建议通过运维计划（cron）定期调用。
    """
    cutoff = datetime.utcnow() - timedelta(days=x.days)
    readings_deleted = db.query(m2.DeviceReading).filter(
        m2.DeviceReading.device_at < cutoff,
        m2.DeviceReading.received_at < cutoff,
    ).delete(synchronize_session=False)
    messages_deleted = db.query(m3.EdgeMessage).filter(m3.EdgeMessage.received_at < cutoff).delete(synchronize_session=False)
    periods_deleted = db.query(m3.MachineStatePeriod).filter(
        m3.MachineStatePeriod.ended_at.isnot(None),
        m3.MachineStatePeriod.ended_at < cutoff,
    ).delete(synchronize_session=False)
    write_audit(db, actor, "maintenance.purge", "RetentionPolicy", "",
                f"days={x.days};readings={readings_deleted};messages={messages_deleted};periods={periods_deleted}")
    db.commit()
    return {"purged_readings": readings_deleted, "purged_edge_messages": messages_deleted,
            "purged_state_periods": periods_deleted, "cutoff_utc": cutoff.isoformat(),
            "note": "仅清理早于保留截止点的历史数据；OEE 查询窗口超过该截点时 data_complete 将为 false。"}


class TraceEventIn(BaseModel):
    event_no: str = Field(min_length=3, max_length=70)
    event_type: str = Field(min_length=2, max_length=40)
    work_order_id: int
    operation_id: int | None = None
    machine_id: int | None = None
    employee_code: str = ""
    product_code: str = ""
    material_code: str = ""
    material_lot: str = ""
    tool_code: str = ""
    good_qty: int = Field(default=0, ge=0)
    reject_qty: int = Field(default=0, ge=0)
    reference_no: str = ""
    event_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _parse_clock(value: str) -> time:
    try:
        hh, mm = value.split(":", 1)
        t = time(int(hh), int(mm))
    except Exception as exc:
        raise HTTPException(400, f"无效班次时间: {value}") from exc
    return t


def _next_work_start(dt: datetime, shift_start: time, shift_end: time, break_start: time, break_end: time, workdays: set[int]) -> datetime:
    cur = dt
    for _ in range(370):
        if cur.weekday() not in workdays:
            cur = datetime.combine(cur.date() + timedelta(days=1), shift_start)
            continue
        day_start = datetime.combine(cur.date(), shift_start)
        day_end = datetime.combine(cur.date(), shift_end)
        brk_start = datetime.combine(cur.date(), break_start)
        brk_end = datetime.combine(cur.date(), break_end)
        if cur < day_start:
            cur = day_start
        if cur >= day_end:
            cur = datetime.combine(cur.date() + timedelta(days=1), shift_start)
            continue
        if brk_start <= cur < brk_end:
            cur = brk_end
        if cur < day_end:
            return cur
        cur = datetime.combine(cur.date() + timedelta(days=1), shift_start)
    raise HTTPException(400, "排程日历搜索超过370天，检查工作日和班次配置")


def _add_work_minutes(start: datetime, minutes: int, shift_start: time, shift_end: time, break_start: time, break_end: time, workdays: set[int]) -> datetime:
    cur = start
    remaining = float(minutes)
    for _ in range(370):
        cur = _next_work_start(cur, shift_start, shift_end, break_start, break_end, workdays)
        day_end = datetime.combine(cur.date(), shift_end)
        brk_start = datetime.combine(cur.date(), break_start)
        brk_end = datetime.combine(cur.date(), break_end)
        boundaries = [b for b in (brk_start, day_end) if b > cur]
        boundary = min(boundaries) if boundaries else day_end
        usable = max(0.0, (boundary - cur).total_seconds() / 60)
        if remaining <= usable:
            return cur + timedelta(minutes=remaining)
        remaining -= usable
        if boundary == brk_start:
            cur = brk_end
        else:
            cur = datetime.combine(cur.date() + timedelta(days=1), shift_start)
    raise HTTPException(400, "排程工时超出可计算时间范围")


def _slot_for_machine(earliest: datetime, minutes: int, occupied: list[tuple[datetime, datetime]], calendar: dict) -> tuple[datetime, datetime]:
    start = earliest
    for _ in range(1000):
        end = _add_work_minutes(start, minutes, **calendar)
        conflict = next(((a, b) for a, b in sorted(occupied, key=lambda x: x[0]) if start < b and end > a), None)
        if not conflict:
            return start, end
        start = conflict[1]
    raise HTTPException(409, "排程冲突迭代次数过多")


@router.get("/aps/jobs")
def list_aps_jobs(status: str | None = None, db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    q = db.query(m4.APSJob)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(m4.APSJob.priority.desc(), m4.APSJob.due_at.asc().nullslast(), m4.APSJob.created_at.asc()).all()


@router.post("/aps/jobs")
def create_aps_job(x: APSJobIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "planner"))):
    order = db.get(m1.WorkOrder, x.work_order_id)
    op = db.get(m1.Operation, x.operation_id)
    if not order or not op or op.work_order_id != x.work_order_id:
        raise HTTPException(400, "工单或工序不存在，或工序不属于该工单")
    if db.query(m4.APSJob).filter_by(job_no=x.job_no).first():
        raise HTTPException(409, "APS job_no 已存在")
    machines = {m.code for m in db.query(m1.Machine).filter(m1.Machine.enabled.is_(True), m1.Machine.code.in_(x.eligible_machine_codes)).all()}
    if not machines:
        raise HTTPException(400, "eligible_machine_codes 中没有有效且启用的设备")
    if len(machines) != len(set(x.eligible_machine_codes)):
        raise HTTPException(400, "部分设备编码无效、重复或已停用")
    row = m4.APSJob(
        job_no=x.job_no, work_order_id=x.work_order_id, operation_id=x.operation_id,
        priority=x.priority, due_at=_as_plant_local(x.due_at) if x.due_at else None,
        release_at=_as_plant_local(x.release_at) if x.release_at else datetime.now(_plant_timezone()).replace(tzinfo=None),
        required_minutes=x.required_minutes, eligible_machine_codes=",".join(sorted(machines)), status="queued",
    )
    db.add(row); db.flush()
    write_audit(db, actor, "aps.job.create", "APSJob", row.id, x.job_no)
    db.commit(); db.refresh(row)
    return row


@router.post("/aps/generate")
def generate_aps(x: APSGenerateIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "planner"))):
    start = _as_plant_local(x.horizon_start)
    end = start + timedelta(days=x.horizon_days)
    ss, se = _parse_clock(x.shift_start), _parse_clock(x.shift_end)
    bs, be = _parse_clock(x.break_start), _parse_clock(x.break_end)
    if not (ss < se and ss <= bs < be <= se):
        raise HTTPException(400, "班次及休息时间必须满足 shift_start <= break_start < break_end <= shift_end")
    if not x.workdays or any(d not in range(7) for d in x.workdays):
        raise HTTPException(400, "workdays 必须为 0=周一 至 6=周日的非空数组")
    cal = {"shift_start": ss, "shift_end": se, "break_start": bs, "break_end": be, "workdays": set(x.workdays)}
    jobs = db.query(m4.APSJob).filter(m4.APSJob.status == "queued", m4.APSJob.release_at < end).all()
    # Preserve operation sequence within each work order. Work orders are ranked by
    # their best available priority, then earliest due date; operations inside each
    # order are topologically sequenced by the MES routing sequence.
    jobs_by_order: dict[int, list[m4.APSJob]] = {}
    for job in jobs:
        jobs_by_order.setdefault(job.work_order_id, []).append(job)
    order_rank = []
    for order_id, order_jobs in jobs_by_order.items():
        due_values = [j.due_at for j in order_jobs if j.due_at]
        order_rank.append(( -max(j.priority for j in order_jobs), min(due_values) if due_values else datetime.max, order_id, order_jobs))
    ordered_jobs = []
    for _, _, _, order_jobs in sorted(order_rank, key=lambda v: (v[0], v[1], v[2])):
        order_jobs.sort(key=lambda j: ((db.get(m1.Operation, j.operation_id).sequence if db.get(m1.Operation, j.operation_id) else 999999), j.id))
        ordered_jobs.extend(order_jobs)
    jobs = ordered_jobs[:x.max_jobs]
    run_no = "APS-" + uuid4().hex[:12].upper()
    run = m4.APSRun(run_no=run_no, horizon_start=start, horizon_end=end, strategy="priority_edd", status="draft",
                    generated_jobs=len(jobs), created_by=actor.username,
                    parameters_json=json.dumps(x.model_dump(mode="json"), ensure_ascii=False))
    db.add(run); db.flush()
    machines = {m.code: m for m in db.query(m1.Machine).filter(m1.Machine.enabled.is_(True)).all()}
    occupied: dict[int, list[tuple[datetime, datetime]]] = {m.id: [] for m in machines.values()}
    # SchedulePlan times are stored as factory-local wall clock (same convention as
    # APS assignment times), so they can be compared directly with the local horizon.
    for p in db.query(m2.SchedulePlan).filter(m2.SchedulePlan.status.in_(["approved", "in_progress"]), m2.SchedulePlan.start_at < end, m2.SchedulePlan.end_at > start).all():
        occupied.setdefault(p.machine_id, []).append((p.start_at, p.end_at))
    order_ready: dict[int, datetime] = {}
    assignments = []
    unscheduled = []
    for job in jobs:
        op = db.get(m1.Operation, job.operation_id)
        candidates = [machines[c] for c in job.eligible_machine_codes.split(",") if c in machines]
        if op and op.machine_id:
            candidates = [candidate for candidate in candidates if candidate.id == op.machine_id]
        if not candidates:
            unscheduled.append({"job_no": job.job_no, "reason": "没有可用/已启用的合格设备"})
            continue
        prior_release = order_ready.get(job.work_order_id, start)
        earliest = max(start, job.release_at, prior_release)
        choices = []
        for machine in candidates:
            s, e = _slot_for_machine(earliest, job.required_minutes, occupied.get(machine.id, []), cal)
            choices.append((e, s, machine))
        finish, slot_start, selected = min(choices, key=lambda v: (v[0], v[1], v[2].code))
        if slot_start >= end or finish > end:
            unscheduled.append({"job_no": job.job_no, "reason": "超出排程窗口"})
            continue
        reason = f"优先级={job.priority}; 最早完工规则; 合格设备={','.join(m.code for m in candidates)}"
        assignment = m4.APSAssignment(run_id=run.id, job_id=job.id, machine_id=selected.id,
                                       start_at=slot_start, end_at=finish,
                                       planned_minutes=job.required_minutes, status="draft", explanation=reason)
        db.add(assignment); assignments.append(assignment)
        job.status = "drafted"
        occupied.setdefault(selected.id, []).append((slot_start, finish))
        order_ready[job.work_order_id] = finish
    run.scheduled_jobs = len(assignments); run.unscheduled_jobs = len(unscheduled)
    run.status = "generated" if assignments else "empty"
    write_audit(db, actor, "aps.generate", "APSRun", run.id, f"run={run_no};scheduled={len(assignments)};unscheduled={len(unscheduled)}")
    db.commit()
    return {"run_id": run.id, "run_no": run.run_no, "strategy": run.strategy, "scheduled_jobs": len(assignments),
            "unscheduled_jobs": unscheduled, "assignments": [{"id": a.id, "job_id": a.job_id, "machine_id": a.machine_id,
            "start_at": a.start_at, "end_at": a.end_at, "planned_minutes": a.planned_minutes, "explanation": a.explanation} for a in assignments],
            "note": "这是可解释的启发式草案；未审批前不占用正式产能。请核实设备能力、夹具、刀具和人员后审批。"}


@router.get("/aps/runs")
def list_aps_runs(limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    return db.query(m4.APSRun).order_by(m4.APSRun.created_at.desc()).limit(limit).all()


@router.get("/aps/runs/{run_id}/assignments")
def get_aps_assignments(run_id: int, db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    if not db.get(m4.APSRun, run_id):
        raise HTTPException(404, "APS运行批次不存在")
    rows = db.query(m4.APSAssignment).filter_by(run_id=run_id).order_by(m4.APSAssignment.start_at).all()
    out = []
    for r in rows:
        job = db.get(m4.APSJob, r.job_id); machine = db.get(m1.Machine, r.machine_id)
        out.append({"id": r.id, "job_no": job.job_no, "machine_code": machine.code, "start_at": r.start_at,
                    "end_at": r.end_at, "planned_minutes": r.planned_minutes, "status": r.status,
                    "schedule_plan_id": r.schedule_plan_id, "explanation": r.explanation})
    return out


def _refresh_aps_run_status(db: Session, run_id: int) -> None:
    run = db.get(m4.APSRun, run_id)
    if not run:
        return
    rows = db.query(m4.APSAssignment).filter_by(run_id=run_id).all()
    drafts = sum(1 for row in rows if row.status == "draft")
    approved = sum(1 for row in rows if row.status == "approved")
    rejected = sum(1 for row in rows if row.status == "rejected")
    if drafts:
        run.status = "generated"
    elif approved and (rejected or run.unscheduled_jobs):
        run.status = "partially_approved"
    elif approved:
        run.status = "approved"
    elif rejected:
        run.status = "rejected"
    else:
        run.status = "empty"


@router.post("/aps/assignments/{assignment_id}/approve")
def approve_aps_assignment(assignment_id: int, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "planner"))):
    a = db.get(m4.APSAssignment, assignment_id)
    if not a:
        raise HTTPException(404, "APS分配不存在")
    if a.status != "draft":
        raise HTTPException(409, "只有草案分配可以审批")
    # Lock the machine row before the conflict check so concurrent approvals on the
    # same machine serialize; SQLite already serializes writers.
    db.get(m1.Machine, a.machine_id, with_for_update=True)
    conflict = db.query(m2.SchedulePlan).filter(
        m2.SchedulePlan.machine_id == a.machine_id,
        m2.SchedulePlan.status.in_(["approved", "in_progress"]),
        m2.SchedulePlan.start_at < a.end_at, m2.SchedulePlan.end_at > a.start_at,
    ).first()
    if conflict:
        raise HTTPException(409, f"审批时发现设备占用冲突，现有排程 ID={conflict.id}")
    job = db.get(m4.APSJob, a.job_id)
    op = db.get(m1.Operation, job.operation_id)
    if op.machine_id and op.machine_id != a.machine_id:
        raise HTTPException(409, "工序已指定其他设备，需先核实工艺设备约束")
    plan_no = "APS-" + uuid4().hex[:10].upper()
    plan = m2.SchedulePlan(plan_no=plan_no, work_order_id=job.work_order_id, machine_id=a.machine_id,
                           start_at=a.start_at, end_at=a.end_at, planned_minutes=a.planned_minutes,
                           sequence_no=op.sequence, status="approved", approved_by=actor.username, approved_at=datetime.utcnow())
    db.add(plan); db.flush()
    a.status = "approved"; a.schedule_plan_id = plan.id; job.status = "planned"
    _refresh_aps_run_status(db, a.run_id)
    write_audit(db, actor, "aps.assignment.approve", "APSAssignment", a.id, f"job={job.job_no};plan={plan_no}")
    db.commit()
    return {"approved": True, "assignment_id": a.id, "schedule_plan_id": plan.id, "plan_no": plan_no}


@router.post("/aps/assignments/{assignment_id}/reject")
def reject_aps_assignment(assignment_id: int, note: str = Query(default="", max_length=500),
                          db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "planner"))):
    a = db.get(m4.APSAssignment, assignment_id)
    if not a:
        raise HTTPException(404, "APS分配不存在")
    if a.status != "draft":
        raise HTTPException(409, "只有草案分配可以驳回")
    job = db.get(m4.APSJob, a.job_id)
    a.status = "rejected"
    job.status = "queued"
    a.explanation = (a.explanation + f"; rejected_by={actor.username}; note={note}")[:500]
    _refresh_aps_run_status(db, a.run_id)
    write_audit(db, actor, "aps.assignment.reject", "APSAssignment", a.id, f"job={job.job_no};note={note}")
    db.commit()
    return {"rejected": True, "assignment_id": a.id, "job_status": job.status}


@router.post("/maintenance/thresholds")
def create_threshold(x: ThresholdIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "maintenance"))):
    if x.direction not in {"high", "low"}:
        raise HTTPException(400, "direction 只能是 high 或 low")
    if not math.isfinite(x.warning_value) or not math.isfinite(x.critical_value):
        raise HTTPException(400, "阈值必须是有限数字")
    if x.direction == "high" and x.warning_value > x.critical_value:
        raise HTTPException(400, "高值报警时 warning_value 不能大于 critical_value")
    if x.direction == "low" and x.warning_value < x.critical_value:
        raise HTTPException(400, "低值报警时 warning_value 不能小于 critical_value")
    if x.machine_code and not db.query(m1.Machine).filter_by(code=x.machine_code).first():
        raise HTTPException(404, "设备编码不存在")
    old = db.query(m4.MaintenanceThreshold).filter_by(machine_code=x.machine_code, point_name=x.point_name).first()
    if old:
        raise HTTPException(409, "该设备/点位阈值已存在，请使用 PUT 更新")
    row = m4.MaintenanceThreshold(**x.model_dump())
    db.add(row); db.flush(); write_audit(db, actor, "maintenance.threshold.create", "MaintenanceThreshold", row.id, x.point_name)
    db.commit(); db.refresh(row); return row


@router.put("/maintenance/thresholds/{threshold_id}")
def update_threshold(threshold_id: int, x: ThresholdIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "maintenance"))):
    row = db.get(m4.MaintenanceThreshold, threshold_id)
    if not row:
        raise HTTPException(404, "维护阈值不存在")
    if x.direction not in {"high", "low"}:
        raise HTTPException(400, "direction 只能是 high 或 low")
    if not math.isfinite(x.warning_value) or not math.isfinite(x.critical_value):
        raise HTTPException(400, "阈值必须是有限数字")
    if x.direction == "high" and x.warning_value > x.critical_value:
        raise HTTPException(400, "高值报警时 warning_value 不能大于 critical_value")
    if x.direction == "low" and x.warning_value < x.critical_value:
        raise HTTPException(400, "低值报警时 warning_value 不能小于 critical_value")
    if x.machine_code and not db.query(m1.Machine).filter_by(code=x.machine_code).first():
        raise HTTPException(404, "设备编码不存在")
    duplicate = db.query(m4.MaintenanceThreshold).filter(m4.MaintenanceThreshold.id != threshold_id,
        m4.MaintenanceThreshold.machine_code == x.machine_code, m4.MaintenanceThreshold.point_name == x.point_name).first()
    if duplicate:
        raise HTTPException(409, "该设备/点位阈值配置已存在")
    for key, value in x.model_dump().items():
        setattr(row, key, value)
    write_audit(db, actor, "maintenance.threshold.update", "MaintenanceThreshold", row.id, x.point_name)
    db.commit(); db.refresh(row); return row


@router.get("/maintenance/thresholds")
def list_thresholds(db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    return db.query(m4.MaintenanceThreshold).order_by(m4.MaintenanceThreshold.machine_code, m4.MaintenanceThreshold.point_name).all()


@router.get("/maintenance/predictions")
def maintenance_predictions(window_hours: int = Query(default=72, ge=1, le=720), sample_limit: int = Query(default=20, ge=3, le=100),
                            db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    now = datetime.utcnow()
    thresholds = db.query(m4.MaintenanceThreshold).filter_by(enabled=True).all()
    machines = db.query(m1.Machine).filter_by(enabled=True).all()
    results = []
    for machine in machines:
        applicable_by_point = {}
        for threshold in thresholds:
            if threshold.machine_code in ("", machine.code):
                current = applicable_by_point.get(threshold.point_name)
                if current is None or threshold.machine_code == machine.code:
                    applicable_by_point[threshold.point_name] = threshold
        for th in applicable_by_point.values():
            readings = db.query(m2.DeviceReading).filter(
                m2.DeviceReading.machine_id == machine.id, m2.DeviceReading.point_name == th.point_name,
                m2.DeviceReading.quality == "good", m2.DeviceReading.numeric_value.isnot(None),
                m2.DeviceReading.device_at >= now - timedelta(hours=window_hours),
                m2.DeviceReading.device_at <= now,
            ).order_by(m2.DeviceReading.device_at.desc()).limit(sample_limit).all()
            readings = list(reversed(readings))
            if not readings:
                result = {"machine_code": machine.code, "point_name": th.point_name, "risk": "insufficient_data", "sample_count": 0,
                          "explanation": "配置了阈值，但所选时间窗内没有 quality=good 的数值样本。"}
                results.append(result); continue
            latest = float(readings[-1].numeric_value)
            trend = None; projected = None
            if len(readings) >= 3:
                xs = [(r.device_at - readings[0].device_at).total_seconds() / 3600 for r in readings]
                ys = [float(r.numeric_value) for r in readings]
                xm = sum(xs) / len(xs); ym = sum(ys) / len(ys)
                denom = sum((v - xm) ** 2 for v in xs)
                trend = sum((xs[i] - xm) * (ys[i] - ym) for i in range(len(xs))) / denom if denom else 0.0
                projected = latest + trend * 24
            critical = latest >= th.critical_value if th.direction == "high" else latest <= th.critical_value
            warning = latest >= th.warning_value if th.direction == "high" else latest <= th.warning_value
            crossing = projected is not None and (projected >= th.warning_value if th.direction == "high" else projected <= th.warning_value)
            projected_critical = projected is not None and (projected >= th.critical_value if th.direction == "high" else projected <= th.critical_value)
            if critical:
                risk = "critical"
            elif warning:
                risk = "warning"
            elif projected_critical:
                risk = "predictive_critical"
            elif crossing:
                risk = "predictive_warning"
            else:
                risk = "normal" if len(readings) >= 3 else "insufficient_trend"
            direction_text = "上升" if trend is not None and trend > 0 else "下降" if trend is not None and trend < 0 else "基本稳定/无法判定"
            explanation = f"当前值 {latest:g}{th.unit}；警戒阈值 {th.warning_value:g}，严重阈值 {th.critical_value:g}；样本 {len(readings)}；趋势 {direction_text}。24小时预测为线性外推，不是已训练的故障预测模型。"
            results.append({"machine_code": machine.code, "point_name": th.point_name, "latest_value": latest, "projected_24h": projected,
                            "warning_value": th.warning_value, "critical_value": th.critical_value, "risk": risk,
                            "trend_per_hour": trend, "sample_count": len(readings), "explanation": explanation})
    # Read-only endpoint: predictions are computed on demand and not persisted,
    # so polling does not grow the maintenance_predictions table. The
    # MaintenancePrediction model is retained for future snapshot histories.
    return {"evaluated_at": now, "window_hours": window_hours, "model_type": "threshold_plus_linear_trend_baseline",
            "is_machine_learning": False, "predictions": results}


class PurgeIn(BaseModel):
    days: int = Field(default=30, ge=1, le=3650)


@router.post("/maintenance/purge")
def purge_old_data(x: PurgeIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin"))):
    """管理端手动数据保留清理：删除超过保留期的设备读数、边缘消息与已结束状态段。

    审计日志、工单、排程、追溯事件等台账数据不受影响。被清理的历史窗口内
    OEE 查询会因数据缺失而标记 data_complete=false。
    """
    cutoff = datetime.utcnow() - timedelta(days=x.days)
    readings_deleted = db.query(m2.DeviceReading).filter(
        m2.DeviceReading.device_at < cutoff,
        m2.DeviceReading.received_at < cutoff,
    ).delete(synchronize_session=False)
    messages_deleted = db.query(m3.EdgeMessage).filter(m3.EdgeMessage.received_at < cutoff).delete(synchronize_session=False)
    periods_deleted = db.query(m3.MachineStatePeriod).filter(
        m3.MachineStatePeriod.ended_at.isnot(None),
        m3.MachineStatePeriod.ended_at < cutoff,
    ).delete(synchronize_session=False)
    write_audit(db, actor, "maintenance.purge", "RetentionPolicy", "",
                f"days={x.days};readings={readings_deleted};messages={messages_deleted};periods={periods_deleted}")
    db.commit()
    return {"purged_readings": readings_deleted, "purged_edge_messages": messages_deleted,
            "purged_state_periods": periods_deleted, "cutoff_utc": cutoff.isoformat(),
            "note": "仅清理早于保留截止点的历史数据；OEE 查询窗口超过该截点时 data_complete 将为 false。"}


@router.post("/ai/production-analysis/run")
def run_production_analysis(days: int = Query(default=7, ge=1, le=90), db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "planner", "quality", "maintenance"))):
    end = datetime.utcnow(); start = end - timedelta(days=days)
    reports = db.query(m1.Report).filter(m1.Report.created_at >= start, m1.Report.created_at <= end).all()
    good = sum(r.good_qty for r in reports); scrap = sum(r.scrap_qty for r in reports)
    labor = sum(r.labor_minutes for r in reports)
    ncr_open = db.query(m2.Nonconformance).filter_by(status="open").count()
    alarms_open = db.query(m3.AlarmEvent).filter(m3.AlarmEvent.status != "resolved", m3.AlarmEvent.created_at >= start).count()
    state_periods = db.query(m3.MachineStatePeriod).filter(m3.MachineStatePeriod.started_at <= end,
        m3.MachineStatePeriod.state.in_(["alarm", "maintenance"])).all()
    downtime_minutes = 0.0
    downtime_by_machine: dict[int, float] = {}
    for p in state_periods:
        right = min(end, p.ended_at or end); left = max(start, p.started_at)
        minutes = max(0.0, (right-left).total_seconds()/60)
        downtime_minutes += minutes
        downtime_by_machine[p.machine_id] = downtime_by_machine.get(p.machine_id, 0.0) + minutes
    scrap_by_operation: dict[int, dict[str, float]] = {}
    for report in reports:
        bucket = scrap_by_operation.setdefault(report.operation_id, {"good": 0.0, "scrap": 0.0, "labor": 0.0})
        bucket["good"] += report.good_qty; bucket["scrap"] += report.scrap_qty; bucket["labor"] += report.labor_minutes
    total = good + scrap
    findings = []
    recommendations = []
    if total:
        yield_pct = good / total * 100
        if yield_pct < 98:
            findings.append({"code": "YIELD_BELOW_98", "severity": "warning", "value_pct": round(yield_pct, 2), "description": "统计窗口内报工良率低于98%规则线。该规则线需按产品族校准。"})
            recommendations.append("按产品、工序、设备和刀具批次分层分析废品原因；优先复核贡献最大的缺陷代码。")
    else:
        yield_pct = None
        findings.append({"code": "NO_PRODUCTION_REPORTS", "severity": "info", "description": "统计窗口内没有生产报工，无法计算良率。"})
    if ncr_open:
        findings.append({"code": "OPEN_NCR", "severity": "warning", "count": ncr_open, "description": "存在未关闭NCR。"})
        recommendations.append("按责任人和逾期时长清理未关闭NCR，并确认返工工单与复检证据。")
    if alarms_open:
        findings.append({"code": "OPEN_MACHINE_ALARMS", "severity": "warning", "count": alarms_open, "description": "统计窗口内存在未解决设备报警。"})
        recommendations.append("对重复报警按设备和点位归并，检查传感器质量、维护工单及停机原因。")
    if downtime_minutes > 0:
        findings.append({"code": "DOWNTIME_CAPTURED", "severity": "info", "minutes": round(downtime_minutes, 1), "description": "基于设备 alarm/maintenance 状态段计算的停机时间；需校准班次口径。"})
        recommendations.append("将停机状态与维修工单、备件领用和根因分类关联，避免只统计状态而缺少原因。")
    if total and scrap / total > 0.02:
        recommendations.append("检查首件确认、刀具寿命、夹具定位和关键尺寸趋势，针对高报废工序开展专项改善。")
    if scrap_by_operation:
        op_id, op_stats = max(scrap_by_operation.items(), key=lambda item: item[1]["scrap"])
        if op_stats["scrap"] > 0:
            op = db.get(m1.Operation, op_id)
            op_total = op_stats["good"] + op_stats["scrap"]
            op_yield = op_stats["good"] / op_total * 100 if op_total else 0
            findings.append({"code": "TOP_SCRAP_OPERATION", "severity": "warning" if op_yield < 98 else "info",
                "operation_id": op_id, "operation_code": op.operation_code if op else "unknown",
                "scrap_qty": int(op_stats["scrap"]), "yield_pct": round(op_yield, 2),
                "description": "该统计窗口内报废数量最高的工序；仅基于当前报工记录。"})
            recommendations.append(f"优先检查工序 {op.operation_code if op else op_id} 的缺陷分布、刀具状态、夹具重复定位和首件检验记录。")
    if downtime_by_machine:
        for machine_id, minutes in sorted(downtime_by_machine.items(), key=lambda item: item[1], reverse=True)[:3]:
            if minutes <= 0:
                continue
            machine = db.get(m1.Machine, machine_id)
            findings.append({"code": "MACHINE_DOWNTIME_HOTSPOT", "severity": "warning" if minutes >= 120 else "info",
                "machine_id": machine_id, "machine_code": machine.code if machine else "unknown",
                "minutes": round(minutes, 1), "description": "该设备 alarm/maintenance 状态累计时间靠前；未扣除计划停机与班次。"})
        top_machine_id = max(downtime_by_machine, key=downtime_by_machine.get)
        top_machine = db.get(m1.Machine, top_machine_id)
        recommendations.append(f"复核设备 {top_machine.code if top_machine else top_machine_id} 的停机原因码与维修工单，区分故障停机、计划维护和采集异常。")
    if not recommendations:
        recommendations.append("继续积累工单、报工、设备和质量数据；达到统计样本量后再建立产品族级基线。")
    status = "critical" if any(f.get("severity") == "critical" for f in findings) else "warning" if any(f.get("severity") == "warning" for f in findings) else "info"
    summary = f"最近{days}天：合格{good}件、不合格{scrap}件、良率 {round(yield_pct,2) if yield_pct is not None else 'N/A'}%、人工{labor}分钟、未结NCR {ncr_open}、未解决报警 {alarms_open}。"
    run_no = "AI-" + uuid4().hex[:12].upper()
    row = m4.ProductionInsight(run_no=run_no, period_start=start, period_end=end, overall_status=status, summary=summary,
        findings_json=json.dumps(findings, ensure_ascii=False), recommendations_json=json.dumps(recommendations, ensure_ascii=False), created_by=actor.username)
    db.add(row); db.flush(); write_audit(db, actor, "ai.production_analysis.run", "ProductionInsight", row.id, run_no); db.commit()
    return {"id": row.id, "run_no": run_no, "engine_version": row.engine_version, "model_type": "explainable_rules_v1",
            "is_llm": False, "is_trained_ml": False, "period_start": start, "period_end": end, "overall_status": status,
            "summary": summary, "findings": findings, "recommendations": recommendations}


@router.get("/ai/production-analysis/latest")
def latest_production_analysis(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    rows = db.query(m4.ProductionInsight).order_by(m4.ProductionInsight.created_at.desc()).limit(limit).all()
    return [{"id": r.id, "run_no": r.run_no, "overall_status": r.overall_status, "summary": r.summary,
             "findings": json.loads(r.findings_json or "[]"), "recommendations": json.loads(r.recommendations_json or "[]"),
             "engine_version": r.engine_version, "created_at": r.created_at} for r in rows]


@router.post("/traceability/events")
def add_traceability_event(x: TraceEventIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "operator", "quality", "maintenance"))):
    if db.query(m4.TraceabilityEvent).filter_by(event_no=x.event_no).first():
        raise HTTPException(409, "追溯事件编号已存在")
    order = db.get(m1.WorkOrder, x.work_order_id)
    if not order:
        raise HTTPException(404, "工单不存在")
    if x.operation_id:
        op = db.get(m1.Operation, x.operation_id)
        if not op or op.work_order_id != x.work_order_id:
            raise HTTPException(400, "工序不属于该工单")
    if x.machine_id and not db.get(m1.Machine, x.machine_id):
        raise HTTPException(404, "设备不存在")
    if x.employee_code and not db.query(m2.Employee).filter_by(employee_code=x.employee_code, active=True).first():
        raise HTTPException(404, "员工不存在或已停用")
    if x.tool_code and not db.query(m1.Tool).filter_by(tool_code=x.tool_code).first():
        raise HTTPException(404, "刀具编码不存在")
    row = m4.TraceabilityEvent(event_no=x.event_no, event_type=x.event_type, work_order_id=x.work_order_id,
        operation_id=x.operation_id, machine_id=x.machine_id, employee_code=x.employee_code or "",
        product_code=x.product_code or order.product_code, material_code=x.material_code, material_lot=x.material_lot,
        tool_code=x.tool_code, good_qty=x.good_qty, reject_qty=x.reject_qty, reference_no=x.reference_no,
        event_at=_as_utc_naive(x.event_at) if x.event_at else datetime.utcnow(), metadata_json=json.dumps(x.metadata, ensure_ascii=False))
    db.add(row); db.flush(); write_audit(db, actor, "traceability.event.create", "TraceabilityEvent", row.id, x.event_no)
    db.commit(); db.refresh(row); return row


@router.get("/traceability/work-orders/{work_order_id}")
def trace_by_work_order(work_order_id: int, db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    if not db.get(m1.WorkOrder, work_order_id):
        raise HTTPException(404, "工单不存在")
    return db.query(m4.TraceabilityEvent).filter_by(work_order_id=work_order_id).order_by(m4.TraceabilityEvent.event_at, m4.TraceabilityEvent.id).all()


@router.get("/traceability/material-lots/{material_lot}")
def trace_by_material_lot(material_lot: str, db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    rows = db.query(m4.TraceabilityEvent).filter_by(material_lot=material_lot).order_by(m4.TraceabilityEvent.event_at, m4.TraceabilityEvent.id).all()
    return rows


@router.get("/dashboard")
def v4_dashboard(db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    return {
        "aps_queued_jobs": db.query(m4.APSJob).filter_by(status="queued").count(),
        "aps_draft_assignments": db.query(m4.APSAssignment).filter_by(status="draft").count(),
        "open_ncr": db.query(m2.Nonconformance).filter_by(status="open").count(),
        "open_machine_alarms": db.query(m3.AlarmEvent).filter(m3.AlarmEvent.status != "resolved").count(),
        "active_maintenance_thresholds": db.query(m4.MaintenanceThreshold).filter_by(enabled=True).count(),
        "traceability_events": db.query(m4.TraceabilityEvent).count(),
    }
