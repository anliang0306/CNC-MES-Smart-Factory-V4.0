"""V3 edge messages, machine state periods, gateway heartbeat and alarm management."""
from alembic import op
from sqlalchemy import inspect
from app.db import Base
from app import models  # noqa: F401
from app import models_v2  # noqa: F401
from app import models_v3  # noqa: F401 - registers V3 tables

revision = "0003_v3_edge_oee"
down_revision = "0002_v2_mes_modules"
branch_labels = None
depends_on = None


def upgrade():
    names = (
        "edge_messages", "machine_state_periods", "edge_gateway_heartbeats",
        "alarm_rules", "alarm_events"
    )
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables[n] for n in names])
    indexes = {item["name"] for item in inspect(bind).get_indexes("device_readings")}
    if "ix_device_readings_machine_point_device_at" not in indexes:
        op.create_index(
            "ix_device_readings_machine_point_device_at",
            "device_readings", ["machine_id", "point_name", "device_at"], unique=False,
        )


def downgrade():
    # Keep operational telemetry and audit evidence; destructive rollback requires an approved runbook.
    pass
