"""Celery worker package."""

from app.worker.celery_app import celery_app

__all__ = ["celery_app"]
