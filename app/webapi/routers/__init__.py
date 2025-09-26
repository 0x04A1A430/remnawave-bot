from fastapi import APIRouter

from . import auth, health, payments, settings, stats, tickets, transactions, users

api_router = APIRouter(prefix="/api")

api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(stats.router, tags=["stats"])
api_router.include_router(users.router, tags=["users"])
api_router.include_router(transactions.router, tags=["transactions"])
api_router.include_router(payments.router, tags=["payments"])
api_router.include_router(tickets.router, tags=["tickets"])
api_router.include_router(settings.router, tags=["settings"])

__all__ = ["api_router"]
