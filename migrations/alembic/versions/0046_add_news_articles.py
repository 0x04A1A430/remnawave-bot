"""add news_articles table

Revision ID: 0046
Revises: 0045
Create Date: 2026-03-23

Adds news_articles table for the cabinet news/blog feature.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0046'
down_revision: str | None = '0045'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'news_articles',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('slug', sa.String(500), nullable=False),
        sa.Column('content', sa.Text(), nullable=False, server_default=''),
        sa.Column('excerpt', sa.Text(), nullable=True),
        sa.Column('category', sa.String(100), nullable=False, server_default=''),
        sa.Column('category_color', sa.String(20), nullable=False, server_default='#00e5a0'),
        sa.Column('tag', sa.String(50), nullable=True),
        sa.Column('featured_image_url', sa.Text(), nullable=True),
        sa.Column('is_published', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_featured', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('read_time_minutes', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('views_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_news_articles_slug', 'news_articles', ['slug'], unique=True)
    op.create_index('ix_news_articles_published_at', 'news_articles', ['published_at'])


def downgrade() -> None:
    op.drop_index('ix_news_articles_published_at', table_name='news_articles')
    op.drop_index('ix_news_articles_slug', table_name='news_articles')
    op.drop_table('news_articles')
