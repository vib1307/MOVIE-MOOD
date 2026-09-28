# Deploy runbook: Hetzner CPX22 + OpenAI LLM + DuckDNS + Let's Encrypt

Why this setup: D-028, D-032 and D-033 in `docs/DECISIONS.md`. The target is one **Hetzner CPX22** (2 vCPU AMD / 4 GB / 80 GB, ~$23/month). It runs:
- Ollama with **only `nomic-embed-text`** (embeddings, ~0.5 GB)
- uvicorn (FastAPI + Gradio)
- nginx with HTTPS

The LLM step (judge + "why", lazy-ingest intent) goes to **OpenAI** (`LLM_PROVIDER=openai`).

```
internet ──443──> nginx ──> 127.0.0.1:8000 uvicorn (FastAPI + Gradio) ──> OpenAI (LLM)
                                   └──> 127.0.0.1:11434 Ollama, nomic only (never public)
```

The same files work on Oracle Always Free (see "Other hosts" at the end).

Files in this folder:

| File | Goes to |
|---|---|
| `setup.sh` | run once (and again after changes) with sudo from `/opt/moviemood` |
| `traffic.sh` | traffic numbers from the nginx logs (run on the server) |
| `moviemood.service` | `/etc/systemd/system/moviemood.service` |
| `ollama.override.conf` | `/etc/systemd/system/ollama.service.d/override.conf` |
| `nginx-moviemood.conf` | `/etc/nginx/sites-available/moviemood` (DOMAIN filled in) |
| `nginx-proxy-snippet.conf` | `/etc/nginx/snippets/moviemood-proxy.conf` |

## 1. Hetzner server + OpenAI key + DuckDNS (your laptop, browser)

1. **OpenAI key and cost guard.** At <https://platform.openai.com>:
   - Create an API key (Dashboard → API keys). Keep it for step 4.
   - Set a **monthly budget** with email alerts (Settings → Limits). A few dollars is plenty. nginx also limits searches per IP.
2. **Create the server.** <https://console.hetzner.cloud> → your project → **Add Server**:
   - Location: any where **CPX22** is offered (e.g. Falkenstein, Nuremberg, Helsinki).
   - Image: **Ubuntu 24.04**.
   - Type: **Shared vCPU → x86 (AMD) → CPX22** (2 vCPU / 4 GB / 80 GB).
   - Networking: **Public IPv4** ✅ and IPv6 ✅.
   - SSH keys: **Add SSH key** → paste the output of `pbcopy < ~/.ssh/id_ed25519.pub`.
   - Firewalls: **Create Firewall** → Inbound rules **TCP 22**, **TCP 80**, **TCP 443** (Any IPv4 + IPv6) → apply it to this server.
   - Name: `moviemood` → **Create & Buy now**.
3. Note the server's **IPv4 address**.
4. **DuckDNS**: log in at <https://www.duckdns.org> and create a subdomain, e.g. `moviemood`. Set its IP to the server IP and save.
   - Check it resolves: `dig +short moviemood.duckdns.org` should print the IP.

Hetzner's login user is **`root`** (`ssh root@<IP>`). The `sudo` in the commands below is harmless as root.

## 2. Get the code onto the server
```bash
ssh root@<IP>
sudo git clone https://github.com/vib1307/MOVIE-MOOD.git /opt/moviemood
sudo git config --global --add safe.directory /opt/moviemood   # setup.sh chowns the repo to the app user
```
If the repo is **private**, use a read-only deploy key:
- On the server, run `sudo ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N ""`.
- Add `/root/.ssh/id_ed25519.pub` in GitHub → repo → Settings → Deploy keys (read-only).
- Then clone with `sudo git clone git@github.com:vib1307/MOVIE-MOOD.git /opt/moviemood`.

## 3. Setup (about 5 min)
```bash
cd /opt/moviemood && sudo bash deploy/setup.sh moviemood.duckdns.org
```
This installs and configures:
- a 4 GB swap file
- nginx, certbot and goaccess
- the firewall: ufw with SSH + HTTP/HTTPS (on Oracle it opens 80/443 in the image's iptables instead)
- the `moviemood` user
- Python 3.11 with the **exact** dev versions (`requirements.lock`)
- Ollama, plus `nomic-embed-text` (a local LLM only with `PULL_LLM=...`, see `setup.sh`)
- the systemd units and the nginx site

The app is not started yet.

## 4. Secrets and data (from your laptop, in the project folder)
The server's `.env` is your laptop's `.env` plus the LLM provider. Add these two lines to a **copy**, so your laptop keeps using local Ollama:
```bash
cp .env /tmp/server.env
printf 'LLM_PROVIDER=openai\nOPENAI_API_KEY=sk-...your key...\n' >> /tmp/server.env
# optional: OPENAI_MODEL=gpt-4.1-mini (the default in config.py)
scp /tmp/server.env root@<IP>:/opt/moviemood/.env && rm /tmp/server.env
rsync -avz data/movies.json data/added_ids.json data/cache root@<IP>:/opt/moviemood/data/
ssh root@<IP> 'chown -R moviemood:moviemood /opt/moviemood && chmod 600 /opt/moviemood/.env'
```
- The app refuses to start with `LLM_PROVIDER=openai` and no key (clear error in `journalctl -u moviemood`).
- `data/cache/` (33 MB) holds the raw TMDB/OMDb responses, so the server never re-spends API quota on them.
- **Only do this once.** After go-live, the server's `movies.json` grows on its own through lazy ingestion. Copying the laptop's file over it again would drop those films.

## 5. Build the index and start
```bash
cd /opt/moviemood
sudo -u moviemood .venv/bin/python scripts/build_index.py   # a few minutes on 2 vCPUs
sudo systemctl restart moviemood
curl -s localhost:8000/health    # {"status":"ok","ollama":true,"movies_indexed":505}
```

## 6. HTTPS
```bash
sudo certbot --nginx -d moviemood.duckdns.org   # pick "redirect HTTP to HTTPS"
```
Renewal is automatic (`systemctl list-timers | grep certbot`).

## 7. Verify
- `https://moviemood.duckdns.org/health` → 200. Also open `/docs` and `/`.
- Try "dark and scary", "Brad Pitt" and "action movies with more 8.5 above imdb", and open a "Why this pick" panel.
- Time step 2 ("Refining with AI…") on 3 queries and write the numbers in D-033.
- Rate limit: `for i in $(seq 10); do curl -s -o /dev/null -w "%{http_code}\n" -X POST https://moviemood.duckdns.org/api/v1/recommend -H 'Content-Type: application/json' -d '{"query":"cozy and light","rerank":false}'; done` should print some 429s.
- `sudo reboot`, then `/health` is green again within ~1–2 min.
- Hetzner console → server → **Snapshots → Take snapshot** (restore point; costs a few cents a month). Or enable **Backups** (+20% of the server price).

## Day-2
| Task | Command |
|---|---|
| Deploy new code | `cd /opt/moviemood && sudo git pull && sudo bash deploy/setup.sh moviemood.duckdns.org && sudo systemctl restart moviemood` (setup.sh re-syncs deps, units and nginx; models already pulled are skipped fast) |
| Traffic per day | `sudo bash deploy/traffic.sh` (last 7 days) or `... traffic.sh 30` |
| Traffic dashboard | `sudo bash deploy/traffic.sh --report`, then on the laptop `scp root@<IP>:/tmp/moviemood-report.html . && open moviemood-report.html` |
| App logs | `journalctl -u moviemood -f` |
| Ollama logs | `journalctl -u ollama -f` |
| nginx logs | `sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log` |
| Restart | `sudo systemctl restart moviemood` |
| Rebuild index (after a blob change) | `sudo -u moviemood .venv/bin/python scripts/build_index.py && sudo systemctl restart moviemood` |

## LLM provider
- Server: `LLM_PROVIDER=openai` in `/opt/moviemood/.env`. Change the model with `OPENAI_MODEL=...`, then `sudo systemctl restart moviemood`.
- Back to a local LLM (only on a box with ~8 GB+ RAM): remove `LLM_PROVIDER`, run `ollama pull qwen2.5:7b`, and restart.
- If OpenAI fails (key, quota, outage), searches still work: step 2 falls back to plain retrieval order, and lazy ingest is skipped. Check `journalctl -u moviemood | grep "LLM judge failed"`.

## Cost
- Hetzner CPX22: ~$23/month (the $25 paid at account verification should cover the first month; check Console → Billing).
- OpenAI: a small model judging 20 films per search costs a fraction of a cent per search. The OpenAI budget from step 1 caps it.

## Other hosts
**Oracle Always Free** (A1 ARM, 2 OCPU / 12 GB, ₹0), if capacity is ever available in your home region:
- The login user is `ubuntu`, not root, so copy files to `~` first and then `sudo mv` them into `/opt/moviemood`.
- Open 80/443 in the VCN security list. `setup.sh` handles the image's own iptables by itself.
- 12 GB fits qwen2.5:7b locally (`PULL_LLM=qwen2.5:7b`, no `LLM_PROVIDER`).
- Oracle may reclaim an Always Free VM that is idle for 7 days (CPU, network **and** memory all under 20%).
