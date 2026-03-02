# 🏦 Digital Chama Backend

A comprehensive **Django REST Framework** backend for **Digital Chama** - a modern savings group management platform. This system handles community finances, payments, investments, meetings, and automated workflows with AI-powered insights and OTP authentication.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Django 5.1](https://img.shields.io/badge/django-5.1-darkgreen.svg)](https://www.djangoproject.com/)
[![DRF 3.15](https://img.shields.io/badge/drf-3.15-red.svg)](https://www.django-rest-framework.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [✨ Features](#-features)
- [🏗️ Architecture](#️-architecture)
- [🛠️ Tech Stack](#️-tech-stack)
- [📦 Prerequisites](#-prerequisites)
- [🚀 Quick Start](#-quick-start)
- [🐳 Docker Setup](#-docker-setup)
- [⚙️ Configuration](#️-configuration)
- [📡 API Documentation](#-api-documentation)
- [🧠 AI Features](#-ai-features)
- [🔔 Notifications & OTP](#-notifications--otp)
- [🗄️ Project Structure](#️-project-structure)
- [📚 Core Modules](#-core-modules)
- [🚢 Deployment](#-deployment)
- [🧪 Testing](#-testing)
- [📝 Contributing](#-contributing)

---

## ✨ Features

### 👥 **Account Management**
- JWT token-based authentication
- OTP via SMS (Africa's Talking) & Email
- Role-based access control (RBAC)
- Member profiles & KYC verification

### 💰 **Financial Management**
- Multi-currency wallet system
- Real-time balance tracking
- Ledger & transaction history
- Expense & income categorization
- Automated reconciliation

### 💳 **Payment Processing**
- Multiple payment methods (M-Pesa, bank transfer, cash)
- Payment gateway integration
- Invoice generation (PDF)
- Payment reconciliation workflows

### 📊 **Analytics & Reports**
- Custom report generation (PDF/Excel)
- Real-time dashboards
- Financial insights & forecasting
- Member activity analytics

### 🤝 **Collaborative Tools**
- Meeting scheduler & minutes
- Issue tracker & resolution workflow
- Message boards & announcements
- Document management
- Governance tracking

### 🤖 **AI-Powered Features**
- AI chat assistant with streaming responses
- Financial insights & suggestions
- Automated task recommendations
- Predictive analytics

### ⏰ **Automation & Scheduling**
- Celery background jobs
- Periodic tasks with Beat scheduler
- Automated reminders & notifications
- Workflow automation

### 🔒 **Security**
- CORS protection
- CSRF tokens
- Rate limiting & throttling
- Input validation & sanitization
- Secure password hashing (Argon2)
- Audit logging

---

## 🏗️ Architecture

```
┌─────────────────────┐
│  Frontend (Vercel)  │
│  React/Next.js      │
└──────────┬──────────┘
           │ HTTPS
           ▼
┌─────────────────────────────┐
│   API Gateway (Render)      │
│   Django DRF                │
│   - Authentication          │
│   - Business Logic          │
│   - API Endpoints           │
└──────────┬──────────────────┘
           │
      ┌────┴────────────────┐
      │                     │
      ▼                     ▼
┌────────────┐      ┌──────────────┐
│ PostgreSQL │      │ Redis Cache  │
│ (Render)   │      │ (Render)     │
└────────────┘      └──────────────┘
                           │
                           ▼
                    ┌────────────────┐
                    │ Celery Worker  │
                    │ (Background)   │
                    │ - Tasks        │
                    │ - Notifications│
                    └────────────────┘
                           │
                    ┌──────┴──────┐
                    ▼             ▼
              ┌─────────┐    ┌──────────┐
              │ Mailgun │    │Africa's  │
              │ (SMTP)  │    │Talking   │
              └─────────┘    │(SMS/OTP) │
                             └──────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Framework** | Django 5.1 + Django REST Framework 3.15 |
| **Database** | PostgreSQL 15+ |
| **Cache/Queue** | Redis 7+ |
| **Background Jobs** | Celery 5.4 + Celery Beat |
| **API Gateway** | Gunicorn 23 |
| **Authentication** | JWT (SimpleJWT) |
| **Documentation** | drf-spectacular (OpenAPI/Swagger) |
| **AI Integration** | OpenAI GPT API |
| **Notifications** | Mailgun (Email), Africa's Talking (SMS) |
| **Security** | Argon2, CORS, CSRF, Rate Limiting |
| **Monitoring** | Sentry, Health Checks |
| **Container** | Docker + Docker Compose |
| **Code Quality** | Ruff, Black, isort, pre-commit |

---

## 📦 Prerequisites

- **Python 3.12+**
- **PostgreSQL 13+** (or use Render managed)
- **Redis 6+** (or use Render managed)
- **Docker & Docker Compose** (optional, for containerization)
- **Git**

---

## 🚀 Quick Start

### 1️⃣ Clone the Repository

```bash
git clone https://github.com/kipruto45/Mychama-backend.git
cd Mychama-backend
```

### 2️⃣ Create Virtual Environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

### 4️⃣ Configure Environment Variables

```bash
cp .env.example .env
# Edit .env with your local settings
```

### 5️⃣ Run Migrations

```bash
python manage.py migrate
```

### 6️⃣ Create Superuser

```bash
python manage.py createsuperuser
```

### 7️⃣ Start Development Server

```bash
python manage.py runserver
```

**API will be available at:** `http://localhost:8000/api/`  
**Admin panel:** `http://localhost:8000/admin/`  
**API Docs (Swagger):** `http://localhost:8000/api/schema/swagger-ui/`

---

## 🐳 Docker Setup

### Build & Run

```bash
# Build the image
docker build -f docker/Dockerfile -t mychama-backend .

# Run with Docker Compose
docker-compose up -d
```

### Access Services

```
API:           http://localhost:8000/api/
Admin:         http://localhost:8000/admin/
Swagger Docs:  http://localhost:8000/api/schema/swagger-ui/
Flower (Celery): http://localhost:5555
```

### Check Logs

```bash
docker-compose logs -f app
docker-compose logs -f celery_worker
docker-compose logs -f celery_beat
```

---

## ⚙️ Configuration

### Environment Variables

Create `.env` in the project root:

```bash
# Django Settings
DEBUG=False
SECRET_KEY=your-secret-key-change-in-production
DJANGO_SETTINGS_MODULE=config.settings.production
ALLOWED_HOSTS=localhost,127.0.0.1,your-domain.com

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/digital_chama

# Redis & Cache
REDIS_URL=redis://localhost:6379/0
CACHE_URL=redis://localhost:6379/1
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# Security & CORS
CSRF_TRUSTED_ORIGINS=http://localhost:3000,https://your-frontend.com
CORS_ALLOWED_ORIGINS=http://localhost:3000,https://your-frontend.com

# Email (Mailgun)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.mailgun.org
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=postmaster@your-mailgun-domain.com
EMAIL_HOST_PASSWORD=your-mailgun-api-key
DEFAULT_FROM_EMAIL=noreply@your-domain.com

# SMS & OTP (Africa's Talking)
SMS_PROVIDER=africastalking
AFRICAS_TALKING_USERNAME=your_sandbox_username
AFRICAS_TALKING_API_KEY=your-api-key
AFRICAS_TALKING_SENDER_ID=MYCHAMA

# OTP Configuration
OTP_EXPIRY_MINUTES=5
OTP_MAX_ATTEMPTS=5
OTP_COOLDOWN_SECONDS=60
OTP_RESEND_WINDOW_SECONDS=600
OTP_MAX_RESENDS_PER_WINDOW=3

# AI (OpenAI)
OPENAI_API_KEY=sk-your-openai-api-key

# Logging
LOG_LEVEL=INFO

# Monitoring
SENTRY_DSN=https://your-sentry-dsn

# Frontend URL
FRONTEND_URL=http://localhost:3000
```

---

## 📡 API Documentation

### Base URL
- **Development:** `http://localhost:8000/api/`
- **Production:** `https://api.my-cham-a.app/api/`

### Authentication

All requests (except public endpoints) require JWT token in header:

```bash
Authorization: Bearer <your-token>
```

### Get Access Token

**POST** `/api/v1/auth/login/`

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Response:
```json
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

### Key Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| **POST** | `/api/v1/auth/register/` | Register new account |
| **POST** | `/api/v1/auth/otp/request/` | Request OTP |
| **POST** | `/api/v1/auth/otp/verify/` | Verify OTP |
| **GET** | `/api/v1/chamas/` | List user's chamas |
| **POST** | `/api/v1/chamas/` | Create new chama |
| **GET** | `/api/v1/payments/` | List payments |
| **POST** | `/api/v1/payments/` | Create payment |
| **GET** | `/api/v1/meetings/` | List meetings |
| **GET** | `/api/v1/finance/` | Financial summary |
| **POST** | `/api/ai/chat/` | AI chat (with streaming) |

📖 **Full API Documentation:** Visit `/api/schema/swagger-ui/` after starting the server

---

## 🧠 AI Features

### AI Chat with Streaming

```bash
POST /api/ai/chat/stream/

{
  "message": "How much do we have saved this month?",
  "chama_id": "uuid-of-chama"
}
```

### AI Suggestions

```bash
GET /api/public-ai/suggestions/?context=financial
```

---

## 🔔 Notifications & OTP

### OTP Flow

1. **Request OTP**
   ```bash
   POST /api/v1/auth/otp/request/
   { "phone_number": "+254712345678" }
   ```

2. **User receives SMS** (via Africa's Talking)

3. **Verify OTP**
   ```bash
   POST /api/v1/auth/otp/verify/
   { "phone_number": "+254712345678", "code": "123456" }
   ```

### Background Jobs (Celery)

Key background tasks:
- Email notifications
- SMS delivery
- Financial reports generation
- Automated reminders
- Data cleanup

Monitor with Flower:
```bash
flower -A config.celery --port=5555
# Access at http://localhost:5555
```

---

## 🗄️ Project Structure

```
digital_chama_system/
├── config/                    # Django settings
│   ├── settings/
│   │   ├── base.py           # Base configuration
│   │   ├── development.py    # Dev-specific
│   │   └── production.py     # Prod-specific
│   ├── urls.py               # URL routing
│   ├── wsgi.py               # WSGI application
│   └── celery.py             # Celery configuration
├── apps/                      # Django apps
│   ├── accounts/             # Authentication & users
│   ├── chama/                # Chama (group) management
│   ├── finance/              # Financial records
│   ├── payments/             # Payment processing
│   ├── notifications/        # Alerts & reminders
│   ├── meetings/             # Meetings & minutes
│   ├── issues/               # Issue tracking
│   ├── ai/                   # AI integration
│   ├── automations/          # Workflow automation
│   ├── billing/              # Billing & invoicing
│   ├── exports/              # Data export (PDF/Excel)
│   ├── reports/              # Report generation
│   ├── governance/           # Governance & roles
│   └── ...                   # Other modules
├── core/                      # Core utilities
│   ├── models.py             # Base models
│   ├── permissions.py        # Permission classes
│   ├── pagination.py         # Pagination
│   ├── throttles.py          # Rate limiting
│   ├── middleware.py         # Custom middleware
│   └── ...
├── api/                       # API routing & responses
│   ├── urls.py               # Unified API URLs
│   ├── views.py              # API views
│   └── routers.py            # DRF routers
├── docker/                    # Docker configuration
│   ├── Dockerfile
│   ├── nginx.conf
│   └── nginx.prod.conf
├── monitoring/               # Monitoring & observability
├── scripts/                  # Utility scripts
├── docker-compose.yml        # Container orchestration
├── manage.py                 # Django management
└── requirements.txt          # Python dependencies
```

---

## 📚 Core Modules

### **Accounts** (`apps/accounts/`)
- User registration & authentication
- OTP verification
- JWT token management
- Profile management
- Email confirmation

### **Chama** (`apps/chama/`)
- Group creation & management
- Member roles & permissions
- Membership workflows
- Group settings

### **Finance** (`apps/finance/`)
- Transaction recording
- Ledger management
- Balance tracking
- Financial reconciliation

### **Payments** (`apps/payments/`)
- Payment gateway integration
- Invoice generation
- Payment tracking
- Receipt management

### **Notifications** (`apps/notifications/`)
- Email notifications
- SMS alerts
- In-app notifications
- Notification preferences

### **Meetings** (`apps/meetings/`)
- Meeting scheduling
- Attendee tracking
- Minutes & resolutions
- Document management

### **AI** (`apps/ai/`)
- Chat interface with streaming
- Financial insights
- Automated suggestions
- Context-aware responses

### **Automations** (`apps/automations/`)
- Workflow rules
- Scheduled tasks
- Event-driven actions
- Integration with Celery

---

## 🚢 Deployment

### Deploy to Render

1. **Connect Repository**
   - Push code to GitHub
   - Sign in to [Render](https://render.com/)
   - Create new Web Service from GitHub

2. **Configure Build & Start Commands**

   **Build:** 
   ```bash
   pip install -r requirements.txt
   python manage.py collectstatic --noinput
   python manage.py migrate
   ```

   **Start:** 
   ```bash
   gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120
   ```

3. **Set Environment Variables**
   - Add all variables from `.env.production`
   - Use Render managed PostgreSQL & Redis
   - Configure custom domain

4. **Deploy Celery Worker**
   - Create new Background Worker
   - Start command: `celery -A config worker -l INFO`

5. **Deploy Celery Beat** (one of)
   - Option A: Background Worker: `celery -A config beat -l INFO`
   - Option B: Render Cron Job (recommended for free tier)

### Quick Deployment Checklist

- [ ] Database migrations run
- [ ] Static files collected
- [ ] Superuser created
- [ ] ALLOWED_HOSTS configured
- [ ] CSRF_TRUSTED_ORIGINS set
- [ ] Email credentials configured
- [ ] SMS provider credentials set
- [ ] OpenAI API key added
- [ ] Sentry DSN configured (optional)
- [ ] Health check endpoint tested

---

## 🧪 Testing

### Run Tests

```bash
# All tests
pytest

# Specific module
pytest tests/test_accounts_auth.py

# With coverage
pytest --cov=apps --cov-report=html
```

### Lint & Format Code

```bash
# Check with ruff
ruff check .

# Format with black
black .

# Sort imports
isort .

# All checks
make quality
```

---

## 📝 Contributing

1. Create feature branch: `git checkout -b feature/your-feature`
2. Commit changes: `git commit -am 'Add feature'`
3. Push to branch: `git push origin feature/your-feature`
4. Create Pull Request

### Code Style Guide

- Follow PEP 8
- Use type hints where possible
- Write docstrings for functions/classes
- Keep lines under 100 characters
- Use meaningful variable names

---

## 📄 License

This project is licensed under the **MIT License** - see LICENSE file for details.

---

## 🆘 Support & Documentation

- 📖 **API Docs:** `/api/schema/swagger-ui/`
- 🐛 **Issue Tracker:** GitHub Issues
- 💬 **Discussions:** GitHub Discussions
- 📧 **Email:** support@my-cham-a.app

---

## 🔗 Related Projects

- **Frontend:** [Mychama-frontend](https://github.com/kipruto45/Mychama-frontend)
- **Mobile:** (Coming soon)

---

**Built with ❤️ for African Savings Groups**
