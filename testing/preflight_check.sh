#!/bin/bash
# =============================================================================
# Digital Chama - Pre-flight Checks
# =============================================================================
# Run this script before starting any services to verify the environment
# is properly configured.
#
# Usage: ./preflight_check.sh
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ERRORS=0
WARNINGS=0

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((ERRORS++))
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    ((WARNINGS++))
}

echo ""
echo "=========================================="
echo "Digital Chama - Pre-flight Checks"
echo "=========================================="
echo ""

# 1. Check Docker
log_info "Checking Docker..."
if command -v docker &> /dev/null; then
    log_success "Docker is installed: $(docker --version)"
else
    log_fail "Docker is not installed"
fi

if command -v docker-compose &> /dev/null || command -v docker &> /dev/null; then
    if docker ps &> /dev/null; then
        log_success "Docker daemon is running"
    else
        log_fail "Docker daemon is not running"
    fi
else
    log_fail "Docker Compose not available"
fi

echo ""

# 2. Check Docker Compose Services
log_info "Checking Docker Compose configuration..."
if [ -f "docker-compose.yml" ]; then
    log_success "docker-compose.yml found"
    
    # Check services are defined
    SERVICES=$(docker-compose config --services 2>/dev/null | wc -l)
    if [ "$SERVICES" -gt 0 ]; then
        log_success "$SERVICES services defined"
    else
        log_fail "No services defined in docker-compose.yml"
    fi
else
    log_fail "docker-compose.yml not found"
fi

echo ""

# 3. Check Environment File
log_info "Checking environment configuration..."
cd digital_chama_system

if [ -f ".env" ]; then
    log_success ".env file found"
    
    # Check key variables
    KEY_VARS=("DEBUG" "DATABASE_URL" "REDIS_URL")
    
    for var in "${KEY_VARS[@]}"; do
        if grep -q "^${var}=" .env; then
            VALUE=$(grep "^${var}=" .env | cut -d'=' -f2)
            if [ -n "$VALUE" ]; then
                log_success "$var is configured"
            else
                log_warn "$var is empty"
            fi
        else
            log_warn "$var is not defined in .env"
        fi
    done
else
    log_fail ".env file not found (copy .env.example to .env)"
fi

cd ..

echo ""

# 4. Check Python and Dependencies
log_info "Checking Python..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    log_success "Python installed: $PYTHON_VERSION"
else
    log_fail "Python 3 is not installed"
fi

echo ""

# 5. Check Required Ports
log_info "Checking ports..."
PORTS=(8000 5432 6379)
for port in "${PORTS[@]}"; do
    if lsof -i :$port &> /dev/null; then
        log_warn "Port $port is in use"
    else
        log_success "Port $port is available"
    fi
done

echo ""

# 6. Check Disk Space
log_info "Checking disk space..."
AVAILABLE=$(df -h . | tail -1 | awk '{print $4}')
log_success "Available disk space: $AVAILABLE"

echo ""

# 7. Check Memory
log_info "Checking memory..."
if command -v free &> /dev/null; then
    TOTAL_MEM=$(free -h | grep Mem | awk '{print $2}')
    AVAILABLE_MEM=$(free -h | grep Mem | awk '{print $7}')
    log_success "Memory: $AVAILABLE_MEM available / $TOTAL_MEM total"
else
    log_warn "Could not check memory"
fi

echo ""

# Summary
echo "=========================================="
echo "Summary"
echo "=========================================="
echo ""

if [ $ERRORS -gt 0 ]; then
    echo -e "${RED}Errors: $ERRORS${NC}"
fi

if [ $WARNINGS -gt 0 ]; then
    echo -e "${YELLOW}Warnings: $WARNINGS${NC}"
fi

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}All checks passed!${NC}"
    echo ""
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}Pre-flight checks complete with warnings${NC}"
    echo ""
    exit 0
else
    echo -e "${RED}Pre-flight checks failed!${NC}"
    echo ""
    exit 1
fi
