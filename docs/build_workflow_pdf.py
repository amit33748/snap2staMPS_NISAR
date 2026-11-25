"""Generate docs/NISAR_INSAR_WORKFLOW.pdf with clean workflow diagram and cross-platform process steps."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from build_workflow_diagram import build_diagram


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
DIAGRAM_PNG = DOCS_DIR / "nisar_workflow_diagram.png"
PDF_OUT = DOCS_DIR / "NISAR_INSAR_WORKFLOW.pdf"

C_BG_CANVAS = colors.HexColor("#F8F6F1")
C_BG_SURFACE = colors.HexColor("#FDFCF9")
C_BORDER = colors.HexColor("#DED9CE")
C_BORDER_STRONG = colors.HexColor("#C5BEB0")
C_INK = colors.HexColor("#18202B")
C_MUTED = colors.HexColor("#56606D")
C_ACCENT = colors.HexColor("#C84B28")


def _draw_page_frame(c: canvas.Canvas, width: float, height: float, page_num: int, total_pages: int, title: str, subtitle: str) -> None:
    c.setFillColor(C_BG_CANVAS)
    c.rect(0, 0, width, height, fill=1, stroke=0)

    margin = 12 * mm
    c.setStrokeColor(C_BORDER_STRONG)
    c.setLineWidth(0.7)
    c.rect(margin, margin, width - 2 * margin, height - 2 * margin, fill=0, stroke=1)

    tick = 3.0 * mm
    c.setStrokeColor(C_ACCENT)
    c.setLineWidth(1.0)
    for x, dx in ((margin, tick), (width - margin, -tick)):
        for y, dy in ((margin, tick), (height - margin, -tick)):
            c.line(x, y, x + dx, y)
            c.line(x, y, x, y + dy)

    hdr_top = height - margin - 9 * mm
    c.setFillColor(C_ACCENT)
    c.setFont("Helvetica-Bold", 7.3)
    c.drawString(margin + 5 * mm, hdr_top + 4 * mm, "SNAP2STAMPS_NISAR  /  CROSS-PLATFORM WINDOWS, LINUX & MACOS WORKFLOW")

    c.setFillColor(C_INK)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin + 5 * mm, hdr_top - 3.2 * mm, title)

    c.setFillColor(C_MUTED)
    c.setFont("Helvetica", 8.8)
    c.drawString(margin + 5 * mm, hdr_top - 8.5 * mm, subtitle)

    c.setStrokeColor(C_BORDER)
    c.setLineWidth(0.75)
    c.line(margin + 5 * mm, hdr_top - 11.5 * mm, width - margin - 5 * mm, hdr_top - 11.5 * mm)

    c.line(margin + 5 * mm, margin + 8.0 * mm, width - margin - 5 * mm, margin + 8.0 * mm)
    c.setFillColor(C_MUTED)
    c.setFont("Courier", 7.0)
    c.drawString(margin + 5 * mm, margin + 3.5 * mm, "RUNTIME: INDEPENDENT .VENV (ISOLATED FROM ARCGIS PRO)  |  PIPELINE STOPS AT STEP 3 STAMPS EXPORT")
    c.drawRightString(width - margin - 5 * mm, margin + 3.5 * mm, f"PLATE 0{page_num} / 0{total_pages}")


def _draw_page_1(c: canvas.Canvas, w: float, h: float) -> None:
    _draw_page_frame(
        c, w, h, 1, 2,
        "NISAR L-Band InSAR to StaMPS Export Flowchart",
        "Automated pre-processing ends cleanly at Step 3 (StaMPS Export); downstream StaMPS PS steps remain external",
    )

    margin = 12 * mm
    box_x = margin + 5 * mm
    box_y = margin + 11 * mm
    box_w = w - 2 * margin - 10 * mm
    box_h = h - margin - 24 * mm - box_y

    c.setFillColor(colors.white)
    c.setStrokeColor(C_BORDER_STRONG)
    c.setLineWidth(0.8)
    c.roundRect(box_x, box_y, box_w, box_h, 3.0, fill=1, stroke=1)

    if DIAGRAM_PNG.exists():
        c.drawImage(
            str(DIAGRAM_PNG),
            box_x + 3 * mm,
            box_y + 2 * mm,
            width=box_w - 6 * mm,
            height=box_h - 4 * mm,
            preserveAspectRatio=True,
            anchor="c",
        )


def _draw_page_2(c: canvas.Canvas, w: float, h: float) -> None:
    _draw_page_frame(
        c, w, h, 2, 2,
        "Cross-Platform Process Steps & Multi-Image AOI Reference",
        "Windows / Linux / macOS execution via independent .venv, step-by-step operations, and multi-image AOI handling",
    )

    margin = 12 * mm
    inner_x = margin + 5 * mm
    inner_w = w - 2 * margin - 10 * mm

    # Upper row: Cross-platform launch + Multi-Image AOI rules
    top_y = h - 92 * mm
    top_h = 52 * mm
    left_w = inner_w * 0.50
    right_w = inner_w - left_w - 5 * mm
    right_x = inner_x + left_w + 5 * mm

    c.setFillColor(C_BG_SURFACE)
    c.setStrokeColor(C_BORDER_STRONG)
    c.roundRect(inner_x, top_y, left_w, top_h, 3.0, fill=1, stroke=1)
    c.setFillColor(C_INK)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(inner_x + 4 * mm, top_y + top_h - 6.5 * mm, "Cross-Platform Setup & Web Workbench (.venv)")
    c.setStrokeColor(C_BORDER)
    c.line(inner_x + 4 * mm, top_y + top_h - 8.5 * mm, inner_x + left_w - 4 * mm, top_y + top_h - 8.5 * mm)

    lines_left = [
        ("Windows GUI:", ".\\launch_workbench.bat  (HTML/CSS/JS app via .venv)", True),
        ("Linux/macOS GUI:", "bash launch_workbench.sh  (bootstraps .venv cleanly)", True),
        ("CLI (all OS):", "python bin/auto_run.py -F bin/project_nisar.conf --step all", True),
        ("SNAP GPT Auto-Find:", "Checks C:\\Program Files\\esa-snap, /Applications/esa-snap, ~/esa-snap", False),
        ("Dependency Check:", "One-click pip install -r requirements.txt inside Web GUI", False),
        ("AOI Map:", "Open-source India Leaflet map with basemap switcher + box draw", False),
    ]
    ry = top_y + top_h - 14.5 * mm
    for k, v, is_cmd in lines_left:
        c.setFillColor(C_ACCENT if is_cmd else C_INK)
        c.setFont("Helvetica-Bold", 7.6)
        c.drawString(inner_x + 4 * mm, ry, k)
        c.setFillColor(C_INK if is_cmd else C_MUTED)
        c.setFont("Courier" if is_cmd else "Helvetica", 6.7 if is_cmd else 7.4)
        c.drawString(inner_x + 34 * mm, ry, v)
        ry -= 6.1 * mm

    c.setFillColor(C_BG_SURFACE)
    c.setStrokeColor(C_BORDER_STRONG)
    c.roundRect(right_x, top_y, right_w, top_h, 3.0, fill=1, stroke=1)
    c.setFillColor(C_INK)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(right_x + 4 * mm, top_y + top_h - 6.5 * mm, "Multi-Image AOI Behavior & SNAP Compatibility")
    c.setStrokeColor(C_BORDER)
    c.line(right_x + 4 * mm, top_y + top_h - 8.5 * mm, right_x + right_w - 4 * mm, top_y + top_h - 8.5 * mm)

    lines_right = [
        "1. Multi-date, single track: all scenes paired cleanly into one INSAR_<MASTER>.",
        "2. AOI across 2 frames on same date: Step 2 keeps first frame per YYYYMMDD;",
        "   keep AOI inside one frame or process in separate project folders.",
        "3. Multiple overlapping tracks: set a single TRACK number per workspace.",
        "4. SNAP 14 coregistration: DEM-Assisted-Coregistration + tileExtensionPercent=400.",
        "5. Subsetting: exact pixel bounds from HDF5 geolocationGrid + metadata patch.",
    ]
    ry = top_y + top_h - 14.5 * mm
    c.setFont("Helvetica", 7.6)
    c.setFillColor(C_INK)
    for ln in lines_right:
        c.drawString(right_x + 4 * mm, ry, ln)
        ry -= 6.1 * mm

    # Lower table: Detailed Process Steps (Download -> Step 3 StaMPS Export)
    bot_y = margin + 12 * mm
    bot_h = top_y - bot_y - 6 * mm
    c.setFillColor(C_BG_SURFACE)
    c.setStrokeColor(C_BORDER_STRONG)
    c.roundRect(inner_x, bot_y, inner_w, bot_h, 3.0, fill=1, stroke=1)
    c.setFillColor(C_INK)
    c.setFont("Helvetica-Bold", 9.6)
    c.drawString(inner_x + 4 * mm, bot_y + bot_h - 6.5 * mm, "Process Step Breakdown (Automated Pipeline Stops at Step 3 StaMPS Export)")
    c.setStrokeColor(C_BORDER)
    c.line(inner_x + 4 * mm, bot_y + bot_h - 8.5 * mm, inner_x + inner_w - 4 * mm, bot_y + bot_h - 8.5 * mm)

    steps_table = [
        ("Download", "asf_nisar_downloader.py", "Queries catalog by AOI & TRACK, filters polarization locally, downloads .h5 with HTTP Range 206 resume into raw/."),
        ("Step 0: Prep", "nisar_step_0_prep.py", "Validates /science/LSAR (or SSAR) HDF5 structure, extracts orbit vectors, stages scenes into secondaries/<YYYYMMDD>/."),
        ("Automaster", "nisar_automaster.py", "Computes perpendicular/temporal baseline dispersion, selects central Master date, moves Master into master/."),
        ("Step 1: Subset", "nisar_step_1_subset.py", "Reads geolocationGrid, runs SNAP GPT Subset (i_<POL>,q_<POL>), patches slant_range_to_first_pixel & range_sampling_rate."),
        ("Step 2: Coreg & IFG", "nisar_step_2_coreg_ifg.py", "Runs DEM-Assisted-Coregistration (Copernicus 30m DEM, tileExtensionPercent=400), Interferogram, TopoPhaseRemoval."),
        ("Step 3: Export (Final)", "nisar_step_3_export.py", "Runs SNAP StampsExport and sanitizes headers (CRLF -> LF) into INSAR_<MASTER>/{dem, diff0, geo, rslc}."),
    ]
    sy = bot_y + bot_h - 15 * mm
    for s_name, s_file, s_desc in steps_table:
        c.setFillColor(C_ACCENT)
        c.setFont("Helvetica-Bold", 8.0)
        c.drawString(inner_x + 5 * mm, sy, s_name)
        c.setFillColor(C_INK)
        c.setFont("Courier", 7.3)
        c.drawString(inner_x + 36 * mm, sy, s_file)
        c.setFillColor(C_MUTED)
        c.setFont("Helvetica", 7.7)
        c.drawString(inner_x + 84 * mm, sy, s_desc)
        sy -= 10.5 * mm


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    build_diagram()
    w, h = landscape(A4)
    c = canvas.Canvas(str(PDF_OUT), pagesize=(w, h))
    c.setTitle("snap2stamps_nisar Workflow Specification")
    c.setAuthor("snap2stamps_nisar Workbench")
    _draw_page_1(c, w, h)
    c.showPage()
    _draw_page_2(c, w, h)
    c.save()
    print(f"Generated PDF: {PDF_OUT} ({PDF_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
