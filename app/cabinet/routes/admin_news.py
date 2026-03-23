"""Admin routes for managing news articles in cabinet."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.news import (
    create_news_article,
    delete_news_article,
    get_all_news,
    get_all_news_count,
    get_news_article_by_id,
    update_news_article,
)
from app.database.models import NewsArticle, User

from ..dependencies import get_cabinet_db, require_permission
from ..schemas.news import (
    NewsArticleListItem,
    NewsArticleResponse,
    NewsCreateRequest,
    NewsListResponse,
    NewsToggleResponse,
    NewsUpdateRequest,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/news', tags=['Cabinet Admin News'])


def _article_to_detail(article: NewsArticle) -> dict[str, Any]:
    """Convert NewsArticle ORM instance to full detail dict.

    Expects the ``author`` relationship to be eagerly loaded.
    """
    author_name: str | None = None
    if article.author:
        author_name = article.author.first_name or article.author.username or f'#{article.author.id}'

    return {
        'id': article.id,
        'title': article.title,
        'slug': article.slug,
        'content': article.content,
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
        'author_name': author_name,
        'created_at': article.created_at,
        'updated_at': article.updated_at,
    }


@router.get('', response_model=NewsListResponse)
async def list_all_news(
    admin: User = Depends(require_permission('news:read')),
    db: AsyncSession = Depends(get_cabinet_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> NewsListResponse:
    """Get all news articles (admin view, includes unpublished).

    articles and total are independent — run them concurrently.
    """
    try:
        articles, total = await asyncio.gather(
            get_all_news(db, limit=limit, offset=offset),
            get_all_news_count(db),
        )

        items = [NewsArticleListItem.model_validate(a) for a in articles]

        return NewsListResponse(items=items, total=total)
    except HTTPException:
        raise
    except Exception:
        logger.exception('Failed to list all news')
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to load news articles',
        )


@router.get('/{article_id}', response_model=NewsArticleResponse)
async def get_article_detail(
    article_id: int,
    admin: User = Depends(require_permission('news:read')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> NewsArticleResponse:
    """Get a single news article by ID (admin view)."""
    article = await get_news_article_by_id(db, article_id)
    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Article not found',
        )

    return NewsArticleResponse(**_article_to_detail(article))


@router.post('', response_model=NewsArticleResponse, status_code=status.HTTP_201_CREATED)
async def create_article(
    request: NewsCreateRequest,
    admin: User = Depends(require_permission('news:create')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> NewsArticleResponse:
    """Create a new news article."""
    try:
        article = await create_news_article(
            db,
            title=request.title,
            slug=request.slug,
            content=request.content,
            excerpt=request.excerpt,
            category=request.category,
            category_color=request.category_color,
            tag=request.tag,
            featured_image_url=request.featured_image_url,
            is_published=request.is_published,
            is_featured=request.is_featured,
            read_time_minutes=request.read_time_minutes,
            created_by=admin.id,
        )
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='An article with this slug already exists',
        )
    except Exception:
        logger.exception('Failed to create news article')
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to create article',
        )

    # Reload with author relationship
    article = await get_news_article_by_id(db, article.id)
    if not article:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to reload article after creation',
        )
    return NewsArticleResponse(**_article_to_detail(article))


@router.put('/{article_id}', response_model=NewsArticleResponse)
async def update_article(
    article_id: int,
    request: NewsUpdateRequest,
    admin: User = Depends(require_permission('news:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> NewsArticleResponse:
    """Update an existing news article."""
    article = await get_news_article_by_id(db, article_id)
    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Article not found',
        )

    try:
        update_data = request.model_dump(exclude_unset=True)
        article = await update_news_article(db, article, **update_data)
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='An article with this slug already exists',
        )
    except Exception:
        logger.exception('Failed to update news article', article_id=article_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to update article',
        )

    # Reload with author relationship (update used bulk UPDATE, author not populated)
    article = await get_news_article_by_id(db, article.id)
    if not article:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to reload article after update',
        )
    return NewsArticleResponse(**_article_to_detail(article))


@router.delete('/{article_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove_article(
    article_id: int,
    admin: User = Depends(require_permission('news:delete')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> None:
    """Delete a news article."""
    article = await get_news_article_by_id(db, article_id)
    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Article not found',
        )

    try:
        await delete_news_article(db, article)
    except Exception:
        logger.exception('Failed to delete news article', article_id=article_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to delete article',
        )


@router.post('/{article_id}/publish', response_model=NewsToggleResponse)
async def toggle_publish(
    article_id: int,
    admin: User = Depends(require_permission('news:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> NewsToggleResponse:
    """Toggle the published status of a news article."""
    article = await get_news_article_by_id(db, article_id)
    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Article not found',
        )

    new_published = not article.is_published

    update_kwargs: dict[str, Any] = {'is_published': new_published}
    # Auto-set published_at on first publish
    if new_published and article.published_at is None:
        update_kwargs['published_at'] = datetime.now(UTC)

    try:
        article = await update_news_article(db, article, **update_kwargs)
        return NewsToggleResponse(
            id=article.id,
            is_published=article.is_published,
            is_featured=article.is_featured,
            published_at=article.published_at,
        )
    except Exception:
        logger.exception('Failed to toggle publish', article_id=article_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to toggle publish status',
        )


@router.post('/{article_id}/feature', response_model=NewsToggleResponse)
async def toggle_featured(
    article_id: int,
    admin: User = Depends(require_permission('news:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
) -> NewsToggleResponse:
    """Toggle the featured status of a news article."""
    article = await get_news_article_by_id(db, article_id)
    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Article not found',
        )

    try:
        article = await update_news_article(db, article, is_featured=not article.is_featured)
        return NewsToggleResponse(
            id=article.id,
            is_published=article.is_published,
            is_featured=article.is_featured,
            published_at=article.published_at,
        )
    except Exception:
        logger.exception('Failed to toggle featured', article_id=article_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to toggle featured status',
        )
