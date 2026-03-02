# =============================================================================
# Digital Chama - Development Testing Makefile
# =============================================================================
# Use this Makefile for local development, testing, and Docker operations
#
# Usage:
#   make up              - Start all services
#   make down            - Stop all services
#   make test            - Run tests
#   make smoke           - Run smoke tests
# =============================================================================

.PHONY: help up down logs migrate seed test smoke clean restart status

# Configuration
DC = docker-compose
PROJECT_NAME = digital-chama
BACKEND_URL = http://localhost:8000

# Colors
GREEN = \033[0;32m
YELLOW = \033[1;33m
BLUE = \033[0;34m
NC = \033[0m

# =============================================================================
# Docker Commands
# =============================================================================

help:
	@echo ""
	@echo -e "$(BLUE)=========================================$(NC)"
	@echo -e "$(BLUE) Digital Chama - Development Commands $(NC)"
	@echo -e "$(BLUE)=========================================$(NC)"
	@echo ""
	@echo -e "$(GREEN)Docker Commands:$(NC)"
	@echo "  make up              - Start all Docker services"
	@echo "  make down            - Stop all Docker services"
	@echo "  make restart         - Restart all services"
	@echo "  make logs             - View all logs"
	@echo "  make logs-web        - View web container logs"
	@echo "  make logs-celery     - View celery logs"
	@echo ""
	@echo -e "$(GREEN)Django Commands:$(NC)"
	@echo "  make migrate         - Run database migrations"
	@echo "  make makemigrations  - Create new migrations"
	@echo "  make seed            - Seed database with test data"
	@echo "  make superuser       - Create superuser"
	@echo "  make collectstatic   - Collect static files"
	@echo ""
	@echo -e "$(GREEN)Testing Commands:$(NC)"
	@echo "  make test            - Run pytest"
	@echo "  make smoke           - Run smoke tests"
	@echo "  make health          - Check health endpoints"
	@echo "  make db-shell        - Open database shell"
	@echo ""
	@echo -e "$(GREEN)Development:$(NC)"
	@echo "  make clean           - Clean up containers and volumes"
	@echo "  make status          - Show service status"
	@echo ""

up:
	@echo "Starting Docker services..."
	$(DC) up -d
	@echo "Waiting for services to be ready..."
	@sleep 5
	@$(MAKE) status

down:
	@echo "Stopping Docker services..."
	$(DC) down

restart:
	@echo "Restarting Docker services..."
	$(DC) restart

logs:
	$(DC) logs -f

logs-web:
	$(DC) logs -f web

logs-celery:
	$(DC) logs -f celery_worker

status:
	@echo ""
	@echo "Service Status:"
	@$(DC) ps
	@echo ""
	@echo "Health Checks:"
	@curl -s $(BACKEND_URL)/health/ | grep -o '"status":"[^"]*"' || echo "Backend not responding"
	@echo ""

# =============================================================================
# Django Commands
# =============================================================================

migrate:
	@echo "Running migrations..."
	$(DC) exec -T web python manage.py migrate --noinput

makemigrations:
	@echo "Creating migrations..."
	$(DC) exec -T web python manage.py makemigrations

seed:
	@echo "Seeding database..."
	$(DC) exec -T web python manage.py seed_users --count=50 || echo "Seeding command not found"

superuser:
	@echo "Creating superuser..."
	$(DC) exec -T web python manage.py createsuperuser

collectstatic:
	@echo "Collecting static files..."
	$(DC) exec -T web python manage.py collectstatic --noinput

shell:
	@echo "Opening Django shell..."
	$(DC) exec web python manage.py shell

db-shell:
	@echo "Opening database shell..."
	$(DC) exec -T postgres psql -U chama -d digital_chama_dev

# =============================================================================
# Testing Commands
# =============================================================================

test:
	@echo "Running pytest..."
	$(DC) exec -T web pytest -v --tb=short

test-coverage:
	@echo "Running pytest with coverage..."
	$(DC) exec -T web pytest --cov=. --cov-report=html --cov-report=term

smoke:
	@echo "Running smoke tests..."
	@chmod +x testing/smoke_test.sh
	$(DC) exec -T web bash -c "cd testing && ./smoke_test.sh" || ./testing/smoke_test.sh

health:
	@echo "Checking health endpoints..."
	@echo ""
	@echo "Main Health:"
	@curl -s $(BACKEND_URL)/health/
	@echo ""
	@echo ""
	@echo "Database Health:"
	@curl -s $(BACKEND_URL)/health/db/
	@echo ""
	@echo ""
	@echo "Redis Health:"
	@curl -s $(BACKEND_URL)/health/redis/
	@echo ""

# =============================================================================
# Utility Commands
# =============================================================================

clean:
	@echo "Cleaning up..."
	$(DC) down -v
	@echo "Removed containers and volumes"

build:
	@echo "Building Docker images..."
	$(DC) build

pull:
	@echo "Pulling latest images..."
	$(DC) pull

# =============================================================================
# Quick Start
# =============================================================================

bootstrap: up migrate seed
	@echo ""
	@echo "========================================="
	@echo "Bootstrap complete!"
	@echo "Backend: $(BACKEND_URL)"
	@echo "Admin:   $(BACKEND_URL)/admin"
	@echo "========================================="
