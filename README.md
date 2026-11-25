# NISAR `snap2stamps` (`snap2stamps_nisar`)

**Cross-Platform (Windows, Linux, macOS) Pre-Processing Pipeline & Web Workbench for NASA-ISRO SAR (NISAR) L/S-Band RSLC HDF5 to StaMPS Export**

`snap2stamps_nisar` automates pre-processing of **NISAR L1 RSLC** HDF5 scenes from catalog download through HDF5 validation, baseline-based master selection, wide-swath spatial subsetting, SNAP 14 DEM-assisted coregistration and topographic phase removal, and **Step 3 StaMPS Export** (`INSAR_<MASTER>/{dem, diff0, geo, rslc}`). The automated pipeline stops cleanly at **StaMPS Export** so downstream PS selection and time-series analysis remain under your control in StaMPS / MATLAB / Octave.

---

## Key Capabilities

- **Cross-Platform Support (Windows, Linux, macOS)**: Unified ESA SNAP `gpt` discovery ([bin/platform_utils.py](bin/platform_utils.py)), UTF-8 console/subprocess handling, OS-specific launchers (`launch_workbench.bat` / `launch_workbench.sh`), and `.gitattributes` LF/CRLF enforcement.
- **HTML / CSS / JS Web Workbench (`gui/server.py` + `gui/static/`)**: Designed for straightforward interactive setup and monitoring inside an isolated `.venv` (avoiding ArcGIS Pro Python conflicts):
  - **Environment & Dependency Checker**: Verifies Python `.venv`, installed packages, SNAP `gpt`, XML graphs, and free disk space, with one-click `pip install -r requirements.txt`.
  - **India-Focused Open-Source Map**: Browser-native Leaflet map centered on India with a top-right basemap switcher (`OpenStreetMap`, `OpenTopoMap Terrain`, `OSM Humanitarian`, `Satellite Imagery`) and rectangle drawing synced to the config editor.
  - **Live Log Streaming & Output Inspector**: Real-time step execution logs (`Download` through `Step 3: StaMPS Export`), baseline geometry plots, and quicklook preview inspection.
- **Resumable Catalog Downloader (`bin/asf_nisar_downloader.py`)**: Searches NASA ASF by AOI, dates, and track with HTTP `206 Range` resumable downloads and local polarization filtering.
- **HDF5 Validation & Automaster (`bin/nisar_step_0_prep.py`, `bin/nisar_automaster.py`)**: Validates `/science/LSAR` (or `SSAR`) granules, extracts orbit state vectors, stages scenes by `<YYYYMMDD>`, and computes `baselines_table.txt`.
- **Spatial Subsetting & Metadata Patching (`bin/nisar_step_1_subset.py`)**: Computes exact pixel bounds from `geolocationGrid`, crops `i_<POL>,q_<POL>` bands via SNAP GPT, and updates `slant_range_to_first_pixel`, `range_sampling_rate`, and scene center metadata.
- **DEM-Assisted Coregistration & StaMPS Export (`bin/nisar_step_2_coreg_ifg.py`, `bin/nisar_step_3_export.py`)**: Runs `DEM-Assisted-Coregistration` (`tileExtensionPercent=400`), `Interferogram`, `TopoPhaseRemoval`, and `StampsExport` with `CRLF` to `LF` header sanitization.

---

## Processing Workflow

![NISAR InSAR to StaMPS Export Flowchart](docs/nisar_workflow_diagram.png)

Full technical guide and printable 2-page PDF: [docs/NISAR_INSAR_WORKFLOW_GUIDE.md](docs/NISAR_INSAR_WORKFLOW_GUIDE.md) | [docs/NISAR_INSAR_WORKFLOW.pdf](docs/NISAR_INSAR_WORKFLOW.pdf)

---

## Repository Structure

```text
snap2stamps_nisar/
├── bin/
│   ├── project_nisar.conf.example   # Configuration template (copy or generate via GUI)
│   ├── platform_utils.py            # Cross-platform SNAP GPT & OS path helpers
│   ├── auto_run.py                  # CLI pipeline orchestrator (stops at Step 3 Export)
│   ├── asf_nisar_downloader.py      # NASA ASF resumable RSLC downloader
│   ├── bhoonidhi_nisar_downloader.py# ISRO Bhoonidhi STAC client (experimental)
│   ├── friendly_logger.py           # Logging & SNAP/StaMPS error translator
│   ├── nisar_step_0_prep.py         # HDF5 validator, orbit extractor & staging
│   ├── nisar_automaster.py          # Optimal master selector & baseline table
│   ├── nisar_step_1_subset.py       # SNAP GPT spatial subset + .dim metadata patcher
│   ├── nisar_step_2_coreg_ifg.py    # Batch DEM-Assisted Coregistration & IFG
│   └── nisar_step_3_export.py       # Batch StampsExport & CRLF -> LF sanitizer
├── docs/
│   ├── nisar_workflow_diagram.png   # Workflow flowchart (PNG)
│   ├── nisar_workflow_diagram.svg   # Workflow flowchart (SVG)
│   ├── NISAR_INSAR_WORKFLOW.pdf     # Printable 2-page workflow specification
│   └── NISAR_INSAR_WORKFLOW_GUIDE.md# Detailed technical workflow guide
├── graphs/
│   ├── nisar_coreg_ifg.xml          # SNAP 14 Coregistration + IFG + TopoPhaseRemoval graph
│   └── nisar_stamps_export.xml      # SNAP 14 StampsExport graph
├── gui/
│   ├── server.py                    # FastAPI server & live job runner
│   ├── backend.py                   # Config, workspace scanner & system doctor
│   ├── static/                      # Pure HTML, CSS & JS Leaflet Workbench UI
│   ├── run_dashboard.bat            # Windows GUI launcher (.venv)
│   └── run_dashboard.sh             # Linux/macOS GUI launcher (.venv)
├── launch_workbench.bat             # Root Windows launcher (.venv)
├── launch_workbench.sh              # Root Linux/macOS launcher (.venv)
├── tests/
│   └── test_prep_auto.py            # Automated unit tests
├── .gitattributes
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Quickstart

### 1. Prerequisites
- **Python**: `3.10` – `3.13` in an independent `.venv` (not ArcGIS Pro's Python)
- **ESA SNAP**: `14.0+` with Microwave Toolbox (`NisarRSLCProductReader`, `DEM-Assisted-Coregistration`, `Interferogram`, `TopoPhaseRemoval`, `StampsExport`)
- **StaMPS** *(optional downstream)*: `v4.1b+` with MATLAB or GNU Octave for external `mt_prep_snap` and `stamps(1, 8)` after Step 3 Export

### 2. Launch the Web Workbench (Recommended)

**Windows (PowerShell / CMD):**
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\launch_workbench.bat
```

**Linux / macOS:**
```bash
bash launch_workbench.sh
```

The browser opens `http://127.0.0.1:8501`, where you can check/install dependencies, draw your study area over India, configure `bin/project_nisar.conf`, run pipeline steps (`Download` through `Step 3: StaMPS Export`), and watch live logs.

### 3. Run via CLI
```bash
python bin/auto_run.py -F bin/project_nisar.conf --step download
python bin/auto_run.py -F bin/project_nisar.conf --step 0
python bin/auto_run.py -F bin/project_nisar.conf --step automaster
python bin/auto_run.py -F bin/project_nisar.conf --step 1
python bin/auto_run.py -F bin/project_nisar.conf --step 2
python bin/auto_run.py -F bin/project_nisar.conf --step 3
```
*(Or `--step all` to run Steps 0 through 3, stopping at `INSAR_<MASTER>/` export.)*

---

## Multi-Image AOI Notes & Known Limitations

- **AOI straddling two frames on the same date**: `nisar_step_2_coreg_ifg.py` deduplicates inputs by `<YYYYMMDD>` and processes the first frame per acquisition date (no slice mosaic operator is currently included).
- **Multiple overlapping tracks**: Pin one `TRACK` number per `PROJECTFOLDER` so all acquisitions share compatible orbital geometry.
- **ISRO Bhoonidhi Downloader**: Experimental STAC REST implementation (`bhoonidhi_nisar_downloader.py`); not yet validated against live production endpoints.
- **Automated Tests**: Run `python -m unittest discover -s tests -v`.

---

## License
Open-source under the Apache 2.0 / MIT License.
