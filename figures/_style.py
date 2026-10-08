"""Shared drawing style and helpers for the paper figures.

Coordinates are in print inches (axes span the whole figure), so every font
size below is the size at IEEE print width. Minimum text size is 7 pt.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]   # artifact repository root
SVG_DIR = ROOT / "figures" / "out"
PNG_DIR = ROOT / "figures" / "out"

INK = "#000000"
ACCENT = "#1F5AA6"      # the single accent colour: off-board (remote GPU / cloud) and fallback edges
GRAY = "#5A5A5A"
PANEL = "#F4F4F4"       # flat container fill (no gradient)

FS = 7.5      # body
FS_S = 7.0    # smallest allowed
FS_T = 8.5    # titles
LW = 0.8

matplotlib.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "svg.fonttype": "none",       # keep text as text in SVG
    "font.size": FS,
    "lines.linewidth": LW,
    "savefig.facecolor": "white",
})

_REGISTRY = []   # (text artist, Box) pairs checked for overflow at save time


class Box:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2

    def top(self, f=0.5):
        return (self.x + self.w * f, self.y + self.h)

    def bottom(self, f=0.5):
        return (self.x + self.w * f, self.y)

    def left(self, f=0.5):
        return (self.x, self.y + self.h * f)

    def right(self, f=0.5):
        return (self.x + self.w, self.y + self.h * f)


def new_fig(w, h, y0=0.0):
    fig = plt.figure(figsize=(w, h - y0))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(y0, h)
    ax.set_aspect("equal")
    ax.axis("off")
    _REGISTRY.clear()
    return fig, ax


def box(ax, x, y, w, h, text="", fs=FS, color=INK, fill="white", lw=LW, ls="-",
        bold=False, round_=True, tcolor=None, ha="center", va="center", check=True,
        italic=False, z=3):
    style = "round,pad=0,rounding_size=0.05" if round_ else "square,pad=0"
    p = FancyBboxPatch((x, y), w, h, boxstyle=style, linewidth=lw, edgecolor=color,
                       facecolor=fill, linestyle=ls, zorder=z)
    ax.add_patch(p)
    b = Box(x, y, w, h)
    if text:
        tx = {"center": b.cx, "left": x + 0.06}[ha]
        ty = {"center": b.cy, "top": y + h - 0.05}[va]
        t = ax.text(tx, ty, text, fontsize=fs, ha=ha, va="center" if va == "center" else "top",
                    color=tcolor or INK, fontweight="bold" if bold else "normal",
                    fontstyle="italic" if italic else "normal", zorder=z + 1, linespacing=1.15)
        if check:
            _REGISTRY.append((t, b))
    return b


def group(ax, x, y, w, h, title, color=INK, fill=PANEL, fs=FS_T, lw=LW, ls="-", tcolor=None):
    """Container with a bold title in its top-left corner."""
    box(ax, x, y, w, h, fill=fill, color=color, lw=lw, ls=ls, z=1)
    ax.text(x + 0.07, y + h - 0.06, title, fontsize=fs, fontweight="bold", ha="left", va="top",
            color=tcolor or color, zorder=2)
    return Box(x, y, w, h)


def diamond(ax, cx, cy, w, h, text, fs=FS, color=INK, lw=LW):
    pts = [(cx, cy + h / 2), (cx + w / 2, cy), (cx, cy - h / 2), (cx - w / 2, cy)]
    ax.add_patch(Polygon(pts, closed=True, facecolor="white", edgecolor=color, lw=lw, zorder=3))
    t = ax.text(cx, cy, text, fontsize=fs, ha="center", va="center", zorder=4, linespacing=1.1)
    b = Box(cx - w / 2, cy - h / 2, w, h)
    _REGISTRY.append((t, Box(cx - w / 4 - 0.02, cy - h / 4 - 0.02, w / 2 + 0.04, h / 2 + 0.04)))
    return b


def path(ax, pts, color=INK, lw=LW, ls="-", head=True, both=False, z=2):
    """Orthogonal (or any) polyline with an arrowhead at the last point."""
    xs, ys = zip(*pts)
    ax.add_line(Line2D(xs, ys, color=color, lw=lw, linestyle=ls, zorder=z,
                       solid_capstyle="butt", dash_capstyle="butt"))
    hs = dict(arrowstyle="-|>,head_length=0.32,head_width=0.16", color=color, lw=lw,
              shrinkA=0, shrinkB=0, mutation_scale=10)
    if head:
        ax.annotate("", xy=pts[-1], xytext=pts[-2], arrowprops=hs, zorder=z)
    if both:
        ax.annotate("", xy=pts[0], xytext=pts[1], arrowprops=hs, zorder=z)


def label(ax, x, y, text, fs=FS_S, color=INK, ha="center", va="center", bg=True, italic=False,
          bold=False, z=5, rot=0):
    kw = {}
    if bg:
        kw["bbox"] = dict(boxstyle="square,pad=0.08", facecolor="white", edgecolor="none")
    return ax.text(x, y, text, fontsize=fs, color=color, ha=ha, va=va, zorder=z,
                   fontstyle="italic" if italic else "normal",
                   fontweight="bold" if bold else "normal", linespacing=1.1, rotation=rot, **kw)


def legend_line(ax, x, y, text, color=INK, ls="-", length=0.3, fs=FS_S):
    path(ax, [(x, y), (x + length, y)], color=color, ls=ls)
    ax.text(x + length + 0.06, y, text, fontsize=fs, va="center", ha="left")


def _check(fig):
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    ax = fig.axes[0]
    problems = []
    for t, b in _REGISTRY:
        e = t.get_window_extent(r)
        (x0, y0) = ax.transData.transform((b.x, b.y))
        (x1, y1) = ax.transData.transform((b.x + b.w, b.y + b.h))
        tol = -0.03 * fig.dpi  # require ~0.03 in clearance inside the box
        if e.x0 < x0 - tol or e.x1 > x1 + tol or e.y0 < y0 - tol or e.y1 > y1 + tol:
            problems.append(t.get_text().replace("\n", " / "))
    small = [t.get_text() for t in fig.findobj(matplotlib.text.Text)
             if t.get_text() and t.get_fontsize() < FS_S - 1e-6]
    return problems, small


def save(fig, name, png_width_px=1800, min_dpi=300):
    problems, small = _check(fig)
    for p in problems:
        print(f"  [overflow] {name}: {p}")
    for s in small:
        print(f"  [<7pt] {name}: {s}")
    SVG_DIR.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(SVG_DIR / f"{name}.svg")
    dpi = max(min_dpi, png_width_px / fig.get_figwidth())   # PNG at least 300 dpi
    fig.savefig(PNG_DIR / f"{name}.png", dpi=dpi)
    plt.close(fig)
    print(f"saved {name}: svg + png ({round(fig.get_figwidth() * dpi)}px), overflow={len(problems)}, small={len(small)}")


# ---- data-figure helpers -----------------------------------------------------
LIGHT = "#BDBDBD"     # third neutral (on-board / secondary marks)


def clean_axes(ax, grid_axis=None):
    """White report style: no top/right spines, thin ticks, optional light grid."""
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_linewidth(0.6)
    ax.tick_params(labelsize=FS_S, width=0.6, length=2.5)
    if grid_axis:
        ax.grid(axis=grid_axis, color="#E3E3E3", lw=0.5, zorder=0)
        ax.set_axisbelow(True)


def pct_linear(v, q):
    """Percentile with linear interpolation (numpy default)."""
    v = sorted(v)
    k = q * (len(v) - 1)
    i = int(k)
    return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (k - i)


def median(v):
    return pct_linear(v, 0.5)
