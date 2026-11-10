"""Render the pixel-art link buttons that sit between the card and the board.

Writes link_<name>_<theme>.svg for portfolio, Telegram and Discord, in both
themes.

GitHub shows README SVGs through <img>, so nothing inside one is clickable and
nothing reacts to hover. Each button therefore has to be its own image inside
its own link. To still read as one row, every image is exactly a third of the
card's width and the README sizes each one at a third of the column; the gaps
are drawn inside the images, so the outer edges line up with the card and the
board.

Colours and type come from build_card, the sprite helpers from build_pacman:
one palette, one pixel grid, one font.

Usage:
    python tools/build_links.py
"""

from __future__ import annotations

from pathlib import Path

from build_card import ADVANCE, LINE_MID, THEMES
from build_pacman import CANVAS_W, CLAUDE_CORAL, bitmap, loop, mix, sprite_path, validate_smil

ROOT = Path(__file__).resolve().parent.parent

PX = 3                         # one sprite pixel
GAP = 14                       # space between two buttons
SLOT_W = CANVAS_W / 3          # each image: a third of the row
BUTTON_W = (CANVAS_W - 2 * GAP) / 3
HEIGHT = 20 * PX
LABEL_FONT = 18
ICON_GAP = 12                  # between the icon and its label
OUTLINE_MIX = 0.18             # outline: this far from the background toward the text

# Button i starts at i * (BUTTON_W + GAP) on the row and its image at
# i * SLOT_W, so inside its own image it sits i * GAP / 3 from the left edge.
# The first touches the left edge, the last the right one.
assert BUTTON_W % PX == 0, "the button must be a whole number of pixels wide"

# --- Sprites ----------------------------------------------------------------
MONITOR = """
##########
#........#
#.#......#
#..#.....#
#.#......#
#........#
##########
....##....
..######..
"""

MONITOR_CURSOR = """
..........
..........
..........
..........
....###...
..........
..........
..........
..........
"""

# Telegram's paper plane, nose up-right, with the fold cut out of the wing.
TELEGRAM = """
......####
.....#####
....##.###
...##..###
..##...###
.##....###
##.....###
#......###
"""

# Discord's Clyde face: two ear bumps on top, two eye holes, tapered chin.
DISCORD = """
.##....##.
##########
##########
###.##.###
##########
##########
.########.
..######..
"""

# Each icon has its own idle move, as each Claude on the board has its own
# gait: the monitor's cursor blinks, the plane flies up-right one pixel, and
# Discord rattles like a notification.
CURSOR_BLINK = 1.06            # the card's prompt cursor, same cadence
FLY_EVERY = 2.0                # the plane jumps one pixel up-right, once per this many seconds
RATTLE_EVERY = 2.4             # Discord shakes, once per this many seconds


def path(art: str) -> str:
    return sprite_path(bitmap(art), PX, PX)


def twitch(offsets: list[tuple[int, int]], times: list[float], dur: float) -> str:
    """Whole-pixel jumps (in PX units) at the given fractions of a dur-second cycle."""
    values = ";".join(f"{dx * PX},{dy * PX}" for dx, dy in offsets)
    return (f'<animateTransform attributeName="transform" type="translate" '
            f'values="{values}" keyTimes="{";".join(f"{t:g}" for t in times)}" dur="{dur}s" '
            f'calcMode="discrete" repeatCount="indefinite"/>')


def monitor() -> str:
    return (f'<path d="{path(MONITOR)}"/>'
            f'<path d="{path(MONITOR_CURSOR)}">{loop("opacity", ["1", "0"], CURSOR_BLINK)}</path>')


def telegram() -> str:
    fly = twitch([(0, 0), (1, -1), (0, 0)], [0, 0.8, 0.9], FLY_EVERY)
    return f'<g>{fly}<path d="{path(TELEGRAM)}"/></g>'


def discord() -> str:
    rattle = twitch([(0, 0), (-1, 0), (1, 0), (-1, 0), (1, 0), (0, 0)],
                    [0, 0.7, 0.74, 0.78, 0.82, 0.86], RATTLE_EVERY)
    return f'<g>{rattle}<path d="{path(DISCORD)}"/></g>'


# (file name, label, accent, icon)
LINKS = (
    ("portfolio", "PORTFOLIO", "green", monitor),
    ("telegram", "TELEGRAM", "telegram", telegram),
    ("discord", "DISCORD", "discord", discord),
)


def stepped(x: float, y: float, w: float, h: float) -> str:
    """A rectangle whose corners are cut by one pixel step."""
    return (f"M{x + PX:g} {y:g}h{w - 2 * PX:g}v{PX}h{PX}v{h - 2 * PX:g}h{-PX}v{PX}"
            f"h{-(w - 2 * PX):g}v{-PX}h{-PX}v{-(h - 2 * PX):g}h{PX}z")


def build(theme: str, slot: int) -> str:
    c = THEMES[theme]
    _, label, accent_key, icon = LINKS[slot]
    accent = CLAUDE_CORAL[theme] if accent_key == "coral" else c[accent_key]

    x = slot * GAP / 3
    icon_w = 10 * PX
    label_w = len(label) * LABEL_FONT * ADVANCE
    left = x + (BUTTON_W - icon_w - ICON_GAP - label_w) / 2

    # Centre on the area above the accent bar, not on the whole button.
    mid = (PX + HEIGHT - 2 * PX) / 2

    return f"""<?xml version='1.0' encoding='UTF-8'?>
<svg xmlns="http://www.w3.org/2000/svg" width="{SLOT_W:.3f}px" height="{HEIGHT}px"
     viewBox="0 0 {SLOT_W:.3f} {HEIGHT}" font-family="ConsolasFallback,Consolas,Menlo,monospace">
<style>
@font-face {{
  src: local('Consolas'), local('Consolas Bold');
  font-family: 'ConsolasFallback';
  font-display: swap;
  size-adjust: 109%;
}}
text {{white-space: pre;}}
.px {{shape-rendering: crispEdges;}}
</style>
<g class="px">
<path d="{stepped(x, 0, BUTTON_W, HEIGHT)}" fill="{mix(c['bg'], c['fg'], OUTLINE_MIX)}"/>
<path d="{stepped(x + PX, PX, BUTTON_W - 2 * PX, HEIGHT - 2 * PX)}" fill="{c['bg']}"/>
<rect x="{x + 2 * PX:g}" y="{HEIGHT - 2 * PX}" width="{BUTTON_W - 4 * PX:g}" height="{PX}" fill="{accent}"/>
<g fill="{accent}" transform="translate({left + icon_w / 2:.2f},{mid:g})">{icon()}</g>
</g>
<text x="{left + icon_w + ICON_GAP:.2f}" y="{mid + LINE_MID * LABEL_FONT:.2f}" font-size="{LABEL_FONT}px" fill="{c['fg']}">{label}</text>
</svg>
"""


def main() -> None:
    print(f"row      : {CANVAS_W}px = 3 x {BUTTON_W:g}px buttons + 2 x {GAP}px gaps")
    for theme in THEMES:
        for slot, (name, *_) in enumerate(LINKS):
            svg = build(theme, slot)
            checked = validate_smil(svg)
            out = ROOT / f"link_{name}_{theme}.svg"
            out.write_text(svg, encoding="utf-8")
            print(f"wrote {out.name} ({out.stat().st_size:,} bytes, {checked} animations validated)")


if __name__ == "__main__":
    main()
