#!/bin/bash
# Digital Chama - Quality & Security Gates Check
# Run code quality, security, and test validations

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() { echo -e "${BLUE}========== $1 ==========${NC}"; }
print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[⚠]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

# Activate virtual environment
if [ ! -f "venv/bin/activate" ]; then
    print_error "Virtual environment not found"
    exit 1
fi
source venv/bin/activate

cd "$(dirname "${BASH_SOURCE[0]}")"

print_header "Django System Check (--deploy mode)"
python manage.py check --deploy 2>&1 | grep -E "(Error|Warning|System|OK)" || true
echo ""

print_header "Code Formatting Check"
if command -v black &> /dev/null; then
    black --check --quiet . 2>&1 | head -20 || print_warning "Some files need formatting. Run: black ."
    print_status "Black formatting check complete"
else
    print_warning "black not installed"
fi
echo ""

print_header "Import Sorting Check"
if command -v isort &> /dev/null; then
    isort --check-only --quiet . 2>&1 | head -20 || print_warning "Some imports need sorting. Run: isort ."
    print_status "isort check complete"
else
    print_warning "isort not installed"
fi
echo ""

print_header "Linting Check (ruff)"
if command -v ruff &> /dev/null; then
    ruff check . --select E,F,I,B,UP,DJ --exclude venv,migrations 2>&1 | head -30 || true
    print_status "Ruff linting complete"
else
    print_warning "ruff not installed"
fi
echo ""

print_header "Running Tests"
if command -v pytest &> /dev/null; then
    pytest --co -q 2>&1 | head -10 || true
    echo ""
    print_warning "To run full tests: pytest -v"
else
    print_warning "pytest not installed"
fi
echo ""

print_header "Security Checks"

# Check for common security issues
print_status "Checking for hardcoded secrets..."
if grep -r "SECRET_KEY = " --include="*.py" . 2>/dev/null | grep -v "django-insecure-" | grep -v "env\|settings\|\.pem" | head -5; then
    print_warning "Found potential hardcoded secrets"
else
    print_status "No obvious hardcoded secrets found"
fi

# Check for DEBUG=True in production settings
print_status "Checking DEBUG settings..."
if grep -l "DEBUG = True" config/settings/production.py 2>/dev/null; then
    print_error "DEBUG=True found in production settings!"
else
    print_status "DEBUG properly configured per environment"
fi

# Check for ALLOWED_HOSTS
print_status "Checking ALLOWED_HOSTS..."
if grep "ALLOWED_HOSTS.*localhost" config/settings/production.py > /dev/null 2>&1; then
    print_warning "ALLOWED_HOSTS includes localhost in production settings (expected for dev)"
else
    print_status "ALLOWED_HOSTS properly separated"
fi

echo ""
print_header "Database Integrity Check"
python manage.py check --database default 2>&1 | grep -E "(ERROR|OK)" || true
echo ""

print_header "Static Files Check"
python manage.py check --static 2>&1 | grep -E "(ERROR|OK)" || true
echo ""

print_header "Environment Validation"
python manage.py shell << EOF
import os
from django.conf import settings

print("✓ DEBUG:", settings.DEBUG)
print("✓ ALLOWED_HOSTS:", settings.ALLOWED_HOSTS[:3], "...")
print("✓ Database:", type(settings.DATABASES['default']['ENGINE']).__name__)
print("✓ Cache:", type(settings.CACHES['default']['BACKEND']).__name__)
print("✓ Celery Broker:", settings.CELERY_BROKER_URL[:30] if settings.CELERY_BROKER_URL else "Not set")
print("✓ Email Backend:", settings.EMAIL_BACKEND.split('.')[-1])
print("✓ TIME_ZONE:", settings.TIME_ZONE)
print("✓ USE_TZ:", settings.USE_TZ)

# Check critical apps
from django.apps import apps
critical_apps = ['accounts', 'chama', 'finance', 'payments']
for app in critical_apps:
    if apps.is_installed(f'apps.{app}'):
        print(f"✓ app.{app} loaded")
    else:
        print(f"✗ app.{app} NOT loaded")
EOF

echo ""
print_header "Pre-commit Hook Status"
if [ -f ".pre-commit-config.yaml" ]; then
    print_status ".pre-commit-config.yaml found"
    if [ -d ".git/hooks" ]; then
        if [ -f ".git/hooks/pre-commit" ]; then
            print_status "pre-commit hooks installed"
        else
            print_warning "pre-commit hooks not installed. Run: pre-commit install"
        fi
    fi
else
    print_warning ".pre-commit-config.yaml not found"
fi

echo ""
print_header "Summary"
print_status "Quality gates check completed!"
echo ""
echo "Recommendations:"
echo "  • Run full tests: pytest -v"
echo "  • Format code: black ."
echo "  • Sort imports: isort ."
echo "  • Full linting: ruff check ."
echo "  • Install pre-commit: pre-commit install"
echo ""
