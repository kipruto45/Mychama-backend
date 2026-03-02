#!/bin/bash
# Monitoring Setup Script for Digital Chama System
# Sets up Prometheus, Grafana, and AlertManager for production monitoring

set -e

# Configuration
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITORING_DIR="$PROJECT_ROOT/monitoring"
DOCKER_COMPOSE_FILE="$MONITORING_DIR/docker-compose.monitoring.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}📊 Digital Chama Monitoring Setup${NC}"
echo "==================================="

# Create monitoring directory
mkdir -p "$MONITORING_DIR"

# Create Docker Compose for monitoring stack
cat > "$DOCKER_COMPOSE_FILE" << 'EOF'
version: '3.8'

services:
  prometheus:
    image: prom/prometheus:latest
    container_name: digital_chama_prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'
      - '--storage.tsdb.retention.time=200h'
      - '--web.enable-lifecycle'
    networks:
      - monitoring
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    container_name: digital_chama_grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin123
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
    networks:
      - monitoring
    depends_on:
      - prometheus
    restart: unless-stopped

  alertmanager:
    image: prom/alertmanager:latest
    container_name: digital_chama_alertmanager
    ports:
      - "9093:9093"
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/config.yml:ro
      - alertmanager_data:/alertmanager
    networks:
      - monitoring
    restart: unless-stopped

  node-exporter:
    image: prom/node-exporter:latest
    container_name: digital_chama_node_exporter
    ports:
      - "9100:9100"
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /:/rootfs:ro
    command:
      - '--path.procfs=/host/proc'
      - '--path.rootfs=/rootfs'
      - '--path.sysfs=/host/sys'
      - '--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)'
    networks:
      - monitoring
    restart: unless-stopped

volumes:
  prometheus_data:
  grafana_data:
  alertmanager_data:

networks:
  monitoring:
    driver: bridge
EOF

echo -e "${GREEN}✓ Created monitoring Docker Compose file${NC}"

# Create Grafana provisioning directory
mkdir -p "$MONITORING_DIR/grafana/provisioning/datasources"
mkdir -p "$MONITORING_DIR/grafana/provisioning/dashboards"

# Create Grafana datasource configuration
cat > "$MONITORING_DIR/grafana/provisioning/datasources/prometheus.yml" << 'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true
EOF

echo -e "${GREEN}✓ Created Grafana datasource configuration${NC}"

# Create Grafana dashboard provisioning
cat > "$MONITORING_DIR/grafana/provisioning/dashboards/dashboard.yml" << 'EOF'
apiVersion: 1

providers:
  - name: 'Digital Chama'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
EOF

echo -e "${GREEN}✓ Created Grafana dashboard provisioning${NC}"

# Copy dashboard to provisioning directory
cp "$MONITORING_DIR/grafana-dashboard.json" "$MONITORING_DIR/grafana/provisioning/dashboards/digital-chama-dashboard.json"

# Create AlertManager configuration
cat > "$MONITORING_DIR/alertmanager.yml" << 'EOF'
global:
  smtp_smarthost: 'smtp.gmail.com:587'
  smtp_from: 'alerts@yourdomain.com'
  smtp_auth_username: 'your-email@gmail.com'
  smtp_auth_password: 'your-app-password'

route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'email-alerts'
  routes:
  - match:
      severity: critical
    receiver: 'email-alerts'

receivers:
- name: 'email-alerts'
  email_configs:
  - to: 'admin@yourdomain.com'
    send_resolved: true

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'instance']
EOF

echo -e "${GREEN}✓ Created AlertManager configuration${NC}"

# Create monitoring startup script
cat > "$MONITORING_DIR/start_monitoring.sh" << 'EOF'
#!/bin/bash
# Start Monitoring Stack

set -e

echo "Starting Digital Chama Monitoring Stack..."

# Start monitoring services
docker-compose -f docker-compose.monitoring.yml up -d

echo "Monitoring services started!"
echo ""
echo "Access URLs:"
echo "- Grafana: http://localhost:3000 (admin/admin123)"
echo "- Prometheus: http://localhost:9090"
echo "- AlertManager: http://localhost:9093"
echo "- Node Exporter: http://localhost:9100"
echo ""
echo "To stop monitoring: docker-compose -f docker-compose.monitoring.yml down"
EOF

chmod +x "$MONITORING_DIR/start_monitoring.sh"

echo -e "${GREEN}✓ Created monitoring startup script${NC}"

# Create README for monitoring
cat > "$MONITORING_DIR/README.md" << 'EOF'
# Digital Chama Monitoring Setup

This directory contains the monitoring stack configuration for the Digital Chama system.

## Services

- **Prometheus**: Metrics collection and storage
- **Grafana**: Visualization and dashboards
- **AlertManager**: Alert management and notifications
- **Node Exporter**: System metrics collection

## Quick Start

1. **Start monitoring stack**:
   ```bash
   ./start_monitoring.sh
   ```

2. **Access services**:
   - Grafana: http://localhost:3000 (admin/admin123)
   - Prometheus: http://localhost:9090
   - AlertManager: http://localhost:9093

3. **Import dashboard**:
   - In Grafana, go to Dashboards → Import
   - Upload `grafana-dashboard.json`

## Configuration

### Prometheus
- Scrapes metrics from Django app (`/health/metrics/`)
- Collects system metrics via Node Exporter
- Retention: 200 hours

### Grafana
- Pre-configured with Prometheus datasource
- Auto-provisions Digital Chama dashboard
- Default admin credentials: admin/admin123

### AlertManager
- Configured for email notifications
- Update SMTP settings in `alertmanager.yml`

## Metrics Available

### Application Metrics
- Total users and active users
- Chama statistics
- Financial activity (contributions, loans)
- Response times and error rates

### System Metrics
- CPU, memory, disk usage
- Network I/O
- Process information

## Custom Alerts

Add custom alert rules in `prometheus.yml`:

```yaml
rule_files:
  - "alert_rules.yml"
```

Example alert_rules.yml:
```yaml
groups:
  - name: digital_chama_alerts
    rules:
      - alert: HighCPUUsage
        expr: digital_chama_cpu_usage_percent > 90
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High CPU usage detected"
          description: "CPU usage is {{ $value }}%"

      - alert: ServiceDown
        expr: up{job="digital-chama"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Digital Chama service is down"
```

## Integration with Production

For production deployment:

1. **Update network configuration** to connect to your application containers
2. **Configure external storage** for Prometheus and Grafana data
3. **Set up proper authentication** for Grafana
4. **Configure SMTP** for AlertManager notifications
5. **Set up reverse proxy** (nginx) for external access

## Troubleshooting

### Common Issues

1. **Grafana not accessible**:
   - Check if port 3000 is available
   - Verify Docker containers are running

2. **No metrics in Prometheus**:
   - Check application health endpoint: `curl http://your-app/health/metrics/`
   - Verify network connectivity between containers

3. **Dashboard not loading**:
   - Check Grafana logs: `docker logs digital_chama_grafana`
   - Verify dashboard JSON is valid

### Logs

View logs for each service:
```bash
docker logs digital_chama_prometheus
docker logs digital_chama_grafana
docker logs digital_chama_alertmanager
```

## Security Notes

- Change default Grafana password in production
- Configure proper authentication and authorization
- Use HTTPS for external access
- Restrict network access to monitoring ports
- Regularly update Docker images
EOF

echo -e "${GREEN}✓ Created monitoring README${NC}"

echo ""
echo -e "${BLUE}🎉 Monitoring setup complete!${NC}"
echo ""
echo "Next steps:"
echo "1. Start monitoring: cd monitoring && ./start_monitoring.sh"
echo "2. Access Grafana at http://localhost:3000"
echo "3. Import the Digital Chama dashboard"
echo "4. Update alert configurations for your environment"
echo ""
echo "Files created:"
echo "- docker-compose.monitoring.yml"
echo "- grafana/provisioning/datasources/prometheus.yml"
echo "- grafana/provisioning/dashboards/dashboard.yml"
echo "- grafana/provisioning/dashboards/digital-chama-dashboard.json"
echo "- alertmanager.yml"
echo "- start_monitoring.sh"
echo "- README.md"