"""Compass — Streamlit UI.

Point Compass at any Git repo (GitHub, GitLab, Bitbucket, SSH), a local
folder, or a .zip upload, and watch four Bob subagents build an onboarding
pack. Includes a one-click Demo that works without a Bob key.

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
from pathlib import Path
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Page config + light custom styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Compass — Onboarding for any repo",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": "Compass is an IBM Bob 2.0 agent that turns any Git repo, "
                 "folder, or .zip into an onboarding pack in minutes.",
    },
)

st.markdown(
    """
    <style>
      /* Hero */
      .compass-hero { padding: 0.15rem 0 0.75rem 0; }
      .compass-hero h1 { margin: 0 0 0.15rem 0; font-size: 2.2rem; letter-spacing: -0.5px; }
      .compass-hero p  { color: #6b7280; margin: 0; font-size: 1.02rem; }
      .compass-badge   { display: inline-block; padding: 2px 8px; border-radius: 999px;
                         background: #eef2ff; color: #4f46e5; font-size: 0.75rem;
                         margin: 4px 4px 0 0; border: 1px solid #e0e7ff; }
      /* Nicer tabs */
      .stTabs [data-baseweb="tab-list"] { gap: 10px; }
      .stTabs [data-baseweb="tab"]      { font-weight: 500; }
      /* Bordered containers get a bit of breathing room */
      div[data-testid="stVerticalBlockBorderWrapper"] { padding: 0.5rem 0; }
      /* Answer / rendered markdown feels roomier */
      .compass-answer { line-height: 1.55; font-size: 1.02rem; }
      .compass-answer pre, .compass-answer code { font-size: 0.92em; }
      /* Small "muted" caption look */
      .muted { color: #6b7280; font-size: 0.85rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------

_defaults = {
    "running": False,
    "pack_dir": None,          # str path to the last completed pack
    "last_error": None,
    "queued_run": None,        # dict of run params, set by button click, consumed next rerun
    "progress_lines": [],      # list[str] rendered under the run form during a run
}
for k, v in _defaults.items():
    st.session_state.setdefault(k, v)

# ---------------------------------------------------------------------------
# Sidebar — Bob key + health only (COMPASS_API_TOKEN is not needed here)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### 🧭 Compass")
    st.caption("Developer onboarding, in minutes.")
    st.divider()

    st.markdown("**Bob API key**")
    bob_key = st.text_input(
        "BOB_API_KEY",
        value=os.getenv("BOB_API_KEY", ""),
        type="password",
        label_visibility="collapsed",
        help="Get one at https://bob.ibm.com → API keys. Used only for real runs; the Demo tab does not need it.",
        disabled=st.session_state.running,
    )
    if bob_key:
        os.environ["BOB_API_KEY"] = bob_key
        st.success("Bob key loaded for this session.", icon="✅")
    else:
        st.info("Paste your Bob key above to enable real runs. The Demo tab works without it.", icon="🔑")

    st.divider()

    # --- Health check ---
    import shutil as _shutil
    checks = {
        "Bob CLI on PATH": _shutil.which("bob") is not None,
        "Git on PATH":     _shutil.which("git") is not None,
        "Node on PATH":    _shutil.which("node") is not None,
        "BOB_API_KEY set": bool(os.environ.get("BOB_API_KEY")),
    }
    st.markdown("**Environment**")
    for label, ok in checks.items():
        st.markdown(f"- {'✅' if ok else '⚠️'} {label}")

    st.divider()
    with st.expander("What token do I need?"):
        st.markdown(
            "**Only `BOB_API_KEY`** — from `bob.ibm.com → API keys`. "
            "Paste it above and hit Run.\n\n"
            "There is no separate 'Compass token' for the UI. "
            "`COMPASS_API_TOKEN` is only needed if you also run `compass serve` "
            "(the FastAPI backend for watsonx Orchestrate). The server auto-generates "
            "one on first launch if you don't set it."
        )
    st.caption("Hackathon: IBM Bob 2.0 · lablab.ai · MIT-licensed.")

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
        <span class="compass-badge">watsonx.ai</span>
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
    """Return (mermaid_source, arch_md_without_mermaid_block)."""
    if "```mermaid" not in arch_md:
        return "", arch_md
    head, _, tail = arch_md.partition("```mermaid")
    src, _, rest = tail.partition("```")
    return src.strip(), (head + rest).strip()


def _render_mermaid(source: str, height: int = 460) -> None:
    """Render Mermaid via a small inline HTML component."""
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
    """Render Mermaid to PNG via the public mermaid.ink service."""
    if not source.strip():
        return None
    try:
        encoded = base64.urlsafe_b64encode(source.encode("utf-8")).decode("ascii").rstrip("=")
        url = f"https://mermaid.ink/img/{encoded}?type=png&bgColor=white"
        r = httpx.get(url, timeout=15)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
            return r.content
    except Exception:
        pass
    return None


def _folder_picker_dialog() -> tuple[str | None, str | None]:
    """Open a native folder picker on the Streamlit host machine.

    Returns (chosen_path, error_message). Exactly one is non-None:
      * (path, None) — a folder was picked
      * (None, "cancelled") — the dialog opened and the user cancelled
      * (None, "<other>") — tkinter unavailable, no display, etc.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return None, f"tkinter unavailable: {exc}"

    try:
        root = tk.Tk()
        root.withdraw()
        # Bring the dialog to the front on Windows. -topmost + focus_force is
        # the combination that actually works when Streamlit is behind Chrome.
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

    # --- Metrics ---
    report_path = pack_dir / "report.json"
    total_time = total_bob = ""
    try:
        report = json.loads(_read(report_path)) if report_path.exists() else {}
        total_time = f"{report.get('total_wall_clock_seconds', 0):.1f}s"
        # bobcoins spent lives in the run terminal; expose via the report if we recorded it
    except Exception:
        report = {}

    st.success(f"Pack written to `{pack_dir}`", icon="✅")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Wall clock",  total_time or "—")
    m2.metric("Subagents",    str(len(report.get("subagents", []))))
    m3.metric("Total tokens", str(report.get("total_tokens", 0)))
    m4.metric("Repo",         (report.get("repo") or "—")[:24] + ("…" if len(report.get("repo") or "") > 24 else ""))

    # --- Downloads section (its own bordered box) ---
    with st.container(border=True):
        st.markdown("#### ⬇️  Downloads")

        arch_md = _read(pack_dir / "ARCHITECTURE.md")
        mermaid_src, _ = _extract_mermaid(arch_md)

        d1, d2, d3, d4 = st.columns(4)
        d1.download_button(
            "🗂️  Full pack (.zip)", data=_pack_to_zip_bytes(pack_dir),
            file_name=f"{pack_dir.name}.zip", mime="application/zip",
            use_container_width=True,
        )
        d2.download_button(
            "📋 OVERVIEW.md", data=_read(pack_dir / "OVERVIEW.md"),
            file_name="OVERVIEW.md", mime="text/markdown",
            use_container_width=True, disabled=not (pack_dir / "OVERVIEW.md").exists(),
        )
        d3.download_button(
            "🛠️ DEV_LOOP.md", data=_read(pack_dir / "DEV_LOOP.md"),
            file_name="DEV_LOOP.md", mime="text/markdown",
            use_container_width=True, disabled=not (pack_dir / "DEV_LOOP.md").exists(),
        )
        d4.download_button(
            "🎯 STARTER_TASKS.md", data=_read(pack_dir / "STARTER_TASKS.md"),
            file_name="STARTER_TASKS.md", mime="text/markdown",
            use_container_width=True, disabled=not (pack_dir / "STARTER_TASKS.md").exists(),
        )

        e1, e2, e3, e4 = st.columns(4)
        e1.download_button(
            "🗺️ ARCHITECTURE.md", data=arch_md, file_name="ARCHITECTURE.md",
            mime="text/markdown", use_container_width=True, disabled=not arch_md,
        )
        e2.download_button(
            "📖 TOUR.md", data=_read(pack_dir / "TOUR.md"),
            file_name="TOUR.md", mime="text/markdown",
            use_container_width=True, disabled=not (pack_dir / "TOUR.md").exists(),
        )
        e3.download_button(
            "📊 report.json", data=_read(pack_dir / "report.json"),
            file_name="report.json", mime="application/json",
            use_container_width=True, disabled=not (pack_dir / "report.json").exists(),
        )
        # --- Architecture as a standalone PNG ---
        png_bytes = _mermaid_png_bytes(mermaid_src) if mermaid_src else None
        e4.download_button(
            "🖼️ Architecture (PNG)",
            data=png_bytes or b"",
            file_name="architecture.png",
            mime="image/png",
            use_container_width=True,
            disabled=png_bytes is None,
            help="Rendered via mermaid.ink" if png_bytes else "No Mermaid block in ARCHITECTURE.md",
        )

    # --- Content section (its own bordered box, tabbed) ---
    with st.container(border=True):
        st.markdown("#### 📚 Content")

        subtabs = st.tabs([
            "📋 Overview", "🗺️ Architecture", "🛠️ Dev loop",
            "📖 Tour", "🎯 Starter tasks", "📊 Report",
        ])

        with subtabs[0]:
            st.markdown(f"<div class='compass-answer'>{_read(pack_dir / 'OVERVIEW.md') or '_(empty)_'}</div>",
                        unsafe_allow_html=False)
            st.markdown(_read(pack_dir / "OVERVIEW.md") or "_(empty)_")

        with subtabs[1]:
            mermaid_src, rest = _extract_mermaid(_read(pack_dir / "ARCHITECTURE.md"))
            if mermaid_src:
                st.caption("Live architecture diagram (rendered from Mermaid):")
                _render_mermaid(mermaid_src)
                st.caption("Download this diagram as a PNG from the Downloads section above.")
                st.divider()
            st.markdown(rest or "_(empty)_")

        with subtabs[2]:
            st.markdown(_read(pack_dir / "DEV_LOOP.md") or "_(empty)_")

        with subtabs[3]:
            st.markdown(_read(pack_dir / "TOUR.md") or "_(empty)_")

        with subtabs[4]:
            st.markdown(_read(pack_dir / "STARTER_TASKS.md") or "_(empty)_")

        with subtabs[5]:
            if report_path.exists():
                st.json(report)
            else:
                st.info("No report.json in this pack.")


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_onboard, tab_ask, tab_demo, tab_about = st.tabs(
    ["🚀 Onboard", "💬 Ask this repo", "🎬 Demo (no key)", "ℹ️ About & deploy"]
)

# ==== ONBOARD =============================================================
with tab_onboard:
    running = st.session_state.running

    st.subheader("Onboard a repository")
    st.caption("Real runs need `BOB_API_KEY` in the sidebar and the `bob` CLI on the host running Compass.")

    with st.container(border=True):
        st.markdown("**1. Source**")
        src_kind = st.radio(
            "Where does the repo live?",
            options=["Git URL", "Local folder", "Upload .zip"],
            horizontal=True,
            key="src_kind",
            disabled=running,
            label_visibility="collapsed",
        )

        repo_spec: str | None = None

        if src_kind == "Git URL":
            repo_spec = st.text_input(
                "Repository URL",
                placeholder="https://github.com/pallets/flask",
                help="Anything `git clone` accepts: HTTPS, SSH, GitLab, Bitbucket, self-hosted…",
                disabled=running,
                key="git_url",
            )
        elif src_kind == "Local folder":
            # The text_input owns the "local_path" widget key. To let the
            # Browse button also write to it we mutate session_state inside an
            # on_click callback (which fires BEFORE the widget re-instantiates
            # on the next rerun; writing to a widget-owned key after that
            # raises StreamlitWidgetAlreadyInstantiatedError).
            st.session_state.setdefault("local_path", "")
            st.session_state.setdefault("last_browse_msg", None)

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
                    "Path to a local folder",
                    placeholder=r"D:\work\my-service",
                    disabled=running,
                    key="local_path",
                )
            with col_browse:
                st.write("")  # vertical spacer aligns with the input
                st.button(
                    "📁 Browse…",
                    disabled=running,
                    use_container_width=True,
                    help="Opens on the machine running Streamlit (this laptop). "
                         "If the dialog does not appear, it may be behind the browser — Alt-Tab.",
                    on_click=_pick_local_folder,
                )
            st.caption(
                "💡 The folder picker opens on **the machine running Streamlit** — "
                "not on the device you're viewing this page from. "
                "If a dialog doesn't appear, Alt-Tab to it, or just paste the path directly."
            )
            msg = st.session_state.get("last_browse_msg")
            if msg:
                level, text = msg
                if level == "success":
                    st.success(text, icon="📁")
                elif level == "info":
                    st.info(text, icon="ℹ️")
                else:
                    st.error(f"Browse failed — {text}. Paste the path directly instead.", icon="⚠️")
            if st.session_state.local_path:
                st.caption(f"Current path: `{st.session_state.local_path}`")
        else:
            uploaded = st.file_uploader(
                "Upload a .zip of the repository",
                type=["zip"],
                disabled=running,
                help="The zip is extracted into an isolated workspace; your original is never modified.",
                key="zip_upload",
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
    run_clicked = run_col.button(
        "🚀 Run Compass", type="primary",
        disabled=(not ready) or running,
        use_container_width=True,
    )
    if not ready and not running:
        help_col.info("Choose a source above to enable the Run button.", icon="👆")

    # --- Kick off the run inline ------------------------------------------
    if run_clicked:
        if not os.environ.get("BOB_API_KEY"):
            st.error("No BOB_API_KEY. Paste one into the sidebar, or use the **Demo** tab.", icon="🔑")
        else:
            st.session_state.running = True
            st.session_state.last_error = None
            st.session_state.progress_lines = []

            output_dir = (Path.cwd() / output_name).resolve()
            steps = [
                ("clone", "Resolving source"),
                ("scout", "Scout — inventory"),
                ("cartographer", "Cartographer — architecture"),
                ("devloop", "DevLoopRunner — install / test (slowest step)"),
                ("guide", "Guide — tour + starter tasks"),
                ("done", "Done"),
            ]
            step_map = {k: v for k, v in steps}
            state: dict[str, str] = {}

            with st.status("Running Compass — you can't change the inputs above until this finishes.",
                           expanded=True) as status:
                def render_progress() -> None:
                    lines = []
                    for k, label in steps:
                        v = state.get(k)
                        if v is None:
                            icon = "⏳"; text = "…"
                        elif "done" in v.lower() or k == "done":
                            icon = "✅"; text = v
                        else:
                            icon = "🔵"; text = v
                        lines.append(f"{icon} **{label}** — {text}")
                    placeholder.markdown("\n\n".join(lines))

                placeholder = st.empty()
                render_progress()

                def on_progress(step: str, msg: str) -> None:
                    state[step] = msg
                    render_progress()

                try:
                    from compass.application.orchestrator import Orchestrator
                    Orchestrator(on_progress=on_progress).run(
                        repo=str(repo_spec),
                        role=None if role == "(any)" else role,
                        difficulty=difficulty,
                        output_dir=output_dir,
                        allow_large=False,
                    )
                    st.session_state.pack_dir = str(output_dir)
                    status.update(label="Compass run complete ✅", state="complete", expanded=False)
                except Exception as exc:
                    st.session_state.last_error = f"{type(exc).__name__}: {exc}"
                    status.update(label=f"Run failed: {exc}", state="error", expanded=True)
                finally:
                    st.session_state.running = False

            st.rerun()

    # --- Render the last completed pack (if any) --------------------------
    if st.session_state.last_error:
        st.error(f"Last run failed — {st.session_state.last_error}", icon="🛑")
        st.caption("Common causes: `bob` CLI not on PATH, Bob key invalid, or the repo needs longer than the 300 s timeout.")
    elif st.session_state.pack_dir:
        st.divider()
        _render_pack(Path(st.session_state.pack_dir))

# ==== ASK =================================================================
with tab_ask:
    st.subheader("Ask this repo")
    st.caption("Grounded Q&A over an existing pack. Answers must cite `file:line` from the pack's `notes/*.json`.")

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
                else:
                    st.caption("No citations returned.")
        except NotImplementedError:
            st.warning(
                "`/ask` is scaffolded but not yet wired. Next step: BM25 over `notes/*.json` "
                "and a grounded LLM call. Track this in `src/compass/application/ask.py`.",
                icon="🚧",
            )
        except FileNotFoundError as e:
            st.error(str(e), icon="📁")

# ==== DEMO ================================================================
with tab_demo:
    st.subheader("Demo — see what a pack looks like")
    st.caption("Uses a canned fixture. No Bob call, no network, no API key.")

    with st.container(border=True):
        demo_dir = st.text_input(
            "Where to write the demo pack",
            value=str((Path.cwd() / "onboarding_demo").resolve()),
            key="demo_dir",
        )
        if st.button("🎬 Generate demo pack", type="primary"):
            from compass.application.demo import write_demo_pack
            path = Path(demo_dir)
            write_demo_pack(path)
            st.session_state.pack_dir = str(path)
            st.rerun()

    if st.session_state.pack_dir and Path(st.session_state.pack_dir) == Path(demo_dir):
        st.divider()
        _render_pack(Path(st.session_state.pack_dir))

# ==== ABOUT ===============================================================
with tab_about:
    st.subheader("About Compass")
    st.markdown(
        """
Compass turns *"first productive commit in weeks"* into *"first productive commit in hours."*

**Four Bob subagents** run in sequence:

1. **Scout** — inventory (languages, package managers, entry points).
2. **Cartographer** — architecture map with evidence for every edge.
3. **DevLoopRunner** — install / build / test commands Bob *actually ran*.
4. **Guide** — reading tour + three starter tasks with hint ladders.

**Sources it accepts:** any Git URL, a local folder (with a native picker on this machine), or a `.zip` upload.
Local paths and ZIPs are copied into an isolated workspace — your originals are never touched.

**One token, one place.** The only credential Compass needs is `BOB_API_KEY`
(from `bob.ibm.com → API keys`). Paste it into the sidebar. There is no
separate "Compass token" for the UI.

**Deploy:** see `DEPLOY.md` in the repo. The recommended path is the
`scripts/serve_public.ps1` launcher: it starts Streamlit + a Cloudflare
Quick Tunnel and prints a public `https://…trycloudflare.com` URL — the link
to paste into your lablab submission.
        """
    )
