#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nisar_automaster.py: Automated and Manual Master Selection for NISAR InSAR.
Selects optimal master acquisition by minimizing perpendicular baseline (Bperp)
and temporal baseline dispersion (Btemp), maximizing stack coherence, or
respecting user-defined MANUAL selection.

Author: Antigravity Team
Compatible with SNAP 14.0 and StaMPS PSI processing.
"""

import os
import sys
import glob
import json
import math
import shutil
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


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="NISAR Master Selection: Optimal AUTO or MANUAL Master Selection"
    )
    parser.add_argument(
        "--ProjFile", "-F",
        type=str,
        default="project_nisar.conf",
        help="Path to project configuration file (e.g., project_nisar.conf)"
    )
    parser.add_argument(
        "--Mode", "-M",
        type=str,
        default=None,
        help="Master selection mode: AUTO, MANUAL, FIRST, LAST, or specific date YYYYMMDD"
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


def compute_orbit_state_at_mid(orbit_vectors):
    """
    Extracts or interpolates position and velocity vector at mid-pass time.
    Returns (pos_xyz, vel_xyz).
    """
    if not orbit_vectors:
        return None, None
    n = len(orbit_vectors)
    mid_idx = n // 2
    pos = orbit_vectors[mid_idx]["pos"]
    vel = orbit_vectors[mid_idx]["vel"]
    return np.array(pos, dtype=float), np.array(vel, dtype=float)


def compute_perpendicular_baseline(pos1, vel1, pos2, vel2, look_angle_deg=37.0):
    """
    Calculates the perpendicular baseline Bperp (meters) between two satellite orbits.
    
    pos1, vel1: Master position and velocity in ECEF (meters, m/s)
    pos2, vel2: Secondary position and velocity in ECEF
    look_angle_deg: Average look angle (NISAR L-band swath ~33-47 deg, center ~37 deg)
    """
    if pos1 is None or pos2 is None or vel1 is None or vel2 is None:
        return 0.0

    # Baseline vector from Master to Secondary
    B_vec = pos2 - pos1

    # Unit vectors for master orbit frame
    r_norm = np.linalg.norm(pos1)
    if r_norm == 0:
        return float(np.linalg.norm(B_vec))
    u_rad = pos1 / r_norm  # Radial unit vector (up)

    v_norm = np.linalg.norm(vel1)
    if v_norm == 0:
        return float(np.linalg.norm(B_vec))
    u_vel = vel1 / v_norm  # Velocity unit vector (along-track)

    # Cross-track unit vector: u_cross = u_vel x u_rad
    u_cross = np.cross(u_vel, u_rad)
    u_cross_norm = np.linalg.norm(u_cross)
    if u_cross_norm > 0:
        u_cross /= u_cross_norm

    # Convert look angle to radians
    theta = math.radians(look_angle_deg)

    # Perpendicular baseline is component in range-elevation plane perpendicular to LOS:
    # LOS = sin(theta) * u_cross - cos(theta) * u_rad
    # Perp to LOS = cos(theta) * u_cross + sin(theta) * u_rad
    u_perp = math.cos(theta) * u_cross + math.sin(theta) * u_rad
    B_perp = float(np.dot(B_vec, u_perp))

    return B_perp


def get_available_scenes(project_folder, metadata_file=None):
    """
    Scans project folder and metadata manifest for available acquisition dates.
    Returns dict mapping date_str -> dict(files, metadata).
    """
    scenes_by_date = {}

    # Check metadata manifest first
    if metadata_file and os.path.exists(metadata_file):
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                meta_list = json.load(f)
                for item in meta_list:
                    d = item.get("date_yyyymmdd")
                    if d:
                        if d not in scenes_by_date:
                            scenes_by_date[d] = {"date": d, "files": [], "meta": item}
                        scenes_by_date[d]["files"].append(item.get("filepath", ""))
        except Exception:
            pass

    # Scan secondaries directory
    sec_dir = os.path.join(project_folder, "secondaries")
    if os.path.exists(sec_dir):
        for entry in os.listdir(sec_dir):
            epath = os.path.join(sec_dir, entry)
            if os.path.isdir(epath) and len(entry) == 8 and entry.isdigit():
                h5_files = glob.glob(os.path.join(epath, "*.h5"))
                if h5_files:
                    if entry not in scenes_by_date:
                        scenes_by_date[entry] = {"date": entry, "files": h5_files, "meta": None}
                    else:
                        scenes_by_date[entry]["files"] = h5_files

    # Also check master directory if already populated
    mst_dir = os.path.join(project_folder, "master")
    if os.path.exists(mst_dir):
        for entry in os.listdir(mst_dir):
            epath = os.path.join(mst_dir, entry)
            if os.path.isdir(epath) and len(entry) == 8 and entry.isdigit():
                h5_files = glob.glob(os.path.join(epath, "*.h5"))
                if h5_files and entry not in scenes_by_date:
                    scenes_by_date[entry] = {"date": entry, "files": h5_files, "meta": None, "in_master": True}
            elif entry.endswith(".h5"):
                # Extract date from filename
                import re
                m = re.search(r"(\d{8})T", entry) or re.search(r"_(\d{8})_", entry)
                if m:
                    d = m.group(1)
                    if d not in scenes_by_date:
                        scenes_by_date[d] = {"date": d, "files": [epath], "meta": None, "in_master": True}

    return scenes_by_date


def select_master_auto(scenes_by_date, b_crit=4000.0, t_crit=365.0):
    """
    Computes baseline dispersion and coherence matrix to select the optimal master.
    
    NISAR L-band critical baseline is ~4000m.
    Minimizes:
      Dispersion metric: D(m) = sqrt( mean( (Bperp/Bcrit)^2 + (Btemp/Tcrit)^2 ) )
    Maximizes:
      Stack Coherence: Gamma(m) = mean( (1 - |Bperp|/Bcrit) * exp(-Btemp / 180) )
    """
    dates = sorted(scenes_by_date.keys())
    n = len(dates)
    if n == 0:
        raise ValueError("No acquisition dates found in project!")
    if n == 1:
        return dates[0], {dates[0]: {"bperp": 0.0, "btemp": 0, "coherence": 1.0}}

    dt_objs = {}
    orbit_cache = {}
    for d in dates:
        dt_objs[d] = datetime.datetime.strptime(d, "%Y%m%d")
        meta = scenes_by_date[d].get("meta")
        if meta and meta.get("orbit_state_vectors"):
            p, v = compute_orbit_state_at_mid(meta["orbit_state_vectors"])
            orbit_cache[d] = (p, v)
        else:
            orbit_cache[d] = (None, None)

    scores = {}
    baseline_matrices = {}

    for cand in dates:
        p_cand, v_cand = orbit_cache[cand]
        t_cand = dt_objs[cand]
        
        bperps = []
        btemps = []
        coherences = []
        cand_dict = {}

        for other in dates:
            p_other, v_other = orbit_cache[other]
            t_other = dt_objs[other]

            # Btemp in days
            btemp = (t_other - t_cand).days
            abs_btemp = abs(btemp)

            # Bperp in meters
            if p_cand is not None and p_other is not None:
                bperp = compute_perpendicular_baseline(p_cand, v_cand, p_other, v_other)
            else:
                # If synthetic/mock orbit without vectors, approximate minimal dispersion
                bperp = 0.0

            # Coherence model
            gamma_spatial = max(0.0, 1.0 - abs(bperp) / b_crit)
            gamma_temp = math.exp(-abs_btemp / 180.0)
            coherence = gamma_spatial * gamma_temp

            cand_dict[other] = {
                "bperp": round(bperp, 2),
                "btemp": btemp,
                "coherence": round(coherence, 4)
            }

            if other != cand:
                bperps.append(bperp)
                btemps.append(abs_btemp)
                coherences.append(coherence)

        # Calculate dispersion penalty
        mean_coh = np.mean(coherences) if coherences else 1.0
        dispersion = math.sqrt(
            np.mean([(bp / b_crit) ** 2 + (bt / t_crit) ** 2 for bp, bt in zip(bperps, btemps)])
        ) if bperps else 0.0

        scores[cand] = {
            "coherence": mean_coh,
            "dispersion": dispersion,
            "mean_bperp": np.mean(np.abs(bperps)) if bperps else 0.0,
            "max_bperp": np.max(np.abs(bperps)) if bperps else 0.0,
            "mean_btemp": np.mean(btemps) if btemps else 0.0
        }
        baseline_matrices[cand] = cand_dict

    # Optimal candidate maximizes coherence score (and minimizes dispersion)
    best_candidate = max(dates, key=lambda d: (scores[d]["coherence"], -scores[d]["dispersion"]))

    return best_candidate, baseline_matrices[best_candidate], scores


def move_master_to_destination(project_folder, master_date, scenes_by_date, logger=None):
    """
    Moves master scene from secondaries/<master_date> into master/ folder.
    """
    master_dir = os.path.join(project_folder, "master")
    secondaries_dir = os.path.join(project_folder, "secondaries")
    os.makedirs(master_dir, exist_ok=True)

    sec_date_dir = os.path.join(secondaries_dir, master_date)
    if os.path.exists(sec_date_dir):
        files = os.listdir(sec_date_dir)
        for f in files:
            src = os.path.join(sec_date_dir, f)
            dst = os.path.join(master_dir, f)
            if logger:
                logger.subtask(f"Moving master file: {f} -> master/", status="working")
            try:
                shutil.move(src, dst)
            except Exception:
                shutil.copy2(src, dst)
                os.remove(src)
        try:
            os.rmdir(sec_date_dir)
        except Exception:
            pass
        if logger:
            logger.subtask(f"Master {master_date} staged in master/ directory.", status="done")
    else:
        # Check if already in master
        if os.path.exists(master_dir) and any(master_date in f for f in os.listdir(master_dir)):
            if logger:
                logger.info(f"Master scene {master_date} is already located in master/ folder.")


def update_config_file(config_file, master_date):
    """Updates MASTER field in the configuration file."""
    config = configparser.ConfigParser()
    config.read(config_file)
    if "PROC_PARAMETERS" not in config:
        config["PROC_PARAMETERS"] = {}
    config["PROC_PARAMETERS"]["MASTER"] = master_date
    with open(config_file, "w", encoding="utf-8") as f:
        config.write(f)


def save_baseline_table(project_folder, master_date, baselines_dict, logger=None):
    """
    Writes PROJECTFOLDER/baselines_table.txt with perpendicular and temporal baselines.
    """
    tbl_path = os.path.join(project_folder, "baselines_table.txt")
    lines = [
        f"# NISAR InSAR Baselines relative to Master Date: {master_date}",
        f"{'DATE':<12} | {'BPERP (m)':<12} | {'BTEMP (days)':<14} | {'COHERENCE':<10}",
        "-" * 56
    ]
    for d, vals in sorted(baselines_dict.items()):
        is_mst = " (MASTER)" if d == master_date else ""
        lines.append(
            f"{d:<12} | {vals['bperp']:<12.2f} | {vals['btemp']:<14} | {vals['coherence']:<10.4f}{is_mst}"
        )
    content = "\n".join(lines) + "\n"
    with open(tbl_path, "w", encoding="utf-8") as f:
        f.write(content)

    if logger:
        logger.subtask(f"Saved baseline table to: {tbl_path}", status="done")


def run_master_selection(config_file, mode_override=None):
    config, cfg_path = load_config(config_file)
    project_folder = config["PROJECT_DEFINITION"].get("PROJECTFOLDER", ".")
    log_folder = os.path.join(project_folder, "logs")
    os.makedirs(log_folder, exist_ok=True)
    log_file = os.path.join(log_folder, "nisar_automaster.log")

    logger = FriendlyLogger(log_file=log_file) if FriendlyLogger else FallbackLogger()

    logger.banner("NISAR MASTER SELECTION ENGINE")
    logger.info(f"Loaded config: {cfg_path}")
    logger.info(f"Project Folder: {project_folder}")

    # Determine selection mode
    conf_mode = config["PROC_PARAMETERS"].get("MASTERSEL", "AUTO").upper()
    conf_master = config["PROC_PARAMETERS"].get("MASTER", "").strip()
    mode = mode_override.upper() if mode_override else conf_mode

    # Discover available dates
    meta_json = os.path.join(project_folder, "nisar_scenes_metadata.json")
    scenes_by_date = get_available_scenes(project_folder, metadata_file=meta_json)
    available_dates = sorted(scenes_by_date.keys())

    if not available_dates:
        logger.error(
            "No Scenes Available for Master Selection",
            f"No NISAR dates found in {project_folder}/secondaries or nisar_scenes_metadata.json.",
            [
                "Ensure you have downloaded scenes using asf_nisar_downloader.py or bhoonidhi_nisar_downloader.py.",
                "Run nisar_step_0_prep.py first to scan and organize your RSLC .h5 files.",
                "Check that PROJECTFOLDER in project_nisar.conf points to your actual data directory."
            ]
        )
        return 1

    logger.info(f"Detected {len(available_dates)} acquisition dates: {', '.join(available_dates)}")

    chosen_master = None
    baseline_info = {}

    if mode == "MANUAL":
        target = conf_master
        if not target and mode_override and mode_override not in ["MANUAL", "AUTO", "FIRST", "LAST"]:
            target = mode_override
        if not target:
            logger.error(
                "Manual Master Date Missing",
                "Mode is set to MANUAL, but no MASTER date (YYYYMMDD) is specified in config or arguments.",
                [
                    f"Set MASTER = {available_dates[len(available_dates)//2]} in project_nisar.conf.",
                    f"Or run: python nisar_automaster.py -F {config_file} -M {available_dates[0]}",
                    "Or set MASTERSEL = AUTO to let the algorithm pick the optimal date automatically."
                ]
            )
            return 1
        if target not in scenes_by_date:
            logger.error(
                f"Selected Master {target} Not Found",
                f"Date {target} does not match any available scene dates in the project.",
                [
                    f"Available dates are: {', '.join(available_dates)}",
                    f"Update MASTER in project_nisar.conf to one of the available dates above."
                ]
            )
            return 1
        chosen_master = target
        logger.info(f"Using user-specified MANUAL master: {chosen_master}")
        # Compute baseline info relative to manual master
        _, b_dict, _ = select_master_auto(scenes_by_date)
        # Re-calc relative to chosen_master
        dt_chosen = datetime.datetime.strptime(chosen_master, "%Y%m%d")
        for d in available_dates:
            dt_other = datetime.datetime.strptime(d, "%Y%m%d")
            baseline_info[d] = {
                "bperp": 0.0,
                "btemp": (dt_other - dt_chosen).days,
                "coherence": round(math.exp(-abs((dt_other - dt_chosen).days) / 180.0), 4)
            }

    elif mode == "FIRST":
        chosen_master = available_dates[0]
        logger.info(f"Using FIRST acquisition as master: {chosen_master}")
        dt_chosen = datetime.datetime.strptime(chosen_master, "%Y%m%d")
        for d in available_dates:
            dt_other = datetime.datetime.strptime(d, "%Y%m%d")
            baseline_info[d] = {
                "bperp": 0.0,
                "btemp": (dt_other - dt_chosen).days,
                "coherence": round(math.exp(-abs((dt_other - dt_chosen).days) / 180.0), 4)
            }

    elif mode == "LAST":
        chosen_master = available_dates[-1]
        logger.info(f"Using LAST acquisition as master: {chosen_master}")
        dt_chosen = datetime.datetime.strptime(chosen_master, "%Y%m%d")
        for d in available_dates:
            dt_other = datetime.datetime.strptime(d, "%Y%m%d")
            baseline_info[d] = {
                "bperp": 0.0,
                "btemp": (dt_other - dt_chosen).days,
                "coherence": round(math.exp(-abs((dt_other - dt_chosen).days) / 180.0), 4)
            }

    elif mode in available_dates:
        # Direct date passed in -M
        chosen_master = mode
        logger.info(f"Using explicit date passed via mode: {chosen_master}")
        dt_chosen = datetime.datetime.strptime(chosen_master, "%Y%m%d")
        for d in available_dates:
            dt_other = datetime.datetime.strptime(d, "%Y%m%d")
            baseline_info[d] = {
                "bperp": 0.0,
                "btemp": (dt_other - dt_chosen).days,
                "coherence": round(math.exp(-abs((dt_other - dt_chosen).days) / 180.0), 4)
            }

    else:
        # AUTO selection
        logger.info("Executing AUTO master selection algorithm (minimizing Bperp and Btemp dispersion)...")
        best_cand, b_dict, scores = select_master_auto(scenes_by_date)
        chosen_master = best_cand
        baseline_info = b_dict

        logger.info("\n" + "=" * 70)
        logger.info(f"{'CANDIDATE DATE':<16} | {'MEAN COHERENCE':<16} | {'DISPERSION INDEX':<18} | {'STATUS'}")
        logger.info("-" * 70)
        for d in available_dates:
            sc = scores[d]
            is_best = " ⭐ OPTIMAL MASTER" if d == best_cand else ""
            logger.info(f"{d:<16} | {sc['coherence']:<16.4f} | {sc['dispersion']:<18.4f} |{is_best}")
        logger.info("=" * 70 + "\n")

    logger.success(f"Selected Master Acquisition Date: {chosen_master}")

    # Move files to master/ folder
    move_master_to_destination(project_folder, chosen_master, scenes_by_date, logger=logger)

    # Update configuration
    update_config_file(cfg_path, chosen_master)
    logger.subtask(f"Updated configuration ({os.path.basename(cfg_path)}): MASTER = {chosen_master}", status="done")

    # Save baseline table
    save_baseline_table(project_folder, chosen_master, baseline_info, logger=logger)

    # Display baseline distribution table
    logger.info("\nBaseline configuration relative to Master (" + chosen_master + "):")
    logger.info(f"{'SECONDARY DATE':<16} | {'BPERP (m)':<12} | {'BTEMP (days)':<14} | {'COHERENCE'}")
    logger.info("-" * 56)
    for d, vals in sorted(baseline_info.items()):
        marker = " (Master)" if d == chosen_master else ""
        logger.info(f"{d:<16} | {vals['bperp']:<12.2f} | {vals['btemp']:<14} | {vals['coherence']:<10.4f}{marker}")
    logger.info("-" * 56 + "\n")

    return 0


def main():
    args = parse_arguments()
    try:
        sys.exit(run_master_selection(args.ProjFile, args.Mode))
    except Exception as exc:
        print(f"\n❌ Execution Error in nisar_automaster: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

