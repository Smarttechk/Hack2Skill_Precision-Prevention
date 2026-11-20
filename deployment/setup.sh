#!/bin/sh
# First-time setup on a fresh Ubuntu VPS. Run as root, from the cloned repo:
#   cd /opt/hack2skill/app && sh deployment/setup.sh YOUR_VPS_IP
# It installs Python and nginx, loads the demo pack, asks you to create the admin account,
# then starts the app as a service behind nginx with HTTPS (self-signed unless you have a domain).
set -eu

SERVER_NAME="${1:-}"
[ -n "$SERVER_NAME" ] || { echo "Usage: sh deployment/setup.sh YOUR_VPS_IP_OR_DOMAIN"; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Run this as root."; exit 1; }

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER=hack2skill

echo "== packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip git nginx openssl ufw

echo "== service user"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "== python environment"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "== demo data (simulated results and the built app)"
if [ ! -d "$APP_DIR/frontend/dist" ]; then
  (cd "$APP_DIR" && "$APP_DIR/venv/bin/python" scripts/demo_pack.py load)
fi
mkdir -p "$APP_DIR/outputs"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR/outputs" "$APP_DIR/config" "$APP_DIR/data" 2>/dev/null || true

echo "== admin account"
# Without an account the app opens to everyone as an admin. Do not go on until one exists.
if [ ! -f "$APP_DIR/config/users.json" ]; then
  printf "Admin user name: "
  read -r ADMIN_NAME
  (cd "$APP_DIR" && "$APP_DIR/venv/bin/python" scripts/add_user.py "$ADMIN_NAME" admin)
fi
[ -f "$APP_DIR/config/users.json" ] || { echo "No account was created, so I am stopping."; exit 1; }
chown "$APP_USER":"$APP_USER" "$APP_DIR/config/users.json"
chmod 600 "$APP_DIR/config/users.json"

echo "== https certificate"
CERT_DIR=/etc/ssl/hack2skill
mkdir -p "$CERT_DIR"
if [ ! -f "$CERT_DIR/cert.pem" ]; then
  # self-signed: browsers will warn once. With a domain, run certbot afterwards (see deployment/README.md).
  case "$SERVER_NAME" in
    *[!0-9.]*) SAN="DNS:$SERVER_NAME" ;;
    *) SAN="IP:$SERVER_NAME" ;;
  esac
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
    -subj "/CN=$SERVER_NAME" -addext "subjectAltName=$SAN"
  chmod 600 "$CERT_DIR/key.pem"
fi

echo "== systemd service"
sed -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__APP_USER__|$APP_USER|g" "$APP_DIR/deployment/hack2skill.service" > /etc/systemd/system/hack2skill.service
if [ ! -f /etc/hack2skill.env ]; then
  cp "$APP_DIR/deployment/hack2skill.env.example" /etc/hack2skill.env
  chmod 600 /etc/hack2skill.env
fi
systemctl daemon-reload
systemctl enable --now hack2skill

echo "== nginx"
sed -e "s|__SERVER_NAME__|$SERVER_NAME|g" "$APP_DIR/deployment/nginx.conf" > /etc/nginx/sites-available/hack2skill
ln -sf /etc/nginx/sites-available/hack2skill /etc/nginx/sites-enabled/hack2skill
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "== firewall (SSH, HTTP, HTTPS only)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo
echo "Done. Open https://$SERVER_NAME (accept the certificate warning if it is self-signed)."
echo "Check:  curl -k https://$SERVER_NAME/api/health"
echo "Logs:   journalctl -u hack2skill -n 100 --no-pager"
