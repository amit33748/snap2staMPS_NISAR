"""Backend utilities for the snap2stamps_nisar Daylight Survey Workbench."""

import configparser
import glob
import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


GUI_DIR = Path(__file__).resolve().parent
REPO_ROOT = GUI_DIR.parent
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from platform_utils import (  # noqa: E402
    default_project_folder,
    default_snap_folder,
    default_snap_home,
    find_gpt_executable,
)

GRAPHS_DIR = REPO_ROOT / "graphs"
LOGS_DIR = REPO_ROOT / "logs"
ASSETS_DIR = REPO_ROOT / "assets"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"
DEFAULT_CONF_PATH = BIN_DIR / "project_nisar.conf"
EXAMPLE_CONF_PATH = BIN_DIR / "project_nisar.conf.example"

INDIA_CENTER: Tuple[float, float] = (22.5, 79.0)
INDIA_ZOOM: int = 5


PIPELINE_STEPS: List[Dict[str, str]] = [
    {
        "key": "download",
        "label": "Download",
        "detail": "Search NASA ASF / ISRO Bhoonidhi for scenes over your AOI and save .h5 files into raw/.",
    },
    {
        "key": "0",
        "label": "Step 0: Prep",
        "detail": "Verify NISAR HDF5 structure and organize scenes by date into master/ and secondaries/.",
    },
    {
        "key": "automaster",
        "label": "Automaster",
        "detail": "Calculate orbital baselines and automatically pick the best central Master date.",
    },
    {
        "key": "1",
        "label": "Step 1: Subset",
        "detail": "Crop each wide 240 km swath down to your study area box to save disk space and time.",
    },
    {
        "key": "2",
        "label": "Step 2: Coreg & IFG",
        "detail": "Align scenes using 30m DEM and compute topographic-phase-removed interferograms.",
    },
    {
        "key": "3",
        "label": "Step 3: StaMPS Export",
        "detail": "Export StaMPS-ready INSAR_<MASTER>/{dem,diff0,geo,rslc} folders (pipeline stops here).",
    },
]


def load_config(conf_path: Optional[Path] = None) -> Tuple[configparser.ConfigParser, Path, bool]:
    """Load project_nisar.conf case-insensitively, falling back to project_nisar.conf.example."""
    target = Path(conf_path) if conf_path else DEFAULT_CONF_PATH
    cfg = configparser.ConfigParser()

    used_example = False
    if target.exists():
        cfg.read(target, encoding="utf-8")
    elif EXAMPLE_CONF_PATH.exists():
        cfg.read(EXAMPLE_CONF_PATH, encoding="utf-8")
        used_example = True

    return cfg, target, used_example


def cfg_get(cfg: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    """Safely read a stripped string option from ConfigParser."""
    if cfg.has_option(section, key):
        return str(cfg.get(section, key)).strip()
    return default


def cfg_float(cfg: configparser.ConfigParser, section: str, key: str, default: float) -> float:
    """Safely read a float option from ConfigParser."""
    try:
        return float(cfg_get(cfg, section, key, str(default)))
    except ValueError:
        return default


def cfg_int(cfg: configparser.ConfigParser, section: str, key: str, default: int) -> int:
    """Safely read an integer option from ConfigParser."""
    try:
        return int(float(cfg_get(cfg, section, key, str(default))))
    except ValueError:
        return default


def save_config(conf_path: Path, sections_data: Dict[str, Dict[str, str]]) -> Path:
    """Persist structured config sections while merging existing case-insensitive values."""
    src_cfg = configparser.ConfigParser()
    if conf_path.exists():
        src_cfg.read(conf_path, encoding="utf-8")
    elif EXAMPLE_CONF_PATH.exists():
        src_cfg.read(EXAMPLE_CONF_PATH, encoding="utf-8")

    out_cfg = configparser.ConfigParser()
    out_cfg.optionxform = str

    # Copy existing sections/options normalizing keys to uppercase unless camelCase like autoDownload
    preserve_case = {"autodownload": "autoDownload"}
    for sec in src_cfg.sections():
        out_cfg.add_section(sec)
        for k, v in src_cfg.items(sec):
            norm_k = preserve_case.get(k.lower(), k.upper())
            out_cfg.set(sec, norm_k, v)

    for section, kv in sections_data.items():
        if not out_cfg.has_section(section):
            out_cfg.add_section(section)
        for key, value in kv.items():
            # Remove any stale lowercase duplicate before writing canonical key
            if key.lower() != key and out_cfg.has_option(section, key.lower()):
                out_cfg.remove_option(section, key.lower())
            out_cfg.set(section, key, str(value))

    conf_path.parent.mkdir(parents=True, exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as fh:
        out_cfg.write(fh)
    return conf_path


def _count_files(patterns: List[str]) -> List[Path]:
    matches: List[Path] = []
    for pat in patterns:
        matches.extend(Path(p) for p in glob.glob(pat))
    return sorted(set(matches))


def resolve_insar_dir(project_folder: str, master: str = "") -> Optional[Path]:
    """Locate INSAR_<MASTER> folder inside project_folder."""
    if not project_folder:
        return None
    root = Path(project_folder)
    if not root.exists():
        return None
    if master:
        candidate = root / f"INSAR_{master.strip()}"
        if candidate.is_dir():
            return candidate
    insar_dirs = sorted(p for p in root.glob("INSAR_*") if p.is_dir())
    return insar_dirs[-1] if insar_dirs else None


def parse_baselines_table(baselines_path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    """Parse baselines_table.txt into (master_date, rows)."""
    if not baselines_path.exists():
        return "", []

    master_date = ""
    rows: List[Dict[str, Any]] = []
    with open(baselines_path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#") and "Master Date:" in line:
                master_date = line.split("Master Date:", 1)[1].strip()
                continue
            if line.startswith("#") or line.startswith("-") or line.upper().startswith("DATE"):
                continue
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            date_str = parts[0]
            try:
                bperp = float(parts[1])
            except ValueError:
                bperp = 0.0
            try:
                btemp = int(float(parts[2]))
            except ValueError:
                btemp = 0
            coh_part = parts[3]
            is_master = "(MASTER)" in coh_part.upper() or (master_date and date_str == master_date)
            coh_clean = coh_part.replace("(MASTER)", "").replace("(master)", "").strip()
            try:
                coh = float(coh_clean)
            except ValueError:
                coh = 0.0
            rows.append(
                {
                    "date": date_str,
                    "bperp_m": bperp,
                    "btemp_days": btemp,
                    "coherence": coh,
                    "is_master": is_master,
                }
            )
    return master_date, rows


def load_scenes_metadata(meta_path: Path) -> List[Dict[str, Any]]:
    """Read nisar_scenes_metadata.json safely."""
    if not meta_path.exists():
        return []
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("scenes", []) if isinstance(data.get("scenes"), list) else [data]
    except Exception:
        return []
    return []


def scan_workspace(project_folder: str, master_override: str = "") -> Dict[str, Any]:
    """Inspect real NISAR project folders, baselines, metadata, StaMPS exports, and previews."""
    empty_state: Dict[str, Any] = {
        "exists": False,
        "project_folder": project_folder,
        "raw_files": [],
        "raw_count": 0,
        "staged_master": [],
        "staged_secondaries": [],
        "staged_count": 0,
        "subset_files": [],
        "subset_count": 0,
        "coreg_files": [],
        "coreg_count": 0,
        "ifg_files": [],
        "ifg_count": 0,
        "insar_dir": None,
        "stamps_subdirs": [],
        "stamps_diff_count": 0,
        "stamps_export_ready": False,
        "master_date": master_override,
        "baselines": [],
        "scenes_meta": [],
        "previews": [],
        "repo_assets": sorted(ASSETS_DIR.glob("*.png")) if ASSETS_DIR.exists() else [],
    }
    if not project_folder:
        return empty_state

    root = Path(project_folder)
    if not root.exists():
        return empty_state

    raw_files = _count_files([str(root / "raw" / "*.h5")])
    staged_master = _count_files([str(root / "master" / "*.h5")])
    staged_secondaries = _count_files(
        [
            str(root / "secondaries" / "*.h5"),
            str(root / "secondaries" / "*" / "*.h5"),
        ]
    )
    subset_files = _count_files(
        [
            str(root / "subsets" / "master" / "*.dim"),
            str(root / "subsets" / "secondaries" / "*" / "*.dim"),
            str(root / "subsets" / "*.dim"),
        ]
    )
    coreg_files = _count_files([str(root / "coreg" / "*.dim")])
    ifg_files = _count_files([str(root / "ifg" / "*.dim")])

    master_from_bl, baselines = parse_baselines_table(root / "baselines_table.txt")
    effective_master = master_override.strip() or master_from_bl
    insar_dir = resolve_insar_dir(project_folder, effective_master)

    stamps_subdirs: List[str] = []
    stamps_export_ready = False
    stamps_diff_count = 0
    if insar_dir and insar_dir.is_dir():
        stamps_subdirs = sorted(p.name for p in insar_dir.iterdir() if p.is_dir())
        diff_dir = insar_dir / "diff0"
        if diff_dir.is_dir():
            stamps_diff_count = len(list(diff_dir.glob("*.diff")))
        stamps_export_ready = all((insar_dir / d).is_dir() for d in ("dem", "diff0", "geo", "rslc"))

    previews = _count_files([str(root / "previews" / "*.png")])
    scenes_meta = load_scenes_metadata(root / "nisar_scenes_metadata.json")

    return {
        "exists": True,
        "project_folder": str(root),
        "raw_files": raw_files,
        "raw_count": len(raw_files),
        "staged_master": staged_master,
        "staged_secondaries": staged_secondaries,
        "staged_count": len(staged_master) + len(staged_secondaries),
        "subset_files": subset_files,
        "subset_count": len(subset_files),
        "coreg_files": coreg_files,
        "coreg_count": len(coreg_files),
        "ifg_files": ifg_files,
        "ifg_count": len(ifg_files),
        "insar_dir": insar_dir,
        "stamps_subdirs": stamps_subdirs,
        "stamps_diff_count": stamps_diff_count,
        "stamps_export_ready": stamps_export_ready,
        "master_date": effective_master,
        "baselines": baselines,
        "scenes_meta": scenes_meta,
        "previews": previews,
        "repo_assets": empty_state["repo_assets"],
    }


def build_stage_ribbon_data(ws: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert workspace counts into the 6-stage top ribbon model."""
    counts = [
        ("Raw", "HDF5 scenes", ws["raw_count"]),
        ("Staged", "Master + Sec", ws["staged_count"]),
        ("Subsets", "AOI cropped", ws["subset_count"]),
        ("Coreg", "DEM-assisted", ws["coreg_count"]),
        ("IFG", "Wrapped phase", ws["ifg_count"]),
        ("StaMPS Export", "diff0 layers", ws.get("stamps_diff_count", 0)),
    ]
    # Mark the first zero-count stage following non-zero progress as active
    first_incomplete_seen = False
    stages: List[Dict[str, Any]] = []
    for label, sub, cnt in counts:
        done = cnt > 0
        active = False
        if not done and not first_incomplete_seen:
            active = True
            first_incomplete_seen = True
        stages.append({"label": label, "sub": sub, "count": cnt, "done": done, "active": active})
    return stages


def resolve_snap_gpt(cfg: configparser.ConfigParser) -> Tuple[Optional[str], str]:
    """Find SNAP GPT executable from config, PATH, or OS standard locations."""
    snap_folder = cfg_get(cfg, "SNAP_GPT", "SNAP_INSTALLATION_FOLDER", "")
    hit = find_gpt_executable(cfg)
    if hit:
        return hit, f"Detected ({hit})"
    return None, f"Not found (checked '{snap_folder or default_snap_folder()}' and PATH)"


def build_install_deps_command() -> List[str]:
    """Return pip command to install/verify requirements.txt inside the active Python env."""
    return [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)]


def run_system_doctor(cfg: configparser.ConfigParser) -> List[Dict[str, str]]:
    """Run non-destructive environment, dependency, SNAP, graph, and disk checks."""
    checks: List[Dict[str, str]] = []

    exe_lower = sys.executable.lower()
    in_arcgis = "arcgispro" in exe_lower
    in_venv = ".venv" in exe_lower or sys.prefix != sys.base_prefix
    if in_arcgis:
        py_status = "err"
        py_note = f"Running inside ArcGIS Pro Python ({sys.executable}). Launch with .venv instead."
    elif in_venv:
        py_status = "ok"
        py_note = f"Isolated .venv Python {sys.version.split()[0]} ({sys.executable})"
    else:
        py_status = "warn"
        py_note = f"System Python {sys.version.split()[0]} ({sys.executable}); .venv recommended."

    checks.append(
        {
            "component": "Python Environment",
            "status": py_status,
            "detail": py_note,
        }
    )

    required_mods = [
        ("fastapi", "Web API server"),
        ("uvicorn", "ASGI runner"),
        ("h5py", "HDF5 reader"),
        ("numpy", "Array processing"),
        ("matplotlib", "Plotting and previews"),
        ("asf_search", "NASA ASF catalog"),
        ("requests", "HTTP Range download"),
        ("shapely", "AOI geometry"),
        ("pyproj", "Coordinate transforms"),
        ("geopandas", "Vector AOI support"),
        ("reportlab", "Workflow PDF builder"),
        ("PIL", "Image handling"),
    ]
    missing: List[str] = []
    for mod_name, _ in required_mods:
        try:
            importlib.import_module(mod_name)
        except Exception:
            missing.append(mod_name)
    if missing:
        checks.append(
            {
                "component": "Python Dependencies",
                "status": "err",
                "detail": f"Missing: {', '.join(missing)}",
            }
        )
    else:
        checks.append(
            {
                "component": "Python Dependencies",
                "status": "ok",
                "detail": f"All {len(required_mods)} core modules available",
            }
        )

    gpt_path, gpt_msg = resolve_snap_gpt(cfg)
    checks.append(
        {
            "component": "ESA SNAP GPT",
            "status": "ok" if gpt_path else "warn",
            "detail": gpt_msg,
        }
    )

    graph_files = [
        GRAPHS_DIR / "nisar_coreg_ifg.xml",
        GRAPHS_DIR / "nisar_stamps_export.xml",
    ]
    missing_graphs = [g.name for g in graph_files if not g.exists()]
    checks.append(
        {
            "component": "SNAP XML Graphs",
            "status": "ok" if not missing_graphs else "err",
            "detail": "Ready (coreg_ifg, stamps_export)"
            if not missing_graphs
            else f"Missing: {', '.join(missing_graphs)}",
        }
    )

    checks.append(
        {
            "component": "Active Config",
            "status": "ok" if DEFAULT_CONF_PATH.exists() else "warn",
            "detail": str(DEFAULT_CONF_PATH)
            if DEFAULT_CONF_PATH.exists()
            else "Using template defaults; save Setup to create bin/project_nisar.conf",
        }
    )

    project_folder = cfg_get(cfg, "PROJECT_DEFINITION", "PROJECTFOLDER", "")
    disk_probe = Path(project_folder) if project_folder else REPO_ROOT
    while not disk_probe.exists() and disk_probe.parent != disk_probe:
        disk_probe = disk_probe.parent
    try:
        usage = shutil.disk_usage(disk_probe)
        free_gb = usage.free / (1024**3)
        status = "ok" if free_gb >= 20.0 else ("warn" if free_gb >= 5.0 else "err")
        checks.append(
            {
                "component": "Workspace Disk Space",
                "status": status,
                "detail": f"{free_gb:.1f} GB free on {disk_probe.anchor or disk_probe}",
            }
        )
    except Exception as exc:
        checks.append({"component": "Workspace Disk Space", "status": "warn", "detail": str(exc)})

    return checks


def build_pipeline_command(
    step_key: str,
    conf_path: Path,
    project_folder: str,
    master: str = "",
    da_thresh: float = 0.4,
    rg_patches: int = 2,
    az_patches: int = 2,
    rg_overlap: int = 50,
    az_overlap: int = 50,
) -> Tuple[List[str], Path, Optional[str]]:
    """Build exact subprocess command and working directory for auto_run or mt_prep_nisar."""
    if step_key == "mt_prep":
        insar_dir = resolve_insar_dir(project_folder, master)
        if not insar_dir:
            return (
                [],
                REPO_ROOT,
                "INSAR_<MASTER> folder not found yet. Run Step 3 (StaMPS Export) first.",
            )
        cmd = [
            sys.executable,
            str(BIN_DIR / "mt_prep_nisar.py"),
            "-F",
            str(conf_path),
            "--da_thresh",
            f"{da_thresh:.2f}",
            "--rg_patches",
            str(int(rg_patches)),
            "--az_patches",
            str(int(az_patches)),
            "--rg_overlap",
            str(int(rg_overlap)),
            "--az_overlap",
            str(int(az_overlap)),
        ]
        return cmd, insar_dir, None

    cmd = [
        sys.executable,
        str(BIN_DIR / "auto_run.py"),
        "-F",
        str(conf_path),
        "--step",
        step_key,
    ]
    return cmd, REPO_ROOT, None


def list_recent_logs(limit: int = 8) -> List[Path]:
    """Return most recently modified .log files in logs/."""
    if not LOGS_DIR.exists():
        return []
    logs = sorted(LOGS_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[:limit]


def read_log_tail(log_path: Path, max_lines: int = 160) -> str:
    """Read last max_lines lines of a log file safely."""
    if not log_path.exists():
        return ""
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    return "".join(lines[-max_lines:])


def render_baseline_figure(baselines: List[Dict[str, Any]]) -> plt.Figure:
    """Create a Daylight Survey styled Matplotlib figure of temporal vs perpendicular baselines."""
    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=130)
    fig.patch.set_facecolor("#fdfcf9")
    ax.set_facecolor("#f8f6f1")

    if not baselines:
        ax.text(
            0.5,
            0.5,
            "No baselines_table.txt rows found yet",
            ha="center",
            va="center",
            color="#56606d",
            fontsize=10,
            transform=ax.transAxes,
        )
        ax.set_axis_off()
        return fig

    master_pts = [b for b in baselines if b.get("is_master")]
    sec_pts = [b for b in baselines if not b.get("is_master")]

    m_x = master_pts[0]["btemp_days"] if master_pts else 0
    m_y = master_pts[0]["bperp_m"] if master_pts else 0.0

    for row in sec_pts:
        ax.plot(
            [m_x, row["btemp_days"]],
            [m_y, row["bperp_m"]],
            color="#8e9aa8",
            linewidth=1.2,
            linestyle="-",
            zorder=2,
        )

    if sec_pts:
        sx = [r["btemp_days"] for r in sec_pts]
        sy = [r["bperp_m"] for r in sec_pts]
        ax.scatter(
            sx,
            sy,
            s=62,
            color="#29628a",
            edgecolors="#fdfcf9",
            linewidths=1.2,
            zorder=4,
            label="Secondary",
        )
        for r in sec_pts:
            offset_y = 6 if r["bperp_m"] >= 0 else -11
            ax.annotate(
                f"{r['date']}\n({r['bperp_m']:+.1f} m)",
                (r["btemp_days"], r["bperp_m"]),
                textcoords="offset points",
                xytext=(0, offset_y),
                ha="center",
                fontsize=7.2,
                color="#18202b",
            )

    if master_pts:
        ax.scatter(
            [m_x],
            [m_y],
            s=115,
            color="#c84b28",
            edgecolors="#fdfcf9",
            linewidths=1.5,
            zorder=5,
            label=f"Master ({master_pts[0]['date']})",
        )

    ax.axhline(0, color="#ded9ce", linewidth=0.9, zorder=1)
    ax.axvline(0, color="#ded9ce", linewidth=0.9, zorder=1)
    ax.grid(True, linestyle=":", color="#ded9ce", alpha=0.9, zorder=0)

    ax.set_xlabel("Temporal Baseline (days)", fontsize=8.5, color="#18202b", labelpad=6)
    ax.set_ylabel("Perpendicular Baseline (m)", fontsize=8.5, color="#18202b", labelpad=6)
    ax.tick_params(colors="#56606d", labelsize=8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#c5beb0")

    ax.margins(x=0.14, y=0.22)
    ax.legend(
        frameon=True,
        facecolor="#fdfcf9",
        edgecolor="#ded9ce",
        fontsize=8,
        loc="upper left",
    )
    fig.tight_layout()
    return fig


def _read_envi_phase_band(data_dir: Path) -> Optional[np.ndarray]:
    """Read first float32 Phase_*.img band inside a BEAM-DIMAP .data folder."""
    phase_imgs = sorted(data_dir.glob("Phase_ifg*.img"))
    if not phase_imgs:
        return None
    img_path = phase_imgs[0]
    hdr_path = img_path.with_suffix(".hdr")
    if not hdr_path.exists():
        return None

    samples = 0
    lines = 0
    byte_order = 0
    data_type = 4
    with open(hdr_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "=" not in line:
                continue
            k, v = [p.strip().lower() for p in line.split("=", 1)]
            if k == "samples":
                samples = int(v)
            elif k == "lines":
                lines = int(v)
            elif k == "byte order":
                byte_order = int(v)
            elif k == "data type":
                data_type = int(v)
    if samples <= 0 or lines <= 0 or data_type != 4:
        return None

    dtype = ">f4" if byte_order == 1 else "<f4"
    arr = np.fromfile(img_path, dtype=dtype, count=samples * lines)
    if arr.size != samples * lines:
        return None
    arr = arr.reshape((lines, samples))
    step = max(1, max(lines, samples) // 700)
    return arr[::step, ::step]


def generate_quicklook_preview(project_folder: str) -> Tuple[bool, str]:
    """Render an overview PNG from existing ifg/*.dim interferogram phase bands."""
    root = Path(project_folder)
    ifg_dirs = sorted(root.glob("ifg/*.data"))
    if not ifg_dirs:
        return False, "No ifg/*.data products found. Run Step 2 first."

    panels: List[Tuple[str, np.ndarray]] = []
    for d in ifg_dirs[:6]:
        arr = _read_envi_phase_band(d)
        if arr is not None:
            panels.append((d.stem, arr))

    if not panels:
        return False, "Could not locate readable Phase_ifg*.img bands inside ifg/*.data."

    cols = min(3, len(panels))
    rows = int(np.ceil(len(panels) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 3.6 * rows), dpi=130)
    fig.patch.set_facecolor("#fdfcf9")
    axes_arr = np.atleast_1d(axes).ravel()

    for idx, ax in enumerate(axes_arr):
        if idx >= len(panels):
            ax.set_axis_off()
            continue
        name, arr = panels[idx]
        valid = np.where(arr == 0.0, np.nan, arr)
        ax.imshow(valid, cmap="twilight_shifted", vmin=-np.pi, vmax=np.pi, aspect="auto")
        ax.set_title(name, fontsize=8.5, color="#18202b", pad=5)
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_color("#ded9ce")

    fig.suptitle("NISAR Wrapped Interferogram Phase Quicklook", fontsize=11, color="#18202b")
    fig.tight_layout()
    out_dir = root / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "gui_phase_quicklook.png"
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return True, str(out_path)
