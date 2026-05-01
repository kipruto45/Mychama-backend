# MyChama Backend API

> Enterprise-grade REST API for managing community savings groups (Chamas)

[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python)](https://www.python.org)
[![Django](https://img.shields.io/badge/Django-4.2+-092e20?style=flat-square&logo=django)](https://www.djangoproject.com)
[![DRF](https://img.shields.io/badge/DRF-3.14+-a30000?style=flat-square)](https://www.django-rest-framework.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-336791?style=flat-square&logo=postgresql)](https://www.postgresql.org)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

## Overview

MyChama Backend is a robust, scalable REST API built with Django and Django REST Framework. It powers the MyChama mobile application and provides comprehensive features for managing community savings groups, including member management, financial tracking, loan processing, and governance.

## ✨ Key Features

### Authentication & Security
- 🔐 **JWT Authentication** - Secure token-based authentication
- ✅ **OTP Verification** - SMS-based two-factor authentication
- 👤 **Role-Based Access Control (RBAC)** - Granular permission system
- 🛡️ **API Key Management** - Secure API key generation and rotation
- 📋 **Audit Logging** - Complete audit trail for all operations

### Member & Chama Management
- 👥 **Member Management** - Complete member lifecycle management
- 🏘️ **Chama Management** - Multi-level organization support
- 📊 **Member Profiles** - KYC verification and document storage
- 🎯 **Role Delegation** - Dynamic role assignment and management
- 📤 **Bulk Imports** - CSV-based bulk member operations

### Financial Operations
- 💰 **Contribution Tracking** - Real-time contribution recording
- 💳 **Payment Processing** - M-Pesa, bank transfers, and cash payments
- 💸 **Wallet Management** - Member wallet operations
- 🏦 **Loan Management** - Complete loan lifecycle management
- 📊 **Financial Reports** - Comprehensive financial analytics
- 🔍 **Payment Disputes** - Dispute resolution workflow

### Governance & Compliance
- 📅 **Meeting Management** - Schedule and track meetings
- ✍️ **Meeting Minutes** - Document decisions and resolutions
- 📋 **Governance Rules** - Configurable governance policies
- 📊 **Compliance Monitoring** - Automated compliance checks
- 🔐 **KYC Management** - KYC verification workflows

### Advanced Features
- 🤖 **AI Integration** - Smart recommendations and insights
- 📧 **Email Notifications** - Automated email communications
- 📱 **SMS Integration** - SMS notifications and OTP delivery
- 🔔 **Push Notifications** - Real-time mobile notifications
- 📊 **Analytics & Reporting** - Comprehensive business intelligence
- ⚡ **Background Tasks** - Asynchronous task processing with Celery

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 14+
- Redis (for Celery)
- Git

### Installation

\`\`\`bash
# Clone the repository
git clone https://github.com/kipruto45/Mychama-backend.git
cd Mychama-backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Update .env with your configuration

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start development server
python manage.py runserver
\`\`\`

## 📁 Project Structure

\`\`\`
Mychama-backend/
├── apps/
│   ├── accounts/          # User authentication & profiles
│   ├── chama/             # Chama management
│   ├── finance/           # Financial operations
│   ├── payments/          # Payment processing
│   ├── loans/             # Loan management
│   ├── governance/        # Governance & meetings
│   ├── audit/             # Audit logging
│   ├── notifications/     # Email/SMS notifications
│   ├── ai/                # AI features
│   └── analytics/         # Analytics & reporting
├── config/                # Django settings
├── core/                  # Core utilities & helpers
├── docs/                  # API documentation
├── scripts/               # Management scripts
├── tests/                 # Test suite
├── manage.py              # Django CLI
└── requirements.txt       # Python dependencies
\`\`\`

## 🔐 Security

- ✅ HTTPS/TLS enforced in production
- ✅ CORS configured for mobile app
- ✅ SQL injection prevention via ORM
- ✅ CSRF protection enabled
- ✅ Rate limiting on sensitive endpoints
- ✅ Input validation and sanitization

## 🧪 Testing

\`\`\`bash
# Run all tests
python manage.py test

# Run specific app tests
python manage.py test apps.chama
\`\`\`

## 🚀 Deployment

### Using Docker
\`\`\`bash
docker build -t mychama-backend .
docker run -d -p 8000:8000 \\
  -e DATABASE_URL=postgresql://... \\
  mychama-backend
\`\`\`

## 🤝 Contributing

We welcome contributions! Please:

1. Fork the repository
2. Create a feature branch
3. Follow PEP 8 style guide
4. Write tests for new features
5. Submit a Pull Request

## 📄 License

This project is licensed under the MIT License - see [LICENSE](LICENSE)

## 📞 Support

- **Issues**: Report bugs via [GitHub Issues](https://github.com/kipruto45/Mychama-backend/issues)
- **Email**: support@mychama.app
- **Documentation**: [MyChama Docs](https://docs.mychama.app)

---

**Made with Django + PostgreSQL**  
**Powering Community Savings Groups Across Africa**
