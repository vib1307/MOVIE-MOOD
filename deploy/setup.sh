#!/usr/bin/env bash
# One-time (and re-runnable) server setup for MovieMood on Ubuntu 24.04 arm64 (Hetzner CAX31).
# See deploy/README.md and D-028.
#
# Usage, as root, from the cloned repo:
#   cd /opt/moviemood && sudo bash deploy/setup.sh moviemood.duckdns.org
#
# Safe to re-run: every step checks or overwrites. It does NOT start the app if .env or
# data/movies.json is missing; it prints what to do next instead.

set -euo pipefail

DOMAIN="${1:?usage: sudo bash deploy/setup.sh <domain, e.g. moviemood.duckdns.org>}"
APP_DIR=/opt/moviemood
APP_USER=moviemood
PY_VERSION=3.11  # same as the dev venv; requirements.lock was frozen there
export UV_PYTHON_INSTALL_DIR=/opt/uv-python  # not /root, so the app user can read the interpreter

say() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }
[[ "$(pwd)" == "$APP_DIR" ]] || { echo "run from $APP_DIR (the cloned repo)"; exit 1; }

say "System packages"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q nginx certbot python3-certbot-nginx git curl rsync ufw

say "App user $APP_USER"
id "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

say "Python $PY_VERSION venv (uv) + locked dependencies"
command -v uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
[[ -x .venv/bin/python ]] || uv venv --python "$PY_VERSION" .venv
uv pip install --python .venv/bin/python -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .

say "Ollama + models"
command -v ollama &>/dev/null || curl -fsSL https://ollama.com/install.sh | sh
install -D -m 644 deploy/ollama.override.conf /etc/systemd/system/ollama.service.d/override.conf
systemctl daemon-reload
systemctl enable --now ollama
systemctl restart ollama
for _ in {1..30}; do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done
ollama pull nomic-embed-text
ollama pull qwen2.5:7b  # ~4.7 GB, the default LLM (D-026)

say "Permissions"
mkdir -p data chroma_db
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
[[ -f .env ]] && chmod 600 .env

say "systemd unit"
install -m 644 deploy/moviemood.service /etc/systemd/system/moviemood.service
systemctl daemon-reload
systemctl enable moviemood

say "nginx site for $DOMAIN"
install -m 644 deploy/nginx-proxy-snippet.conf /etc/nginx/snippets/moviemood-proxy.conf
sed "s/DOMAIN/$DOMAIN/g" deploy/nginx-moviemood.conf > /etc/nginx/sites-available/moviemood
ln -sf /etc/nginx/sites-available/moviemood /etc/nginx/sites-enabled/moviemood
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
# Re-running rewrote the site file above without certbot's HTTPS block, so put it back.
if [[ -d /etc/letsencrypt/live/$DOMAIN ]]; then
    certbot install --nginx --cert-name "$DOMAIN" --redirect --non-interactive
    systemctl reload nginx
fi

say "Firewall (ufw): SSH + HTTP/HTTPS only"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

say "Status"
missing=()
[[ -f .env ]] || missing+=(".env (scp it from your laptop)")
[[ -f data/movies.json ]] || missing+=("data/movies.json (rsync data/ from your laptop)")
if (( ${#missing[@]} )); then
    echo "Not starting the app yet. Missing:"
    printf '  - %s\n' "${missing[@]}"
    echo "Then: see deploy/README.md steps 4-6 (build index, start, certbot)."
else
    echo "Ready. Next (deploy/README.md):"
    echo "  sudo -u $APP_USER .venv/bin/python scripts/build_index.py"
    echo "  sudo systemctl restart moviemood && curl -s localhost:8000/health"
    echo "  sudo certbot --nginx -d $DOMAIN"
fi
