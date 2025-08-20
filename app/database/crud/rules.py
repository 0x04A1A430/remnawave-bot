import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.database.models import ServiceRule

logger = logging.getLogger(__name__)


async def get_rules_by_language(db: AsyncSession, language: str = "ru") -> Optional[ServiceRule]:
    result = await db.execute(
        select(ServiceRule)
        .where(
            ServiceRule.language == language,
            ServiceRule.is_active == True
        )
        .order_by(ServiceRule.order, ServiceRule.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_or_update_rules(
    db: AsyncSession,
    content: str,
    language: str = "ru",
    title: str = "Правила сервиса"
) -> ServiceRule:
    
    existing_rules_result = await db.execute(
        select(ServiceRule).where(
            ServiceRule.language == language,
            ServiceRule.is_active == True
        )
    )
    existing_rules = existing_rules_result.scalars().all()
    
    for rule in existing_rules:
        rule.is_active = False
        rule.updated_at = datetime.utcnow()
    
    new_rules = ServiceRule(
        title=title,
        content=content,
        language=language,
        is_active=True,
        order=0
    )
    
    db.add(new_rules)
    await db.commit()
    await db.refresh(new_rules)
    
    logger.info(f"✅ Правила для языка {language} обновлены")
    return new_rules


async def get_current_rules_content(db: AsyncSession, language: str = "ru") -> str:
    rules = await get_rules_by_language(db, language)
    
    if rules:
        return rules.content
    else:
        return """
🔒 <b>Правила использования сервиса</b>

1. Сервис предоставляется "как есть" без каких-либо гарантий.

2. Запрещается использование сервиса для незаконных действий.

3. Администрация оставляет за собой право заблокировать доступ пользователя при нарушении правил.

4. Возврат средств осуществляется в соответствии с политикой возврата.

5. Пользователь несет полную ответственность за безопасность своего аккаунта.

6. При возникновении вопросов обращайтесь в техническую поддержку.

Используя сервис, вы соглашаетесь с данными правилами.
"""