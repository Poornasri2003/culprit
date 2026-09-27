# Deploying Compass

**Official submission link: Streamlit Community Cloud.** This document explains what works there, what doesn't, why, and how to show the rest.

## The constraint, confirmed

Compass's real onboarding runs shell out to the `bob` CLI. We tested directly whether this could be auto-installed on any cloud host (the obvious hope: `npm install -g bobshell` inside a container) and confirmed it cannot:

- `bobshell` returns a real `404 Not Found` from the public npm registry — it is not published there.
- The actual `bob` CLI is bundled by **IBM's official "IBM Bob" installer** (a full desktop application), not distributed as a fetchable package.

So there is currently no way to make a from-scratch cloud container run real Bob onboardings without first running IBM's official installer on that machine — which rules out Streamlit Community Cloud, Hugging Face Spaces, and any serverless platform for the *real* Onboard flow.

This is a distribution constraint, not a bug in Compass. `src/compass/infrastructure/bob_bootstrap.py` documents the investigation in detail and fails with a clear, honest error (rather than a confusing 404) if you ever try.

## What Streamlit Community Cloud DOES give you

Everything except the real Bob run:

- ✅ **Accounts** — sign up / sign in (bcrypt-hashed passwords in MongoDB Atlas)
- ✅ **Demo tab** — a full six-file onboarding pack from a canned fixture, no Bob call
- ✅ **History tab** — past runs, Bobcoin costs, per-subagent breakdown (once you've run something, anywhere)
- ✅ **Download buttons, Mermaid rendering, architecture PNG export** — all client-independent
- ⚠️ **Real Onboard** — shows a clear warning banner up front, and a clean error if you click Run anyway. This is intentional, not a bug: the app tells you exactly why, rather than hanging or crashing.

## 1. Deploy to Streamlit Community Cloud

1. Push this repo to GitHub (already done: `github.com/Poornasri2003/culprit`, or rename it to `compass` if you prefer — the redirect is automatic).
2. Go to <https://share.streamlit.io/> and sign in with GitHub.
3. Click **Create app** → **"Yup, I have an app"** (deploy from existing repo).
4. Fill in:
   - Repository: `Poornasri2003/culprit`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
5. Click **"Advanced settings…"** → **Secrets**, and paste:
   ```toml
   MONGO_URI = "mongodb+srv://<user>:<url-encoded-password>@<cluster>.mongodb.net/?appName=Cluster0"
   MONGO_DB = "compass"

   # Optional — only if you set up Google sign-in (see .env.example for the GCP steps)
   # GOOGLE_CLIENT_ID = "..."
   # GOOGLE_CLIENT_SECRET = "..."
   # GOOGLE_REDIRECT_URI = "https://<your-app>.streamlit.app"

   # Deliberately omitted: BOB_API_KEY. There is no working `bob` CLI on this
   # host regardless of the key, so setting it here would be misleading.
   ```
   Remember to URL-encode any `@` in your Mongo password as `%40` (see `.env.example`).
6. Click **Deploy**. First build takes 2-3 minutes (installs from `requirements.txt` and `packages.txt`).

You'll get a permanent URL like `https://compass-<random>.streamlit.app`. It survives your laptop being off, redeploys automatically on every push to `main`, and is what you paste into the lablab submission form as the "deployment link."

## 2. Show the real Onboard flow — the video

Since the deployed link can't run real Bob, the second half of the submission is a **local recording**:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[ui]
Copy-Item .env.example .env      # fill in BOB_API_KEY, MONGO_URI
streamlit run streamlit_app.py
```

Record yourself onboarding a real repo end-to-end: the four subagents running live, the six Markdown files landing on disk, the Bobcoin cost tracking in the sidebar and History tab. This is standard practice for any hackathon agent that depends on a local CLI tool with its own licensing/distribution — judges expect it.

## 3. If you also want a live, always-on real-Onboard link

This needs a host where you've run IBM's official Bob installer yourself — the same one on your Windows machine. Two ways to get there:

**A. Keep it laptop-based, temporarily public.** Run `scripts/serve_public.ps1` during the specific hours judges are reviewing. It starts Streamlit and a Cloudflare Quick Tunnel and prints a fresh `https://…trycloudflare.com` URL each time. Zero cost, but the URL changes on every restart and your laptop must stay on, plugged in, and connected.

**B. A dedicated VPS with Bob's installer run on it.** More setup (needs the IBM Bob installer to support your VPS's OS — check whether IBM ships a Linux build), a small monthly cost, but survives independently of your laptop and gets you a stable IP/domain with no tunnel needed at all. Not yet set up in this repo; ask if you want to pursue it.

For most hackathon judging windows, **Option A during the window + the Streamlit Cloud link as your permanent submission + the video as backup** covers everything a judge needs to see, without the cost or setup time of a VPS.

## 4. The FastAPI HTTP surface (`compass serve`) for watsonx Orchestrate

Independent of the Streamlit UI. Deploy wherever Bob is actually installed:

```powershell
compass serve --pack .\onboarding --host 0.0.0.0 --port 8080
```

Same Bob-availability constraint applies — this is for the Orchestrate integration story, not for public traffic.

## Submission checklist

1. ✅ **Streamlit Community Cloud URL** — the permanent link, accounts + Demo + History all working
2. ✅ **GitHub repo** — README, ARCHITECTURE, SPEC, MIT license, this file
3. ⬜ **Local recording** — a real onboarding run, proving the four Bob subagents work end-to-end
4. ⬜ **Orchestrate note** — `orchestrate/*.yaml` + a screenshot, if you build that piece out
