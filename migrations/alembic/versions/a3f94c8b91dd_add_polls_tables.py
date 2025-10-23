"""add polls tables"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision: str = "a3f94c8b91dd"
down_revision: Union[str, None] = "8fd1e338eb45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLL_TABLE = "polls"
POLL_QUESTIONS_TABLE = "poll_questions"
POLL_OPTIONS_TABLE = "poll_options"
POLL_RUNS_TABLE = "poll_runs"
POLL_RESPONSES_TABLE = "poll_responses"
POLL_ANSWERS_TABLE = "poll_answers"


def _table_exists(inspector: Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, POLL_TABLE):
        op.create_table(
            POLL_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column(
                "reward_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "reward_amount_kopeks",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        )
        inspector = sa.inspect(bind)

    if not _table_exists(inspector, POLL_QUESTIONS_TABLE):
        op.create_table(
            POLL_QUESTIONS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("poll_id", sa.Integer(), nullable=False),
            sa.Column(
                "order",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("text", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["poll_id"], [f"{POLL_TABLE}.id"], ondelete="CASCADE"),
        )
        inspector = sa.inspect(bind)

    if not _table_exists(inspector, POLL_OPTIONS_TABLE):
        op.create_table(
            POLL_OPTIONS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("question_id", sa.Integer(), nullable=False),
            sa.Column(
                "order",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("text", sa.String(length=255), nullable=False),
            sa.ForeignKeyConstraint(
                ["question_id"],
                [f"{POLL_QUESTIONS_TABLE}.id"],
                ondelete="CASCADE",
            ),
        )
        inspector = sa.inspect(bind)

    if not _table_exists(inspector, POLL_RUNS_TABLE):
        op.create_table(
            POLL_RUNS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("poll_id", sa.Integer(), nullable=False),
            sa.Column("target_type", sa.String(length=100), nullable=False),
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
                server_default="scheduled",
            ),
            sa.Column(
                "total_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "sent_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "failed_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "completed_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["poll_id"], [f"{POLL_TABLE}.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        )
        inspector = sa.inspect(bind)

    if not _table_exists(inspector, POLL_RESPONSES_TABLE):
        op.create_table(
            POLL_RESPONSES_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("poll_id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("current_question_id", sa.Integer(), nullable=True),
            sa.Column("message_id", sa.Integer(), nullable=True),
            sa.Column("chat_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "is_completed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "reward_given",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "reward_amount_kopeks",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["poll_id"], [f"{POLL_TABLE}.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["run_id"], [f"{POLL_RUNS_TABLE}.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["current_question_id"], [f"{POLL_QUESTIONS_TABLE}.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("poll_id", "user_id", name="uq_poll_user"),
        )
        inspector = sa.inspect(bind)

    if not _table_exists(inspector, POLL_ANSWERS_TABLE):
        op.create_table(
            POLL_ANSWERS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("response_id", sa.Integer(), nullable=False),
            sa.Column("question_id", sa.Integer(), nullable=False),
            sa.Column("option_id", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["response_id"], [f"{POLL_RESPONSES_TABLE}.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["question_id"], [f"{POLL_QUESTIONS_TABLE}.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["option_id"], [f"{POLL_OPTIONS_TABLE}.id"], ondelete="CASCADE"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, POLL_ANSWERS_TABLE):
        op.drop_table(POLL_ANSWERS_TABLE)
        inspector = sa.inspect(bind)

    if _table_exists(inspector, POLL_RESPONSES_TABLE):
        op.drop_table(POLL_RESPONSES_TABLE)
        inspector = sa.inspect(bind)

    if _table_exists(inspector, POLL_RUNS_TABLE):
        op.drop_table(POLL_RUNS_TABLE)
        inspector = sa.inspect(bind)

    if _table_exists(inspector, POLL_OPTIONS_TABLE):
        op.drop_table(POLL_OPTIONS_TABLE)
        inspector = sa.inspect(bind)

    if _table_exists(inspector, POLL_QUESTIONS_TABLE):
        op.drop_table(POLL_QUESTIONS_TABLE)
        inspector = sa.inspect(bind)

    if _table_exists(inspector, POLL_TABLE):
        op.drop_table(POLL_TABLE)
