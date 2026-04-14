# VoxRad Web Server — Deployment Guide

Deploy VoxRad as a single-container web service — on Fly.io (recommended, always-on free tier) or on any Linux host using Docker Compose.

---

## Option A — Fly.io (recommended, always-on free tier)

Fly.io keeps your container running permanently — no cold-start delays. The free allowance
(one shared-cpu-1x VM + 3 GB storage) covers a single VoxRad instance indefinitely.

### 1. Install flyctl

```bash
curl -L https://fly.io/install.sh | sh   # Linux / macOS
# or: brew install flyctl               # macOS with Homebrew
flyctl auth login
```

### 2. Create the app and volumes

```bash
# Pick a unique app name (e.g. voxrad-yourname) and your nearest region:
# Regions: https://fly.io/docs/reference/regions/  (e.g. syd, lax, iad, lhr, sin)
flyctl apps create voxrad-yourname

# Persistent storage — created once, survives redeploys
flyctl volumes create voxrad_config --size 1 --region syd
flyctl volumes create voxrad_data   --size 1 --region syd
```

Update the `app` and `primary_region` fields in `fly.toml` to match.

### 3. Set secrets

```bash
flyctl secrets set \
  VOXRAD_WEB_PASSWORD=changeme \
  VOXRAD_TRANSCRIPTION_API_KEY=gsk_... \
  VOXRAD_TEXT_API_KEY=sk-...

# Optional — streaming STT (Deepgram / AssemblyAI):
flyctl secrets set DEEPGRAM_API_KEY=...
flyctl secrets set VOXRAD_STREAMING_STT_PROVIDER=deepgram
```

### 4. Deploy

```bash
flyctl deploy
# App URL is printed at the end, e.g. https://voxrad-yourname.fly.dev
```

### Updating

```bash
git pull
flyctl deploy
```

### Logs & status

```bash
flyctl logs          # tail live logs
flyctl status        # machine health
flyctl ssh console   # SSH into the container
```

---

## Option B — On-Premises (Docker Compose)

### Prerequisites

| Requirement | Notes |
|---|---|
| Docker ≥ 24 + Compose v2 | `docker compose version` |
| A domain name (optional) | Required for HTTPS |
| TLS certificates (optional) | Let's Encrypt / your CA |
| Transcription API key | OpenAI Whisper or compatible (e.g. local faster-whisper) |
| Text model API key | OpenAI or compatible (e.g. local Ollama) |

---

### Quick start (localhost only)

```bash
# 1. Clone the repo
git clone https://github.com/markbekhit/voxrad.git
cd voxrad

# 2. Create your .env file
cp .env.example .env
$EDITOR .env          # set VOXRAD_WEB_PASSWORD and your API keys

# 3. Build and start
docker compose up -d

# 4. Open http://localhost:8765 in your browser
```

The first `docker compose up` builds the image (~5 min). Subsequent starts are instant.

---

### Configuration

All configuration is done via environment variables in `.env`.

### Required

| Variable | Description |
|---|---|
| `VOXRAD_WEB_PASSWORD` | UI login password. **Change before any network-accessible deployment.** |
| `VOXRAD_TRANSCRIPTION_API_KEY` | Whisper-compatible transcription API key |
| `VOXRAD_TEXT_API_KEY` | LLM API key for report formatting |

### Optional

| Variable | Default | Description |
|---|---|---|
| `VOXRAD_PORT` | `8765` | Host port to bind |
| `VOXRAD_WORKING_DIR` | `/data/working` | Path inside container for templates, guidelines, reports |
| `VOXRAD_MM_API_KEY` | _(empty)_ | Gemini API key (only if multimodal mode is used) |

### Using local models

Point the API URLs at your local inference servers via settings.ini in the `voxrad-config` volume,
or configure them in the web UI's Settings tab on first use:

```
VOXRAD_TRANSCRIPTION_BASE_URL=http://host.docker.internal:8000/v1
VOXRAD_TEXT_BASE_URL=http://host.docker.internal:11434/v1
```

See [local-whisper-setup.md](local-whisper-setup.md) for running a local Whisper server.

---

### Volumes

| Volume | Mount point in container | Contents |
|---|---|---|
| `voxrad-config` | `/root/.voxrad` | `settings.ini`, encrypted API key files |
| `voxrad-data` | `/data/working` | `templates/`, `guidelines/`, `reports/` |

### Adding templates and guidelines

Copy files into the data volume while the container is running:

```bash
docker compose cp templates/. voxrad:/data/working/templates/
docker compose cp guidelines/. voxrad:/data/working/guidelines/
```

Or place them in a local directory and use a bind mount instead of the named volume:

```yaml
# docker-compose.yml override
volumes:
  - ./my-templates:/data/working/templates:ro
  - ./my-guidelines:/data/working/guidelines:ro
```

---

### HTTPS with nginx (recommended for production)

HTTP Basic Auth sends credentials in cleartext. Always run behind HTTPS for any deployment accessible outside localhost.

### 1. Obtain TLS certificates

```bash
# Using certbot (Let's Encrypt):
certbot certonly --standalone -d your.domain.com

# Copy into the deploy/certs directory:
mkdir -p deploy/certs
cp /etc/letsencrypt/live/your.domain.com/fullchain.pem deploy/certs/
cp /etc/letsencrypt/live/your.domain.com/privkey.pem   deploy/certs/
chmod 600 deploy/certs/privkey.pem
```

### 2. Update nginx.conf

Edit `deploy/nginx.conf` and replace `server_name _;` with your domain:

```nginx
server_name your.domain.com;
```

### 3. Start with the nginx profile

```bash
docker compose --profile nginx up -d
```

This starts both `voxrad` (on internal port 8765) and `nginx` (on ports 80/443).
Nginx proxies HTTPS → VoxRad and redirects HTTP → HTTPS automatically.

---

### Advanced: encrypted API keys

For higher security, use the encrypted-key workflow instead of plaintext env vars:

1. Run the desktop app on any machine and save/encrypt your API keys in Settings.
2. Copy the encrypted files to the server:
   ```bash
   # On the desktop machine, keys are at ~/.voxrad/
   scp ~/.voxrad/*.encrypted ~/.voxrad/.asr_salt ~/.voxrad/.text_salt \
       user@server:/var/lib/docker/volumes/voxrad_voxrad-config/_data/
   ```
3. In `.env`, set the passwords instead of the raw API keys:
   ```
   VOXRAD_TRANSCRIPTION_PASSWORD=yourpassword
   VOXRAD_TEXT_PASSWORD=yourpassword
   VOXRAD_TRANSCRIPTION_API_KEY=   # leave blank
   VOXRAD_TEXT_API_KEY=            # leave blank
   ```

Encrypted keys always take precedence over plaintext env vars.

---

### Updating

```bash
git pull
docker compose build --no-cache
docker compose up -d
```

---

### Troubleshooting

| Symptom | Fix |
|---|---|
| `503 Transcription API key not loaded` | Set `VOXRAD_TRANSCRIPTION_API_KEY` in `.env` and restart |
| `503 Text model API key not loaded` | Set `VOXRAD_TEXT_API_KEY` in `.env` and restart |
| `401 Incorrect password` | Check `VOXRAD_WEB_PASSWORD` in `.env` |
| Templates not showing | Copy templates into the `voxrad-data` volume (see above) |
| Container exits immediately | Run `docker compose logs voxrad` to see the error |
| Port already in use | Change `VOXRAD_PORT` in `.env` |
