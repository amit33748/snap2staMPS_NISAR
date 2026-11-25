#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FriendlyLogger: A human-centered, beginner-friendly logging and error translation engine
for NISAR InSAR processing with snap2stamps_nisar.
"""

import sys
import time
import os
import re

class FriendlyLogger:
    def __init__(self, log_file=None):
        self.log_file = log_file
        self.start_times = {}

    def _write(self, msg):
        print(msg)
        sys.stdout.flush()
        if self.log_file:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.log_file)), exist_ok=True)
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(msg + '\n')
            except Exception:
                pass

    def banner(self, title):
        line = "=" * 80
        self._write(f"\n{line}")
        self._write(f"  {title.center(76)}")
        self._write(f"{line}\n")

    def step_header(self, step_num, total_steps, title, what_it_does, typical_time="2-5 minutes"):
        line = "=" * 80
        subline = "-" * 80
        self._write(f"\n{line}")
        self._write(f"🚀 [STEP {step_num}/{total_steps}] {title.upper()}")
        self._write(f"{line}")
        self._write(f"ℹ️  What this does: {what_it_does}")
        self._write(f"⏱️  Typical time : {typical_time}")
        self._write(f"{subline}\n")
        self.start_times[f"step_{step_num}"] = time.time()

    def step_footer(self, step_num, title):
        elapsed = ""
        key = f"step_{step_num}"
        if key in self.start_times:
            secs = int(time.time() - self.start_times[key])
            mins = secs // 60
            rem_secs = secs % 60
            elapsed = f" in {mins}m {rem_secs}s" if mins > 0 else f" in {secs}s"
        self._write(f"\n✅ [STEP {step_num}] {title} completed successfully{elapsed}!\n")

    def info(self, msg):
        self._write(f"ℹ️  {msg}")

    def progress(self, current, total, item_name=""):
        pct = int((current / total) * 100) if total > 0 else 0
        bar_len = 30
        filled = int((pct / 100) * bar_len)
        bar = "=" * filled + (">" if filled < bar_len else "") + " " * (bar_len - filled - (1 if filled < bar_len else 0))
        details = f" - {item_name}" if item_name else ""
        self._write(f"⏳ [{current}/{total}] [{bar}] {pct}%{details}")

    def subtask(self, msg, status="done", duration=None):
        dur_str = f" (Done in {duration})" if duration else ""
        if status == "done":
            self._write(f"   ✓ {msg}{dur_str}")
        elif status == "working":
            self._write(f"   ⏳ {msg}...")
        elif status == "warning":
            self._write(f"   ⚠️ {msg}")
        elif status == "failed":
            self._write(f"   ❌ {msg}")

    def success(self, msg):
        self._write(f"🎉 {msg}")

    def warning(self, msg):
        self._write(f"⚠️  WARNING: {msg}")

    def error(self, title, problem, solutions):
        line = "=" * 80
        self._write(f"\n{line}")
        self._write(f"❌ OOPS! SOMETHING WENT WRONG: {title.upper()}")
        self._write(f"{line}")
        self._write(f"🔍 What happened:")
        self._write(f"   {problem}\n")
        self._write(f"💡 How to fix this in 3 easy steps:")
        for idx, sol in enumerate(solutions, 1):
            self._write(f"   {idx}. {sol}")
        self._write(f"{line}\n")

    def translate_and_report_error(self, raw_err_text, current_step="Unknown Step"):
        """Translates raw InSAR/SNAP Java/C-shell errors into beginner-friendly advice."""
        err_lower = raw_err_text.lower()
        
        if "outofmemory" in err_lower or "heap space" in err_lower or "java heap" in err_lower:
            self.error(
                f"Memory Limit Exceeded in {current_step}",
                "Your computer ran out of RAM / Java heap memory while processing NISAR rasters.",
                [
                    "Open the dashboard Settings and increase CACHE (e.g., from 16G to 32G or 64G).",
                    "Reduce CPU core allocation (e.g., set CPU to 4 or 6) so threads do not compete for memory.",
                    "Draw a smaller Area of Interest (AOI) box on the map to crop unneeded wide-swath areas."
                ]
            )
        elif "badly formed number" in err_lower or "@: badly formed number" in err_lower:
            self.error(
                f"Shell Number Format Error in {current_step}",
                "The parameter file contains Windows carriage returns (\\r\\n) or corrupted header dimensions.",
                [
                    "Run the built-in Post-Export Sanitizer in Step 3 or use mt_prep_nisar.py.",
                    "Verify that the master SLC is not missing from the export folder.",
                    "Make sure your dataset paths have no spaces, quotes, or special characters."
                ]
            )
        elif "lon file does not exist" in err_lower or "exit 3" in err_lower:
            self.error(
                f"Missing Coordinate Files in {current_step}",
                "StaMPS could not find the latitude/longitude lookup tables (geo/*.lon, geo/*.lat).",
                [
                    "Make sure 'outputLatLonBands' and 'outputElevationBand' are set to true in TopoPhaseRemoval.",
                    "Re-run Step 2 (Coregistration & Interferogram generation) and Step 3 (Export).",
                    "Verify that the DEM tile for your region was downloaded properly without void errors."
                ]
            )
        elif "already contains a band with the name" in err_lower:
            self.error(
                f"SNAP Band Duplication Bug in {current_step}",
                "SNAP encountered an internal band naming collision during StampsExport.",
                [
                    "Clean up temporary files in your project directory (target.dim, target.data).",
                    "Ensure only one polarization (e.g. HH or VV) is selected at a time in the configuration.",
                    "Re-run the export step using the updated 'nisar_stamps_export.xml' graph."
                ]
            )
        elif "no valid srtm" in err_lower or ("dem" in err_lower and ("fail" in err_lower or "error" in err_lower or "not found" in err_lower)):
            self.error(
                f"Elevation Model (DEM) Download Failed in {current_step}",
                "The elevation model could not be fetched automatically or has voids in this region.",
                [
                    "Switch to 'Copernicus 30m Global DEM' in your configuration (has 100% global coverage).",
                    "Check your internet connection to ensure ESA/Copernicus download servers are reachable.",
                    "Provide an External DEM GeoTIFF via the EXTDEM setting in the dashboard."
                ]
            )
        elif "no space left on device" in err_lower or "disk full" in err_lower:
            self.error(
                f"Disk Space Exhausted in {current_step}",
                "The target drive ran out of free space during raster processing.",
                [
                    "Free up at least 50-100 GB on the project drive.",
                    "Set SMARTHDD = Y in project_nisar.conf to remove intermediate files automatically.",
                    "Move raw NISAR HDF5 archive files to an external storage drive after subsetting."
                ]
            )
        else:
            # Generic friendly fallback
            first_line = raw_err_text.strip().split('\n')[0][:140] if raw_err_text else "Unknown error"
            self.error(
                f"Error Encountered in {current_step}",
                f"Details: {first_line}",
                [
                    "Check the detailed log output above for the specific failing component.",
                    "Verify all input NISAR radar files (.h5) are intact, valid HDF5, and not corrupted.",
                    "Ensure SNAP installation directory is correct and accessible."
                ]
            )
