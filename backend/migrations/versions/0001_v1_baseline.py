"""V1 schema baseline (safe for fresh installs and existing V1 databases)."""
from alembic import op
from app.db import Base
from app import models  # noqa: F401 - V1 tables only in this revision

revision = "0001_v1_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # create_all(checkfirst=True) creates only absent V1 tables.
    v1_tables = [Base.metadata.tables[n] for n in (
        "machines", "work_orders", "operations", "production_reports",
        "quality_inspections", "tools", "materials", "inventory_transactions",
        "machine_events", "alerts", "piece_rates"
    )]
    Base.metadata.create_all(bind=op.get_bind(), tables=v1_tables)


def downgrade():
    # Baseline downgrade is intentionally a no-op to prevent accidental V1 data loss.
    pass
