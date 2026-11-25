#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nisar_step_2_coreg_ifg.py: Batch Coregistration & Topo-Removed Interferogram Generation for NISAR.
Part of the snap2stamps_nisar processing chain.

Uses SNAP 14.0 GPT with nisar_coreg_ifg.xml:
- DEM-Assisted Back-Geocoding with Copernicus 30m Global DEM
- Continuous SweepSAR processing (ESD omitted)
- Interferogram formation & Topo-Phase Removal with elevation and lat/lon coordinate bands
- Configurable parallel processing via GPT CACHE and CPU arguments
"""

import os
import sys
import re
import glob
import time
import shutil
import argparse
import configparser
import subprocess

# Ensure bin directory is on path for friendly_logger import
BIN_DIR = os.path.dirname(os.path.abspath(__file__))
if BIN_DIR not in sys.path:
    sys.path.insert(0, BIN_DIR)

try:
    from friendly_logger import FriendlyLogger
except ImportError:
    class FriendlyLogger:
        """Fallback logger if friendly_logger.py is unavailable."""
        def __init__(self, log_file=None):
            self.log_file = log_file
            self.start_times = {}
        def banner(self, msg): print(f"\n{'='*70}\n  {msg}\n{'='*70}\n")
        def step_header(self, step, total, title, desc, typical=""): print(f"\n[STEP {step}/{total}] {title}\n  {desc}\n")
        def step_footer(self, step, title): print(f"\n[STEP {step}] {title} Complete!\n")
        def info(self, msg): print(f"ℹ️  {msg}")
        def progress(self, cur, tot, item=""): print(f"⏳ [{cur}/{tot}] {item}")
        def subtask(self, msg, status="done", duration=None): print(f"   [{status}] {msg}")
        def success(self, msg): print(f"🎉 {msg}")
        def warning(self, msg): print(f"⚠️  {msg}")
        def error(self, title, prob, sols):
            print(f"\n❌ {title}: {prob}")
            for i, s in enumerate(sols, 1): print(f"   {i}. {s}")
        def translate_and_report_error(self, err_text, current_step=""):
            print(f"❌ Error in {current_step}: {err_text[:200]}")

def parse_args():
    parser = argparse.ArgumentParser(
        description="NISAR InSAR Step 2: Coregistration & Interferogram Generation with SNAP GPT"
    )
    parser.add_argument(
        "--ProjFile", "-F",
        type=str,
        required=True,
        help="Path to project_nisar.conf configuration file"
    )
    parser.add_argument(
        "--master", "-m",
        type=str,
        default=None,
        help="Override Master acquisition date (YYYYMMDD) or filepath"
    )
    parser.add_argument(
        "--cache", "-c",
        type=str,
        default=None,
        help="Override SNAP tile cache size (e.g. 32G, 16G)"
    )
    parser.add_argument(
        "--cpu", "-q",
        type=str,
        default=None,
        help="Override CPU thread count (e.g. 8, 12)"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Force overwrite existing coreg and ifg outputs"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and generate GPT graphs without executing SNAP"
    )
    return parser.parse_args()

def extract_date_from_name(filename):
    """
    Extracts an 8-digit date string (YYYYMMDD) from a NISAR filename or folder.
    Handles NISAR HDF5 patterns, BEAM-DIMAP filenames, and ISO timestamps.
    """
    base = os.path.basename(filename)
    # Look for standard YYYYMMDD before a T timestamp or delimiter
    match = re.search(r'(?:^|[^0-9])(20\d{6})(?:T\d{6}|[^0-9]|$)', base)
    if match:
        return match.group(1)
    # Fallback to any 8-digit sequence starting with 20
    match = re.search(r'(20\d{6})', base)
    if match:
        return match.group(1)
    return None

from platform_utils import find_gpt_executable as _shared_find_gpt_executable


def find_gpt_executable(config):
    """Locates the SNAP gpt executable from config, PATH, or standard installation paths."""
    return _shared_find_gpt_executable(config)

def find_input_scenes(project_folder, master_cfg):
    """
    Discovers available NISAR scenes across standard folder conventions:
    1. PROJECTFOLDER/subsets/master & PROJECTFOLDER/subsets/secondaries/<YYYYMMDD> (from Step 1)
    2. PROJECTFOLDER/subset (legacy flat subset dir)
    3. PROJECTFOLDER/master & PROJECTFOLDER/secondaries/<YYYYMMDD> (or slaves)
    4. PROJECTFOLDER/raw
    5. Directly in PROJECTFOLDER
    """
    subsets_master_dir = os.path.join(project_folder, "subsets", "master")
    subsets_sec_dir = os.path.join(project_folder, "subsets", "secondaries")

    # Prefer subsetted products if available
    all_files = []
    for sub_dir in [subsets_master_dir, subsets_sec_dir]:
        if os.path.isdir(sub_dir):
            dims = glob.glob(os.path.join(sub_dir, "*.dim")) + glob.glob(os.path.join(sub_dir, "*", "*.dim"))
            h5s = glob.glob(os.path.join(sub_dir, "*.h5")) + glob.glob(os.path.join(sub_dir, "*", "*.h5"))
            all_files.extend(dims if dims else h5s)

    if not all_files:
        search_dirs = [
            os.path.join(project_folder, "subset"),
            os.path.join(project_folder, "master"),
            os.path.join(project_folder, "slaves"),
            os.path.join(project_folder, "secondaries"),
            os.path.join(project_folder, "raw"),
            project_folder
        ]
        for s_dir in search_dirs:
            if os.path.isdir(s_dir):
                # Prefer .dim BEAM-DIMAP headers or .h5 granules (1 or 2 levels deep)
                dims = glob.glob(os.path.join(s_dir, "*.dim")) + glob.glob(os.path.join(s_dir, "*", "*.dim"))
                h5s = glob.glob(os.path.join(s_dir, "*.h5")) + glob.glob(os.path.join(s_dir, "*", "*.h5"))
                all_files.extend(dims if dims else h5s)

    # Deduplicate and sort by date
    unique_scenes = {}
    for f in all_files:
        dt = extract_date_from_name(f)
        if dt and dt not in unique_scenes:
            unique_scenes[dt] = os.path.abspath(f)

    if not unique_scenes:
        return None, []

    # Identify master
    master_date = None
    if master_cfg:
        master_cfg_clean = master_cfg.strip()
        cfg_date = extract_date_from_name(master_cfg_clean)
        if cfg_date and cfg_date in unique_scenes:
            master_date = cfg_date
        elif os.path.isfile(master_cfg_clean):
            # Master given as full path
            f_dt = extract_date_from_name(master_cfg_clean)
            if f_dt:
                unique_scenes[f_dt] = os.path.abspath(master_cfg_clean)
                master_date = f_dt

    if not master_date:
        # Check subsets/master first, then master/ folder
        for m_dir in [subsets_master_dir, os.path.join(project_folder, "master")]:
            if os.path.isdir(m_dir):
                m_files = glob.glob(os.path.join(m_dir, "*.dim")) or glob.glob(os.path.join(m_dir, "*.h5"))
                if m_files:
                    master_date = extract_date_from_name(m_files[0])
                    if master_date:
                        unique_scenes[master_date] = os.path.abspath(m_files[0])
                        break

    if not master_date:
        master_date = sorted(unique_scenes.keys())[0]

    master_file = unique_scenes[master_date]
    secondaries = [
        (dt, path) for dt, path in sorted(unique_scenes.items())
        if dt != master_date
    ]

    return (master_date, master_file), secondaries

def run_gpt_process(gpt_exe, graph_path, cache, cpu, logger, step_name):
    """Executes SNAP GPT with real-time logging and return code checking."""
    cmd = [gpt_exe, graph_path, "-c", str(cache), "-q", str(cpu)]
    logger.info(f"GPT Command: {' '.join(cmd)}")
    
    start_time = time.time()
    err_lines = []
    
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    try:
        for line in proc.stdout:
            clean = line.rstrip()
            if clean:
                print(f"   [SNAP] {clean}")
                sys.stdout.flush()
                # Check for fatal error keywords
                if any(k in clean.lower() for k in ["error", "exception", "failed", "fatal", "outofmemory"]):
                    err_lines.append(clean)
        proc.wait()
    except Exception as e:
        proc.kill()
        logger.error(
            "GPT Process Interrupted",
            str(e),
            ["Check system resources and SNAP installation."]
        )
        return False, str(e), 0

    elapsed = time.time() - start_time
    if proc.returncode != 0:
        err_msg = "\n".join(err_lines[-12:]) if err_lines else f"GPT process exited with code {proc.returncode}"
        logger.translate_and_report_error(err_msg, current_step=step_name)
        return False, err_msg, elapsed

    return True, "", elapsed

def main():
    args = parse_args()
    conf_path = os.path.abspath(args.ProjFile)

    if not os.path.isfile(conf_path):
        print(f"❌ Error: Configuration file '{conf_path}' not found.")
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(conf_path)

    project_folder = os.path.abspath(config.get("PROJECT_DEFINITION", "PROJECTFOLDER", fallback="."))
    graphs_cfg = config.get("PROJECT_DEFINITION", "GRAPHSFOLDER", fallback="")
    
    # Locate nisar_coreg_ifg.xml graph template
    candidate_graphs = [
        os.path.join(graphs_cfg, "nisar_coreg_ifg.xml") if graphs_cfg else "",
        os.path.join(BIN_DIR, "..", "graphs", "nisar_coreg_ifg.xml"),
        os.path.join(project_folder, "graphs", "nisar_coreg_ifg.xml")
    ]
    graph_template = None
    for cg in candidate_graphs:
        if cg and os.path.isfile(cg):
            graph_template = os.path.abspath(cg)
            break

    if not graph_template:
        print(f"❌ Error: Could not locate 'nisar_coreg_ifg.xml' graph template in: {candidate_graphs}")
        sys.exit(1)

    # Computing resources
    cache = args.cache or config.get("COMPUTING_RESOURCES", "CACHE", fallback="16G").strip()
    cpu = args.cpu or config.get("COMPUTING_RESOURCES", "CPU", fallback="8").strip()
    overwrite = args.overwrite or (config.get("PROC_OPTIONS", "OVERWRITE", fallback="N").strip().upper() == "Y")
    smarthdd = config.get("PROC_OPTIONS", "SMARTHDD", fallback="Y").strip().upper() == "Y"

    # DEM Settings
    dem_name = config.get("PROC_PARAMETERS", "DEM_NAME", fallback="Copernicus 30m Global DEM").strip()
    ext_dem = config.get("PROC_PARAMETERS", "EXTDEM", fallback="").strip()
    if ext_dem and not os.path.isfile(ext_dem):
        print(f"⚠️  Warning: External DEM '{ext_dem}' not found. Falling back to Copernicus 30m Global DEM.")
        ext_dem = ""

    # Master definition
    master_cfg = args.master or config.get("PROC_PARAMETERS", "MASTER", fallback="").strip()

    # Setup directories
    coreg_dir = os.path.join(project_folder, "coreg")
    ifg_dir = os.path.join(project_folder, "ifg")
    graphs_run_dir = os.path.join(project_folder, "graphs")
    logs_dir = os.path.join(project_folder, "logs")

    for d in [coreg_dir, ifg_dir, graphs_run_dir, logs_dir]:
        os.makedirs(d, exist_ok=True)

    log_file = os.path.join(logs_dir, f"coreg_ifg_{time.strftime('%Y%m%d_%H%M%S')}.log")
    logger = FriendlyLogger(log_file=log_file)

    logger.banner("NISAR Step 2: Coregistration & Interferogram Generation")
    logger.info(f"Project Directory : {project_folder}")
    logger.info(f"Graph Template    : {graph_template}")
    logger.info(f"DEM Model         : {dem_name}" + (f" (External: {ext_dem})" if ext_dem else ""))
    logger.info(f"Resources         : CACHE={cache}, CPU={cpu}")
    logger.info(f"Overwrite Mode    : {'YES' if overwrite else 'NO'}")

    # Check GPT executable
    gpt_exe = find_gpt_executable(config)
    if not gpt_exe and not args.dry_run:
        logger.error(
            "SNAP GPT Executable Not Found",
            "Could not detect SNAP 'gpt' command on PATH or SNAP_INSTALLATION_FOLDER.",
            [
                "Open project_nisar.conf and set SNAP_INSTALLATION_FOLDER to your SNAP install directory.",
                "Ensure SNAP is installed (version 9, 10, 11, or 14).",
                "Verify that bin/gpt.exe exists in the configured SNAP folder."
            ]
        )
        sys.exit(1)
    
    if gpt_exe:
        logger.info(f"SNAP GPT Engine   : {gpt_exe}")

    # Discover inputs
    master_info, secondaries = find_input_scenes(project_folder, master_cfg)
    if not master_info or not secondaries:
        logger.error(
            "Insufficient NISAR Scenes for InSAR Processing",
            f"Found {len(secondaries)} secondary scenes in {project_folder}. InSAR requires at least 1 master and 1 secondary.",
            [
                "Run Step 0 (nisar_step_0_prep.py) and Step 1 (nisar_step_1_subset.py) first.",
                "Verify your project directory contains valid NISAR RSLC .h5 granules or subsetted .dim products.",
                "Check folder permissions and file naming."
            ]
        )
        sys.exit(1)

    master_date, master_file = master_info
    logger.info(f"Master Scene      : {master_date} ({os.path.basename(master_file)})")
    logger.info(f"Secondaries Count : {len(secondaries)} pairs to process")

    # Read base XML graph template
    with open(graph_template, "r", encoding="utf-8") as f:
        graph_xml_base = f.read()

    total_pairs = len(secondaries)
    completed_pairs = 0
    skipped_pairs = 0
    failed_pairs = []

    for idx, (sec_date, sec_file) in enumerate(secondaries, 1):
        pair_name = f"{master_date}_{sec_date}"
        logger.progress(idx, total_pairs, f"Pair: {pair_name}")

        out_coreg_dim = os.path.join(coreg_dir, f"{pair_name}.dim")
        out_ifg_dim = os.path.join(ifg_dir, f"{pair_name}.dim")

        # Check existing outputs
        if not overwrite and os.path.isfile(out_coreg_dim) and os.path.isfile(out_ifg_dim):
            logger.subtask(f"Pair {pair_name} already exists. Skipping (OVERWRITE=N).", status="done")
            skipped_pairs += 1
            continue

        # Prepare customized graph XML
        # SNAP gpt on Windows requires forward slashes or escaped backslashes in paths
        m_path_clean = os.path.abspath(master_file).replace("\\", "/")
        s_path_clean = os.path.abspath(sec_file).replace("\\", "/")
        coreg_dir_clean = os.path.abspath(coreg_dir).replace("\\", "/")
        ifg_dir_clean = os.path.abspath(ifg_dir).replace("\\", "/")
        ext_dem_clean = os.path.abspath(ext_dem).replace("\\", "/") if ext_dem else ""

        graph_xml = graph_xml_base
        graph_xml = graph_xml.replace("MASTER", m_path_clean)
        graph_xml = graph_xml.replace("SECONDARY", s_path_clean)
        graph_xml = graph_xml.replace("OUTPUTCOREGFOLDER", coreg_dir_clean)
        graph_xml = graph_xml.replace("OUTPUTIFGFOLDER", ifg_dir_clean)
        graph_xml = graph_xml.replace("OUTPUTFILE", pair_name)
        
        # DEM replacement
        if ext_dem_clean:
            graph_xml = graph_xml.replace("<externalDEMFile>EXTERNALDEM</externalDEMFile>", f"<externalDEMFile>{ext_dem_clean}</externalDEMFile>")
            graph_xml = graph_xml.replace("Copernicus 30m Global DEM", "External DEM")
        else:
            graph_xml = graph_xml.replace("<externalDEMFile>EXTERNALDEM</externalDEMFile>", "<externalDEMFile/>")
            if dem_name != "Copernicus 30m Global DEM":
                graph_xml = graph_xml.replace("Copernicus 30m Global DEM", dem_name)

        run_graph_file = os.path.join(graphs_run_dir, f"nisar_coreg_ifg_{pair_name}.xml")
        with open(run_graph_file, "w", encoding="utf-8") as gf:
            gf.write(graph_xml)

        if args.dry_run:
            logger.subtask(f"[DRY RUN] Generated graph: {run_graph_file}", status="done")
            completed_pairs += 1
            continue

        logger.subtask(f"Computing Coregistration & Topo-Removed IFG for {pair_name}...", status="working")
        success, err, dur = run_gpt_process(gpt_exe, run_graph_file, cache, cpu, logger, f"Coreg & IFG: {pair_name}")

        dur_mins, dur_secs = divmod(int(dur), 60)
        dur_str = f"{dur_mins}m {dur_secs}s" if dur_mins > 0 else f"{dur_secs}s"

        if success:
            logger.subtask(f"Pair {pair_name} finished successfully in {dur_str}", status="done")
            completed_pairs += 1
            if smarthdd and os.path.isfile(run_graph_file):
                try: os.remove(run_graph_file)
                except Exception: pass
        else:
            logger.subtask(f"Pair {pair_name} failed after {dur_str}", status="failed")
            failed_pairs.append((pair_name, err))

    # Summary
    logger.banner("STEP 2 PROCESSING SUMMARY")
    logger.info(f"Total Pairs Considered : {total_pairs}")
    logger.info(f"Successfully Processed : {completed_pairs}")
    logger.info(f"Skipped (Already Done) : {skipped_pairs}")
    logger.info(f"Failed Pairs           : {len(failed_pairs)}")

    if failed_pairs:
        logger.warning(f"{len(failed_pairs)} pair(s) encountered errors during coregistration.")
        for p, err in failed_pairs:
            print(f"   ❌ {p}: {err[:120]}")
        sys.exit(1)
    else:
        logger.success("All NISAR coregistered SLCs and interferograms generated successfully!")
        sys.exit(0)

if __name__ == "__main__":
    main()
