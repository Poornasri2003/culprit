"""Render the submission slide deck to docs/Culprit_slides.pdf (16:9, 12 slides)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
DOCS = Path(__file__).resolve().parent.parent / "docs"
FONTS = Path("C:/Windows/Fonts")
BG_TOP, BG_BOTTOM = (11, 16, 32), (20, 30, 58)
TEXT, MUTED = (236, 241, 250), (150, 164, 190)
BLUE, RED, GREEN, AMBER = (69, 137, 255), (255, 107, 107), (66, 214, 140), (255, 189, 46)
PANEL, EDGE = (14, 20, 38), (48, 64, 102)
TOTAL = 12


def F(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


REG, BOLD, MONO, MONO_B = "segoeui.ttf", "segoeuib.ttf", "consola.ttf", "consolab.ttf"


def canvas(title: str, num: int):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    if title:
        d.text((120, 90), title, font=F(BOLD, 72), fill=TEXT)
        d.rectangle([120, 190, 260, 197], fill=BLUE)
    d.text((120, H - 70), "Culprit  ·  IBM Bob 2.0 Hackathon", font=F(REG, 24), fill=MUTED)
    d.text((W - 160, H - 70), f"{num} / {TOTAL}", font=F(REG, 24), fill=MUTED)
    return img, d


def bullets(d, items, x=120, y=260, size=40, gap=78, colour=TEXT):
    for item in items:
        d.ellipse([x, y + size * 0.45, x + 14, y + size * 0.45 + 14], fill=BLUE)
        d.text((x + 40, y), item, font=F(REG, size), fill=colour)
        y += gap
    return y


def panel(d, x, y, w, h, lines, size=30, step=48):
    d.rounded_rectangle([x, y, x + w, y + h], radius=22, fill=PANEL, outline=EDGE, width=2)
    ly = y + 36
    for text, colour, bold in lines:
        d.text((x + 36, ly), text, font=F(MONO_B if bold else MONO, size), fill=colour)
        ly += step


def box(d, x, y, w, h, head, body, colour=BLUE):
    d.rounded_rectangle([x, y, x + w, y + h], radius=20, fill=PANEL, outline=colour, width=3)
    d.text((x + 30, y + 24), head, font=F(BOLD, 36), fill=colour)
    ly = y + 90
    for line in body:
        d.text((x + 30, ly), line, font=F(REG, 28), fill=TEXT)
        ly += 44


def slide1():
    img, d = canvas("", 1)
    d.text((120, 250), "CULPRIT", font=F(BOLD, 170), fill=TEXT)
    d.rectangle([120, 460, 320, 469], fill=BLUE)
    d.text((120, 510), "The AI debugger that proves the bug,", font=F(REG, 60), fill=TEXT)
    d.text((120, 590), "then proves the fix.", font=F(REG, 60), fill=TEXT)
    d.text((120, 720), "Live probe  →  failing test  →  root cause  →  tested fix  →  commit",
           font=F(REG, 34), fill=MUTED)
    d.text((120, 800), "Built with IBM Bob IDE  ·  Runs on IBM Bob Shell  ·  watsonx Orchestrate agent",
           font=F(BOLD, 32), fill=BLUE)
    d.text((120, 870), "github.com/Poornasri2003/culprit", font=F(MONO, 30), fill=MUTED)
    return img


def slide2():
    img, d = canvas("The problem: silent bugs in running services", 2)
    y = bullets(d, [
        "Developers spend 35-50% of their time validating and debugging (ACM Queue)",
        "The costliest bugs live in production: the service answers 200 OK, but wrong",
        "No crash, no stack trace, tests green; the cause often sits in another codebase",
        "Fixing by hand: reproduce, read several repos, fix, prove, add a test (hours)",
        "Code-only or log-only AI assistants guess; they never check the live behaviour",
    ], size=36, gap=78)
    d.text((120, y + 20), "Example (our demo):", font=F(BOLD, 32), fill=MUTED)
    panel(d, 120, y + 70, 1680, 150, [
        ("$ curl -X POST /cart/total -d '{\"items\":[{\"price\":20,\"qty\":2}],\"discount_code\":\"SAVE10\"}'", MUTED, False),
        ('{"total": 32.4}      <- documented rule says 33.2', RED, True),
    ], size=30)
    return img


def slide3():
    img, d = canvas("Demo: wrong answer to committed fix in 63 s", 4)
    panel(d, 120, 250, 1680, 560, [
        ("$ culprit debug --url http://localhost:8000/cart/total --method POST --body ... \\", MUTED, False),
        ('      --auth-bearer --folder frontend --folder backend --folder shared \\', MUTED, False),
        ('      --issue "cart total wrong when discount code applied"', MUTED, False),
        ("   0.3s   probe live URL          HTTP 200 {\"total\":32.4}", TEXT, False),
        ("   0.3s   Reproducer || CauseTracer   (2 IBM Bob subagents in parallel)", TEXT, False),
        ("  37.7s   repro test FAILS on current code  -> bug proven", TEXT, False),
        ("  38.6s   FixAuthor: 3 ranked fixes (exact edits)", TEXT, False),
        ("  47.5s   Guard: permanent regression test", TEXT, False),
        ("  62.9s   all tests PASS -> commit", GREEN, True),
        ("  Status: FIXED   63s   0.21 Bobcoins   root cause shared/pricing.py:28", GREEN, True),
    ], size=30, step=52)
    d.text((120, 850), "Restart the service:  {\"total\": 33.2}  (correct)", font=F(BOLD, 40), fill=GREEN)
    return img


def slide4():
    img, d = canvas("Agents, subagents and tools", 7)
    box(d, 120, 250, 520, 250, "Entry points", ["Terminal: culprit debug", "Orchestrate agent", "culprit_oncall (chat)"], BLUE)
    box(d, 700, 250, 520, 250, "Orchestrator", ["Plain Python, no AI", "3 attempts, 240 s,", "Bobcoin cap"], AMBER)
    box(d, 1280, 250, 520, 250, "Tools (code)", ["HTTP probe  ·  pytest", "git apply / revert", "/ commit"], GREEN)
    box(d, 120, 560, 1680, 300, "IBM Bob subagents (Bob Shell, headless, read-only ask mode)", [
        "Reproducer  →  pytest that must FAIL on the current code",
        "CauseTracer  →  file, line, explanation, confidence (runs in parallel with Reproducer)",
        "FixAuthor  →  up to 3 fixes, lowest risk first, as exact search/replace edits",
        "Guard  →  permanent regression test in the project's own style",
    ], BLUE)
    d.text((120, 900), "The test suite is the judge: nothing is committed unless every test passes.",
           font=F(BOLD, 36), fill=GREEN)
    return img


def slide5():
    img, d = canvas("How we used IBM Bob", 8)
    box(d, 120, 250, 820, 600, "1. Built with Bob IDE", [
        "SPEC.md first, then 7 Bob prompts,",
        "one architecture layer at a time:",
        "• Plan mode: ARCHITECTURE.md",
        "• Agent mode: the demo app with the bug",
        "• models, infrastructure, Bob client,",
        "  4 subagents, orchestrator, CLI",
        "• Every task exported to bob_sessions/",
        "• Reviewed + verified with real Bob runs",
        "• About 19 of 40 Bobcoins used",
    ], BLUE)
    box(d, 980, 250, 820, 600, "2. Bob inside Culprit", [
        "All 4 subagents are IBM Bob, via",
        "Bob Shell (headless):",
        "bob run --format json --mode ask",
        "   --max-cost <remaining budget>",
        "• Reads code across all codebases",
        "• Read-only: Culprit applies the edits",
        "• Bobcoin cap enforced twice",
        "• About 0.2 Bobcoins per full fix",
        "• Keys never logged, headers redacted",
    ], GREEN)
    return img


def slide6():
    img, d = canvas("Chat-triggered debugging: watsonx Orchestrate", 9)
    bullets(d, [
        "Agent culprit_oncall lets an on-call engineer fix a bug from chat",
        "Two OpenAPI tools: start_culprit_debug(issue), get_culprit_debug_result(job_id)",
        "They call culprit serve: a bearer-token-protected FastAPI service",
        "Asynchronous by design: a run (about 60 s) exceeds Orchestrate's 40 s tool limit",
        "Callers choose only the issue text; the project and URL are fixed server-side",
        "Tool endpoints tested end to end over a public tunnel: FIXED in 73 s",
        "One command deploys the agent: scripts/orchestrate_deploy.py",
    ], size=36, gap=80)
    panel(d, 120, 850, 1680, 110, [
        ("Orchestrate chat -> agent -> tools -> tunnel -> culprit serve -> Bob Shell", MUTED, False),
    ], size=30)
    return img


def slide7():
    img, d = canvas("Why it's different, and why it matters", 11)
    box(d, 120, 250, 820, 330, "Proof, not guesses", [
        "Bug must be reproduced by a failing",
        "assertion before any fix",
        "Fix is kept only if every test passes;",
        "otherwise files are restored exactly",
    ], GREEN)
    box(d, 980, 250, 820, 330, "Runtime + code evidence", [
        "Probes the live service, then traces",
        "across several codebases",
        "Finds bugs that return 200 OK",
        "with the wrong value",
    ], BLUE)
    box(d, 120, 620, 820, 300, "Business value", [
        "About 1 hour of debugging -> about 60 s",
        "Every fix ships with a regression test",
        "Costs about 0.2 Bobcoins per bug",
    ], AMBER)
    box(d, 980, 620, 820, 300, "Safe by design", [
        "Bob is read-only; code applies edits",
        "Hard Bobcoin budget per run",
        "Secrets only in .env, redacted",
    ], BLUE)
    return img


def slide8():
    img, d = canvas("Results and what's next", 12)
    stats = [("FIXED", "live demo bug"), ("~60 s", "bug to commit"), ("0.21", "Bobcoins per run"), ("127", "unit tests")]
    x = 120
    for big, small in stats:
        d.text((x, 260), big, font=F(BOLD, 80), fill=GREEN)
        d.text((x, 370), small, font=F(REG, 30), fill=MUTED)
        x += 430
    d.text((120, 480), "Next steps", font=F(BOLD, 44), fill=TEXT)
    bullets(d, [
        "Clone codebases from --git; JavaScript/TypeScript test runners",
        "Open a GitHub pull request instead of a local commit",
        "Deploy the Orchestrate agent for team-wide, chat-first on-call",
    ], y=560, size=36, gap=74)
    d.text((120, 820), "github.com/Poornasri2003/culprit", font=F(MONO_B, 40), fill=BLUE)
    d.text((120, 880), "Thank you!", font=F(BOLD, 48), fill=TEXT)
    return img



def arrow(d, x1, y1, x2, y2, colour=MUTED, width=4):
    import math
    d.line([(x1, y1), (x2, y2)], fill=colour, width=width)
    ang = math.atan2(y2 - y1, x2 - x1)
    for s_ in (0.45, -0.45):
        d.line([(x2, y2), (x2 - 22 * math.cos(ang + s_), y2 - 22 * math.sin(ang + s_))], fill=colour, width=width)


def node(d, x, y, w, h, title, sub="", colour=BLUE, fill=PANEL):
    """Box with a centred title and optional sub-lines separated by '|'."""
    d.rounded_rectangle([x, y, x + w, y + h], radius=16, fill=fill, outline=colour, width=3)
    tf = F(BOLD, 28)
    tw = d.textlength(title, font=tf)
    d.text((x + (w - tw) / 2, y + (18 if sub else (h - 36) / 2)), title, font=tf, fill=colour)
    if sub:
        sf = F(REG, 22)
        for i, line in enumerate(sub.split("|")):
            lw = d.textlength(line, font=sf)
            d.text((x + (w - lw) / 2, y + 58 + i * 28), line, font=sf, fill=TEXT)


def slide_arch(num):
    img, d = canvas("Architecture", num)
    node(d, 120, 240, 420, 130, "watsonx Orchestrate", "agent culprit_oncall|2 OpenAPI tools", BLUE)
    node(d, 120, 400, 420, 110, "culprit serve", "FastAPI · bearer token · async jobs", BLUE)
    node(d, 620, 240, 420, 130, "Terminal", "culprit debug --url ...|Click + Rich live view", BLUE)
    arrow(d, 330, 370, 330, 400)
    node(d, 120, 560, 920, 110, "Orchestrator (plain Python)", "self-healing loop · 3 attempts · 240 s · Bobcoin cap", AMBER)
    arrow(d, 330, 510, 330, 560)
    arrow(d, 830, 370, 830, 560)
    node(d, 1120, 240, 680, 90, "IBM Bob Shell (headless, ask mode)", "", GREEN)
    agents = (("Reproducer", "failing pytest"), ("CauseTracer", "root cause"),
              ("FixAuthor", "3 ranked fixes"), ("Guard", "regression test"))
    for i, (t, sub) in enumerate(agents):
        node(d, 1120 + (i % 2) * 350, 360 + (i // 2) * 150, 330, 120, t, sub, GREEN)
    arrow(d, 1040, 600, 1120, 470, GREEN)
    tools = (("HttpClient", "probe live URL"), ("TestRunner", "pytest"), ("GitOps", "apply · revert · commit"))
    for i, (t, sub) in enumerate(tools):
        node(d, 120 + i * 310, 740, 290, 110, t, sub, MUTED)
        arrow(d, 265 + i * 310, 670, 265 + i * 310, 740)
    node(d, 1120, 740, 680, 130, "Target project", "frontend · backend · shared + tests|live service on :8000", RED)
    arrow(d, 1030, 795, 1120, 795, RED)
    return img


def slide_flow(num):
    img, d = canvas("Flow: from bug report to commit", num)
    steps = [("1", "Probe", "live URL|HTTP 200 · 32.4", BLUE),
             ("2", "Reproduce", "Bob writes a test|that must FAIL", GREEN),
             ("3", "Trace", "Bob: file, line,|confidence", GREEN),
             ("4", "Fix", "Bob: 3 ranked|exact edits", GREEN),
             ("5", "Guard", "Bob: permanent|regression test", GREEN),
             ("6", "Test", "whole suite|must PASS", AMBER),
             ("7", "Commit", "fix + test|in git", BLUE)]
    x, y, w, h = 120, 300, 215, 150
    for i, (n, t, sub, c) in enumerate(steps):
        bx = x + i * 245
        d.ellipse([bx + w / 2 - 28, y - 70, bx + w / 2 + 28, y - 14], fill=c)
        nw = d.textlength(n, font=F(BOLD, 32))
        d.text((bx + w / 2 - nw / 2, y - 64), n, font=F(BOLD, 32), fill=(11, 16, 32))
        node(d, bx, y, w, h, t, sub, c)
        if i < len(steps) - 1:
            arrow(d, bx + w + 4, y + h / 2, bx + 241, y + h / 2)
    d.text((120, 540), "Gates (decided by code, never by AI):", font=F(BOLD, 36), fill=TEXT)
    bullets(d, [
        "Repro test passes on the current code  ->  NEEDS_HUMAN  (bug not proven)",
        "Confidence below 0.70  ->  NEEDS_HUMAN",
        "Tests fail after the fix  ->  revert files, retry with the failure as context (max 3)",
        "Bobcoin budget used up  ->  PARTIAL",
    ], y=610, size=32, gap=62)
    return img


def slide_tech(num):
    img, d = canvas("Tech stack", num)
    cols = [
        ("IBM", BLUE, ["IBM Bob IDE (Plan + Agent mode)", "IBM Bob Shell 2.0.5 (headless)",
                       "watsonx Orchestrate (agent,", "  OpenAPI tools, ADK 2.17)"]),
        ("Core (Python 3.11+)", GREEN, ["Click · Rich (CLI + live view)", "Pydantic v2 (domain models)",
                                        "anyio (parallel subagents)", "httpx (live probe)",
                                        "GitPython (commit / revert)", "pytest (runner + 127 tests)"]),
        ("Service & demo", AMBER, ["FastAPI + uvicorn (culprit serve)", "Cloudflare tunnel",
                                   "Flask (demo shop services)", "python-dotenv (.env secrets)"]),
    ]
    x = 120
    for head, c, items in cols:
        box(d, x, 250, 540, 400, head, items, c)
        x += 580
    d.text((120, 700), "Review: Claude Code  ·  License: MIT  ·  github.com/Poornasri2003/culprit",
           font=F(REG, 30), fill=MUTED)
    return img


def slide_solution(num):
    img, d = canvas("The solution: Culprit", num)
    d.text((120, 240), "Give it a running service, the code behind it, and one sentence about the bug.",
           font=F(REG, 38), fill=TEXT)
    steps = [("Observe", "calls the live service|and records what it does", BLUE),
             ("Prove", "IBM Bob writes a test|that fails on the bug", GREEN),
             ("Explain", "IBM Bob traces the root|cause across codebases", GREEN),
             ("Fix", "IBM Bob proposes ranked|minimal fixes", GREEN),
             ("Verify", "whole test suite must pass,|else revert and retry", AMBER),
             ("Deliver", "commit fix + regression|test, with a report", BLUE)]
    for i, (t, sub, c) in enumerate(steps):
        x = 120 + (i % 3) * 580
        y = 370 + (i // 3) * 200
        node(d, x, y, 540, 150, t, sub, c)
    d.text((120, 800), "Who it's for: backend, platform and on-call engineers of multi-service systems.",
           font=F(REG, 34), fill=MUTED)
    d.text((120, 860), "How: one CLI command, an HTTP API, or a watsonx Orchestrate chat agent.",
           font=F(REG, 34), fill=MUTED)
    return img



def main() -> None:
    slides = [slide1(), slide2(), slide_solution(3), slide3(), slide_flow(5), slide_arch(6),
              slide4(), slide5(), slide6(), slide_tech(10), slide7(), slide8()]
    DOCS.mkdir(exist_ok=True)
    out = DOCS / "Culprit_slides.pdf"
    slides[0].save(out, save_all=True, append_images=slides[1:], resolution=144)
    for i, s in enumerate(slides, 1):
        s.save(DOCS / f"slide_{i}.png")
    print(f"Saved {out} ({len(slides)} slides)")


if __name__ == "__main__":
    main()
