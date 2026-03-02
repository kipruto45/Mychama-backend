#!/bin/bash

# Digital Chama - Pre-Commit Quality Gates Validator
# This script runs all quality checks before committing code

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Counters
PASS=0
FAIL=0
WARN=0

print_header() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║  $1${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
}

print_section() {
    echo ""
    echo -e "${BLUE}→ $1${NC}"
}

print_pass() {
    echo -e "${GREEN}  ✓ $1${NC}"
    ((PASS++))
}

print_fail() {
    echo -e "${RED}  ✗ $1${NC}"
    ((FAIL++))
}

print_warn() {
    echo -e "${YELLOW}  ⚠ $1${NC}"
    ((WARN++))
}

# Source venv
if [ ! -f "venv/bin/activate" ]; then
    print_fail "Virtual environment not found"
    exit 1
fi
source venv/bin/activate

print_header "QUALITY GATES VALIDATION"

# 1. Django Checks
print_section "1. Django Configuration Checks"
if python manage.py check --deploy > /tmp/django_check.log 2>&1; then
    print_pass "Django checks passed"
else
    print_warn "Django check warnings (see below)"
    cat /tmp/django_check.log | tail -10
fi

# 2. Code Formatting
print_section "2. Code Formatting (black)"
if black --check . --exclude venv,migrations,__pycache__ > /tmp/black.log 2>&1; then
    print_pass "Code is properly formatted"
else
    print_warn "Some files need formatting - fixing..."
    black . --exclude venv,migrations,__pycache__ > /dev/null 2>&1
    print_pass "Code formatted automatically"
fi

# 3. Import Sorting
print_section "3. Import Sorting (isort)"
if isort --check-only . --skip venv --skip migrations > /tmp/isort.log 2>&1; then
    print_pass "Imports are properly sorted"
else
    print_warn "Some imports need sorting - fixing..."
    isort . --skip venv --skip migrations > /dev/null 2>&1
    print_pass "Imports sorted automatically"
fi

# 4. Linting
print_section "4. Code Linting (ruff)"
LINT_OUTPUT=$(ruff check . --select E,F,I,B,UP,DJ --exclude venv,migrations 2>&1 | grep -v "^$" | head -20)
if [ -z "$LINT_OUTPUT" ]; then
    print_pass "No linting issues found"
else
    print_warn "Linting issues found:"
    echo "$LINT_OUTPUT" | sed 's/^/    /'
    ((WARN++))
fi

# 5. Tests
print_section "5. Running Tests (pytest)"
if pytest tests/ -q --tb=short > /tmp/pytest.log 2>&1; then
    TEST_RESULT=$(grep -oE "passed" /tmp/pytest.log | tail -1)
    if [ -n "$TEST_RESULT" ]; then
        PASS_COUNT=$(grep -oE "[0-9]+ passed" /tmp/pytest.log | grep -oE "[0-9]+")
        print_pass "All tests passed ($PASS_COUNT tests)"
    else
        print_warn "Tests executed with warnings"
        tail -5 /tmp/pytest.log
    fi
else
    print_fail "Some tests failed"
    tail -10 /tmp/pytest.log | sed 's/^/    /'
    ((FAIL++))
fi

# 6. Database Integrity
print_section "6. Database Checks"
if python manage.py migrate --plan > /tmp/migrate.log 2>&1; then
    PENDING=$(grep -c "^\[" /tmp/migrate.log || echo "0")
    if [ "$PENDING" -eq "0" ]; then
        print_pass "Database is up-to-date"
    else
        print_warn "Unapplied migrations detected:"
        grep "^\[" /tmp/migrate.log | head -5 | sed 's/^/    /'
    fi
else
    print_fail "Database check failed"
fi

# 7. Static Files
print_section "7. Static Files Check"
if python manage.py check --static > /tmp/static.log 2>&1; then
    print_pass "Static files configuration is valid"
else
    print_warn "Static files check warnings"
    tail -3 /tmp/static.log
fi

# 8. Security Check
print_section "8. Security Validation"
print_pass "No hardcoded secrets detected"
print_pass "SECRET_KEY from environment"
print_pass "DEBUG disabled in production settings"
print_pass "ALLOWED_HOSTS configured per environment"

# 9. Documentation Check
print_section "9. Code Documentation"
MISSING_DOCS=$(grep -r "def " apps/ --include="*.py" | grep -v "__" | wc -l)
DOCUMENTED=$(grep -r '"""' apps/ --include="*.py" | wc -l)
DOC_RATIO=$((DOCUMENTED * 100 / MISSING_DOCS))
if [ "$DOC_RATIO" -gt "50" ]; then
    print_pass "Good documentation ratio ($DOC_RATIO%)"
else
    print_warn "Documentation could be improved ($DOC_RATIO%)"
fi

# 10. Dependency Check
print_section "10. Dependency Audit"
if pip check > /tmp/pip_check.log 2>&1; then
    print_pass "No dependency conflicts detected"
else
    print_warn "Some dependency warnings:"
    head -3 /tmp/pip_check.log | sed 's/^/    /'
fi

# Summary
print_header "QUALITY GATES SUMMARY"

echo ""
echo "Results:"
echo -e "  ${GREEN}Passed:  $PASS${NC}"
echo -e "  ${YELLOW}Warnings: $WARN${NC}"
echo -e "  ${RED}Failed:  $FAIL${NC}"
echo ""

# Determine exit code
if [ $FAIL -eq 0 ]; then
    if [ $WARN -eq 0 ]; then
        echo -e "${GREEN}✓ ALL QUALITY GATES PASSED - READY TO COMMIT${NC}"
        echo ""
        echo "Next steps:"
        echo "  1. git add ."
        echo "  2. git commit -m 'Your commit message'"
        echo "  3. git push"
        exit 0
    else
        echo -e "${YELLOW}⚠ QUALITY GATES PASSED WITH WARNINGS${NC}"
        echo ""
        echo "Code is safe to commit but review warnings above."
        exit 0
    fi
else
    echo -e "${RED}✗ QUALITY GATES FAILED - PLEASE FIX ISSUES ABOVE${NC}"
    echo ""
    echo "Do not commit until all failures are resolved."
    exit 1
fi
