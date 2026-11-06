"""Upgrade database schema before API startup.
Existing V1 installations are stamped to the V1 baseline, then receive V2, V3 and V4 tables.
New installations create V1 + V2 + V3 + V4 schema through the migration chain.
"""
import subprocess
import sys
from sqlalchemy import inspect
from app.db import engine

def _alembic(*args: str) -> None:
    # `python -m alembic` instead of the `alembic` console script:
    # the Scripts dir may not be on PATH (e.g. user-level pip installs on Windows).
    subprocess.run([sys.executable, "-m", "alembic", *args], check=True)

tables = set(inspect(engine).get_table_names())
if "work_orders" in tables and "alembic_version" not in tables:
    _alembic("stamp", "0001_v1_baseline")
_alembic("upgrade", "head")
print("MES database migrations are up to date")
