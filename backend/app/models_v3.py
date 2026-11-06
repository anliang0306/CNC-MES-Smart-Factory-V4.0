from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class EdgeMessage(Base):
    __tablename__ = "edge_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(50), default="edge-gateway")

class MachineStatePeriod(Base):
    __tablename__ = "machine_state_periods"
    __table_args__ = (Index("ix_machine_state_window", "machine_id", "started_at", "ended_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(String(240), default="")
    source: Mapped[str] = mapped_column(String(50), default="edge-gateway")
    event_id: Mapped[str] = mapped_column(String(100), unique=True)

class EdgeGatewayHeartbeat(Base):
    __tablename__ = "edge_gateway_heartbeats"
    id: Mapped[int] = mapped_column(primary_key=True)
    gateway_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="online")
    version: Mapped[str] = mapped_column(String(40), default="")
    ip_address: Mapped[str] = mapped_column(String(80), default="")
    message: Mapped[str] = mapped_column(String(240), default="")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class AlarmRule(Base):
    __tablename__ = "alarm_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    machine_code: Mapped[str] = mapped_column(String(40), default="", index=True)
    point_name: Mapped[str] = mapped_column(String(100), index=True)
    operator: Mapped[str] = mapped_column(String(8))
    threshold: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    title: Mapped[str] = mapped_column(String(160))
    detail_template: Mapped[str] = mapped_column(String(240), default="")
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class AlarmEvent(Base):
    __tablename__ = "alarm_events"
    __table_args__ = (Index("ix_alarm_status_created", "status", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alarm_rules.id"), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    title: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    acknowledged_by: Mapped[str] = mapped_column(String(60), default="")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str] = mapped_column(String(60), default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
