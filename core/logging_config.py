"""
Enhanced logging configuration using Loguru.

Provides structured logging with:
- JSON formatting for production
- Colored output for development
- Log rotation and retention
- Sentry integration
- Request/response logging
"""

import logging
import sys
from pathlib import Path

from django.conf import settings
from loguru import logger

from core.logging_redaction import patch_loguru_record


class InterceptHandler(logging.Handler):
    """
    Intercept standard logging messages toward Loguru.
    
    This handler intercepts all standard library logging and redirects
    it to Loguru for unified logging.
    """

    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging():
    """
    Configure Loguru logging for the application.
    
    Sets up:
    - Console handler with appropriate formatting
    - File handler with rotation
    - Sentry integration for errors
    - Intercept standard library logging
    """
    # Remove default Loguru handler
    logger.remove()
    logger.configure(patcher=patch_loguru_record)

    # Determine log level from settings
    log_level = getattr(settings, "LOG_LEVEL", "INFO")

    # Console handler
    if getattr(settings, "DEBUG", False):
        # Development: colored, readable format
        logger.add(
            sys.stderr,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>",
            level=log_level,
            colorize=True,
            backtrace=True,
            diagnose=True,
        )
    else:
        # Production: JSON format for structured logging
        logger.add(
            sys.stderr,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
            level=log_level,
            serialize=True,  # JSON output
            backtrace=False,
            diagnose=False,
        )

    # File handler with rotation
    log_dir = Path(settings.BASE_DIR) / "logs"
    log_dir.mkdir(exist_ok=True)

    logger.add(
        log_dir / "app.log",
        rotation="10 MB",  # Rotate when file reaches 10 MB
        retention="30 days",  # Keep logs for 30 days
        compression="gz",  # Compress rotated files
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level=log_level,
        backtrace=True,
        diagnose=True,
    )

    # Error log file (only errors and above)
    logger.add(
        log_dir / "error.log",
        rotation="10 MB",
        retention="90 days",  # Keep error logs longer
        compression="gz",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level="ERROR",
        backtrace=True,
        diagnose=True,
    )

    # Intercept standard library logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Redirect specific loggers to Loguru
    for log_name in ("django", "django.request", "django.db.backends", "celery"):
        logging_logger = logging.getLogger(log_name)
        logging_logger.handlers = [InterceptHandler()]
        logging_logger.propagate = False

    # Sentry integration for errors
    if getattr(settings, "SENTRY_DSN", None):
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration

            # Add Sentry handler for ERROR and above
            logger.add(
                lambda msg: sentry_sdk.capture_message(msg.record["message"])
                if msg.record["level"].no >= 40
                else None,
                level="ERROR",
            )
        except ImportError:
            pass

    return logger


def get_logger(name: str = None):
    """
    Get a logger instance with optional name binding.
    
    Args:
        name: Logger name (usually __name__)
        
    Returns:
        Configured logger instance
    """
    if name:
        return logger.bind(name=name)
    return logger


# Request logging middleware helper
def log_request(request, response=None, duration=None):
    """
    Log HTTP request/response details.
    
    Args:
        request: Django request object
        response: Django response object (optional)
        duration: Request duration in seconds (optional)
    """
    log_data = {
        "method": request.method,
        "path": request.path,
        "user": str(getattr(request, "user", "anonymous")),
        "ip": request.META.get("REMOTE_ADDR", ""),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }

    if response:
        log_data["status_code"] = response.status_code

    if duration:
        log_data["duration_seconds"] = round(duration, 3)

    if response and response.status_code >= 400:
        logger.warning("Request failed", **log_data)
    else:
        logger.info("Request completed", **log_data)
