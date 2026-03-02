#!/bin/bash
# Digital Chama System - Local Development Setup Script
# This script sets up the local development environment

set -e

echo "🚀 Digital Chama System - Local Development Setup"
echo "=================================================="

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }
print_step() { echo -e "${BLUE}[→]${NC} $1"; }

# Check Python version
print_step "Checking Python version..."
python_version=$(python --version | awk '{print $2}')
if [[ "$python_version" < "3.11" ]]; then
    print_error "Python 3.11+ required (found $python_version)"
    exit 1
fi
print_status "Python $python_version found"

# Activate virtual environment
print_step "Activating virtual environment..."
if [ ! -d "venv" ]; then
    print_error "Virtual environment not found. Run: python -m venv venv"
    exit 1
fi
source venv/bin/activate
print_status "Virtual environment activated"

# Check Redis
print_step "Checking Redis service..."
if ! redis-cli ping > /dev/null 2>&1; then
    print_error "Redis is not running. Starting Redis..."
    sudo systemctl start redis-server
fi
print_status "Redis is running"

# Check PostgreSQL
print_step "Checking PostgreSQL..."
if command -v psql &> /dev/null; then
    if psql -U postgres -c "SELECT 1" > /dev/null 2>&1; then
        print_status "PostgreSQL is available"
    fi
fi

# Copy environment file if not exists
if [ ! -f ".env" ]; then
    print_step "Creating .env file from .env.example..."
    cp .env.example .env
    print_status ".env file created (edit with your settings)"
fi

# Install/update dependencies
print_step "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
print_status "Dependencies installed"

# Run migrations
print_step "Running database migrations..."
python manage.py migrate --noinput
print_status "Migrations completed"

# Create superuser if doesn't exist
print_step "Checking superuser..."
if ! python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); exit(0 if User.objects.filter(is_superuser=True).exists() else 1)" 2>/dev/null; then
    echo "No superuser found. Creating one..."
    python manage.py createsuperuser
else
    print_status "Superuser exists"
fi

# Load demo data
print_step "Loading demo data..."
if python manage.py shell < /dev/null 2>&1; then
    python scripts/seed_db.py 2>&1 | tail -1 || true
    print_status "Demo data loaded"
fi

# Run Django checks
print_step "Running Django checks..."
python manage.py check
print_status "All checks passed"

echo ""
echo -e "${GREEN}✓ Setup completed!${NC}"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your settings (especially SECRET_KEY)"
echo "2. In separate terminals, run:"
echo "   - python manage.py runserver"
echo "   - celery -A config worker --loglevel=info"
echo "   - celery -A config beat --loglevel=info"
echo "   - celery -A config flower (optional)"
echo ""
echo "3. Access the application:"
echo "   - API: http://localhost:8000/api/v1/"
echo "   - Docs: http://localhost:8000/api/docs/"
echo "   - Admin: http://localhost:8000/admin/"
echo "   - Flower: http://localhost:5555/"
