#!/usr/bin/env bash
# One-time (and re-runnable) server setup for MovieMood on Ubuntu 24.04 or 26.04 (x86 or arm64).
# Target: Hetzner CX23 (2 vCPU / 4 GB, D-035) with the LLM on OpenAI; also works on Oracle.
# See deploy/README.md, D-028, D-032 and D-033.
#
# Usage, as root, from the cloned repo:
#   cd /opt/moviemood && sudo bash deploy/setup.sh moviemood.duckdns.org
# With the LLM on this box too (needs ~8 GB RAM, not a 4 GB server):
#   sudo PULL_LLM=qwen2.5:7b bash deploy/setup.sh moviemood.duckdns.org
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

say "Swap (4 GB safety net: nomic + app use ~1.5 GB; a local LLM needs far more)"
if ! swapon --show | grep -q /swapfile; then
    [[ -f /swapfile ]] || { fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile; }
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "System packages"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q nginx certbot python3-certbot-nginx git curl rsync ufw goaccess  # goaccess: deploy/traffic.sh --report
# Not netfilter-persistent here: on Ubuntu 26.04 ufw "Breaks" it, so apt refuses both (D-035).
# Only the Oracle branch below needs it, and Oracle's image already ships it.

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
ollama pull nomic-embed-text  # embeddings always run here (D-033)
# The LLM is OpenAI by default on the server (LLM_PROVIDER=openai in .env). Only pull a
# local one when asked, e.g. PULL_LLM=qwen2.5:7b (~4.7 GB, D-026).
if [[ -n "${PULL_LLM:-}" ]]; then ollama pull "$PULL_LLM"; fi

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

# Oracle's Ubuntu images ship their own iptables rules (only SSH open, then a REJECT)
# saved by netfilter-persistent; ufw on top of them is known to break things. So on
# Oracle, open 80/443 in those rules instead. Elsewhere (e.g. Hetzner) use ufw.
if [[ -f /etc/iptables/rules.v4 ]] && iptables -S INPUT | grep -q -- "-j REJECT"; then
    say "Firewall (Oracle iptables): allow HTTP/HTTPS"
    for port in 80 443; do
        if ! iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
            # insert just above the first REJECT, wherever it is
            reject=$(iptables -L INPUT --line-numbers | awk '$2 == "REJECT" {print $1; exit}')
            iptables -I INPUT "$reject" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
        fi
    done
    command -v netfilter-persistent &>/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q netfilter-persistent
    netfilter-persistent save
    echo "Also open 80/443 in the VCN security list (Oracle console), see deploy/README.md."
else
    say "Firewall (ufw): SSH + HTTP/HTTPS only"
    ufw allow OpenSSH
    ufw allow 'Nginx Full'
    ufw --force enable
fi

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
