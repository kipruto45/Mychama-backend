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
