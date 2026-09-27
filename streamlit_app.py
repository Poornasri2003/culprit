"""Compass — Streamlit UI with auth, cost tracking, and run history.

Point Compass at any Git repo, local folder, or .zip upload; watch four Bob
subagents build an onboarding pack; and see the Bobcoins each subagent spent
tracked per-run in MongoDB.

Run locally:
    streamlit run streamlit_app.py

Deploy: see DEPLOY.md.
"""
from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

load_dotenv()


def _load_streamlit_secrets_into_env() -> None:
    """Copy Streamlit Cloud's Secrets manager values into os.environ.

    Streamlit Cloud has no `.env` file — secrets are pasted into a "Secrets"
    text box in the app's settings and surface as `st.secrets`, not
    environment variables. Compass's config layer reads `os.environ`
    everywhere, so we bridge the two here, once, at import time. Local `.env`
    values (already loaded above) take precedence — this only fills in what's
    still missing.
    """
    try:
        secrets = st.secrets
    except Exception:
        return
    for key in ("BOB_API_KEY", "MONGO_URI", "MONGO_DB", "GOOGLE_CLIENT_ID",
                "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI", "COMPASS_API_TOKEN",
                "COMPASS_BOOTSTRAP_DIR"):
        try:
            value = secrets.get(key)
        except Exception:
            value = None
        if value and not os.environ.get(key):
            os.environ[key] = str(value)


_load_streamlit_secrets_into_env()

# ---------------------------------------------------------------------------
# Page + styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Compass — Onboarding for any repo",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": "Compass — IBM Bob 2.0 developer-onboarding agent."},
)

st.markdown(
    """
    <style>
      .compass-hero  { padding: 0.15rem 0 0.75rem 0; }
      .compass-hero h1 { margin: 0 0 0.15rem 0; font-size: 2.2rem; letter-spacing: -0.5px; }
      .compass-hero p  { color: #6b7280; margin: 0; font-size: 1.02rem; }
      .compass-badge   { display: inline-block; padding: 2px 8px; border-radius: 999px;
                         background: #eef2ff; color: #4f46e5; font-size: 0.75rem;
                         margin: 4px 4px 0 0; border: 1px solid #e0e7ff; }
      .stTabs [data-baseweb="tab-list"] { gap: 10px; }
      .stTabs [data-baseweb="tab"]      { font-weight: 500; }
      .compass-answer { line-height: 1.55; font-size: 1.02rem; }
      .muted { color: #6b7280; font-size: 0.85rem; }
      .cost-pill { display: inline-block; padding: 2px 10px; border-radius: 999px;
                   background: #ecfdf5; color: #065f46; font-weight: 600;
                   border: 1px solid #a7f3d0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Session-state defaults
# ---------------------------------------------------------------------------

_defaults = {
    "user": None,                 # dict {username, email, provider}
    "running": False,
    "pack_dir": None,             # str path to last pack
    "last_error": None,
    "last_pack_totals": None,     # dict {bobcoins, tokens, wallclock}
    "last_browse_msg": None,
    "local_path": "",
    "output_name": "onboarding_ui",
}
for k, v in _defaults.items():
    st.session_state.setdefault(k, v)

# ---------------------------------------------------------------------------
# Storage & auth
# ---------------------------------------------------------------------------

from compass.storage import RunsRepo, UsersRepo, get_db
from compass.storage.mongo import is_real_mongo
from compass.storage.users import AuthError

_db = get_db()
_users = UsersRepo(_db)
_runs = RunsRepo(_db)


def _render_auth() -> None:
    """Login / signup screen. Shown when st.session_state.user is None."""
    st.markdown(
        """
        <div class="compass-hero">
          <h1>🧭 Compass</h1>
          <p>Sign in to onboard any repo. Your usage and run history live in your account.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not is_real_mongo():
        st.warning(
            "MongoDB is not configured — using an in-memory database. Accounts "
            "you create will vanish when the server restarts. Set `MONGO_URI` "
            "in `.env` to persist.",
            icon="🗄️",
        )

    tab_signin, tab_signup, tab_google = st.tabs(["🔐 Sign in", "🆕 Create account", "🟢 Google"])

    with tab_signin:
        with st.form("signin_form", clear_on_submit=False):
            u = st.text_input("Username", key="signin_username")
            p = st.text_input("Password", type="password", key="signin_password")
            submit = st.form_submit_button("Sign in", type="primary", use_container_width=True)
            if submit:
                try:
                    user = _users.sign_in(username=u, password=p)
                    st.session_state.user = {"username": user.username, "email": user.email,
                                             "provider": user.provider}
                    st.rerun()
                except AuthError as e:
                    st.error(str(e), icon="🚫")

    with tab_signup:
        with st.form("signup_form", clear_on_submit=False):
            u = st.text_input("Username", key="signup_username",
                              help="Letters, digits, underscores; lower-cased on save.")
            e = st.text_input("Email (optional)", key="signup_email")
            p = st.text_input("Password (min. 8 chars)", type="password", key="signup_password")
            p2 = st.text_input("Confirm password", type="password", key="signup_password2")
            submit = st.form_submit_button("Create account", type="primary", use_container_width=True)
            if submit:
                if p != p2:
                    st.error("Passwords do not match.", icon="🚫")
                else:
                    try:
                        user = _users.sign_up(username=u, password=p, email=e or None)
                        st.session_state.user = {"username": user.username, "email": user.email,
                                                 "provider": user.provider}
                        st.success("Account created — you're signed in.", icon="✅")
                        st.rerun()
                    except AuthError as ex:
                        st.error(str(ex), icon="🚫")

    with tab_google:
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            st.info(
                "Google sign-in requires `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` "
                "in your `.env`.\n\n"
                "**One-time setup:**\n"
                "1. Go to <https://console.cloud.google.com/apis/credentials> and "
                "   create an OAuth 2.0 Client ID (type: Web application).\n"
                "2. Add your app URL to *Authorized redirect URIs* — for local use, "
                "   `http://localhost:8501`.\n"
                "3. Paste the Client ID and Secret into `.env`, then restart Streamlit.\n\n"
                "For the Cloudflare tunnel URL, you need a **named** tunnel with a stable "
                "hostname (Cloudflare Zero Trust) — the random Quick Tunnel URL changes "
                "every restart, which Google rejects. See DEPLOY.md.",
                icon="🔧",
            )
        else:
            try:
                from streamlit_oauth import OAuth2Component
            except Exception:
                st.error(
                    "Install `streamlit-oauth` to enable Google sign-in: "
                    "`pip install streamlit-oauth`",
                    icon="📦",
                )
            else:
                oauth = OAuth2Component(
                    client_id=client_id,
                    client_secret=client_secret,
                    authorize_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
                    token_endpoint="https://oauth2.googleapis.com/token",
                    refresh_token_endpoint="https://oauth2.googleapis.com/token",
                    revoke_token_endpoint="https://oauth2.googleapis.com/revoke",
                )
                result = oauth.authorize_button(
                    name="Sign in with Google",
                    icon="https://www.google.com/favicon.ico",
                    redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501"),
                    scope="openid email profile",
                    key="google_oauth",
                    use_container_width=True,
                )
                if result and "token" in result:
                    id_token = result["token"].get("id_token", "")
                    try:
                        # Verify id_token
                        from google.auth.transport import requests as g_requests
                        from google.oauth2 import id_token as g_idtoken
                        claims = g_idtoken.verify_oauth2_token(
                            id_token, g_requests.Request(), client_id
                        )
                        user = _users.upsert_google(
                            google_sub=claims["sub"],
                            email=claims.get("email", ""),
                            name=claims.get("name"),
                        )
                        st.session_state.user = {"username": user.username, "email": user.email,
                                                 "provider": user.provider}
                        st.rerun()
                    except Exception as ex:
                        st.error(f"Google verification failed: {ex}", icon="🚫")


# ---------------------------------------------------------------------------
# Auth wall
# ---------------------------------------------------------------------------

if not st.session_state.user:
    _render_auth()
    st.stop()

CURRENT_USER = st.session_state.user
USERNAME = CURRENT_USER["username"]

# ---------------------------------------------------------------------------
# Sidebar (post-login)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown(f"### 🧭 Compass")
    st.caption(f"Signed in as **{USERNAME}**"
               + (f" · {CURRENT_USER['email']}" if CURRENT_USER.get("email") else ""))
    if st.button("Sign out", use_container_width=True, disabled=st.session_state.running):
        st.session_state.user = None
        st.session_state.pack_dir = None
        st.session_state.last_pack_totals = None
        st.rerun()

    st.divider()

    st.markdown("**Bob API key**")
    # Never pre-fill this box with a value that came from Streamlit secrets —
    # a shared deployment-wide key would otherwise be revealable to any signed-in
    # user via the input's built-in show/hide eye icon. It stays empty unless
    # *this* user types their own key, which then overrides the deployment's.
    bob_key = st.text_input(
        "BOB_API_KEY", value="",
        type="password", label_visibility="collapsed",
        help="Get one at https://bob.ibm.com → API keys. Leave blank to use the deployment's key, if one is configured.",
        disabled=st.session_state.running,
    )
    if bob_key:
        os.environ["BOB_API_KEY"] = bob_key
        st.success("Using the key you pasted.", icon="✅")
    elif os.environ.get("BOB_API_KEY"):
        st.success("Using the deployment's configured Bob key.", icon="✅")
    else:
        st.info("Paste your Bob key to enable real runs.", icon="🔑")

    # --- Cost / usage panel ---
    st.divider()
    totals = _runs.totals_for(USERNAME)
    st.markdown("**Your usage**")
    c1, c2 = st.columns(2)
    c1.metric("Runs", totals["runs"])
    c2.metric("Bobcoins", f"{totals['total_bobcoins']:.4f}")
    st.caption(f"Successful: {totals['successful_runs']} · "
               f"Tokens: {totals['total_tokens']:,}")

    # --- Health ---
    import shutil as _shutil
    from compass.infrastructure.bob_bootstrap import is_bootstrapped
    st.divider()
    bob_ready = _shutil.which("bob") is not None or is_bootstrapped()
    checks = {
        "Bob runtime":   bob_ready,
        "Git":           _shutil.which("git") is not None,
        "BOB_API_KEY":   bool(os.environ.get("BOB_API_KEY")),
        "Real MongoDB":  is_real_mongo(),
    }
    st.markdown("**Environment**")
    for label, ok in checks.items():
        st.markdown(f"- {'✅' if ok else '⚠️'} {label}")
    if not bob_ready:
        st.caption(
            "Bob CLI not found on this host, and it can't be auto-installed here — "
            "`bobshell` isn't on the public npm registry. Real runs need Bob "
            "installed via IBM's official installer on the machine running Compass."
        )
    st.caption("MIT-licensed · IBM Bob 2.0 · lablab.ai")

# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------

st.markdown(
    """
    <div class="compass-hero">
      <h1>🧭 Compass</h1>
      <p>Point at any repo — GitHub, GitLab, Bitbucket, SSH, a local folder, or a ZIP — and get an onboarding pack in minutes.</p>
      <div>
        <span class="compass-badge">IBM Bob 2.0</span>
        <span class="compass-badge">watsonx Orchestrate</span>
        <span class="compass-badge">MongoDB</span>
        <span class="compass-badge">MIT</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT = Path("./onboarding_ui").resolve()


def _pack_to_zip_bytes(pack_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in pack_dir.rglob("*"):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(pack_dir))
    return buf.getvalue()


def _extract_mermaid(arch_md: str) -> tuple[str, str]:
    if "```mermaid" not in arch_md:
        return "", arch_md
    head, _, tail = arch_md.partition("```mermaid")
    src, _, rest = tail.partition("```")
    return src.strip(), (head + rest).strip()


def _render_mermaid(source: str, height: int = 460) -> None:
    html = f"""
    <div style="background:white; padding:8px; border-radius:8px;">
      <div class="mermaid">{source}</div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({{startOnLoad: true, theme: 'default'}});</script>
    """
    components.html(html, height=height, scrolling=True)


@st.cache_data(show_spinner=False)
def _mermaid_png_bytes(source: str) -> bytes | None:
    if not source.strip():
        return None
    try:
        encoded = base64.urlsafe_b64encode(source.encode("utf-8")).decode("ascii").rstrip("=")
        r = httpx.get(f"https://mermaid.ink/img/{encoded}?type=png&bgColor=white", timeout=15)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
            return r.content
    except Exception:
        pass
    return None


def _folder_picker_dialog() -> tuple[str | None, str | None]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return None, f"tkinter unavailable: {exc}"
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.after(100, lambda: root.focus_force())
        path = filedialog.askdirectory(master=root, title="Choose a repo folder for Compass")
        root.destroy()
    except Exception as exc:
        return None, f"picker failed: {exc}"
    if not path:
        return None, "cancelled"
    return path, None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _render_pack(pack_dir: Path) -> None:
    """Render a completed onboarding pack — metrics, downloads, and content sub-tabs."""
    report_path = pack_dir / "report.json"
    report = {}
    try:
        report = json.loads(_read(report_path)) if report_path.exists() else {}
    except Exception:
        pass

    kind = (report.get("inventory") or {}).get("content_kind", "unknown")
    if kind != "code" and kind != "unknown":
        st.info(
            f"This upload looks like **{kind}**, not a codebase. Compass produced a "
            f"content overview instead of an architecture — the description tab tells "
            f"you what's in there and how to explore it.",
            icon="📚",
        )

    st.success(f"Pack written to `{pack_dir}`", icon="✅")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Wall clock",  f"{report.get('total_wall_clock_seconds', 0):.1f}s")
    m2.metric("Bobcoins",     f"{report.get('total_bobcoins', 0):.4f}")
    m3.metric("Tokens",       f"{report.get('total_tokens', 0):,}")
    m4.metric("Content",      kind.capitalize())

    # --- Downloads ---
    with st.container(border=True):
        st.markdown("#### ⬇️ Downloads")
        arch_md = _read(pack_dir / "ARCHITECTURE.md")
        mermaid_src, _ = _extract_mermaid(arch_md)

        d1, d2, d3, d4 = st.columns(4)
        d1.download_button("🗂️ Full pack (.zip)", data=_pack_to_zip_bytes(pack_dir),
                           file_name=f"{pack_dir.name}.zip", mime="application/zip",
                           use_container_width=True)
        d2.download_button("📋 OVERVIEW.md", data=_read(pack_dir / "OVERVIEW.md"),
                           file_name="OVERVIEW.md", mime="text/markdown",
                           use_container_width=True,
                           disabled=not (pack_dir / "OVERVIEW.md").exists())
        d3.download_button("🛠️ DEV_LOOP.md", data=_read(pack_dir / "DEV_LOOP.md"),
                           file_name="DEV_LOOP.md", mime="text/markdown",
                           use_container_width=True,
                           disabled=not (pack_dir / "DEV_LOOP.md").exists())
        d4.download_button("🎯 STARTER_TASKS.md", data=_read(pack_dir / "STARTER_TASKS.md"),
                           file_name="STARTER_TASKS.md", mime="text/markdown",
                           use_container_width=True,
                           disabled=not (pack_dir / "STARTER_TASKS.md").exists())

        e1, e2, e3, e4 = st.columns(4)
        e1.download_button("🗺️ ARCHITECTURE.md", data=arch_md, file_name="ARCHITECTURE.md",
                           mime="text/markdown", use_container_width=True, disabled=not arch_md)
        e2.download_button("📖 TOUR.md", data=_read(pack_dir / "TOUR.md"),
                           file_name="TOUR.md", mime="text/markdown",
                           use_container_width=True,
                           disabled=not (pack_dir / "TOUR.md").exists())
        e3.download_button("📊 report.json", data=_read(pack_dir / "report.json"),
                           file_name="report.json", mime="application/json",
                           use_container_width=True,
                           disabled=not (pack_dir / "report.json").exists())
        png = _mermaid_png_bytes(mermaid_src) if mermaid_src else None
        e4.download_button("🖼️ Architecture (PNG)", data=png or b"",
                           file_name="architecture.png", mime="image/png",
                           use_container_width=True, disabled=png is None,
                           help="Rendered via mermaid.ink" if png
                                else "No Mermaid block in ARCHITECTURE.md")

    # --- Content ---
    with st.container(border=True):
        st.markdown("#### 📚 Content")
        subtabs = st.tabs(["📋 Overview", "🗺️ Architecture", "🛠️ Dev loop",
                           "📖 Tour", "🎯 Starter tasks", "📊 Report"])
        with subtabs[0]:
            st.markdown(_read(pack_dir / "OVERVIEW.md") or "_(empty)_")
        with subtabs[1]:
            mermaid_src, rest = _extract_mermaid(_read(pack_dir / "ARCHITECTURE.md"))
            if mermaid_src:
                st.caption("Live architecture diagram (rendered from Mermaid):")
                _render_mermaid(mermaid_src)
                st.divider()
            st.markdown(rest or "_(empty)_")
        with subtabs[2]:
            st.markdown(_read(pack_dir / "DEV_LOOP.md") or "_(empty)_")
        with subtabs[3]:
            st.markdown(_read(pack_dir / "TOUR.md") or "_(empty)_")
        with subtabs[4]:
            st.markdown(_read(pack_dir / "STARTER_TASKS.md") or "_(empty)_")
        with subtabs[5]:
            if report:
                st.json(report)
            else:
                st.info("No report.json in this pack.")


# ---------------------------------------------------------------------------
# Tabs (post-login)
# ---------------------------------------------------------------------------

tab_onboard, tab_ask, tab_demo, tab_history, tab_about = st.tabs(
    ["🚀 Onboard", "💬 Ask this repo", "🎬 Demo (no key)", "🕘 History", "ℹ️ About & deploy"]
)

# ==== ONBOARD =============================================================
with tab_onboard:
    running = st.session_state.running

    st.subheader("Onboard a repository")
    st.caption("Real runs need `BOB_API_KEY` in the sidebar and the `bob` CLI on the host.")

    with st.container(border=True):
        st.markdown("**1. Source**")
        src_kind = st.radio(
            "Where does the repo live?",
            options=["Git URL", "Local folder", "Upload .zip"],
            horizontal=True, key="src_kind", disabled=running,
            label_visibility="collapsed",
        )

        repo_spec: str | None = None

        if src_kind == "Git URL":
            repo_spec = st.text_input(
                "Repository URL",
                placeholder="https://github.com/pallets/flask",
                disabled=running, key="git_url",
            )
        elif src_kind == "Local folder":
            st.session_state.setdefault("local_path", "")

            def _pick_local_folder() -> None:
                chosen, err = _folder_picker_dialog()
                if chosen:
                    st.session_state.local_path = chosen
                    st.session_state.last_browse_msg = ("success", f"Picked: {chosen}")
                elif err == "cancelled":
                    st.session_state.last_browse_msg = ("info", "No folder chosen (cancelled).")
                else:
                    st.session_state.last_browse_msg = ("error", err or "picker failed")

            col_path, col_browse = st.columns([5, 1])
            with col_path:
                repo_spec = st.text_input(
                    "Path to a local folder", placeholder=r"D:\work\my-service",
                    disabled=running, key="local_path",
                )
            with col_browse:
                st.write("")
                st.button("📁 Browse…", disabled=running, use_container_width=True,
                          on_click=_pick_local_folder)
            st.caption(
                "💡 The picker opens on **the machine running Streamlit** — not on "
                "the device you're viewing this from. Alt-Tab if you don't see it, "
                "or paste the path directly."
            )
            msg = st.session_state.get("last_browse_msg")
            if msg:
                level, text = msg
                {"success": st.success, "info": st.info, "error": st.error}[level](
                    text if level != "error" else f"Browse failed — {text}",
                    icon={"success": "📁", "info": "ℹ️", "error": "⚠️"}[level],
                )
            if st.session_state.local_path:
                st.caption(f"Current path: `{st.session_state.local_path}`")
        else:
            uploaded = st.file_uploader(
                "Upload a .zip of the repository", type=["zip"],
                disabled=running, key="zip_upload",
            )
            if uploaded is not None:
                tmp = Path(tempfile.mkdtemp(prefix="compass-upload-"))
                saved = tmp / uploaded.name
                saved.write_bytes(uploaded.getvalue())
                repo_spec = str(saved)
                st.caption(f"Uploaded to `{saved}`")

    with st.container(border=True):
        st.markdown("**2. Options**")
        c1, c2, c3 = st.columns(3)
        with c1:
            role = st.selectbox("Role hint (optional)",
                                options=["(any)", "backend", "frontend", "sre", "full-stack"],
                                disabled=running)
        with c2:
            difficulty = st.selectbox("Starter task difficulty",
                                      options=["easy", "medium"], disabled=running)
        with c3:
            output_name = st.text_input("Output folder name",
                                        value=st.session_state.get("output_name", "onboarding_ui"),
                                        disabled=running, key="output_name")

    ready = bool(repo_spec and str(repo_spec).strip())
    run_col, help_col = st.columns([1, 4])
    run_clicked = run_col.button("🚀 Run Compass", type="primary",
                                 disabled=(not ready) or running, use_container_width=True)
    if not ready and not running:
        help_col.info("Choose a source above to enable the Run button.", icon="👆")

    if run_clicked:
        if not os.environ.get("BOB_API_KEY"):
            st.error("No BOB_API_KEY. Paste one into the sidebar, or use the **Demo** tab.", icon="🔑")
        else:
            st.session_state.running = True
            st.session_state.last_error = None

            output_dir = (Path.cwd() / output_name).resolve()
            steps = [
                ("clone", "Resolving source"),
                ("scout", "Scout — inventory"),
                ("cartographer", "Cartographer — architecture"),
                ("devloop", "DevLoopRunner — install / test (slowest)"),
                ("guide", "Guide — tour + starter tasks"),
                ("done", "Done"),
            ]
            state: dict[str, str] = {}

            import time as _time
            t_start = _time.perf_counter()
            pack_obj = None
            error_msg = None

            with st.status("Running Compass — inputs are locked until this finishes.",
                           expanded=True) as status:
                placeholder = st.empty()

                def render_progress() -> None:
                    lines = []
                    for k, label in steps:
                        v = state.get(k)
                        if v is None:
                            icon, text = "⏳", "…"
                        elif "done" in v.lower() or k == "done":
                            icon, text = "✅", v
                        else:
                            icon, text = "🔵", v
                        lines.append(f"{icon} **{label}** — {text}")
                    placeholder.markdown("\n\n".join(lines))

                render_progress()

                def on_progress(step: str, msg: str) -> None:
                    state[step] = msg
                    render_progress()

                try:
                    from compass.application.orchestrator import Orchestrator
                    pack_obj = Orchestrator(on_progress=on_progress).run(
                        repo=str(repo_spec),
                        role=None if role == "(any)" else role,
                        difficulty=difficulty,
                        output_dir=output_dir, allow_large=False,
                    )
                    st.session_state.pack_dir = str(output_dir)
                    status.update(label="Compass run complete ✅", state="complete", expanded=False)
                except Exception as exc:
                    error_msg = f"{type(exc).__name__}: {exc}"
                    st.session_state.last_error = error_msg
                    status.update(label=f"Run failed: {exc}", state="error", expanded=True)
                finally:
                    st.session_state.running = False

            # Record the run to MongoDB (success or failure)
            try:
                _runs.record(
                    username=USERNAME,
                    repo=str(repo_spec),
                    role=None if role == "(any)" else role,
                    difficulty=difficulty,
                    pack_dir=str(output_dir),
                    duration_seconds=(pack_obj.total_wall_clock_seconds if pack_obj
                                      else _time.perf_counter() - t_start),
                    total_bobcoins=(pack_obj.total_bobcoins if pack_obj else 0.0),
                    subagent_bobcoins={r.name: r.bobcoins for r in pack_obj.subagents}
                                     if pack_obj else {},
                    total_tokens=(pack_obj.total_tokens if pack_obj else 0),
                    content_kind=(pack_obj.inventory.content_kind if pack_obj else "unknown"),
                    status="success" if pack_obj else "failed",
                    error=error_msg,
                )
            except Exception:
                pass  # persistence is best-effort; UI still shows the pack

            st.rerun()

    if st.session_state.last_error:
        st.error(f"Last run failed — {st.session_state.last_error}", icon="🛑")
        st.caption("Common causes: `bob` CLI not on PATH, Bob key invalid, or "
                   "the repo needs longer than the 300 s per-subagent timeout.")
    elif st.session_state.pack_dir:
        st.divider()
        _render_pack(Path(st.session_state.pack_dir))

# ==== ASK =================================================================
with tab_ask:
    st.subheader("Ask this repo")
    st.caption("Grounded Q&A over an existing pack. Answers must cite `file:line` from `notes/*.json`.")
    with st.container(border=True):
        default_pack = st.session_state.pack_dir or str(DEFAULT_OUTPUT)
        pack_dir = Path(st.text_input("Pack directory", value=default_pack))
        question = st.text_area("Your question",
                                placeholder="Where does routing happen?", height=90)
        ask_clicked = st.button("🔍 Ask", type="primary", disabled=not question.strip())
    if ask_clicked:
        try:
            from compass.application.ask import answer_question
            ans = answer_question(pack_dir=pack_dir, question=question)
            with st.container(border=True):
                st.markdown("**Answer**")
                st.markdown(f"<div class='compass-answer'>{ans.answer}</div>",
                            unsafe_allow_html=True)
                if ans.citations:
                    with st.expander(f"Citations ({len(ans.citations)})", expanded=True):
                        for c in ans.citations:
                            st.markdown(f"- `{c.file}:{c.line_start}-{c.line_end}` — {c.snippet}")
        except NotImplementedError:
            st.warning(
                "`/ask` is scaffolded but not yet wired. Next step: BM25 over "
                "`notes/*.json` and a grounded LLM call.", icon="🚧",
            )
        except FileNotFoundError as e:
            st.error(str(e), icon="📁")

# ==== DEMO ================================================================
with tab_demo:
    st.subheader("Demo — see what a pack looks like")
    st.caption("Canned fixture. No Bob call, no network, no API key.")
    with st.container(border=True):
        demo_dir = st.text_input("Where to write the demo pack",
                                 value=str((Path.cwd() / "onboarding_demo").resolve()),
                                 key="demo_dir")
        if st.button("🎬 Generate demo pack", type="primary"):
            from compass.application.demo import write_demo_pack
            path = Path(demo_dir)
            write_demo_pack(path)
            st.session_state.pack_dir = str(path)
            st.rerun()
    if st.session_state.pack_dir and Path(st.session_state.pack_dir) == Path(demo_dir):
        st.divider()
        _render_pack(Path(st.session_state.pack_dir))

# ==== HISTORY =============================================================
with tab_history:
    st.subheader("Your run history")
    st.caption(f"Everything you've onboarded as **{USERNAME}**, newest first.")
    rows = _runs.list_for(USERNAME, limit=50)
    if not rows:
        st.info("No runs yet. Run something from the Onboard tab and it'll show up here.", icon="🗄️")
    else:
        totals = _runs.totals_for(USERNAME)
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Runs", totals["runs"])
        h2.metric("Successful", totals["successful_runs"])
        h3.metric("Total Bobcoins", f"{totals['total_bobcoins']:.4f}")
        h4.metric("Total tokens", f"{totals['total_tokens']:,}")
        st.divider()
        for r in rows:
            with st.container(border=True):
                title = r.get("repo") or "(unknown)"
                started = r.get("started_at")
                if isinstance(started, datetime):
                    started_s = started.strftime("%Y-%m-%d %H:%M UTC")
                else:
                    started_s = str(started or "")
                status_icon = "✅" if r.get("status") == "success" else "❌"
                st.markdown(f"**{status_icon} {title}** &nbsp; · &nbsp; "
                            f"<span class='muted'>{started_s}</span>",
                            unsafe_allow_html=True)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Duration", f"{r.get('duration_seconds', 0):.1f}s")
                c2.metric("Bobcoins", f"{r.get('total_bobcoins', 0):.4f}")
                c3.metric("Tokens",   f"{r.get('total_tokens', 0):,}")
                c4.metric("Content",  (r.get("content_kind") or "?").capitalize())
                if r.get("subagent_bobcoins"):
                    parts = ", ".join(f"{k}: {v:.4f}"
                                      for k, v in r["subagent_bobcoins"].items())
                    st.caption(f"Per-subagent Bobcoins — {parts}")
                if r.get("error"):
                    st.caption(f"Error: `{r['error']}`")

# ==== ABOUT ===============================================================
with tab_about:
    st.subheader("About Compass")
    st.markdown(
        """
Compass turns *"first productive commit in weeks"* into *"first productive commit in hours."*

**Four Bob subagents** run in sequence:
1. **Scout** — inventory + content-kind (code / docs / media / mixed).
2. **Cartographer** — architecture map with evidence.
3. **DevLoopRunner** — install / build / test Bob *actually* ran.
4. **Guide** — reading tour + three starter tasks with hint ladders.

**Sources:** any Git URL, a local folder (with a native picker), or a `.zip` upload.

**Accounts + history** stored in MongoDB — set `MONGO_URI` in `.env`
for real persistence, or leave it blank for an in-memory dev fallback.
Every run records the Bobcoins each subagent spent so you can see costs
per project and lifetime totals in the sidebar.

**Deploy:** see `DEPLOY.md`. The recommended path for a live judging link
is `scripts/serve_public.ps1` (Streamlit + Cloudflare Quick Tunnel).
        """
    )
