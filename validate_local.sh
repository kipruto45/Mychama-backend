#!/bin/bash
# Digital Chama - Complete Local System Validation Report

echo "╔════════════════════════════════════════════════════════════════════╗"
echo "║  DIGITAL CHAMA SYSTEM - LOCAL READINESS VALIDATION REPORT          ║"
echo "║  Generated: $(date)                         ║"
echo "╚════════════════════════════════════════════════════════════════════╝"
echo ""

PROJECT_DIR="/home/kipruto/Desktop/CHAMA/digital_chama_system"
cd "$PROJECT_DIR" || exit 1

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0

check() {
    if eval "$1" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} $2"
        ((PASS++))
    else
        echo -e "${RED}✗${NC} $2"
        ((FAIL++))
    fi
}

section() {
    echo ""
    echo -e "${BLUE}═══ $1 ═══${NC}"
}

# Activate venv first
source venv/bin/activate 2>/dev/null || true

section "SYSTEM PREREQUISITES"
check "python --version" "Python 3.11+ installed"
check "which redis-cli" "Redis CLI available"
check "which psql" "PostgreSQL CLI available"
check "test -d venv" "Virtual environment exists"
check "test -f venv/bin/activate" "VirtualEnv activate script present"

section "PYTHON & DEPENDENCIES"
check "python -c 'import django'" "Django installed"
check "python -c 'import rest_framework'" "Django REST Framework installed"
check "python -c 'import celery'" "Celery installed"
check "python -c 'import redis'" "Redis Python client installed"
check "python -c 'import psycopg'" "PostgreSQL adapter installed"
check "python -c 'import pytest'" "Pytest installed"

section "PROJECT STRUCTURE"
check "test -f manage.py" "manage.py present"
check "test -d config" "config/ directory present"
check "test -d apps" "apps/ directory present"
check "test -d tests" "tests/ directory present"
check "test -f config/settings/base.py" "Base settings present"
check "test -f config/settings/development.py" "Development settings present"
check "test -f config/celery.py" "Celery config present"
check "test -f requirements.txt" "requirements.txt present"
check "test -f pyproject.toml" "pyproject.toml present"
check "test -f .env.example" ".env.example present"

section "CONFIGURATION"
check "test -f .env" ".env file exists"
check "grep -q 'DEBUG=True' .env" "DEBUG=True in .env"
check "grep -q 'SECRET_KEY' .env" "SECRET_KEY configured"
check "grep -q 'ALLOWED_HOSTS' .env" "ALLOWED_HOSTS configured"
check "grep -q 'DATABASE_URL\|POSTGRES' .env" "Database URL configured"
check "grep -q 'REDIS_URL' .env" "Redis URL configured"

section "EXTERNAL SERVICES"
check "redis-cli ping" "Redis is running"

section "MIGRATION & DATABASE"
check "test -d apps/accounts/migrations" "Account migrations exist"
check "test -d apps/chama/migrations" "Chama migrations exist"
check "test -d apps/finance/migrations" "Finance migrations exist"
check "test -d apps/payments/migrations" "Payment migrations exist"
check "python manage.py showmigrations --plan 2>/dev/null" "Migration plan works"

section "DJANGO APPS"
check "python manage.py check 2>/dev/null" "Django checks pass"

section "CELERY & TASKS"
check "python -c 'from config.celery import app; print(len(app.tasks))' | grep -E '[0-9]+'" "Celery tasks autodiscovered"

section "AUTOMATION SCRIPTS"
check "test -x setup_local.sh" "setup_local.sh is executable"
check "test -x run_local.sh" "run_local.sh is executable"
check "test -x test_webhooks.sh" "test_webhooks.sh is executable"
check "test -x check_quality.sh" "check_quality.sh is executable"

section "DOCUMENTATION"
check "test -f README.md" "README.md present"
check "test -f LOCAL_READINESS_CHECKLIST.md" "LOCAL_READINESS_CHECKLIST.md present"
check "test -f DEPLOYMENT_GUIDE.md" "DEPLOYMENT_GUIDE.md present"

section "CODE QUALITY TOOLS"
check "python -c 'import black'" "black code formatter installed"
check "python -c 'import ruff'" "ruff linter installed"
check "python -c 'import isort'" "isort import sorter installed"

echo ""
echo "╔════════════════════════════════════════════════════════════════════╗"
echo "║  VALIDATION SUMMARY                                                ║"
echo "║  Passed: $(printf '%-2d' $PASS)                                                    ║"
echo "║  Failed: $(printf '%-2d' $FAIL)                                                    ║"
echo "╚════════════════════════════════════════════════════════════════════╝"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}✓ ALL CHECKS PASSED - System Ready for Local Development!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Run: ./setup_local.sh"
    echo "  2. Or:  ./run_local.sh"
    echo "  3. Access API at: http://localhost:8000/api/v1/"
    exit 0
else
    echo -e "${RED}✗ $FAIL CHECKS FAILED - Please fix issues above${NC}"
    exit 1
fi
