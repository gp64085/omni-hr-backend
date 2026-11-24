"""add_password_reset_tokens

Revision ID: 20261124_reset_tokens
Revises: e19fe4f66ff4
Create Date: 2026-11-24 12:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261124_reset_tokens"
down_revision: Union[str, Sequence[str], None] = "e19fe4f66ff4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "password_reset_tokens" not in tables:
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("token_hash", sa.String(length=255), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "is_used", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_password_reset_tokens_token_hash"),
            "password_reset_tokens",
            ["token_hash"],
            unique=True,
        )
        op.create_index(
            op.f("ix_password_reset_tokens_user_id"),
            "password_reset_tokens",
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "password_reset_tokens" in tables:
        indexes = [
            idx["name"] for idx in inspector.get_indexes("password_reset_tokens")
        ]
        if op.f("ix_password_reset_tokens_user_id") in indexes:
            op.drop_index(
                op.f("ix_password_reset_tokens_user_id"),
                table_name="password_reset_tokens",
            )
        if op.f("ix_password_reset_tokens_token_hash") in indexes:
            op.drop_index(
                op.f("ix_password_reset_tokens_token_hash"),
                table_name="password_reset_tokens",
            )
        op.drop_table("password_reset_tokens")
