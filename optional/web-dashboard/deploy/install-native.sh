#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/opt/bf4-status-dashboard
SERVICE_USER=bf4dashboard
ENV_DIR=/etc/bf4-status-dashboard
if [[ $EUID -ne 0 ]]; then echo "Run as root (or with sudo)." >&2; exit 1; fi
apt-get update
apt-get install -y python3-venv python3-pip python3-dev libpq-dev postgresql-client apache2
if ! id "$SERVICE_USER" >/dev/null 2>&1; then useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"; fi
mkdir -p "$ENV_DIR"; chown root:"$SERVICE_USER" "$ENV_DIR"; chmod 750 "$ENV_DIR"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R root:root "$APP_DIR"; chmod -R a+rX "$APP_DIR"
install -m 0644 "$APP_DIR/deploy/bf4-status-dashboard.service" /etc/systemd/system/bf4-status-dashboard.service
install -m 0644 "$APP_DIR/deploy/bf4-status-dashboard-sampler.service" /etc/systemd/system/bf4-status-dashboard-sampler.service
install -m 0644 "$APP_DIR/deploy/bf4-status-dashboard-sampler.timer" /etc/systemd/system/bf4-status-dashboard-sampler.timer
a2enmod proxy proxy_http headers
systemctl daemon-reload
echo "NEXT: create $ENV_DIR/dashboard.env from .env.example, then enable bf4-status-dashboard."
echo "Phase 2 sampler units are installed but intentionally not enabled."
