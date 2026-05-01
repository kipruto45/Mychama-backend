import os

from celery import Celery


def _default_settings_module() -> str:
    configured = os.getenv("DJANGO_SETTINGS_MODULE")
    if configured:
        return configured

    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("PYTEST_VERSION"):
        return "config.settings.test"

    return "config.settings.development"


os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    _default_settings_module(),
)

app = Celery("digital_chama_system")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Load schedule from celery_schedule.py
app.conf.beat_schedule = {}
try:
    from config.celery_schedule import CELERY_BEAT_SCHEDULE
    app.conf.beat_schedule = CELERY_BEAT_SCHEDULE
except ImportError:
    pass


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
