"""Generate clean reference-style NISAR InSAR -> StaMPS Export workflow diagram (PNG + SVG)."""

from pathlib import Path
from typing import Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


DOCS_DIR = Path(__file__).resolve().parent
OUT_PNG = DOCS_DIR / "nisar_workflow_diagram.png"
OUT_SVG = DOCS_DIR / "nisar_workflow_diagram.svg"

INK = "#141920"
MUTED = "#4A5568"
ACCENT = "#C84B28"
BG = "#FFFFFF"
SOFT_DASH = "#4A5568"


def _box(
    ax,
    cx: float,
    cy: float,
    w: float,
    h: float,
    text: str,
    *,
    stacked: bool = False,
    bold: bool = False,
    fontsize: float = 8.2,
    edge: str = INK,
    lw: float = 1.25,
    facecolor: str = "#FFFFFF",
    linestyle: str = "-",
) -> Tuple[float, float, float, float]:
    """Draw a rounded rectangle centered at (cx, cy); return (left, bottom, right, top)."""
    x0 = cx - w / 2.0
    y0 = cy - h / 2.0

    if stacked:
        for off in (1.1, 0.55):
            back = FancyBboxPatch(
                (x0 + off, y0 - off),
                w,
                h,
                boxstyle="round,pad=0.0,rounding_size=0.9",
                facecolor="#FFFFFF",
                edgecolor=edge,
                linewidth=lw * 0.9,
                zorder=2,
            )
            ax.add_patch(back)

    patch = FancyBboxPatch(
        (x0, y0),
        w,
        h,
        boxstyle="round,pad=0.0,rounding_size=0.9",
        facecolor=facecolor,
        edgecolor=edge,
        linewidth=lw,
        linestyle=linestyle,
        zorder=3,
    )
    ax.add_patch(patch)

    ax.text(
        cx,
        cy,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        fontweight="semibold" if bold else "medium",
        color=INK,
        linespacing=1.22,
        zorder=4,
    )
    return (x0, y0, x0 + w, y0 + h)


def _ortho(ax, pts, *, arrow: bool = True, color: str = INK, lw: float = 1.15) -> None:
    """Draw an orthogonal polyline with an optional crisp arrowhead on the final segment."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if arrow and len(pts) >= 2:
        if len(pts) > 2:
            ax.plot(xs[:-1], ys[:-1], color=color, linewidth=lw, solid_capstyle="round", zorder=2)
        x_prev, y_prev = pts[-2]
        x_end, y_end = pts[-1]
        ax.annotate(
            "",
            xy=(x_end, y_end),
            xytext=(x_prev, y_prev),
            arrowprops=dict(
                arrowstyle="-|>,head_length=0.45,head_width=0.26",
                color=color,
                lw=lw,
                shrinkA=0,
                shrinkB=0,
            ),
            zorder=2,
        )
    else:
        ax.plot(xs, ys, color=color, linewidth=lw, solid_capstyle="round", zorder=2)


def build_diagram() -> None:
    fig, ax = plt.subplots(figsize=(15.5, 9.2), dpi=300)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 168)
    ax.set_ylim(0, 96)
    ax.axis("off")

    ax.text(
        3.0,
        92.8,
        "snap2stamps_nisar Automated Pipeline  (Windows / Linux / macOS  |  Stops at Step 3 StaMPS Export)",
        ha="left",
        va="center",
        fontsize=10.5,
        fontweight="bold",
        color=INK,
    )
    ax.text(
        144.0,
        92.8,
        "Downstream StaMPS / MATLAB (External)",
        ha="center",
        va="center",
        fontsize=9.3,
        fontweight="bold",
        color=MUTED,
    )

    # ------------------------------------------------------------------
    # LEFT ENTRY: Multi-image input & Master/Secondary Selection
    # ------------------------------------------------------------------
    in_b = _box(ax, 10.0, 66.0, 14.5, 8.2, "NISAR L1 RSLC\n(.h5 Granules)\nASF / Bhoonidhi", stacked=True, fontsize=7.9)
    sel_b = _box(ax, 27.0, 66.0, 13.8, 8.6, "Automaster &\nMaster / Secondary\nSelection", fontsize=8.0)
    _ortho(ax, [(in_b[2] + 1.1, 66.0), (sel_b[0], 66.0)])

    # ------------------------------------------------------------------
    # TOP BRANCH: Master Image -> Step 1 Spatial Subset -> Metadata Patch
    # ------------------------------------------------------------------
    m_img = _box(ax, 46.5, 81.0, 13.4, 6.2, "Master Image\n(.h5)", bold=True, fontsize=8.1)
    m_sub = _box(ax, 63.8, 81.0, 14.2, 6.2, "Spatial Subset\n(Step 1: SNAP GPT)", fontsize=7.9)
    m_pat = _box(ax, 81.8, 81.0, 15.0, 6.6, "Patch Subset Meta\n(Slant Range & Rate)", fontsize=7.8)

    _ortho(ax, [(sel_b[2], 66.0), (37.0, 66.0), (37.0, 81.0), (m_img[0], 81.0)])
    _ortho(ax, [(m_img[2], 81.0), (m_sub[0], 81.0)])
    _ortho(ax, [(m_sub[2], 81.0), (m_pat[0], 81.0)])

    # ------------------------------------------------------------------
    # BOTTOM BRANCH: Secondaries -> Step 0 Staging -> Step 1 Subset -> Patch
    # ------------------------------------------------------------------
    s_img = _box(ax, 46.5, 53.0, 14.2, 6.6, "Secondary Images\n(.h5 Stack)", stacked=True, fontsize=8.0)
    s_prep = _box(ax, 46.5, 39.5, 14.8, 6.8, "Secondary Staging\n(Step 0: YYYYMMDD)", fontsize=7.9)
    s_sub = _box(ax, 63.8, 39.5, 14.2, 6.6, "Spatial Subset\n(Step 1: SNAP GPT)", fontsize=7.9)
    s_pat = _box(ax, 81.8, 39.5, 15.0, 6.6, "Patch Subset Meta\n(Slant Range & Rate)", fontsize=7.8)

    _ortho(ax, [(sel_b[2], 66.0), (37.0, 66.0), (37.0, 53.0), (s_img[0], 53.0)])
    _ortho(ax, [(46.5, s_img[1] - 1.1), (46.5, s_prep[3])])
    _ortho(ax, [(s_prep[2], 39.5), (s_sub[0], 39.5)])
    _ortho(ax, [(s_sub[2], 39.5), (s_pat[0], 39.5)])

    # ------------------------------------------------------------------
    # CENTER COREGISTRATION & IFG CONVERGENCE COLUMN (x = 96..126)
    # Completely separated to the right of Patch Subset Meta (right edge = 89.3)
    # ------------------------------------------------------------------
    coreg_b = _box(
        ax,
        111.5,
        65.0,
        17.5,
        7.8,
        "DEM-Assisted\nCoregistration\n(tileExtension=400%)",
        bold=True,
        fontsize=7.9,
        edge=INK,
    )
    # Top Meta -> top of Coregistration
    _ortho(ax, [(m_pat[2], 81.0), (111.5, 81.0), (111.5, coreg_b[3])])
    # Bottom Meta -> right to x=95.5 -> up to y=65.0 -> left edge of Coregistration
    _ortho(ax, [(s_pat[2], 39.5), (95.5, 39.5), (95.5, 65.0), (coreg_b[0], 65.0)])

    # Split from Coregistration into Interferogram + Topo Removal (left) and Coreg RSLC (right)
    ifg_b = _box(ax, 103.5, 49.0, 13.0, 5.8, "Interferogram\nFormation", fontsize=7.8)
    topo_b = _box(ax, 103.5, 37.5, 13.0, 6.2, "Topographic\nPhase Removal", fontsize=7.8)
    rslc_b = _box(ax, 119.5, 43.2, 12.6, 6.4, "Coregistered\nRSLC Stack", fontsize=7.8)

    _ortho(ax, [(111.5, coreg_b[1]), (111.5, 55.2), (103.5, 55.2), (103.5, ifg_b[3])])
    _ortho(ax, [(111.5, coreg_b[1]), (111.5, 55.2), (119.5, 55.2), (119.5, rslc_b[3])])
    _ortho(ax, [(103.5, ifg_b[1]), (103.5, topo_b[3])])

    # Merge into Pair Validation -> StaMPS Export (Step 3)
    merge_b = _box(ax, 111.5, 24.0, 16.0, 5.6, "Coreg + IFG\nPair Validation", fontsize=7.9)
    export_b = _box(
        ax,
        111.5,
        11.2,
        19.0,
        8.4,
        "StaMPS Export (Step 3)\nINSAR_<MASTER>/\ndem | diff0 | geo | rslc",
        bold=True,
        fontsize=8.0,
        edge=ACCENT,
        lw=1.6,
        facecolor="#FFF8F5",
    )

    _ortho(ax, [(103.5, topo_b[1]), (103.5, 30.2), (111.5, 30.2), (111.5, merge_b[3])])
    _ortho(ax, [(119.5, rslc_b[1]), (119.5, 30.2), (111.5, 30.2), (111.5, merge_b[3])])
    _ortho(ax, [(111.5, merge_b[1]), (111.5, export_b[3])])

    # Callout note cleanly left of StaMPS Export (x = 39..91, gap > 10 units)
    ax.text(
        40.0,
        13.5,
        "Pipeline stops cleanly at StaMPS Export (Step 3).\n"
        "No automatic PS extraction (mt_prep_snap) is executed.\n\n"
        "Multi-Image AOI Behavior:\n"
        "  - Same track, multiple dates: all paired in one INSAR_<MASTER>.\n"
        "  - 2 frames on same date: Step 2 keeps first frame per date.\n"
        "  - Multiple overlapping tracks: pin one TRACK per workspace.",
        ha="left",
        va="center",
        fontsize=7.6,
        color=MUTED,
        linespacing=1.36,
        bbox=dict(boxstyle="round,pad=0.65", facecolor="#FAF9F6", edgecolor="#CBD5E1", linewidth=0.9),
    )

    # ------------------------------------------------------------------
    # RIGHT VERTICAL COLUMN: External Downstream StaMPS Processing
    # ------------------------------------------------------------------
    dash_outer = FancyBboxPatch(
        (131.5, 3.0),
        25.8,
        87.2,
        boxstyle="round,pad=0.0,rounding_size=1.2",
        facecolor="#FCFBF9",
        edgecolor=SOFT_DASH,
        linewidth=1.0,
        linestyle="--",
        zorder=1,
    )
    ax.add_patch(dash_outer)

    col_x = 144.4
    stamps_steps = [
        (86.5, 4.4, "mt_prep_snap / Initial Load"),
        (79.4, 4.4, "Estimate Gamma Value"),
        (72.3, 4.4, "Selection of PS Pixels"),
        (65.2, 4.4, "Weed Out Adjacent Pixels"),
        (57.2, 5.8, "Correct Wrapped Phase &\nMerge Patches"),
        (49.2, 4.4, "Unwrap Phase (3D)"),
        (41.5, 5.4, "Calculate Spatially Correlated\nLook Angle (DEM) Error"),
        (33.8, 4.8, "Filter Spatially\nCorrelated Noise"),
        (26.8, 4.4, "Deformation Estimation"),
    ]

    prev_y_bot = None
    first_stamps_left = None
    for idx, (cy, bh, label) in enumerate(stamps_steps):
        b = _box(ax, col_x, cy, 21.8, bh, label, fontsize=7.5, edge=SOFT_DASH, lw=1.05)
        if idx == 0:
            first_stamps_left = (b[0], cy)
        if prev_y_bot is not None:
            _ortho(ax, [(col_x, prev_y_bot), (col_x, b[3])], color=SOFT_DASH, lw=1.0)
        prev_y_bot = b[1]

    ts_frame = FancyBboxPatch(
        (133.2, 5.2),
        22.4,
        17.5,
        boxstyle="round,pad=0.0,rounding_size=1.0",
        facecolor="#FFFFFF",
        edgecolor=SOFT_DASH,
        linewidth=1.0,
        linestyle="--",
        zorder=2,
    )
    ax.add_patch(ts_frame)

    ts1 = _box(ax, col_x, 18.2, 19.6, 5.0, "Land Surface Movement\nTime Series", fontsize=7.5, edge=SOFT_DASH, lw=1.05)
    ts2 = _box(ax, col_x, 9.4, 19.6, 4.6, "Time Series\nVisualization", fontsize=7.5, edge=SOFT_DASH, lw=1.05)
    _ortho(ax, [(col_x, prev_y_bot), (col_x, ts1[3])], color=SOFT_DASH, lw=1.0)
    _ortho(ax, [(col_x, ts1[1]), (col_x, ts2[3])], color=SOFT_DASH, lw=1.0)

    # Connect StaMPS Export (Step 3) cleanly up to Initial Load of External StaMPS
    _ortho(
        ax,
        [
            (export_b[2], 11.2),
            (128.0, 11.2),
            (128.0, first_stamps_left[1]),
            (first_stamps_left[0], first_stamps_left[1]),
        ],
        color=ACCENT,
        lw=1.35,
    )

    # Final output arrows exiting right edge
    _ortho(ax, [(155.6, 13.8), (159.2, 13.8), (159.2, 18.0), (162.8, 18.0)], color=SOFT_DASH, lw=1.0)
    _ortho(ax, [(155.6, 13.8), (159.2, 13.8), (159.2, 9.5), (162.8, 9.5)], color=SOFT_DASH, lw=1.0)
    ax.text(163.3, 18.0, "LOS\nVelocity", ha="left", va="center", fontsize=7.3, color=MUTED)
    ax.text(163.3, 9.5, "Time\nSeries", ha="left", va="center", fontsize=7.3, color=MUTED)

    fig.tight_layout(pad=0.6)
    fig.savefig(OUT_PNG, dpi=300, facecolor=BG)
    fig.savefig(OUT_SVG, facecolor=BG)
    plt.close(fig)
    print(f"Generated {OUT_PNG} ({OUT_PNG.stat().st_size} bytes) and {OUT_SVG}")


if __name__ == "__main__":
    build_diagram()
