import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from aiogram import Bot

from app.config import settings
from app.database.crud.user import add_user_balance, get_user_by_id
from app.database.crud.referral import create_referral_earning
from app.database.models import TransactionType, ReferralEarning
from app.utils.user_utils import get_effective_referral_commission_percent

logger = logging.getLogger(__name__)


async def send_referral_notification(
    bot: Bot,
    user_id: int,
    message: str
):
    try:
        await bot.send_message(user_id, message, parse_mode="HTML")
        logger.info(f"✅ Уведомление отправлено пользователю {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления пользователю {user_id}: {e}")


async def process_referral_registration(
    db: AsyncSession,
    new_user_id: int,
    referrer_id: int,
    bot: Bot = None
):
    try:
        new_user = await get_user_by_id(db, new_user_id)
        referrer = await get_user_by_id(db, referrer_id)
        
        if not new_user or not referrer:
            logger.error(f"Пользователи не найдены: new_user_id={new_user_id}, referrer_id={referrer_id}")
            return False
        
        if new_user.referred_by_id != referrer_id:
            logger.error(f"Пользователь {new_user_id} не привязан к рефереру {referrer_id}")
            return False
        
        await create_referral_earning(
            db=db,
            user_id=referrer_id,
            referral_id=new_user_id,
            amount_kopeks=0,
            reason="referral_registration_pending"
        )

        try:
            from app.services.referral_contest_service import referral_contest_service

            await referral_contest_service.on_referral_registration(db, new_user_id)
        except Exception as exc:
            logger.debug("Не удалось записать конкурсную регистрацию: %s", exc)

        if bot:
            commission_percent = get_effective_referral_commission_percent(referrer)
            referral_notification = (
                f"🎉 <b>Добро пожаловать!</b>\n\n"
                f"Вы перешли по реферальной ссылке пользователя <b>{referrer.full_name}</b>!\n\n"
                f"💰 При первом пополнении от {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)} "
                f"вы получите бонус {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}!\n\n"
                # f"🎁 Ваш реферер также получит награду за ваше первое пополнение."
            )
            await send_referral_notification(bot, new_user.telegram_id, referral_notification)
            
            inviter_notification = (
                f"👥 <b>Новый реферал!</b>\n\n"
                f"По вашей ссылке зарегистрировался пользователь <b>{new_user.full_name}</b>!\n\n"
                f"💰 Когда он пополнит баланс от {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}, "
                f"вы получите минимум {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)} или "
                f"{commission_percent}% от суммы (что больше).\n\n"
                f"📈 С каждого последующего пополнения вы будете получать {commission_percent}% комиссии."
            )
            await send_referral_notification(bot, referrer.telegram_id, inviter_notification)
        
        logger.info(f"✅ Зарегистрирован реферал {new_user_id} для {referrer_id}. Бонусы будут выданы после пополнения.")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обработки реферальной регистрации: {e}")
        return False


async def process_referral_topup(
    db: AsyncSession,
    user_id: int, 
    topup_amount_kopeks: int,
    bot: Bot = None
):
    try:
        user = await get_user_by_id(db, user_id)
        if not user or not user.referred_by_id:
            logger.info(f"Пользователь {user_id} не является рефералом")
            return True
        
        referrer = await get_user_by_id(db, user.referred_by_id)
        if not referrer:
            logger.error(f"Реферер {user.referred_by_id} не найден")
            return False

        commission_percent = get_effective_referral_commission_percent(referrer)
        qualifies_for_first_bonus = (
            topup_amount_kopeks >= settings.REFERRAL_MINIMUM_TOPUP_KOPEKS
        )
        commission_amount = 0
        if commission_percent > 0:
            commission_amount = int(
                topup_amount_kopeks * commission_percent / 100
            )

        if not user.has_made_first_topup:
            if not qualifies_for_first_bonus:
                logger.info(
                    "Пополнение %s на %s₽ меньше минимума для первого бонуса, но комиссия будет начислена",
                    user_id,
                    topup_amount_kopeks / 100,
                )

                if commission_amount > 0:
                    await add_user_balance(
                        db,
                        referrer,
                        commission_amount,
                        f"Комиссия {commission_percent}% с пополнения {user.full_name}",
                        bot=bot,
                    )

                    await create_referral_earning(
                        db=db,
                        user_id=referrer.id,
                        referral_id=user.id,
                        amount_kopeks=commission_amount,
                        reason="referral_commission_topup",
                    )

                    logger.info(
                        "💰 Комиссия с пополнения: %s получил %s₽ (до первого бонуса)",
                        referrer.telegram_id,
                        commission_amount / 100,
                    )

                    if bot:
                        commission_notification = (
                            f"💰 <b>Реферальная комиссия!</b>\n\n"
                            f"Ваш реферал <b>{user.full_name}</b> пополнил баланс на "
                            f"{settings.format_price(topup_amount_kopeks)}\n\n"
                            f"🎁 Ваша комиссия ({commission_percent}%): "
                            f"{settings.format_price(commission_amount)}\n\n"
                            f"💎 Средства зачислены на ваш баланс."
                        )
                        await send_referral_notification(bot, referrer.telegram_id, commission_notification)

                return True

            user.has_made_first_topup = True
            await db.commit()
            
            try:
                await db.execute(
                    delete(ReferralEarning).where(
                        ReferralEarning.user_id == referrer.id,
                        ReferralEarning.referral_id == user.id, 
                        ReferralEarning.reason == "referral_registration_pending"
                    )
                )
                await db.commit()
                logger.info(f"🗑️ Удалена запись 'ожидание пополнения' для реферала {user.id}")
            except Exception as e:
                logger.error(f"Ошибка удаления записи ожидания: {e}")
            
            if settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS > 0:
                await add_user_balance(
                    db, user, settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS,
                    f"Бонус за первое пополнение по реферальной программе",
                    bot=bot
                )
                logger.info(f"💰 Реферал {user.id} получил бонус {settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS/100}₽")
                
                if bot:
                    bonus_notification = (
                        f"🎉 <b>Бонус получен!</b>\n\n"
                        f"За первое пополнение вы получили бонус "
                        f"{settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}!\n\n"
                        f"💎 Средства зачислены на ваш баланс."
                    )
                    await send_referral_notification(bot, user.telegram_id, bonus_notification)
            
            commission_amount = int(topup_amount_kopeks * commission_percent / 100)
            inviter_bonus = max(settings.REFERRAL_INVITER_BONUS_KOPEKS, commission_amount)

            if inviter_bonus > 0:
                await add_user_balance(
                    db, referrer, inviter_bonus,
                    f"Бонус за первое пополнение реферала {user.full_name}",
                    bot=bot
                )

                await create_referral_earning(
                    db=db,
                    user_id=referrer.id,
                    referral_id=user.id,
                    amount_kopeks=inviter_bonus,
                    reason="referral_first_topup"
                )
                logger.info(f"💰 Реферер {referrer.telegram_id} получил бонус {inviter_bonus/100}₽")

                if bot:
                    inviter_bonus_notification = (
                        f"💰 <b>Реферальная награда!</b>\n\n"
                        f"Ваш реферал <b>{user.full_name}</b> сделал первое пополнение!\n\n"
                        f"🎁 Вы получили награду: {settings.format_price(inviter_bonus)}\n\n"
                        f"📈 Теперь с каждого его пополнения вы будете получать {commission_percent}% комиссии."
                    )
                    await send_referral_notification(bot, referrer.telegram_id, inviter_bonus_notification)
        
        else:
            if commission_amount > 0:
                await add_user_balance(
                    db, referrer, commission_amount,
                    f"Комиссия {commission_percent}% с пополнения {user.full_name}",
                    bot=bot
                )

                await create_referral_earning(
                    db=db,
                    user_id=referrer.id,
                    referral_id=user.id,
                    amount_kopeks=commission_amount,
                    reason="referral_commission_topup"
                )

                logger.info(f"💰 Комиссия с пополнения: {referrer.telegram_id} получил {commission_amount/100}₽")

                if bot:
                    commission_notification = (
                        f"💰 <b>Реферальная комиссия!</b>\n\n"
                        f"Ваш реферал <b>{user.full_name}</b> пополнил баланс на "
                        f"{settings.format_price(topup_amount_kopeks)}\n\n"
                        f"🎁 Ваша комиссия ({commission_percent}%): "
                        f"{settings.format_price(commission_amount)}\n\n"
                        f"💎 Средства зачислены на ваш баланс."
                    )
                    await send_referral_notification(bot, referrer.telegram_id, commission_notification)
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обработки пополнения реферала: {e}")
        return False


async def process_referral_purchase(
    db: AsyncSession,
    user_id: int,
    purchase_amount_kopeks: int,
    transaction_id: int = None,
    bot: Bot = None
):
    try:
        user = await get_user_by_id(db, user_id)
        if not user or not user.referred_by_id:
            return True
        
        referrer = await get_user_by_id(db, user.referred_by_id)
        if not referrer:
            logger.error(f"Реферер {user.referred_by_id} не найден")
            return False
        
        commission_percent = get_effective_referral_commission_percent(referrer)
            
        commission_amount = int(purchase_amount_kopeks * commission_percent / 100)
        
        if commission_amount > 0:
            await add_user_balance(
                db, referrer, commission_amount,
                f"Комиссия {commission_percent}% с покупки {user.full_name}",
                bot=bot
            )
            
            await create_referral_earning(
                db=db,
                user_id=referrer.id,
                referral_id=user.id, 
                amount_kopeks=commission_amount,
                reason="referral_commission",
                referral_transaction_id=transaction_id
            )
            
            logger.info(f"💰 Комиссия с покупки: {referrer.telegram_id} получил {commission_amount/100}₽")
            
            if bot:
                purchase_commission_notification = (
                    f"💰 <b>Комиссия с покупки!</b>\n\n"
                    f"Ваш реферал <b>{user.full_name}</b> совершил покупку на "
                    f"{settings.format_price(purchase_amount_kopeks)}\n\n"
                    f"🎁 Ваша комиссия ({commission_percent}%): "
                    f"{settings.format_price(commission_amount)}\n\n"
                    f"💎 Средства зачислены на ваш баланс."
                )
                await send_referral_notification(bot, referrer.telegram_id, purchase_commission_notification)
        
        if not user.has_had_paid_subscription:
            user.has_had_paid_subscription = True
            await db.commit()
            logger.info(f"✅ Пользователь {user_id} отмечен как имевший платную подписку")
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обработки покупки реферала: {e}")
        import traceback
        logger.error(f"Полный traceback: {traceback.format_exc()}")
        return False
