FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_USER=appuser
ENV APP_UID=10001

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN useradd --create-home --uid ${APP_UID} --shell /usr/sbin/nologin ${APP_USER} \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R ${APP_USER}:${APP_USER} /app

USER ${APP_USER}

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
