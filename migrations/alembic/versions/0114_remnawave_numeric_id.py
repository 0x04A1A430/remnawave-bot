"""add numeric remnawave_id identity columns (Remnawave 3.0.0)

Remnawave 3.0.0 removed the ``uuid`` field from ``UsersSchema`` entirely: a
panel user is now identified only by a numeric ``id``.  This fork previously
tracked that id in ``panel_user_id`` (revision 0099).  This revision converges
the schema to the upstream shape: it adds ``remnawave_id`` columns with the same
index/constraint layout as upstream 0104, copies the already-populated values
from ``panel_user_id``, and then drops the legacy columns so the database ends
up indistinguishable from upstream (plus the fork's extra feature columns).

Empty (NULL) ``panel_user_id`` rows stay unresolved: their ``remnawave_id`` is
NULL until the one-shot backfill service has talked to the panel.

Revision ID: 0114
Revises: 0113
Create Date: 2026-08-10

"""

import sqlalchemy as sa
from alembic import op


revision = '0114'
down_revision = '0113'
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> dict[str, dict]:
    return {column['name']: column for column in inspector.get_columns(table)}


def _index_names(inspector: sa.Inspector, table: str) -> set[str]:
    return {str(item['name']) for item in inspector.get_indexes(table) if item.get('name')}


def _copy_from_panel_user_id(table: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = _columns(inspector, table)
    if 'panel_user_id' in cols and 'remnawave_id' in cols:
        bind.execute(
            sa.text(
                f'UPDATE {table} SET remnawave_id = panel_user_id '
                'WHERE panel_user_id IS NOT NULL AND remnawave_id IS NULL'
            )
        )


def _drop_panel_user_id(table: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = _columns(inspector, table)
    if 'panel_user_id' not in cols:
        return
    for index in ('ix_users_panel_user_id', 'ix_subscriptions_panel_user_id'):
        if index in _index_names(inspector, table):
            op.drop_index(index, table_name=table)
    with op.batch_alter_table(table) as batch:
        batch.drop_column('panel_user_id')


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # --- users --------------------------------------------------------------
    if 'users' in tables and 'remnawave_id' not in _columns(inspector, 'users'):
        op.add_column('users', sa.Column('remnawave_id', sa.BigInteger(), nullable=True))

    _copy_from_panel_user_id('users')

    inspector = sa.inspect(bind)
    if 'users' in tables and 'ix_users_remnawave_id' not in _index_names(inspector, 'users'):
        # unique=True doubles as the uniqueness guarantee for users.remnawave_id;
        # NULLs do not collide in either backend.
        op.create_index('ix_users_remnawave_id', 'users', ['remnawave_id'], unique=True)

    # --- subscriptions ------------------------------------------------------
    if 'subscriptions' in tables:
        inspector = sa.inspect(bind)
        if 'remnawave_id' not in _columns(inspector, 'subscriptions'):
            op.add_column('subscriptions', sa.Column('remnawave_id', sa.BigInteger(), nullable=True))

        _copy_from_panel_user_id('subscriptions')

        inspector = sa.inspect(bind)
        subscription_indexes = _index_names(inspector, 'subscriptions')
        if 'uq_subscriptions_remnawave_id' not in subscription_indexes and 'ix_subscriptions_remnawave_id' not in subscription_indexes:
            # NOTE (fork divergence): upstream 0104 uses a PARTIAL UNIQUE index
            # here, but this fork stores one panel account id per subscription
            # row (single-tariff tariff switches reuse the same panel account), so
            # duplicates are legitimate.  Keep the column SAME as upstream but the
            # index non-unique, or existing installs cannot be migrated.
            op.create_index('ix_subscriptions_remnawave_id', 'subscriptions', ['remnawave_id'], unique=False)
        if 'ix_subscriptions_remnawave_short_uuid' not in subscription_indexes:
            op.create_index(
                'ix_subscriptions_remnawave_short_uuid',
                'subscriptions',
                ['remnawave_short_uuid'],
                unique=False,
            )

    # --- grace_access_sessions ---------------------------------------------
    if 'grace_access_sessions' in tables:
        inspector = sa.inspect(bind)
        grace_columns = _columns(inspector, 'grace_access_sessions')

        if 'remnawave_id' not in grace_columns:
            op.add_column('grace_access_sessions', sa.Column('remnawave_id', sa.BigInteger(), nullable=True))

        # The panel can no longer supply a uuid, so new sessions must be allowed
        # to leave the historical column empty.
        if grace_columns.get('remnawave_uuid', {}).get('nullable') is False:
            with op.batch_alter_table('grace_access_sessions') as batch:
                batch.alter_column(
                    'remnawave_uuid',
                    existing_type=sa.String(length=255),
                    nullable=True,
                )

        inspector = sa.inspect(bind)
        if 'ix_grace_access_sessions_remnawave_id' not in _index_names(inspector, 'grace_access_sessions'):
            op.create_index(
                'ix_grace_access_sessions_remnawave_id',
                'grace_access_sessions',
                ['remnawave_id'],
                unique=False,
            )

    # --- drop legacy panel_user_id columns ---------------------------------
    if 'users' in tables:
        _drop_panel_user_id('users')
    if 'subscriptions' in tables:
        _drop_panel_user_id('subscriptions')


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'grace_access_sessions' in tables:
        grace_indexes = _index_names(inspector, 'grace_access_sessions')
        if 'ix_grace_access_sessions_remnawave_id' in grace_indexes:
            op.drop_index('ix_grace_access_sessions_remnawave_id', table_name='grace_access_sessions')

        null_uuids = bind.execute(
            sa.text('SELECT COUNT(*) FROM grace_access_sessions WHERE remnawave_uuid IS NULL')
        ).scalar_one()
        if null_uuids:
            raise RuntimeError(
                f'Cannot downgrade 0114: {null_uuids} grace_access_sessions row(s) have no historical '
                'remnawave_uuid, so dropping remnawave_id would erase their only panel identity.'
            )
        with op.batch_alter_table('grace_access_sessions') as batch:
            batch.alter_column('remnawave_uuid', existing_type=sa.String(length=255), nullable=False)

        inspector = sa.inspect(bind)
        if 'remnawave_id' in _columns(inspector, 'grace_access_sessions'):
            op.drop_column('grace_access_sessions', 'remnawave_id')

    if 'subscriptions' in tables:
        inspector = sa.inspect(bind)
        subscription_indexes = _index_names(inspector, 'subscriptions')
        if 'ix_subscriptions_remnawave_short_uuid' in subscription_indexes:
            op.drop_index('ix_subscriptions_remnawave_short_uuid', table_name='subscriptions')
        if 'ix_subscriptions_remnawave_id' in subscription_indexes or 'uq_subscriptions_remnawave_id' in subscription_indexes:
            op.drop_index('ix_subscriptions_remnawave_id', table_name='subscriptions')
        inspector = sa.inspect(bind)
        if 'remnawave_id' in _columns(inspector, 'subscriptions'):
            op.drop_column('subscriptions', 'remnawave_id')

    if 'users' in tables:
        inspector = sa.inspect(bind)
        if 'ix_users_remnawave_id' in _index_names(inspector, 'users'):
            op.drop_index('ix_users_remnawave_id', table_name='users')
        inspector = sa.inspect(bind)
        if 'remnawave_id' in _columns(inspector, 'users'):
            op.drop_column('users', 'remnawave_id')