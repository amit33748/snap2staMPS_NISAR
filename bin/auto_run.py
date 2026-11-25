#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_run.py: Master Orchestrator for NISAR InSAR Pre-processing for StaMPS.
Executes Step 0 (Prep), Automaster, Step 1 (Subset AOI), Step 2 (Coreg & IFG), and Step 3 (StaMPS Export)
with visual friendly logging and real-time error translation.
"""

import os
import sys
import time
import argparse
import configparser
import subprocess

bin_dir = os.path.dirname(os.path.abspath(__file__))
if bin_dir not in sys.path:
    sys.path.insert(0, bin_dir)

from friendly_logger import FriendlyLogger
from platform_utils import configure_utf8_console

configure_utf8_console()

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Master Orchestrator for NISAR InSAR snap2stamps Processing Pipeline"
    )
    parser.add_argument(
        "--ProjFile", "-F",
        type=str,
        required=True,
        help="Path to project_nisar.conf configuration file"
    )
    parser.add_argument(
        "--step", "-s",
        type=str,
        default="all",
        choices=["all", "download", "0", "automaster", "1", "2", "3", "mt_prep"],
        help="Specific pipeline step to run (default: all)"
    )
    return parser.parse_args()

def run_script(script_path, conf_path, logger, step_name, extra_args=None):
    if not os.path.isfile(script_path):
        logger.error(
            f"Script Not Found: {os.path.basename(script_path)}",
            f"Could not locate the script at '{script_path}'.",
            [
                f"Ensure all scripts are placed in the bin directory: {os.path.dirname(script_path)}",
                "Verify file permissions and file naming.",
                "Consult the snap2stamps_nisar README for directory structure guidance."
            ]
        )
        return 1

    cmd = [sys.executable, script_path, "-F", conf_path]
    if extra_args:
        cmd.extend(extra_args)

    logger.info(f"Executing: {' '.join(cmd)}")
    start_time = time.time()
    
    error_buffer = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        for line in proc.stdout:
            clean_line = line.rstrip()
            print(clean_line)
            sys.stdout.flush()
            if any(k in clean_line.lower() for k in ["error", "exception", "failed", "fatal", "outofmemory"]):
                error_buffer.append(clean_line)

        proc.wait()
        elapsed = int(time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        if proc.returncode != 0:
            err_summary = "\n".join(error_buffer[-10:]) if error_buffer else f"Process exited with error code {proc.returncode}"
            logger.translate_and_report_error(err_summary, current_step=step_name)
            return proc.returncode
        else:
            logger.subtask(f"{step_name} completed successfully in {time_str}", status="done")
            return 0

    except Exception as e:
        logger.translate_and_report_error(str(e), current_step=step_name)
        return 1

def main():
    args = parse_arguments()
    conf_file = os.path.abspath(args.ProjFile)

    if not os.path.isfile(conf_file):
        print(f"❌ Error: Configuration file '{conf_file}' does not exist.")
        sys.exit(1)

    bin_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(bin_dir, "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"pipeline_run_{time.strftime('%Y%m%d_%H%M%S')}.log")

    logger = FriendlyLogger(log_file=log_file)
    logger.banner("NISAR InSAR Automated Processing Orchestrator (snap2stamps_nisar)")
    logger.info(f"Loaded Configuration: {conf_file}")
    logger.info(f"Pipeline Execution Log : {log_file}")

    config = configparser.ConfigParser()
    config.read(conf_file)

    sensor = config.get("PROC_PARAMETERS", "SENSOR", fallback="NISAR")
    auto_download = config.get("DOWNLOAD_SOURCE", "autoDownload", fallback="N").upper()
    provider = config.get("DOWNLOAD_SOURCE", "PROVIDER", fallback="ASF").upper()
    master_sel = config.get("PROC_PARAMETERS", "MASTERSEL", fallback="AUTO").upper()

    step_choice = args.step.lower()

    # Step: Download
    if step_choice in ["all", "download"] and (auto_download == "Y" or step_choice == "download"):
        logger.step_header(
            "0-DL", "4", "Download NISAR SLC Products",
            f"Automated acquisition of NISAR RSLC files from {provider}",
            typical_time="Depends on bandwidth & data volume"
        )
        dl_script = "asf_nisar_downloader.py" if provider == "ASF" else "bhoonidhi_nisar_downloader.py"
        ret = run_script(os.path.join(bin_dir, dl_script), conf_file, logger, f"Download ({provider})")
        if ret != 0 and step_choice == "download":
            sys.exit(ret)
        logger.step_footer("0-DL", f"Download ({provider})")

    # Step 0: Prep
    if step_choice in ["all", "0"]:
        logger.step_header(
            "0", "3", "Data Preparation & HDF5 Validation",
            "Scans project directory for NISAR .h5 granules, validates structure, extracts orbit state vectors & dates",
            typical_time="1-3 minutes"
        )
        ret = run_script(os.path.join(bin_dir, "nisar_step_0_prep.py"), conf_file, logger, "Step 0 - Prep")
        if ret != 0:
            logger.warning("Pipeline halted at Step 0 due to an error.")
            sys.exit(ret)
        logger.step_footer("0", "Data Preparation")

    # Automaster Selection (if AUTO)
    if (step_choice in ["all", "automaster"] and master_sel != "MANUAL") or step_choice == "automaster":
        logger.step_header(
            "M", "3", "Optimal Master Acquisition Selection",
            "Evaluates temporal baselines and perpendicular baselines to minimize decorrelation",
            typical_time="30-60 seconds"
        )
        ret = run_script(os.path.join(bin_dir, "nisar_automaster.py"), conf_file, logger, "Automaster Selection")
        if ret != 0:
            logger.warning("Pipeline halted at Master Selection due to an error.")
            sys.exit(ret)
        logger.step_footer("M", "Automaster Selection")

    # Step 1: Spatial Subset (Crop AOI)
    if step_choice in ["all", "1"]:
        logger.step_header(
            "1", "3", "Spatial Subset (AOI Cropping)",
            "Crops the >240km wide NISAR SweepSAR swath to user-specified BBOX/Shapefile to save 80% RAM & Disk",
            typical_time="3-8 minutes"
        )
        ret = run_script(os.path.join(bin_dir, "nisar_step_1_subset.py"), conf_file, logger, "Step 1 - Spatial Subset")
        if ret != 0:
            logger.warning("Pipeline halted at Step 1 due to an error.")
            sys.exit(ret)
        logger.step_footer("1", "Spatial Subset")

    # Step 2: Coregistration & Interferogram Generation
    if step_choice in ["all", "2"]:
        logger.step_header(
            "2", "3", "Coregistration & Interferogram Formation",
            "High-precision Back-Geocoding with Copernicus 30m Global DEM & Topo-phase removal (SweepSAR continuous)",
            typical_time="15-45 minutes"
        )
        ret = run_script(os.path.join(bin_dir, "nisar_step_2_coreg_ifg.py"), conf_file, logger, "Step 2 - Coreg & IFG")
        if ret != 0:
            logger.warning("Pipeline halted at Step 2 due to an error.")
            sys.exit(ret)
        logger.step_footer("2", "Coregistration & Interferogram Generation")

    # Step 3: StaMPS Export & Sanitization
    if step_choice in ["all", "3"]:
        logger.step_header(
            "3", "3", "StaMPS Export & Parameter Sanitizer",
            "Generates StaMPS-compatible diff0 & geo folders, normalizes headers and converts CRLF -> LF",
            typical_time="5-15 minutes"
        )
        ret = run_script(os.path.join(bin_dir, "nisar_step_3_export.py"), conf_file, logger, "Step 3 - StaMPS Export")
        if ret != 0:
            logger.warning("Pipeline halted at Step 3 due to an error.")
            sys.exit(ret)
        logger.step_footer("3", "StaMPS Export & Sanitization")

    # Step: mt_prep (Optional)
    if step_choice == "mt_prep":
        logger.step_header(
            "P", "StaMPS", "Pure-Python mt_prep_nisar Candidate Preparation",
            "Creates patches, runs calamp, extracts PSC candidates directly without fragile csh/gawk syntax",
            typical_time="2-10 minutes"
        )
        ret = run_script(os.path.join(bin_dir, "mt_prep_nisar.py"), conf_file, logger, "mt_prep_nisar")
        if ret != 0:
            sys.exit(ret)
        logger.step_footer("P", "mt_prep_nisar Preparation")

    # Final Success Banner & Next Steps Guidance
    if step_choice in ["all", "3"]:
        logger.banner("🎉 ALL NISAR PRE-PROCESSING STEPS COMPLETED SUCCESSFULLY!")
        logger.info("Your dataset is now fully exported and sanitized for StaMPS InSAR analysis.")
        print("-" * 80)
        print("📋 NEXT STEPS TO RUN STAMPS:")
        print("  1. Open a terminal inside the project directory (or use mt_prep_nisar.py):")
        print("     python mt_prep_nisar.py <patch_x> <patch_y> <da_thresh>")
        print("     or in Linux/WSL: mt_prep_snap <YYYYMMDD> <INSAR_dir> <da_thresh>")
        print("  2. Launch MATLAB or Octave in the INSAR directory.")
        print("  3. Run stamps(1, 8) to execute full Persistent Scatterer Interferometry.")
        print("-" * 80)

if __name__ == "__main__":
    main()
