#!/bin/bash
# Start the opencode usage dashboard stack: exporter + prometheus + grafana.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

start() {
  pgrep -f "$1" >/dev/null && echo "already running: $1" && return 0
  echo "starting: $1"
  nohup $2 > "$DIR/$3" 2>&1 </dev/null &
  disown
  return 0
}

start "exporter.py 9105"  "python3 exporter.py 9105"            "logs/exporter.log"
start "prometheus --config.file=prometheus.yml" \
  "prometheus --config.file=prometheus.yml --storage.tsdb.path=./data-prom --web.listen-address=127.0.0.1:9090" \
  "logs/prometheus.log"
start "grafana server --config=grafana/grafana.ini" \
  "$(brew --prefix grafana)/bin/grafana server --config=grafana/grafana.ini --homepath=$(brew --prefix grafana)/share/grafana" \
  "logs/grafana.log"

echo
echo "All started. Verify:"
echo "  http://127.0.0.1:9999/api/health   (grafana, port 9999)"
echo "  http://127.0.0.1:9090/-/healthy    (prometheus)"
echo "  http://127.0.0.1:9105/metrics      (exporter)"
echo
echo "Dashboard: http://127.0.0.1:9999/d/opencode-usage/opencode-agent-usage"