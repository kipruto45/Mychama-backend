#!/bin/bash
# Digital Chama - Run Locally Comprehensive Script
# Start all services for local development

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }
print_step() { echo -e "${BLUE}[→]${NC} $1"; }
print_info() { echo -e "${YELLOW}[ℹ]${NC} $1"; }

# Activate virtual environment
if [ ! -d "venv" ]; then
    print_error "Virtual environment not found. Run: python -m venv venv"
    exit 1
fi
source venv/bin/activate

# Verify prerequisites
print_step "Verifying prerequisites..."

# Check Redis
if ! redis-cli ping > /dev/null 2>&1; then
    print_error "Redis is not running"
    exit 1
fi
print_status "Redis is running"

# Check Django
python manage.py check > /dev/null 2>&1
print_status "Django checks passed"

# Display startup menu
echo ""
echo -e "${BLUE}Digital Chama - Local Development${NC}"
echo "====================================="
echo ""
echo "Choose what to start:"
echo "  1) All services (web + worker + beat + flower)"
echo "  2) Web server only (Django)"
echo "  3) Worker only (Celery)"
echo "  4) Beat only (Celery Beat)"
echo "  5) Flower only (Celery Monitoring)"
echo "  6) Custom services"
echo ""

read -p "Enter your choice (1-6): " choice

case $choice in
    1)
        print_info "Starting all services in tmux..."
        
        if ! command -v tmux &> /dev/null; then
            print_error "tmux not found. Falling back to serial startup..."
            echo ""
            print_step "Starting Django web server..."
            python manage.py runserver 0.0.0.0:8000 &
            WEB_PID=$!
            
            sleep 2
            print_step "Starting Celery worker..."
            celery -A config worker --loglevel=info &
            WORKER_PID=$!
            
            print_step "Starting Celery beat..."
            celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule &
            BEAT_PID=$!
            
            print_info "Services running. Press Ctrl+C to stop..."
            wait
        else
            # Use tmux for better terminal management
            SESSION="chama"
            tmux new-session -d -s $SESSION -x 250 -y 50
            
            print_step "Starting Django web server..."
            tmux new-window -t $SESSION -n "web" "source venv/bin/activate && python manage.py runserver 0.0.0.0:8000"
            
            sleep 2
            print_step "Starting Celery worker..."
            tmux new-window -t $SESSION -n "worker" "source venv/bin/activate && celery -A config worker --loglevel=info"
            
            print_step "Starting Celery beat..."
            tmux new-window -t $SESSION -n "beat" "source venv/bin/activate && celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule"
            
            print_step "Starting Flower..."
            tmux new-window -t $SESSION -n "flower" "source venv/bin/activate && celery -A config flower"
            
            print_status "All services started in tmux session '$SESSION'"
            print_info "Attach to tmux: tmux attach-session -t $SESSION"
        fi
        ;;
    2)
        print_step "Starting Django web server on port 8000..."
        python manage.py runserver 0.0.0.0:8000
        ;;
    3)
        print_step "Starting Celery worker..."
        celery -A config worker --loglevel=info
        ;;
    4)
        print_step "Starting Celery beat scheduler..."
        celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule
        ;;
    5)
        print_step "Starting Flower on port 5555..."
        celery -A config flower --port=5555
        ;;
    6)
        print_info "Available services:"
        echo "  - Django: python manage.py runserver"
        echo "  - Worker: celery -A config worker --loglevel=info"
        echo "  - Beat: celery -A config beat --loglevel=info"
        echo "  - Flower: celery -A config flower"
        echo ""
        read -p "Enter command to run: " cmd
        eval "$cmd"
        ;;
    *)
        print_error "Invalid choice"
        exit 1
        ;;
esac
