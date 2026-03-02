# =============================================================================
# Digital Chama - Makefile for Development
# =============================================================================
# Usage: make <target>
#
# Targets:
#   up          - Start all Docker services
#   down        - Stop all Docker services
#   restart     - Restart all Docker services
#   logs        - View logs (all services)
#   logs-web    - View web logs
#   logs-worker - View worker logs
#   logs-db     - View database logs
#   migrate     - Run database migrations
#   makemigrations - Create new migrations
#   seed        - Seed development data
#   createsuperuser - Create a superuser
#   test        - Run tests
#   test-coverage - Run tests with coverage
#   shell       - Open Django shell
#   dbshell     - Open PostgreSQL shell
#   redis-cli   - Open Redis CLI
#   clean       - Clean up containers and volumes
#   health      - Run health checks
#   bootstrap   - Run full bootstrap (up + migrate + seed)
#   smtp-test   - Test email sending
#   sms-test    - Test SMS sending
#   otp-test    - Test OTP sending
#   flower      - Open Flower monitoring
# =============================================================================

# Default target
.PHONY: help
help:
	@echo "Digital Chama - Available Make Targets"
	@echo "======================================="
	@echo ""
	@echo "Docker Commands:"
	@echo "  make up           - Start all services"
	@echo "  make down         - Stop all services"
	@echo "  make restart      - Restart all services"
	@echo "  make logs         - View all logs"
	@echo "  make logs-web     - View web logs"
	@echo "  make logs-worker  - View worker logs"
	@echo "  make logs-db      - View database logs"
	@echo ""
	@echo "Database Commands:"
	@echo "  make migrate      - Run migrations"
	@echo "  make makemigrations - Create migrations"
	@echo "  make dbshell      - Open PostgreSQL shell"
	@echo "  make shell        - Open Django shell"
	@echo ""
	@echo "Development Commands:"
	@echo "  make seed         - Seed development data"
	@echo "  make createsuperuser - Create superuser"
	@echo "  make bootstrap    - Full bootstrap (up + migrate + seed)"
	@echo ""
	@echo "Testing Commands:"
	@echo "  make test         - Run tests"
	@echo "  make test-coverage - Run tests with coverage"
	@echo "  make smtp-test    - Test email sending"
	@echo "  make sms-test     - Test SMS sending"
	@echo "  make otp-test     - Test OTP sending"
	@echo ""
	@echo "Utilities:"
	@echo "  make health       - Run health checks"
	@echo "  make redis-cli    - Open Redis CLI"
	@echo "  make flower       - Open Flower monitoring"
	@echo "  make clean        - Clean up containers and volumes"
	@echo ""

# Docker Commands
.PHONY: up
up:
	docker-compose up -d
	@echo "Waiting for services to be ready..."
	@sleep 5
	@echo "Services started. Check health with: make health"

.PHONY: down
down:
	docker-compose down

.PHONY: restart
restart:
	docker-compose restart

.PHONY: logs
logs:
	docker-compose logs -f

.PHONY: logs-web
logs-web:
	docker-compose logs -f web

.PHONY: logs-worker
logs-worker:
	docker-compose logs -f worker

.PHONY: logs-db
logs-db:
	docker-compose logs -f postgres

.PHONY: logs-redis
logs-redis:
	docker-compose logs -f redis

.PHONY: ps
ps:
	docker-compose ps

# Database Commands
.PHONY: migrate
migrate:
	docker-compose exec -T web python manage.py migrate

.PHONY: makemigrations
makemigrations:
	docker-compose exec -T web python manage.py makemigrations

.PHONY: dbshell
dbshell:
	docker-compose exec postgres psql -U digital_chama -d digital_chama

.PHONY: shell
shell:
	docker-compose exec web python manage.py shell

.PHONY: createsuperuser
createsuperuser:
	docker-compose exec -T web python manage.py createsuperuser

# Development Data
.PHONY: seed
seed:
	docker-compose exec -T web python manage.py seed_users --count 50

.PHONY: seed-10
seed-10:
	docker-compose exec -T web python manage.py seed_users --count 10

.PHONY: bootstrap
bootstrap:
	@echo "Running full bootstrap..."
	@echo "1. Starting services..."
	docker-compose up -d
	@echo "2. Waiting for database..."
	@sleep 10
	@echo "3. Running migrations..."
	make migrate
	@echo "4. Collecting static files..."
	docker-compose exec -T web python manage.py collectstatic --noinput
	@echo "5. Checking health..."
	make health

# Testing
.PHONY: test
test:
	docker-compose exec -T web python -m pytest -v

.PHONY: test-coverage
test-coverage:
	docker-compose exec -T web python -m pytest --cov=. --cov-report=html --cov-report=term

.PHONY: test-apps
test-apps:
	docker-compose exec -T web python -m pytest apps/ -v

.PHONY: test-accounts
test-accounts:
	docker-compose exec -T web python -m pytest apps/accounts/ -v

.PHONY: test-notifications
test-notifications:
	docker-compose exec -T web python -m pytest apps/notifications/ -v

.PHONY: test-ai
test-ai:
	docker-compose exec -T web python -m pytest apps/ai/ -v

# Service Testing
.PHONY: health
health:
	@echo "Running health checks..."
	@echo ""
	@echo "Basic Health:"
	@curl -sf http://localhost:8000/health/ | python3 -m json.tool || echo "FAILED"
	@echo ""
	@echo "Notifications Health:"
	@curl -sf http://localhost:8000/health/notifications/ | python3 -m json.tool || echo "FAILED"

.PHONY: smtp-test
smtp-test:
	@echo "Testing email sending..."
	@echo "Enter email address:"
	@read email && docker-compose exec -T web python manage.py test_email $$email

.PHONY: sms-test
sms-test:
	@echo "Testing SMS sending..."
	@echo "Enter phone number (E.164 format, e.g., +254712345678):"
	@read phone && docker-compose exec -T web python manage.py test_sms $$phone

.PHONY: otp-test
otp-test:
	@echo "Testing OTP sending..."
	@echo "Enter phone number (E.164 format, e.g., +254712345678):"
	@read phone && docker-compose exec -T web python manage.py test_otp --phone $$phone --channel sms

# Utilities
.PHONY: redis-cli
redis-cli:
	docker-compose exec redis redis-cli

.PHONY: flower
flower:
	@echo "Flower monitoring available at: http://localhost:5556"

.PHONY: clean
clean:
	docker-compose down -v
	@echo "Cleaned up containers and volumes"

.PHONY: rebuild
rebuild:
	docker-compose up -d --build --force-recreate

.PHONY: flush-redis
flush-redis:
	docker-compose exec redis redis-cli FLUSHALL

.PHONY: check
check:
	docker-compose exec -T web python manage.py check

.PHONY: show-urls
show-urls:
	docker-compose exec -T web python manage.py show_urls

.PHONY: showmigrations
showmigrations:
	docker-compose exec -T web python manage.py showmigrations

# Linting
.PHONY: lint
lint:
	docker-compose exec -T web flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics

.PHONY: format
format:
	docker-compose exec -T web black .

.PHONY: check-quality
check-quality:
	docker-compose exec -T web python check_quality.sh
