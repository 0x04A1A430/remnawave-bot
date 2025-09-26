"""Пакет с FastAPI-приложением для веб-админки."""

from .server import WebAPIServer, create_app

__all__ = ["WebAPIServer", "create_app"]
