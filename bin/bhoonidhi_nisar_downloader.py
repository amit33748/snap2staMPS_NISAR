#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bhoonidhi_nisar_downloader.py: ISRO Bhoonidhi STAC REST API Downloader for NISAR Data.
Integrated into snap2stamps_nisar processing pipeline.

Supports:
- /auth/token: JWT authentication with ISRO Bhoonidhi portal credentials
- /data: STAC-compliant catalog search with BBOX and SHP/GeoJSON polygon geometries
- /download: Robust chunked file streaming with HTTP Range resume capability,
  automatic retries, and beginner-friendly progress logging.
"""

import os
import sys
import time
import json
import argparse
import configparser
import warnings
from typing import Optional, List, Dict, Any, Tuple
import requests
from urllib.parse import urljoin

warnings.filterwarnings('ignore')

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
        def translate_and_report_error(self, err, current_step="Bhoonidhi Download"):
            print(f"\n❌ Error in {current_step}: {err}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="ISRO Bhoonidhi STAC REST API Downloader for NISAR Data",
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
        "--shp",
        type=str,
        default=None,
        help="Path to AOI Shapefile or GeoJSON"
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
        "--freq",
        type=str,
        default="L",
        choices=["L", "S", "L+S"],
        help="Radar frequency band"
    )
    parser.add_argument(
        "--pol",
        type=str,
        default="HH",
        help="Polarisation filter (e.g. HH, VV, HH+HV)"
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Base URL of Bhoonidhi STAC REST API"
    )
    parser.add_argument(
        "--username", "-u",
        type=str,
        default=None,
        help="Bhoonidhi account username"
    )
    parser.add_argument(
        "--password", "-p",
        type=str,
        default=None,
        help="Bhoonidhi account password"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=2 * 1024 * 1024,
        help="Streaming chunk size in bytes (default: 2MB)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform search query and display results without downloading"
    )
    parser.add_argument(
        "--mock-demo",
        action="store_true",
        help="Run in demonstration mode with mock STAC responses for offline testing"
    )
    return parser.parse_args()


class BhoonidhiClient:
    """Client for ISRO Bhoonidhi STAC REST API."""

    def __init__(self, base_url: str = "https://bhoonidhi.nrsc.gov.in/bhoonidhi-api", logger: Optional[FriendlyLogger] = None):
        self.base_url = base_url.rstrip("/")
        self.logger = logger or FriendlyLogger()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "snap2stamps-nisar-bhoonidhi-client/1.0",
            "Accept": "application/json"
        })
        self.jwt_token: Optional[str] = None

    def authenticate(self, username: str, password: str) -> bool:
        """
        Authenticates via POST /auth/token and obtains a JWT bearer token.
        """
        auth_url = f"{self.base_url}/auth/token"
        self.logger.subtask(f"Authenticating to ISRO Bhoonidhi at {auth_url}...", status="working")

        payload = {
            "username": username,
            "password": password
        }

        try:
            resp = self.session.post(auth_url, json=payload, timeout=25)
            if resp.status_code in [200, 201]:
                data = resp.json()
                # Parse JWT token from common response schemas
                token = (
                    data.get("token") or
                    data.get("access_token") or
                    data.get("id_token") or
                    data.get("jwt") or
                    (data.get("data", {}).get("token") if isinstance(data.get("data"), dict) else None)
                )

                if token:
                    self.jwt_token = token
                    self.session.headers.update({"Authorization": f"Bearer {token}"})
                    self.logger.subtask(f"Authentication successful for user '{username}'", status="done")
                    return True
                else:
                    self.logger.warning(f"Unexpected token payload from Bhoonidhi: {list(data.keys())}")
                    return False
            elif resp.status_code in [401, 403]:
                self.logger.error(
                    "Bhoonidhi Authentication Refused (401/403)",
                    f"Invalid username or password for user '{username}'.",
                    [
                        "Verify your Bhoonidhi credentials at https://bhoonidhi.nrsc.gov.in/",
                        "Check that your account has been verified via email confirmation.",
                        "Update USERNAME and PASSWORD under [BHOONIDHI_API] in project_nisar.conf."
                    ]
                )
                return False
            else:
                self.logger.warning(f"Bhoonidhi auth endpoint returned HTTP {resp.status_code}: {resp.text[:120]}")
                return False

        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Could not connect to Bhoonidhi auth server: {e}")
            return False

    def search_stac(
        self,
        bbox: List[float],
        start_date: str,
        end_date: str,
        frequency: str = "L",
        polarization: str = "HH",
        geojson_geom: Optional[Dict[str, Any]] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Queries /data endpoint using STAC criteria (BBOX / polygon geometry, datetime, frequency, pol).
        """
        search_urls = [
            f"{self.base_url}/data",
            f"{self.base_url}/data/search",
            f"{self.base_url}/stac/search",
            f"{self.base_url}/search"
        ]

        # Standard STAC search payload
        time_filter = f"{start_date}T00:00:00Z/{end_date}T23:59:59Z"
        stac_payload = {
            "collections": ["NISAR", "NISAR_L_RSLC", "NISAR_S_RSLC"],
            "bbox": bbox,
            "datetime": time_filter,
            "limit": limit,
            "query": {
                "sensor": {"eq": "NISAR"},
                "frequency": {"eq": frequency.upper()},
                "polarization": {"in": [p.strip() for p in polarization.split(",")]}
            }
        }
        if geojson_geom:
            stac_payload["intersects"] = geojson_geom

        items = []
        for url in search_urls:
            try:
                self.logger.info(f"Querying Bhoonidhi STAC endpoint: {url}")
                # Try POST (standard STAC)
                resp = self.session.post(url, json=stac_payload, timeout=30)
                if resp.status_code == 200:
                    res_json = resp.json()
                    features = res_json.get("features", [])
                    if isinstance(features, list) and features:
                        items = features
                        break
                    # Sometimes STAC returns a direct list of items
                    elif isinstance(res_json, list):
                        items = res_json
                        break
                    elif "data" in res_json and isinstance(res_json["data"], list):
                        items = res_json["data"]
                        break
                elif resp.status_code in [404, 405]:
                    # Try GET with query parameters
                    params = {
                        "bbox": ",".join(map(str, bbox)),
                        "datetime": time_filter,
                        "collection": "NISAR",
                        "frequency": frequency,
                        "polarization": polarization
                    }
                    resp_get = self.session.get(url, params=params, timeout=30)
                    if resp_get.status_code == 200:
                        res_json = resp_get.json()
                        features = res_json.get("features", res_json.get("data", []))
                        if isinstance(features, list) and features:
                            items = features
                            break
            except Exception as e:
                self.logger.warning(f"Request to {url} failed: {e}")

        # Normalize items into uniform format
        normalized_scenes = []
        for feat in items:
            props = feat.get("properties", feat)
            assets = feat.get("assets", {})
            scene_id = feat.get("id") or props.get("scene_id") or props.get("granule_id") or "NISAR_Granule"
            
            # Locate download URL
            dl_url = None
            size_bytes = 0
            
            # STAC standard asset keys
            for key in ["data", "download", "RSLC", "GSLC", "product", "source"]:
                if key in assets:
                    dl_url = assets[key].get("href")
                    size_bytes = assets[key].get("file:size", 0)
                    break
            
            if not dl_url:
                # Check links array
                for link in feat.get("links", []):
                    if link.get("rel") in ["enclosure", "download", "data"]:
                        dl_url = link.get("href")
                        break

            # Fallback direct download endpoint if scene_id known
            if not dl_url:
                dl_url = f"{self.base_url}/download?scene_id={scene_id}"

            # Ensure download URL is absolute
            if dl_url and not dl_url.startswith("http"):
                dl_url = urljoin(self.base_url, dl_url)

            normalized_scenes.append({
                "id": scene_id,
                "date": props.get("datetime", props.get("acquisition_date", "Unknown"))[:10],
                "orbit": props.get("orbit", props.get("track", props.get("relative_orbit", "N/A"))),
                "frequency": props.get("frequency", frequency),
                "polarization": props.get("polarization", polarization),
                "download_url": dl_url,
                "size_bytes": size_bytes,
                "filename": f"{scene_id}.h5" if not scene_id.endswith(".h5") else scene_id
            })

        return normalized_scenes

    def download_file_with_resume(
        self,
        url: str,
        dest_path: str,
        expected_size: int = 0,
        chunk_size: int = 2 * 1024 * 1024,
        max_retries: int = 5
    ) -> bool:
        """
        Downloads a file with chunked streaming and HTTP Range resume support.
        """
        part_path = dest_path + ".part"
        final_filename = os.path.basename(dest_path)

        # Step A: Check if complete file already exists
        if os.path.isfile(dest_path):
            existing_size = os.path.getsize(dest_path)
            if expected_size > 0 and existing_size == expected_size:
                self.logger.subtask(f"File {final_filename} already completely downloaded ({existing_size/(1024*1024):.1f} MB). Skipping.", status="done")
                return True
            elif expected_size == 0 and existing_size > 1024 * 1024:
                self.logger.subtask(f"File {final_filename} exists ({existing_size/(1024*1024):.1f} MB). Skipping.", status="done")
                return True

        for attempt in range(1, max_retries + 1):
            try:
                downloaded_bytes = 0
                headers = {}

                # Step B: Check partial file for resume
                if os.path.isfile(part_path):
                    downloaded_bytes = os.path.getsize(part_path)
                    headers["Range"] = f"bytes={downloaded_bytes}-"
                    self.logger.info(f"Resuming download from byte {downloaded_bytes} ({downloaded_bytes/(1024*1024):.1f} MB)...")

                with self.session.get(url, headers=headers, stream=True, timeout=30) as resp:
                    # HTTP 206 Partial Content: resume supported
                    if resp.status_code == 206:
                        mode = "ab"
                        total_size = downloaded_bytes + int(resp.headers.get("Content-Length", 0))
                    elif resp.status_code == 200:
                        # Server does not support resume, start from beginning
                        mode = "wb"
                        downloaded_bytes = 0
                        total_size = int(resp.headers.get("Content-Length", expected_size))
                    elif resp.status_code == 416:
                        # Range Not Satisfiable -> file already complete!
                        if os.path.isfile(part_path):
                            os.replace(part_path, dest_path)
                            self.logger.subtask(f"Download complete: {final_filename}", status="done")
                            return True
                        raise IOError(f"HTTP 416 Range Not Satisfiable on {url}")
                    else:
                        resp.raise_for_status()

                    total_mb = f"{total_size / (1024 * 1024):.1f}" if total_size > 0 else "Unknown"
                    self.logger.info(f"Streaming {final_filename} [Total: {total_mb} MB]")

                    start_time = time.time()
                    last_log_time = start_time
                    chunk_bytes_since_log = 0

                    with open(part_path, mode) as f:
                        for chunk in resp.iter_content(chunk_size=chunk_size):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded_bytes += len(chunk)
                            chunk_bytes_since_log += len(chunk)

                            now = time.time()
                            if now - last_log_time >= 3.0:
                                speed_mb = (chunk_bytes_since_log / (1024 * 1024)) / (now - last_log_time)
                                pct = int((downloaded_bytes / total_size) * 100) if total_size > 0 else 0
                                dl_mb = f"{downloaded_bytes / (1024 * 1024):.1f}"
                                rem_mb = (total_size - downloaded_bytes) / (1024 * 1024) if total_size > 0 else 0
                                eta_secs = int(rem_mb / speed_mb) if speed_mb > 0 else 0
                                eta_str = f"{eta_secs // 60}m {eta_secs % 60}s" if eta_secs > 0 else "estimating..."

                                self.logger.progress(
                                    pct, 100,
                                    f"{dl_mb}/{total_mb} MB ({pct}%) | {speed_mb:.2f} MB/s | ETA: {eta_str}"
                                )
                                last_log_time = now
                                chunk_bytes_since_log = 0

                # Rename .part to final file
                if os.path.isfile(part_path):
                    os.replace(part_path, dest_path)
                
                self.logger.subtask(f"Successfully downloaded {final_filename}", status="done")
                return True

            except (requests.exceptions.RequestException, IOError) as e:
                self.logger.warning(f"Download attempt {attempt}/{max_retries} interrupted: {e}")
                if attempt < max_retries:
                    wait_sec = attempt * 3
                    self.logger.info(f"Retrying with resume in {wait_sec} seconds...")
                    time.sleep(wait_sec)
                else:
                    self.logger.error(
                        "Download Failed after Retries",
                        f"Could not complete download of {final_filename}: {e}",
                        [
                            "Check your internet bandwidth and stability.",
                            "Verify if the Bhoonidhi server is throttling connections.",
                            "Re-run the script; it will automatically resume from the last byte."
                        ]
                    )
                    return False

        return False


def get_mock_scenes(bbox: List[float], start_date: str, end_date: str, freq: str, pol: str) -> List[Dict[str, Any]]:
    """Generates synthetic scene metadata for demonstration, testing, or offline sandbox mode."""
    scenes = [
        {
            "id": "NISAR_L_PR_RSLC_001_034_005_20240410T002530_20240410T002555_HH.h5",
            "date": "2024-04-10",
            "orbit": 34,
            "frequency": freq,
            "polarization": pol,
            "download_url": "https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/download?scene=mock_01",
            "size_bytes": 1024 * 1024 * 850,
            "filename": "NISAR_L_PR_RSLC_001_034_005_20240410T002530_20240410T002555_HH.h5"
        },
        {
            "id": "NISAR_L_PR_RSLC_001_034_005_20240422T002530_20240422T002555_HH.h5",
            "date": "2024-04-22",
            "orbit": 34,
            "frequency": freq,
            "polarization": pol,
            "download_url": "https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/download?scene=mock_02",
            "size_bytes": 1024 * 1024 * 855,
            "filename": "NISAR_L_PR_RSLC_001_034_005_20240422T002530_20240422T002555_HH.h5"
        },
        {
            "id": "NISAR_L_PR_RSLC_001_034_005_20240504T002530_20240504T002555_HH.h5",
            "date": "2024-05-04",
            "orbit": 34,
            "frequency": freq,
            "polarization": pol,
            "download_url": "https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/download?scene=mock_03",
            "size_bytes": 1024 * 1024 * 848,
            "filename": "NISAR_L_PR_RSLC_001_034_005_20240504T002530_20240504T002555_HH.h5"
        }
    ]
    return scenes


def parse_aoi_bbox(config: Optional[configparser.ConfigParser], cli_bbox: Optional[str]) -> Tuple[List[float], Optional[Dict[str, Any]]]:
    """Extracts bounding box coordinates [lonmin, latmin, lonmax, latmax] and GeoJSON geometry."""
    if cli_bbox:
        pts = [float(x.strip()) for x in cli_bbox.split(',')]
        if len(pts) == 4:
            return pts, None

    if config and "AOI_DEFINITION" in config:
        sec = config["AOI_DEFINITION"]
        mode = sec.get("AOI_MODE", "BBOX").upper()

        if mode == "BBOX":
            return [
                float(sec.get("LONMIN", "77.10")),
                float(sec.get("LATMIN", "28.50")),
                float(sec.get("LONMAX", "77.40")),
                float(sec.get("LATMAX", "28.80"))
            ], None

        if mode in ["SHP", "GEOJSON"]:
            aoi_file = sec.get("AOI_FILE", "").strip()
            if aoi_file and os.path.isfile(aoi_file):
                try:
                    import geopandas as gpd
                    gdf = gpd.read_file(aoi_file)
                    if gdf.crs and gdf.crs.to_epsg() != 4326:
                        gdf = gdf.to_crs(epsg=4326)
                    minx, miny, maxx, maxy = gdf.total_bounds
                    geom = gdf.unary_union.__geo_interface__
                    return [float(minx), float(miny), float(maxx), float(maxy)], geom
                except Exception:
                    pass

    # Default fallback BBOX (Delhi NCR region)
    return [77.10, 28.50, 77.40, 28.80], None


def download_nisar_bhoonidhi(
    config_file: Optional[str] = None,
    out_dir: Optional[str] = None,
    bbox_str: Optional[str] = None,
    shp_file: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    frequency: str = "L",
    polarization: str = "HH",
    api_url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    chunk_size: int = 2 * 1024 * 1024,
    dry_run: bool = False,
    mock_demo: bool = False,
    logger: Optional[FriendlyLogger] = None
) -> int:
    """Main execution function for downloading NISAR granules from ISRO Bhoonidhi."""
    if logger is None:
        logger = FriendlyLogger()

    logger.banner("ISRO Bhoonidhi STAC REST API Downloader for NISAR RSLC Data")

    # Read configuration file if provided
    config = None
    if config_file:
        if not os.path.isfile(config_file):
            logger.error(
                "Configuration File Missing",
                f"File '{config_file}' could not be located.",
                ["Verify the path provided to -F / --ProjFile."]
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
            target_dir = os.path.join(project_folder, "raw")

    if not target_dir:
        target_dir = os.path.join(os.getcwd(), "nisar_raw")

    target_dir = os.path.abspath(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    logger.info(f"Destination Directory : {target_dir}")

    # Resolve Parameters
    bhoonidhi_url = api_url
    user = username
    pwd = password
    freq = frequency
    pol = polarization
    start = start_date
    end = end_date

    if config:
        if "BHOONIDHI_API" in config:
            b_sec = config["BHOONIDHI_API"]
            bhoonidhi_url = bhoonidhi_url or b_sec.get("BHOONIDHI_URL", "").strip()
            user = user or b_sec.get("USERNAME", "").strip()
            pwd = pwd or b_sec.get("PASSWORD", "").strip()
        if "PROC_PARAMETERS" in config:
            p_sec = config["PROC_PARAMETERS"]
            freq = freq or p_sec.get("FREQUENCY", "L").strip()
            pol = pol or p_sec.get("POLARISATION", "HH").strip()
        if "ASF_SEARCH" in config:
            start = start or config["ASF_SEARCH"].get("START", "").strip()
            end = end or config["ASF_SEARCH"].get("END", "").strip()

    bhoonidhi_url = bhoonidhi_url or "https://bhoonidhi.nrsc.gov.in/bhoonidhi-api"
    start = start or "2024-01-01"
    end = end or time.strftime("%Y-%m-%d")

    # Environment variable fallbacks for credentials
    user = user or os.environ.get("BHOONIDHI_USER", os.environ.get("BHOONIDHI_USERNAME", ""))
    pwd = pwd or os.environ.get("BHOONIDHI_PASS", os.environ.get("BHOONIDHI_PASSWORD", ""))

    bbox_coords, geojson_geom = parse_aoi_bbox(config, bbox_str)

    logger.info(f"Bhoonidhi API Base URL: {bhoonidhi_url}")
    logger.info(f"AOI Bounding Box      : {bbox_coords}")
    logger.info(f"Temporal Window       : {start} to {end}")
    logger.info(f"Frequency & Pol       : {freq}-Band | {pol}")

    client = BhoonidhiClient(base_url=bhoonidhi_url, logger=logger)

    # Step 1: Authentication (/auth/token)
    logger.step_header(
        "1", "3", "Authentication with ISRO Bhoonidhi (/auth/token)",
        "Obtains a secure JWT bearer token for STAC querying and data downloads",
        typical_time="2-5 seconds"
    )

    is_authenticated = False
    if user and pwd and not mock_demo:
        is_authenticated = client.authenticate(user, pwd)
        if not is_authenticated:
            logger.warning("Authentication failed. Proceeding with public search or simulation check.")
    elif not mock_demo:
        logger.warning(
            "No Bhoonidhi USERNAME / PASSWORD configured in project_nisar.conf.\n"
            "       Will attempt unauthenticated catalog query."
        )

    logger.step_footer("1", "Authentication Check")

    # Step 2: STAC Search (/data)
    logger.step_header(
        "2", "3", "STAC Catalog Search (/data)",
        "Queries ISRO STAC catalog with BBOX and NISAR sensor filters",
        typical_time="5-15 seconds"
    )

    scenes = []
    if mock_demo:
        logger.info("Demo / Mock mode active. Generating synthetic scene responses...")
        scenes = get_mock_scenes(bbox_coords, start, end, freq, pol)
    else:
        scenes = client.search_stac(
            bbox=bbox_coords,
            start_date=start,
            end_date=end,
            frequency=freq,
            polarization=pol,
            geojson_geom=geojson_geom
        )

        if not scenes:
            logger.info("No live scenes found on Bhoonidhi server for specified criteria.")
            logger.info("Checking if mock demonstration is requested or server is in staging...")
            # If search returns nothing but user wants to verify pipeline, inform gently
            logger.banner("ℹ️  NO NISAR PRODUCTS DISCOVERED ON BHOONIDHI")
            logger.info("Bhoonidhi catalog returned 0 granules for this AOI and date range.")
            logger.info("💡 To test your end-to-end processing pipeline before satellite data is published:")
            logger.info("   Run with: python bhoonidhi_nisar_downloader.py -F project_nisar.conf --mock-demo")
            return 0

    logger.step_footer("2", "STAC Catalog Search")
    total_scenes = len(scenes)
    logger.success(f"Located {total_scenes} NISAR granule(s) from ISRO Bhoonidhi STAC catalog!")

    print("\n" + "=" * 90)
    print(f"{'#':<4} {'Bhoonidhi Scene ID':<50} {'Date':<12} {'Orbit':<8} {'Size (MB)':<10}")
    print("-" * 90)
    for idx, sc in enumerate(scenes, 1):
        sz = f"{sc['size_bytes'] / (1024*1024):.1f}" if sc['size_bytes'] else "N/A"
        print(f"{idx:<4} {sc['id'][:48]:<50} {sc['date']:<12} {str(sc['orbit']):<8} {sz:<10}")
    print("=" * 90 + "\n")

    if dry_run:
        logger.info("Dry-run requested (--dry-run). Catalog query completed successfully without download.")
        return 0

    # Step 3: Chunked Streaming with Resume (/download)
    logger.step_header(
        "3", "3", "Chunked File Streaming with Resume Capability",
        f"Downloading {total_scenes} granules into {target_dir} with HTTP Range resume support",
        typical_time="Depends on file size & bandwidth"
    )

    successful = 0
    start_total_dl = time.time()

    for idx, sc in enumerate(scenes, 1):
        filename = sc["filename"]
        dest_file = os.path.join(target_dir, filename)
        dl_url = sc["download_url"]
        exp_size = sc["size_bytes"]

        logger.info(f"\n[{idx}/{total_scenes}] Processing Granule: {filename}")
        
        if mock_demo:
            # Create a small simulated NISAR HDF5 container for pipeline testing
            if not os.path.isfile(dest_file):
                logger.info(f"Writing synthetic NISAR granule placeholder ({filename})...")
                with open(dest_file, "wb") as f:
                    # Write 64KB dummy data
                    f.write(b"NISAR_MOCK_HDF5_DATA" * 4096)
            successful += 1
            logger.subtask(f"Granule {filename} verified (mock mode)", status="done")
            continue

        ok = client.download_file_with_resume(
            url=dl_url,
            dest_path=dest_file,
            expected_size=exp_size,
            chunk_size=chunk_size
        )
        if ok:
            successful += 1

    total_time = int(time.time() - start_total_dl)
    mins, secs = divmod(total_time, 60)
    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

    logger.step_footer("3", "Chunked Download Execution")

    if successful == total_scenes:
        logger.banner("🎉 ALL BHOONIDHI NISAR GRANULES ACQUIRED SUCCESSFULLY!")
        logger.info(f"Total downloaded : {successful}/{total_scenes} scenes in {time_str}")
        logger.info(f"Target folder    : {target_dir}")
        return 0
    else:
        logger.warning(f"Downloaded {successful}/{total_scenes} scenes with warnings. Check logs for interrupted files.")
        return 0 if successful > 0 else 1


def main():
    args = parse_arguments()
    ret = download_nisar_bhoonidhi(
        config_file=args.ProjFile,
        out_dir=args.output,
        bbox_str=args.bbox,
        shp_file=args.shp,
        start_date=args.start,
        end_date=args.end,
        frequency=args.freq,
        polarization=args.pol,
        api_url=args.url,
        username=args.username,
        password=args.password,
        chunk_size=args.chunk_size,
        dry_run=args.dry_run,
        mock_demo=args.mock_demo
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
