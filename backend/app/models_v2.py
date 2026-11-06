from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(80), default="生产部")
    skill_level: Mapped[str] = mapped_column(String(30), default="L1")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    product_name: Mapped[str] = mapped_column(String(160))
    drawing_revision: Mapped[str] = mapped_column(String(20), default="A")
    material_code: Mapped[str] = mapped_column(String(60), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class RoutingOperation(Base):
    __tablename__ = "routing_operations"
    __table_args__ = (UniqueConstraint("product_code", "revision", "sequence", name="uq_routing_product_revision_seq"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str] = mapped_column(String(80), index=True)
    revision: Mapped[str] = mapped_column(String(20), default="A")
    sequence: Mapped[int] = mapped_column(Integer)
    operation_code: Mapped[str] = mapped_column(String(40))
    operation_name: Mapped[str] = mapped_column(String(120))
    work_center: Mapped[str] = mapped_column(String(80), default="CNC")
    standard_cycle_seconds: Mapped[float] = mapped_column(Float, default=0)
    setup_minutes: Mapped[float] = mapped_column(Float, default=0)
    preferred_machine_code: Mapped[str] = mapped_column(String(40), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Nonconformance(Base):
    __tablename__ = "nonconformances"
    id: Mapped[int] = mapped_column(primary_key=True)
    ncr_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id"), nullable=True)
    defect_code: Mapped[str] = mapped_column(String(40))
    defect_description: Mapped[str] = mapped_column(Text)
    affected_qty: Mapped[int] = mapped_column(Integer)
    disposition: Mapped[str] = mapped_column(String(30), default="pending")
    owner_code: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(30), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class SchedulePlan(Base):
    __tablename__ = "schedule_plans"
    __table_args__ = (Index("ix_schedule_machine_window", "machine_id", "start_at", "end_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    planned_minutes: Mapped[int] = mapped_column(Integer)
    sequence_no: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    approved_by: Mapped[str] = mapped_column(String(40), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class DeviceReading(Base):
    __tablename__ = "device_readings"
    __table_args__ = (
        Index("ix_device_readings_machine_point_received", "machine_id", "point_name", "received_at"),
        Index("ix_device_readings_machine_point_device_at", "machine_id", "point_name", "device_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    point_name: Mapped[str] = mapped_column(String(100), index=True)
    numeric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    text_value: Mapped[str] = mapped_column(String(160), default="")
    quality: Mapped[str] = mapped_column(String(30), default="good")
    device_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(50), default="edge-gateway")
    source_message_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(30), default="operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), default="system", index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(80), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
