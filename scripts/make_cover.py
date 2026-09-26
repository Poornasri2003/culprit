"""Render the 16:9 submission cover image to docs/cover.png (1920x1080)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
OUT = Path(__file__).resolve().parent.parent / "docs" / "cover.png"
FONTS = Path("C:/Windows/Fonts")

BG_TOP, BG_BOTTOM = (11, 16, 32), (20, 30, 58)
TEXT, MUTED = (236, 241, 250), (150, 164, 190)
BLUE, RED, GREEN = (69, 137, 255), (255, 107, 107), (66, 214, 140)
PANEL, PANEL_EDGE = (14, 20, 38), (48, 64, 102)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def main() -> None:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # vertical gradient
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))

    # Left: title block
    x = 120
    d.text((x, 150), "CULPRIT", font=font("segoeuib.ttf", 150), fill=TEXT)
    d.rectangle([x, 335, x + 180, 343], fill=BLUE)
    d.text((x, 380), "The AI debugger that proves", font=font("segoeui.ttf", 54), fill=TEXT)
    d.text((x, 448), "the bug, then proves the fix.", font=font("segoeui.ttf", 54), fill=TEXT)
    d.text((x, 560), "Live probe  →  failing test  →  root cause  →  tested fix  →  commit",
           font=font("segoeui.ttf", 30), fill=MUTED)

    # Badges
    bx, by = x, 660
    for label in ("IBM Bob", "Bob Shell subagents", "watsonx Orchestrate"):
        f = font("segoeuib.ttf", 30)
        w = d.textlength(label, font=f)
        d.rounded_rectangle([bx, by, bx + w + 48, by + 62], radius=31, outline=BLUE, width=3)
        d.text((bx + 24, by + 11), label, font=f, fill=TEXT)
        bx += w + 72

    # Stats
    sy = 800
    for big, small in (("~60 s", "bug to commit"), ("0.2", "Bobcoins per fix"), ("0", "untested fixes committed")):
        d.text((x, sy), big, font=font("segoeuib.ttf", 64), fill=GREEN)
        d.text((x, sy + 82), small, font=font("segoeui.ttf", 26), fill=MUTED)
        x += 320

    # Right: terminal panel
    px, py, pw, ph = 1180, 150, 620, 780
    d.rounded_rectangle([px, py, px + pw, py + ph], radius=24, fill=PANEL, outline=PANEL_EDGE, width=2)
    for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse([px + 30 + i * 34, py + 28, px + 50 + i * 34, py + 48], fill=c)
    mono, mono_b = font("consola.ttf", 27), font("consolab.ttf", 27)
    lines = [
        ("$ curl /cart/total  SAVE10", MUTED, mono),
        ('{"total": 32.4}   WRONG (33.2)', RED, mono_b),
        ("", TEXT, mono),
        ("$ culprit debug --url ... ", MUTED, mono),
        ("  probe      HTTP 200  32.4", TEXT, mono),
        ("  bob ‖ Reproducer + CauseTracer", TEXT, mono),
        ("  repro test FAILS -> bug proven", TEXT, mono),
        ("  root cause pricing.py:28", TEXT, mono),
        ("  bob  FixAuthor → Guard", TEXT, mono),
        ("  all tests PASS → commit", TEXT, mono),
        ("", TEXT, mono),
        ("  Status: FIXED   63s", GREEN, mono_b),
        ("", TEXT, mono),
        ('{"total": 33.2}   CORRECT', GREEN, mono_b),
    ]
    ly = py + 90
    for text, colour, f in lines:
        d.text((px + 34, ly), text, font=f, fill=colour)
        ly += 46

    OUT.parent.mkdir(exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"Saved {OUT} ({W}x{H})")


if __name__ == "__main__":
    main()
