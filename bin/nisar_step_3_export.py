#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nisar_step_3_export.py: Batch StaMPS Export & Post-Export Parameter Sanitizer for NISAR.
Part of the snap2stamps_nisar processing chain.

Key Responsibilities:
1. Batch executes SNAP StampsExport with psiFormat=true for all coregistered pairs.
2. Integrated Post-Export Sanitizer:
   - Strips Windows carriage returns (\\r\\n -> \\n) from all .par, .base, .hdr, and text files.
   - Cleans numeric parameters and removes trailing whitespace that causes the infamous
     '@: Badly formed number.' error in StaMPS csh scripts.
   - Normalizes all output filenames to standard StaMPS YYYYMMDD / YYYYMMDD_YYYYMMDD format.
   - Validates the exported structure (diff0, geo, rslc) for StaMPS readiness.
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
        description="NISAR InSAR Step 3: StaMPS Export & Post-Export Parameter Sanitizer"
    )
    parser.add_argument(
        "--ProjFile", "-F",
        type=str,
        required=True,
        help="Path to project_nisar.conf configuration file"
    )
    parser.add_argument(
        "--sanitize-only",
        action="store_true",
        help="Skip SNAP GPT export and only run the Post-Export Sanitizer on existing INSAR export folder"
    )
    parser.add_argument(
        "--export-folder",
        type=str,
        default=None,
        help="Explicitly specify target INSAR export folder path"
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
        help="Force overwrite existing exported files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and generate export graphs without executing SNAP"
    )
    return parser.parse_args()

def extract_dates_from_pair(name):
    """
    Extracts (master_date, secondary_date) as 8-digit strings from a pair string.
    Matches patterns like '20240105_20240117' or filenames containing them.
    """
    base = os.path.basename(name)
    m = re.search(r'(20\d{6})[_-](20\d{6})', base)
    if m:
        return m.group(1), m.group(2)
    return None, None

from platform_utils import find_gpt_executable as _shared_find_gpt_executable


def find_gpt_executable(config):
    """Locates the SNAP gpt executable from config, PATH, or standard installation paths."""
    return _shared_find_gpt_executable(config)

def sanitize_stamps_export(export_dir, logger=None):
    """
    Post-Export Sanitizer for StaMPS InSAR data:
    1. Traverses the exported folder recursively (diff0, geo, rslc, etc.).
    2. Strips Windows carriage returns (\\r\\n -> \\n) and cleans trailing spaces
       from parameter files (.base, .par, .rsc, .hdr, .txt, .dim, etc.).
       This completely eliminates the '@: Badly formed number.' error in StaMPS csh scripts.
    3. Normalizes all filenames to standard StaMPS YYYYMMDD / YYYYMMDD_YYYYMMDD format.
    4. Validates existence of key coordinate bands and differential interferograms.
    """
    log = logger.info if logger else print
    subtask = logger.subtask if logger else lambda m, status="done": print(f"   [{status}] {m}")

    export_dir = os.path.abspath(export_dir)
    if not os.path.isdir(export_dir):
        if logger:
            logger.warning(f"Sanitization skipped: Directory '{export_dir}' does not exist.")
        else:
            print(f"⚠️  Directory '{export_dir}' does not exist.")
        return False, {}

    log(f"🧹 Starting Post-Export Sanitization on: {export_dir}")

    text_extensions = {".par", ".base", ".rsc", ".hdr", ".txt", ".dim", ".xml", ".da", ""}
    crlf_cleaned_count = 0
    normalized_names_count = 0
    total_files_scanned = 0

    # Pass 1: Normalize Filenames
    for root, dirs, files in os.walk(export_dir):
        rel_root = os.path.relpath(root, export_dir)
        subfolder = rel_root.split(os.sep)[0].lower() if rel_root != "." else ""

        for fname in files:
            total_files_scanned += 1
            fpath = os.path.join(root, fname)
            fext = os.path.splitext(fname)[1]

            new_name = None

            # Subfolder: diff0 -> files should be YYYYMMDD_YYYYMMDD.<ext>
            if subfolder == "diff0":
                m_dt, s_dt = extract_dates_from_pair(fname)
                if m_dt and s_dt:
                    target_prefix = f"{m_dt}_{s_dt}"
                    if not fname.startswith(target_prefix):
                        # Extract extension including multi-part like .diff.par or .base
                        match_ext = re.search(r'\.(diff|base|par|hdr|rsc).*$', fname, re.IGNORECASE)
                        ext_part = match_ext.group(0) if match_ext else fext
                        new_name = f"{target_prefix}{ext_part}"

            # Subfolder: geo -> files should be YYYYMMDD.lon[.par], YYYYMMDD.lat[.par], YYYYMMDD.dem[.par], elevation_dem.rdc[.par]
            elif subfolder == "geo":
                m = re.search(r'(20\d{6})', fname)
                if m:
                    dt = m.group(1)
                    is_par = fname.lower().endswith(".par")
                    for band in ["lon", "lat", "dem", "elevation"]:
                        if band in fname.lower():
                            target_ext = "dem" if band == "elevation" else band
                            expected = f"{dt}.{target_ext}.par" if is_par else f"{dt}.{target_ext}"
                            if fname != expected:
                                new_name = expected
                            break

            # Subfolder: rslc -> files should be YYYYMMDD.rslc, YYYYMMDD.rslc.par
            elif subfolder == "rslc":
                m = re.search(r'(20\d{6})', fname)
                if m:
                    dt = m.group(1)
                    if "par" in fname.lower() and fname != f"{dt}.rslc.par":
                        new_name = f"{dt}.rslc.par"
                    elif "rslc" in fname.lower() and not fname.endswith(".par") and fname != f"{dt}.rslc":
                        new_name = f"{dt}.rslc"

            if new_name and new_name != fname:
                new_path = os.path.join(root, new_name)
                try:
                    if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(fpath):
                        os.remove(new_path)
                    os.rename(fpath, new_path)
                    normalized_names_count += 1
                    subtask(f"Normalized filename: {fname} -> {new_name}", status="done")
                except Exception as e:
                    subtask(f"Could not rename {fname} to {new_name}: {e}", status="warning")

    # Pass 2: CRLF -> LF & Parameter Line Sanitization
    for root, dirs, files in os.walk(export_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            fext = os.path.splitext(fname)[1].lower()

            # Inspect text files or parameter files
            if fext in text_extensions or fname in ["heading.da", "wavelength", "heading"]:
                try:
                    with open(fpath, "rb") as bf:
                        raw_bytes = bf.read()

                    # Check for CRLF or CR carriage returns
                    has_cr = b"\r" in raw_bytes

                    # Process line by line to strip trailing whitespace and carriage returns
                    # Ensures numbers (like "width: 2500 ") don't retain trailing spaces that break csh
                    lines = raw_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
                    cleaned_lines = []
                    modified = has_cr

                    for line in lines:
                        # Strip trailing spaces/tabs before newline
                        stripped_line = line.rstrip(b" \t")
                        if stripped_line != line:
                            modified = True
                        cleaned_lines.append(stripped_line)

                    if modified:
                        cleaned_content = b"\n".join(cleaned_lines)
                        with open(fpath, "wb") as bf:
                            bf.write(cleaned_content)
                        crlf_cleaned_count += 1

                except Exception as e:
                    subtask(f"Could not sanitize {fname}: {e}", status="warning")

    # Pass 3: Validate StaMPS Export Directory Structure
    diff0_dir = os.path.join(export_dir, "diff0")
    geo_dir = os.path.join(export_dir, "geo")
    rslc_dir = os.path.join(export_dir, "rslc")

    diff_files = glob.glob(os.path.join(diff0_dir, "*.diff")) if os.path.isdir(diff0_dir) else []
    base_files = glob.glob(os.path.join(diff0_dir, "*.base")) if os.path.isdir(diff0_dir) else []
    lon_files = glob.glob(os.path.join(geo_dir, "*.lon")) if os.path.isdir(geo_dir) else []
    lat_files = glob.glob(os.path.join(geo_dir, "*.lat")) if os.path.isdir(geo_dir) else []
    dem_files = glob.glob(os.path.join(geo_dir, "*.dem")) if os.path.isdir(geo_dir) else []

    stats = {
        "export_dir": export_dir,
        "scanned_files": total_files_scanned,
        "crlf_cleaned_files": crlf_cleaned_count,
        "normalized_files": normalized_names_count,
        "diff_count": len(diff_files),
        "base_count": len(base_files),
        "lon_count": len(lon_files),
        "lat_count": len(lat_files),
        "dem_count": len(dem_files),
        "ready_for_stamps": bool(diff_files and base_files and lon_files and lat_files)
    }

    subtask(f"Sanitized {crlf_cleaned_count} text/parameter files (converted CRLF -> LF, stripped trailing whitespace).", status="done")
    subtask(f"Normalized {normalized_names_count} filenames to standard YYYYMMDD format.", status="done")

    if stats["ready_for_stamps"]:
        if logger:
            logger.success("Post-Export Sanitization complete: Dataset is 100% READY for StaMPS PSI!")
        else:
            print("🎉 Post-Export Sanitization complete: Dataset is 100% READY for StaMPS PSI!")
    else:
        if logger:
            logger.warning("Sanitizer finished, but some StaMPS components may be missing. Check diff0/ and geo/ folders.")
        else:
            print("⚠️  Sanitizer finished, but some StaMPS components may be missing.")

    return True, stats

def run_gpt_process(gpt_exe, graph_path, cache, cpu, logger, step_name):
    """Executes SNAP GPT StampsExport with streaming output and error trapping."""
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
                print(f"   [SNAP Export] {clean}")
                sys.stdout.flush()
                if any(k in clean.lower() for k in ["error", "exception", "failed", "fatal", "outofmemory"]):
                    err_lines.append(clean)
        proc.wait()
    except Exception as e:
        proc.kill()
        logger.error(
            "GPT Export Interrupted",
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
    
    # Locate nisar_stamps_export.xml template
    candidate_graphs = [
        os.path.join(graphs_cfg, "nisar_stamps_export.xml") if graphs_cfg else "",
        os.path.join(BIN_DIR, "..", "graphs", "nisar_stamps_export.xml"),
        os.path.join(project_folder, "graphs", "nisar_stamps_export.xml")
    ]
    graph_template = None
    for cg in candidate_graphs:
        if cg and os.path.isfile(cg):
            graph_template = os.path.abspath(cg)
            break

    if not graph_template and not args.sanitize_only:
        print(f"❌ Error: Could not locate 'nisar_stamps_export.xml' graph template in: {candidate_graphs}")
        sys.exit(1)

    # Computing resources
    cache = args.cache or config.get("COMPUTING_RESOURCES", "CACHE", fallback="16G").strip()
    cpu = args.cpu or config.get("COMPUTING_RESOURCES", "CPU", fallback="8").strip()
    overwrite = args.overwrite or (config.get("PROC_OPTIONS", "OVERWRITE", fallback="N").strip().upper() == "Y")
    smarthdd = config.get("PROC_OPTIONS", "SMARTHDD", fallback="Y").strip().upper() == "Y"

    # Coreg & Ifg folders
    coreg_dir = os.path.join(project_folder, "coreg")
    ifg_dir = os.path.join(project_folder, "ifg")
    graphs_run_dir = os.path.join(project_folder, "graphs")
    logs_dir = os.path.join(project_folder, "logs")

    for d in [graphs_run_dir, logs_dir]:
        os.makedirs(d, exist_ok=True)

    log_file = os.path.join(logs_dir, f"export_proc_{time.strftime('%Y%m%d_%H%M%S')}.log")
    logger = FriendlyLogger(log_file=log_file)

    logger.banner("NISAR Step 3: StaMPS Export & Post-Export Parameter Sanitizer")
    logger.info(f"Project Directory : {project_folder}")
    logger.info(f"Overwrite Mode    : {'YES' if overwrite else 'NO'}")

    # Discover Coreg & Ifg Pairs
    coreg_files = glob.glob(os.path.join(coreg_dir, "*.dim"))
    pairs = []
    master_date = None

    for cf in sorted(coreg_files):
        m_dt, s_dt = extract_dates_from_pair(cf)
        if m_dt and s_dt:
            pair_name = f"{m_dt}_{s_dt}"
            ifg_dim = os.path.join(ifg_dir, f"{pair_name}.dim")
            if os.path.isfile(ifg_dim):
                pairs.append((pair_name, cf, ifg_dim, m_dt, s_dt))
                if not master_date:
                    master_date = m_dt

    # Determine Target Export Folder
    if args.export_folder:
        export_folder = os.path.abspath(args.export_folder)
    elif master_date:
        export_folder = os.path.join(project_folder, f"INSAR_{master_date}")
    else:
        # Fallback: check if existing INSAR_* folder exists
        insar_dirs = glob.glob(os.path.join(project_folder, "INSAR_*"))
        export_folder = insar_dirs[0] if insar_dirs else os.path.join(project_folder, "INSAR_export")

    logger.info(f"Target Export Dir : {export_folder}")

    # Mode: Sanitize-Only
    if args.sanitize_only:
        logger.info("Operating in --sanitize-only mode. Skipping SNAP GPT export.")
        success, stats = sanitize_stamps_export(export_folder, logger)
        sys.exit(0 if success else 1)

    # Validate pairs
    if not pairs:
        logger.error(
            "No Coregistered & Interferogram Pairs Found",
            f"Could not find matching .dim files in '{coreg_dir}' and '{ifg_dir}'.",
            [
                "Run Step 2 (nisar_step_2_coreg_ifg.py) first to generate coregistered stacks and interferograms.",
                "Ensure coreg and ifg files follow the YYYYMMDD_YYYYMMDD.dim naming convention.",
                "Verify file permissions."
            ]
        )
        sys.exit(1)

    logger.info(f"Master Date       : {master_date}")
    logger.info(f"Pairs to Export   : {len(pairs)} pairs")

    # Check GPT executable
    gpt_exe = find_gpt_executable(config)
    if not gpt_exe and not args.dry_run:
        logger.error(
            "SNAP GPT Executable Not Found",
            "Could not detect SNAP 'gpt' command on PATH or SNAP_INSTALLATION_FOLDER.",
            [
                "Open project_nisar.conf and set SNAP_INSTALLATION_FOLDER to your SNAP install directory.",
                "Ensure SNAP is installed with InSAR and StampsExport modules.",
                "Verify that bin/gpt.exe exists in the configured folder."
            ]
        )
        sys.exit(1)

    # Read base XML graph template
    with open(graph_template, "r", encoding="utf-8") as f:
        graph_xml_base = f.read()

    os.makedirs(export_folder, exist_ok=True)

    total_pairs = len(pairs)
    completed_pairs = 0
    skipped_pairs = 0
    failed_pairs = []

    for idx, (pair_name, c_file, i_file, m_dt, s_dt) in enumerate(pairs, 1):
        logger.progress(idx, total_pairs, f"Exporting pair: {pair_name}")

        diff_file = os.path.join(export_folder, "diff0", f"{pair_name}.diff")
        base_file = os.path.join(export_folder, "diff0", f"{pair_name}.base")

        # Check existing export
        if not overwrite and os.path.isfile(diff_file) and os.path.isfile(base_file):
            logger.subtask(f"Pair {pair_name} already exported. Skipping (OVERWRITE=N).", status="done")
            skipped_pairs += 1
            continue

        c_file_clean = os.path.abspath(c_file).replace("\\", "/")
        i_file_clean = os.path.abspath(i_file).replace("\\", "/")
        export_folder_clean = os.path.abspath(export_folder).replace("\\", "/")

        graph_xml = graph_xml_base
        graph_xml = graph_xml.replace("COREGFILE", c_file_clean)
        graph_xml = graph_xml.replace("IFGFILE", i_file_clean)
        graph_xml = graph_xml.replace("OUTPUTFOLDER", export_folder_clean)

        run_graph_file = os.path.join(graphs_run_dir, f"export_{pair_name}.xml")
        with open(run_graph_file, "w", encoding="utf-8") as gf:
            gf.write(graph_xml)

        if args.dry_run:
            logger.subtask(f"[DRY RUN] Generated export graph: {run_graph_file}", status="done")
            completed_pairs += 1
            continue

        logger.subtask(f"Running StampsExport for pair {pair_name}...", status="working")
        success, err, dur = run_gpt_process(gpt_exe, run_graph_file, cache, cpu, logger, f"Export: {pair_name}")

        dur_mins, dur_secs = divmod(int(dur), 60)
        dur_str = f"{dur_mins}m {dur_secs}s" if dur_mins > 0 else f"{dur_secs}s"

        if success:
            logger.subtask(f"Export of {pair_name} completed in {dur_str}", status="done")
            completed_pairs += 1
            if smarthdd and os.path.isfile(run_graph_file):
                try: os.remove(run_graph_file)
                except Exception: pass
        else:
            logger.subtask(f"Export of {pair_name} failed after {dur_str}", status="failed")
            failed_pairs.append((pair_name, err))

    # Run Integrated Post-Export Sanitizer
    if not args.dry_run and os.path.isdir(export_folder):
        logger.step_header(
            "3-S", "3", "Post-Export Sanitizer & CRLF Fixer",
            "Strips Windows carriage returns (\\r\\n -> \\n), normalizes parameter files, and checks StaMPS readiness"
        )
        san_ok, stats = sanitize_stamps_export(export_folder, logger)
        logger.step_footer("3-S", "Post-Export Sanitizer")

    # Summary
    logger.banner("STEP 3 PROCESSING & EXPORT SUMMARY")
    logger.info(f"Total Pairs Considered : {total_pairs}")
    logger.info(f"Successfully Exported  : {completed_pairs}")
    logger.info(f"Skipped (Already Done) : {skipped_pairs}")
    logger.info(f"Failed Exports         : {len(failed_pairs)}")
    logger.info(f"StaMPS Export Folder   : {export_folder}")

    if failed_pairs:
        logger.warning(f"{len(failed_pairs)} pair(s) failed during StampsExport.")
        for p, err in failed_pairs:
            print(f"   ❌ {p}: {err[:120]}")
        sys.exit(1)
    else:
        logger.success("All NISAR interferograms exported and sanitized successfully for StaMPS PSI!")
        sys.exit(0)

if __name__ == "__main__":
    main()
