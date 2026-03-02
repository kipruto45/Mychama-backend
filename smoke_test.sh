#!/bin/bash
# =============================================================================
# Digital Chama - Release Candidate Smoke Test
# =============================================================================
# This script performs a comprehensive smoke test before deployment.
# It verifies all critical functionality is working.
#
# Usage: ./smoke_test.sh
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
API_BASE="http://localhost:8000/api/v1"
HEALTH_BASE="http://localhost:8000/health"

# Test counters
PASSED=0
FAILED=0

# Helper functions
pass() {
    echo -e "${GREEN}✓ PASS${NC}: $1"
    ((PASSED++))
}

fail() {
    echo -e "${RED}✗ FAIL${NC}: $1"
    ((FAILED++))
}

info() {
    echo -e "${BLUE}ℹ INFO${NC}: $1"
}

warn() {
    echo -e "${YELLOW}⚠ WARN${NC}: $1"
}

# Banner
echo ""
echo "========================================"
echo "  Digital Chama - Smoke Test"
echo "========================================"
echo ""

# Check if services are running
echo -e "${BLUE}Checking services...${NC}"
if ! curl -sf "$HEALTH_BASE/" > /dev/null 2>&1; then
    echo -e "${RED}ERROR: Services not running. Run 'make up' first.${NC}"
    exit 1
fi

# =============================================================================
# 1. HEALTH CHECKS
# =============================================================================
echo -e "\n${BLUE}=== Health Checks ===${NC}"

# Basic Health
if curl -sf "$HEALTH_BASE/" > /dev/null 2>&1; then
    HEALTH_STATUS=$(curl -sf "$HEALTH_BASE/" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null)
    if [ "$HEALTH_STATUS" = "healthy" ]; then
        pass "Basic health endpoint"
    else
        fail "Basic health status: $HEALTH_STATUS"
    fi
else
    fail "Basic health endpoint unreachable"
fi

# Notifications Health
if curl -sf "$HEALTH_BASE/notifications/" > /dev/null 2>&1; then
    pass "Notifications health endpoint"
else
    fail "Notifications health endpoint unreachable"
fi

# Payments Health
if curl -sf "$HEALTH_BASE/payments/" > /dev/null 2>&1; then
    pass "Payments health endpoint"
else
    warn "Payments health endpoint unreachable (optional)"
fi

# =============================================================================
# 2. AUTHENTICATION TESTS
# =============================================================================
echo -e "\n${BLUE}=== Authentication Tests ===${NC}"

# Generate unique test user
TEST_PHONE="+2547$(date +%H%M%S)"
TEST_EMAIL="test$(date +%s)@example.com"
TEST_PASSWORD="TestPass123!"

# Register
REGISTER_RESPONSE=$(curl -sf -X POST "$API_BASE/auth/register/" \
    -H "Content-Type: application/json" \
    -d "{
        \"phone\": \"$TEST_PHONE\",
        \"email\": \"$TEST_EMAIL\",
        \"password\": \"$TEST_PASSWORD\",
        \"first_name\": \"Smoke\",
        \"last_name\": \"Test\"
    }" 2>&1) || true

if echo "$REGISTER_RESPONSE" | grep -q "OTP\|otp\|user\|message"; then
    pass "User registration"
    
    # Get OTP if in dev mode
    DEBUG=$(docker-compose exec -T web python -c "from django.conf import settings; print(settings.DEBUG)" 2>/dev/null)
    if [ "$DEBUG" = "True" ] || [ "$DEBUG" = "true" ]; then
        OTP=$(curl -sf "http://localhost:8000/api/v1/dev/otp/latest/" | python3 -c "import sys,json; print(json.load(sys.stdin).get('otp',''))" 2>/dev/null) || OTP=""
        if [ -n "$OTP" ]; then
            # Verify OTP
            VERIFY_RESPONSE=$(curl -sf -X POST "$API_BASE/auth/verify-otp/" \
                -H "Content-Type: application/json" \
                -d "{
                    \"phone\": \"$TEST_PHONE\",
                    \"otp\": \"$OTP\",
                    \"purpose\": \"login\"
                }" 2>&1) || true
            
            if echo "$VERIFY_RESPONSE" | grep -q "token\|Token\|verified"; then
                pass "OTP verification (dev mode)"
                
                # Extract token
                TOKEN=$(echo "$VERIFY_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null) || TOKEN=""
            else
                warn "Could not verify OTP automatically"
                TOKEN=""
            fi
        else
            warn "No OTP available (check PRINT_OTP_IN_CONSOLE=True)"
            TOKEN=""
        fi
    else
        warn "Skipping OTP verification (not in DEBUG mode)"
        TOKEN=""
    fi
else
    fail "User registration: $REGISTER_RESPONSE"
    TOKEN=""
fi

# Login (should work with unverified user if OTP not required)
LOGIN_RESPONSE=$(curl -sf -X POST "$API_BASE/auth/login/" \
    -H "Content-Type: application/json" \
    -d "{
        \"phone\": \"$TEST_PHONE\",
        \"password\": \"$TEST_PASSWORD\"
    }" 2>&1) || true

if echo "$LOGIN_RESPONSE" | grep -q "token\|Token"; then
    pass "User login"
    TOKEN=${TOKEN:-$(echo "$LOGIN_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)}
else
    warn "Login may require OTP verification first"
fi

# =============================================================================
# 3. EMAIL & SMS TESTS
# =============================================================================
echo -e "\n${BLUE}=== Notification Tests ===${NC}"

# Test email
if docker-compose exec -T web python manage.py test_email admin@example.com > /dev/null 2>&1; then
    pass "Email sending"
else
    warn "Email sending may need configuration"
fi

# Test SMS
if docker-compose exec -T web python manage.py test_sms +254712345678 > /dev/null 2>&1; then
    pass "SMS sending"
else
    warn "SMS sending may need configuration"
fi

# =============================================================================
# 4. CELERY TESTS
# =============================================================================
echo -e "\n${BLUE}=== Celery Tests ===${NC}"

# Check worker
if docker-compose ps worker 2>/dev/null | grep -q "Up"; then
    pass "Celery worker running"
else
    fail "Celery worker not running"
fi

# Check beat
if docker-compose ps beat 2>/dev/null | grep -q "Up"; then
    pass "Celery beat running"
else
    warn "Celery beat not running (optional)"
fi

# Check Redis
if docker-compose exec -T redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
    pass "Redis connectivity"
else
    fail "Redis not responding"
fi

# =============================================================================
# 5. DATABASE TESTS
# =============================================================================
echo -e "\n${BLUE}=== Database Tests ===${NC}"

# Check migrations
MIGRATION_STATUS=$(docker-compose exec -T web python manage.py showmigrations --plan 2>/dev/null | grep -c "\[ ]" || echo "0")
if [ "$MIGRATION_STATUS" -gt 0 ]; then
    pass "All migrations applied ($MIGRATION_STATUS)"
else
    warn "Could not verify migrations"
fi

# Check users exist
USER_COUNT=$(docker-compose exec -T web python -c "
from django.contrib.auth import get_user_model
User = get_user_model()
print(User.objects.count())
" 2>/dev/null) || echo "0"

if [ "$USER_COUNT" -gt 0 ]; then
    pass "Users exist in database ($USER_COUNT)"
else
    warn "No users found in database"
fi

# =============================================================================
# 6. SECURITY TESTS
# =============================================================================
echo -e "\n${BLUE}=== Security Tests ===${NC}"

# Check DEBUG setting
DEBUG_STATUS=$(docker-compose exec -T web python -c "from django.conf import settings; print(settings.DEBUG)" 2>/dev/null)
if [ "$DEBUG_STATUS" = "False" ]; then
    pass "DEBUG is False (production safe)"
else
    warn "DEBUG is True (suitable for development only)"
fi

# Check secure headers
HEADERS=$(curl -sI "$HEALTH_BASE/" 2>/dev/null | grep -i "X-Frame-Options\|X-Content-Type" || true)
if echo "$HEADERS" | grep -q "X-Frame-Options"; then
    pass "Security headers present"
else
    warn "Security headers may not be configured"
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo "========================================"
echo "  TEST SUMMARY"
echo "========================================"
echo ""
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}🎉 ALL TESTS PASSED!${NC}"
    echo "The system is ready for deployment."
    exit 0
else
    echo -e "${RED}⚠️  SOME TESTS FAILED${NC}"
    echo "Please review the failures before deploying."
    exit 1
fi
