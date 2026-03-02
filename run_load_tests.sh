#!/bin/bash
# Load Testing Runner for Digital Chama System
# This script runs comprehensive load tests using Locust

set -e

# Configuration
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS_DIR="$PROJECT_ROOT/logs"
REPORTS_DIR="$PROJECT_ROOT/reports"
LOCUST_CONF="$PROJECT_ROOT/locust.conf"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Create directories
mkdir -p "$LOGS_DIR"
mkdir -p "$REPORTS_DIR"

echo -e "${BLUE}🚀 Digital Chama Load Testing Suite${NC}"
echo "======================================"

# Function to run load test
run_load_test() {
    local test_name="$1"
    local users="$2"
    local spawn_rate="$3"
    local run_time="$4"
    local host="$5"

    echo -e "${YELLOW}Running $test_name...${NC}"
    echo "Users: $users, Spawn Rate: $spawn_rate, Duration: $run_time"

    # Create temporary config for this test
    cat > "$LOCUST_CONF.tmp" << EOF
locustfile = "tests/load_tests.py"
headless = true
users = $users
spawn-rate = $spawn_rate
run-time = $run_time
host = $host
csv = "$REPORTS_DIR/${test_name,,}_results"
loglevel = "INFO"
logfile = "$LOGS_DIR/${test_name,,}.log"
stop-timeout = 30
EOF

    # Run the test
    if locust -f tests/load_tests.py --config="$LOCUST_CONF.tmp" --html="$REPORTS_DIR/${test_name,,}_report.html"; then
        echo -e "${GREEN}✓ $test_name completed successfully${NC}"
    else
        echo -e "${RED}✗ $test_name failed${NC}"
        return 1
    fi

    # Clean up temp config
    rm -f "$LOCUST_CONF.tmp"
}

# Function to run health check test
run_health_test() {
    local host="$1"

    echo -e "${YELLOW}Running Health Check Test...${NC}"

    # Simple health check with curl
    if curl -f -s "$host/health/" > /dev/null; then
        echo -e "${GREEN}✓ Health check passed${NC}"
        return 0
    else
        echo -e "${RED}✗ Health check failed${NC}"
        return 1
    fi
}

# Function to generate summary report
generate_summary_report() {
    local host="$1"

    echo -e "${BLUE}Generating Summary Report...${NC}"

    cat > "$REPORTS_DIR/load_test_summary.md" << EOF
# Digital Chama Load Testing Summary Report

Generated on: $(date)
Test Environment: $host

## Test Scenarios Executed

### 1. Health Check Test
- **Purpose**: Verify basic service availability
- **Result**: $(run_health_test "$host" && echo "PASSED" || echo "FAILED")

### 2. Basic Load Test (50 users)
- **Users**: 50
- **Spawn Rate**: 5 users/second
- **Duration**: 2 minutes
- **Focus**: Authentication and basic operations

### 3. Medium Load Test (200 users)
- **Users**: 200
- **Spawn Rate**: 10 users/second
- **Duration**: 5 minutes
- **Focus**: Full user workflows including payments and loans

### 4. Stress Test (500 users)
- **Users**: 500
- **Spawn Rate**: 20 users/second
- **Duration**: 3 minutes
- **Focus**: System limits and failure points

### 5. Spike Test (1000 users)
- **Users**: 1000
- **Spawn Rate**: 50 users/second
- **Duration**: 1 minute
- **Focus**: Sudden traffic spikes

## Key Metrics to Review

1. **Response Times**: Check 95th percentile response times
2. **Error Rates**: Should be < 1% for normal operations
3. **Throughput**: Requests per second under load
4. **Resource Usage**: Monitor CPU, memory, and database connections

## Recommendations

- Review detailed HTML reports for each test scenario
- Monitor application logs during testing
- Check database performance metrics
- Validate auto-scaling configurations
- Review rate limiting and circuit breaker settings

## Files Generated

- \`reports/basic_load_report.html\` - Basic load test results
- \`reports/medium_load_report.html\` - Medium load test results
- \`reports/stress_test_report.html\` - Stress test results
- \`reports/spike_test_report.html\` - Spike test results
- \`logs/*.log\` - Detailed execution logs
- \`reports/*_results.csv\` - Raw performance data

EOF

    echo -e "${GREEN}✓ Summary report generated: $REPORTS_DIR/load_test_summary.md${NC}"
}

# Main execution
main() {
    local host="${1:-https://your-domain.com}"

    echo "Target Host: $host"
    echo ""

    # Run health check first
    if ! run_health_test "$host"; then
        echo -e "${RED}Health check failed. Aborting load tests.${NC}"
        exit 1
    fi

    # Run load tests
    run_load_test "Basic_Load_Test" 50 5 "2m" "$host"
    run_load_test "Medium_Load_Test" 200 10 "5m" "$host"
    run_load_test "Stress_Test" 500 20 "3m" "$host"
    run_load_test "Spike_Test" 1000 50 "1m" "$host"

    # Generate summary
    generate_summary_report "$host"

    echo ""
    echo -e "${GREEN}🎉 Load testing completed!${NC}"
    echo "Check the reports directory for detailed results."
    echo "Run 'locust -f tests/load_tests.py --config=locust.conf' for interactive testing."
}

# Check if locust is installed
if ! command -v locust &> /dev/null; then
    echo -e "${RED}Error: Locust is not installed.${NC}"
    echo "Install with: pip install locust"
    exit 1
fi

# Run main function with provided host or default
main "$@"