#!/bin/bash
# Monitoring Setup Script for MyChama Backend
# Sets up Prometheus, Grafana, AlertManager, and Node Exporter

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITORING_DIR="$PROJECT_ROOT/monitoring"
DOCKER_COMPOSE_FILE="$MONITORING_DIR/docker-compose.monitoring.yml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}📊 MyChama Monitoring Setup${NC}"
echo "==================================="

mkdir -p "$MONITORING_DIR"

cat > "$DOCKER_COMPOSE_FILE" << 'EOF'
services:
  prometheus:
    image: prom/prometheus:latest
    container_name: mychama-prometheus
    ports:
      - "9090:9090"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--storage.tsdb.retention.time=200h"
      - "--web.enable-lifecycle"
    networks:
      - monitoring
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    container_name: mychama-grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
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
    container_name: mychama-alertmanager
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
    container_name: mychama-node-exporter
    ports:
      - "9100:9100"
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /:/rootfs:ro
    command:
      - "--path.procfs=/host/proc"
      - "--path.rootfs=/rootfs"
      - "--path.sysfs=/host/sys"
      - "--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)"
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

mkdir -p "$MONITORING_DIR/grafana/provisioning/datasources"
mkdir -p "$MONITORING_DIR/grafana/provisioning/dashboards"

cat > "$MONITORING_DIR/prometheus.yml" << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["prometheus:9090"]

  - job_name: "node-exporter"
    static_configs:
      - targets: ["node-exporter:9100"]

  - job_name: "mychama-backend"
    metrics_path: "/health/metrics/"
    static_configs:
      - targets: ["host.docker.internal:8000"]
EOF

echo -e "${GREEN}✓ Created Prometheus configuration${NC}"

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

cat > "$MONITORING_DIR/grafana/provisioning/dashboards/dashboard.yml" << 'EOF'
apiVersion: 1

providers:
  - name: "MyChama"
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
EOF

echo -e "${GREEN}✓ Created Grafana dashboard provisioning${NC}"

if [ -f "$MONITORING_DIR/grafana-dashboard.json" ]; then
  cp "$MONITORING_DIR/grafana-dashboard.json" "$MONITORING_DIR/grafana/provisioning/dashboards/mychama-dashboard.json"
  echo -e "${GREEN}✓ Copied Grafana dashboard${NC}"
else
  echo -e "${YELLOW}⚠ grafana-dashboard.json not found, skipping dashboard copy${NC}"
fi

cat > "$MONITORING_DIR/alertmanager.yml" << 'EOF'
global:
  resolve_timeout: 5m

route:
  group_by: ["alertname"]
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: "default"

receivers:
  - name: "default"
EOF

echo -e "${GREEN}✓ Created AlertManager configuration${NC}"

cat > "$MONITORING_DIR/start_monitoring.sh" << 'EOF'
#!/bin/bash
set -e

echo "Starting MyChama Monitoring Stack..."

docker compose -f docker-compose.monitoring.yml up -d

echo ""
echo "Monitoring services started!"
echo ""
echo "Access URLs:"
echo "- Grafana: http://localhost:3000"
echo "- Prometheus: http://localhost:9090"
echo "- AlertManager: http://localhost:9093"
echo "- Node Exporter: http://localhost:9100"
echo ""
echo "Grafana login:"
echo "- Username: admin"
echo "- Password: admin123"
echo ""
echo "To stop monitoring:"
echo "docker compose -f docker-compose.monitoring.yml down"
EOF

chmod +x "$MONITORING_DIR/start_monitoring.sh"

echo -e "${GREEN}✓ Created monitoring startup script${NC}"

cat > "$MONITORING_DIR/README.md" << 'EOF'
# MyChama Monitoring

This monitoring stack includes:

- Prometheus
- Grafana
- AlertManager
- Node Exporter

## Start monitoring

```bash
cd monitoring
./start_monitoring.sh
