#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asf_nisar_downloader.py: NASA ASF (Alaska Satellite Facility) Downloader for NISAR RSLC Data.
Integrated into snap2stamps_nisar processing pipeline.

Uses the official asf-search SDK to search and download NISAR RSLC/GSLC HDF5 granules
with NASA Earthdata credentials, spatial AOI filtering (BBOX, Shapefile, GeoJSON),
and multi-threaded downloading with friendly progress tracking.
"""

import os
import sys
import argparse
import configparser
import warnings
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Tuple

# Suppress harmless third-party warnings
warnings.filterwarnings('ignore')

# Add parent and local bin directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    from friendly_logger import FriendlyLogger
except ImportError:
    class FriendlyLogger:
        def __init__(self, log_file=None): self.log_file = log_file
        def banner(self, title): print(f"\n{'='*75}\n  {title}\n{'='*75}\n")
        def step_header(self, s, t, title, desc, typical=""): print(f"\n[STEP {s}/{t}] {title}\n  {desc}\n{'-'*75}")
        def step_footer(self, s, title): print(f"✅ [STEP {s}] {title} finished successfully.\n")
        def info(self, msg): print(f"ℹ️  {msg}")
        def success(self, msg): print(f"🎉 {msg}")
        def warning(self, msg): print(f"⚠️  WARNING: {msg}")
        def subtask(self, msg, status="done", duration=None): print(f"   [{status}] {msg}")
        def progress(self, cur, tot, item=""): print(f"⏳ [{cur}/{tot}] {item}")
        def error(self, title, problem, solutions):
            print(f"\n❌ ERROR: {title}\nProblem: {problem}\nSolutions:")
            for idx, sol in enumerate(solutions, 1): print(f"   {idx}. {sol}")
        def translate_and_report_error(self, err, current_step="ASF Download"):
            print(f"\n❌ Error in {current_step}: {err}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="NASA ASF NISAR Data Downloader for snap2stamps_nisar Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--ProjFile", "-F",
        type=str,
        default=None,
        help="Path to project_nisar.conf configuration file"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Target output directory for downloaded NISAR scenes"
    )
    parser.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="Area of interest bounding box as 'lonmin,latmin,lonmax,latmax'"
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Start date in YYYY-MM-DD format"
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date in YYYY-MM-DD format"
    )
    parser.add_argument(
        "--track",
        type=int,
        default=None,
        help="Relative orbit / track number (optional)"
    )
    parser.add_argument(
        "--polarization",
        type=str,
        default=None,
        help="Polarization filter (e.g. HH, VV, HH+HV)"
    )
    parser.add_argument(
        "--product-type",
        type=str,
        default="RSLC",
        choices=["RSLC", "GSLC", "SLC", "GCOV"],
        help="NISAR Product level"
    )
    parser.add_argument(
        "--user",
        type=str,
        default=None,
        help="NASA Earthdata Login username"
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="NASA Earthdata Login password"
    )
    parser.add_argument(
        "--processes", "-p",
        type=int,
        default=None,
        help="Number of concurrent download processes"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform search query and display results without downloading"
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=250,
        help="Maximum number of scenes to return from ASF search"
    )
    return parser.parse_args()


def load_aoi_wkt(config: Optional[configparser.ConfigParser], cli_bbox: Optional[str] = None, logger: Optional[FriendlyLogger] = None) -> str:
    """Parses AOI definition into a standardized WKT polygon string."""
    # Priority 1: CLI bbox override
    if cli_bbox:
        try:
            parts = [float(x.strip()) for x in cli_bbox.split(',')]
            if len(parts) == 4:
                lonmin, latmin, lonmax, latmax = parts
                return f"POLYGON(({lonmin} {latmin}, {lonmax} {latmin}, {lonmax} {latmax}, {lonmin} {latmax}, {lonmin} {latmin}))"
        except Exception as e:
            if logger:
                logger.warning(f"Could not parse CLI bbox '{cli_bbox}': {e}. Falling back to configuration.")

    if not config or "AOI_DEFINITION" not in config:
        # Default global fallback or empty
        return "POLYGON((77.10 28.50, 77.40 28.50, 77.40 28.80, 77.10 28.80, 77.10 28.50))"

    aoi_sec = config["AOI_DEFINITION"]
    aoi_mode = aoi_sec.get("AOI_MODE", "BBOX").strip().upper()

    if aoi_mode == "WKT":
        wkt_str = aoi_sec.get("WKT", "").strip()
        if wkt_str:
            return wkt_str

    if aoi_mode == "BBOX":
        lonmin = aoi_sec.get("LONMIN", "").strip()
        latmin = aoi_sec.get("LATMIN", "").strip()
        lonmax = aoi_sec.get("LONMAX", "").strip()
        latmax = aoi_sec.get("LATMAX", "").strip()
        if lonmin and latmin and lonmax and latmax:
            return f"POLYGON(({lonmin} {latmin}, {lonmax} {latmin}, {lonmax} {latmax}, {lonmin} {latmax}, {lonmin} {latmin}))"
        else:
            raise ValueError("Incomplete BBOX parameters in configuration file.")

    if aoi_mode in ["SHP", "GEOJSON", "KML"]:
        aoi_file = aoi_sec.get("AOI_FILE", "").strip()
        if not aoi_file or not os.path.isfile(aoi_file):
            raise FileNotFoundError(f"AOI file specified does not exist: {aoi_file}")

        try:
            import geopandas as gpd
            if aoi_mode == "KML":
                import fiona
                fiona.drvsupport.supported_drivers['KML'] = 'rw'
                gdf = gpd.read_file(aoi_file, driver='KML')
            else:
                gdf = gpd.read_file(aoi_file)
            
            # Reproject to EPSG:4326 if needed
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)

            # Use total envelope polygon
            minx, miny, maxx, maxy = gdf.total_bounds
            return f"POLYGON(({minx} {miny}, {maxx} {miny}, {maxx} {maxy}, {minx} {maxy}, {minx} {miny}))"
        except Exception as e:
            if logger:
                logger.warning(f"GeoPandas AOI loading failed ({e}). Attempting pure-Python geometry fallback.")
            
            # Simple fallback for GeoJSON
            if aoi_file.lower().endswith(('.json', '.geojson')):
                import json
                with open(aoi_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                coords = []
                def extract_coords(obj):
                    if isinstance(obj, list):
                        if len(obj) >= 2 and isinstance(obj[0], (int, float)) and isinstance(obj[1], (int, float)):
                            coords.append((obj[0], obj[1]))
                        else:
                            for item in obj: extract_coords(item)
                    elif isinstance(obj, dict):
                        for v in obj.values(): extract_coords(v)
                extract_coords(data)
                if coords:
                    xs = [c[0] for c in coords]
                    ys = [c[1] for c in coords]
                    return f"POLYGON(({min(xs)} {min(ys)}, {max(xs)} {min(ys)}, {max(xs)} {max(ys)}, {min(xs)} {max(ys)}, {min(xs)} {min(ys)}))"
            raise

    raise ValueError(f"Unsupported AOI_MODE: '{aoi_mode}'. Supported modes: BBOX, SHP, GeoJSON, KML, WKT.")


def resolve_credentials(config: Optional[configparser.ConfigParser], cli_user: Optional[str], cli_pass: Optional[str]) -> tuple:
    """Extracts NASA Earthdata credentials from CLI, config, or environment variables."""
    user = cli_user
    pwd = cli_pass

    if not user and config:
        if "ASF_SEARCH" in config:
            user = config["ASF_SEARCH"].get("ASF_USER", "").strip()
        elif "SEARCH_PARAMS" in config:
            user = config["SEARCH_PARAMS"].get("ASF_USER", "").strip()

    if not pwd and config:
        if "ASF_SEARCH" in config:
            pwd = config["ASF_SEARCH"].get("ASF_PASS", "").strip()
        elif "SEARCH_PARAMS" in config:
            pwd = config["SEARCH_PARAMS"].get("ASF_PASS", "").strip()

    # Environment variable fallbacks
    if not user:
        user = os.environ.get("EARTHDATA_USERNAME", os.environ.get("EARTHDATA_USER", os.environ.get("ASF_USER", "")))
    if not pwd:
        pwd = os.environ.get("EARTHDATA_PASSWORD", os.environ.get("EARTHDATA_PASS", os.environ.get("ASF_PASS", "")))

    return user, pwd


def _extract_expected_size_and_filename(item) -> Tuple[str, int]:
    """Extracts target filename and expected byte size from an ASFProduct."""
    props = getattr(item, "properties", {})
    url = props.get("url", "")
    fname = props.get("fileName") or (url.split("/")[-1].split("?")[0] if url else "")
    if not fname:
        fname = props.get("sceneName", "nisar_granule") + ".h5"

    size_raw = props.get("bytes", 0)
    expected_bytes = 0
    if isinstance(size_raw, dict):
        if fname in size_raw and isinstance(size_raw[fname], dict):
            expected_bytes = int(size_raw[fname].get("bytes", 0) or 0)
        else:
            h5_sizes = [
                int(v.get("bytes", 0) or 0)
                for k, v in size_raw.items()
                if isinstance(v, dict) and k.endswith(".h5") and "QA" not in k
            ]
            expected_bytes = h5_sizes[0] if h5_sizes else 0
    elif isinstance(size_raw, (int, float)):
        expected_bytes = int(size_raw)

    return fname, expected_bytes


def _download_one_resumable(
    item,
    target_dir: str,
    username: str,
    password: str,
    max_retries: int = 12,
    chunk_size: int = 4 * 1024 * 1024,
) -> Tuple[str, bool, str]:
    """Downloads a single ASF NISAR granule with HTTP Range resume and retries."""
    import asf_search as asf

    props = getattr(item, "properties", {})
    url = props.get("url", "")
    if not url:
        return (props.get("sceneName", "unknown"), False, "Missing download URL in ASF metadata")

    fname, expected_bytes = _extract_expected_size_and_filename(item)
    dest_path = os.path.join(target_dir, fname)

    if os.path.isfile(dest_path) and expected_bytes > 0 and os.path.getsize(dest_path) == expected_bytes:
        return (fname, True, f"already complete ({expected_bytes / (1024**3):.2f} GB)")

    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            sess = asf.ASFSession()
            sess.auth_with_creds(username, password)

            existing = os.path.getsize(dest_path) if os.path.isfile(dest_path) else 0
            if expected_bytes > 0 and existing == expected_bytes:
                return (fname, True, f"complete ({expected_bytes / (1024**3):.2f} GB)")
            if expected_bytes > 0 and existing > expected_bytes:
                # Truncate/restart if corrupted oversize file
                existing = 0

            headers = {}
            if existing > 0:
                headers["Range"] = f"bytes={existing}-"
                print(f"   ↻ Resuming {fname} from {existing / (1024**3):.2f} GB (attempt {attempt}/{max_retries})...", flush=True)
            else:
                print(f"   ⬇ Starting {fname} (attempt {attempt}/{max_retries})...", flush=True)

            with sess.get(url, headers=headers, stream=True, timeout=(30, 120)) as resp:
                if existing > 0 and resp.status_code == 200:
                    # Server ignored Range; restart from 0
                    existing = 0
                    mode = "wb"
                elif resp.status_code in (200, 206):
                    mode = "ab" if (existing > 0 and resp.status_code == 206) else "wb"
                elif resp.status_code == 416 and expected_bytes > 0 and existing == expected_bytes:
                    return (fname, True, f"complete ({expected_bytes / (1024**3):.2f} GB)")
                else:
                    resp.raise_for_status()
                    mode = "wb"

                if expected_bytes == 0:
                    cr = resp.headers.get("Content-Range", "")
                    if "/" in cr and cr.split("/")[-1].isdigit():
                        expected_bytes = int(cr.split("/")[-1])
                    elif resp.headers.get("Content-Length", "").isdigit():
                        expected_bytes = existing + int(resp.headers["Content-Length"])

                written = existing
                next_report = ((written // (2 * 1024**3)) + 1) * (2 * 1024**3)
                with open(dest_path, mode) as fh:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)
                        if written >= next_report:
                            pct = f" ({100.0 * written / expected_bytes:.1f}%)" if expected_bytes else ""
                            print(f"   ⏳ {fname}: {written / (1024**3):.2f} GB{pct}", flush=True)
                            next_report += 2 * 1024**3

            final_size = os.path.getsize(dest_path)
            if expected_bytes > 0 and final_size < expected_bytes:
                raise IOError(f"Short read: got {final_size} of {expected_bytes} bytes")

            return (fname, True, f"downloaded ({final_size / (1024**3):.2f} GB)")

        except Exception as exc:
            last_err = str(exc)
            backoff = min(30, 3 * attempt)
            print(f"   ⚠️ {fname} interrupted (attempt {attempt}/{max_retries}): {last_err[:120]}. Retrying in {backoff}s...", flush=True)
            time.sleep(backoff)

    return (fname, False, last_err)


def download_nisar_asf(
    config_file: Optional[str] = None,
    out_dir: Optional[str] = None,
    bbox: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    track: Optional[int] = None,
    polarization: Optional[str] = None,
    product_type: str = "RSLC",
    user: Optional[str] = None,
    password: Optional[str] = None,
    processes: Optional[int] = None,
    dry_run: bool = False,
    max_results: int = 250,
    logger: Optional[FriendlyLogger] = None
) -> int:
    """Main execution function for downloading NISAR granules from ASF."""
    if logger is None:
        logger = FriendlyLogger()

    logger.banner("NASA ASF NISAR RSLC Data Downloader (Alaska Satellite Facility)")

    # Read configuration file if provided
    config = None
    if config_file:
        if not os.path.isfile(config_file):
            logger.error(
                "Config File Not Found",
                f"Specified configuration file '{config_file}' does not exist.",
                [
                    "Verify the path to project_nisar.conf.",
                    "Generate a default configuration using the snap2stamps_nisar dashboard."
                ]
            )
            return 1
        config = configparser.ConfigParser()
        config.read(config_file)
        logger.info(f"Loaded Configuration: {os.path.abspath(config_file)}")

    # Resolve output directory
    target_dir = out_dir
    if not target_dir and config and "PROJECT_DEFINITION" in config:
        project_folder = config["PROJECT_DEFINITION"].get("PROJECTFOLDER", "").strip()
        if project_folder:
            # Download raw NISAR files into <PROJECTFOLDER>/raw (standard convention)
            target_dir = os.path.join(project_folder, "raw")

    if not target_dir:
        target_dir = os.path.join(os.getcwd(), "nisar_raw")

    target_dir = os.path.abspath(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    logger.info(f"Destination Directory : {target_dir}")

    # Resolve AOI Polygon
    try:
        wkt_polygon = load_aoi_wkt(config, bbox, logger)
        logger.subtask(f"AOI WKT Polygon: {wkt_polygon[:60]}... ({len(wkt_polygon)} chars)", status="done")
    except Exception as e:
        logger.error(
            "AOI Geometry Parsing Failure",
            f"Failed to determine Area of Interest: {e}",
            [
                "Check AOI_MODE and coordinate values (LONMIN, LATMIN, LONMAX, LATMAX) in project_nisar.conf.",
                "Ensure your Shapefile or GeoJSON contains valid polygonal geometries in WGS84 (EPSG:4326)."
            ]
        )
        return 1

    # Resolve Search Parameters
    start = start_date
    end = end_date
    proc_type_str = product_type
    pol = polarization
    trk = track
    npd = processes

    if config:
        if "ASF_SEARCH" in config:
            asf_sec = config["ASF_SEARCH"]
            start = start or asf_sec.get("START", "").strip()
            end = end or asf_sec.get("END", "").strip()
            proc_type_str = proc_type_str or asf_sec.get("BEAM_MODE", "RSLC").strip()
            trk = trk or (int(asf_sec.get("TRACK", "0")) if asf_sec.get("TRACK") else None)
        if "PROC_PARAMETERS" in config:
            pol = pol or config["PROC_PARAMETERS"].get("POLARISATION", "HH").strip()
        if "COMPUTING_RESOURCES" in config:
            npd = npd or int(config["COMPUTING_RESOURCES"].get("CPU", "4"))

    start = start or "2024-01-01"
    end = end or time.strftime("%Y-%m-%d")
    if processes is not None:
        npd = max(1, npd)
    else:
        # Cap concurrent HTTP download workers to avoid Earthdata throttling
        npd = max(1, min(npd or 4, 4))

    logger.info(f"Search Time Window    : {start} to {end}")
    logger.info(f"Target Product Type   : {proc_type_str}")
    logger.info(f"Polarization Filter   : {pol or 'All'}")
    if trk:
        logger.info(f"Relative Orbit/Track  : {trk}")

    # Check for asf_search library
    try:
        import asf_search as asf
    except ImportError:
        logger.error(
            "asf_search Library Not Installed",
            "The official NASA ASF search SDK 'asf-search' is not installed in this Python environment.",
            [
                "Run: pip install asf-search",
                "Or run: pip install -r snap2stamps_nisar/requirements.txt"
            ]
        )
        return 1

    # Resolve Credentials & Authenticate
    username, password = resolve_credentials(config, user, password)

    session = None
    if not dry_run:
        if not username or not password:
            logger.warning(
                "NASA Earthdata credentials not provided in project_nisar.conf or CLI arguments.\n"
                "       Searching metadata without authentication. Download step will require credentials."
            )
        else:
            logger.subtask(f"Authenticating with NASA Earthdata as user '{username}'...", status="working")
            try:
                session = asf.ASFSession()
                session.auth_with_creds(username, password)
                logger.subtask("Authentication successful (Earthdata token granted)", status="done")
            except Exception as e:
                logger.error(
                    "NASA Earthdata Authentication Failed",
                    f"Could not authenticate with Earthdata Login: {e}",
                    [
                        "Double-check your ASF_USER and ASF_PASS in project_nisar.conf.",
                        "Verify your account at https://urs.earthdata.nasa.gov/",
                        "Ensure you have authorized the 'Alaska Satellite Facility' application in your Earthdata Profile."
                    ]
                )
                return 1

    # Prepare ASF Search Query
    logger.step_header(
        "1", "2", "Querying Alaska Satellite Facility (ASF)",
        "Searching ASF catalog for NISAR radar granules matching AOI, dates, and mode",
        typical_time="5-15 seconds"
    )

    platform_query = ["NISAR"]
    # Check if asf constants provide NISAR
    if hasattr(asf, 'constants') and hasattr(asf.constants, 'NISAR'):
        platform_query = [asf.constants.NISAR]

    # Map processing level
    level_map = {
        "RSLC": asf.PRODUCT_TYPE.RSLC if hasattr(asf.PRODUCT_TYPE, 'RSLC') else "RSLC",
        "GSLC": asf.PRODUCT_TYPE.GSLC if hasattr(asf.PRODUCT_TYPE, 'GSLC') else "GSLC",
        "SLC": asf.PRODUCT_TYPE.SLC if hasattr(asf.PRODUCT_TYPE, 'SLC') else "SLC",
        "GCOV": asf.PRODUCT_TYPE.GCOV if hasattr(asf.PRODUCT_TYPE, 'GCOV') else "GCOV",
    }
    proc_levels = [level_map.get(proc_type_str.upper(), proc_type_str)]

    search_kwargs: Dict[str, Any] = {
        "platform": platform_query,
        "processingLevel": proc_levels,
        "intersectsWith": wkt_polygon,
        "start": start,
        "end": end,
        "maxResults": max_results
    }
    if trk:
        search_kwargs["relativeOrbit"] = trk
    # Note: ASF CMR stores polarization=None for NISAR products (uses mainBandPolarization instead),
    # so we filter polarization locally after geo_search rather than passing it in search_kwargs.

    logger.info("Executing ASF GeoSearch API query...")
    try:
        results = asf.geo_search(**search_kwargs)
    except Exception as e:
        logger.warning(f"Primary search failed with NISAR platform constant ({e}). Retrying with generic parameters...")
        try:
            # Fallback search without strict platform keyword or with string platform
            search_kwargs["platform"] = ["NISAR"]
            results = asf.geo_search(**search_kwargs)
        except Exception as e2:
            logger.error(
                "ASF Search API Call Failed",
                f"Failed to query ASF search endpoint: {e2}",
                [
                    "Verify your internet connection and DNS settings.",
                    "Ensure your AOI bounding coordinates are valid longitudes (-180 to 180) and latitudes (-90 to 90).",
                    "Try broadening the date window in project_nisar.conf."
                ]
            )
            return 1

    # Filter locally by polarization if requested (checks mainBandPolarization / sideBandPolarization)
    if pol and len(results) > 0:
        pol_clean = [p.strip().upper() for p in pol.replace("+", ",").split(",") if p.strip()]
        if pol_clean:
            filtered = [
                item for item in results
                if not (
                    getattr(item, "properties", {}).get("mainBandPolarization")
                    or getattr(item, "properties", {}).get("sideBandPolarization")
                )
                or any(
                    p in (getattr(item, "properties", {}).get("mainBandPolarization") or [])
                    or p in (getattr(item, "properties", {}).get("sideBandPolarization") or [])
                    for p in pol_clean
                )
            ]
            if filtered:
                results = asf.ASFSearchResults(filtered)

    total_scenes = len(results)
    logger.step_footer("1", "ASF Catalog Query")

    if total_scenes == 0:
        logger.banner("ℹ️  NO NISAR GRANULES FOUND FOR SPECIFIED CRITERIA")
        logger.info("No granules in ASF matched the given date range and bounding box.")
        logger.info("💡 Note: If NISAR is newly commissioned or in calibration/validation phase,")
        logger.info("   expand the date range (e.g. 2024 to present) or widen the AOI bounding box.")
        logger.info("   Alternatively, test synthetic sample scenes placed into the 'raw/' folder.")
        return 0

    logger.success(f"Discovered {total_scenes} matching NISAR granule(s) in ASF catalog!")
    print("\n" + "=" * 90)
    print(f"{'#':<4} {'Granule Name':<50} {'Date':<12} {'Orbit':<8} {'Size (MB)':<10}")
    print("-" * 90)
    for idx, item in enumerate(results[:25], 1):
        props = getattr(item, "properties", {})
        name = props.get("sceneName", getattr(item, "sceneName", f"NISAR_Granule_{idx}"))
        dt = (props.get("startTime") or props.get("acquisitionDate") or "Unknown")[:10]
        orbit = str(props.get("pathNumber", props.get("relativeOrbit", "N/A")))
        size_raw = props.get("bytes", 0)
        if isinstance(size_raw, dict):
            # NISARProduct returns a dict of {filename: {'bytes': int, 'format': str}}
            h5_sizes = [
                v.get("bytes", 0) for k, v in size_raw.items()
                if isinstance(v, dict) and k.endswith(".h5") and "QA" not in k
            ]
            size_bytes = h5_sizes[0] if h5_sizes else sum(
                v.get("bytes", 0) for v in size_raw.values() if isinstance(v, dict)
            )
        else:
            size_bytes = size_raw or 0
        size_mb = f"{size_bytes / (1024*1024):.1f}" if size_bytes else "N/A"
        print(f"{idx:<4} {name[:48]:<50} {dt:<12} {orbit:<8} {size_mb:<10}")
    if total_scenes > 25:
        print(f"... and {total_scenes - 25} more granules (total: {total_scenes})")
    print("=" * 90 + "\n")

    if dry_run:
        logger.info("Dry-run requested (--dry-run). Search completed without downloading files.")
        return 0

    # Execute Download
    logger.step_header(
        "2", "2", "Downloading NISAR RSLC Products",
        f"Streaming {total_scenes} granules into {target_dir} ({npd} concurrent processes)",
        typical_time="Depends on file size & network speed"
    )

    if not session:
        logger.error(
            "Missing Credentials for Download",
            "ASF requires valid NASA Earthdata authentication to download NISAR granules.",
            [
                "Specify ASF_USER and ASF_PASS in project_nisar.conf.",
                "Or pass credentials via --user and --password command-line arguments.",
                "Or export EARTHDATA_USERNAME and EARTHDATA_PASSWORD environment variables."
            ]
        )
        return 1

    try:
        start_dl = time.time()
        logger.info(f"Initiating resumable multi-threaded download ({npd} parallel workers)...")
        failures = []
        with ThreadPoolExecutor(max_workers=npd) as pool:
            futures = {
                pool.submit(_download_one_resumable, item, target_dir, username, password): item
                for item in results
            }
            for idx, fut in enumerate(as_completed(futures), 1):
                fname, ok, detail = fut.result()
                if ok:
                    logger.subtask(f"[{idx}/{total_scenes}] {fname}: {detail}", status="done")
                else:
                    logger.subtask(f"[{idx}/{total_scenes}] {fname}: {detail}", status="failed")
                    failures.append((fname, detail))

        dl_time = int(time.time() - start_dl)
        mins, secs = divmod(dl_time, 60)
        dur_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        if failures:
            err_msg = "; ".join(f"{fn}: {d}" for fn, d in failures)
            logger.translate_and_report_error(err_msg, current_step="ASF Download")
            return 1

        logger.step_footer("2", "Granule Acquisition")
        logger.success(f"Successfully downloaded {total_scenes} NISAR granules in {dur_str}!")
        logger.info(f"Granules saved to: {target_dir}")
        return 0

    except Exception as e:
        logger.translate_and_report_error(str(e), current_step="ASF Download")
        return 1


def main():
    args = parse_arguments()
    ret = download_nisar_asf(
        config_file=args.ProjFile,
        out_dir=args.output,
        bbox=args.bbox,
        start_date=args.start,
        end_date=args.end,
        track=args.track,
        polarization=args.polarization,
        product_type=args.product_type,
        user=args.user,
        password=args.password,
        processes=args.processes,
        dry_run=args.dry_run,
        max_results=args.max_results
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
