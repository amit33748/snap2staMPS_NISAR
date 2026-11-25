#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nisar_step_0_prep.py: Scan project folder for NISAR HDF5 (.h5) files,
validate NISAR HDF5 structures (/science/LSAR/RSLC/...), extract orbit state
vectors and timestamps, and setup master/secondary directories.

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
import datetime
from pathlib import Path

try:
    import h5py
except ImportError:
    h5py = None

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
        description="NISAR Step 0: Scan, Validate NISAR RSLC HDF5, Extract Orbits & Setup Directories"
    )
    parser.add_argument(
        "--ProjFile", "-F",
        type=str,
        default="project_nisar.conf",
        help="Path to project configuration file (e.g., project_nisar.conf)"
    )
    return parser.parse_args()


def load_config(config_path):
    """Load and parse the project configuration file."""
    if not os.path.isabs(config_path):
        # Look in current directory first, then script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.join(script_dir, config_path)
        if os.path.exists(cand):
            config_path = cand
        elif not os.path.exists(config_path):
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

    config = configparser.ConfigParser()
    config.read(config_path)
    return config, os.path.abspath(config_path)


def decode_h5_str(val):
    """Safely decode HDF5 dataset string/bytes attribute or value."""
    if val is None:
        return ""
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="ignore").strip()
    if isinstance(val, np.ndarray):
        if val.ndim == 0:
            return decode_h5_str(val.item())
        return [decode_h5_str(x) for x in val]
    return str(val).strip()


def validate_and_inspect_nisar_h5(h5_path, frequency="L", polarisation="HH"):
    """
    Validates the NISAR RSLC HDF5 structure and extracts essential metadata,
    including timestamps and orbit state vectors.
    
    Structure inspected:
      /science/LSAR (or SSAR)
        /identification: missionId, radarBand, lookDirection, orbitPassDirection, track, frame, timestamps
        /RSLC/metadata/orbit: time, position, velocity, referenceEpoch
        /RSLC/swaths/frequencyA (or B): polarisation dataset (HH, VV, etc.), slantRange, zeroDopplerTime
    """
    if h5py is None:
        raise ImportError("h5py package is required to inspect NISAR HDF5 files. Run: pip install h5py")

    meta = {
        "filepath": os.path.abspath(h5_path),
        "filename": os.path.basename(h5_path),
        "valid": False,
        "band": frequency.upper(),
        "mission": "NISAR",
        "look_direction": "Right",
        "orbit_pass": "Ascending",
        "track": None,
        "frame": None,
        "start_time_utc": None,
        "stop_time_utc": None,
        "center_time_utc": None,
        "date_yyyymmdd": None,
        "swath_shape": None,
        "pol_found": None,
        "num_orbit_vectors": 0,
        "orbit_state_vectors": [],
        "errors": []
    }

    try:
        with h5py.File(h5_path, "r") as h5:
            # 1. Determine root SAR group (/science/LSAR or /science/SSAR)
            root_band = f"science/{frequency.upper()}SAR"
            if root_band not in h5:
                # Auto-fallback to any available SAR band under /science
                found_roots = [k for k in h5.get("science", {}).keys() if k.endswith("SAR")]
                if found_roots:
                    root_band = f"science/{found_roots[0]}"
                    meta["band"] = found_roots[0].replace("SAR", "")
                else:
                    meta["errors"].append(f"No valid SAR root group (/science/*SAR) found in {meta['filename']}.")
                    return meta

            sar_grp = h5[root_band]

            # 2. Inspect identification group
            ident_grp = sar_grp.get("identification")
            if ident_grp is not None:
                if "missionId" in ident_grp:
                    meta["mission"] = decode_h5_str(ident_grp["missionId"][()])
                if "lookDirection" in ident_grp:
                    meta["look_direction"] = decode_h5_str(ident_grp["lookDirection"][()])
                if "orbitPassDirection" in ident_grp:
                    meta["orbit_pass"] = decode_h5_str(ident_grp["orbitPassDirection"][()])
                
                track_val = ident_grp.get("trackNumber") or ident_grp.get("track")
                if track_val is not None:
                    meta["track"] = int(np.array(track_val).flatten()[0])
                    
                frame_val = ident_grp.get("frameNumber") or ident_grp.get("frame")
                if frame_val is not None:
                    meta["frame"] = int(np.array(frame_val).flatten()[0])

                t_start = ident_grp.get("zeroDopplerStartTime")
                if t_start is not None:
                    meta["start_time_utc"] = decode_h5_str(t_start[()])

                t_stop = ident_grp.get("zeroDopplerEndTime")
                if t_stop is not None:
                    meta["stop_time_utc"] = decode_h5_str(t_stop[()])
            else:
                meta["errors"].append("Group /identification is missing.")

            # 3. Inspect RSLC swaths & polarization
            rslc_grp = sar_grp.get("RSLC")
            if rslc_grp is None:
                meta["errors"].append("Group /RSLC is missing.")
                return meta

            swaths_grp = rslc_grp.get("swaths")
            if swaths_grp is not None:
                # Typically swaths/frequencyA or swaths/frequencyB
                freq_keys = list(swaths_grp.keys())
                freq_key = "frequencyA" if "frequencyA" in freq_keys else (freq_keys[0] if freq_keys else None)
                if freq_key:
                    freq_grp = swaths_grp[freq_key]
                    pol_candidates = [polarisation.upper(), "HH", "VV", "HV", "VH"]
                    matched_pol = None
                    for p in pol_candidates:
                        if p in freq_grp:
                            matched_pol = p
                            break
                    if matched_pol:
                        ds = freq_grp[matched_pol]
                        meta["swath_shape"] = list(ds.shape)
                        meta["pol_found"] = matched_pol
                    else:
                        meta["errors"].append(f"No matching polarization ({polarisation}) found in {freq_key}.")
            else:
                meta["errors"].append("Group /RSLC/swaths is missing.")

            # 4. Inspect orbit state vectors
            meta_grp = rslc_grp.get("metadata")
            if meta_grp is not None:
                orbit_grp = meta_grp.get("orbit")
                if orbit_grp is not None:
                    times = orbit_grp.get("time")
                    pos = orbit_grp.get("position")
                    vel = orbit_grp.get("velocity")
                    ref_epoch = decode_h5_str(orbit_grp["referenceEpoch"][()]) if "referenceEpoch" in orbit_grp else ""

                    if times is not None and pos is not None and vel is not None:
                        t_arr = np.array(times)
                        p_arr = np.array(pos)
                        v_arr = np.array(vel)
                        n_pts = len(t_arr)
                        meta["num_orbit_vectors"] = n_pts
                        
                        # Store compact orbit vectors
                        orbit_list = []
                        for i in range(n_pts):
                            orbit_list.append({
                                "t": float(t_arr[i]),
                                "pos": [float(x) for x in p_arr[i]],
                                "vel": [float(x) for x in v_arr[i]],
                                "epoch": ref_epoch
                            })
                        meta["orbit_state_vectors"] = orbit_list

            # 5. Extract date and center timestamp
            dt = None
            if meta["start_time_utc"]:
                clean_ts = meta["start_time_utc"].replace("Z", "").split(".")[0]
                try:
                    dt = datetime.datetime.fromisoformat(clean_ts)
                except Exception:
                    pass

            # Fallback date extraction from filename (e.g. NISAR_L1_PR_RSLC_..._20240515T...)
            if dt is None:
                fname = meta["filename"]
                import re
                match = re.search(r"(\d{8})T(\d{6})", fname) or re.search(r"_(\d{8})_", fname)
                if match:
                    d_str = match.group(1)
                    meta["date_yyyymmdd"] = d_str
                    meta["start_time_utc"] = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}T00:00:00Z"
            else:
                meta["date_yyyymmdd"] = dt.strftime("%Y%m%d")

            # Check validity
            if meta["date_yyyymmdd"] and meta["num_orbit_vectors"] > 0:
                meta["valid"] = True
            elif meta["date_yyyymmdd"]:
                meta["valid"] = True
                meta["errors"].append("Orbit vectors missing or non-standard, but timestamp validated.")
            else:
                meta["errors"].append("Could not resolve acquisition timestamp or date.")

    except Exception as exc:
        meta["errors"].append(f"HDF5 read exception: {str(exc)}")

    return meta


def setup_project_directories(project_folder):
    """
    Creates standard directory tree for snap2stamps_nisar:
      PROJECTFOLDER/
        master/
        secondaries/
        subsets/
          master/
          secondaries/
        coreg/
        ifg/
        stamps_export/
        logs/
    """
    subdirs = [
        "master",
        "secondaries",
        "subsets",
        os.path.join("subsets", "master"),
        os.path.join("subsets", "secondaries"),
        "coreg",
        "ifg",
        "stamps_export",
        "logs"
    ]
    created = []
    for sd in subdirs:
        p = os.path.join(project_folder, sd)
        if not os.path.exists(p):
            os.makedirs(p, exist_ok=True)
            created.append(p)
    return created


def organize_nisar_files(project_folder, scenes_meta, logger=None):
    """
    Organizes valid NISAR HDF5 scenes into date-based secondary directories:
      secondaries/<YYYYMMDD>/<filename>.h5
    If a file is already inside master/ or secondaries/<YYYYMMDD>/, it is left in place.
    """
    secondaries_dir = os.path.join(project_folder, "secondaries")
    master_dir = os.path.join(project_folder, "master")
    organized = []

    for sc in scenes_meta:
        if not sc["valid"]:
            continue
        src_path = sc["filepath"]
        date_str = sc["date_yyyymmdd"]
        fname = sc["filename"]

        # Check if already organized in master or secondaries
        target_secondary_dir = os.path.join(secondaries_dir, date_str)
        target_path = os.path.join(target_secondary_dir, fname)
        master_target = os.path.join(master_dir, fname)
        master_date_target = os.path.join(master_dir, date_str, fname)

        if os.path.exists(target_path) or os.path.exists(master_target) or os.path.exists(master_date_target):
            sc["organized_path"] = target_path if os.path.exists(target_path) else (master_target if os.path.exists(master_target) else master_date_target)
            organized.append((src_path, sc["organized_path"], "already_in_place"))
            continue

        os.makedirs(target_secondary_dir, exist_ok=True)
        # Move or copy the file
        try:
            # If in the same drive or root, move it; if it's already in secondaries but unorganized, move
            shutil.move(src_path, target_path)
            sc["organized_path"] = target_path
            sc["filepath"] = target_path
            organized.append((src_path, target_path, "moved"))
        except Exception:
            try:
                shutil.copy2(src_path, target_path)
                sc["organized_path"] = target_path
                sc["filepath"] = target_path
                organized.append((src_path, target_path, "copied"))
            except Exception as e:
                if logger:
                    logger.warning(f"Failed to place {fname} into {target_secondary_dir}: {e}")

    return organized


def run_prep(config_file):
    config, cfg_path = load_config(config_file)
    project_folder = config["PROJECT_DEFINITION"].get("PROJECTFOLDER", ".")
    log_folder = os.path.join(project_folder, "logs")
    os.makedirs(log_folder, exist_ok=True)
    log_file = os.path.join(log_folder, "nisar_step_0_prep.log")

    logger = FriendlyLogger(log_file=log_file) if FriendlyLogger else FallbackLogger()

    logger.banner("NISAR DATA PREPARATION & VALIDATION (STEP 0)")
    logger.step_header(
        0, 3,
        "Data Preparation and RSLC HDF5 Validation",
        "Scans project folder for NISAR .h5 RSLC products, validates HDF5 hierarchy (/science/LSAR/RSLC/...), extracts orbit state vectors and timestamps, and creates master/secondaries folder structure.",
        typical_time="1-2 minutes"
    )

    logger.info(f"Loaded configuration from: {cfg_path}")
    logger.info(f"Project Workspace Directory: {project_folder}")

    frequency = config["PROC_PARAMETERS"].get("FREQUENCY", "L")
    polarisation = config["PROC_PARAMETERS"].get("POLARISATION", "HH")

    # 1. Setup project directories
    logger.subtask("Setting up project directories (master, secondaries, subsets, coreg, ifg, stamps_export)...", status="working")
    setup_project_directories(project_folder)
    logger.subtask("Project directories successfully initialized.", status="done")

    # 2. Search for NISAR .h5 files
    logger.info(f"Scanning for NISAR RSLC .h5 files (Frequency: {frequency}-Band, Polarisation: {polarisation})...")
    search_dirs = [
        project_folder,
        os.path.join(project_folder, "raw"),
        os.path.join(project_folder, "download"),
        os.path.join(project_folder, "secondaries"),
        os.path.join(project_folder, "master")
    ]
    h5_candidates = []
    seen = set()
    for sdir in search_dirs:
        if os.path.exists(sdir):
            for pattern in ["*.h5", "*.he5", "*/*.h5"]:
                for f in glob.glob(os.path.join(sdir, pattern)):
                    norm = os.path.normpath(f)
                    if norm not in seen and os.path.isfile(norm):
                        seen.add(norm)
                        h5_candidates.append(norm)

    if not h5_candidates:
        logger.warning(f"No .h5 files found in {project_folder} or standard subfolders (raw, download, secondaries).")
        logger.info("If you have not downloaded NISAR data yet, please run asf_nisar_downloader.py or bhoonidhi_nisar_downloader.py first.")
        # Return success with empty manifest to allow pipeline chaining
        manifest_path = os.path.join(project_folder, "nisar_scenes_metadata.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
        logger.step_footer(0, "Data Preparation")
        return 0

    logger.info(f"Discovered {len(h5_candidates)} candidate HDF5 file(s). Validating structure...")

    # 3. Validate and extract metadata
    validated_scenes = []
    invalid_scenes = []

    for idx, fpath in enumerate(h5_candidates, 1):
        logger.progress(idx, len(h5_candidates), os.path.basename(fpath))
        meta = validate_and_inspect_nisar_h5(fpath, frequency=frequency, polarisation=polarisation)
        if meta["valid"]:
            validated_scenes.append(meta)
            logger.subtask(
                f"Valid NISAR RSLC: {meta['filename']} | Date: {meta['date_yyyymmdd']} | "
                f"Track: {meta['track']} | Orbits: {meta['num_orbit_vectors']} points",
                status="done"
            )
        else:
            invalid_scenes.append(meta)
            err_msg = "; ".join(meta["errors"])
            logger.subtask(f"Skipping {meta['filename']}: {err_msg}", status="warning")

    # 4. Organize files into secondaries/<YYYYMMDD>
    if validated_scenes:
        logger.info(f"Organizing {len(validated_scenes)} validated scene(s) into secondaries/<YYYYMMDD> folders...")
        organize_nisar_files(project_folder, validated_scenes, logger=logger)

    # 5. Save scenes manifest
    manifest_path = os.path.join(project_folder, "nisar_scenes_metadata.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(validated_scenes, f, indent=2)
    logger.subtask(f"Saved NISAR scene manifest to: {manifest_path}", status="done")

    # 6. Print summary table
    logger.info("\n" + "=" * 78)
    logger.info(f"{'DATE':<10} | {'TRACK':<6} | {'FRAME':<6} | {'PASS':<10} | {'POL':<5} | {'ORBITS':<7} | {'FILENAME'}")
    logger.info("-" * 78)
    for sc in validated_scenes:
        fname = sc["filename"]
        if len(fname) > 28:
            fname = fname[:12] + "..." + fname[-13:]
        logger.info(
            f"{sc['date_yyyymmdd']:<10} | {str(sc['track']):<6} | {str(sc['frame']):<6} | "
            f"{sc['orbit_pass'][:10]:<10} | {str(sc['pol_found']):<5} | {sc['num_orbit_vectors']:<7} | {fname}"
        )
    logger.info("=" * 78 + "\n")

    logger.success(f"Successfully processed {len(validated_scenes)} valid NISAR RSLC scene(s)!")
    logger.step_footer(0, "Data Preparation and RSLC HDF5 Validation")
    return 0


def main():
    args = parse_arguments()
    try:
        sys.exit(run_prep(args.ProjFile))
    except Exception as exc:
        print(f"\n❌ Execution Error in nisar_step_0_prep: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

