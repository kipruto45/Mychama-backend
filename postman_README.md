# Digital Chama API - Postman Collection

## Quick Start Guide

### Step 1: Import Files into Postman

1. Open Postman application
2. Click **Import** button (top-left)
3. Select **Upload Files**
4. Import both files:
   - `postman_collection.json` - API endpoints
   - `postman_environment.json` - Environment variables and test credentials

### Step 2: Configure Environment

1. In Postman, click the **Environments** dropdown (top-right)
2. Select **Digital Chama - Local Development**
3. The environment includes pre-configured test credentials

### Step 3: Authenticate

1. Select the **Authentication** folder in the collection
2. Click **Login** request
3. Click **Send** - This will:
   - Send login credentials
   - Store the `access_token` in environment variable
   - Store the `refresh_token` in environment variable

4. All subsequent requests will automatically use the Bearer token

## Test Credentials

| Role | Phone | Password |
|------|-------|----------|
| ADMIN | +254700000001 | superadmin123 |
| SECRETARY | +254700000002 | Secretary123! |
| TREASURER | +254700000003 | Treasurer123! |
| MEMBER | +254700000004 | Member123! |

## API Endpoints Overview

### Authentication
- `POST /api/v1/auth/login` - User login
- `POST /api/v1/auth/register` - User registration
- `POST /api/v1/auth/refresh` - Refresh access token
- `POST /api/v1/auth/logout` - Logout
- `GET /api/v1/auth/me` - Get current user info
- `POST /api/v1/auth/change-password` - Change password

### Chama (Groups)
- `GET /api/v1/chamas/` - List all chamas
- `POST /api/v1/chamas/` - Create new chama
- `GET /api/v1/chamas/{id}/` - Get chama details
- `POST /api/v1/chamas/{id}/request-join` - Request to join
- `GET /api/v1/chamas/{id}/members` - List members
- `GET /api/v1/chamas/{id}/membership-requests` - List join requests
- `POST /api/v1/chamas/{id}/membership-requests/{id}/approve` - Approve request

### Finance
- `GET /api/v1/finance/wallet` - Get wallet balance
- `GET /api/v1/finance/credit-score` - Get credit score
- `GET /api/v1/finance/loans/` - List loans
- `POST /api/v1/finance/loans/request` - Request loan
- `GET /api/v1/finance/loans/eligibility` - Check loan eligibility
- `POST /api/v1/finance/loans/{id}/repay` - Repay loan
- `GET /api/v1/finance/dashboard` - Get finance dashboard

### Payments
- `POST /api/v1/payments/deposit/stk/initiate` - Initiate STK push
- `GET /api/v1/payments/my/transactions` - List my transactions
- `POST /api/v1/payments/withdraw/request` - Request withdrawal
- `GET /api/v1/payments/admin/transactions` - Admin transactions

### Meetings
- `GET /api/v1/meetings/` - List meetings
- `POST /api/v1/meetings/` - Create meeting
- `POST /api/v1/meetings/{id}/attendance/mark` - Mark attendance
- `GET /api/v1/meetings/{id}/votes` - Get meeting votes
- `POST /api/v1/meetings/{id}/votes` - Submit vote

### Notifications
- `GET /api/v1/notifications/` - Get notifications
- `GET /api/v1/notifications/unread-count` - Get unread count
- `POST /api/v1/notifications/read-all` - Mark all as read
- `POST /api/v1/notifications/broadcast` - Send broadcast

### Reports
- `GET /api/v1/reports/member-statement` - Member statement
- `GET /api/v1/reports/loan-statement` - Loan statement
- `GET /api/v1/reports/chama-summary` - Chama summary
- `GET /api/v1/reports/chama-health` - Health score
- `GET /api/v1/reports/collection-forecast` - Collection forecast

### AI Insights
- `POST /api/v1/ai/chat` - AI chat
- `GET /api/v1/ai/status` - AI service status
- `GET /api/v1/ai/risk-profile/{id}/` - Risk profile
- `GET /api/v1/ai/insights/{id}/` - AI insights
- `GET /api/v1/ai/fraud-flags/{id}/` - Fraud flags

## Base URL Configuration

The collection uses `{{base_url}}` variable set to:
- **Local:** `http://localhost:8000/api/v1`

## Notes

- All protected endpoints require Bearer token authentication
- The collection automatically handles token refresh
- Some endpoints require specific roles (ADMIN, TREASURER, etc.)
- UUIDs are used for most resource identifiers

## Troubleshooting

### 401 Unauthorized
- Make sure you've run the Login request first
- Check that `access_token` is set in environment variables

### 404 Not Found
- Ensure Docker services are running
- Check the API is accessible at http://localhost:8000/api/v1/

### Token Expired
- Run the Login request again to get new tokens
- Or use Refresh Token endpoint
