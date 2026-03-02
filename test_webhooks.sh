#!/bin/bash
# Digital Chama - Webhook Testing & M-Pesa Simulation
# Provides curl commands for testing M-Pesa callbacks locally

set -e

# Configuration
BASE_URL="${1:-http://localhost:8000}"
PHONE_NUMBER="${2:-254712345678}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() { echo -e "${BLUE}========== $1 ==========${NC}"; }
print_cmd() { echo -e "${YELLOW}\$ $1${NC}"; }
print_result() { echo -e "${GREEN}Result:${NC}"; }

# Test 1: Health Check
print_header "1. Health Check"
print_cmd "curl $BASE_URL/api/v1/health/"
curl -s "$BASE_URL/api/v1/health/" | jq . || true
echo ""

# Test 2: User Registration
print_header "2. User Registration"
print_cmd "Register test user"
REGISTER_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/auth/register/" \
  -H "Content-Type: application/json" \
  -d "{
    \"phone_number\": \"+$PHONE_NUMBER\",
    \"password\": \"TestPassword123!\",
    \"first_name\": \"Test\",
    \"last_name\": \"User\"
  }")
print_result
echo "$REGISTER_RESPONSE" | jq . || echo "$REGISTER_RESPONSE"
echo ""

# Test 3: Test C2B Callback (Pay Bill)
print_header "3. C2B Callback (Pay Bill) - Stub Mode"
print_cmd "Simulate M-Pesa C2B payment"
curl -s -X POST "$BASE_URL/api/v1/payments/callback/c2b/" \
  -H "Content-Type: application/json" \
  -d '{
    "TransactionType": "Pay Bill",
    "TransID": "LIB221119A0000001",
    "TransTime": "20240222120000",
    "TransAmount": "100.00",
    "BusinessShortCode": "175903",
    "BillRefNumber": "TEST-REF-001",
    "InvoiceNumber": "",
    "OrgAccountBalance": "10000.00",
    "ThirdPartyTransID": "",
    "MSISDN": "'$PHONE_NUMBER'",
    "FirstName": "Test",
    "MiddleName": "User",
    "LastName": "Demo"
  }' | jq . || true
echo ""

# Test 4: STK Push Callback
print_header "4. STK Push Callback - Stub Mode"
print_cmd "Simulate M-Pesa STK callback"
curl -s -X POST "$BASE_URL/api/v1/payments/callback/stk/" \
  -H "Content-Type: application/json" \
  -d '{
    "Body": {
      "stkCallback": {
        "MerchantRequestID": "16813-1590837-1",
        "CheckoutRequestID": "ws_CO_210322120627b9bf7d3ceb7e8a8dd061",
        "ResultCode": 0,
        "ResultDesc": "The service request is processed successfully.",
        "CallbackMetadata": {
          "Item": [
            {"Name": "Amount", "Value": 100.0},
            {"Name": "MpesaReceiptNumber", "Value": "LIB221119A0000001"},
            {"Name": "Timestamp", "Value": 20240222120000},
            {"Name": "PhoneNumber", "Value": '$(echo $PHONE_NUMBER | sed "s/^254/254/")'}
          ]
        }
      }
    }
  }' | jq . || true
echo ""

# Test 5: STK Push Failure Response
print_header "5. STK Push - Failed Transaction"
print_cmd "Simulate failed STK push (user cancelled)"
curl -s -X POST "$BASE_URL/api/v1/payments/callback/stk/" \
  -H "Content-Type: application/json" \
  -d '{
    "Body": {
      "stkCallback": {
        "MerchantRequestID": "16813-1590837-2",
        "CheckoutRequestID": "ws_CO_210322120627b9bf7d3ceb7e8a8dd062",
        "ResultCode": 1032,
        "ResultDesc": "Request cancelled by user",
        "CallbackMetadata": {
          "Item": []
        }
      }
    }
  }' | jq . || true
echo ""

# Test 6: B2C Payout Callback
print_header "6. B2C Payout Callback"
print_cmd "Simulate B2C payout response"
curl -s -X POST "$BASE_URL/api/v1/payments/callback/b2c/" \
  -H "Content-Type: application/json" \
  -d '{
    "Result": {
      "ResultType": 0,
      "ResultCode": 0,
      "ResultDesc": "The service request has been accepted successfully.",
      "OriginatorConversationID": "16813-34254-1",
      "ConversationID": "AG_20170615_00002fb37cc0e0e6e9e0",
      "TransactionID": "LIB221119A0000002",
      "ReferenceData": {
        "ReferenceItem": {
          "Key": "QueueTimeoutURL",
          "Value": "https://ip_address:port/b2c/queue/timeout/"
        }
      }
    }
  }' | jq . || true
echo ""

# Test 7: Webhook Testing Information
print_header "7. Local Webhook Testing Setup"
echo ""
echo "For testing webhooks from external services (e.g., actual M-Pesa):"
echo ""
echo "Option A: Using ngrok (recommended)"
echo "  1. Install ngrok: https://ngrok.com/download"
echo "  2. Start ngrok: ngrok http 8000"
echo "  3. Update M-Pesa callback URL to: https://YOUR_NGROK_URL/api/v1/payments/callback/"
echo ""
echo "Option B: Using cloudflared"
echo "  1. Install: sudo apt install cloudflared"
echo "  2. Start: cloudflared tunnel --url http://localhost:8000"
echo "  3. Update M-Pesa callback URL with the provided URL"
echo ""
echo "Option C: Using SSH tunneling"
echo "  1. On server: ssh -R 80:localhost:8000 example.com"
echo ""
echo "Then register C2B URLs:"
echo "  python manage.py register_c2b_urls"
echo ""

# Test 8: Idempotency Testing
print_header "8. Idempotency Testing"
echo ""
echo "Testing duplicate callback handling (should be idempotent):"
CALLBACK_DATA='{
  "TransactionType": "Pay Bill",
  "TransID": "IDEMPOTENT-TEST-001",
  "TransTime": "20240222120000",
  "TransAmount": "50.00",
  "BusinessShortCode": "175903",
  "BillRefNumber": "IDEMPOTENT-001",
  "InvoiceNumber": "",
  "OrgAccountBalance": "10000.00",
  "ThirdPartyTransID": "",
  "MSISDN": "'$PHONE_NUMBER'",
  "FirstName": "Test",
  "MiddleName": "Idempotent",
  "LastName": "Test"
}'

echo "First callback (should create payment):"
curl -s -X POST "$BASE_URL/api/v1/payments/callback/c2b/" \
  -H "Content-Type: application/json" \
  -d "$CALLBACK_DATA" | jq .result_code || true

echo ""
echo "Duplicate callback (should return same result without duplicate):"
curl -s -X POST "$BASE_URL/api/v1/payments/callback/c2b/" \
  -H "Content-Type: application/json" \
  -d "$CALLBACK_DATA" | jq .result_code || true
echo ""

echo -e "${GREEN}✓ Webhook testing guide complete${NC}"
echo ""
echo "For actual M-Pesa integration testing with Daraja credentials:"
echo "  1. Set MPESA_USE_STUB=False in .env"
echo "  2. Configure DARAJA_CONSUMER_KEY, DARAJA_CONSUMER_SECRET, etc."
echo "  3. Test STK push: POST /api/v1/payments/stk-push/"
echo "  4. Test C2B callback after registering URLs"
