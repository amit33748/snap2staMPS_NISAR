"""FastAPI server and job runner for the snap2stamps_nisar HTML/CSS/JS Workbench."""

import argparse
import io
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

GUI_DIR = Path(__file__).resolve().parent
STATIC_DIR = GUI_DIR / "static"
REPO_ROOT = GUI_DIR.parent
BIN_DIR = REPO_ROOT / "bin"
for _p in (str(GUI_DIR), str(BIN_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend  # noqa: E402
from platform_utils import configure_utf8_console  # noqa: E402

configure_utf8_console()


class JobManager:
    """Thread-safe single-process job manager with live log buffering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running: bool = False
        self.step_key: str = ""
        self.label: str = ""
        self.exit_code: Optional[int] = None
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.lines: List[str] = []
        self._proc: Optional[subprocess.Popen] = None

    def start(self, cmd: List[str], cwd: Path, step_key: str, label: str) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError(f"Another task is already running: {self.label}")
            self.running = True
            self.step_key = step_key
            self.label = label
            self.exit_code = None
            self.started_at = time.time()
            self.finished_at = None
            self.lines = [f"$ {' '.join(cmd)}", f"cwd: {cwd}", ""]
            self._proc = None

        thread = threading.Thread(target=self._worker, args=(cmd, cwd), daemon=True)
        thread.start()

    def _worker(self, cmd: List[str], cwd: Path) -> None:
        try:
            child_env = {**os.environ, "PYTHONUTF8": "1"}
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            with self._lock:
                self._proc = proc
            if proc.stdout is not None:
                for raw in proc.stdout:
                    clean = raw.rstrip("\r\n")
                    with self._lock:
                        self.lines.append(clean)
                        if len(self.lines) > 4000:
                            self.lines = self.lines[-3000:]
            rc = proc.wait()
            with self._lock:
                self.exit_code = rc
        except Exception as exc:
            with self._lock:
                self.lines.append(f"[ERROR] Failed to execute command: {exc}")
                self.exit_code = 1
        finally:
            with self._lock:
                self.running = False
                self.finished_at = time.time()
                self._proc = None

    def stop(self) -> bool:
        with self._lock:
            proc = self._proc
            if not self.running or proc is None:
                return False
            self.lines.append("[USER] Termination requested...")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return True

    def snapshot(self, offset: int = 0) -> Dict[str, Any]:
        with self._lock:
            total = len(self.lines)
            safe_offset = max(0, min(offset, total))
            return {
                "running": self.running,
                "step_key": self.step_key,
                "label": self.label,
                "exit_code": self.exit_code,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "total_lines": total,
                "lines": self.lines[safe_offset:],
            }


class ConfigPayload(BaseModel):
    project_folder: str
    graphs_folder: str = "./graphs"
    snap_folder: str
    snap_home: str
    lonmin: float
    latmin: float
    lonmax: float
    latmax: float
    provider: str = "ASF"
    frequency: str = "L"
    polarisation: str = "HH"
    track: str = ""
    start: str = ""
    end: str = ""
    master: str = ""
    overwrite: str = "N"
    cpu: int = 16
    cache: str = "32G"
    dem_name: str = "Copernicus 30m Global DEM"
    asf_user: str = ""
    asf_pass: str = ""
    bhoonidhi_user: str = ""
    bhoonidhi_pass: str = ""


jobs = JobManager()
app = FastAPI(title="snap2stamps_nisar Workbench", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _serialize_state() -> Dict[str, Any]:
    cfg, conf_path, used_example = backend.load_config(backend.DEFAULT_CONF_PATH)
    pf = backend.cfg_get(cfg, "PROJECT_DEFINITION", "PROJECTFOLDER", backend.default_project_folder())
    master = backend.cfg_get(cfg, "PROC_PARAMETERS", "MASTER", "")
    ws = backend.scan_workspace(pf, master)
    effective_master = master or ws.get("master_date", "")

    config_view = {
        "project_folder": pf,
        "graphs_folder": backend.cfg_get(cfg, "PROJECT_DEFINITION", "GRAPHSFOLDER", "./graphs"),
        "snap_folder": backend.cfg_get(cfg, "SNAP_GPT", "SNAP_INSTALLATION_FOLDER", backend.default_snap_folder()),
        "snap_home": backend.cfg_get(cfg, "SNAP_GPT", "SNAP_HOME_DIR", backend.default_snap_home()),
        "lonmin": backend.cfg_float(cfg, "AOI_DEFINITION", "LONMIN", 76.68),
        "latmin": backend.cfg_float(cfg, "AOI_DEFINITION", "LATMIN", 30.65),
        "lonmax": backend.cfg_float(cfg, "AOI_DEFINITION", "LONMAX", 76.88),
        "latmax": backend.cfg_float(cfg, "AOI_DEFINITION", "LATMAX", 30.82),
        "provider": backend.cfg_get(cfg, "DOWNLOAD_SOURCE", "PROVIDER", "ASF").upper(),
        "frequency": backend.cfg_get(cfg, "PROC_PARAMETERS", "FREQUENCY", "L").upper(),
        "polarisation": backend.cfg_get(cfg, "PROC_PARAMETERS", "POLARISATION", "HH").upper(),
        "track": backend.cfg_get(cfg, "ASF_SEARCH", "TRACK", ""),
        "start": backend.cfg_get(cfg, "ASF_SEARCH", "START", "2026-09-22"),
        "end": backend.cfg_get(cfg, "ASF_SEARCH", "END", "2026-11-22"),
        "master": master,
        "overwrite": backend.cfg_get(cfg, "PROC_OPTIONS", "OVERWRITE", "N").upper(),
        "cpu": backend.cfg_int(cfg, "COMPUTING_RESOURCES", "CPU", 16),
        "cache": backend.cfg_get(cfg, "COMPUTING_RESOURCES", "CACHE", "32G"),
        "dem_name": backend.cfg_get(cfg, "PROC_PARAMETERS", "DEM_NAME", "Copernicus 30m Global DEM"),
        "asf_user": backend.cfg_get(cfg, "ASF_SEARCH", "ASF_USER", ""),
        "has_asf_pass": bool(backend.cfg_get(cfg, "ASF_SEARCH", "ASF_PASS", "")),
        "bhoonidhi_user": backend.cfg_get(cfg, "BHOONIDHI_API", "USERNAME", ""),
        "has_bh_pass": bool(backend.cfg_get(cfg, "BHOONIDHI_API", "PASSWORD", "")),
        "used_example": used_example,
        "conf_path": str(conf_path),
    }

    workspace_view = {
        "exists": ws["exists"],
        "project_folder": ws["project_folder"],
        "raw_count": ws["raw_count"],
        "staged_count": ws["staged_count"],
        "subset_count": ws["subset_count"],
        "coreg_count": ws["coreg_count"],
        "ifg_count": ws["ifg_count"],
        "stamps_diff_count": ws["stamps_diff_count"],
        "stamps_export_ready": ws["stamps_export_ready"],
        "insar_dir": str(ws["insar_dir"]) if ws.get("insar_dir") else "",
        "master_date": effective_master,
        "baselines": ws["baselines"],
        "previews": [p.name for p in ws.get("previews", [])],
    }

    return {
        "config": config_view,
        "workspace": workspace_view,
        "stages": backend.build_stage_ribbon_data(ws),
        "doctor": backend.run_system_doctor(cfg),
        "steps": backend.PIPELINE_STEPS,
        "logs": [p.name for p in backend.list_recent_logs(12)],
        "india_map": {"center": list(backend.INDIA_CENTER), "zoom": backend.INDIA_ZOOM},
        "job": jobs.snapshot(0),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_file = STATIC_DIR / "index.html"
    return HTMLResponse(html_file.read_text(encoding="utf-8"))


@app.get("/api/state")
def get_state() -> Dict[str, Any]:
    return _serialize_state()


@app.post("/api/config")
def post_config(payload: ConfigPayload) -> Dict[str, Any]:
    if payload.lonmin >= payload.lonmax or payload.latmin >= payload.latmax:
        raise HTTPException(status_code=400, detail="Lon Min must be < Lon Max and Lat Min must be < Lat Max.")
    if not payload.project_folder.strip():
        raise HTTPException(status_code=400, detail="Project output folder cannot be empty.")

    cfg, _, _ = backend.load_config(backend.DEFAULT_CONF_PATH)
    existing_asf_pass = backend.cfg_get(cfg, "ASF_SEARCH", "ASF_PASS", "")
    existing_bh_pass = backend.cfg_get(cfg, "BHOONIDHI_API", "PASSWORD", "")

    sections_data = {
        "PROJECT_DEFINITION": {
            "PROJECTFOLDER": payload.project_folder.strip(),
            "GRAPHSFOLDER": payload.graphs_folder.strip() or "./graphs",
        },
        "PROC_OPTIONS": {
            "OVERWRITE": "Y" if payload.overwrite.upper() == "Y" else "N",
        },
        "PROC_PARAMETERS": {
            "FREQUENCY": payload.frequency.upper(),
            "POLARISATION": payload.polarisation.upper(),
            "MASTER": payload.master.strip(),
            "MASTERSEL": "MANUAL" if payload.master.strip() else "AUTO",
            "DEM_NAME": payload.dem_name,
        },
        "AOI_DEFINITION": {
            "AOI_MODE": backend.cfg_get(cfg, "AOI_DEFINITION", "AOI_MODE", "BBOX"),
            "LONMIN": f"{payload.lonmin:.4f}",
            "LATMIN": f"{payload.latmin:.4f}",
            "LONMAX": f"{payload.lonmax:.4f}",
            "LATMAX": f"{payload.latmax:.4f}",
        },
        "DOWNLOAD_SOURCE": {
            "PROVIDER": payload.provider.upper(),
        },
        "ASF_SEARCH": {
            "ASF_USER": payload.asf_user.strip(),
            "ASF_PASS": payload.asf_pass if payload.asf_pass else existing_asf_pass,
            "TRACK": payload.track.strip(),
            "START": payload.start.strip(),
            "END": payload.end.strip(),
        },
        "BHOONIDHI_API": {
            "USERNAME": payload.bhoonidhi_user.strip(),
            "PASSWORD": payload.bhoonidhi_pass if payload.bhoonidhi_pass else existing_bh_pass,
        },
        "SNAP_GPT": {
            "SNAP_INSTALLATION_FOLDER": payload.snap_folder.strip(),
            "SNAP_HOME_DIR": payload.snap_home.strip(),
        },
        "COMPUTING_RESOURCES": {
            "CPU": str(max(1, int(payload.cpu))),
            "CACHE": payload.cache.strip() or "32G",
        },
    }

    saved_path = backend.save_config(backend.DEFAULT_CONF_PATH, sections_data)
    return {"ok": True, "path": str(saved_path), "state": _serialize_state()}


@app.post("/api/run/{step_key}")
def run_step(step_key: str) -> Dict[str, Any]:
    if step_key == "install_deps":
        cmd = backend.build_install_deps_command()
        cwd = REPO_ROOT
        label = "Install Python Dependencies (requirements.txt)"
    else:
        allowed = {s["key"]: s["label"] for s in backend.PIPELINE_STEPS}
        allowed["all"] = "Full Pipeline (Steps 0 to 3: StaMPS Export)"
        if step_key not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported step: {step_key}")
        if not backend.DEFAULT_CONF_PATH.exists():
            raise HTTPException(status_code=400, detail="Save your configuration file first before running steps.")
        cfg, conf_path, _ = backend.load_config(backend.DEFAULT_CONF_PATH)
        pf = backend.cfg_get(cfg, "PROJECT_DEFINITION", "PROJECTFOLDER", "")
        master = backend.cfg_get(cfg, "PROC_PARAMETERS", "MASTER", "")
        cmd, cwd, err = backend.build_pipeline_command(step_key, conf_path, pf, master)
        if err:
            raise HTTPException(status_code=400, detail=err)
        label = allowed[step_key]

    try:
        jobs.start(cmd, cwd, step_key, label)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"ok": True, "label": label, "step_key": step_key}


@app.post("/api/stop")
def stop_job() -> Dict[str, Any]:
    stopped = jobs.stop()
    return {"ok": stopped}


@app.get("/api/job")
def get_job(offset: int = 0) -> Dict[str, Any]:
    return jobs.snapshot(offset)


@app.get("/api/logs/{filename}")
def get_log_file(filename: str) -> Dict[str, Any]:
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(".log"):
        raise HTTPException(status_code=400, detail="Invalid log filename.")
    target = backend.LOGS_DIR / safe_name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Log file not found.")
    return {"filename": safe_name, "content": backend.read_log_tail(target, max_lines=300)}


@app.get("/api/baseline-chart.png")
def get_baseline_chart() -> Response:
    cfg, _, _ = backend.load_config(backend.DEFAULT_CONF_PATH)
    pf = backend.cfg_get(cfg, "PROJECT_DEFINITION", "PROJECTFOLDER", "")
    master = backend.cfg_get(cfg, "PROC_PARAMETERS", "MASTER", "")
    ws = backend.scan_workspace(pf, master)
    fig = backend.render_baseline_figure(ws["baselines"])
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/quicklook")
def post_quicklook() -> Dict[str, Any]:
    cfg, _, _ = backend.load_config(backend.DEFAULT_CONF_PATH)
    pf = backend.cfg_get(cfg, "PROJECT_DEFINITION", "PROJECTFOLDER", "")
    ok, msg = backend.generate_quicklook_preview(pf)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "path": msg, "state": _serialize_state()}


@app.get("/api/preview/{filename}")
def get_preview_image(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.lower().endswith(".png"):
        raise HTTPException(status_code=400, detail="Invalid preview image filename.")
    cfg, _, _ = backend.load_config(backend.DEFAULT_CONF_PATH)
    pf = backend.cfg_get(cfg, "PROJECT_DEFINITION", "PROJECTFOLDER", "")
    target = Path(pf) / "previews" / safe_name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Preview image not found.")
    return FileResponse(str(target), media_type="image/png")


def main() -> None:
    parser = argparse.ArgumentParser(description="snap2stamps_nisar Workbench Web Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--open", action="store_true", help="Open browser automatically")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
