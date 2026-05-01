try:
    from config.celery import app as celery_app
    __all__ = ("celery_app",)
except ImportError:
    celery_app = None
    __all__ = ("celery_app",)
