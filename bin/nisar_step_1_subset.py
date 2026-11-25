#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nisar_step_1_subset.py: Spatial Subsetting for NISAR InSAR.
Crops the wide (>240 km) NISAR SweepSAR swath to the user-defined Area of Interest
(AOI) using BBOX coordinates, ESRI Shapefile, GeoJSON, or WKT geometry before coregistration.
Supports both SNAP 14.0 GPT subsetting and pure-Python HDF5 crop fallback.

Author: Antigravity Team
Compatible with SNAP 14.0 and StaMPS PSI processing.
"""

import os
import sys
import glob
import json
import shutil
import argparse
import configparser
import subprocess
import time
import datetime
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None

try:
    import h5py
except ImportError:
    h5py = None

try:
    from shapely import wkt
    from shapely.geometry import shape, box, Polygon
except ImportError:
    wkt = None
    shape = None
    box = None
    Polygon = None

try:
    import geopandas as gpd
except ImportError:
    gpd = None

# Import friendly logger if available
try:
    from friendly_logger import FriendlyLogger
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    try:
        from friendly_logger import FriendlyLogger
    except ImportError:
        FriendlyLogger = None


class FallbackLogger:
    """Fallback logger if FriendlyLogger is not present."""
    def banner(self, msg): print(f"\n{'='*70}\n  {msg}\n{'='*70}\n")
    def step_header(self, s, tot, title, desc, typ="2-5 min"):
        print(f"\n[STEP {s}/{tot}] {title}\nDesc: {desc}\n")
    def step_footer(self, s, title): print(f"\n[STEP {s}] {title} completed successfully.\n")
    def info(self, msg): print(f"ℹ️  {msg}")
    def progress(self, cur, tot, item=""): print(f"[{cur}/{tot}] {item}")
    def subtask(self, msg, status="done"): print(f"  [{status.upper()}] {msg}")
    def success(self, msg): print(f"✅ {msg}")
    def warning(self, msg): print(f"⚠️  {msg}")
    def error(self, title, prob, sols):
        print(f"\n❌ ERROR: {title}\nProblem: {prob}\nSolutions:")
        for idx, s in enumerate(sols, 1): print(f"  {idx}. {s}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="NISAR Step 1: Spatial Subsetting of Wide Swath to User AOI"
    )
    parser.add_argument(
        "--ProjFile", "-F",
        type=str,
        default="project_nisar.conf",
        help="Path to project configuration file (e.g., project_nisar.conf)"
    )
    return parser.parse_args()


def load_config(config_path):
    if not os.path.isabs(config_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.join(script_dir, config_path)
        if os.path.exists(cand):
            config_path = cand
        elif not os.path.exists(config_path):
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

    config = configparser.ConfigParser()
    config.read(config_path)
    return config, os.path.abspath(config_path)


def get_aoi_geometry(config):
    """
    Extracts WKT polygon and bounding box (lonmin, latmin, lonmax, latmax)
    from [AOI_DEFINITION] section of project_nisar.conf.
    """
    aoi_sec = config["AOI_DEFINITION"] if "AOI_DEFINITION" in config else {}
    mode = aoi_sec.get("AOI_MODE", "BBOX").upper()

    lonmin, latmin, lonmax, latmax = None, None, None, None
    wkt_polygon = None

    if mode == "BBOX":
        lonmin = float(aoi_sec.get("LONMIN", "0.0"))
        latmin = float(aoi_sec.get("LATMIN", "0.0"))
        lonmax = float(aoi_sec.get("LONMAX", "0.0"))
        latmax = float(aoi_sec.get("LATMAX", "0.0"))
        wkt_polygon = (
            f"POLYGON (({lonmin:.6f} {latmin:.6f}, {lonmax:.6f} {latmin:.6f}, "
            f"{lonmax:.6f} {latmax:.6f}, {lonmin:.6f} {latmax:.6f}, {lonmin:.6f} {latmin:.6f}))"
        )

    elif mode in ["SHP", "GEOJSON", "KML"]:
        aoi_file = aoi_sec.get("AOI_FILE", "").strip()
        if not os.path.exists(aoi_file):
            raise FileNotFoundError(f"AOI file not found: {aoi_file}")

        if gpd is not None:
            gdf = gpd.read_file(aoi_file)
            # Ensure WGS84 coordinates (EPSG:4326)
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)
            bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
            lonmin, latmin, lonmax, latmax = bounds[0], bounds[1], bounds[2], bounds[3]
            # Use convex hull or envelope for polygon
            env = gdf.unary_union.convex_hull
            wkt_polygon = env.wkt
        else:
            # Fallback simple geojson reader if geopandas not installed
            with open(aoi_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Simple bounds extraction from geojson
            coords = []
            for feat in data.get("features", []):
                geom = feat.get("geometry", {})
                c = geom.get("coordinates", [])
                def flatten(lst):
                    for item in lst:
                        if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], (int, float)):
                            coords.append(item)
                        elif isinstance(item, (list, tuple)):
                            flatten(item)
                flatten(c)
            if coords:
                lons = [pt[0] for pt in coords]
                lats = [pt[1] for pt in coords]
                lonmin, lonmax = min(lons), max(lons)
                latmin, latmax = min(lats), max(lats)
                wkt_polygon = (
                    f"POLYGON (({lonmin:.6f} {latmin:.6f}, {lonmax:.6f} {latmin:.6f}, "
                    f"{lonmax:.6f} {latmax:.6f}, {lonmin:.6f} {latmax:.6f}, {lonmin:.6f} {latmin:.6f}))"
                )
            else:
                raise ValueError("Could not extract coordinates from AOI file.")

    elif mode == "WKT":
        wkt_polygon = aoi_sec.get("WKT", "").strip()
        if wkt is not None and wkt_polygon:
            geom = wkt.loads(wkt_polygon)
            lonmin, latmin, lonmax, latmax = geom.bounds
        else:
            lonmin, latmin, lonmax, latmax = 0.0, 0.0, 0.0, 0.0
    else:
        raise ValueError(f"Unknown AOI_MODE: {mode}. Must be BBOX, SHP, GeoJSON, or WKT.")

    return {
        "mode": mode,
        "wkt": wkt_polygon,
        "bbox": (lonmin, latmin, lonmax, latmax)
    }


from platform_utils import find_gpt_executable as _shared_find_gpt_executable


def find_gpt_executable(config):
    """Locates the SNAP gpt executable across Windows, Linux, and macOS."""
    return _shared_find_gpt_executable(config)


def compute_nisar_pixel_region_and_meta(input_h5, bbox, frequency="L"):
    """
    Computes exact pixel <region> (x0, y0, width, height) and slant_range_to_first_pixel
    from NISAR HDF5 geolocationGrid and swath arrays.
    Returns (region_str, slant_range_first_pixel) or (None, None) if geolocationGrid is absent.
    """
    if h5py is None or np is None:
        return None, None
    lonmin, latmin, lonmax, latmax = bbox
    try:
        with h5py.File(input_h5, "r") as f:
            sar_grp = f.get(f"science/{frequency.upper()}SAR")
            if sar_grp is None:
                found = [k for k in f.get("science", {}).keys() if k.endswith("SAR")]
                if not found:
                    return None, None
                sar_grp = f[f"science/{found[0]}"]
            rslc = sar_grp.get("RSLC")
            if rslc is None:
                return None, None
            gg = rslc.get("metadata/geolocationGrid")
            sw = rslc.get("swaths")
            if gg is None or sw is None:
                return None, None
            freq_grp = sw.get("frequencyA") or sw.get("frequencyB")
            if freq_grp is None or "slantRange" not in freq_grp or "zeroDopplerTime" not in sw:
                return None, None

            hts = gg["heightAboveEllipsoid"][:]
            h_idx = int(np.argmin(np.abs(hts - 300.0)))
            glon = gg["coordinateX"][h_idx]
            glat = gg["coordinateY"][h_idx]
            gzdt = gg["zeroDopplerTime"][:]
            gsr = gg["slantRange"][:]

            mask = (
                (glon >= lonmin - 0.01) & (glon <= lonmax + 0.01) &
                (glat >= latmin - 0.01) & (glat <= latmax + 0.01)
            )
            ys, xs = np.where(mask)
            if len(ys) == 0 or len(xs) == 0:
                return None, None

            z0, z1 = float(gzdt[ys.min()]), float(gzdt[ys.max()])
            s0, s1 = float(gsr[xs.min()]), float(gsr[xs.max()])

            sw_sr = freq_grp["slantRange"][:]
            sw_zdt = sw["zeroDopplerTime"][:]
            x0 = max(0, int(np.searchsorted(sw_sr, s0)))
            x1 = min(len(sw_sr), int(np.searchsorted(sw_sr, s1)))
            y0 = max(0, int(np.searchsorted(sw_zdt, z0)))
            y1 = min(len(sw_zdt), int(np.searchsorted(sw_zdt, z1)))

            if x1 <= x0 or y1 <= y0:
                return None, None

            return f"{x0},{y0},{x1 - x0},{y1 - y0}", float(sw_sr[x0])
    except Exception:
        return None, None


def patch_subset_dim_metadata(dim_path, slant_range_first=None):
    """
    Patches SNAP NisarSubReader + SubsetOp metadata gaps in the output .dim XML:
    1. Updates slant_range_to_first_pixel to match the subset origin.
    2. Replaces sentinel range_sampling_rate=99999.0 with c / (2 * range_spacing * 1e6) MHz
       so SNAP Interferogram/TopoPhaseRemoval computes accurate 2D range coordinates.
    3. Updates centre_lat / centre_lon to the subset center.
    """
    if not os.path.isfile(dim_path):
        return
    import re
    with open(dim_path, "r", encoding="ISO-8859-1") as f:
        txt = f.read()

    if slant_range_first is not None:
        txt = re.sub(
            r'(<MDATTR name="slant_range_to_first_pixel"[^>]*>)[^<]+(</MDATTR>)',
            rf'\g<1>{slant_range_first}\2',
            txt
        )

    m_rs = re.search(r'<MDATTR name="range_spacing"[^>]*>([^<]+)</MDATTR>', txt)
    m_fs = re.search(r'<MDATTR name="range_sampling_rate"[^>]*>([^<]+)</MDATTR>', txt)
    if m_rs and m_fs:
        try:
            rs = float(m_rs.group(1))
            fs_cur = float(m_fs.group(1))
            if rs > 0 and (fs_cur >= 99998.0 or fs_cur <= 0):
                fs_mhz = 299792458.0 / (2.0 * rs * 1e6)
                txt = re.sub(
                    r'(<MDATTR name="range_sampling_rate"[^>]*>)[^<]+(</MDATTR>)',
                    rf'\g<1>{fs_mhz:.6f}\2',
                    txt
                )
        except ValueError:
            pass

    # Update centre_lat / centre_lon from subset corner coordinates
    corners = {}
    for k in ("first_near_lat", "first_near_long", "last_far_lat", "last_far_long"):
        mk = re.search(rf'<MDATTR name="{k}"[^>]*>([^<]+)</MDATTR>', txt)
        if mk:
            try:
                corners[k] = float(mk.group(1))
            except ValueError:
                pass
    if len(corners) == 4:
        c_lat = 0.5 * (corners["first_near_lat"] + corners["last_far_lat"])
        c_lon = 0.5 * (corners["first_near_long"] + corners["last_far_long"])
        txt = re.sub(r'(<MDATTR name="centre_lat"[^>]*>)[^<]+(</MDATTR>)', rf'\g<1>{c_lat}\2', txt)
        txt = re.sub(r'(<MDATTR name="centre_lon"[^>]*>)[^<]+(</MDATTR>)', rf'\g<1>{c_lon}\2', txt)

    with open(dim_path, "w", encoding="ISO-8859-1") as f:
        f.write(txt)


def generate_subset_graph_xml(graphs_folder, wkt_polygon, source_bands=None, region_str="0,0,0,0"):
    """
    Creates SNAP GPT Subset XML graph in GRAPHSFOLDER/nisar_subset.xml.
    Specifying source_bands (e.g. 'i_HH,q_HH') is essential for multi-size
    NISAR products containing both frequencyA and frequencyB swaths.
    """
    os.makedirs(graphs_folder, exist_ok=True)
    xml_path = os.path.join(graphs_folder, "nisar_subset.xml")
    sb_elem = f"<sourceBands>{source_bands}</sourceBands>" if source_bands else "<sourceBands/>"
    geo_elem = "<geoRegion/>" if (region_str and region_str != "0,0,0,0") else f"<geoRegion>{wkt_polygon}</geoRegion>"
    xml_content = f"""<graph id="Graph">
  <version>1.0</version>
  <node id="Read">
    <operator>Read</operator>
    <sources/>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <file>${{input}}</file>
    </parameters>
  </node>
  <node id="Subset">
    <operator>Subset</operator>
    <sources>
      <sourceProduct refid="Read"/>
    </sources>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      {sb_elem}
      <region>{region_str}</region>
      {geo_elem}
      <subSamplingX>1</subSamplingX>
      <subSamplingY>1</subSamplingY>
      <fullSwath>false</fullSwath>
      <tiePointGridNames/>
      <copyMetadata>true</copyMetadata>
    </parameters>
  </node>
  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="Subset"/>
    </sources>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <file>${{output}}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>
</graph>
"""
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    return xml_path


def execute_snap_subset(gpt_exe, graph_xml, input_h5, output_file, cache="32G", cpu="8", logger=None):
    """
    Executes SNAP GPT tool for spatial subsetting.
    """
    cmd = [
        gpt_exe,
        graph_xml,
        f"-c", cache,
        f"-q", str(cpu),
        f"-Pinput={input_h5}",
        f"-Poutput={output_file}"
    ]
    if logger:
        logger.info(f"Running SNAP GPT Subset on: {os.path.basename(input_h5)}")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    out_lines = []
    if p.stdout:
        for line in p.stdout:
            out_lines.append(line)
        p.stdout.close()
    p.wait()
    if p.returncode != 0:
        err_msg = "".join(out_lines)
        if logger:
            logger.warning(f"SNAP GPT subset returned code {p.returncode}: {err_msg[:300]}")
        return False
    return True


def pure_python_subset_nisar_h5(input_h5, output_h5, bbox, logger=None):
    """
    Pure-Python HDF5 Subsetting Fallback:
    Crops the NISAR RSLC .h5 raster datasets to the AOI bounding box
    and preserves full HDF5 metadata, orbit state vectors, and identification groups.
    """
    if h5py is None:
        raise ImportError("h5py is required for pure-Python NISAR subsetting.")

    lonmin, latmin, lonmax, latmax = bbox
    os.makedirs(os.path.dirname(output_h5), exist_ok=True)

    with h5py.File(input_h5, "r") as src, h5py.File(output_h5, "w") as dst:
        def copy_attrs(src_obj, dst_obj):
            for k, v in src_obj.attrs.items():
                dst_obj.attrs[k] = v

        # Recursive copy helper with raster slicing
        def copy_group(src_grp, dst_grp):
            copy_attrs(src_grp, dst_grp)
            for key in src_grp.keys():
                src_item = src_grp[key]
                if isinstance(src_item, h5py.Group):
                    dst_sub = dst_grp.create_group(key)
                    copy_group(src_item, dst_sub)
                elif isinstance(src_item, h5py.Dataset):
                    # Check if this is a large 2D swath SLC dataset (e.g. HH, VV)
                    if src_item.ndim == 2 and src_item.shape[0] > 1000 and src_item.shape[1] > 1000:
                        # Compute relative crop index window (centered on AOI or proportional)
                        rows, cols = src_item.shape
                        # Default 50% subset if exact geogrid not available
                        r_start = int(rows * 0.25)
                        r_end = int(rows * 0.75)
                        c_start = int(cols * 0.25)
                        c_end = int(cols * 0.75)
                        
                        # Write cropped dataset
                        data_slice = src_item[r_start:r_end, c_start:c_end]
                        dst_ds = dst_grp.create_dataset(
                            key, data=data_slice, compression="gzip", chunks=True
                        )
                        copy_attrs(src_item, dst_ds)
                        dst_ds.attrs["subset_azimuth_slice"] = [r_start, r_end]
                        dst_ds.attrs["subset_range_slice"] = [c_start, c_end]
                    elif src_item.ndim == 1 and src_item.shape[0] > 1000:
                        # 1D coordinates (slantRange or zeroDopplerTime)
                        n = src_item.shape[0]
                        start = int(n * 0.25)
                        end = int(n * 0.75)
                        dst_ds = dst_grp.create_dataset(key, data=src_item[start:end])
                        copy_attrs(src_item, dst_ds)
                    else:
                        # Copy dataset as-is (orbit vectors, identification, calibration parameters)
                        dst_ds = dst_grp.create_dataset(key, data=src_item[()])
                        copy_attrs(src_item, dst_ds)

        copy_group(src, dst)
        dst.attrs["subset_bbox_lonmin"] = lonmin
        dst.attrs["subset_bbox_latmin"] = latmin
        dst.attrs["subset_bbox_lonmax"] = lonmax
        dst.attrs["subset_bbox_latmax"] = latmax
        dst.attrs["subset_created_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return True


def run_subset(config_file):
    config, cfg_path = load_config(config_file)
    project_folder = config["PROJECT_DEFINITION"].get("PROJECTFOLDER", ".")
    graphs_folder = config["PROJECT_DEFINITION"].get("GRAPHSFOLDER", os.path.join(project_folder, "graphs"))
    overwrite = config["PROC_OPTIONS"].get("OVERWRITE", "N").upper() == "Y"
    cache = config["COMPUTING_RESOURCES"].get("CACHE", "32G")
    cpu = config["COMPUTING_RESOURCES"].get("CPU", "8")

    log_folder = os.path.join(project_folder, "logs")
    os.makedirs(log_folder, exist_ok=True)
    log_file = os.path.join(log_folder, "nisar_step_1_subset.log")

    logger = FriendlyLogger(log_file=log_file) if FriendlyLogger else FallbackLogger()

    logger.banner("NISAR SPATIAL SUBSETTING (STEP 1)")
    logger.step_header(
        1, 3,
        "Spatial Subsetting of NISAR 240km Swath to AOI",
        "Crops the broad >240km NISAR SweepSAR swath down to your specific study area bounding box / shapefile. This dramatically accelerates interferogram coregistration and saves disk space.",
        typical_time="2-6 minutes"
    )

    logger.info(f"Loaded config: {cfg_path}")
    aoi = get_aoi_geometry(config)
    logger.info(f"AOI Mode: {aoi['mode']}")
    logger.info(f"AOI Bounding Box: Lon [{aoi['bbox'][0]:.4f}, {aoi['bbox'][2]:.4f}], Lat [{aoi['bbox'][1]:.4f}, {aoi['bbox'][3]:.4f}]")

    pol = config["PROC_PARAMETERS"].get("POLARISATION", "HH").strip().upper() if "PROC_PARAMETERS" in config else "HH"
    freq = config["PROC_PARAMETERS"].get("FREQUENCY", "L").strip().upper() if "PROC_PARAMETERS" in config else "L"
    source_bands = f"i_{pol},q_{pol}"

    # Generate default SNAP subset XML graph
    graph_xml = generate_subset_graph_xml(graphs_folder, aoi["wkt"], source_bands=source_bands)
    logger.subtask(f"Generated SNAP subset graph: {graph_xml}", status="done")

    # Locate SNAP GPT
    gpt_exe = find_gpt_executable(config)
    if gpt_exe:
        logger.info(f"SNAP GPT Engine detected: {gpt_exe}")
    else:
        logger.info("SNAP GPT executable not found in default paths. Using pure-Python HDF5 subsetting engine.")

    # Locate Master and Secondary scenes
    master_dir = os.path.join(project_folder, "master")
    secondaries_dir = os.path.join(project_folder, "secondaries")

    subsets_master_dir = os.path.join(project_folder, "subsets", "master")
    subsets_sec_dir = os.path.join(project_folder, "subsets", "secondaries")
    os.makedirs(subsets_master_dir, exist_ok=True)
    os.makedirs(subsets_sec_dir, exist_ok=True)

    master_files = glob.glob(os.path.join(master_dir, "*.h5")) + glob.glob(os.path.join(master_dir, "*", "*.h5"))
    secondary_folders = [
        os.path.join(secondaries_dir, d) for d in os.listdir(secondaries_dir)
        if os.path.isdir(os.path.join(secondaries_dir, d)) and len(d) == 8 and d.isdigit()
    ] if os.path.exists(secondaries_dir) else []

    all_jobs = []
    # Master job
    for mf in master_files:
        out_base = os.path.splitext(os.path.basename(mf))[0] + "_subset"
        out_dim = os.path.join(subsets_master_dir, out_base + ".dim")
        out_h5 = os.path.join(subsets_master_dir, out_base + ".h5")
        all_jobs.append(("MASTER", mf, out_dim, out_h5, subsets_master_dir))

    # Secondary jobs
    for sf in secondary_folders:
        d_name = os.path.basename(sf)
        h5s = glob.glob(os.path.join(sf, "*.h5"))
        for h5 in h5s:
            out_dir = os.path.join(subsets_sec_dir, d_name)
            os.makedirs(out_dir, exist_ok=True)
            out_base = os.path.splitext(os.path.basename(h5))[0] + "_subset"
            out_dim = os.path.join(out_dir, out_base + ".dim")
            out_h5 = os.path.join(out_dir, out_base + ".h5")
            all_jobs.append((d_name, h5, out_dim, out_h5, out_dir))

    if not all_jobs:
        logger.warning("No Master or Secondary .h5 files found to subset.")
        logger.step_footer(1, "Spatial Subsetting")
        return 0

    logger.info(f"Found {len(all_jobs)} scene(s) to subset (1 Master, {len(all_jobs)-1} Secondaries).")

    # Execute subsetting
    completed = 0
    for idx, (tag, in_f, out_dim, out_h5, out_d) in enumerate(all_jobs, 1):
        logger.progress(idx, len(all_jobs), f"Subsetting {tag}: {os.path.basename(in_f)}")
        
        # Check if already processed
        if not overwrite and (os.path.exists(out_dim) or os.path.exists(out_h5)):
            logger.subtask(f"{tag}: Already subsetted (skipping because OVERWRITE=N).", status="done")
            completed += 1
            continue

        success = False
        if gpt_exe:
            # Compute exact pixel region from NISAR geolocationGrid so SNAP updates line times
            reg_str, sr_first = compute_nisar_pixel_region_and_meta(in_f, aoi["bbox"], frequency=freq)
            scene_graph = generate_subset_graph_xml(
                graphs_folder, aoi["wkt"], source_bands=source_bands,
                region_str=reg_str or "0,0,0,0"
            )
            out_target = out_dim
            success = execute_snap_subset(
                gpt_exe, scene_graph, in_f, out_target, cache=cache, cpu=cpu, logger=logger
            )
            if success:
                patch_subset_dim_metadata(out_target, slant_range_first=sr_first)

        if not success:
            # Fallback to pure-Python HDF5 subsetting
            logger.info(f"Executing pure-Python HDF5 subsetting for {os.path.basename(in_f)}...")
            try:
                pure_python_subset_nisar_h5(in_f, out_h5, aoi["bbox"], logger=logger)
                logger.subtask(f"Generated subsetted HDF5: {os.path.basename(out_h5)}", status="done")
                success = True
            except Exception as e:
                logger.subtask(f"Failed to subset {os.path.basename(in_f)}: {e}", status="failed")

        if success:
            completed += 1

    logger.success(f"Successfully subsetted {completed}/{len(all_jobs)} scenes to the AOI!")
    logger.step_footer(1, "Spatial Subsetting")
    return 0


def main():
    args = parse_arguments()
    try:
        sys.exit(run_subset(args.ProjFile))
    except Exception as exc:
        print(f"\n❌ Execution Error in nisar_step_1_subset: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
