# Deploy runbook: Oracle Cloud Always Free + DuckDNS + Let's Encrypt

Why this setup: D-028 and D-032 in `docs/DECISIONS.md`. The target is one **Always Free** ARM VM (VM.Standard.A1.Flex, **2 OCPU / 12 GB**, ₹0) that runs Ollama (`nomic-embed-text` + `qwen2.5:7b`), uvicorn (FastAPI + Gradio), and nginx with HTTPS. The same files work on any Ubuntu 24.04 VM (e.g. Hetzner): `setup.sh` picks the firewall method by itself.

```
internet ──443──> nginx ──> 127.0.0.1:8000 uvicorn (FastAPI + Gradio)
                                   └──> 127.0.0.1:11434 Ollama (never public)
```

Files in this folder:

| File | Goes to |
|---|---|
| `setup.sh` | run once (and again after changes) with sudo from `/opt/moviemood` |
| `traffic.sh` | traffic numbers from the nginx logs (run on the server) |
| `moviemood.service` | `/etc/systemd/system/moviemood.service` |
| `ollama.override.conf` | `/etc/systemd/system/ollama.service.d/override.conf` |
| `nginx-moviemood.conf` | `/etc/nginx/sites-available/moviemood` (DOMAIN filled in) |
| `nginx-proxy-snippet.conf` | `/etc/nginx/snippets/moviemood-proxy.conf` |

## 1. Oracle account and VM (your laptop, browser)

1. **Sign up** at <https://signup.cloud.oracle.com>.
   - The card is only for identity verification. It shows a temporary hold, and nothing is charged unless you upgrade the account.
   - **Home region**: pick carefully, because it **can't be changed later** and Always Free VMs only run there. From India: **India West (Mumbai)** or **India South (Hyderabad)**.
2. **Budget alert** (safety net): Billing & Cost Management → Budgets → Create budget, **$1**, with an email alert.
3. **Create the VM**: Compute → Instances → **Create instance**.
   - Name: `moviemood`.
   - Image: **Canonical Ubuntu 24.04** (Change image → Ubuntu → 24.04, **aarch64**).
   - Shape: **Ampere → VM.Standard.A1.Flex**, **2 OCPUs**, **12 GB** memory. Look for the "Always Free-eligible" tag.
   - Networking: create a new VCN with a **public subnet**, and **Assign a public IPv4 address** ✅.
   - SSH keys: **Paste public key** → contents of `~/.ssh/id_ed25519.pub` (`pbcopy < ~/.ssh/id_ed25519.pub`).
   - Boot volume: **100 GB** (200 GB total is free).
   - **Create**.
   - If you get "Out of host capacity": no free ARM capacity right now. Try another Availability Domain (if the region has one) or retry later.
4. **Open 80/443 in the VCN**: Instance → Subnet → Security List (Default) → **Add Ingress Rules**, twice. Source `0.0.0.0/0`, TCP, destination port **80**, then the same for **443**. Port 22 is already there.
   - The VM's own iptables are opened by `setup.sh`.
5. Note the instance's **Public IP address**.
6. **DuckDNS**: log in at <https://www.duckdns.org> and create a subdomain, e.g. `moviemood`. Set its IP to the public IP and save.
   - Check it resolves: `dig +short moviemood.duckdns.org` should print the IP.

The login user on Oracle's Ubuntu image is **`ubuntu`** (not root). Use `sudo` for admin commands.

## 2. Get the code onto the server
```bash
ssh ubuntu@<IP>
sudo git clone https://github.com/vib1307/MOVIE-MOOD.git /opt/moviemood
sudo git config --global --add safe.directory /opt/moviemood   # setup.sh chowns the repo to the app user
```
If the repo is **private**, use a read-only deploy key:
- On the server, run `sudo ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N ""`.
- Add `/root/.ssh/id_ed25519.pub` in GitHub → repo → Settings → Deploy keys (read-only).
- Then clone with `sudo git clone git@github.com:vib1307/MOVIE-MOOD.git /opt/moviemood`.

## 3. Setup (about 15 min; most of it is the 4.7 GB model download)
```bash
cd /opt/moviemood && sudo bash deploy/setup.sh moviemood.duckdns.org
```
This installs and configures:
- a 4 GB swap file
- nginx, certbot and goaccess
- the firewall: on Oracle it opens 80/443 in the image's iptables (ufw isn't used there)
- the `moviemood` user
- Python 3.11 with the **exact** dev versions (`requirements.lock`)
- Ollama, plus both models
- the systemd units and the nginx site

The app is not started yet.

## 4. Secrets and data (from your laptop, in the project folder)
`ubuntu` can't write to `/opt/moviemood` directly, so copy to the home folder first:
```bash
scp .env ubuntu@<IP>:~/
rsync -avz data/movies.json data/added_ids.json data/cache ubuntu@<IP>:~/data/
ssh ubuntu@<IP> 'sudo mv ~/.env /opt/moviemood/.env && sudo rsync -a ~/data/ /opt/moviemood/data/ && rm -rf ~/data \
  && sudo chown -R moviemood:moviemood /opt/moviemood && sudo chmod 600 /opt/moviemood/.env'
```
- `data/cache/` (33 MB) holds the raw TMDB/OMDb responses, so the server never re-spends API quota on them.
- **Only do this once.** After go-live, the server's `movies.json` grows on its own through lazy ingestion. Copying the laptop's file over it again would drop those films.

## 5. Build the index and start
```bash
cd /opt/moviemood
sudo -u moviemood .venv/bin/python scripts/build_index.py   # several minutes on 2 OCPUs
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
- Time step 2 ("Refining with AI…") on 3 queries and write the numbers in D-032. If it's too slow, see "Speed" below.
- Rate limit: `for i in $(seq 10); do curl -s -o /dev/null -w "%{http_code}\n" -X POST https://moviemood.duckdns.org/api/v1/recommend -H 'Content-Type: application/json' -d '{"query":"cozy and light","rerank":false}'; done` should print some 429s.
- `sudo reboot`, then `/health` is green again within ~1–2 min.
- Oracle console → Boot volume → **Create backup** (restore point; Always Free includes a few volume backups).

## Day-2
| Task | Command |
|---|---|
| Deploy new code | `cd /opt/moviemood && sudo git pull && sudo bash deploy/setup.sh moviemood.duckdns.org && sudo systemctl restart moviemood` (setup.sh re-syncs deps, units and nginx; models already pulled are skipped fast) |
| Traffic per day | `sudo bash deploy/traffic.sh` (last 7 days) or `... traffic.sh 30` |
| Traffic dashboard | `sudo bash deploy/traffic.sh --report`, then on the laptop `scp ubuntu@<IP>:/tmp/moviemood-report.html . && open moviemood-report.html` |
| App logs | `journalctl -u moviemood -f` |
| Ollama logs | `journalctl -u ollama -f` |
| nginx logs | `sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log` |
| Restart | `sudo systemctl restart moviemood` |
| Rebuild index (after a blob change) | `sudo -u moviemood .venv/bin/python scripts/build_index.py && sudo systemctl restart moviemood` |

## Speed
Everything runs on 2 ARM cores, so step 1 (retrieval) stays instant and step 2 is slow. If step 2 is too slow:
- Add `LLM_MODEL=llama3.2` to `.env`, run `ollama pull llama3.2`, then `sudo systemctl restart moviemood`. It's ~2x faster but less accurate (D-026).
- Or plan a hosted LLM (Groq free tier / Bedrock; needs code).

## Oracle "idle" reclamation
Oracle may reclaim an Always Free VM that is idle for 7 days, meaning the 95th-percentile CPU, network **and** memory are all under 20%. qwen2.5:7b stays loaded (`OLLAMA_KEEP_ALIVE=24h`, ~6 of 12 GB), so memory stays above 20% and the VM shouldn't count as idle. Keep the boot volume backup anyway.

## Cost
₹0 on Always Free. The budget alert from step 1 emails you if anything ever starts costing money.
