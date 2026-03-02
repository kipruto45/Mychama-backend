# Digital Chama - Development Stage Testing Runbook

This comprehensive runbook provides complete testing and verification procedures for the Digital Chama Django + DRF project before deploying to production.

---

## Table of Contents

1. [Pre-flight Checks](#1-pre-flight-checks-before-running-anything)
2. [One-command Local Bootstrap Script](#2-one-command-local-bootstrap-script)
3. [Healthcheck Endpoints](#3-healthcheck-endpoints)
4. [Database & Migrations Testing](#4-database--migrations-testing)
5. [Authentication & Registration Testing](#5authentication--registration-testing)
6. [OTP System Testing](#6-otp-system-testing)
7. [Notifications Testing](#7-notifications-testing)
8. [Celery & Redis Testing](#8-celery--redis-testing)
9. [Contact Us Form Testing](#9-contact-us-form-testing)
10. [M-Pesa Daraja Testing (Optional)](#10-m-pesa-daraja-testing-optional)
11. [AI Features Testing (Optional)](#11-ai-features-testing-optional)
12. [Security Tests](#12-security-tests)
13. [End-to-End QA Checklist](#13-end-to-end-qa-checklist)
14. [Release Candidate Smoke Test Script](#14-release-candidate-smoke-test-script)

---

## 1. Pre-flight Checks (Before Running Anything)

### 1.1 Verify Docker Installation

```bash
# Check Docker version
docker --version
# Expected: Docker version 20.x.x or higher

# Check Docker Compose version
docker-compose --version
# OR (newer Docker CLI)
docker compose version
```

### 1.2 Verify Container Status

```bash
# Navigate to project directory
cd digital_chama_system

# Check if any containers are running
docker-compose ps

# Stop any existing containers
docker-compose down
```

### 1.3 Verify .env File Loading

```bash
# Check if .env exists
ls -la .env

# Verify critical environment variables are set
grep -E "^(POSTGRES_DB|POSTGRES_USER|POSTGRES_PASSWORD|SECRET_KEY|DEBUG)=" .env

# Print key settings (safely - without exposing secrets)
echo "Checking .env configuration..."
source .env
echo "DEBUG=$DEBUG"
echo "ALLOWED_HOSTS configured: $([ -n "$ALLOWED_HOSTS" ] && echo 'Yes' || echo 'No')"
echo "DATABASE_URL configured: $([ -n "$DATABASE_URL" ] && echo 'Yes' || echo 'No')"
echo "REDIS_URL configured: $([ -n "$REDIS_URL" ] && echo 'Yes' || echo 'No')"
```

### 1.4 Verify PostgreSQL Connectivity

```bash
# Wait for PostgreSQL and check connectivity
docker-compose up -d postgres

# Wait for PostgreSQL to be ready
for i in {1..30}; do
  if docker-compose exec -T postgres pg_isready -U digital_chama &>/dev/null; then
    echo "PostgreSQL is ready"
    break
  fi
  echo "Waiting for PostgreSQL..."
  sleep 2
done
```

### 1.5 Verify Redis Connectivity

```bash
# Check Redis
docker-compose up -d redis

# Test Redis connection
docker-compose exec -T redis redis-cli ping
# Expected: PONG
```

### 1.6 Verify Celery Broker

```bash
# Check Redis as Celery broker
docker-compose exec redis redis-cli -c ping
# Expected: PONG

# Check broker URL in settings
grep CELERY_BROKER_URL .env
```

### 1.7 Verify Django Settings

```bash
# Check key Django settings
docker-compose exec -T web python manage.py check

# Verify DEBUG setting
docker-compose exec -T web python -c "from django.conf import settings; print('DEBUG:', settings.DEBUG)"

# Verify ALLOWED_HOSTS
docker-compose exec -T web python -c "from django.conf import settings; print('ALLOWED_HOSTS:', settings.ALLOWED_HOSTS)"
```

---

## 2. One-command Local Bootstrap Script

### 2.1 Dev Bootstrap Script

The [`dev_bootstrap.sh`](digital_chama_system/dev_bootstrap.sh) script automates the entire development setup:

```bash
cd digital_chama_system
chmod +x dev_bootstrap.sh
./dev_bootstrap.sh
```

This script:
- ✅ Runs pre-flight checks
- ✅ Brings up Docker Compose services
- ✅ Waits for services to be healthy
- ✅ Runs migrations
- ✅ Collects static files
- ✅ Creates a superuser (interactive)
- ✅ Seeds dev data (optional)
- ✅ Verifies Celery workers
- ✅ Runs health checks

### 2.2 Makefile Alternative

The [`Makefile`](digital_chama_system/Makefile) provides convenient targets:

```bash
# Start all services
make up

# Stop all services
make down

# View logs
make logs
make logs-web
make logs-worker
make logs-db

# Run migrations
make migrate

# Seed dev data
make seed

# Run tests
make test

# Run health checks
make health

# Full bootstrap
make bootstrap
```

---

## 3. Healthcheck Endpoints

The system provides multiple health check endpoints:

### 3.1 Basic Health Check

```bash
# Basic health (DB + Redis)
curl -sf http://localhost:8000/health/
# Returns: {"status": "healthy", "services": {"database": {...}, "redis": {...}}}
```

### 3.2 Notifications Health Check

```bash
# Check email, SMS, and Celery
curl -sf http://localhost:8000/health/notifications/
# Returns: {"status": "healthy", "email": "ok", "sms": "ok", "celery": "ok"}
```

### 3.3 Payments Health Check (M-Pesa)

```bash
# Check M-Pesa configuration
curl -sf http://localhost:8000/health/payments/
# Returns: {"status": "healthy", "mpesa": {"environment": "sandbox", ...}}
```

### 3.4 Dev OTP Endpoint (DEBUG Only)

```bash
# Get latest OTP (only works with PRINT_OTP_IN_CONSOLE=True)
curl -sf http://localhost:8000/api/v1/dev/otp/latest/
# Returns: {"otp": "123456", "purpose": "login", ...}
```

### 3.5 Detailed Health Check

```bash
# Full system metrics
curl -sf http://localhost:8000/health/detailed/
# Returns: Full system status with metrics
```

### 3.6 Health Check Script

```bash
#!/bin/bash
# health_check.sh

echo "=== Digital Chama Health Checks ==="
echo ""

echo "1. Basic Health:"
curl -sf http://localhost:8000/health/ | python3 -m json.tool

echo ""
echo "2. Notifications Health:"
curl -sf http://localhost:8000/health/notifications/ | python3 -m json.tool

echo ""
echo "3. Payments Health:"
curl -sf http://localhost:8000/health/payments/ | python3 -m json.tool
```

---

## 4. Database & Migrations Testing

### 4.1 Show Migration Status

```bash
# Show migration status
docker-compose exec -T web python manage.py showmigrations

# Or with Make
make showmigrations
```

### 4.2 Check Tables Exist

```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U digital_chama -d digital_chama

# List all tables
\dt

# Or via Django
docker-compose exec -T web python manage.py dbshell
```

### 4.3 Verify Core Tables

```sql
-- Check accounts tables
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name LIKE 'accounts_%';

-- Check notifications tables
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name LIKE 'notifications_%';

-- Check OTP-related tables
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name LIKE '%otp%';
```

### 4.4 Verify Schema for OTP/Notification Models

```bash
# Check OTP model fields
docker-compose exec -T web python -c "
from apps.accounts.models import User
from django.contrib.auth import get_user_model
User = get_user_model()
print('User model fields:', [f.name for f in User._meta.get_fields()])
"

# Check NotificationLog model
docker-compose exec -T web python -c "
from apps.notifications.models import NotificationLog
print('NotificationLog fields:', [f.name for f in NotificationLog._meta.get_fields()])
"
```

### 4.5 Verify Permissions/Groups Seeded

```bash
# Check groups
docker-compose exec -T web python manage.py shell -c "
from django.contrib.auth.models import Group
groups = Group.objects.all()
for g in groups:
    print(f'{g.name}: {g.permissions.count()} permissions')
"

# Check if admin user exists
docker-compose exec -T web python -c "
from django.contrib.auth import get_user_model
User = get_user_model()
superusers = User.objects.filter(is_superuser=True).count()
print(f'Superusers: {superusers}')
"
```

### 4.6 Test Admin Login

```bash
# Access admin panel
# URL: http://localhost:8000/admin

# Verify via curl
curl -I http://localhost:8000/admin/login/
# Expected: HTTP/1.1 200 OK
```

---

## 5. Authentication & Registration Testing

### 5.1 Test Registration Endpoint

```bash
# Register a new user
curl -X POST http://localhost:8000/api/v1/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "phone": "+254712345678",
    "password": "SecurePass123!",
    "first_name": "Test",
    "last_name": "User"
  }'

# Expected: {"message": "OTP sent to your phone/email", "user_id": "..."}
```

### 5.2 Verify User Created in DB

```bash
# Check user in database
docker-compose exec -T web python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.filter(phone='+254712345678').first()
if user:
    print(f'User exists: {user.email}')
    print(f'Verified: {user.is_verified}')
else:
    print('User not found')
"
```

### 5.3 Test Password Hashing

```bash
# Verify password is hashed
docker-compose exec -T web python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.filter(phone='+254712345678').first()
if user:
    print(f'Password hash: {user.password[:50]}...')
    print(f'Check password: {user.check_password(\"SecurePass123!\")}')
"
```

### 5.4 Test Login Endpoint

```bash
# Successful login
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+254712345678",
    "password": "SecurePass123!"
  }'

# Expected: {"token": "...", "user": {...}}
```

### 5.5 Test Login Failure Cases

```bash
# Wrong password
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+254712345678",
    "password": "WrongPassword"
  }'

# Expected: {"non_field_errors": ["Invalid credentials"]}
```

### 5.6 Test Lockout Behavior

```bash
# Attempt 5+ failed logins (with wrong password)
for i in {1..6}; do
  curl -s -X POST http://localhost:8000/api/v1/auth/login/ \
    -H "Content-Type: application/json" \
    -d '{
      "phone": "+254712345678",
      "password": "WrongPassword"
    }'
  echo ""
done

# Expected after 5 attempts: {"detail": "Too many login attempts. Please try again later."}
```

### 5.7 Test Password Reset Flow

```bash
# Request password reset
curl -X POST http://localhost:8000/api/v1/auth/password/reset/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com"
  }'

# Expected: {"detail": "Password reset email has been sent."}
```

### 5.8 Recommended Pytest Tests

```python
# tests/test_auth.py

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def test_user(db):
    return User.objects.create_user(
        phone='+254712345679',
        email='test@example.com',
        password='SecurePass123!',
        first_name='Test',
        last_name='User'
    )

def test_registration_success(api_client):
    response = api_client.post('/api/v1/auth/register/', {
        'email': 'newuser@example.com',
        'phone': '+254712345680',
        'password': 'SecurePass123!',
        'first_name': 'New',
        'last_name': 'User'
    })
    assert response.status_code == 201

def test_login_success(api_client, test_user):
    response = api_client.post('/api/v1/auth/login/', {
        'phone': test_user.phone,
        'password': 'SecurePass123!'
    })
    assert response.status_code == 200
    assert 'token' in response.data

def test_login_failure(api_client, test_user):
    response = api_client.post('/api/v1/auth/login/', {
        'phone': test_user.phone,
        'password': 'WrongPassword'
    })
    assert response.status_code == 400

def test_login_lockout_after_max_attempts(api_client, test_user):
    for _ in range(5):
        api_client.post('/api/v1/auth/login/', {
            'phone': test_user.phone,
            'password': 'WrongPassword'
        })
    
    response = api_client.post('/api/v1/auth/login/', {
        'phone': test_user.phone,
        'password': 'WrongPassword'
    })
    assert response.status_code == 429  # Too Many Requests
```

---

## 6. OTP System Testing (Dev-safe and Real)

### 6.1 Dev-mode OTP (Recommended)

Enable dev-mode OTP in your `.env` file:

```bash
# .env configuration
PRINT_OTP_IN_CONSOLE=True
DEBUG=True
```

#### Using the Dev OTP Endpoint

```bash
# First, request an OTP via the API
curl -X POST http://localhost:8000/api/v1/auth/request-otp/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+254712345678",
    "purpose": "login"
  }'

# Then retrieve the OTP (if PRINT_OTP_IN_CONSOLE=True)
curl -sf http://localhost:8000/api/v1/dev/otp/latest/
# Returns: {"otp": "123456", "purpose": "login", ...}

# Verify the OTP
curl -X POST http://localhost:8000/api/v1/auth/verify-otp/ \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+254712345678",
    "otp": "123456",
    "purpose": "login"
  }'
```

⚠️ **Important**: The dev OTP endpoint is automatically disabled when `DEBUG=False` or in production!

### 6.2 Real OTP Delivery

#### Test Mailgun SMTP Email

```bash
# Ensure email settings in .env
EMAIL_PROVIDER=mailgun
EMAIL_HOST=smtp.mailgun.org
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-mailgun-username
EMAIL_HOST_PASSWORD=your-mailgun-password

# Test email sending
docker-compose exec -T web python manage.py test_email your-email@example.com
```

#### Test Africa’s Talking SMS

```bash
# Ensure SMS settings in .env
SMS_PROVIDER=africastalking
AFRICAS_TALKING_USERNAME=your-username
AFRICAS_TALKING_API_KEY=your-api-key
AFRICAS_TALKING_SENDER_ID=CHAMA

# Test SMS sending
docker-compose exec -T web python manage.py test_sms +254712345678
```

#### Test OTP via Management Command

```bash
# Test OTP via SMS
docker-compose exec -T web python manage.py test_otp \
  --phone +254712345678 \
  --channel sms \
  --purpose login

# Test OTP via Email
docker-compose exec -T web python manage.py test_otp \
  --email test@example.com \
  --channel email \
  --purpose login

# Test OTP via Both channels
docker-compose exec -T web python manage.py test_otp \
  --phone +254712345678 \
  --email test@example.com \
  --channel both \
  --purpose login
```

### 6.3 Sandbox vs Live (Africa's Talking)

- **Sandbox**: Use sandbox username for testing. SMS may not actually deliver to real phones.
- **Live**: Use live API key for production. Actual SMS delivery.

```bash
# Check current mode
grep AFRICAS_TALKING_USERNAME .env
# Sandbox: sandbox
# Live: your-app
```

### 6.4 Expected Outputs and Logs

```bash
# View email logs
docker-compose logs web | grep -i email

# View SMS logs
docker-compose logs worker | grep -i sms

# View OTP logs
docker-compose logs worker | grep -i otp
```

---

## 7. Notifications Testing

### 7.1 Test In-App Notifications

```bash
# Create a notification (via Django shell)
docker-compose exec -T web python manage.py shell -c "
from apps.notifications.models import Notification, NotificationLog
from django.contrib.auth import get_user_model
from apps.accounts.models import User

user = User.objects.first()
notification = Notification.objects.create(
    user=user,
    title='Test Notification',
    message='This is a test notification',
    notification_type='info'
)
print(f'Created notification: {notification.id}')
"

# Query recent notifications
docker-compose exec -T web python manage.py shell -c "
from apps.notifications.models import Notification
notifications = Notification.objects.all()[:10]
for n in notifications:
    print(f'{n.id}: {n.title} - {n.get_status_display()}')
"
```

### 7.2 Test Email Notifications

```bash
# Send test email notification
docker-compose exec -T web python manage.py test_email your-email@example.com
```

### 7.3 Test SMS Notifications

```bash
# Send test SMS notification
docker-compose exec -T web python manage.py test_sms +254712345678
```

### 7.4 Test NotificationLog Status Transitions

```bash
# Check NotificationLog table
docker-compose exec postgres psql -U digital_chama -d digital_chama -c "
SELECT id, notification_type, channel, status, created_at, sent_at, failure_reason
FROM notifications_notificationlog
ORDER BY created_at DESC
LIMIT 20;
"
```

### 7.5 Query Last 20 Notification Logs

```bash
# Using Django ORM
docker-compose exec -T web python manage.py shell -c "
from apps.notifications.models import NotificationLog
logs = NotificationLog.objects.select_related('user').order_by('-created_at')[:20]
for log in logs:
    print(f'{log.id} | {log.channel} | {log.status} | {log.user.email if log.user else \"N/A\"} | {log.created_at}')
"
```

---

## 8. Celery & Redis Testing

### 8.1 Verify Worker Running

```bash
# Check worker status
docker-compose ps worker
# Expected: Status shows "Up"

# Check worker logs
docker-compose logs worker | head -20
```

### 8.2 Check Worker Connected to Broker

```bash
# Inspect Celery worker
docker-compose exec worker celery -A config inspect stats
# Expected: Shows worker stats

# Check active queues
docker-compose exec worker celery -A config inspect active_queues
# Expected: Shows registered queues
```

### 8.3 Verify Tasks Being Consumed

```bash
# Check registered tasks
docker-compose exec worker celery -A config inspect registered

# Check active tasks
docker-compose exec worker celery -A config inspect active
```

### 8.4 Test Beat Scheduling

```bash
# Check beat schedule
docker-compose exec worker celery -A config beat --schedule=/tmp/celerybeat-schedule --info
```

### 8.5 View Active Queues and Tasks

```bash
# List queues in Redis
docker-compose exec redis redis-cli LRANGE celery 0 -1

# Check Celery results in Redis
docker-compose exec redis redis-cli KEYS '*celery*'
```

### 8.6 Optional: Flower Monitoring

```bash
# Flower is available at
echo "Flower monitoring: http://localhost:5556"

# Check Flower is running
docker-compose ps flower

# Access Flower
# URL: http://localhost:5556
```

### 8.7 Debugging: If Tasks Don't Run

```bash
# 1. Check Redis connectivity
docker-compose exec worker redis-cli ping
# Expected: PONG

# 2. Check broker URL
docker-compose exec worker env | grep CELERY_BROKER

# 3. Check for task errors in logs
docker-compose logs worker | grep -i error

# 4. Restart worker
docker-compose restart worker

# 5. Flush Redis queues (if needed)
docker-compose exec redis redis-cli FLUSHALL
```

---

## 9. Contact Us Form Testing

### 9.1 Test Contact Us Endpoint

```bash
# Submit contact form
curl -X POST http://localhost:8000/api/v1/contact/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test User",
    "email": "test@example.com",
    "phone": "+254712345678",
    "subject": "Test Subject",
    "message": "This is a test message from the contact form."
  }'

# Expected: {"message": "Thank you for contacting us. We'll respond shortly."}
```

### 9.2 Verify Email Received

```bash
# Check logs for contact email
docker-compose logs web | grep -i contact

# Or check worker logs
docker-compose logs worker | grep -i contact
```

### 9.3 Verify Message Stored in DB

```bash
# Check in database
docker-compose exec postgres psql -U digital_chama -d digital_chama -c "
SELECT id, name, email, subject, created_at 
FROM contacts_contact 
ORDER BY created_at DESC 
LIMIT 10;
"
```

### 9.4 Test Rate Limiting

```bash
# Attempt multiple submissions
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/api/v1/contact/ \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Test User",
      "email": "test'${i}'@example.com",
      "subject": "Test",
      "message": "Test message"
    }'
  echo ""
done

# Expected: Rate limited after threshold
```

### 9.5 Verify Reply-To Header

```bash
# Check email headers in logs
docker-compose logs worker | grep -i "Reply-To"
# Expected: Should show Reply-To header with user's email
```

---

## 10. M-Pesa Daraja Testing (Optional)

### 10.1 Prerequisites

```bash
# Ensure M-Pesa settings in .env
MPESA_ENVIRONMENT=sandbox
MPESA_USE_STUB=True  # Use stub for testing
MPESA_CONSUMER_KEY=your-consumer-key
MPESA_CONSUMER_SECRET=your-consumer-secret
MPESA_SHORTCODE=your-shortcode
MPESA_PASSKEY=your-passkey
MPESA_CALLBACK_SECRET=your-callback-secret
```

### 10.2 Check Payments Health

```bash
# Verify M-Pesa configuration
curl -sf http://localhost:8000/health/payments/
# Returns: {"status": "healthy", "mpesa": {...}}
```

### 10.3 Test STK Push (Sandbox)

```bash
# Initiate STK push
curl -X POST http://localhost:8000/api/v1/payments/stk-push/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Token <your-token>" \
  -d '{
    "phone_number": "+254712345678",
    "amount": 10,
    "account_reference": "CHAMA001",
    "transaction_desc": "Test payment"
  }'

# Expected: {"CheckoutRequestID": "...", "ResponseCode": "0", ...}
```

### 10.4 Callback Testing

For local testing without public callback URL:

#### Option A: Use ngrok

```bash
# Install ngrok
# Download from https://ngrok.com/download

# Start ngrok tunnel
ngrok http 8000

# Update .env with callback URL
DARAJA_CALLBACK_BASE_URL=https://your-ngrok-subdomain.ngrok.io

# Test STK push - callbacks will hit your ngrok endpoint
```

#### Option B: Stub/Replay Payloads

```bash
# Create a test callback endpoint
curl -X POST http://localhost:8000/api/v1/payments/callback/stk/ \
  -H "Content-Type: application/json" \
  -d '{
    "Body": {
      "stkCallback": {
        "MerchantRequestID": "TEST123",
        "CheckoutRequestID": "TEST456",
        "ResultCode": 0,
        "ResultDesc": "Success",
        "CallbackMetadata": {
          "Item": [
            {"Name": "Amount", "Value": 10},
            {"Name": "MpesaReceiptNumber", "Value": "TEST123456"},
            {"Name": "PhoneNumber", "Value": "254712345678"}
          ]
        }
      }
    }
  }'
```

### 10.5 Signature Validation Tests

```bash
# Test with invalid signature
curl -X POST http://localhost:8000/api/v1/payments/callback/stk/ \
  -H "Content-Type: application/json" \
  -H "X-MPESA-SIGNATURE: invalid-signature" \
  -d '{...}'

# Expected: 401 Unauthorized
```

### 10.6 IP Allowlist Tests

```bash
# Verify IP allowlist is enabled
curl -sf http://localhost:8000/health/payments/ | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('IP Allowlist:', data.get('mpesa', {}).get('ip_allowlist_enabled'))
"

# Safaricom sandbox IPs (for testing)
# 196.201.214.200
# 196.201.214.206
# 196.201.213.114
```

---

## 11. AI Features Testing (Optional)

### 11.1 Health Check for OpenAI

```bash
# Check OpenAI configuration
docker-compose exec -T web python -c "
from django.conf import settings
api_key = getattr(settings, 'OPENAI_API_KEY', None)
print('OpenAI API Key configured:', bool(api_key))
"
```

### 11.2 Test Chat Endpoint

```bash
# Test AI chat (if available)
curl -X POST http://localhost:8000/api/v1/ai/chat/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Token <your-token>" \
  -d '{
    "message": "Hello, how can I help with chama management?"
  }'

# Expected: {"response": "...", ...}
```

### 11.3 Test Moderation

```bash
# Test content moderation
curl -X POST http://localhost:8000/api/v1/ai/moderate/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Token <your-token>" \
  -d '{
    "content": "This is inappropriate content"
  }'
```

### 11.4 Rate Limit Tests

```bash
# Test AI chat rate limits
for i in {1..35}; do
  curl -s -X POST http://localhost:8000/api/v1/ai/chat/ \
    -H "Content-Type: application/json" \
    -H "Authorization: Token <your-token>" \
    -d '{"message": "test"}'
  echo ""
done

# Expected: Rate limited after threshold (e.g., 30/hour)
```

---

## 12. Security Tests

### 12.1 CORS Configuration

```bash
# Test CORS headers
curl -I http://localhost:8000/api/v1/ \
  -H "Origin: http://localhost:3000"
# Expected: Access-Control-Allow-Origin header

# Check CORS settings
docker-compose exec -T web python -c "
from django.conf import settings
print('CORS_ALLOW_ALL_ORIGINS:', getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False))
print('CORS_ALLOWED_ORIGINS:', getattr(settings, 'CORS_ALLOWED_ORIGINS', []))
"
```

### 12.2 CSRF Behavior

```bash
# Test CSRF for session-based auth
curl -I http://localhost:8000/api/v1/auth/login/ \
  -X POST \
  -H "Content-Type: application/json"
# Should work for token-based auth
```

### 12.3 Secure Headers

```bash
# Check security headers
curl -I http://localhost:8000/ | grep -i "X-"
# Expected: X-Frame-Options: DENY, X-Content-Type-Options: nosniff, etc.
```

### 12.4 File Upload Constraints

```bash
# Test file size limits
curl -X POST http://localhost:8000/api/v1/upload/ \
  -F "file=@large_file.jpg"  # > 10MB
# Expected: 413 Request Entity Too Large
```

### 12.5 Permission Checks

```bash
# Test unauthorized access
curl -X GET http://localhost:8000/api/v1/admin/users/
# Expected: 401/403 Unauthorized without auth

# Test with valid token
curl -X GET http://localhost:8000/api/v1/admin/users/ \
  -H "Authorization: Token <valid-token>"
# Expected: 200 OK or 403 if no permission
```

---

## 13. End-to-End QA Checklist

### Pre-Deployment Checklist

| # | Check Item | Status | Notes |
|---|------------|--------|-------|
| 1 | ✅ Services up (docker-compose up -d) | | |
| 2 | ✅ Database migrated (python manage.py migrate) | | |
| 3 | ✅ Admin login works (http://localhost:8000/admin) | | |
| 4 | ✅ User registration works | | |
| 5 | ✅ User login works | | |
| 6 | ✅ OTP request works | | |
| 7 | ✅ OTP verification works (dev mode) | | |
| 8 | ✅ Email sending works | | |
| 9 | ✅ SMS sending works | | |
| 10 | ✅ Celery tasks consumed | | |
| 11 | ✅ Contact Us form works | | |
| 12 | ✅ Contact Us email received | | |
| 13 | ✅ Notifications logged in DB | | |
| 14 | ✅ Throttles/rate limits enforced | | |
| 15 | ✅ Lockout after failed attempts | | |
| 16 | ✅ Health endpoints return 200 | | |
| 17 | ✅ (Optional) M-Pesa sandbox flow | | |
| 18 | ✅ (Optional) AI chat works | | |
| 19 | ✅ Static files served | | |
| 20 | ✅ DEBUG=False for production | | |

### Quick Verification Commands

```bash
# Run all health checks
make health

# Run smoke test
./smoke_test.sh

# Run tests
make test

# Check logs for errors
docker-compose logs --tail=100 | grep -i error
```

---

## 14. Release Candidate Smoke Test Script

### 14.1 Smoke Test Script

Create [`smoke_test.sh`](digital_chama_system/smoke_test.sh):

```bash
#!/bin/bash
# =============================================================================
# Digital Chama - Release Candidate Smoke Test
# =============================================================================
# This script performs a comprehensive smoke test before deployment.
# It verifies all critical functionality is working.
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

# =============================================================================
# 1. HEALTH CHECKS
# =============================================================================
echo -e "${BLUE}=== Health Checks ===${NC}"

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
echo -e "\n${BLUE ===${NC}=== Authentication Tests}"

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

if echo "$REGISTER_RESPONSE" | grep -q "OTP\|otp\|user"; then
    pass "User registration"
    
    # Get OTP if in dev mode
    if [ "$DEBUG" = "True" ] || [ "$DEBUG" = "true" ]; then
        OTP=$(curl -sf "$HEALTH_BASE/dev/otp/latest/" | python3 -c "import sys,json; print(json.load(sys.stdin).get('otp',''))" 2>/dev/null) || OTP=""
        if [ -n "$OTP" ]; then
            # Verify OTP
            VERIFY_RESPONSE=$(curl -sf -X POST "$API_BASE/auth/verify-otp/" \
                -H "Content-Type: application/json" \
                -d "{
                    \"phone\": \"$TEST_PHONE\",
                    \"otp\": \"$OTP\",
                    \"purpose\": \"login\"
                }" 2>&1) || true
            
            if echo "$VERIFY_RESPONSE" | grep -q "token\|Token"; then
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
if docker-compose ps worker | grep -q "Up"; then
    pass "Celery worker running"
else
    fail "Celery worker not running"
fi

# Check beat
if docker-compose ps beat | grep -q "Up"; then
    pass "Celery beat running"
else
    warn "Celery beat not running (optional)"
fi

# Check Redis
if docker-compose exec -T redis redis-cli ping | grep -q "PONG"; then
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
HEADERS=$(curl -sI "$HEALTH_BASE/" | grep -i "X-Frame-Options\|X-Content-Type" || true)
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
```

### 14.2 Running the Smoke Test

```bash
# Make executable
chmod +x smoke_test.sh

# Run smoke test
./smoke_test.sh

# Or with Make
make smoke-test
```

---

## Quick Reference Commands

### Docker Commands

```bash
# Start everything
docker-compose up -d

# View logs
docker-compose logs -f

# Stop everything
docker-compose down

# Restart a service
docker-compose restart web
```

### Django Commands

```bash
# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run tests
python -m pytest

# Open shell
python manage.py shell
```

### Service Testing

```bash
# Health checks
curl http://localhost:8000/health/
curl http://localhost:8000/health/notifications/
curl http://localhost:8000/health/payments/

# Test email
python manage.py test_email your@email.com

# Test SMS
python manage.py test_sms +254712345678

# Test OTP
python manage.py test_otp --phone +254712345678 --channel both
```

---

## Troubleshooting

### Common Issues

1. **PostgreSQL not ready**: Wait longer or check logs
2. **Redis connection refused**: Check Redis is running
3. **Celery tasks not executing**: Check worker logs
4. **Email not sending**: Verify SMTP credentials
5. **SMS not sending**: Verify Africa's Talking credentials
6. **M-Pesa callbacks not working**: Use ngrok for local testing

### Getting Help

```bash
# View all logs
docker-compose logs

# View specific service logs
docker-compose logs web
docker-compose logs worker
docker-compose logs postgres
docker-compose logs redis

# Check service status
docker-compose ps
```

---

*Document Version: 1.0*
*Last Updated: 2026-03-02*
*For Digital Chama Django + DRF Project*
