"""add composite index (subscription_id, expires_at) on traffic_purchases

Housekeeping-запросы в `_housekeep_expired_purchases` и
`_apply_base_limit_preserving_active_purchases` фильтруют по обоим колонкам:
    WHERE subscription_id = :id AND expires_at <op> :now

До этой миграции были только single-column индексы (`subscription_id`,
`expires_at`), PostgreSQL выбирал один и фильтровал по второму через scan.
На активных юзерах с большим числом докупок это становилось горячим.

CREATE INDEX CONCURRENTLY чтобы не лочить таблицу на проде.

Revision ID: 0080
Revises: 0079
Create Date: 2026-05-13

"""

from typing import Sequence, Union

from alembic import op


revision: str = '0080'
down_revision: Union[str, None] = '0079'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == 'postgresql':
        # CREATE INDEX CONCURRENTLY требует не быть внутри транзакции
        with op.get_context().autocommit_block():
            op.execute(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_traffic_purchases_sub_expires '
                'ON traffic_purchases (subscription_id, expires_at)'
            )
            # Redundant: leftmost prefix покрывается композитным индексом выше.
            # Дроп уменьшает write amplification на INSERT/UPDATE/DELETE.
            op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_traffic_purchases_subscription_id')
    else:
        op.create_index(
            'ix_traffic_purchases_sub_expires',
            'traffic_purchases',
            ['subscription_id', 'expires_at'],
            unique=False,
        )
        try:
            op.drop_index('ix_traffic_purchases_subscription_id', table_name='traffic_purchases')
        except Exception:  # noqa: BLE001
            # SQLite/dev DB может не иметь этого индекса — норм
            pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == 'postgresql':
        with op.get_context().autocommit_block():
            # Восстанавливаем single-column index перед удалением composite
            op.execute(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_traffic_purchases_subscription_id '
                'ON traffic_purchases (subscription_id)'
            )
            op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_traffic_purchases_sub_expires')
    else:
        op.create_index(
            'ix_traffic_purchases_subscription_id',
            'traffic_purchases',
            ['subscription_id'],
            unique=False,
        )
        op.drop_index('ix_traffic_purchases_sub_expires', table_name='traffic_purchases')
