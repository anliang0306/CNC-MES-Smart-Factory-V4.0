from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from .db import get_db
from . import models as m1
from . import models_v2 as m
from .auth import get_current_user, require_roles, hash_password, verify_password, issue_token, write_audit, check_login_rate_limit, record_login_failure, clear_login_failures

router = APIRouter(prefix="/api/v2", tags=["MES V2.0"])


def _plant_tz():
    try:
        return ZoneInfo(os.getenv("PLANT_TIMEZONE", "UTC"))
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(503, "PLANT_TIMEZONE 不是有效的 IANA 时区") from exc


def _plant_local_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(_plant_tz()).replace(tzinfo=None)

class LoginIn(BaseModel): username: str; password: str
class UserIn(BaseModel): username: str = Field(min_length=3, max_length=60); password: str = Field(min_length=12); role: str = "operator"
class EmployeeIn(BaseModel): employee_code: str; full_name: str; department: str = "生产部"; skill_level: str = "L1"; active: bool = True
class ProductIn(BaseModel): product_code: str; product_name: str; drawing_revision: str = "A"; material_code: str = ""; description: str = ""; active: bool = True
class RoutingStepIn(BaseModel): sequence: int = Field(gt=0); operation_code: str; operation_name: str; work_center: str = "CNC"; standard_cycle_seconds: float = Field(ge=0); setup_minutes: float = Field(ge=0, default=0); preferred_machine_code: str = ""
class RoutingIn(BaseModel): product_code: str; revision: str = "A"; operations: list[RoutingStepIn] = Field(min_length=1)
class NCRIn(BaseModel): ncr_no: str; work_order_id: int; operation_id: int | None = None; defect_code: str; defect_description: str; affected_qty: int = Field(gt=0); owner_code: str = ""
class DispositionIn(BaseModel): action: str; owner_code: str = ""
class PlanIn(BaseModel): plan_no: str = ""; work_order_id: int; machine_id: int; start_at: datetime; end_at: datetime; planned_minutes: int = Field(gt=0); sequence_no: int = 10
class ReadingIn(BaseModel): machine_id: int; point_name: str; numeric_value: float | None = None; text_value: str = ""; quality: str = "good"; device_at: datetime | None = None; source: str = "edge-gateway"; source_message_id: str | None = None

@router.post("/auth/login")
def login(x: LoginIn, db: Session = Depends(get_db)):
    # 按用户名限流（对不存在的用户同样生效，避免用户枚举/暴力破解）
    check_login_rate_limit(x.username)
    user = db.query(m.User).filter_by(username=x.username, active=True).first()
    if not user or not verify_password(x.password, user.password_hash):
        record_login_failure(x.username)
        raise HTTPException(401, "用户名或密码错误")
    clear_login_failures(x.username)
    return {"access_token": issue_token(user), "token_type": "bearer", "expires_in": int(os.getenv("TOKEN_TTL_SECONDS", "28800")), "username": user.username, "role": user.role}

@router.post("/auth/users")
def create_user(x: UserIn, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin"))):
    allowed = {"admin", "planner", "quality", "operator", "maintenance", "gateway", "viewer"}
    if x.role not in allowed: raise HTTPException(400, "不支持的角色")
    if db.query(m.User).filter_by(username=x.username).first(): raise HTTPException(409, "用户名已存在")
    u = m.User(username=x.username, password_hash=hash_password(x.password), role=x.role)
    db.add(u); db.flush(); write_audit(db, actor, "user.create", "User", u.id, f"role={u.role}"); db.commit()
    return {"id": u.id, "username": u.username, "role": u.role}

@router.get("/auth/me")
def me(actor: m.User = Depends(get_current_user)):
    return {"username": actor.username, "role": actor.role}

@router.get("/employees")
def employees(db: Session = Depends(get_db), actor: m.User = Depends(get_current_user)):
    return db.query(m.Employee).order_by(m.Employee.employee_code).all()

@router.post("/employees")
def add_employee(x: EmployeeIn, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin", "planner"))):
    if db.query(m.Employee).filter_by(employee_code=x.employee_code).first(): raise HTTPException(409, "员工编号已存在")
    o = m.Employee(**x.model_dump()); db.add(o); db.flush(); write_audit(db, actor, "employee.create", "Employee", o.id, x.employee_code); db.commit(); db.refresh(o); return o

@router.get("/products")
def products(db: Session = Depends(get_db), actor: m.User = Depends(get_current_user)):
    return db.query(m.Product).order_by(m.Product.product_code).all()

@router.post("/products")
def add_product(x: ProductIn, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin", "planner"))):
    if db.query(m.Product).filter_by(product_code=x.product_code).first(): raise HTTPException(409, "产品编码已存在")
    o = m.Product(**x.model_dump()); db.add(o); db.flush(); write_audit(db, actor, "product.create", "Product", o.id, x.product_code); db.commit(); db.refresh(o); return o

@router.get("/routings/{product_code}")
def get_routing(product_code: str, revision: str = "A", db: Session = Depends(get_db), actor: m.User = Depends(get_current_user)):
    steps = db.query(m.RoutingOperation).filter_by(product_code=product_code, revision=revision, active=True).order_by(m.RoutingOperation.sequence).all()
    return {"product_code": product_code, "revision": revision, "operations": steps}

@router.post("/routings")
def save_routing(x: RoutingIn, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin", "planner"))):
    if not db.query(m.Product).filter_by(product_code=x.product_code).first(): raise HTTPException(404, "产品主数据不存在")
    seqs = [z.sequence for z in x.operations]
    if len(seqs) != len(set(seqs)): raise HTTPException(400, "工序序号不能重复")
    existing = db.query(m.RoutingOperation).filter_by(product_code=x.product_code, revision=x.revision).all()
    for row in existing: row.active = False
    for step in x.operations: db.add(m.RoutingOperation(product_code=x.product_code, revision=x.revision, **step.model_dump()))
    write_audit(db, actor, "routing.publish", "Routing", x.product_code, f"revision={x.revision};steps={len(x.operations)}"); db.commit()
    return {"published": True, "product_code": x.product_code, "revision": x.revision, "step_count": len(x.operations)}

@router.get("/quality/ncr")
def ncr_list(status: str | None = None, db: Session = Depends(get_db), actor: m.User = Depends(get_current_user)):
    q = db.query(m.Nonconformance)
    if status: q = q.filter_by(status=status)
    return q.order_by(m.Nonconformance.created_at.desc()).all()

@router.post("/quality/ncr")
def create_ncr(x: NCRIn, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin", "quality", "operator"))):
    if not db.get(m1.WorkOrder, x.work_order_id): raise HTTPException(404, "工单不存在")
    if x.operation_id is not None:
        op = db.get(m1.Operation, x.operation_id)
        if not op or op.work_order_id != x.work_order_id: raise HTTPException(400, "工序与工单不匹配")
    if db.query(m.Nonconformance).filter_by(ncr_no=x.ncr_no).first(): raise HTTPException(409, "NCR编号已存在")
    o = m.Nonconformance(**x.model_dump()); db.add(o); db.flush()
    db.add(m1.Alert(severity="high", category="quality", title="NCR不合格事件", detail=f"{x.ncr_no}: {x.defect_description}; qty={x.affected_qty}"))
    write_audit(db, actor, "ncr.create", "Nonconformance", o.id, x.ncr_no); db.commit(); db.refresh(o); return o

@router.post("/quality/ncr/{ncr_id}/disposition")
def dispose_ncr(ncr_id: int, x: DispositionIn, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin", "quality"))):
    allowed = {"rework", "scrap", "use_as_is", "return_to_supplier"}
    if x.action not in allowed: raise HTTPException(400, f"处置方式必须为: {', '.join(sorted(allowed))}")
    o = db.get(m.Nonconformance, ncr_id)
    if not o: raise HTTPException(404, "NCR不存在")
    if o.status == "closed": raise HTTPException(409, "NCR已关闭")
    o.disposition = x.action; o.owner_code = x.owner_code or actor.username; o.status = "closed"; o.closed_at = datetime.utcnow()
    write_audit(db, actor, "ncr.disposition", "Nonconformance", o.id, f"action={x.action};owner={o.owner_code}"); db.commit()
    return {"id": o.id, "status": o.status, "disposition": o.disposition}

@router.get("/planning/capacity")
def capacity(days: int = Query(default=7, ge=1, le=90), db: Session = Depends(get_db), actor: m.User = Depends(get_current_user)):
    now = datetime.now(_plant_tz()).replace(tzinfo=None); end = now + timedelta(days=days)
    machines = db.query(m1.Machine).filter(m1.Machine.enabled == True).all()
    out = []
    for machine in machines:
        plans = db.query(m.SchedulePlan).filter(m.SchedulePlan.machine_id == machine.id, m.SchedulePlan.status.in_(["approved", "in_progress"]), m.SchedulePlan.start_at < end, m.SchedulePlan.end_at > now).all()
        booked = sum(p.planned_minutes for p in plans)
        out.append({"machine_id": machine.id, "machine_code": machine.code, "status": machine.status, "booked_minutes": booked, "window_minutes": days * 24 * 60, "load_pct": round(booked / (days * 24 * 60) * 100, 1), "active_plans": len(plans)})
    return {"from": now, "to": end, "days": days, "machines": out}

@router.get("/planning/plans")
def plans(status: str | None = None, db: Session = Depends(get_db), actor: m.User = Depends(get_current_user)):
    q = db.query(m.SchedulePlan)
    if status: q = q.filter_by(status=status)
    return q.order_by(m.SchedulePlan.start_at).all()

def _has_conflict(db: Session, machine_id: int, start_at: datetime, end_at: datetime, exclude_id: int | None = None) -> bool:
    q = db.query(m.SchedulePlan).filter(m.SchedulePlan.machine_id == machine_id, m.SchedulePlan.status.in_(["approved", "in_progress"]), m.SchedulePlan.start_at < end_at, m.SchedulePlan.end_at > start_at)
    if exclude_id: q = q.filter(m.SchedulePlan.id != exclude_id)
    return db.query(q.exists()).scalar()

@router.post("/planning/plans")
def create_plan(x: PlanIn, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin", "planner"))):
    start_at = _plant_local_time(x.start_at)
    end_at = _plant_local_time(x.end_at)
    if end_at <= start_at: raise HTTPException(400, "结束时间必须晚于开始时间")
    if not db.get(m1.WorkOrder, x.work_order_id): raise HTTPException(404, "工单不存在")
    if not db.get(m1.Machine, x.machine_id): raise HTTPException(404, "设备不存在")
    plan_no = x.plan_no or "SP-" + uuid4().hex[:10].upper()
    if db.query(m.SchedulePlan).filter_by(plan_no=plan_no).first(): raise HTTPException(409, "排程编号已存在")
    payload = x.model_dump(exclude={"plan_no", "start_at", "end_at"})
    o = m.SchedulePlan(plan_no=plan_no, start_at=start_at, end_at=end_at, **payload, status="draft")
    db.add(o); db.flush(); write_audit(db, actor, "schedule.create", "SchedulePlan", o.id, plan_no); db.commit(); db.refresh(o)
    return {"id": o.id, "plan_no": o.plan_no, "status": o.status, "note": "草案尚未占用设备产能；审批时会再次检查冲突"}

@router.post("/planning/plans/{plan_id}/approve")
def approve_plan(plan_id: int, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin", "planner"))):
    # 先锁设备行再检查冲突，序列化同一设备的并发审批（SQLite 上为无操作）
    o = db.get(m.SchedulePlan, plan_id, with_for_update=True)
    if not o: raise HTTPException(404, "排程不存在")
    if o.status != "draft": raise HTTPException(409, "只有草案状态可以审批")
    db.get(m1.Machine, o.machine_id, with_for_update=True)
    if _has_conflict(db, o.machine_id, o.start_at, o.end_at, o.id): raise HTTPException(409, "设备时间窗冲突，不能审批")
    o.status = "approved"; o.approved_by = actor.username; o.approved_at = datetime.utcnow()
    write_audit(db, actor, "schedule.approve", "SchedulePlan", o.id, o.plan_no); db.commit()
    return {"id": o.id, "status": o.status, "approved_by": o.approved_by}

@router.post("/devices/readings")
def add_readings(x: ReadingIn, db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin", "gateway", "maintenance"))):
    machine = db.get(m1.Machine, x.machine_id)
    if not machine: raise HTTPException(404, "设备不存在")
    if x.numeric_value is None and not x.text_value: raise HTTPException(400, "numeric_value 或 text_value 至少提供一个")
    if x.source_message_id:
        old = db.query(m.DeviceReading).filter_by(source_message_id=x.source_message_id).first()
        if old: return {"accepted": True, "duplicate": True, "id": old.id, "machine_code": machine.code}
    o = m.DeviceReading(machine_id=x.machine_id, point_name=x.point_name, numeric_value=x.numeric_value, text_value=x.text_value, quality=x.quality, device_at=x.device_at or datetime.utcnow(), source=x.source, source_message_id=x.source_message_id)
    db.add(o); db.commit(); db.refresh(o)
    # High-frequency telemetry is not copied into AuditLog per point; security-sensitive
    # configuration and business actions remain auditable without exploding log volume.
    return {"accepted": True, "duplicate": False, "id": o.id, "machine_code": machine.code, "received_at": o.received_at}

@router.get("/devices/readings/latest")
def latest_readings(machine_id: int | None = None, limit: int = Query(default=100, ge=1, le=1000), db: Session = Depends(get_db), actor: m.User = Depends(get_current_user)):
    q = db.query(m.DeviceReading)
    if machine_id: q = q.filter_by(machine_id=machine_id)
    return q.order_by(m.DeviceReading.received_at.desc()).limit(limit).all()

@router.get("/analytics/production")
def production_analytics(db: Session = Depends(get_db), actor: m.User = Depends(get_current_user)):
    totals = db.query(func.coalesce(func.sum(m1.WorkOrder.planned_qty), 0), func.coalesce(func.sum(m1.WorkOrder.good_qty), 0), func.coalesce(func.sum(m1.WorkOrder.scrap_qty), 0)).first()
    planned, good, scrap = [int(v or 0) for v in totals]
    reports = db.query(func.count(m1.Report.id), func.coalesce(func.sum(m1.Report.labor_minutes), 0)).first()
    return {"planned_qty": planned, "good_qty": good, "scrap_qty": scrap, "reported_qty": good + scrap, "completion_pct": round((good + scrap) / planned * 100, 2) if planned else 0, "yield_pct": round(good / (good + scrap) * 100, 2) if good + scrap else 0, "report_count": int(reports[0] or 0), "labor_minutes": int(reports[1] or 0), "open_ncr": db.query(m.Nonconformance).filter_by(status="open").count(), "pending_plans": db.query(m.SchedulePlan).filter_by(status="draft").count()}

@router.get("/audit/logs")
def audit_logs(limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin"))):
    return db.query(m.AuditLog).order_by(m.AuditLog.created_at.desc()).limit(limit).all()


@router.post("/demo/seed")
def seed_v2(db: Session = Depends(get_db), actor: m.User = Depends(require_roles("admin"))):
    created = {"employees": 0, "products": 0, "routing_steps": 0}
    if not db.query(m.Employee).count():
        db.add_all([m.Employee(employee_code="EMP-001", full_name="操作员示例", department="CNC一组", skill_level="L2"), m.Employee(employee_code="EMP-002", full_name="质检员示例", department="质量部", skill_level="Q1")]); created["employees"] = 2
    if not db.query(m.Product).filter_by(product_code="PART-001").first():
        db.add(m.Product(product_code="PART-001", product_name="精密铝合金支架", drawing_revision="A", material_code="AL6061")); created["products"] = 1
    if not db.query(m.RoutingOperation).filter_by(product_code="PART-001", revision="A", active=True).count():
        db.add_all([m.RoutingOperation(product_code="PART-001", revision="A", sequence=10, operation_code="OP10", operation_name="CNC粗铣", standard_cycle_seconds=270, setup_minutes=20, work_center="CNC"), m.RoutingOperation(product_code="PART-001", revision="A", sequence=20, operation_code="OP20", operation_name="精铣及倒角", standard_cycle_seconds=180, setup_minutes=15, work_center="CNC")]); created["routing_steps"] = 2
    write_audit(db, actor, "demo.seed", "System", "", str(created)); db.commit()
    return {"seeded": True, "created": created}
