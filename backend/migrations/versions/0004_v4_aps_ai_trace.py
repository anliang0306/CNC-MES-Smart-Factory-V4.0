"""V4 APS, explainable production insights, maintenance baseline and traceability."""
from alembic import op
from app.db import Base
from app import models  # noqa: F401
from app import models_v2  # noqa: F401
from app import models_v3  # noqa: F401
from app import models_v4  # noqa: F401 - register V4 tables

revision = "0004_v4_aps_ai_trace"
down_revision = "0003_v3_edge_oee"
branch_labels = None
depends_on = None


def upgrade():
    names = (
        "aps_jobs", "aps_runs", "aps_assignments", "maintenance_thresholds",
        "maintenance_predictions", "production_insights", "traceability_events",
    )
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables[n] for n in names])


def downgrade():
    # V4 stores planning decisions and traceability evidence. Do not drop them automatically.
    pass
