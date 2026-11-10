"""Build the neofetch-style profile card SVGs (dark_mode.svg / light_mode.svg).

The card is regenerated from scratch on every run: the ASCII art comes from
assets/ascii-art.txt and the numbers from cache/stats.json (written by
tools/fetch_stats.py). Nothing patches the SVG after the fact, so the column
arithmetic lives in exactly one place -- the Row builder below.

Rows are assembled as segments with one or more elastic `fill` runs. The fills
expand to land the row on exactly PANEL_COLS characters, and render() asserts
that they did. An arithmetic slip is therefore a crash, not a row that quietly
drifts out of alignment.

Usage:
    python tools/build_card.py
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASCII_SRC = ROOT / "assets" / "ascii-art.txt"
ASCII_SRC_LIGHT = ROOT / "assets" / "ascii-art-light.txt"
STATS_SRC = ROOT / "cache" / "stats.json"

BIRTHDATE = dt.date(2001, 7, 14)

# --- Type metrics -----------------------------------------------------------
# Consolas advances 0.55em per glyph; the generic monospace fallback advances
# 0.60em. The @font-face below re-declares Consolas at size-adjust:109%, which
# lands it on 0.55 * 1.09 = 0.5995 -- i.e. both paths measure the same, so the
# card keeps its layout whether or not the viewer has Consolas installed.
# Verified in-browser with getComputedTextLength(), not assumed.
ADVANCE = 0.5995

# Each theme draws its own art file, and the two grids differ (60x26 vs
# 87x47). Pinning the art COLUMN to a fixed pixel width and deriving the font
# size from the grid keeps both cards exactly the same size, whatever art
# either one is given.
ART_COL_WIDTH = 414.0

# asciiart.eu/image-to-ascii rasterises on a fixed cell 0.54 as wide as it is
# tall (derived: a square source at 87 characters comes back 47 rows, 47/87).
# The line height has to reproduce that or the portrait comes out stretched.
ART_CELL_ASPECT = 0.54


def art_metrics(cols: int) -> tuple[float, float]:
    """Return (font_size, line_height) that fit `cols` into ART_COL_WIDTH."""
    font = ART_COL_WIDTH / (cols * ADVANCE)
    return font, font * ADVANCE / ART_CELL_ASPECT

PANEL_FONT = 16.0
PANEL_LINE = 20.0
PANEL_COLS = 58

MARGIN = 15
GUTTER = 18

THEMES = {
    "dark": {
        "art_src": ASCII_SRC,
        "bg": "#161b22", "fg": "#c9d1d9", "art": "#c9d1d9",
        "key": "#FF6B9D", "value": "#a5d6ff",
        "add": "#3fb950", "del": "#f85149", "dots": "#616e7f",
        "green": "#2da44e", "telegram": "#229ED9", "discord": "#5865F2",
        "pac": "#FFE600",
    },
    "light": {
        "art_src": ASCII_SRC_LIGHT,
        "bg": "#ffffff", "fg": "#24292f", "art": "#24292f",
        "key": "#D6336C", "value": "#0550ae",
        "add": "#1a7f37", "del": "#cf222e", "dots": "#8c959f",
        "green": "#1a7f37", "telegram": "#0088cc", "discord": "#4752C4",
        "pac": "#EAB308",
    },
}

TITLE = "megumii"

INFO = [
    ("OS", "Windows, Linux"),
    ("Uptime", None),  # filled from BIRTHDATE
    ("Host", "Neptunus"),
    ("IDE", "VSCode, Cursor"),
    None,
    # "Languages.Programming" does not fit in 58 cols with all six languages,
    # so the row is just "Languages" (the web / real-language rows are skipped).
    ("Languages", "MDX, TS, JS, Solidity, Rust, Lua"),
    None,
    ("Focus.AI", "Agent Automation"),
    ("Hobbies", "Osu!"),
]

CONTACT = [
    ("Discord", "discordapp.com/users/873803230042263563"),
    ("Telegram", "KatouMegumii"),
    ("Portfolio", "megumii.xyz"),
]


def esc(text: str) -> str:
    return html.escape(text, quote=False)


# --- Row builder ------------------------------------------------------------
class Row:
    """A panel row assembled from fixed segments and elastic fills."""

    def __init__(self) -> None:
        self._segs: list[dict] = []

    def text(self, value: str, cls: str | None = None, eid: str | None = None) -> "Row":
        self._segs.append({"kind": "text", "v": str(value), "cls": cls, "id": eid})
        return self

    def key(self, value: str) -> "Row":
        """A key, splitting on dots so `Languages.Programming` colours both halves."""
        parts = str(value).split(".")
        for i, part in enumerate(parts):
            if i:
                self.text(".")
            self.text(part, cls="key")
        return self

    def value(self, v: str, eid: str | None = None) -> "Row":
        return self.text(v, cls="value", eid=eid)

    def fill(self, char: str = ".", cls: str = "cc", eid: str | None = None) -> "Row":
        self._segs.append({"kind": "fill", "char": char, "cls": cls, "id": eid})
        return self

    def _fixed_len(self) -> int:
        return sum(len(s["v"]) for s in self._segs if s["kind"] == "text")

    def plain(self, width: int) -> str:
        return "".join(
            s["v"] if s["kind"] == "text" else s["char"] * self._fill_widths(width)[i]
            for i, s in enumerate(self._segs)
        )

    def _fill_widths(self, width: int) -> dict[int, int]:
        fills = [i for i, s in enumerate(self._segs) if s["kind"] == "fill"]
        if not fills:
            return {}
        slack = width - self._fixed_len()
        if slack < len(fills):
            raise SystemExit(
                f"row does not fit in {width} cols (needs {self._fixed_len() + len(fills)}): "
                f"{''.join(s.get('v', '~') for s in self._segs)!r}"
            )
        base, extra = divmod(slack, len(fills))
        return {idx: base + (1 if n < extra else 0) for n, idx in enumerate(fills)}

    def render(self, x: int, y: float, width: int) -> str:
        widths = self._fill_widths(width)
        out, total = [], 0
        for i, s in enumerate(self._segs):
            body = s["v"] if s["kind"] == "text" else s["char"] * widths[i]
            total += len(body)
            attrs = ""
            if s["cls"]:
                attrs += f' class="{s["cls"]}"'
            if s["id"]:
                attrs += f' id="{s["id"]}"'
            out.append(f"<tspan{attrs}>{esc(body)}</tspan>" if attrs else esc(body))
        assert total == width, f"row rendered {total} cols, expected {width}"
        return f'<tspan x="{x}" y="{y:g}">{"".join(out)}</tspan>'


def info_row(key: str, value: str, eid: str | None = None) -> Row:
    return (
        Row().text(". ", cls="cc").key(key).text(":")
        .text(" ", cls="cc").fill().text(" ", cls="cc")
        .value(value, eid)
    )


def rule_row(label: str, bold: bool = False) -> Row:
    r = Row()
    r.text(label, cls="title" if bold else None)
    r.text(" -").fill("—", cls=None).text("-")
    return r


# --- Data -------------------------------------------------------------------
def uptime(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    years = today.year - BIRTHDATE.year
    months = today.month - BIRTHDATE.month
    days = today.day - BIRTHDATE.day
    if days < 0:
        months -= 1
        prev = today.replace(day=1) - dt.timedelta(days=1)
        days += prev.day
    if months < 0:
        years -= 1
        months += 12

    def plural(n: int, word: str) -> str:
        return f"{n} {word}" + ("" if n == 1 else "s")

    return f"{plural(years,'year')}, {plural(months,'month')}, {plural(days,'day')}"


def load_stats() -> dict:
    blank = {k: "--" for k in
             ("repos", "contrib", "stars", "commits", "followers", "loc", "added", "deleted")}
    if not STATS_SRC.exists():
        return blank
    raw = json.loads(STATS_SRC.read_text(encoding="utf-8"))
    return {k: f"{raw[k]:,}" if isinstance(raw.get(k), int) else blank[k] for k in blank}


def load_art(src: Path) -> list[str]:
    lines = src.read_text(encoding="utf-8").split("\n")
    filled = [i for i, line in enumerate(lines) if line.strip()]
    if not filled:
        raise SystemExit(f"{src} has no content")
    body = [line.rstrip() for line in lines[filled[0]: filled[-1] + 1]]
    indent = min(len(l) - len(l.lstrip()) for l in body if l.strip())
    return [l[indent:] for l in body]


# --- Vertical layout --------------------------------------------------------
# A line's glyph box runs ~1.0em above its baseline and ~0.3em below (measured
# in-browser with getBBox on the tspans). So its middle sits 0.35em above the
# baseline, and a block of lines reaches 0.65em past its outer baselines on
# both sides -- which is what lets two blocks in different font sizes centre
# on one shared axis.
LINE_MID = 0.35
LINE_PAD = 0.65


# --- Panel ------------------------------------------------------------------
def build_panel(x: int, y0: float) -> tuple[list[str], int]:
    """Render the panel with its first baseline at y0; returns (lines, row count)."""
    st = load_stats()
    rows: list[Row | None] = [rule_row(TITLE, bold=True)]

    for item in INFO:
        if item is None:
            rows.append(None)
            continue
        key, val = item
        if key == "Uptime":
            rows.append(info_row(key, uptime(), eid="age_data"))
        else:
            rows.append(info_row(key, val))

    rows.append(None)
    rows.append(rule_row("- Contact"))
    rows += [info_row(k, v) for k, v in CONTACT]

    rows.append(None)
    rows.append(rule_row("- GitHub Stats"))
    rows.append(
        Row().text(". ", cls="cc").key("Repos").text(":")
        .text(" ", cls="cc").fill(eid="repo_data_dots").text(" ", cls="cc")
        .value(st["repos"], "repo_data")
        .text(" {").text("Contributed", cls="key").text(": ")
        .value(st["contrib"], "contrib_data").text("}")
        .text(" | ").key("Stars").text(":")
        .text(" ", cls="cc").fill(eid="star_data_dots").text(" ", cls="cc")
        .value(st["stars"], "star_data")
    )
    rows.append(
        Row().text(". ", cls="cc").key("Followers").text(":")
        .text(" ", cls="cc").fill(eid="follower_data_dots").text(" ", cls="cc")
        .value(st["followers"], "follower_data")
    )
    rows.append(
        Row().text(". ", cls="cc").key("Lines of Code").text(":")
        .text(" ", cls="cc").fill(eid="loc_data_dots").text(" ", cls="cc")
        .value(st["loc"], "loc_data")
        .text(" ( ").text(st["added"], cls="addColor", eid="loc_add")
        .text("++", cls="addColor").text(", ")
        .text(st["deleted"], cls="delColor", eid="loc_del")
        .text("--", cls="delColor").text(" )")
    )

    out = []
    for i, row in enumerate(rows):
        y = y0 + i * PANEL_LINE
        if row is None:
            out.append(f'<tspan x="{x}" y="{y:g}" class="cc">.</tspan>')
        else:
            out.append(row.render(x, y, PANEL_COLS))

    # A terminal left waiting for the next command. calcMode="discrete" is what
    # makes it snap on and off like a real cursor instead of fading.
    y = y0 + (len(rows) + 1) * PANEL_LINE
    out.append(
        f'<tspan x="{x}" y="{y:g}">'
        f'<tspan class="key">&gt; </tspan>'
        f'<tspan class="value">█'
        f'<animate attributeName="opacity" values="1;0" dur="1.06s"'
        f' calcMode="discrete" repeatCount="indefinite"/>'
        f'</tspan></tspan>'
    )
    return out, len(rows) + 2


def build(theme: str) -> str:
    c = THEMES[theme]
    art = load_art(c["art_src"])
    art_cols = max(len(l) for l in art)
    art_font, art_line = art_metrics(art_cols)

    panel_x = int(MARGIN + ART_COL_WIDTH + GUTTER)
    _, panel_rows = build_panel(panel_x, 0.0)  # dry run: the layout needs the row count

    # Art and panel centre on one horizontal axis, and the card is sized so that
    # axis is its exact middle. The art used to be top-aligned, and being the
    # shorter block it hung ~100px above centre; the panel itself sat 8px high
    # because the top and bottom margins were never equal.
    panel_span = (panel_rows - 1) * PANEL_LINE
    art_span = (len(art) - 1) * art_line
    half = max(panel_span / 2 + LINE_PAD * PANEL_FONT, art_span / 2 + LINE_PAD * art_font)
    axis = MARGIN + half

    panel_lines, _ = build_panel(panel_x, axis - panel_span / 2 + LINE_MID * PANEL_FONT)
    art_y0 = axis - art_span / 2 + LINE_MID * art_font

    width = int(panel_x + PANEL_COLS * PANEL_FONT * ADVANCE + MARGIN)
    height = round(2 * axis)

    art_tspans = "\n".join(
        f'<tspan x="{MARGIN}" y="{art_y0 + i * art_line:.2f}">{esc(line)}</tspan>'
        for i, line in enumerate(art)
    )

    return f"""<?xml version='1.0' encoding='UTF-8'?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}px" height="{height}px"
     font-family="ConsolasFallback,Consolas,Menlo,monospace">
<style>
@font-face {{
  src: local('Consolas'), local('Consolas Bold');
  font-family: 'ConsolasFallback';
  font-display: swap;
  size-adjust: 109%;
}}
.key {{fill: {c['key']};}}
.value {{fill: {c['value']};}}
.title {{fill: {c['key']}; font-weight: bold;}}
.addColor {{fill: {c['add']};}}
.delColor {{fill: {c['del']};}}
.cc {{fill: {c['dots']};}}
text, tspan {{white-space: pre;}}
</style>
<rect width="{width}px" height="{height}px" fill="{c['bg']}" rx="15"/>
<text fill="{c['art']}" font-size="{art_font:.2f}px" class="ascii">
{art_tspans}
</text>
<text fill="{c['fg']}" font-size="{PANEL_FONT:g}px">
{chr(10).join(panel_lines)}
</text>
</svg>
"""


def main() -> None:
    for name, c in THEMES.items():
        a = load_art(c["art_src"])
        cols = max(len(l) for l in a)
        f, ln = art_metrics(cols)
        print(f"{name:>5} art: {cols} cols x {len(a)} lines -> {f:.2f}px / {ln:.2f}px line")
    print(f"uptime   : {uptime()}")
    print(f"stats    : {'cache/stats.json' if STATS_SRC.exists() else 'not fetched yet (placeholders)'}")
    for name in THEMES:
        out = ROOT / f"{name}_mode.svg"
        out.write_text(build(name), encoding="utf-8")
        print(f"wrote {out.name} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
