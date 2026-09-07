"""initial schema

Revision ID: 0001_init
Revises:
Create Date: 2026-08-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "labs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("lab_type", sa.String(length=64), nullable=False),
        sa.Column("golden_image", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("cpu", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="8192"),
        sa.Column("subnet", sa.String(length=64), nullable=True),
        sa.Column("gateway", sa.String(length=64), nullable=True),
        sa.Column("vm_ip", sa.String(length=64), nullable=True),
        sa.Column("vm_pid", sa.Integer(), nullable=True),
        sa.Column("vm_api_socket", sa.String(length=512), nullable=True),
        sa.Column("tap_name", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_labs_status", "labs", ["status"])
    op.create_index("ix_labs_user_id", "labs", ["user_id"])

    op.create_table(
        "ip_allocations",
        sa.Column("lab_id", sa.String(length=64), sa.ForeignKey("labs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("subnet", sa.String(length=64), nullable=False, unique=True),
        sa.Column("gateway", sa.String(length=64), nullable=False),
        sa.Column("vm_ip", sa.String(length=64), nullable=False),
        sa.Column("allocated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "lab_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lab_id", sa.String(length=64), sa.ForeignKey("labs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(length=64), nullable=True),
        sa.Column("to_status", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_lab_events_lab_id", "lab_events", ["lab_id"])


def downgrade() -> None:
    op.drop_index("ix_lab_events_lab_id", table_name="lab_events")
    op.drop_table("lab_events")
    op.drop_table("ip_allocations")
    op.drop_index("ix_labs_user_id", table_name="labs")
    op.drop_index("ix_labs_status", table_name="labs")
    op.drop_table("labs")
