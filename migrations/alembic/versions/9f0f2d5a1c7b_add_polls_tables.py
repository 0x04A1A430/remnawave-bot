from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f0f2d5a1c7b"
down_revision: Union[str, None] = "8fd1e338eb45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "polls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
    op.create_index("ix_polls_id", "polls", ["id"])

    op.create_table(
        "poll_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("poll_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.ForeignKeyConstraint(["poll_id"], ["polls.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_poll_questions_id", "poll_questions", ["id"])
    op.create_index("ix_poll_questions_poll_id", "poll_questions", ["poll_id"])

    op.create_table(
        "poll_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.ForeignKeyConstraint(["question_id"], ["poll_questions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_poll_options_id", "poll_options", ["id"])
    op.create_index("ix_poll_options_question_id", "poll_options", ["question_id"])

    op.create_table(
        "poll_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("poll_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
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
        sa.ForeignKeyConstraint(["poll_id"], ["polls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("poll_id", "user_id", name="uq_poll_user"),
    )
    op.create_index("ix_poll_responses_id", "poll_responses", ["id"])
    op.create_index("ix_poll_responses_poll_id", "poll_responses", ["poll_id"])
    op.create_index("ix_poll_responses_user_id", "poll_responses", ["user_id"])

    op.create_table(
        "poll_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("response_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("option_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["option_id"], ["poll_options.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["poll_questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["response_id"], ["poll_responses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("response_id", "question_id", name="uq_poll_answer_unique"),
    )
    op.create_index("ix_poll_answers_id", "poll_answers", ["id"])
    op.create_index("ix_poll_answers_response_id", "poll_answers", ["response_id"])
    op.create_index("ix_poll_answers_question_id", "poll_answers", ["question_id"])


def downgrade() -> None:
    op.drop_index("ix_poll_answers_question_id", table_name="poll_answers")
    op.drop_index("ix_poll_answers_response_id", table_name="poll_answers")
    op.drop_index("ix_poll_answers_id", table_name="poll_answers")
    op.drop_table("poll_answers")

    op.drop_index("ix_poll_responses_user_id", table_name="poll_responses")
    op.drop_index("ix_poll_responses_poll_id", table_name="poll_responses")
    op.drop_index("ix_poll_responses_id", table_name="poll_responses")
    op.drop_table("poll_responses")

    op.drop_index("ix_poll_options_question_id", table_name="poll_options")
    op.drop_index("ix_poll_options_id", table_name="poll_options")
    op.drop_table("poll_options")

    op.drop_index("ix_poll_questions_poll_id", table_name="poll_questions")
    op.drop_index("ix_poll_questions_id", table_name="poll_questions")
    op.drop_table("poll_questions")

    op.drop_index("ix_polls_id", table_name="polls")
    op.drop_table("polls")
