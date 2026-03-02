#!/bin/bash
# =============================================================================
# Digital Chama - Development Bootstrap Script
# =============================================================================
# This script brings up the development environment, runs migrations,
# creates a superuser, seeds data, and verifies all services are running.
#
# Usage: ./dev_bootstrap.sh
# =============================================================================

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project directories
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR/digital_chama_system"

# Print banner
echo -e "${BLUE}"
echo "=========================================="
echo "  Digital Chama - Dev Bootstrap"
echo "=========================================="
echo -e "${NC}"

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================
echo -e "${YELLOW}Running pre-flight checks...${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker not found. Please install Docker.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker installed: $(docker --version)${NC}"

# Check Docker Compose
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}✗ Docker Compose not found. Please install Docker Compose.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker Compose available${NC}"

# Check .env file
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠ .env file not found. Creating from .env.example...${NC}"
    if [ -f .env.example ]; then
        cp .env.example .env
        echo -e "${YELLOW}⚠ Please edit .env and add your configuration before continuing.${NC}"
        exit 1
    else
        echo -e "${RED}✗ .env.example not found. Cannot create .env${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}✓ .env file exists${NC}"

# Check critical environment variables
echo -e "${YELLOW}Checking critical environment variables...${NC}"
source .env

REQUIRED_VARS=("POSTGRES_DB" "POSTGRES_USER" "POSTGRES_PASSWORD" "SECRET_KEY")
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo -e "${RED}✗ Required variable $var is not set in .env${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓ All required environment variables are set${NC}"

# =============================================================================
# DOCKER SERVICES
# =============================================================================
echo -e "\n${YELLOW}Starting Docker services...${NC}"

# Stop any existing containers first
echo "Stopping existing containers..."
docker-compose down --remove-orphans 2>/dev/null || true

# Build and start services
echo "Building and starting containers..."
docker-compose up -d --build

# Wait for services to be healthy
echo "Waiting for services to become healthy..."

# Wait for PostgreSQL
echo -n "Waiting for PostgreSQL..."
for i in {1..30}; do
    if docker-compose exec -T postgres pg_isready -U "${POSTGRES_USER:-digital_chama}" &>/dev/null; then
        echo -e "${GREEN} OK${NC}"
        break
    fi
    echo -n "."
    sleep 2
done

# Wait for Redis
echo -n "Waiting for Redis..."
for i in {1..15}; do
    if docker-compose exec -T redis redis-cli ping &>/dev/null; then
        echo -e "${GREEN} OK${NC}"
        break
    fi
    echo -n "."
    sleep 1
done

# Wait for Web service
echo -n "Waiting for Web service..."
for i in {1..30}; do
    if curl -sf http://localhost:8000/health/ &>/dev/null; then
        echo -e "${GREEN} OK${NC}"
        break
    fi
    echo -n "."
    sleep 2
done

# =============================================================================
# DATABASE MIGRATIONS
# =============================================================================
echo -e "\n${YELLOW}Running database migrations...${NC}"

docker-compose exec -T web python manage.py migrate --noinput
echo -e "${GREEN}✓ Migrations completed${NC}"

# =============================================================================
# COLLECT STATIC FILES
# =============================================================================
echo -e "\n${YELLOW}Collecting static files...${NC}"

docker-compose exec -T web python manage.py collectstatic --noinput --clear
echo -e "${GREEN}✓ Static files collected${NC}"

# =============================================================================
# CREATE SUPERUSER (Interactive)
# =============================================================================
echo -e "\n${YELLOW}Creating superuser...${NC}"

# Check if superuser already exists
SUPERUSER_EXISTS=$(docker-compose exec -T web python -c "from django.contrib.auth import get_user_model; User = get_user_model(); print('yes' if User.objects.filter(is_superuser=True).exists() else 'no')")

if [ "$SUPERUSER_EXISTS" = "yes" ]; then
    echo -e "${YELLOW}⚠ Superuser already exists. Skipping creation.${NC}"
else
    echo -e "${YELLOW}Creating superuser interactively...${NC}"
    docker-compose exec -T web python manage.py createsuperuser || true
fi

# =============================================================================
# SEED DEV DATA (Optional)
# =============================================================================
echo -e "\n${YELLOW}Checking for seed data...${NC}"

# Check if seed command exists
if docker-compose exec -T web python manage.py seed_users --help &>/dev/null; then
    read -p "Do you want to seed dev data? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Seeding development data..."
        docker-compose exec -T web python manage.py seed_users --count 10 || true
        echo -e "${GREEN}✓ Dev data seeded${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Seed command not found. Skipping.${NC}"
fi

# =============================================================================
# VERIFY CELERY WORKERS
# =============================================================================
echo -e "\n${YELLOW}Checking Celery workers...${NC}"

# Check if worker is running
WORKER_RUNNING=$(docker-compose ps worker | grep -c "Up" || echo "0")
if [ "$WORKER_RUNNING" -gt 0 ]; then
    echo -e "${GREEN}✓ Celery worker is running${NC}"
else
    echo -e "${YELLOWery worker may not}⚠ Cel be running. Check with: docker-compose ps${NC}"
fi

# Check if beat is running
BEAT_RUNNING=$(docker-compose ps beat | grep -c "Up" || echo "0")
if [ "$BEAT_RUNNING" -gt 0 ]; then
    echo -e "${GREEN}✓ Celery beat is running${NC}"
else
    echo -e "${YELLOW}⚠ Celery beat may not be running. Check with: docker-compose ps${NC}"
fi

# =============================================================================
# HEALTH CHECKS
# =============================================================================
echo -e "\n${YELLOW}Running health checks...${NC}"

echo ""
echo "=========================================="
echo "  HEALTH CHECK RESULTS"
echo "=========================================="
echo ""

# Basic Health
echo -n "Basic Health (/health/): "
HEALTH_RESPONSE=$(curl -sf http://localhost:8000/health/ 2>/dev/null)
if [ $? -eq 0 ]; then
    HEALTH_STATUS=$(echo "$HEALTH_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
    if [ "$HEALTH_STATUS" = "healthy" ]; then
        echo -e "${GREEN}✓ PASS${NC}"
    else
        echo -e "${YELLOW}⚠ DEGRADED${NC}"
    fi
else
    echo -e "${RED}✗ FAIL${NC}"
fi

# Database Health
echo -n "Database Health: "
DB_STATUS=$(echo "$HEALTH_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('services',{}).get('database',{}).get('status','unknown'))" 2>/dev/null || echo "unknown")
if [ "$DB_STATUS" = "healthy" ]; then
    echo -e "${GREEN}✓ PASS${NC}"
else
    echo -e "${RED}✗ FAIL${NC}"
fi

# Redis Health
echo -n "Redis Health: "
REDIS_STATUS=$(echo "$HEALTH_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('services',{}).get('redis',{}).get('status','unknown'))" 2>/dev/null || echo "unknown")
if [ "$REDIS_STATUS" = "healthy" ]; then
    echo -e "${GREEN}✓ PASS${NC}"
else
    echo -e "${RED}✗ FAIL${NC}"
fi

# Notifications Health
echo -n "Notifications Health (/health/notifications/): "
NOTIF_RESPONSE=$(curl -sf http://localhost:8000/health/notifications/ 2>/dev/null)
if [ $? -eq 0 ]; then
    NOTIF_STATUS=$(echo "$NOTIF_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
    if [ "$NOTIF_STATUS" = "healthy" ]; then
        echo -e "${GREEN}✓ PASS${NC}"
    else
        echo -e "${YELLOW}⚠ DEGRADED${NC}"
    fi
else
    echo -e "${RED}✗ FAIL${NC}"
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo "=========================================="
echo "  BOOTSTRAP COMPLETE"
echo "=========================================="
echo ""
echo "Services:"
echo "  - Web:        http://localhost:8000"
echo "  - API:        http://localhost:8000/api/v1"
echo "  - Admin:      http://localhost:8000/admin"
echo "  - Nginx:      http://localhost:8888"
echo "  - Flower:     http://localhost:5556"
echo ""
echo "Useful Commands:"
echo "  docker-compose logs -f          # View logs"
echo "  docker-compose logs -f web       # View web logs"
echo "  docker-compose logs -f worker   # View worker logs"
echo "  make test                        # Run tests"
echo ""
echo -e "${GREEN}Happy coding!${NC}"
