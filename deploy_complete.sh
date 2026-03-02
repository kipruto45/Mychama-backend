#!/bin/bash

# Digital Chama System - Complete Deployment Workflow
# This script handles the entire deployment process from droplet creation to validation

set -e

# Configuration - EDIT THESE VALUES
DIGITALOCEAN_TOKEN=""  # Your DigitalOcean API token
SSH_KEY_FINGERPRINT=""  # Your SSH key fingerprint (doctl compute ssh-key list)
DOMAIN="staging.digitalchama.com"  # Your domain for staging
EMAIL="admin@digitalchama.com"     # Email for SSL certificates

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# Check prerequisites
check_prerequisites() {
    print_step "Checking prerequisites..."

    if [ -z "$DIGITALOCEAN_TOKEN" ]; then
        print_error "Please set DIGITALOCEAN_TOKEN in this script"
        exit 1
    fi

    if [ -z "$SSH_KEY_FINGERPRINT" ]; then
        print_error "Please set SSH_KEY_FINGERPRINT in this script"
        print_error "Run: doctl compute ssh-key list"
        exit 1
    fi

    command -v doctl >/dev/null 2>&1 || { print_error "doctl not found. Install it first."; exit 1; }
    command -v jq >/dev/null 2>&1 || { print_error "jq not found. Install with: sudo apt install jq"; exit 1; }
}

# Authenticate with DigitalOcean
setup_doctl() {
    print_step "Setting up DigitalOcean CLI..."
    doctl auth init --access-token "$DIGITALOCEAN_TOKEN"
}

# Create droplet
create_droplet() {
    print_step "Creating DigitalOcean droplet..."

    DROPLET_NAME="digital-chama-staging-$(date +%Y%m%d-%H%M%S)"

    # Create droplet with 2GB RAM, Ubuntu 22.04
    DROPLET_RESPONSE=$(doctl compute droplet create "$DROPLET_NAME" \
        --image ubuntu-22-04-x64 \
        --size s-2vcpu-4gb \
        --region nyc1 \
        --ssh-keys "$SSH_KEY_FINGERPRINT" \
        --wait \
        --format ID,Name,PublicIPv4,Status \
        --no-header)

    DROPLET_ID=$(echo "$DROPLET_RESPONSE" | awk '{print $1}')
    DROPLET_IP=$(echo "$DROPLET_RESPONSE" | awk '{print $3}')

    if [ -z "$DROPLET_IP" ]; then
        print_error "Failed to create droplet or get IP address"
        exit 1
    fi

    print_status "Droplet created: $DROPLET_NAME (ID: $DROPLET_ID, IP: $DROPLET_IP)"

    # Export for later use
    echo "export DROPLET_IP=$DROPLET_IP" >> .deployment_env
    echo "export DROPLET_ID=$DROPLET_ID" >> .deployment_env
}

# Wait for droplet to be ready
wait_for_droplet() {
    print_step "Waiting for droplet to be ready..."
    sleep 60  # Give it time to boot

    # Test SSH connection
    local retries=10
    while [ $retries -gt 0 ]; do
        if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "root@$DROPLET_IP" "echo 'SSH ready'" 2>/dev/null; then
            print_status "SSH connection established"
            break
        fi
        print_warning "Waiting for SSH... ($retries attempts left)"
        sleep 30
        ((retries--))
    done

    if [ $retries -eq 0 ]; then
        print_error "Failed to establish SSH connection"
        exit 1
    fi
}

# Deploy application
deploy_application() {
    print_step "Deploying application to server..."

    # Copy deployment script to server
    scp -o StrictHostKeyChecking=no deploy_staging.sh "root@$DROPLET_IP:/tmp/"

    # Copy application code
    print_status "Uploading application code..."
    rsync -avz --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
        -e "ssh -o StrictHostKeyChecking=no" \
        . "root@$DROPLET_IP:/opt/digital-chama/"

    # Run deployment script
    ssh -o StrictHostKeyChecking=no "root@$DROPLET_IP" "cd /opt/digital-chama && chmod +x deploy_staging.sh && ./deploy_staging.sh"
}

# Configure environment
configure_environment() {
    print_step "Configuring environment variables..."

    # Generate secure values
    SECRET_KEY=$(openssl rand -hex 32)
    DB_PASSWORD=$(openssl rand -hex 16)

    # Create .env.production on server
    ssh -o StrictHostKeyChecking=no "root@$DROPLET_IP" "cat > /opt/digital-chama/.env.production << EOF
# Database Configuration
DB_HOST=postgres
DB_PORT=5432
DB_NAME=digital_chama
DB_USER=digital_chama
DB_PASSWORD=$DB_PASSWORD

# Redis Configuration
REDIS_URL=redis://redis:6379/0

# Django Configuration
SECRET_KEY=$SECRET_KEY
DJANGO_SETTINGS_MODULE=config.settings.production
DEBUG=False

# Domain Configuration
DOMAIN=$DOMAIN
ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1

# SSL Configuration (will be updated after certbot)
SSL_CERT_PATH=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
SSL_KEY_PATH=/etc/letsencrypt/live/$DOMAIN/privkey.pem

# OpenAI Configuration - SET THESE MANUALLY
OPENAI_API_KEY=your_openai_api_key_here

# M-Pesa Configuration - SET THESE MANUALLY
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
EOF"

    print_warning "Please manually update OpenAI and M-Pesa credentials in .env.production"
}

# Setup domain and SSL
setup_domain_ssl() {
    print_step "Setting up domain and SSL..."

    print_warning "Please ensure your domain $DOMAIN points to $DROPLET_IP"
    print_warning "This may take up to 24 hours for DNS propagation"
    read -p "Press Enter when DNS is updated..."

    # Setup SSL
    ssh -o StrictHostKeyChecking=no "root@$DROPLET_IP" "sudo certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email $EMAIL"

    # Update nginx for SSL
    ssh -o StrictHostKeyChecking=no "root@$DROPLET_IP" "sudo systemctl reload nginx"
}

# Run validation tests
run_validation_tests() {
    print_step "Running validation tests..."

    # Health check
    print_status "Testing health endpoint..."
    if curl -f "http://$DROPLET_IP/api/v1/health/" > /dev/null 2>&1; then
        print_status "✅ Health check passed"
    else
        print_error "❌ Health check failed"
    fi

    # Load testing
    print_status "Running load tests..."
    ./run_load_tests.sh "http://$DROPLET_IP"

    # SSL check
    if curl -f "https://$DOMAIN/api/v1/health/" > /dev/null 2>&1; then
        print_status "✅ SSL/HTTPS working"
    else
        print_warning "⚠️  SSL/HTTPS not working yet (DNS may still be propagating)"
    fi
}

# Main deployment flow
main() {
    print_status "🚀 Starting Digital Chama System Deployment"

    check_prerequisites
    setup_doctl
    create_droplet
    source .deployment_env  # Load droplet info
    wait_for_droplet
    deploy_application
    configure_environment
    setup_domain_ssl
    run_validation_tests

    print_status "🎉 Deployment completed successfully!"
    print_status "Application URL: https://$DOMAIN"
    print_status "Admin panel: https://$DOMAIN/admin/"
    print_warning "Next steps:"
    print_warning "1. Update OpenAI and M-Pesa API keys"
    print_warning "2. Test payment integration manually"
    print_warning "3. Set up monitoring and alerts"
    print_warning "4. Run full user acceptance testing"
}

# Run main function
main "$@"