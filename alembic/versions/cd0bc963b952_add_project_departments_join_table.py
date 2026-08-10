"""add project_departments join table

Revision ID: cd0bc963b952
Revises: b2c3d4e5f6a7
Create Date: 2026-08-07 19:09:17.207756

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cd0bc963b952"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "project_departments",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("department_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("project_id", "department_id"),
    )
    op.execute(
        "INSERT INTO project_departments (project_id, department_id) "
        "SELECT id, department_id FROM projects WHERE department_id IS NOT NULL"
    )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("projects_department_id_fkey"), type_="foreignkey"
        )
        batch_op.drop_column("department_id")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("department_id", sa.UUID(), autoincrement=False, nullable=True)
        )
        batch_op.create_foreign_key(
            batch_op.f("projects_department_id_fkey"),
            "departments",
            ["department_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(
        "UPDATE projects SET department_id = ("
        "SELECT department_id FROM project_departments "
        "WHERE project_departments.project_id = projects.id LIMIT 1"
        ")"
    )
    op.drop_table("project_departments")
