# Deploying Compass

For a Bob-agent hackathon there is one right answer and a couple of fallbacks.

**TL;DR — the right answer:**

1. Run Compass locally.
2. Expose it via a **Cloudflare Quick Tunnel** (free, zero signup).
3. Paste the tunnel's `https://…trycloudflare.com` URL into your lablab submission.

This gives judges a **fully working link** — real Onboard, real Ask, real Bob — as long as your machine is online during judging. It's the correct architecture for any agent that shells out to a locally-authenticated CLI like `bob`.

Streamlit Community Cloud and Hugging Face Spaces are useful *fallbacks* for after the judging window (or as a static "look at the UI" backup), but they cannot run real onboardings because the `bob` binary is not deployable to their runtimes.

---

## 1. The one-command launcher (recommended)

Prerequisites (one-time):

```powershell
# a) The Bob Shell CLI — check it's on PATH
bob --version                       # if this fails, install Bob Shell first

# b) Cloudflare's tunnel client
winget install --id Cloudflare.cloudflared

# c) Compass itself
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[ui]
Copy-Item .env.example .env         # then edit .env to add BOB_API_KEY
```

Then, every time you want a live demo link:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\serve_public.ps1
```

That script:

1. Launches Streamlit on `http://localhost:8501` in the background.
2. Starts a Cloudflare Quick Tunnel pointing at it.
3. Prints a URL like `https://random-name-here.trycloudflare.com`.

Paste that URL into your lablab submission. Judges click, land on Compass, upload a ZIP, hit Run — the full four-subagent flow runs on your machine using your Bob key. Close the terminal to stop the tunnel.

**Cloudflare's Quick Tunnel is free, has no signup, no rate limits worth worrying about at hackathon traffic, and gives you a fresh URL every time.** For a stable URL that survives restarts, you can also authenticate cloudflared and create a named tunnel — see the [Cloudflare docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

**ngrok** is a fine alternative if you already have it configured: `ngrok http 8501`.

## 2. Streamlit Community Cloud (fallback, UI-only)

Best when you want a link that survives your laptop being off, but you accept that it can only show the Demo tab. Good for a lablab README as "here's the UI, video below for the real thing."

1. Push this repo to GitHub.
2. <https://share.streamlit.io/> → **New app** → point at `streamlit_app.py`.
3. Advanced settings → Secrets:
   ```toml
   BOB_API_KEY = ""      # blank on purpose — no bob binary on this host
   COMPASS_API_TOKEN = "any random string"
   ```
4. Deploy. `packages.txt` (git) and `pyproject.toml` are picked up automatically.

Deployed link supports: Demo tab, UI navigation, ZIP upload preview, health check (which will show `bob_on_path: false`).
Deployed link does NOT support: real Onboard, real Ask.

## 3. Hugging Face Spaces (Docker fallback)

Same UI, deployed with a Dockerfile that could — *in theory* — include Bob. Only useful if Bob Shell is available on the npm registry or via a URL your Dockerfile can `curl`.

Skeleton `Dockerfile`:

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y git nodejs npm && rm -rf /var/lib/apt/lists/*
# Only works if bobshell is publicly installable — replace with the real install command.
RUN npm install -g bobshell || echo "bobshell not on npm; real Onboard will fail"

WORKDIR /app
COPY pyproject.toml README.md packages.txt ./
COPY src ./src
COPY streamlit_app.py .streamlit ./
RUN pip install --no-cache-dir -e .[ui]

EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Push, set secrets in the Space's settings, done.

## 4. The FastAPI HTTP surface (`compass serve`) for Orchestrate

The Streamlit UI and the FastAPI service are independent. Watsonx Orchestrate calls the FastAPI service (`POST /onboard`, `POST /ask`) — deploy it wherever `bob` is reachable:

```powershell
compass serve --pack .\onboarding --host 0.0.0.0 --port 8080
```

Then front it with a tunnel too, or run it on the same VM as Bob Shell.

---

## What to actually submit to lablab

1. **Live URL** — the `trycloudflare.com` link from `serve_public.ps1`, running on your machine during the judging window.
2. **Backup UI-only URL** — the `streamlit.app` link from Streamlit Community Cloud, so judges after the window still see the artifact.
3. **Loom / mp4 recording** — you running Compass locally against a real repo, showing the four subagents and the six MD files landing. Insurance if both links go down.
4. **GitHub repo** — README, ARCHITECTURE, SPEC, MIT, and this DEPLOY.md.
5. **Orchestrate note** — screenshots + the `orchestrate/*.yaml` files.

The tunnel URL is the star of the submission. Everything else is scaffolding around it.
