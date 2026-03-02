#!/bin/bash
# =============================================================================
# Digital Chama Smoke Test Script
# =============================================================================
# This script performs a comprehensive smoke test of the Digital Chama
# application to verify all core functionality is working before deployment.
#
# Usage: ./smoke_test.sh [--base-url URL] [--skip-otp] [--verbose]
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
BASE_URL="${BASE_URL:-http://localhost:8000}"
SKIP_OTP="${SKIP_OTP:-false}"
VERBOSE="${VERBOSE:-false}"
PASS=0
FAIL=0

# Test results
declare -a PASSED_TESTS=()
declare -a FAILED_TESTS=()

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((PASS++))
    PASSED_TESTS+=("$1")
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((FAIL++))
    FAILED_TESTS+=("$1")
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_verbose() {
    if [ "$VERBOSE" = "true" ]; then
        echo -e "       $1"
    fi
}

print_header() {
    echo ""
    echo "========================================"
    echo "$1"
    echo "========================================"
}

print_results() {
    print_header "SMOKE TEST RESULTS"
    echo ""
    echo -e "Total: ${BLUE}$((PASS + FAIL))${NC} | ${GREEN}Passed: $PASS${NC} | ${RED}Failed: $FAIL${NC}"
    echo ""
    
    if [ $PASS -gt 0 ]; then
        echo -e "${GREEN}✅ Passed Tests:${NC}"
        for test in "${PASSED_TESTS[@]}"; do
            echo "  • $test"
        done
        echo ""
    fi
    
    if [ $FAIL -gt 0 ]; then
        echo -e "${RED}❌ Failed Tests:${NC}"
        for test in "${FAILED_TESTS[@]}"; do
            echo "  • $test"
        done
        echo ""
    fi
    
    echo "========================================"
}

# =============================================================================
# HTTP Helpers
# =============================================================================

http_get() {
    local url="$1"
    local headers="$2"
    curl -s -X GET "$url" $headers
}

http_post() {
    local url="$1"
    local data="$2"
    local headers="$3"
    curl -s -X POST "$url" -H "Content-Type: application/json" $headers -d "$data"
}

# =============================================================================
# Test Cases
# =============================================================================

test_health_endpoint() {
    print_header "1. Testing Health Endpoints"
    
    # Main health
    local response=$(http_get "$BASE_URL/health/")
    if echo "$response" | grep -q '"status".*"ok"'; then
        log_success "Health endpoint returns ok"
    else
        log_fail "Health endpoint did not return ok"
    fi
    
    # DB health
    response=$(http_get "$BASE_URL/health/db/")
    if echo "$response" | grep -q '"status".*"ok"'; then
        log_success "Database health check passes"
    else
        log_fail "Database health check failed"
    fi
    
    # Redis health
    response=$(http_get "$BASE_URL/health/redis/")
    if echo "$response" | grep -q '"status"'; then
        log_success "Redis health check responds"
    else
        log_fail "Redis health check failed"
    fi
}

test_registration() {
    print_header "2. Testing User Registration"
    
    # Generate unique email
    local email="smoketest_$(date +%s)@example.com"
    local payload="{\"email\": \"$email\", \"password\": \"TestPass123!\", \"password_confirm\": \"TestPass123!\", \"first_name\": \"Smoke\", \"last_name\": \"Test\"}"
    
    log_verbose "Registering user: $email"
    
    local response=$(http_post "$BASE_URL/api/accounts/register/" "$payload")
    
    if echo "$response" | grep -q '"email"'; then
        log_success "User registration successful"
        echo "$response" > /tmp/smoke_test_user.json
    else
        log_fail "User registration failed: $response"
    fi
}

test_login() {
    print_header "3. Testing User Login"
    
    # Use email from registration or fallback
    local email="smoketest_$(date +%s)@example.com"
    
    # Check if we have a registered user
    if [ -f /tmp/smoke_test_user.json ]; then
        email=$(cat /tmp/smoke_test_user.json | grep -o '"email":"[^"]*"' | head -1 | cut -d'"' -f4)
    fi
    
    # Try with a default test user if registration failed
    if [ -z "$email" ]; then
        email="admin@example.com"
    fi
    
    local payload="{\"email\": \"$email\", \"password\": \"TestPass123!\"}"
    
    log_verbose "Attempting login with: $email"
    
    local response=$(http_post "$BASE_URL/api/accounts/login/" "$payload")
    
    if echo "$response" | grep -q '"token"'; then
        local token=$(echo "$response" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
        echo "$token" > /tmp/smoke_test_token.txt
        log_success "User login successful"
    else
        log_fail "User login failed: $response"
    fi
}

test_otp_request() {
    if [ "$SKIP_OTP" = "true" ]; then
        log_warn "Skipping OTP tests (--skip-otp flag set)"
        return
    fi
    
    print_header "4. Testing OTP System"
    
    local token=""
    if [ -f /tmp/smoke_test_token.txt ]; then
        token=$(cat /tmp/smoke_test_token.txt)
    fi
    
    if [ -z "$token" ]; then
        log_warn "No auth token available, skipping OTP test"
        return
    fi
    
    local email="smoketest@example.com"
    if [ -f /tmp/smoke_test_user.json ]; then
        email=$(cat /tmp/smoke_test_user.json | grep -o '"email":"[^"]*"' | head -1 | cut -d'"' -f4)
    fi
    
    local payload="{\"email\": \"$email\", \"channel\": \"email\"}"
    
    log_verbose "Requesting OTP for: $email"
    
    local response=$(http_post "$BASE_URL/api/accounts/otp/request/" "$payload" "-H \"Authorization: Token $token\"")
    
    if echo "$response" | grep -q -E '"sent"|"success"|"OTP"'; then
        log_success "OTP request successful"
        
        # In dev mode, OTP is printed to console
        log_info "Check Django console for OTP code (dev mode)"
        
        # Try dev endpoint if available
        local dev_response=$(http_get "$BASE_URL/api/dev/otp/latest/?email=$email")
        if echo "$dev_response" | grep -q '"otp"'; then
            local otp=$(echo "$dev_response" | grep -o '"otp":"[^"]*"' | cut -d'"' -f4)
            log_success "Dev OTP endpoint returned: $otp"
            echo "$otp" > /tmp/smoke_test_otp.txt
        fi
    else
        log_fail "OTP request failed: $response"
    fi
}

test_notifications() {
    print_header "5. Testing Notifications"
    
    local token=""
    if [ -f /tmp/smoke_test_token.txt ]; then
        token=$(cat /tmp/smoke_test_token.txt)
    fi
    
    if [ -z "$token" ]; then
        log_warn "No auth token available, skipping notifications test"
        return
    fi
    
    # Test notifications endpoint
    local response=$(http_get "$BASE_URL/api/notifications/" "-H \"Authorization: Token $token\"")
    
    if echo "$response" | grep -q -E '\[|"results"'; then
        log_success "Notifications endpoint accessible"
    else
        log_fail "Notifications endpoint failed: $response"
    fi
}

test_contact_form() {
    print_header "6. Testing Contact Form"
    
    local payload='{"name": "Smoke Test", "email": "smoketest@example.com", "subject": "Test Subject", "message": "This is a smoke test message"}'
    
    local response=$(http_post "$BASE_URL/api/contact/" "$payload")
    
    if echo "$response" | grep -q -E '"success"|"message"'; then
        log_success "Contact form submission successful"
    else
        log_fail "Contact form submission failed: $response"
    fi
}

test_api_endpoints() {
    print_header "7. Testing API Endpoints"
    
    # Test various API endpoints
    local endpoints=(
        "$BASE_URL/api/"
        "$BASE_URL/api/accounts/"
        "$BASE_URL/api/chama/"
    )
    
    for endpoint in "${endpoints[@]}"; do
        local response=$(http_get "$endpoint")
        if echo "$response" | grep -q -E '{""|\[|\{"'; then
            log_success "Endpoint accessible: $(basename $endpoint)"
        else
            log_fail "Endpoint failed: $(basename $endpoint)"
        fi
    done
}

# =============================================================================
# Main Execution
# =============================================================================

main() {
    echo ""
    echo "🚀 Starting Digital Chama Smoke Test"
    echo "   Base URL: $BASE_URL"
    echo "   Skip OTP: $SKIP_OTP"
    echo ""
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --base-url)
                BASE_URL="$2"
                shift 2
                ;;
            --skip-otp)
                SKIP_OTP="true"
                shift
                ;;
            --verbose)
                VERBOSE="true"
                shift
                ;;
            *)
                echo "Unknown option: $1"
                exit 1
                ;;
        esac
    done
    
    # Run tests
    test_health_endpoint
    test_registration
    test_login
    test_otp_request
    test_notifications
    test_contact_form
    test_api_endpoints
    
    # Cleanup
    rm -f /tmp/smoke_test_user.json /tmp/smoke_test_token.txt /tmp/smoke_test_otp.txt 2>/dev/null || true
    
    # Print results
    print_results
    
    # Exit with appropriate code
    if [ $FAIL -gt 0 ]; then
        echo -e "${RED}❌ Smoke test FAILED${NC}"
        exit 1
    else
        echo -e "${GREEN}✅ All smoke tests PASSED${NC}"
        exit 0
    fi
}

# Run main
main "$@"
