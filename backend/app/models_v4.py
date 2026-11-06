from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class APSJob(Base):
    __tablename__ = "aps_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_no: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5, index=True)  # 1=lowest, 10=highest
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    release_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    required_minutes: Mapped[int] = mapped_column(Integer)
    eligible_machine_codes: Mapped[str] = mapped_column(Text)  # comma-separated, explicitly qualified machines
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class APSRun(Base):
    __tablename__ = "aps_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_no: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    horizon_start: Mapped[datetime] = mapped_column(DateTime)
    horizon_end: Mapped[datetime] = mapped_column(DateTime)
    strategy: Mapped[str] = mapped_column(String(40), default="priority_edd")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    generated_jobs: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_jobs: Mapped[int] = mapped_column(Integer, default=0)
    unscheduled_jobs: Mapped[int] = mapped_column(Integer, default=0)
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(60), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class APSAssignment(Base):
    __tablename__ = "aps_assignments"
    __table_args__ = (
        UniqueConstraint("run_id", "job_id", name="uq_aps_run_job"),
        Index("ix_aps_assignment_machine_window", "machine_id", "start_at", "end_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("aps_runs.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("aps_jobs.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    planned_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    schedule_plan_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_plans.id"), nullable=True)
    explanation: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MaintenanceThreshold(Base):
    __tablename__ = "maintenance_thresholds"
    __table_args__ = (UniqueConstraint("machine_code", "point_name", name="uq_maintenance_machine_point"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_code: Mapped[str] = mapped_column(String(40), index=True)  # empty = all machines
    point_name: Mapped[str] = mapped_column(String(100), index=True)
    warning_value: Mapped[float] = mapped_column(Float)
    critical_value: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(10), default="high")  # high or low
    unit: Mapped[str] = mapped_column(String(24), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MaintenancePrediction(Base):
    __tablename__ = "maintenance_predictions"
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    point_name: Mapped[str] = mapped_column(String(100), index=True)
    latest_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk: Mapped[str] = mapped_column(String(24), default="insufficient_data", index=True)
    trend_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProductionInsight(Base):
    __tablename__ = "production_insights"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_no: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    overall_status: Mapped[str] = mapped_column(String(24), default="info")
    summary: Mapped[str] = mapped_column(Text, default="")
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    recommendations_json: Mapped[str] = mapped_column(Text, default="[]")
    engine_version: Mapped[str] = mapped_column(String(40), default="rules-v1")
    created_by: Mapped[str] = mapped_column(String(60), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TraceabilityEvent(Base):
    __tablename__ = "traceability_events"
    __table_args__ = (
        Index("ix_trace_work_order_event_at", "work_order_id", "event_at"),
        Index("ix_trace_material_lot", "material_code", "material_lot"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_no: Mapped[str] = mapped_column(String(70), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id"), nullable=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    employee_code: Mapped[str] = mapped_column(String(40), default="", index=True)
    product_code: Mapped[str] = mapped_column(String(80), default="", index=True)
    material_code: Mapped[str] = mapped_column(String(60), default="", index=True)
    material_lot: Mapped[str] = mapped_column(String(80), default="", index=True)
    tool_code: Mapped[str] = mapped_column(String(60), default="", index=True)
    good_qty: Mapped[int] = mapped_column(Integer, default=0)
    reject_qty: Mapped[int] = mapped_column(Integer, default=0)
    reference_no: Mapped[str] = mapped_column(String(80), default="")
    event_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
