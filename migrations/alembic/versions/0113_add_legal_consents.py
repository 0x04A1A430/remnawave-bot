"""legal_consents — журнал согласий с офертой и политикой при регистрации в кабинете

Смысл чекбокса «ознакомлен» — в доказательстве, поэтому храним журнал, а не флаг
на пользователе: кто, с каким документом, когда и откуда согласился.

Revision ID: 0113
Revises: 0112
Create Date: 2026-08-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0113'
down_revision: str | None = '0112'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'legal_consents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('document', sa.String(length=32), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_legal_consents_id', 'legal_consents', ['id'])
    # Уникальности нет намеренно: переподтверждение после смены редакции документа
    # должно ложиться новой строкой рядом со старой, а не затирать её.
    op.create_index('ix_legal_consents_user_document', 'legal_consents', ['user_id', 'document'])


def downgrade() -> None:
    op.drop_index('ix_legal_consents_user_document', table_name='legal_consents')
    op.drop_index('ix_legal_consents_id', table_name='legal_consents')
    op.drop_table('legal_consents')