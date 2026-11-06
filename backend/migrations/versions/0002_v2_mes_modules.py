"""V2 master data, NCR, schedule, device readings, users and audit tables."""
from alembic import op
from app.db import Base
from app import models  # noqa: F401
from app import models_v2  # noqa: F401 - registers V2 tables

revision = "0002_v2_mes_modules"
down_revision = "0001_v1_baseline"
branch_labels = None
depends_on = None


def upgrade():
    v2_tables = [Base.metadata.tables[n] for n in (
        "employees", "products", "routing_operations", "nonconformances",
        "schedule_plans", "device_readings", "users", "audit_logs"
    )]
    Base.metadata.create_all(bind=op.get_bind(), tables=v2_tables)


def downgrade():
    # Destructive rollback must be a separately approved operation; preserve evidence by default.
    pass
