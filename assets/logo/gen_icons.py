"""Generate browsertrace brand icons (PNG) for the extension."""

import math
import os
from PIL import Image, ImageDraw

INDIGO = (99, 102, 241, 255)      # #6366f1
CYAN = (6, 182, 211, 255)         # #06b6d4
WHITE = (255, 255, 255, 255)
BG = (15, 23, 42, 255)            # #0f172a


def draw_logo(draw, cx, cy, scale):
    """Draw the browsertrace mark: eye ring + pupil + cursor trace."""
    r_outer = int(90 * scale)
    r_pupil = int(26 * scale)

    # Background circle
    draw.ellipse([cx - r_outer - 4, cy - r_outer - 4, cx + r_outer + 4, cy + r_outer + 4], fill=BG)

    # Eye ring (thick arc)
    ring_w = int(10 * scale)
    bbox = [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer]
    draw.arc(bbox, start=180, end=360, fill=INDIGO, width=ring_w)
    draw.arc(bbox, start=0, end=180, fill=CYAN, width=ring_w)

    # Pupil
    draw.ellipse([cx - r_pupil, cy - r_pupil, cx + r_pupil, cy + r_pupil], fill=WHITE)

    # Cursor dot above-left
    cursor_r = int(8 * scale)
    draw.ellipse([cx - int(36 * scale) - cursor_r, cy - int(36 * scale) - cursor_r,
                  cx - int(36 * scale) + cursor_r, cy - int(36 * scale) + cursor_r], fill=CYAN)

    # Trace trail from cursor toward pupil
    trail_w = int(6 * scale)
    draw.line([cx - int(36 * scale), cy - int(36 * scale), cx, cy], fill=WHITE, width=trail_w)

    # Secondary trace
    trail_w2 = int(4 * scale)
    draw.line([cx, cy, cx + int(14 * scale), cy + int(30 * scale)], fill=WHITE, width=trail_w2)


def make_icon(size, out_path):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    scale = size / 200.0
    draw_logo(draw, cx, cy, scale)
    img.save(out_path, "PNG")
    print(f"wrote {out_path} ({size}x{size})")


if __name__ == "__main__":
    icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension", "icons")
    os.makedirs(icons_dir, exist_ok=True)
    for s in (16, 48, 128):
        make_icon(s, os.path.join(icons_dir, f"icon-{s}.png"))
