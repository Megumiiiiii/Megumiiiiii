"""Render the contribution calendar with a Pac-Man eating it.

Reads cache/calendar.json (written by tools/fetch_stats.py) and writes
pacman_dark.svg / pacman_light.svg next to the card SVGs.

The board keeps GitHub's calendar format -- 53 weeks by 7 days of rounded
squares, months across the top, Mon/Wed/Fri down the side, the Less/More
legend -- so it still reads as your contribution graph. The retro part is
confined to what moves: pixel-art sprites, and a header that counts up like an
arcade score as Pac-Man eats each day.

Design rules:

- Colours are imported from build_card.THEMES. The level ramp is a blend
  between the card's own background and foreground, so the calendar is a
  greyscale version of GitHub's, drawn in the card's palette. Pac-Man is the
  card's accent. Change the card and the board follows. The one exception is
  CLAUDE_CORAL, which belongs to the character rather than the card.
- Every sprite is one <path> on a single pixel grid (U px per pixel), so
  neighbouring pixels never show anti-aliased seams.
- Every long animation shares one duration and begins at 0, so the whole
  board stays in lock-step forever with no scripting.

Usage:
    python tools/build_pacman.py
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from build_card import ADVANCE, THEMES

ROOT = Path(__file__).resolve().parent.parent
CALENDAR_SRC = ROOT / "cache" / "calendar.json"

# --- Geometry (px) ----------------------------------------------------------
U = 2                      # one sprite pixel
CELL = 14                  # a day's square
PITCH = 18                 # day-to-day spacing; the 14/18 ratio matches GitHub's
RADIUS = 3
CANVAS_W = 1018            # matches the card, so the two stack as one unit
MARGIN = 15
LABEL_COL = 38             # room for Mon/Wed/Fri left of the grid
HEADER_FONT = 14
LABEL_FONT = 12

# Share of the way from the card's background to its foreground, per level.
LEVEL_MIX = (0.10, 0.30, 0.50, 0.72, 1.0)

# --- Timing (s) -------------------------------------------------------------
STEP = 0.075               # time to cross one day
INTRO = 1.5                # full calendar, Pac-Man waiting, before he moves
OUTRO = 1.8                # empty calendar and the final total, before the reset
EAT_AHEAD = 5              # px: a day vanishes as the mouth reaches it
CHOMP = 0.24               # one open-half-shut-half mouth cycle

# The chasers: three Claudes, each trailing Pac-Man by some days and each with
# its own trait on top of the walk. Stride lengths differ on purpose -- three
# identical loops started together would march in perfect lock-step.
CAST = (
    # (days behind, trait, seconds per stride)
    (5, "blink", 0.34),
    (8, "wave", 0.30),
    (11, "hop", 0.38),
)
TRAIL = tuple(lag for lag, _, _ in CAST)
WAVE = 0.28                # one arms-down, arms-up cycle
BLINK_EVERY = 2.4          # eyes shut for a beat once per this many seconds

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
WEEKDAYS = {1: "Mon", 3: "Wed", 5: "Fri"}

# --- Sprites ----------------------------------------------------------------
PAC_OPEN = """
..#####..
.######..
######...
#####....
#####....
#####....
######...
.######..
..#####..
"""
PAC_HALF = """
..#####..
.#######.
#########
#######..
#####....
#######..
#########
.#######.
..#####..
"""
PAC_SHUT = """
..#####..
.#######.
#########
#########
#########
#########
#########
.#######.
..#####..
"""
# The Claude Code mascot, drawn from the reference image supplied rather than
# from the terminal logo ( ▐▛███▜▌ / ▝▜█████▛▘ / ▘▘ ▝▝ ). The silhouette is the
# same, but the logo's eyes are the quadrant missing from ▛ and ▜ -- taller
# than wide, so they read as vertical slits -- whereas the mascot has thin
# horizontal eyes. Doubling the logo's rows on square pixels leaves room for
# them. Proportions measured off the reference: eyes ~29% down the body, legs
# ~13% of its width, a three-column gap between the inner legs.
#
# Split into layers so each animates on its own, without drawing every
# combination of arms, legs and eyelids.
CLAWD_BODY = """
..############..
..############..
..#..######..#..
..############..
..############..
..############..
..############..
..############..
................
................
"""
CLAWD_ARMS_DOWN = """
................
................
................
................
##............##
##............##
................
................
................
................
"""
CLAWD_ARMS_UP = """
................
................
##............##
##............##
................
................
................
................
................
................
"""
# Two strides: one pair of legs planted while the other is lifted a pixel.
CLAWD_LEGS_A = """
................
................
................
................
................
................
................
................
..##.##..##.##..
..##........##..
"""
CLAWD_LEGS_B = """
................
................
................
................
................
................
................
................
..##.##..##.##..
.....##..##.....
"""
CLAWD_LIDS = """
................
................
...##......##...
................
................
................
................
................
................
................
"""
CLAWD_PX = (1.5, 1.5)

# The one colour declared here, because it belongs to the character and not to
# the card -- as Pac-Man's yellow belonged to him in the arcade. In the card's
# grey the Claudes read as generic Space Invaders; Claude's own coral is what
# makes them Claude. On white that coral is 3.1:1, barely over the 3:1 floor
# for graphics, so the light theme takes the darker terracotta at 4.2:1.
CLAUDE_CORAL = {"dark": "#D97757", "light": "#C15F3C"}


def bitmap(art: str) -> list[str]:
    return [r for r in art.strip("\n").split("\n") if r]


def sprite_path(rows: list[str], pw: float = U, ph: float = U) -> str:
    """Merge a bitmap into one path of horizontal pixel runs, centred on 0,0."""
    h, w = len(rows), max(len(r) for r in rows)
    x0, y0 = -w * pw / 2, -h * ph / 2
    d = []
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            if row[x] != "#":
                x += 1
                continue
            run = x
            while run < len(row) and row[run] == "#":
                run += 1
            d.append(f"M{x0 + x * pw:g} {y0 + y * ph:g}h{(run - x) * pw:g}v{ph:g}h{-(run - x) * pw:g}z")
            x = run
    return "".join(d)


def mix(a: str, b: str, t: float) -> str:
    """Blend two #rrggbb colours: t=0 is a, t=1 is b."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


# --- Timeline ---------------------------------------------------------------
class Timeline:
    """Pac-Man's route: a zigzag across the seven weekday rows, as timed waypoints."""

    def __init__(self, weeks: int, left: float, top: float) -> None:
        self.left, self.top = left, top
        self.speed = PITCH / STEP

        pts = []
        for r in range(7):
            cols = (0, weeks - 1) if r % 2 == 0 else (weeks - 1, 0)
            pts += [(self.cx(cols[0]), self.cy(r)), (self.cx(cols[1]), self.cy(r))]
        # run off the right edge far enough that the last Claude clears it too
        pts.append((CANVAS_W + (max(TRAIL) + 2) * PITCH, self.cy(6)))
        self.points = pts

        self.dist = [0.0]
        for a, b in zip(pts, pts[1:]):
            self.dist.append(self.dist[-1] + abs(b[0] - a[0]) + abs(b[1] - a[1]))

        self.done = INTRO + (self.dist[-1] + max(TRAIL) * PITCH) / self.speed
        self.total = self.done + OUTRO

    def cx(self, col: int) -> float:
        return self.left + col * PITCH + CELL / 2

    def cy(self, row: int) -> float:
        return self.top + row * PITCH + CELL / 2

    def k(self, t: float) -> str:
        """A keyTime: seconds on the master clock as a 0..1 fraction."""
        return f"{min(max(t / self.total, 0.0), 1.0):.5f}"

    def eat_time(self, col: int, row: int) -> float:
        seg = row * 2
        s = self.dist[seg] + abs(self.cx(col) - self.points[seg][0])
        return INTRO + max(0.0, s - EAT_AHEAD) / self.speed

    def motion(self, lag_cells: int = 0):
        """Keyframes and headings for a sprite trailing Pac-Man by lag_cells.

        Returns (keys, heads): keys is [(t, (x, y))] for the translate, heads is
        [(t, degrees)] for the facing. Both start at t=0 -- a discrete SMIL
        animation whose first keyTime is not 0 is silently discarded.
        """
        lag = lag_cells * PITCH
        start = (self.points[0][0] - lag, self.points[0][1])
        route = [start] + self.points
        dist = [0.0] + [d + lag for d in self.dist]

        keys = [(0.0, start)]
        for d, p in zip(dist, route):
            t = INTRO + d / self.speed
            if (t, p) != keys[-1]:
                keys.append((t, p))

        heads = []
        for (t, a), (_, b) in zip(keys, keys[1:]):
            if a != b:
                heads.append((t, 0 if b[0] > a[0] else 180 if b[0] < a[0] else 90))
        heads[0] = (0.0, heads[0][1])  # face the first move before making it
        return keys, heads


def anim_translate(tl: Timeline, keys) -> str:
    """Linear motion through keys, holding the last point until the loop ends."""
    last = keys[-1][1]
    values = ";".join(f"{x:g},{y:g}" for _, (x, y) in keys) + f";{last[0]:g},{last[1]:g}"
    times = ";".join(tl.k(t) for t, _ in keys) + ";1"
    return (f'<animateTransform attributeName="transform" type="translate" '
            f'values="{values}" keyTimes="{times}" dur="{tl.total:.3f}s" '
            f'repeatCount="indefinite"/>')


def anim_discrete(tl: Timeline, attr: str, values: list[str], times: list[float]) -> str:
    """A step function on the master clock: values[i] holds from times[i]."""
    return (f'<animate attributeName="{attr}" values="{";".join(values)}" '
            f'keyTimes="{";".join(tl.k(t) for t in times)}" dur="{tl.total:.3f}s" '
            f'calcMode="discrete" repeatCount="indefinite"/>')


def loop(attr: str, values: list[str], dur: float) -> str:
    """A short free-running cycle, independent of the master clock."""
    n = len(values)
    keys = ";".join(f"{i / n:.4f}" for i in range(n))
    return (f'<animate attributeName="{attr}" values="{";".join(values)}" '
            f'keyTimes="{keys}" dur="{dur}s" calcMode="discrete" repeatCount="indefinite"/>')


# --- Data -------------------------------------------------------------------
def load_calendar() -> dict:
    if not CALENDAR_SRC.exists():
        # Degrade gracefully instead of failing the whole workflow: an empty
        # board (all level 0) renders fine, and the next successful fetch
        # overwrites it. This matters because the stale cache shipped in the
        # fork belongs to the original author, not this profile -- deleting it
        # must not break the Pac-Man step.
        print(f"WARNING {CALENDAR_SRC} missing -- rendering an empty board",
              file=sys.stderr)
        return {"total": 0, "weeks": [[None] * 7 for _ in range(53)]}
    return json.loads(CALENDAR_SRC.read_text(encoding="utf-8"))


def month_labels(weeks: list[list]) -> list[tuple[int, str]]:
    labels, last = [], None
    for i, week in enumerate(weeks):
        day = next((d for d in week if d), None)
        if not day:
            continue
        m = dt.date.fromisoformat(day["date"]).month
        if m != last:
            labels.append((i, MONTHS[m - 1]))
            last = m
    # GitHub drops a label squeezed against the next one; so do we
    return [lab for j, lab in enumerate(labels)
            if j + 1 == len(labels) or labels[j + 1][0] - lab[0] >= 3]


# --- Render -----------------------------------------------------------------
def build(theme: str, cal: dict) -> str:
    c = THEMES[theme]
    weeks = cal["weeks"]
    ramp = [mix(c["bg"], c["fg"], t) for t in LEVEL_MIX]

    left = MARGIN + LABEL_COL
    header_y = MARGIN + HEADER_FONT
    months_y = header_y + 26
    top = months_y + 8
    grid_bottom = top + 6 * PITCH + CELL
    legend_y = grid_bottom + 24
    height = legend_y + MARGIN

    tl = Timeline(len(weeks), left, top)
    cells: list[str] = []
    sprites: list[str] = []

    # The empty grid: every day that exists, drawn at level 0.
    base = []
    for col, week in enumerate(weeks):
        for row, day in enumerate(week):
            if day:
                base.append(f'<use href="#d" x="{left + col * PITCH:g}" y="{top + row * PITCH:g}"/>')
    cells.append(f'<g fill="{ramp[0]}">{"".join(base)}</g>')

    # Days with contributions sit on top and vanish as Pac-Man eats them.
    eaten: list[tuple[float, int]] = []
    for col, week in enumerate(weeks):
        for row, day in enumerate(week):
            if not day or not day["level"]:
                continue
            t = tl.eat_time(col, row)
            eaten.append((t, day["count"]))
            cells.append(
                f'<use href="#d" x="{left + col * PITCH:g}" y="{top + row * PITCH:g}" '
                f'fill="{ramp[day["level"]]}">'
                f'{anim_discrete(tl, "visibility", ["visible", "hidden"], [0.0, t])}</use>'
            )

    # The Claudes, then Pac-Man on top.
    layer = {name: sprite_path(bitmap(art), *CLAWD_PX) for name, art in (
        ("body", CLAWD_BODY), ("arms_down", CLAWD_ARMS_DOWN), ("arms_up", CLAWD_ARMS_UP),
        ("legs_a", CLAWD_LEGS_A), ("legs_b", CLAWD_LEGS_B), ("lids", CLAWD_LIDS),
    )}

    def flip(a: str, b: str, dur: float) -> str:
        """Two layers taking turns: a shows first, then b, on a dur-second cycle."""
        return (f'<path d="{layer[a]}">{loop("opacity", ["1", "0"], dur)}</path>'
                f'<path d="{layer[b]}" opacity="0">{loop("opacity", ["0", "1"], dur)}</path>')

    for lag, trait, stride in CAST:
        keys, _ = tl.motion(lag)
        parts = [f'<path d="{layer["body"]}"/>', flip("legs_a", "legs_b", stride)]
        parts.append(flip("arms_down", "arms_up", WAVE) if trait == "wave"
                     else f'<path d="{layer["arms_down"]}"/>')
        if trait == "blink":
            parts.append(
                f'<path d="{layer["lids"]}" opacity="0"><animate attributeName="opacity" '
                f'values="0;1;0" keyTimes="0;0.93;0.98" dur="{BLINK_EVERY}s" '
                f'calcMode="discrete" repeatCount="indefinite"/></path>'
            )
        body = "".join(parts)
        if trait == "hop":
            # one pixel row up on every other half-stride, in step with the legs
            body = (f'<g><animateTransform attributeName="transform" type="translate" '
                    f'values="0,0;0,{-CLAWD_PX[1]:g}" keyTimes="0;0.5" dur="{stride}s" '
                    f'calcMode="discrete" repeatCount="indefinite"/>{body}</g>')
        sprites.append(f'<g>{anim_translate(tl, keys)}<g fill="{CLAUDE_CORAL[theme]}">{body}</g></g>')

    keys, heads = tl.motion(0)
    rot = (f'<animateTransform attributeName="transform" type="rotate" '
           f'values="{";".join(str(h) for _, h in heads)}" '
           f'keyTimes="{";".join(tl.k(t) for t, _ in heads)}" dur="{tl.total:.3f}s" '
           f'calcMode="discrete" repeatCount="indefinite"/>')
    idle = anim_discrete(tl, "visibility", ["visible", "hidden"], [0.0, INTRO])
    moving = anim_discrete(tl, "visibility", ["hidden", "visible"], [0.0, INTRO])
    frames = "".join(
        f'<path d="{sprite_path(bitmap(art))}">'
        f'{loop("opacity", [str(v) for v in vis], CHOMP)}</path>'
        for art, vis in ((PAC_OPEN, (1, 0, 0, 0)), (PAC_HALF, (0, 1, 0, 1)), (PAC_SHUT, (0, 0, 1, 0)))
    )
    sprites.append(
        f'<g>{anim_translate(tl, keys)}<g fill="{c["pac"]}">{rot}'
        f'<g>{idle}<path d="{sprite_path(bitmap(PAC_OPEN))}"/></g>'
        f'<g visibility="hidden">{moving}{frames}</g></g></g>'
    )

    # Header: GitHub's own sentence, with the number counting up as an arcade
    # score. Every value is padded to the final width, so in a monospace font
    # the words after it never shift.
    eaten.sort()
    steps, score = [(0.0, 0)], 0
    for t, count in eaten:
        if count:
            score += count
            steps.append((t, score))
    width = len(f"{cal['total']:,}")
    number = []
    for i, (t, value) in enumerate(steps):
        nxt = steps[i + 1][0] if i + 1 < len(steps) else None
        if i == 0:
            # No eaten days at all (empty board): a single static "0", no
            # animation -- the old code passed nxt=None into keyTimes and
            # crashed the whole Pac-Man step.
            vals, times = (["visible"], [0.0]) if nxt is None \
                else (["visible", "hidden"], [0.0, nxt])
        elif nxt is None:
            vals, times = ["hidden", "visible"], [0.0, t]
        else:
            vals, times = ["hidden", "visible", "hidden"], [0.0, t, nxt]
        number.append(
            f'<tspan x="{MARGIN:g}" visibility="{"visible" if i == 0 else "hidden"}">'
            f'{anim_discrete(tl, "visibility", vals, times)}{value:>{width},}</tspan>'
        )
    words_x = MARGIN + width * HEADER_FONT * ADVANCE

    months = "".join(f'<text x="{left + col * PITCH:g}" y="{months_y:g}">{name}</text>'
                     for col, name in month_labels(weeks))
    days = "".join(f'<text x="{MARGIN:g}" y="{tl.cy(row) + LABEL_FONT * 0.35:g}">{name}</text>'
                   for row, name in WEEKDAYS.items())

    # Legend, right-aligned under the grid like GitHub's.
    grid_right = left + (len(weeks) - 1) * PITCH + CELL
    more_w = 4 * LABEL_FONT * ADVANCE
    swatch_x = grid_right - more_w - 6 - (5 * PITCH - (PITCH - CELL))
    swatches = "".join(f'<rect x="{swatch_x + i * PITCH:g}" y="{legend_y - CELL + 3:g}" '
                       f'width="{CELL}" height="{CELL}" rx="{RADIUS}" fill="{ramp[i]}"/>'
                       for i in range(5))

    return f"""<?xml version='1.0' encoding='UTF-8'?>
<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}px" height="{height:g}px"
     font-family="ConsolasFallback,Consolas,Menlo,monospace">
<style>
@font-face {{
  src: local('Consolas'), local('Consolas Bold');
  font-family: 'ConsolasFallback';
  font-display: swap;
  size-adjust: 109%;
}}
text, tspan {{white-space: pre;}}
.px {{shape-rendering: crispEdges;}}
</style>
<defs><rect id="d" width="{CELL}" height="{CELL}" rx="{RADIUS}"/></defs>
<rect width="{CANVAS_W}px" height="{height:g}px" fill="{c['bg']}" rx="15"/>
<text y="{header_y:g}" font-size="{HEADER_FONT}px" fill="{c['fg']}">{"".join(number)}</text>
<text x="{words_x:g}" y="{header_y:g}" font-size="{HEADER_FONT}px" fill="{c['fg']}"> contributions in the last year</text>
<g font-size="{LABEL_FONT}px" fill="{c['dots']}">{months}{days}
<text x="{swatch_x - 6:g}" y="{legend_y:g}" text-anchor="end">Less</text>
<text x="{grid_right - more_w:g}" y="{legend_y:g}">More</text>
</g>
{swatches}
{"".join(cells)}
<g class="px">{"".join(sprites)}</g>
</svg>
"""


def validate_smil(svg: str) -> int:
    """Reject any animation a browser would silently drop.

    SMIL does not error on a malformed animation -- it just ignores it, so a
    bad keyTimes list ships as a sprite that never moves. Check the rules that
    bite: keyTimes must start at 0, never decrease, stay within 1, match the
    number of values, and (for non-discrete modes) end exactly at 1.
    """
    root = ET.fromstring(svg.split("?>", 1)[1])
    count = 0
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("animate", "animateTransform") or "keyTimes" not in el.attrib:
            continue
        count += 1
        keys = [float(k) for k in el.get("keyTimes").split(";")]
        values = el.get("values").split(";")
        where = f"{tag} {el.get('attributeName')} ({len(values)} values)"
        if len(keys) != len(values):
            raise SystemExit(f"{where}: {len(keys)} keyTimes for {len(values)} values")
        if keys[0] != 0:
            raise SystemExit(f"{where}: first keyTime is {keys[0]}, must be 0")
        if any(b < a for a, b in zip(keys, keys[1:])):
            raise SystemExit(f"{where}: keyTimes decrease")
        if keys[-1] > 1:
            raise SystemExit(f"{where}: keyTime {keys[-1]} past 1")
        if el.get("calcMode", "linear") != "discrete" and keys[-1] != 1:
            raise SystemExit(f"{where}: interpolated animation must end at keyTime 1")
        if re.search(r"nan|inf", el.get("values"), re.I):
            raise SystemExit(f"{where}: non-finite value")
    return count


def main() -> None:
    cal = load_calendar()
    days = sum(1 for w in cal["weeks"] for d in w if d)
    print(f"calendar : {len(cal['weeks'])} weeks, {days} days, {cal['total']} contributions")
    for name in THEMES:
        svg = build(name, cal)
        checked = validate_smil(svg)
        out = ROOT / f"pacman_{name}.svg"
        out.write_text(svg, encoding="utf-8")
        print(f"wrote {out.name} ({out.stat().st_size:,} bytes, {checked} animations validated)")


if __name__ == "__main__":
    main()
