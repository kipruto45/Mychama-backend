# Digital Chama - Development Testing Runbook

A comprehensive testing and verification plan for the Digital Chama Django + DRF project before deploying to production (Render/Vercel).

## Table of Contents

1. [Pre-flight Checks](#1-pre-flight-checks)
2. [One-command Local Bootstrap](#2-one-command-local-bootstrap)
3. [Healthcheck Endpoints](#3-healthcheck-endpoints)
4. [Database & Migrations Testing](#4-database--migrations-testing)
5. [Authentication & Registration Testing](#5-authentication--registration-testing)
6. [OTP System Testing](#6-otp-system-testing)
7. [Notifications Testing](#7-notifications-testing)
8. [Celery & Redis Testing](#8-celery--redis-testing)
9. [Contact Us Form Testing](#9-contact-us-form-testing)
10. [M-Pesa Daraja Testing](#10-m-pesa-daraja-testing-optional)
11. [AI Features Testing](#11-ai-features-testing-optional)
12. [Security Tests](#12-security-tests)
13. [End-to-End QA Checklist](#13-end-to-end-qa-checklist)
14. [Release Candidate Smoke Test](#14-release-candidate-smoke-test)

---

## 1. Pre-flight Checks

Before running anything, verify your environment:

```bash
# Check Docker is installed
docker --version
docker-compose --version

# Check Docker daemon is running
docker ps

# Verify .env file exists and has required variables
cd digital_chama_system
cat .env | grep -E "^(DEBUG|DATABASE_URL|REDIS_URL|EMAIL_)" | head -20

# Verify containers can run
docker-compose config --services
```

### Key Environment Variables to Verify

| Variable | Required | Description |
|----------|----------|-------------|
| `DEBUG=True` | Yes (dev) | Django debug mode |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `EMAIL_HOST` | Yes | SMTP server for emails |
| `MAILGUN_API_KEY` | No* | For email delivery (*required for production) |
| `AFRICASTALKING_USERNAME` | No* | For SMS (*required for production) |
| `AFRICASTALKING_API_KEY` | No* | For SMS (*required for production) |

---

## 2. One-command Local Bootstrap

### 2.1 Bash Script: `dev_bootstrap.sh`

Run this script to bring up the entire development environment:

```bash
#!/bin/bash
set -e

echo "🚀 Starting Digital Chama Development Environment..."

# 1. Bring up Docker Compose
echo "📦 Starting containers..."
docker-compose up -d

# 2. Wait for services
echo "⏳ Waiting for services..."
sleep 10

# 3. Run migrations
echo "🗄️ Running migrations..."
docker-compose exec -T web python manage.py migrate --noinput

# 4. Collect static files
echo "📁 Collecting static files..."
docker-compose exec -T web python manage.py collectstatic --noinput

# 5. Create superuser (interactive)
echo "👤 Creating superuser..."
docker-compose exec -T web python manage.py createsuperuser || echo "Superuser creation skipped"

# 6. Seed data (if available)
echo "🌱 Seeding data..."
docker-compose exec -T web python manage.py seed_users --count=10 2>/dev/null || echo "Seeding skipped"

# 7. Check Celery worker
echo "🔧 Checking Celery worker..."
docker-compose ps celery_worker

# 8. Run health checks
echo "🏥 Running health checks..."
echo "   - Main health: $(curl -s http://localhost:8000/health/ | jq -r '.status' 2>/dev/null || echo 'N/A')"
echo "   - DB health: $(curl -s http://localhost:8000/health/db/ | jq -r '.status' 2>/dev/null || echo 'N/A')"
echo "   - Redis health: $(curl -s http://localhost:8000/health/redis/ | jq -r '.status' 2>/dev/null || echo 'N/A')"

echo "✅ Bootstrap complete!"
echo "🌐 Frontend: http://localhost:3000"
echo "🌐 Backend: http://localhost:8000"
echo "📚 Admin: http://localhost:8000/admin"
```

### 2.2 Makefile Alternative

```makefile
# Makefile for Digital Chama Development

.PHONY: up down logs migrate seed test clean restart

# Start all services
up:
	docker-compose up -d
	@echo "Waiting for services..." && sleep 5
	@echo "Run 'make migrate' next"

# Stop all services
down:
	docker-compose down

# View logs
logs:
	docker-compose logs -f

logs-web:
	docker-compose logs -f web

logs-celery:
	docker-compose logs -f celery_worker

# Run migrations
migrate:
	docker-compose exec -T web python manage.py migrate --noinput

# Create migrations
makemigrations:
	docker-compose exec -T web python manage.py makemigrations

# Seed data
seed:
	docker-compose exec -T web python manage.py seed_users --count=50

# Run tests
test:
	docker-compose exec -T web pytest

# Clean up
clean:
	docker-compose down -v
	docker system prune -f

# Restart services
restart:
	docker-compose restart

# Create superuser
superuser:
	docker-compose exec -T web python manage.py createsuperuser

# Collect static files
collectstatic:
	docker-compose exec -T web python manage.py collectstatic --noinput

# Check status
status:
	docker-compose ps
	@echo "Health checks:"
	@curl -s http://localhost:8000/health/ | jq '.'
```

---

## 3. Healthcheck Endpoints

### 3.1 Main Health Endpoint

```bash
# Basic health check
curl -s http://localhost:8000/health/

# Expected response:
{
  "status": "ok",
  "timestamp": "2024-01-15T10:30:00Z",
  "debug": true,
  "environment": "development"
}
```

### 3.2 Database Health

```bash
curl -s http://localhost:8000/health/db/

# Expected response:
{
  "status": "ok",
  "database": "postgresql",
  "tables_count": 45,
  "migrations_applied": true
}
```

### 3.3 Redis/Celery Health

```bash
curl -s http://localhost:8000/health/redis/

# Expected response:
{
  "status": "ok",
  "redis": "connected",
  "celery": "running",
  "worker_count": 1
}
```

### 3.4 Notifications Health

```bash
curl -s http://localhost:8000/health/notifications/

# Expected response:
{
  "status": "ok",
  "email": "configured",
  "sms": "configured",
  "redis": "connected"
}
```

---

## 4. Database & Migrations Testing

### 4.1 Show Migration Status

```bash
# Inside container
docker-compose exec web python manage.py showmigrations

# Or check specific app
docker-compose exec web python manage.py showmigrations accounts
```

### 4.2 Check Tables Exist

```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U chama -d digital_chama_dev

# List all tables
\dt

# Check specific tables
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;
```

### 4.3 Verify Schema for Key Models

```bash
# Check OTP tables
\d accounts_otpdevice
\d accounts_otp

# Check notification tables
\d notifications_notification
\d notifications_notificationlog
```

### 4.4 Check Admin Login

```bash
# Create superuser
docker-compose exec web python manage.py createsuperuser

# Login via admin panel
# Navigate to http://localhost:8000/admin
```

---

## 5. Authentication & Registration Testing

### 5.1 Register Endpoint

```bash
# Test registration
curl -X POST http://localhost:8000/api/accounts/register/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "SecurePass123!",
    "password_confirm": "SecurePass123!",
    "first_name": "Test",
    "last_name": "User",
    "phone_number": "+254712345678"
  }'

# Expected: 201 Created with user data
```

### 5.2 Login Endpoint

```bash
# Test successful login
curl -X POST http://localhost:8000/api/accounts/login/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "SecurePass123!"
  }'

# Expected: 200 OK with tokens
```

### 5.3 Password Hashing Verification

```bash
# Check password is hashed (via Django shell)
docker-compose exec web python manage.py shell

>>> from django.contrib.auth import get_user_model
>>> User = get_user_model()
>>> user = User.objects.get(email='test@example.com')
>>> user.check_password('SecurePass123!')
True
>>> user.password  # Should be hashed (starts with pbkdf2_ or bcrypt$)
```

### 5.4 Lockout Behavior

```bash
# Test failed login attempts (5 failed attempts should trigger lockout)
for i in {1..6}; do
  curl -X POST http://localhost:8000/api/accounts/login/ \
    -H "Content-Type: application/json" \
    -d '{"email": "test@example.com", "password": "WrongPass123!"}'
done
```

---

## 6. OTP System Testing

### 6.1 Dev-mode OTP (Recommended for Development)

Set in `.env`:
```bash
PRINT_OTP_IN_CONSOLE=True
ENABLE_DEV_OTP_ENDPOINT=True
```

OTP will be printed in Django console:
```
OTP for test@example.com: 123456
```

### 6.2 Dev OTP Endpoint

```bash
# Get latest OTP (only works with ENABLE_DEV_OTP_ENDPOINT=True)
curl -s http://localhost:8000/api/dev/otp/latest/?email=test@example.com

# Response:
{
  "otp": "123456",
  "expires_at": "2024-01-15T10:35:00Z"
}
```

### 6.3 Real OTP Delivery Testing

#### Test Email Sending

```bash
# Test email via management command
docker-compose exec web python manage.py test_email recipient@example.com

# Or use curl
curl -X POST http://localhost:8000/api/notifications/test/email/ \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "subject": "Test", "message": "Hello!"}'
```

#### Test SMS Sending

```bash
# Test SMS via management command
docker-compose exec web python manage.py test_sms +254712345678

# Or use curl
curl -X POST http://localhost:8000/api/notifications/test/sms/ \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+254712345678", "message": "Hello!"}'
```

#### Test OTP Flow

```bash
# Request OTP
curl -X POST http://localhost:8000/api/accounts/otp/request/ \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "channel": "email"}'

# Verify OTP (use code from console)
curl -X POST http://localhost:8000/api/accounts/otp/verify/ \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "otp": "123456"}'
```

---

## 7. Notifications Testing

### 7.1 In-app Notifications

```bash
# Create notification via API
curl -X POST http://localhost:8000/api/notifications/ \
  -H "Authorization: Token <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "user": 1,
    "title": "Test Notification",
    "message": "This is a test notification",
    "notification_type": "info"
  }'
```

### 7.2 Query Notification Logs

```bash
# Get last 20 notification logs
docker-compose exec web python manage.py shell

>>> from apps.notifications.models import NotificationLog
>>> logs = NotificationLog.objects.all().order_by('-created_at')[:20]
>>> for log in logs:
...     print(f"{log.status} | {log.channel} | {log.recipient}")
```

### 7.3 Check Notification Status Transitions

```bash
# PENDING -> SENT
# PENDING -> FAILED (with retry)

docker-compose exec web python manage.py shell

>>> from apps.notifications.models import NotificationLog
>>> log = NotificationLog.objects.first()
>>> log.status  # Should show current status
>>> log.attempts  # Number of retry attempts
```

---

## 8. Celery & Redis Testing

### 8.1 Check Worker Status

```bash
# Check if Celery worker is running
docker-compose ps celery_worker

# View worker logs
docker-compose logs celery_worker | tail -20

# Check active queues
docker-compose exec -T celery_worker celery -A digital_chama inspect active_queues
```

### 8.2 Test Task Execution

```bash
# Test a simple task
docker-compose exec web python manage.py shell

>>> from apps.accounts.tasks import send_welcome_email
>>> send_welcome_email.delay(user_id=1)
```

### 8.3 Flower Monitoring (Optional)

```bash
# Start Flower
docker-compose exec -T celery_worker celery -A digital_chama flower --port=5555

# Access at http://localhost:5555
```

---

## 9. Contact Us Form Testing

### 9.1 Test Contact Form Endpoint

```bash
# Submit contact form
curl -X POST http://localhost:8000/api/contact/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test User",
    "email": "test@example.com",
    "subject": "Test Subject",
    "message": "This is a test message",
    "phone": "+254712345678"
  }'

# Expected: 200 OK, email sent to admin
```

### 9.2 Verify Email Received

Check the console output or Mailgun logs for the sent email.

### 9.3 Verify Message Stored in DB

```bash
docker-compose exec web python manage.py shell

>>> from apps.contact.models import ContactMessage
>>> messages = ContactMessage.objects.all()
>>> messages.count()
```

---

## 10. M-Pesa Daraja Testing (Optional)

### 10.1 Sandbox Configuration

```bash
# In .env
MPESA_ENV=sandbox
MPESA_CONSUMER_KEY=your_sandbox_key
MPESA_CONSUMER_SECRET=your_sandbox_secret
MPESA_SHORTCODE=174379
MPESA_INITIATOR_NAME=testapi
MPESA_INITIATOR_PASSWORD=test_password
```

### 10.2 STK Push Test

```bash
# Test STK push
curl -X POST http://localhost:8000/api/payments/mpesa/stkpush/ \
  -H "Authorization: Token <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "254712345678",
    "amount": 10,
    "account_reference": "TEST123",
    "transaction_desc": "Test payment"
  }'
```

### 10.3 Callback Testing

Use ngrok to receive callbacks:
```bash
ngrok http 8000
# Configure M-Pesa callback URL to your ngrok URL
```

---

## 11. AI Features Testing (Optional)

### 11.1 Health Check

```bash
# Check OpenAI key is configured
curl -s http://localhost:8000/health/ai/

# Response:
{
  "status": "ok",
  "openai_configured": true,
  "model": "gpt-4"
}
```

### 11.2 Test Chat Endpoint

```bash
# Test AI chat (if enabled)
curl -X POST http://localhost:8000/api/ai/chat/ \
  -H "Authorization: Token <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, what can you do?"}'
```

---

## 12. Security Tests

### 12.1 CORS Validation

```bash
# Test CORS headers
curl -I -X OPTIONS http://localhost:8000/api/ \
  -H "Origin: http://localhost:3000"

# Should include: Access-Control-Allow-Origin
```

### 12.2 CSRF Check

```bash
# If using session auth, test CSRF protection
curl -X POST http://localhost:8000/api/accounts/login/ \
  -H "Content-Type: application/json" \
  -d '{}'

# Should return 403 if CSRF token missing
```

### 12.3 Permission Checks

```bash
# Test protected endpoint without auth
curl http://localhost:8000/api/accounts/profile/

# Should return 401/403
```

---

## 13. End-to-End QA Checklist

Before deploying, verify all items:

| # | Check | Status |
|---|-------|--------|
| 1 | ✅ Services up (docker-compose ps shows all running) | [ ] |
| 2 | ✅ DB migrated (no unapplied migrations) | [ ] |
| 3 | ✅ Admin login works | [ ] |
| 4 | ✅ Register/Login works | [ ] |
| 5 | ✅ OTP request/verify works | [ ] |
| 6 | ✅ Email sending works | [ ] |
| 7 | ✅ SMS sending works | [ ] |
| 8 | ✅ Celery tasks consumed | [ ] |
| 9 | ✅ Contact Us sends email | [ ] |
| 10 | ✅ Notifications logged | [ ] |
| 11 | ✅ Throttles/Lockout works | [ ] |
| 12 | ✅ (Optional) M-Pesa sandbox flow | [ ] |
| 13 | ✅ Health endpoints OK | [ ] |
| 14 | ✅ CORS configured | [ ] |
| 15 | ✅ DEBUG=False in production | [ ] |

---

## 14. Release Candidate Smoke Test

Use the provided `smoke_test.sh` script:

```bash
#!/bin/bash
# Digital Chama Smoke Test Script

set -e

BASE_URL="http://localhost:8000"
PASS=0
FAIL=0

pass() {
  echo "✅ PASS: $1"
  ((PASS++))
}

fail() {
  echo "❌ FAIL: $1"
  ((FAIL++))
}

echo "=========================================="
echo "Digital Chama Smoke Test"
echo "=========================================="

# 1. Health Check
echo "1. Testing health endpoint..."
RESPONSE=$(curl -s "$BASE_URL/health/")
if echo "$RESPONSE" | grep -q "ok"; then
  pass "Health endpoint"
else
  fail "Health endpoint"
fi

# 2. DB Health
echo "2. Testing database health..."
RESPONSE=$(curl -s "$BASE_URL/health/db/")
if echo "$RESPONSE" | grep -q "ok"; then
  pass "Database health"
else
  fail "Database health"
fi

# 3. Registration
echo "3. Testing registration..."
EMAIL="smoketest_$(date +%s)@example.com"
RESPONSE=$(curl -s -X POST "$BASE_URL/api/accounts/register/" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"password\": \"TestPass123!\", \"password_confirm\": \"TestPass123!\", \"first_name\": \"Smoke\", \"last_name\": \"Test\"}")
if echo "$RESPONSE" | grep -q "email"; then
  pass "Registration"
else
  fail "Registration"
fi

# 4. Login
echo "4. Testing login..."
RESPONSE=$(curl -s -X POST "$BASE_URL/api/accounts/login/" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"password\": \"TestPass123!\"}")
if echo "$RESPONSE" | grep -q "token"; then
  TOKEN=$(echo "$RESPONSE" | jq -r '.token')
  pass "Login"
else
  fail "Login"
  TOKEN=""
fi

if [ -n "$TOKEN" ]; then
  # 5. OTP Request
  echo "5. Testing OTP request..."
  RESPONSE=$(curl -s -X POST "$BASE_URL/api/accounts/otp/request/" \
    -H "Content-Type: application/json" \
    -H "Authorization: Token $TOKEN" \
    -d "{\"email\": \"$EMAIL\", \"channel\": \"email\"}")
  if echo "$RESPONSE" | grep -q "sent\|success"; then
    pass "OTP request"
  else
    fail "OTP request"
  fi
fi

echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="

if [ $FAIL -gt 0 ]; then
  exit 1
fi

exit 0
```

---

## Quick Commands Reference

```bash
# Start everything
make up && make migrate && make seed

# Run smoke test
./smoke_test.sh

# View all logs
make logs

# Stop everything
make down
```

---

For more details, see the main project documentation and individual component test guides.
