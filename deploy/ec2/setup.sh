#!/usr/bin/env bash
# One-time setup on a free-tier Ubuntu EC2 (t2.micro / t3.micro).
# Run as ubuntu after cloning the repo to /opt/quant-api
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/quant-api}"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git curl

# Prefer 3.11 if available (Ubuntu 22.04+ / deadsnakes optional)
if ! command -v python3.11 >/dev/null 2>&1; then
  echo "python3.11 not found — using $(python3 --version)"
fi

echo "==> App directory: $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER:$USER" "$APP_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
  echo "Clone your backend repo into $APP_DIR first, e.g.:"
  echo "  git clone https://github.com/YOU/quant-api.git $APP_DIR"
  exit 1
fi

cd "$APP_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created $APP_DIR/.env from .env.example"
  echo "EDIT IT NOW with production values, then re-run this script:"
  echo "  nano $APP_DIR/.env"
  exit 1
fi

echo "==> Python venv + deps"
python3.11 -m venv .venv 2>/dev/null || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> systemd service"
sudo cp deploy/ec2/quant-api.service /etc/systemd/system/quant-api.service
# Fix paths if APP_DIR is not /opt/quant-api
if [ "$APP_DIR" != "/opt/quant-api" ]; then
  sudo sed -i "s|/opt/quant-api|$APP_DIR|g" /etc/systemd/system/quant-api.service
fi
sudo systemctl daemon-reload
sudo systemctl enable quant-api
sudo systemctl restart quant-api

echo "==> Health"
sleep 2
curl -fsS http://127.0.0.1:8000/api/health || true
echo
echo "Done. Open security group port 8000 (or put nginx on 80/443 later)."
echo "Production env file lives ONLY on the server: $APP_DIR/.env"
