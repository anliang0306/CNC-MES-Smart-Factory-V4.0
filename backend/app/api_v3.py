from datetime import datetime, time, timedelta
from hashlib import sha256
import hmac
import json
import os
import math
from typing import Any
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from .db import get_db
from . import models as m1
from . import models_v2 as m2
from . import models_v3 as m3
from .auth import get_current_user, require_roles, write_audit

router = APIRouter(prefix="/api/v3", tags=["MES V3.0 - Edge / OEE"])
VALID_STATES = {"running", "idle", "setup", "alarm", "maintenance", "planned_stop", "offline", "unknown"}
STATE_ALIASES = {
    "execute": "running", "executing": "running", "cycle": "running", "in_cycle": "running",
    "ready": "idle", "standby": "idle", "stopped": "idle", "stop": "idle",
    "changeover": "setup", "setting": "setup", "fault": "alarm", "error": "alarm",
    "pm": "maintenance", "service": "maintenance", "disconnect": "offline", "active": "running", "program": "running",
    "interrupted": "alarm", "emergency_stop": "alarm", "e_stop": "alarm", "feed_hold": "idle",
}

class EdgeMetric(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: float | str | bool
    quality: str = Field(default="good", max_length=30)

class EdgeAlarmIn(BaseModel):
    code: str = Field(default="EDGE_ALARM", max_length=40)
    severity: str = Field(default="medium", max_length=20)
    title: str = Field(default="设备报警", max_length=160)
    detail: str = Field(default="", max_length=4000)

class EdgeIngestIn(BaseModel):
    message_id: str = Field(min_length=8, max_length=80)
    machine_code: str = Field(min_length=1, max_length=40)
    timestamp: datetime
    state: str | None = None
    state_reason: str = Field(default="", max_length=240)
    metrics: list[EdgeMetric] = Field(default_factory=list, max_length=100)
    alarms: list[EdgeAlarmIn] = Field(default_factory=list, max_length=30)
    source: str = Field(default="edge-gateway", max_length=50)

class HeartbeatIn(BaseModel):
    gateway_id: str = Field(min_length=2, max_length=80)
    version: str = Field(default="", max_length=40)
    ip_address: str = Field(default="", max_length=80)
    message: str = Field(default="", max_length=240)

class AlarmRuleIn(BaseModel):
    rule_code: str = Field(min_length=1, max_length=60)
    machine_code: str = Field(default="", max_length=40)
    point_name: str = Field(min_length=1, max_length=100)
    operator: str = Field(min_length=1, max_length=2)
    threshold: float
    severity: str = Field(default="medium", max_length=20)
    title: str = Field(min_length=1, max_length=160)
    detail_template: str = Field(default="", max_length=240)
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    enabled: bool = True

class AlarmResolveIn(BaseModel):
    note: str = Field(default="", max_length=4000)


def _verify_edge_token(token: str | None):
    expected = os.getenv("EDGE_INGEST_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(503, "EDGE_INGEST_TOKEN 未配置为至少32位随机值")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(401, "边缘网关令牌无效")


def _normalize_state(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower().replace(" ", "_").replace("-", "_")
    value = STATE_ALIASES.get(value, value)
    if value not in VALID_STATES:
        return "unknown"
    return value


def _rule_matches(operator: str, value: float, threshold: float) -> bool:
    return {
        ">": value > threshold, ">=": value >= threshold,
        "<": value < threshold, "<=": value <= threshold,
        "==": value == threshold, "!=": value != threshold,
    }[operator]


def _intersect_seconds(start: datetime, end: datetime, a: datetime, b: datetime | None) -> float:
    left = max(start, a)
    right = min(end, b or end)
    return max(0.0, (right - left).total_seconds())


def _parse_clock(value: str, name: str) -> time:
    try:
        hh, mm = value.split(":", 1)
        t = time(int(hh), int(mm))
    except Exception as exc:
        raise HTTPException(400, f"无效班次时间: {name}={value}") from exc
    return t


def _plant_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("PLANT_TIMEZONE", "UTC"))
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(503, "PLANT_TIMEZONE 不是有效的 IANA 时区") from exc


def _utc_naive_to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)


def _shift_work_segments(start: datetime, end: datetime, shift_start: time, shift_end: time,
                         break_start: time, break_end: time, workdays: set[int]) -> list[tuple[datetime, datetime]]:
    """工厂本地墙钟下 [start, end) 内、扣除休息后的班次工时区间列表。"""
    segments: list[tuple[datetime, datetime]] = []
    day = start.date()
    last_day = end.date()
    days_seen = 0
    while day <= last_day:
        days_seen += 1
        if days_seen > 100:
            raise HTTPException(400, "班次日历计算超出窗口上限")
        if day.weekday() in workdays:
            day_start = datetime.combine(day, shift_start)
            day_end = datetime.combine(day, shift_end)
            brk_start = datetime.combine(day, break_start)
            brk_end = datetime.combine(day, break_end)
            for part_start, part_end in ((day_start, brk_start), (brk_end, day_end)):
                lo = max(start, part_start)
                hi = min(end, part_end)
                if hi > lo:
                    segments.append((lo, hi))
        day += timedelta(days=1)
    return segments


def _counter_delta(db: Session, machine_id: int, point_name: str, start: datetime, end: datetime) -> float:
    before = db.query(m2.DeviceReading).filter(
        m2.DeviceReading.machine_id == machine_id,
        m2.DeviceReading.point_name == point_name,
        m2.DeviceReading.numeric_value.isnot(None),
        m2.DeviceReading.device_at <= start,
        m2.DeviceReading.quality == "good",
    ).order_by(m2.DeviceReading.device_at.desc()).first()
    inside = db.query(m2.DeviceReading).filter(
        m2.DeviceReading.machine_id == machine_id,
        m2.DeviceReading.point_name == point_name,
        m2.DeviceReading.numeric_value.isnot(None),
        m2.DeviceReading.device_at > start,
        m2.DeviceReading.device_at <= end,
        m2.DeviceReading.quality == "good",
    ).order_by(m2.DeviceReading.device_at.asc()).all()
    if not inside:
        return 0.0
    first = float(before.numeric_value) if before else float(inside[0].numeric_value)
    last = float(inside[-1].numeric_value)
    return max(0.0, last - first) if last >= first else max(0.0, last)


@router.post("/edge/ingest")
def ingest_edge(x: EdgeIngestIn, x_edge_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    _verify_edge_token(x_edge_token)
    # Lock the machine row so concurrent ingest batches for the same machine
    # serialize their state-period updates (a no-op on SQLite).
    machine = db.query(m1.Machine).filter_by(code=x.machine_code).with_for_update().first()
    if not machine:
        raise HTTPException(404, f"设备编码不存在: {x.machine_code}")
    raw = x.model_dump(mode="json")
    body_hash = sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    old = db.query(m3.EdgeMessage).filter_by(message_id=x.message_id).first()
    if old:
        if old.payload_hash != body_hash:
            raise HTTPException(409, "message_id 已被不同内容使用，拒绝覆盖")
        return {"accepted": True, "duplicate": True, "machine_code": machine.code}

    event_time = x.timestamp.astimezone(timezone.utc).replace(tzinfo=None) if x.timestamp.tzinfo else x.timestamp
    msg = m3.EdgeMessage(message_id=x.message_id, machine_id=machine.id, payload_hash=body_hash, source=x.source)
    db.add(msg)
    state = _normalize_state(x.state)
    if state:
        open_period = db.query(m3.MachineStatePeriod).filter_by(machine_id=machine.id, ended_at=None).order_by(m3.MachineStatePeriod.started_at.desc()).first()
        if not open_period or open_period.state != state:
            if open_period:
                if event_time < open_period.started_at:
                    db.rollback()
                    raise HTTPException(409, "设备状态事件时间早于当前状态开始时间")
                open_period.ended_at = event_time
            db.add(m3.MachineStatePeriod(
                machine_id=machine.id, state=state, started_at=event_time,
                reason=x.state_reason[:240], source=x.source,
                event_id=f"{x.message_id}:state",
            ))
        machine.status = state

    for idx, metric in enumerate(x.metrics):
        value = metric.value
        numeric_value = None
        text_value = ""
        if isinstance(value, bool):
            text_value = str(value).lower()
        elif isinstance(value, (int, float)):
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                db.rollback()
                raise HTTPException(400, f"点位 {metric.name} 的数值必须为有限数字")
        else:
            text_value = str(value)[:160]
        db.add(m2.DeviceReading(
            machine_id=machine.id, point_name=metric.name,
            numeric_value=numeric_value, text_value=text_value,
            quality=metric.quality[:30], device_at=event_time,
            source=x.source, source_message_id=f"{x.message_id}:{idx}",
        ))
        if numeric_value is not None and metric.quality.strip().lower() == "good":
            rules = db.query(m3.AlarmRule).filter(
                m3.AlarmRule.enabled.is_(True),
                m3.AlarmRule.point_name == metric.name,
                or_(m3.AlarmRule.machine_code == "", m3.AlarmRule.machine_code == machine.code),
            ).all()
            for rule in rules:
                if _rule_matches(rule.operator, numeric_value, rule.threshold):
                    since = datetime.utcnow() - timedelta(seconds=rule.cooldown_seconds)
                    recent = db.query(m3.AlarmEvent).filter(
                        m3.AlarmEvent.rule_id == rule.id,
                        m3.AlarmEvent.machine_id == machine.id,
                        m3.AlarmEvent.created_at >= since,
                        m3.AlarmEvent.status != "resolved",
                    ).first()
                    if not recent:
                        detail = rule.detail_template.format(value=numeric_value, threshold=rule.threshold, machine=machine.code) if rule.detail_template else f"{metric.name}={numeric_value}; threshold {rule.operator} {rule.threshold}"
                        db.add(m3.AlarmEvent(
                            event_key=f"{x.message_id}:rule:{rule.id}", machine_id=machine.id,
                            rule_id=rule.id, severity=rule.severity, title=rule.title,
                            detail=detail,
                        ))

    if state == "alarm":
        db.add(m3.AlarmEvent(event_key=f"{x.message_id}:state-alarm", machine_id=machine.id, severity="high", title="设备进入报警状态", detail=x.state_reason or "设备状态点位报告 alarm"))

    for idx, alarm in enumerate(x.alarms):
        if alarm.severity not in {"low", "medium", "high", "critical"}:
            db.rollback()
            raise HTTPException(400, "设备报警 severity 必须为 low/medium/high/critical")
        db.add(m3.AlarmEvent(
            event_key=f"{x.message_id}:alarm:{idx}", machine_id=machine.id,
            severity=alarm.severity, title=alarm.title,
            detail=f"{alarm.code}: {alarm.detail}"[:4000],
        ))
    try:
        db.commit()
    except Exception:
        db.rollback()
        # Safe retry behavior for unique-key races: only report duplicate if message now exists.
        existing = db.query(m3.EdgeMessage).filter_by(message_id=x.message_id).first()
        if existing:
            if existing.payload_hash != body_hash:
                raise HTTPException(409, "message_id 已被不同内容使用，拒绝覆盖")
            return {"accepted": True, "duplicate": True, "machine_code": machine.code}
        raise
    return {"accepted": True, "duplicate": False, "machine_code": machine.code, "metrics_received": len(x.metrics), "state": state}


@router.post("/edge/heartbeat")
def edge_heartbeat(x: HeartbeatIn, x_edge_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    _verify_edge_token(x_edge_token)
    row = db.query(m3.EdgeGatewayHeartbeat).filter_by(gateway_id=x.gateway_id).first()
    if not row:
        row = m3.EdgeGatewayHeartbeat(gateway_id=x.gateway_id)
        db.add(row)
    row.status = "online"; row.version = x.version[:40]; row.ip_address = x.ip_address[:80]
    row.message = x.message[:240]; row.last_seen_at = datetime.utcnow()
    db.commit()
    return {"accepted": True, "gateway_id": row.gateway_id, "last_seen_at": row.last_seen_at}


@router.get("/edge/gateways")
def edge_gateways(db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "maintenance", "viewer"))):
    rows = db.query(m3.EdgeGatewayHeartbeat).order_by(m3.EdgeGatewayHeartbeat.last_seen_at.desc()).all()
    now = datetime.utcnow()
    return [{"gateway_id": r.gateway_id, "status": "online" if (now-r.last_seen_at).total_seconds() < 90 else "stale", "version": r.version, "ip_address": r.ip_address, "message": r.message, "last_seen_at": r.last_seen_at} for r in rows]


@router.get("/monitor/overview")
def monitor_overview(db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    machines = db.query(m1.Machine).order_by(m1.Machine.code).all()
    now = datetime.utcnow()
    out = []
    for machine in machines:
        latest = db.query(m2.DeviceReading).filter_by(machine_id=machine.id).order_by(m2.DeviceReading.received_at.desc()).first()
        state = db.query(m3.MachineStatePeriod).filter_by(machine_id=machine.id, ended_at=None).order_by(m3.MachineStatePeriod.started_at.desc()).first()
        open_alarms = db.query(m3.AlarmEvent).filter_by(machine_id=machine.id, status="open").count()
        last_seen = latest.received_at if latest else None
        online = bool(last_seen and (now - last_seen).total_seconds() < 90)
        display_state = (state.state if state else machine.status) if online else "offline"
        out.append({
            "machine_id": machine.id, "machine_code": machine.code, "name": machine.name,
            "controller": machine.controller, "state": display_state,
            "online": online, "last_seen_at": last_seen, "open_alarms": open_alarms,
            "latest_point": latest.point_name if latest else None,
            "latest_value": latest.numeric_value if latest and latest.numeric_value is not None else (latest.text_value if latest else None),
        })
    return {"generated_at": now, "total_machines": len(out), "online_machines": sum(1 for x in out if x["online"]), "running_machines": sum(1 for x in out if x["state"] == "running"), "open_alarms": sum(x["open_alarms"] for x in out), "machines": out}


@router.get("/monitor/machines/{machine_id}/readings")
def machine_readings(machine_id: int, point_name: str | None = None, limit: int = Query(200, ge=1, le=2000), db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    if not db.get(m1.Machine, machine_id):
        raise HTTPException(404, "设备不存在")
    q = db.query(m2.DeviceReading).filter_by(machine_id=machine_id)
    if point_name:
        q = q.filter_by(point_name=point_name)
    return q.order_by(m2.DeviceReading.device_at.desc()).limit(limit).all()


@router.get("/oee/machines/{machine_id}")
def machine_oee(machine_id: int, start: datetime | None = None, end: datetime | None = None,
                ideal_cycle_seconds: float = Query(0, ge=0),
                shift_start: str | None = Query(default=None, max_length=5),
                shift_end: str | None = Query(default=None, max_length=5),
                break_start: str | None = Query(default=None, max_length=5),
                break_end: str | None = Query(default=None, max_length=5),
                workdays: list[int] | None = Query(default=None),
                db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    machine = db.get(m1.Machine, machine_id)
    if not machine:
        raise HTTPException(404, "设备不存在")
    end = end or datetime.utcnow(); start = start or (end - timedelta(hours=8))
    if end.tzinfo is not None:
        end = end.astimezone(timezone.utc).replace(tzinfo=None)
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    if end <= start:
        raise HTTPException(400, "end 必须晚于 start")
    if end - start > timedelta(days=90):
        raise HTTPException(400, "单次 OEE 时间窗最多90天")
    periods = db.query(m3.MachineStatePeriod).filter(
        m3.MachineStatePeriod.machine_id == machine_id,
        m3.MachineStatePeriod.started_at < end,
        or_(m3.MachineStatePeriod.ended_at.is_(None), m3.MachineStatePeriod.ended_at > start),
    ).all()
    # An unclosed state period is not assumed to continue forever. Cap it at
    # the last accepted telemetry receive-time + 90s (same offline threshold as monitor UI).
    latest_signal = db.query(m2.DeviceReading).filter_by(machine_id=machine_id, quality="good").order_by(m2.DeviceReading.received_at.desc()).first()
    stale_cutoff = (latest_signal.received_at + timedelta(seconds=90)) if latest_signal else None
    def effective_period_end(period):
        if period.ended_at is not None:
            return period.ended_at
        return stale_cutoff if stale_cutoff is not None else period.started_at
    planned_stop_states = {"planned_stop", "maintenance"}
    # 可选班次日历：提供完整班次参数后，计划/运行时间只统计工厂时区班内工时；
    # 缺省保持旧口径（整个查询窗口视为计划时间）。
    shift_applied = any(v is not None for v in (shift_start, shift_end, break_start, break_end))
    work_segments = []
    tz = None
    if shift_applied:
        if None in (shift_start, shift_end, break_start, break_end):
            raise HTTPException(400, "班次日历需同时提供 shift_start/shift_end/break_start/break_end")
        ss = _parse_clock(shift_start, "shift_start"); se = _parse_clock(shift_end, "shift_end")
        bs = _parse_clock(break_start, "break_start"); be = _parse_clock(break_end, "break_end")
        if not (ss < se and ss <= bs < be <= se):
            raise HTTPException(400, "班次及休息时间必须满足 shift_start <= break_start < break_end <= shift_end")
        days = set(workdays) if workdays is not None else {0, 1, 2, 3, 4, 5}
        if not days or any(d not in range(7) for d in days):
            raise HTTPException(400, "workdays 必须为 0=周一 至 6=周日")
        tz = _plant_timezone()
        start_local = _utc_naive_to_local(start, tz)
        end_local = _utc_naive_to_local(end, tz)
        work_segments = _shift_work_segments(start_local, end_local, ss, se, bs, be, days)
        local_periods = [(_utc_naive_to_local(p.started_at, tz), _utc_naive_to_local(effective_period_end(p), tz), p.state) for p in periods]
        def _clip(a, b):
            total = 0.0
            for seg_start, seg_end in work_segments:
                lo, hi = max(a, seg_start), min(b, seg_end)
                if hi > lo:
                    total += (hi - lo).total_seconds()
            return total
        total_work = sum((seg_end - seg_start).total_seconds() for seg_start, seg_end in work_segments)
        excluded = sum(_clip(a, b) for a, b, st in local_periods if st in planned_stop_states)
        planned_seconds = max(0.0, total_work - excluded)
        operating_seconds = sum(_clip(a, b) for a, b, st in local_periods if st == "running")
    else:
        excluded = sum(_intersect_seconds(start, end, p.started_at, effective_period_end(p)) for p in periods if p.state in planned_stop_states)
        planned_seconds = max(0.0, (end-start).total_seconds() - excluded)
        operating_seconds = sum(_intersect_seconds(start, end, p.started_at, effective_period_end(p)) for p in periods if p.state == "running")
    availability = operating_seconds / planned_seconds if planned_seconds else 0.0
    good = _counter_delta(db, machine_id, "good_count_total", start, end)
    reject = _counter_delta(db, machine_id, "reject_count_total", start, end)
    total = good + reject
    cycle = ideal_cycle_seconds
    if cycle <= 0:
        cycle_row = db.query(m2.DeviceReading).filter(
            m2.DeviceReading.machine_id == machine_id,
            m2.DeviceReading.point_name == "ideal_cycle_seconds",
            m2.DeviceReading.quality == "good",
            m2.DeviceReading.device_at <= end,
        ).order_by(m2.DeviceReading.device_at.desc()).first()
        if cycle_row and cycle_row.numeric_value:
            cycle = float(cycle_row.numeric_value)
    performance = (cycle * total / operating_seconds) if cycle > 0 and operating_seconds > 0 else None
    quality = good / total if total > 0 else None
    # Report uncapped factors for diagnostics; OEE uses performance capped at 100%.
    oee = availability * min(performance, 1.0) * quality if performance is not None and quality is not None else None
    return {
        "machine_id": machine_id, "machine_code": machine.code, "from": start, "to": end,
        "planned_seconds": round(planned_seconds, 1), "operating_seconds": round(operating_seconds, 1),
        "availability_pct": round(availability * 100, 2),
        "performance_pct": round(performance * 100, 2) if performance is not None else None,
        "quality_pct": round(quality * 100, 2) if quality is not None else None,
        "oee_pct": round(oee * 100, 2) if oee is not None else None,
        "good_qty": round(good, 2), "reject_qty": round(reject, 2),
        "ideal_cycle_seconds": cycle if cycle > 0 else None,
        "data_complete": bool(periods) and latest_signal is not None and total > 0 and cycle > 0 and (not shift_applied or planned_seconds > 0),
        "shift_calendar_applied": shift_applied,
        "notes": ["planned_stop 与 maintenance 从计划生产时间中扣除"]
        + (
            [f"已应用班次日历：工作日 {','.join(str(d) for d in sorted(days))}，班次 {shift_start}-{shift_end}，休息 {break_start}-{break_end}（工厂时区 {tz}，仅统计班内时间）"]
            if shift_applied
            else ["当前未提供班次/休息日历，查询窗口其余时间默认视为计划生产时间；可传 shift_start/shift_end/break_start/break_end/workdays 按班次口径计算"]
        )
        + ["performance 原始值可超过100%，OEE计算时按100%封顶", "产量使用累计计数器 good_count_total / reject_count_total 的窗口增量", "data_complete 仅代表设备状态/计数器/理想节拍采集齐备，不代表班次口径已校准"],
    }


@router.get("/alarms/rules")
def alarm_rules(db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "maintenance", "viewer"))):
    return db.query(m3.AlarmRule).order_by(m3.AlarmRule.rule_code).all()


@router.post("/alarms/rules")
def create_alarm_rule(x: AlarmRuleIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "maintenance"))):
    if x.operator not in {">", ">=", "<", "<=", "==", "!="}:
        raise HTTPException(400, "operator 必须为 >, >=, <, <=, ==, !=")
    if x.severity not in {"low", "medium", "high", "critical"}:
        raise HTTPException(400, "severity 必须为 low/medium/high/critical")
    if not math.isfinite(x.threshold):
        raise HTTPException(400, "threshold 必须为有限数字")
    if x.machine_code and not db.query(m1.Machine).filter_by(code=x.machine_code).first():
        raise HTTPException(404, "报警规则指定的设备编码不存在")
    if db.query(m3.AlarmRule).filter_by(rule_code=x.rule_code).first():
        raise HTTPException(409, "规则编号已存在")
    row = m3.AlarmRule(**x.model_dump()); db.add(row); db.flush()
    write_audit(db, actor, "alarm_rule.create", "AlarmRule", row.id, x.rule_code); db.commit(); db.refresh(row)
    return row


@router.get("/alarms/events")
def alarm_events(status: str | None = "open", severity: str | None = None, limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db), actor: m2.User = Depends(get_current_user)):
    q = db.query(m3.AlarmEvent)
    if status: q = q.filter_by(status=status)
    if severity: q = q.filter_by(severity=severity)
    return q.order_by(m3.AlarmEvent.created_at.desc()).limit(limit).all()


@router.post("/alarms/events/{event_id}/acknowledge")
def acknowledge_alarm(event_id: int, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "maintenance", "operator"))):
    row = db.get(m3.AlarmEvent, event_id)
    if not row: raise HTTPException(404, "报警事件不存在")
    if row.status == "resolved": raise HTTPException(409, "报警已关闭")
    row.status = "acknowledged"; row.acknowledged_by = actor.username; row.acknowledged_at = datetime.utcnow()
    write_audit(db, actor, "alarm.acknowledge", "AlarmEvent", row.id, row.title); db.commit()
    return {"id": row.id, "status": row.status, "acknowledged_by": row.acknowledged_by}


@router.post("/alarms/events/{event_id}/resolve")
def resolve_alarm(event_id: int, x: AlarmResolveIn, db: Session = Depends(get_db), actor: m2.User = Depends(require_roles("admin", "maintenance"))):
    row = db.get(m3.AlarmEvent, event_id)
    if not row: raise HTTPException(404, "报警事件不存在")
    if row.status == "resolved": raise HTTPException(409, "报警已关闭")
    row.status = "resolved"; row.resolved_by = actor.username; row.resolved_at = datetime.utcnow()
    if x.note: row.detail = (row.detail + " | Resolution: " + x.note)[:4000]
    write_audit(db, actor, "alarm.resolve", "AlarmEvent", row.id, x.note); db.commit()
    return {"id": row.id, "status": row.status, "resolved_by": row.resolved_by}
