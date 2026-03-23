"""Public news routes for cabinet - user-facing news/blog section."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.news import (
    get_news_article_by_slug,
    get_news_categories,
    get_published_news,
    get_published_news_count,
    increment_views,
)
from app.database.models import User

from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.news import (
    NewsArticleListItem,
    NewsArticleResponse,
    NewsListResponse,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/news', tags=['Cabinet News'])


def _article_to_response(article, *, include_content: bool = True) -> dict:
    """Convert NewsArticle ORM instance to response dict."""
    author_name = None
    if article.author:
        author_name = article.author.first_name or article.author.username or f'#{article.author.id}'

    data = {
        'id': article.id,
        'title': article.title,
        'slug': article.slug,
        'excerpt': article.excerpt,
        'category': article.category,
        'category_color': article.category_color,
        'tag': article.tag,
        'featured_image_url': article.featured_image_url,
        'is_published': article.is_published,
        'is_featured': article.is_featured,
        'published_at': article.published_at,
        'read_time_minutes': article.read_time_minutes,
        'views_count': article.views_count,
    }

    if include_content:
        data['content'] = article.content
        data['author_name'] = author_name
        data['created_at'] = article.created_at
        data['updated_at'] = article.updated_at

    return data


# NOTE: /categories MUST be declared before /{slug} to avoid route conflict
@router.get('/categories', response_model=list[str])
async def list_categories(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> list[str]:
    """Get list of distinct news categories."""
    try:
        return await get_news_categories(db)
    except Exception as e:
        logger.error('Failed to get news categories', error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to load categories',
        )


@router.get('', response_model=NewsListResponse)
async def list_published_news(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
    category: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> NewsListResponse:
    """Get paginated list of published news articles."""
    try:
        articles = await get_published_news(db, category=category, limit=limit, offset=offset)
        total = await get_published_news_count(db, category=category)
        categories = await get_news_categories(db)

        items = [NewsArticleListItem(**_article_to_response(a, include_content=False)) for a in articles]

        return NewsListResponse(items=items, total=total, categories=categories)
    except HTTPException:
        raise
    except Exception as e:
        logger.error('Failed to list published news', error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to load news',
        )


@router.get('/{slug}', response_model=NewsArticleResponse)
async def get_article_by_slug(
    slug: str,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> NewsArticleResponse:
    """Get a single published news article by slug. Increments view count."""
    article = await get_news_article_by_slug(db, slug)

    if not article or not article.is_published:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Article not found',
        )

    # Increment views in background-safe manner (no error propagation)
    try:
        await increment_views(db, article.id)
        await db.refresh(article)
    except Exception:
        logger.warning('Failed to increment views', article_id=article.id)

    return NewsArticleResponse(**_article_to_response(article, include_content=True))
