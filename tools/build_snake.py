"""Render the contribution calendar with a Nokia-style Snake eating it.

Reads cache/calendar.json (written by tools/fetch_stats.py) and writes
snake_dark.svg / snake_light.svg next to the card SVGs.

The board keeps GitHub's calendar format -- 53 weeks by 7 days of rounded
squares, months across the top, Mon/Wed/Fri down the side, the Less/More
legend -- so it still reads as your contribution graph. The retro part is a
classic Nokia-style pixel-art snake that steps cell-by-cell across the grid,
grows longer as it eats contribution cells, and counts up the total like an
arcade score.

Usage:
    python tools/build_snake.py
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

# --- Timing & Snake ---------------------------------------------------------
STEP = 0.065               # discrete Nokia-style tick per cell (seconds)
INTRO = 1.2                # full calendar, snake waiting before moving
OUTRO = 1.6                # empty calendar and final total before reset
INIT_LEN = 4               # starting length (head + 3 body segments)
MAX_LEN = 20               # max length as it eats contributions

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
WEEKDAYS = {1: "Mon", 3: "Wed", 5: "Fri"}

# --- Nokia-style Pixel Sprites (7x7 on U=2 -> 14x14px, matching CELL) -------
SNAKE_HEAD = """
.#####.
####.##
#######
#######
#######
####.##
.#####.
"""

SNAKE_TONGUE = """
.......
.......
.......
.......##
.......
.......
.......
"""

SNAKE_BODY = """
.#####.
#######
##.#.##
#######
##.#.##
#######
.#####.
"""

SNAKE_TAIL = """
.......
..####.
.######
#######
.######
..####.
.......
"""


def bitmap(art: str) -> list[str]:
    return [r for r in art.strip("\n").split("\n") if r]


def sprite_path(rows: list[str], pw: float = U, ph: float = U,
                cx_cols: float | None = None, cy_rows: float | None = None) -> str:
    """Merge a bitmap into one path of horizontal pixel runs, centred on 0,0."""
    h = len(rows)
    w = max(len(r) for r in rows)
    x0 = -(cx_cols if cx_cols is not None else w) * pw / 2
    y0 = -(cy_rows if cy_rows is not None else h) * ph / 2
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
            d.append(
                f"M{x0 + x * pw:g} {y0 + y * ph:g}"
                f"h{(run - x) * pw:g}v{ph:g}h{-(run - x) * pw:g}z"
            )
            x = run
    return "".join(d)


def mix(a: str, b: str, t: float) -> str:
    """Blend two #rrggbb colours: t=0 is a, t=1 is b."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


# --- Timeline ---------------------------------------------------------------
class Timeline:
    """Discrete cell-by-cell zigzag route across the 7 weekday rows."""

    def __init__(self, weeks: int, left: float, top: float) -> None:
        self.weeks = weeks
        self.left, self.top = left, top

        # Build the full cell-by-cell path across the board, then exit right.
        path: list[tuple[int, int]] = []
        for r in range(7):
            cols = range(weeks) if r % 2 == 0 else range(weeks - 1, -1, -1)
            for c in cols:
                path.append((c, r))
        for extra in range(1, MAX_LEN + 3):
            path.append((weeks - 1 + extra, 6))
        self.path = path
        self.cell_step = {cell: idx for idx, cell in enumerate(path[: weeks * 7])}

        self.done = INTRO + len(path) * STEP
        self.total = self.done + OUTRO

    def cx(self, col: int) -> float:
        return self.left + col * PITCH + CELL / 2

    def cy(self, row: int) -> float:
        return self.top + row * PITCH + CELL / 2

    def step_time(self, step: int) -> float:
        return INTRO + step * STEP

    def k(self, t: float) -> str:
        """A keyTime: seconds on the master clock as a 0..1 fraction."""
        return f"{min(max(t / self.total, 0.0), 1.0):.5f}"

    def eat_time(self, col: int, row: int) -> float:
        return self.step_time(self.cell_step[(col, row)])

    def segment_coords(self, lag: int) -> tuple[list[tuple[float, float]], list[float]]:
        """Discrete (x, y) positions and keyTimes for a segment lagging by `lag` steps."""
        # At t=0 (during INTRO), segments sit lined up horizontally to the left of (0, 0).
        start_cell = (-lag, 0)
        coords: list[tuple[float, float]] = [(self.cx(start_cell[0]), self.cy(start_cell[1]))]
        times: list[float] = [0.0]

        for step in range(len(self.path)):
            idx = step - lag
            cell = (idx, 0) if idx < 0 else self.path[idx]
            pt = (self.cx(cell[0]), self.cy(cell[1]))
            if pt != coords[-1]:
                coords.append(pt)
                times.append(self.step_time(step))
        return coords, times

    def head_headings(self) -> tuple[list[int], list[float]]:
        """Discrete rotation angles (0, 90, 180) whenever the head turns."""
        angles: list[int] = [0]
        times: list[float] = [0.0]
        for step in range(1, len(self.path)):
            c0, r0 = self.path[step - 1]
            c1, r1 = self.path[step]
            ang = 0 if c1 > c0 else 180 if c1 < c0 else 90
            if ang != angles[-1]:
                angles.append(ang)
                times.append(self.step_time(step))
        return angles, times


def anim_discrete_translate(tl: Timeline, coords: list[tuple[float, float]],
                            times: list[float]) -> str:
    values = ";".join(f"{x:g},{y:g}" for x, y in coords)
    keys = ";".join(tl.k(t) for t in times)
    return (
        f'<animateTransform attributeName="transform" type="translate" '
        f'values="{values}" keyTimes="{keys}" dur="{tl.total:.3f}s" '
        f'calcMode="discrete" repeatCount="indefinite"/>'
    )


def anim_discrete(tl: Timeline, attr: str, values: list[str], times: list[float]) -> str:
    """A step function on the master clock: values[i] holds from times[i]."""
    return (
        f'<animate attributeName="{attr}" values="{";".join(values)}" '
        f'keyTimes="{";".join(tl.k(t) for t in times)}" dur="{tl.total:.3f}s" '
        f'calcMode="discrete" repeatCount="indefinite"/>'
    )


# --- Data -------------------------------------------------------------------
def load_calendar() -> dict:
    if not CALENDAR_SRC.exists():
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
    return [
        lab for j, lab in enumerate(labels)
        if j + 1 == len(labels) or labels[j + 1][0] - lab[0] >= 3
    ]


# --- Render -----------------------------------------------------------------
def build(theme: str, cal: dict) -> str:
    c = THEMES[theme]
    weeks = cal["weeks"]
    base_col = c.get("mix_base", c["bg"])
    ramp = [mix(base_col, c["fg"], t) for t in LEVEL_MIX]

    # Nokia Snake palette: crisp retro green from the card's theme
    snake_color = c["add"]
    tongue_color = c["del"]

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
                base.append(
                    f'<use href="#d" x="{left + col * PITCH:g}" y="{top + row * PITCH:g}"/>'
                )
    cells.append(f'<g fill="{ramp[0]}">{"".join(base)}</g>')

    # Days with contributions sit on top and vanish as the Snake eats them.
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

    eaten.sort()

    # Determine when each extra body segment unlocks as the snake eats.
    extra_slots = MAX_LEN - INIT_LEN
    grow_times: list[float] = []
    if eaten and extra_slots > 0:
        n_eaten = len(eaten)
        for s in range(extra_slots):
            idx = min(n_eaten - 1, int(s * n_eaten / extra_slots))
            grow_times.append(eaten[idx][0])
    total_len = INIT_LEN + len(grow_times)

    body_d = sprite_path(bitmap(SNAKE_BODY))
    head_d = sprite_path(bitmap(SNAKE_HEAD))
    tongue_d = sprite_path(bitmap(SNAKE_TONGUE), cx_cols=7, cy_rows=7)

    # Render body segments from tail to neck so the head draws cleanly on top.
    for lag in range(total_len - 1, 0, -1):
        coords, times = tl.segment_coords(lag)
        # Hide segments that haven't grown yet.
        vis_anim = ""
        vis_attr = ""
        if lag >= INIT_LEN:
            t_grow = grow_times[lag - INIT_LEN]
            vis_attr = ' visibility="hidden"'
            vis_anim = anim_discrete(tl, "visibility", ["hidden", "visible"], [0.0, t_grow])

        sprites.append(
            f'<g{vis_attr}>{vis_anim}{anim_discrete_translate(tl, coords, times)}'
            f'<path d="{body_d}" fill="{snake_color}"/></g>'
        )

    # Render the Nokia Snake head with rotating direction and flicking tongue.
    head_coords, head_times = tl.segment_coords(0)
    angles, ang_times = tl.head_headings()
    rot = (
        f'<animateTransform attributeName="transform" type="rotate" '
        f'values="{";".join(str(a) for a in angles)}" '
        f'keyTimes="{";".join(tl.k(t) for t in ang_times)}" dur="{tl.total:.3f}s" '
        f'calcMode="discrete" repeatCount="indefinite"/>'
    )
    tongue_flick = (
        '<animate attributeName="opacity" values="0;1;0" '
        'keyTimes="0;0.75;0.9" dur="1.4s" calcMode="discrete" repeatCount="indefinite"/>'
    )
    sprites.append(
        f'<g>{anim_discrete_translate(tl, head_coords, head_times)}'
        f'<g>{rot}'
        f'<path d="{tongue_d}" fill="{tongue_color}" opacity="0">{tongue_flick}</path>'
        f'<path d="{head_d}" fill="{snake_color}"/>'
        f'</g></g>'
    )

    # Header: GitHub's own sentence, with the number counting up like a score.
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

    months = "".join(
        f'<text x="{left + col * PITCH:g}" y="{months_y:g}">{name}</text>'
        for col, name in month_labels(weeks)
    )
    days = "".join(
        f'<text x="{MARGIN:g}" y="{tl.cy(row) + LABEL_FONT * 0.35:g}">{name}</text>'
        for row, name in WEEKDAYS.items()
    )

    # Legend, right-aligned under the grid like GitHub's.
    grid_right = left + (len(weeks) - 1) * PITCH + CELL
    more_w = 4 * LABEL_FONT * ADVANCE
    swatch_x = grid_right - more_w - 6 - (5 * PITCH - (PITCH - CELL))
    swatches = "".join(
        f'<rect x="{swatch_x + i * PITCH:g}" y="{legend_y - CELL + 3:g}" '
        f'width="{CELL}" height="{CELL}" rx="{RADIUS}" fill="{ramp[i]}"/>'
        for i in range(5)
    )

    bg_rect = (
        ""
        if c["bg"] == "transparent"
        else '<rect width="%dpx" height="%gpx" fill="%s" rx="15"/>' % (CANVAS_W, height, c["bg"])
    )

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
<defs>
  <rect id="d" width="{CELL}" height="{CELL}" rx="{RADIUS}"/>
  <clipPath id="board-clip">
    <rect x="{left:g}" y="{top - 4:g}" width="{grid_right - left + 4:g}" height="{grid_bottom - top + 8:g}"/>
  </clipPath>
</defs>
{bg_rect}
<text y="{header_y:g}" font-size="{HEADER_FONT}px" fill="{c['fg']}">{"".join(number)}</text>
<text x="{words_x:g}" y="{header_y:g}" font-size="{HEADER_FONT}px" fill="{c['fg']}"> contributions in the last year</text>
<g font-size="{LABEL_FONT}px" fill="{c['dots']}">{months}{days}
<text x="{swatch_x - 6:g}" y="{legend_y:g}" text-anchor="end">Less</text>
<text x="{grid_right - more_w:g}" y="{legend_y:g}">More</text>
</g>
{swatches}
{"".join(cells)}
<g class="px" clip-path="url(#board-clip)">{"".join(sprites)}</g>
</svg>
"""


def validate_smil(svg: str) -> int:
    """Reject any animation a browser would silently drop."""
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
        out = ROOT / f"snake_{name}.svg"
        out.write_text(svg, encoding="utf-8")
        print(f"wrote {out.name} ({out.stat().st_size:,} bytes, {checked} animations validated)")


if __name__ == "__main__":
    main()
