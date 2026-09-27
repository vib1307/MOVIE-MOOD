# Deploy runbook: Hetzner CAX31 + DuckDNS + Let's Encrypt

Why this setup: D-028 in `docs/DECISIONS.md`. The target is one ARM VM (8 vCPU / 16 GB, ~€13/month) that runs Ollama (`nomic-embed-text` + `qwen2.5:7b`), uvicorn (FastAPI + Gradio), and nginx with HTTPS.

```
internet ──443──> nginx ──> 127.0.0.1:8000 uvicorn (FastAPI + Gradio)
                                   └──> 127.0.0.1:11434 Ollama (never public)
```

Files in this folder:

| File | Goes to |
|---|---|
| `setup.sh` | run once (and again after changes) as root from `/opt/moviemood` |
| `traffic.sh` | traffic numbers from the nginx logs (run on the server) |
| `moviemood.service` | `/etc/systemd/system/moviemood.service` |
| `ollama.override.conf` | `/etc/systemd/system/ollama.service.d/override.conf` |
| `nginx-moviemood.conf` | `/etc/nginx/sites-available/moviemood` (DOMAIN filled in) |
| `nginx-proxy-snippet.conf` | `/etc/nginx/snippets/moviemood-proxy.conf` |

## 1. Accounts (your laptop, browser)
1. **SSH key**: if `ls ~/.ssh/id_ed25519.pub` finds nothing, run `ssh-keygen -t ed25519`.
2. **Hetzner**: sign up at <https://www.hetzner.com/cloud>. New accounts may be asked for ID verification. Then:
   - Create a project, then **Add Server**:
     - Location: Falkenstein, Nuremberg, or Helsinki (the Arm64 servers are EU only).
     - Image: **Ubuntu 24.04**.
     - Type: **Arm64 (Ampere) → CAX31** (8 vCPU / 16 GB).
     - Networking: Public IPv4 on.
     - SSH key: paste `~/.ssh/id_ed25519.pub`.
     - Name: `moviemood`.
   - **Firewalls → Create firewall**: allow inbound TCP 22, 80 and 443, and apply it to the server.
   - Note the server's **IPv4**.
3. **DuckDNS**: log in at <https://www.duckdns.org> and create a subdomain, e.g. `moviemood`. Set its IP to the server's IPv4 and save.
   - Check it resolves: `dig +short moviemood.duckdns.org` should print the IP.

## 2. Get the code onto the server
```bash
ssh root@<IP>
git clone https://github.com/vib1307/MOVIE-MOOD.git /opt/moviemood
git config --global --add safe.directory /opt/moviemood   # setup.sh chowns the repo to the app user
```
If the repo is **private**, use a read-only deploy key:
- On the server, run `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""`.
- Add `~/.ssh/id_ed25519.pub` in GitHub → repo → Settings → Deploy keys (read-only).
- Then clone with `git clone git@github.com:vib1307/MOVIE-MOOD.git /opt/moviemood`.

## 3. Setup (about 10 min; most of it is the 4.7 GB model download)
```bash
cd /opt/moviemood && sudo bash deploy/setup.sh moviemood.duckdns.org
```
This installs:
- nginx, certbot and ufw
- the `moviemood` user
- Python 3.11 with the **exact** dev versions (`requirements.lock`)
- Ollama, plus both models
- the systemd units and the nginx site

The app is not started yet.

## 4. Secrets and data (from your laptop, in the project folder)
```bash
scp .env root@<IP>:/opt/moviemood/.env
rsync -avz data/movies.json data/added_ids.json root@<IP>:/opt/moviemood/data/
rsync -avz data/cache/ root@<IP>:/opt/moviemood/data/cache/
ssh root@<IP> 'chown -R moviemood:moviemood /opt/moviemood && chmod 600 /opt/moviemood/.env'
```
- `data/cache/` (33 MB) holds the raw TMDB/OMDb responses, so the server never re-spends API quota on them.
- **Only do this once.** After go-live, the server's `movies.json` grows on its own through lazy ingestion. Copying the laptop's file over it again would drop those films.

## 5. Build the index and start
```bash
cd /opt/moviemood
sudo -u moviemood .venv/bin/python scripts/build_index.py   # a few minutes on CPU
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
- Time step 2 ("Refining with AI…") on 3 queries and write the numbers in D-028. If it's over 60s, see "Speed" below.
- Rate limit: `for i in $(seq 10); do curl -s -o /dev/null -w "%{http_code}\n" -X POST https://moviemood.duckdns.org/api/v1/recommend -H 'Content-Type: application/json' -d '{"query":"cozy and light","rerank":false}'; done` should print some 429s.
- `sudo reboot`, then `/health` is green again within ~1 min.
- Hetzner console → Server → **Snapshots → Create** (restore point, ~€0.01/GB/month).

## Day-2
| Task | Command |
|---|---|
| Deploy new code | `cd /opt/moviemood && git pull && sudo bash deploy/setup.sh moviemood.duckdns.org && sudo systemctl restart moviemood` (setup.sh re-syncs deps, units and nginx; models already pulled are skipped fast) |
| Traffic per day | `sudo bash deploy/traffic.sh` (last 7 days) or `... traffic.sh 30` |
| Traffic dashboard | `sudo bash deploy/traffic.sh --report`, then on the laptop `scp root@<IP>:/tmp/moviemood-report.html . && open moviemood-report.html` |
| App logs | `journalctl -u moviemood -f` |
| Ollama logs | `journalctl -u ollama -f` |
| nginx logs | `tail -f /var/log/nginx/access.log /var/log/nginx/error.log` |
| Restart | `sudo systemctl restart moviemood` |
| Rebuild index (after a blob change) | `sudo -u moviemood .venv/bin/python scripts/build_index.py && sudo systemctl restart moviemood` |

## Speed
Everything runs on the CPU, and step 1 (retrieval) stays instant. If step 2 is too slow:
- Add `LLM_MODEL=llama3.2` to `.env`, run `ollama pull llama3.2`, then `systemctl restart moviemood`. It's ~2x faster but less accurate (D-026).
- Or plan a Groq free-tier provider (needs code).

## Stopping the bill
Hetzner bills hourly while the server exists, even when it's powered off. **Delete** the server (keep a snapshot if you want) to stop paying.
