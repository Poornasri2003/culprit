"""Turns an OnboardingPack into the Markdown + JSON files under output_dir/."""
from __future__ import annotations

from pathlib import Path

from compass.domain.models import OnboardingPack


def write_pack(pack: OnboardingPack, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    notes = output_dir / "notes"
    notes.mkdir(exist_ok=True)

    # Human-readable
    (output_dir / "OVERVIEW.md").write_text(_overview(pack), encoding="utf-8")
    (output_dir / "ARCHITECTURE.md").write_text(_architecture(pack), encoding="utf-8")
    (output_dir / "DEV_LOOP.md").write_text(_devloop(pack), encoding="utf-8")
    (output_dir / "TOUR.md").write_text(_tour(pack), encoding="utf-8")
    (output_dir / "STARTER_TASKS.md").write_text(_starter_tasks(pack), encoding="utf-8")

    # Machine-readable — used by /ask.
    for name, obj in {
        "inventory": pack.inventory,
        "architecture": pack.architecture,
        "devloop": pack.devloop,
        "guide": pack.guide,
    }.items():
        (notes / f"{name}.json").write_text(obj.model_dump_json(indent=2), encoding="utf-8")

    (output_dir / "report.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")


def _overview(p: OnboardingPack) -> str:
    inv = p.inventory
    lines = [
        f"# {inv.name}",
        "",
        inv.description,
        "",
        "## At a glance",
        f"- Repo: `{p.repo}`",
        "- Languages: " + ", ".join(f"{lang.name} ({lang.percent:.0f}%)" for lang in inv.languages),
        "- Package managers: " + (", ".join(inv.package_managers) or "—"),
        f"- Tracked files: {inv.tracked_file_count}",
        "",
        "## Entry points",
    ]
    for ep in inv.entry_points:
        lines.append(f"- **{ep.kind}** — `{ep.file.path}` — {ep.hint}")
    return "\n".join(lines) + "\n"


def _architecture(p: OnboardingPack) -> str:
    a = p.architecture
    out = ["# Architecture", "", "```mermaid", a.mermaid, "```", "", "## Modules"]
    for m in a.modules:
        out.append(f"### `{m.name}` — {m.role}")
        out.append(f"Root: `{m.root}`")
        out.append("Exemplars: " + ", ".join(f"`{f.path}`" for f in m.exemplar_files))
        out.append("")
    out.append("## Spine paths")
    for sp in a.spine_paths:
        out.append(f"- **{sp.name}** — " + " → ".join(f"`{s.path}`" for s in sp.steps))
    return "\n".join(out) + "\n"


def _devloop(p: OnboardingPack) -> str:
    d = p.devloop
    out = ["# Dev loop", "", "## Prerequisites"]
    out += [f"- {pre}" for pre in d.prerequisites] or ["- (none)"]
    out += ["", "## Commands"]
    for c in d.commands:
        badge = {"ok": "OK", "flaky": "FLAKY", "failed": "FAILED", "skipped": "SKIPPED"}[c.verdict]
        out.append(f"### {c.label} — {badge}")
        out.append(f"```\n{c.cmd}\n```")
        out.append(f"exit={c.exit_code}  in {c.duration_seconds:.1f}s")
        if c.tail_stderr.strip():
            out.append(f"stderr tail:\n```\n{c.tail_stderr}\n```")
        out.append("")
    if d.notes:
        out += ["## Notes"] + [f"- {n}" for n in d.notes]
    return "\n".join(out) + "\n"


def _tour(p: OnboardingPack) -> str:
    out = ["# Reading tour", ""]
    for stop in sorted(p.guide.tour, key=lambda s: s.order):
        out.append(f"## {stop.order}. `{stop.file.path}`")
        out.append(f"**Why:** {stop.why}")
        out.append(f"**Notice:** {stop.notice}")
        out.append("")
    return "\n".join(out) + "\n"


def _starter_tasks(p: OnboardingPack) -> str:
    out = ["# Starter tasks", ""]
    for i, t in enumerate(p.guide.starter_tasks, start=1):
        out.append(f"## {i}. {t.title}  ({t.difficulty})")
        out.append("Files to touch: " + ", ".join(f"`{f.path}`" for f in t.files_to_touch))
        out.append("Acceptance criteria:")
        out += [f"- {ac}" for ac in t.acceptance_criteria]
        out.append("Hints:")
        out.append(f"- small — {t.hints.small}")
        out.append(f"- medium — {t.hints.medium}")
        out.append(f"- large — {t.hints.large}")
        out.append("")
    return "\n".join(out) + "\n"
