#!/usr/bin/env bash
# Uncomment demo sections in the sibling state repository.
# Usage: ./utils/uncomment_demo.sh <phase>
# Phases: network, storage-web, storage-db, storage-app, kernel-web, kernel-db,
#         kernel-app, firewall-web, firewall-db, firewall-app, all
set -euo pipefail

STATE_DIR="${STATE_DIR:-../aap-demo-rhel-gitops-state}"
PHASE="${1:-}"

if [ -z "$PHASE" ]; then
  cat <<EOF
Usage: $0 <phase>
Phases:
  network        — Uncomment static_internal in host_vars/*/network.yml
  storage-web    — Uncomment web_content_pool in web_servers and host_vars
  storage-db     — Uncomment db_data_pool in database_servers and host_vars
  storage-app    — Uncomment app_data_pool in app_servers and host_vars
  kernel-web     — Uncomment kernel_settings in web_servers
  kernel-db      — Uncomment kernel_settings in database_servers
  kernel-app     — Uncomment kernel_settings in app_servers
  firewall-web   — Uncomment HTTP/HTTPS in web_servers
  firewall-db    — Uncomment PostgreSQL in database_servers
  firewall-app   — Uncomment REST ports in app_servers
  all            — Uncomment every demo section
EOF
  exit 1
fi

uncomment() { sed -i '' -E '/^# [A-Z]/! s/^# //' "$@"; }

case "$PHASE" in
  network)
    # Uncomment the static_internal sections in each host's network.yml
    # The sed range matches from "#   static_internal:" to the end of its block
    for host in web-01 web-02 db-01 app-01; do
      f="$STATE_DIR/host_vars/$host/network.yml"
      uncomment "$f"
    done
    ;;
  storage-web)
    uncomment "$STATE_DIR/group_vars/web_servers/storage.yml"
    for host in web-01 web-02; do
      uncomment "$STATE_DIR/host_vars/$host/storage.yml"
    done
    ;;
  storage-db)
    uncomment "$STATE_DIR/group_vars/database_servers/storage.yml"
    uncomment "$STATE_DIR/host_vars/db-01/storage.yml"
    ;;
  storage-app)
    uncomment "$STATE_DIR/group_vars/app_servers/storage.yml"
    uncomment "$STATE_DIR/host_vars/app-01/storage.yml"
    ;;
  kernel-web)
    uncomment "$STATE_DIR/group_vars/web_servers/kernel_settings.yml"
    ;;
  kernel-db)
    uncomment "$STATE_DIR/group_vars/database_servers/kernel_settings.yml"
    ;;
  kernel-app)
    uncomment "$STATE_DIR/group_vars/app_servers/kernel_settings.yml"
    ;;
  firewall-web)
    uncomment "$STATE_DIR/group_vars/web_servers/firewall.yml"
    ;;
  firewall-db)
    uncomment "$STATE_DIR/group_vars/database_servers/firewall.yml"
    ;;
  firewall-app)
    uncomment "$STATE_DIR/group_vars/app_servers/firewall.yml"
    ;;
  all)
    sh "$0" network
    sh "$0" storage-web
    sh "$0" storage-db
    sh "$0" storage-app
    sh "$0" kernel-web
    sh "$0" kernel-db
    sh "$0" kernel-app
    sh "$0" firewall-web
    sh "$0" firewall-db
    sh "$0" firewall-app
    ;;
  *)
    echo "Unknown phase: $PHASE" >&2
    exit 1
    ;;
esac

echo
echo "Demo sections uncommented for phase: $PHASE"
echo "Run: cd $STATE_DIR && git diff && git add -A && git commit -m 'Enable $PHASE' && git push"
