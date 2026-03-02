#!/bin/bash

# Digital Chama System - Staging Deployment Script
# This script deploys the system to a staging environment (e.g., DigitalOcean droplet)

set -e

echo "🚀 Starting Digital Chama System Staging Deployment..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root or with sudo
if [[ $EUID -eq 0 ]]; then
   print_error "This script should not be run as root. Please run as a regular user with sudo access."
   exit 1
fi

# Update system packages
print_status "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install required packages
print_status "Installing required packages..."
sudo apt install -y curl wget git ufw docker.io docker-compose-plugin nginx certbot python3-certbot-nginx

# Enable and start Docker
print_status "Enabling Docker service..."
sudo systemctl enable docker
sudo systemctl start docker

# Add current user to docker group
sudo usermod -aG docker $USER
print_warning "You may need to log out and back in for Docker group changes to take effect."

# Create application directory
print_status "Creating application directory..."
sudo mkdir -p /opt/digital-chama
sudo chown $USER:$USER /opt/digital-chama

# Clone or copy the application code
# Assuming the code is already available; in production, you'd clone from repo
print_status "Setting up application code..."
cp -r /home/kipruto/Desktop/CHAMA/digital_chama_system/* /opt/digital-chama/
cd /opt/digital-chama

# Create .env.production file if it doesn't exist
if [ ! -f .env.production ]; then
    print_status "Creating .env.production template..."
    cat > .env.production << EOF
# Database Configuration
DB_HOST=postgres
DB_PORT=5432
DB_NAME=digital_chama
DB_USER=digital_chama
DB_PASSWORD=CHANGE_THIS_STRONG_PASSWORD

# Redis Configuration
REDIS_URL=redis://redis:6379/0

# Django Configuration
SECRET_KEY=CHANGE_THIS_TO_A_STRONG_SECRET_KEY
DJANGO_SETTINGS_MODULE=config.settings.production
DEBUG=False

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key_here

# M-Pesa Configuration
MPESA_CONSUMER_KEY=your_mpesa_consumer_key
MPESA_CONSUMER_SECRET=your_mpesa_consumer_secret
MPESA_SHORTCODE=your_mpesa_shortcode
MPESA_PASSKEY=your_mpesa_passkey

# Email Configuration (optional)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your_email@gmail.com
EMAIL_HOST_PASSWORD=your_app_password

# Domain Configuration
DOMAIN=staging.yourdomain.com
ALLOWED_HOSTS=staging.yourdomain.com,localhost,127.0.0.1

# SSL Configuration
SSL_CERT_PATH=/etc/letsencrypt/live/staging.yourdomain.com/fullchain.pem
SSL_KEY_PATH=/etc/letsencrypt/live/staging.yourdomain.com/privkey.pem
EOF
    print_warning "Please edit .env.production with your actual configuration values!"
fi

# Create logs directory
mkdir -p logs

# Set up firewall
print_status "Configuring firewall..."
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable

# Configure Nginx
print_status "Configuring Nginx..."
sudo cp docker/nginx.conf /etc/nginx/sites-available/digital-chama
sudo ln -sf /etc/nginx/sites-available/digital-chama /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# Build and start the application
print_status "Building and starting the application..."
docker-compose -f docker-compose.prod.yml up -d --build

# Wait for services to be healthy
print_status "Waiting for services to start..."
sleep 30

# Run database migrations
print_status "Running database migrations..."
docker-compose -f docker-compose.prod.yml exec web python manage.py migrate

# Collect static files
print_status "Collecting static files..."
docker-compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput

# Create superuser (optional)
print_status "Creating superuser..."
docker-compose -f docker-compose.prod.yml exec web python manage.py createsuperuser --noinput --username admin --email admin@digitalchama.com || true

# Set up SSL with Let's Encrypt (if domain is configured)
if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "staging.yourdomain.com" ]; then
    print_status "Setting up SSL certificate..."
    sudo certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@digitalchama.com
fi

# Restart services
print_status "Restarting services..."
docker-compose -f docker-compose.prod.yml restart

print_status "🎉 Staging deployment completed!"
print_status "Application should be available at: http://your-server-ip"
print_status "Admin panel: http://your-server-ip/admin/"
print_warning "Remember to:"
print_warning "1. Update .env.production with real values"
print_warning "2. Configure your domain DNS to point to this server"
print_warning "3. Run load tests: ./run_load_tests.sh"
print_warning "4. Set up monitoring: Deploy Grafana/Prometheus"
print_warning "5. Test payment integration manually"