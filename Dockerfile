# Multi-stage Dockerfile for MyChama Backend

# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

WORKDIR /app

# Install system dependencies needed to build Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies into /root/.local
COPY requirements.txt .

RUN python -m pip install --upgrade pip setuptools wheel && \
    pip install \
    --default-timeout=300 \
    --retries=10 \
    --no-cache-dir \
    --user \
    -r requirements.txt


# Stage 2: Production image
FROM python:3.12-slim AS production

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PATH=/root/.local/bin:$PATH

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r django && useradd -r -g django django

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/logs /app/media /app/staticfiles && \
    chown -R django:django /app /root/.local

# Collect static files
RUN python manage.py collectstatic --noinput

# Switch to non-root user
USER django

# Expose port used inside container
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

# Run gunicorn
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--threads", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]