#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mt_prep_nisar.py: Pure-Python drop-in replacement for mt_prep_snap for StaMPS InSAR.
Handles patch creation, calamp amplitude calibration, and PS/SB candidate extraction
(pscands.1.ij, pscands.1.da, pscands.1.ll, pscands.1.hgt, pscands.1.ph)
directly in pure Python using memory-mapped NumPy arrays, completely eliminating
the need for fragile csh, tcsh, gawk, or compiled C binaries on Windows and Linux.

Author: Antigravity Team
Compatible with StaMPS v4.1b / StaMPS-master and SNAP 14.0.
"""

import os
import sys
import glob
import math
import argparse
import configparser
import datetime
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None

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
    def info(self, msg): print(f"ℹ️  {msg}")
    def subtask(self, msg, status="done"): print(f"  [{status.upper()}] {msg}")
    def success(self, msg): print(f"✅ {msg}")
    def warning(self, msg): print(f"⚠️  {msg}")
    def error(self, title, prob, sols):
        print(f"\n❌ ERROR: {title}\nProblem: {prob}\nSolutions:")
        for idx, s in enumerate(sols, 1): print(f"  {idx}. {s}")


def parse_args():
    """
    Parses both standard mt_prep_snap positional arguments:
      mt_prep_nisar <master_yyyymmdd> <datadir> <da_thresh> [rg_patches] [az_patches] [rg_overlap] [az_overlap] [maskfile]
    AND optional flags (--master, --datadir, etc., or -F project_nisar.conf).
    """
    # Check if flags are used or pure positional arguments
    raw_args = sys.argv[1:]
    
    # Check for help
    if "-h" in raw_args or "--help" in raw_args:
        print_usage()
        sys.exit(0)

    # Flag-based parsing
    parser = argparse.ArgumentParser(
        description="mt_prep_nisar: Pure-Python StaMPS Preparation Engine (Drop-in for mt_prep_snap)"
    )
    parser.add_argument("master_pos", nargs="?", default=None, help="Master date (YYYYMMDD)")
    parser.add_argument("datadir_pos", nargs="?", default=None, help="Data directory with StaMPS structure")
    parser.add_argument("da_thresh_pos", nargs="?", default=None, help="Amplitude dispersion threshold (default 0.4)")
    parser.add_argument("rg_patches_pos", nargs="?", default=None, help="Number of patches in range (default 1)")
    parser.add_argument("az_patches_pos", nargs="?", default=None, help="Number of patches in azimuth (default 1)")
    parser.add_argument("rg_overlap_pos", nargs="?", default=None, help="Overlap pixels in range (default 50)")
    parser.add_argument("az_overlap_pos", nargs="?", default=None, help="Overlap pixels in azimuth (default 50)")
    parser.add_argument("maskfile_pos", nargs="?", default=None, help="Optional binary maskfile")

    parser.add_argument("--master", "-m", type=str, default=None)
    parser.add_argument("--datadir", "-d", type=str, default=None)
    parser.add_argument("--da_thresh", "-t", type=float, default=None)
    parser.add_argument("--rg_patches", "-prg", type=int, default=None)
    parser.add_argument("--az_patches", "-paz", type=int, default=None)
    parser.add_argument("--rg_overlap", "-org", type=int, default=None)
    parser.add_argument("--az_overlap", "-oaz", type=int, default=None)
    parser.add_argument("--maskfile", type=str, default="")
    parser.add_argument("--ProjFile", "-F", type=str, default=None)

    parsed = parser.parse_args()

    # Determine master and datadir from config file if -F provided
    if parsed.ProjFile:
        cfg = configparser.ConfigParser()
        cfg.read(parsed.ProjFile)
        proj_dir = cfg["PROJECT_DEFINITION"].get("PROJECTFOLDER", ".")
        mst = cfg["PROC_PARAMETERS"].get("MASTER", "").strip()
        insar_dir = os.path.join(proj_dir, f"INSAR_{mst}") if mst else ""
        if insar_dir and os.path.isdir(insar_dir):
            export_dir = insar_dir
        else:
            insar_matches = sorted(glob.glob(os.path.join(proj_dir, "INSAR_*")))
            export_dir = insar_matches[0] if insar_matches else os.path.join(proj_dir, "stamps_export")
        if not parsed.master:
            parsed.master = mst
        if not parsed.datadir:
            parsed.datadir = export_dir

    # Resolve positional vs flag arguments
    master = parsed.master or parsed.master_pos
    datadir = parsed.datadir or parsed.datadir_pos
    
    # Check defaults
    da_thresh_val = parsed.da_thresh or (float(parsed.da_thresh_pos) if parsed.da_thresh_pos else None)
    rg_patches_val = parsed.rg_patches or (int(parsed.rg_patches_pos) if parsed.rg_patches_pos else 1)
    az_patches_val = parsed.az_patches or (int(parsed.az_patches_pos) if parsed.az_patches_pos else 1)
    rg_overlap_val = parsed.rg_overlap or (int(parsed.rg_overlap_pos) if parsed.rg_overlap_pos else 50)
    az_overlap_val = parsed.az_overlap or (int(parsed.az_overlap_pos) if parsed.az_overlap_pos else 50)
    maskfile_val = parsed.maskfile or (parsed.maskfile_pos if parsed.maskfile_pos else "")

    if not master or not datadir:
        print_usage()
        sys.exit(4)

    return {
        "master": master,
        "datadir": os.path.abspath(datadir),
        "da_thresh": da_thresh_val,
        "rg_patches": rg_patches_val,
        "az_patches": az_patches_val,
        "rg_overlap": rg_overlap_val,
        "az_overlap": az_overlap_val,
        "maskfile": maskfile_val
    }


def print_usage():
    print("""
=============================================================================
mt_prep_nisar: Pure-Python Drop-in Replacement for mt_prep_snap
=============================================================================
Usage:
  python mt_prep_nisar.py <master_yyyymmdd> <datadir> [da_thresh] [rg_patches] [az_patches] [rg_overlap] [az_overlap] [maskfile]
  or
  python mt_prep_nisar.py -F project_nisar.conf [options]

Arguments:
  master_yyyymmdd = Master acquisition date (e.g. 20240515)
  datadir         = Data directory (containing rslc/, diff0/, geo/, dem/)
  da_thresh       = Amplitude dispersion threshold (default: 0.4 for PS, 0.6 for SB)
  rg_patches      = Number of range patches (default: 1)
  az_patches      = Number of azimuth patches (default: 1)
  rg_overlap      = Overlapping pixels in range (default: 50)
  az_overlap      = Overlapping pixels in azimuth (default: 50)
  maskfile        = Optional char maskfile
=============================================================================
""")


def parse_rsc_par(par_file):
    """
    Parses StaMPS/GAMMA/SNAP .rslc.par file for azimuth_lines and range_samples.
    """
    width = None
    length = None
    with open(par_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.strip().split(":")
            if len(parts) >= 2:
                key = parts[0].strip().lower()
                val = parts[1].strip().split()[0]
                if key in ["range_samples", "width"]:
                    width = int(val)
                elif key in ["azimuth_lines", "length", "lines"]:
                    length = int(val)
            else:
                parts_space = line.strip().split()
                if len(parts_space) >= 2:
                    k = parts_space[0].strip().lower()
                    v = parts_space[1].strip()
                    if k in ["range_samples", "width"] and v.isdigit():
                        width = int(v)
                    elif k in ["azimuth_lines", "length", "lines"] and v.isdigit():
                        length = int(v)
    return width, length


def detect_binary_dtype(filepath, width, length, is_complex=True):
    """
    Auto-detects big-endian ('>c8' / '>f4', standard SNAP/GAMMA StampsExport)
    vs little-endian ('<c8' / '<f4', native synthetic arrays) by sampling the center window.
    """
    be_dt = np.dtype(">c8" if is_complex else ">f4")
    le_dt = np.dtype("<c8" if is_complex else "<f4")
    if not filepath or not os.path.exists(filepath):
        return be_dt

    try:
        r0 = max(0, length // 2 - 32)
        r1 = min(length, r0 + 64)
        c0 = max(0, width // 2 - 32)
        c1 = min(width, c0 + 64)

        mm_be = np.memmap(filepath, dtype=be_dt, mode="r", shape=(length, width))
        mm_le = np.memmap(filepath, dtype=le_dt, mode="r", shape=(length, width))
        s_be = mm_be[r0:r1, c0:c1]
        s_le = mm_le[r0:r1, c0:c1]

        if is_complex:
            a_be = np.abs(s_be)
            a_le = np.abs(s_le)
            be_ok = np.isfinite(a_be) & (a_be > 1e-6) & (a_be < 1e8)
            le_ok = np.isfinite(a_le) & (a_le > 1e-6) & (a_le < 1e8)
        else:
            be_ok = np.isfinite(s_be) & (np.abs(s_be) < 1e6) & (s_be != 0)
            le_ok = np.isfinite(s_le) & (np.abs(s_le) < 1e6) & (s_le != 0)

        be_score = int(np.sum(be_ok))
        le_score = int(np.sum(le_ok))
        return be_dt if be_score >= le_score else le_dt
    except Exception:
        return le_dt


def calibrate_amplitudes(slc_files, width, length, mask_array=None, logger=None, c_dtype=None):
    """
    Pure Python calamp calculation:
    Computes amplitude calibration constant for each SLC file.
    calib_factor = mean(|z|) for |z| > 0.001 and mask == 0.
    """
    calib_factors = {}
    total_files = len(slc_files)
    if c_dtype is None and slc_files:
        c_dtype = detect_binary_dtype(slc_files[0], width, length, is_complex=True)

    for idx, slc_path in enumerate(slc_files, 1):
        if logger:
            logger.progress(idx, total_files, f"Calibrating amplitude: {os.path.basename(slc_path)}")

        try:
            # Memory map SLC (complex64 = 8 bytes per pixel)
            data = np.memmap(slc_path, dtype=c_dtype or np.complex64, mode="r", shape=(length, width))
            # Compute amplitude |z|
            amps = np.abs(data)
            
            # Filter condition: amp > 0.001 and mask == 0
            if mask_array is not None:
                valid = np.isfinite(amps) & (amps > 0.001) & (mask_array == 0)
            else:
                valid = np.isfinite(amps) & (amps > 0.001)

            valid_amps = amps[valid]
            if len(valid_amps) > 0:
                mean_amp = float(np.mean(valid_amps))
            else:
                mean_amp = 1.0

            calib_factors[slc_path] = mean_amp
            del data
            del amps
        except Exception as e:
            if logger:
                logger.warning(f"Error computing calamp on {os.path.basename(slc_path)}: {e}")
            calib_factors[slc_path] = 1.0

    return calib_factors


def generate_patches(width, length, prg=1, paz=1, overlap_rg=50, overlap_az=50, workdir="."):
    """
    Computes grid patches with overlap, writes patch.list and PATCH_X/patch.in.
    """
    width_p = width // prg
    length_p = length // paz

    patch_list_path = os.path.join(workdir, "patch.list")
    patches = []

    ip = 0
    with open(patch_list_path, "w", encoding="utf-8") as plist:
        for irg in range(1, prg + 1):
            for iaz in range(1, paz + 1):
                ip += 1
                patch_name = f"PATCH_{ip}"
                patch_dir = os.path.join(workdir, patch_name)
                os.makedirs(patch_dir, exist_ok=True)
                plist.write(f"{patch_name}\n")

                # Range boundaries (1-based)
                start_rg1 = width_p * (irg - 1) + 1
                end_rg1 = width if irg == prg else width_p * irg
                start_rg = max(1, start_rg1 - overlap_rg)
                end_rg = min(width, end_rg1 + overlap_rg)

                # Azimuth boundaries (1-based)
                start_az1 = length_p * (iaz - 1) + 1
                end_az1 = length if iaz == paz else length_p * iaz
                start_az = max(1, start_az1 - overlap_az)
                end_az = min(length, end_az1 + overlap_az)

                # Write patch.in (with overlap)
                with open(os.path.join(patch_dir, "patch.in"), "w", encoding="utf-8") as f:
                    f.write(f"{start_rg}\n{end_rg}\n{start_az}\n{end_az}\n")

                # Write patch_noover.in (without overlap)
                with open(os.path.join(patch_dir, "patch_noover.in"), "w", encoding="utf-8") as f:
                    f.write(f"{start_rg1}\n{end_rg1}\n{start_az1}\n{end_az1}\n")

                patches.append({
                    "name": patch_name,
                    "dir": patch_dir,
                    "start_rg": start_rg,
                    "end_rg": end_rg,
                    "start_az": start_az,
                    "end_az": end_az,
                    "start_rg1": start_rg1,
                    "end_rg1": end_rg1,
                    "start_az1": start_az1,
                    "end_az1": end_az1
                })

    return patches


def extract_candidates_pure_python(
    patches, slc_files, calib_factors, diff_files, dem_file, lon_file, lat_file,
    width, length, da_thresh, mask_array=None, logger=None
):
    """
    Pure-Python drop-in replacement for mt_extract_cands:
    1. Evaluates Amplitude Dispersion: Da = std_A / mean_A
    2. Writes pscands.1.ij, pscands.1.ij.int, pscands.1.da, mean_amp.flt
    3. Extracts candidate Lon/Lat to pscands.1.ll
    4. Extracts candidate DEM height to pscands.1.hgt
    5. Extracts candidate differential phase to pscands.1.ph
    """
    total_candidates_all = 0

    # Auto-detect endianness (SNAP StampsExport writes big-endian '>f4' / '>c8')
    c_dtype = detect_binary_dtype(slc_files[0], width, length, is_complex=True) if slc_files else np.dtype(">c8")
    f_ref = lon_file or lat_file or dem_file
    f_dtype = detect_binary_dtype(f_ref, width, length, is_complex=False) if f_ref else np.dtype(">f4")
    d_dtype = detect_binary_dtype(diff_files[0], width, length, is_complex=True) if diff_files else c_dtype

    # Memory map DEM, lon, lat
    dem_mm = np.memmap(dem_file, dtype=f_dtype, mode="r", shape=(length, width)) if dem_file and os.path.exists(dem_file) else None
    lon_mm = np.memmap(lon_file, dtype=f_dtype, mode="r", shape=(length, width)) if lon_file and os.path.exists(lon_file) else None
    lat_mm = np.memmap(lat_file, dtype=f_dtype, mode="r", shape=(length, width)) if lat_file and os.path.exists(lat_file) else None

    # Memory map diff files
    diff_mms = []
    for df in diff_files:
        if os.path.exists(df):
            diff_mms.append(np.memmap(df, dtype=d_dtype, mode="r", shape=(length, width)))

    n_slc = len(slc_files)

    for p_idx, p in enumerate(patches, 1):
        p_name = p["name"]
        p_dir = p["dir"]
        if logger:
            logger.info(f"Extracting PS candidates for {p_name} ({p_idx}/{len(patches)})...")

        # 0-based index slices
        az_s = p["start_az"] - 1
        az_e = p["end_az"]
        rg_s = p["start_rg"] - 1
        rg_e = p["end_rg"]

        p_h = az_e - az_s
        p_w = rg_e - rg_s

        sum_amp = np.zeros((p_h, p_w), dtype=np.float64)
        sum_amp_sq = np.zeros((p_h, p_w), dtype=np.float64)
        zero_amp_mask = np.zeros((p_h, p_w), dtype=bool)

        for slc_path in slc_files:
            cal_c = calib_factors.get(slc_path, 1.0)
            slc_mm = np.memmap(slc_path, dtype=c_dtype, mode="r", shape=(length, width))
            slc_patch = slc_mm[az_s:az_e, rg_s:rg_e]
            amp = np.abs(slc_patch) / cal_c

            # Mark pixels with near-zero amplitude
            zero_amp_mask |= (amp <= 0.00005)

            sum_amp += amp
            sum_amp_sq += amp * amp

            del slc_mm

        # Write mean_amp.flt (binary float32)
        mean_amp_flt_path = os.path.join(p_dir, "mean_amp.flt")
        sum_amp.astype(np.float32).tofile(mean_amp_flt_path)

        # Compute amplitude dispersion Da = sqrt( N * sum(A^2) / (sum(A)^2) - 1 )
        denom = sum_amp * sum_amp
        valid_denom = (denom > 0) & (~zero_amp_mask)
        if mask_array is not None:
            patch_mask = mask_array[az_s:az_e, rg_s:rg_e]
            valid_denom &= (patch_mask == 0)

        da_grid = np.full((p_h, p_w), 999.0, dtype=np.float32)
        da_sq = np.zeros((p_h, p_w), dtype=np.float32)

        ratio = (n_slc * sum_amp_sq[valid_denom]) / denom[valid_denom] - 1.0
        ratio = np.maximum(0.0, ratio)
        da_grid[valid_denom] = np.sqrt(ratio)

        # Select candidate pixels: Da < da_thresh
        sel_y, sel_x = np.where(da_grid < da_thresh)
        num_cands = len(sel_y)
        total_candidates_all += num_cands

        if logger:
            logger.subtask(f"{p_name}: Selected {num_cands} PS candidate points.", status="done")

        # Global 0-based raster indices
        global_y = az_s + sel_y
        global_x = rg_s + sel_x
        cand_da = da_grid[sel_y, sel_x]

        # 1. Write pscands.1.ij (text: pscid y x)
        ij_path = os.path.join(p_dir, "pscands.1.ij")
        with open(ij_path, "w", encoding="utf-8") as fij:
            for cid in range(num_cands):
                fij.write(f"{cid + 1} {global_y[cid]} {global_x[cid]}\n")

        # 2. Write pscands.1.ij.int (binary 32-bit big-endian J, I -> rg, az)
        ji_path = os.path.join(p_dir, "pscands.1.ij.int")
        with open(ji_path, "wb") as fji:
            if num_cands > 0:
                # Big-endian 32-bit int: J (range), then I (azimuth)
                ji_arr = np.zeros((num_cands, 2), dtype=">i4")
                ji_arr[:, 0] = global_x
                ji_arr[:, 1] = global_y
                ji_arr.tofile(fji)

        # 3. Write pscands.1.da (text: Da per line)
        da_path = os.path.join(p_dir, "pscands.1.da")
        with open(da_path, "w", encoding="utf-8") as fda:
            for val in cand_da:
                fda.write(f"{val:.6f}\n")

        # 4. Extract Lon/Lat -> pscands.1.ll (binary float32 pairs: lon, lat)
        ll_path = os.path.join(p_dir, "pscands.1.ll")
        with open(ll_path, "wb") as fll:
            if num_cands > 0:
                if lon_mm is not None and lat_mm is not None:
                    c_lon = lon_mm[global_y, global_x].astype(np.float32)
                    c_lat = lat_mm[global_y, global_x].astype(np.float32)
                else:
                    c_lon = np.zeros(num_cands, dtype=np.float32)
                    c_lat = np.zeros(num_cands, dtype=np.float32)
                ll_arr = np.column_stack((c_lon, c_lat)).astype(np.float32)
                ll_arr.tofile(fll)

        # 5. Extract DEM -> pscands.1.hgt (binary float32)
        hgt_path = os.path.join(p_dir, "pscands.1.hgt")
        with open(hgt_path, "wb") as fhgt:
            if num_cands > 0:
                if dem_mm is not None:
                    c_hgt = dem_mm[global_y, global_x].astype(np.float32)
                else:
                    c_hgt = np.zeros(num_cands, dtype=np.float32)
                c_hgt.tofile(fhgt)

        # 6. Extract Phase -> pscands.1.ph (binary complex float32: for each candidate, for each ifg)
        ph_path = os.path.join(p_dir, "pscands.1.ph")
        with open(ph_path, "wb") as fph:
            if num_cands > 0 and diff_mms:
                # Shape: (num_candidates, num_ifgs) in complex64
                n_ifgs = len(diff_mms)
                ph_arr = np.zeros((num_cands, n_ifgs), dtype=np.complex64)
                for ifg_idx, dmm in enumerate(diff_mms):
                    ph_arr[:, ifg_idx] = dmm[global_y, global_x]
                ph_arr.tofile(fph)

    return total_candidates_all


def run_mt_prep(args):
    master = args["master"]
    datadir = args["datadir"]
    rg_patches = args["rg_patches"]
    az_patches = args["az_patches"]
    rg_overlap = args["rg_overlap"]
    az_overlap = args["az_overlap"]
    maskfile = args["maskfile"]

    workdir = os.getcwd()
    log_file = os.path.join(workdir, "mt_prep_nisar.log")

    logger = FriendlyLogger(log_file=log_file) if FriendlyLogger else FallbackLogger()

    logger.banner("STAMPS PURE-PYTHON NISAR PREPARATION (MT_PREP_NISAR)")
    logger.info(f"Master Date: {master}")
    logger.info(f"Data Directory: {datadir}")
    logger.info(f"Working Directory: {workdir}")

    # 1. Detect PS or SB mode
    sb_dir = os.path.join(datadir, "SMALL_BASELINES")
    sb_flag = os.path.exists(sb_dir)
    logger.info(f"Processing Mode: {'Small Baselines (SB)' if sb_flag else 'Persistent Scatterer (PS)'}")

    da_thresh = args["da_thresh"]
    if da_thresh is None:
        da_thresh = 0.6 if sb_flag else 0.4
    logger.info(f"Amplitude Dispersion Threshold (da_thresh): {da_thresh}")
    logger.info(f"Patch Configuration: {rg_patches} range x {az_patches} azimuth (Overlap: {rg_overlap} rg, {az_overlap} az)")

    # 2. Locate master .rslc.par
    if sb_flag:
        par_candidates = glob.glob(os.path.join(sb_dir, "*", f"{master}.*rslc.par"))
    else:
        par_candidates = glob.glob(os.path.join(datadir, "*slc", f"{master}.*rslc.par")) + \
                         glob.glob(os.path.join(datadir, "rslc", f"{master}.*rslc.par"))

    if not par_candidates:
        # Fallback search any .rslc.par
        par_candidates = glob.glob(os.path.join(datadir, "**", "*.rslc.par"), recursive=True)

    if not par_candidates:
        logger.error(
            "Master .rslc.par Parameter File Missing",
            f"Could not find {master}.*rslc.par in {datadir}/rslc or {datadir}/SMALL_BASELINES.",
            [
                "Verify that StaMPS export (Step 3) completed successfully.",
                f"Ensure the master date matches the exported filenames (Master specified: {master}).",
                f"Check that files exist inside {datadir}/rslc/*.rslc.par."
            ]
        )
        return 1

    rsc_par_file = par_candidates[0]
    logger.info(f"Found master parameter file: {rsc_par_file}")

    width, length = parse_rsc_par(rsc_par_file)
    if not width or not length:
        logger.error(
            "Failed Parsing Raster Dimensions",
            f"Could not extract width (range_samples) and length (azimuth_lines) from {rsc_par_file}.",
            [
                f"Inspect the contents of {rsc_par_file} for range_samples and azimuth_lines entries.",
                "Ensure StaMPS export was performed with psiFormat=true."
            ]
        )
        return 1

    logger.info(f"Raster Dimensions: Width = {width} range samples, Length = {length} azimuth lines.")

    # 3. Write initial StaMPS descriptor text files
    with open(os.path.join(workdir, "processor.txt"), "w", encoding="utf-8") as f:
        f.write("snap\n")
    with open(os.path.join(workdir, "width.txt"), "w", encoding="utf-8") as f:
        f.write(f"{width}\n")
    with open(os.path.join(workdir, "len.txt"), "w", encoding="utf-8") as f:
        f.write(f"{length}\n")
    with open(os.path.join(workdir, "rsc.txt"), "w", encoding="utf-8") as f:
        f.write(f"{rsc_par_file}\n")
    logger.subtask("Created processor.txt, width.txt, len.txt, and rsc.txt", status="done")

    # 4. Search SLC files for calamp
    if sb_flag:
        slc_files = sorted(glob.glob(os.path.join(sb_dir, "*", "*.*slc")))
        selfile_name = "selsbc.in"
    else:
        slc_files = sorted(glob.glob(os.path.join(datadir, "*slc", "*.*slc")))
        selfile_name = "selpsc.in"

    if not slc_files:
        logger.error(
            "No SLC Files Found for Calibration",
            f"Found no .rslc or .slc files in {datadir}/rslc or {datadir}/SMALL_BASELINES.",
            [
                "Check that StaMPS export produced .rslc files in the export folder.",
                f"Check path: {datadir}/rslc"
            ]
        )
        return 1

    logger.info(f"Found {len(slc_files)} SLC acquisition(s) for calibration.")

    # Write calamp.in
    calamp_in_path = os.path.join(workdir, "calamp.in")
    with open(calamp_in_path, "w", encoding="utf-8") as f:
        for sf in slc_files:
            f.write(f"{sf}\n")

    # Read optional maskfile
    mask_array = None
    if maskfile and os.path.exists(maskfile):
        logger.info(f"Applying spatial mask: {maskfile}")
        mask_array = np.fromfile(maskfile, dtype=np.uint8).reshape((length, width))

    # Run calamp
    logger.info("Computing amplitude calibration factors across SLC stack...")
    calib_factors = calibrate_amplitudes(slc_files, width, length, mask_array=mask_array, logger=logger)

    # Write calamp.out
    calamp_out_path = os.path.join(workdir, "calamp.out")
    with open(calamp_out_path, "w", encoding="utf-8") as f:
        for sf in slc_files:
            f.write(f"{sf} {calib_factors[sf]:.6e}\n")
    logger.subtask(f"Written calamp.out ({len(calib_factors)} factors)", status="done")

    # Write selpsc.in / selsbc.in
    selfile_path = os.path.join(workdir, selfile_name)
    with open(selfile_path, "w", encoding="utf-8") as f:
        f.write(f"{da_thresh}\n{width}\n")
        for sf in slc_files:
            f.write(f"{sf} {calib_factors[sf]:.6e}\n")
    logger.subtask(f"Written {selfile_name}", status="done")

    # 5. Setup Patches
    logger.info(f"Setting up {rg_patches * az_patches} patch grid directory structure...")
    patches = generate_patches(
        width, length,
        prg=rg_patches, paz=az_patches,
        overlap_rg=rg_overlap, overlap_az=az_overlap,
        workdir=workdir
    )
    logger.subtask(f"Created {len(patches)} patch directories and patch.list", status="done")

    # 6. Setup extraction input files (pscphase.in, pscdem.in, psclonlat.in)
    # pscphase.in
    if sb_flag:
        diff_files = sorted(glob.glob(os.path.join(sb_dir, "*", "*.diff")))
    else:
        diff_files = sorted(glob.glob(os.path.join(datadir, "diff0", "*.diff")))
    pscphase_in_path = os.path.join(workdir, "pscphase.in")
    with open(pscphase_in_path, "w", encoding="utf-8") as f:
        f.write(f"{width}\n")
        for df in diff_files:
            f.write(f"{df}\n")
    logger.subtask(f"Written pscphase.in ({len(diff_files)} interferograms)", status="done")

    # pscdem.in
    dem_candidates = glob.glob(os.path.join(datadir, "geo", "*dem.rdc"))
    dem_file = dem_candidates[0] if dem_candidates else ""
    pscdem_in_path = os.path.join(workdir, "pscdem.in")
    with open(pscdem_in_path, "w", encoding="utf-8") as f:
        f.write(f"{width}\n")
        if dem_file:
            f.write(f"{dem_file}\n")
    logger.subtask(f"Written pscdem.in (DEM: {os.path.basename(dem_file)})", status="done")

    # psclonlat.in
    lon_candidates = glob.glob(os.path.join(datadir, "geo", "*.lon"))
    lat_candidates = glob.glob(os.path.join(datadir, "geo", "*.lat"))
    lon_file = lon_candidates[0] if lon_candidates else ""
    lat_file = lat_candidates[0] if lat_candidates else ""

    if not lon_file or not lat_file:
        logger.warning(f"Lon/lat files not found in {datadir}/geo. Georeferencing will use radar coordinate fallback.")

    psclonlat_in_path = os.path.join(workdir, "psclonlat.in")
    with open(psclonlat_in_path, "w", encoding="utf-8") as f:
        f.write(f"{width}\n")
        if lon_file:
            f.write(f"{lon_file}\n")
        if lat_file:
            f.write(f"{lat_file}\n")
    logger.subtask(f"Written psclonlat.in", status="done")

    # 7. Candidate extraction
    logger.info("Executing candidate pixel selection and property extraction in pure Python...")
    total_cands = extract_candidates_pure_python(
        patches=patches,
        slc_files=slc_files,
        calib_factors=calib_factors,
        diff_files=diff_files,
        dem_file=dem_file,
        lon_file=lon_file,
        lat_file=lat_file,
        width=width,
        length=length,
        da_thresh=da_thresh,
        mask_array=mask_array,
        logger=logger
    )

    logger.success(f"StaMPS preparation complete! Extracted {total_cands} total PS candidates across {len(patches)} patch(es).")
    logger.info("Ready for StaMPS MATLAB steps: stamps(1, 1) through stamps(8, 8)!")
    return 0


def main():
    args = parse_args()
    try:
        sys.exit(run_mt_prep(args))
    except Exception as exc:
        print(f"\n❌ Execution Error in mt_prep_nisar: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

