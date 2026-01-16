"""Cabinet API routes."""

from fastapi import APIRouter

from .auth import router as auth_router
from .subscription import router as subscription_router
from .balance import router as balance_router
from .referral import router as referral_router
from .tickets import router as tickets_router
from .admin_tickets import router as admin_tickets_router
from .admin_settings import router as admin_settings_router
from .admin_apps import router as admin_apps_router
from .promocode import router as promocode_router
from .contests import router as contests_router
from .polls import router as polls_router
from .promo import router as promo_router
from .notifications import router as notifications_router
from .info import router as info_router
from .branding import router as branding_router
from .wheel import router as wheel_router
from .admin_wheel import router as admin_wheel_router
from .admin_tariffs import router as admin_tariffs_router
from .admin_servers import router as admin_servers_router
from .admin_stats import router as admin_stats_router
from .admin_ban_system import router as admin_ban_system_router
from .media import router as media_router

# Main cabinet router
router = APIRouter(prefix="/cabinet", tags=["Cabinet"])

# Include all sub-routers
router.include_router(auth_router)
router.include_router(subscription_router)
router.include_router(balance_router)
router.include_router(referral_router)
router.include_router(tickets_router)
router.include_router(promocode_router)
router.include_router(contests_router)
router.include_router(polls_router)
router.include_router(promo_router)
router.include_router(notifications_router)
router.include_router(info_router)
router.include_router(branding_router)
router.include_router(media_router)

# Wheel routes
router.include_router(wheel_router)

# Admin routes
router.include_router(admin_tickets_router)
router.include_router(admin_settings_router)
router.include_router(admin_apps_router)
router.include_router(admin_wheel_router)
router.include_router(admin_tariffs_router)
router.include_router(admin_servers_router)
router.include_router(admin_stats_router)
router.include_router(admin_ban_system_router)

__all__ = ["router"]
