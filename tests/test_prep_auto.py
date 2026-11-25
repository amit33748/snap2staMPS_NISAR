#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_prep_auto.py: Comprehensive test suite for NISAR prep & automaster tools:
- nisar_step_0_prep.py
- nisar_automaster.py
- nisar_step_1_subset.py
- mt_prep_nisar.py
"""

import os
import sys
import glob
import shutil
import tempfile
import unittest
import datetime
import numpy as np
import h5py
import configparser

# Add bin directory to path
BIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
if BIN_DIR not in sys.path:
    sys.path.insert(0, BIN_DIR)

from nisar_step_0_prep import validate_and_inspect_nisar_h5, setup_project_directories, run_prep
from nisar_automaster import select_master_auto, run_master_selection, compute_perpendicular_baseline
from nisar_step_1_subset import get_aoi_geometry, pure_python_subset_nisar_h5, run_subset
from mt_prep_nisar import parse_rsc_par, calibrate_amplitudes, generate_patches, extract_candidates_pure_python, run_mt_prep


class TestNisarPrepAuto(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nisar_test_")
        self.project_dir = os.path.join(self.test_dir, "project")
        os.makedirs(self.project_dir, exist_ok=True)
        self.raw_dir = os.path.join(self.project_dir, "raw")
        os.makedirs(self.raw_dir, exist_ok=True)

        # Create mock NISAR HDF5 files for 3 acquisition dates
        self.dates = ["20240501", "20240513", "20240525"]
        self.h5_files = []
        for idx, d in enumerate(self.dates):
            fn = os.path.join(self.raw_dir, f"NISAR_L1_PR_RSLC_001_045_A_172_2000_HH_{d}T013000_20240501T013030_D00100_M_F_J_001.h5")
            self._create_mock_nisar_h5(fn, d, orbit_offset=idx * 150.0)
            self.h5_files.append(fn)

        # Create mock project_nisar.conf
        self.conf_path = os.path.join(self.project_dir, "project_nisar.conf")
        self._create_mock_conf(self.conf_path, self.project_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_mock_nisar_h5(self, filepath, date_str, orbit_offset=0.0):
        dt = datetime.datetime.strptime(date_str, "%Y%m%d")
        t_start = dt.strftime("%Y-%m-%dT01:30:00.000000Z")
        t_stop = dt.strftime("%Y-%m-%dT01:30:30.000000Z")

        with h5py.File(filepath, "w") as h5:
            # Identification
            ident = h5.create_group("science/LSAR/identification")
            ident.create_dataset("missionId", data=np.bytes_("NISAR"))
            ident.create_dataset("radarBand", data=np.bytes_("L"))
            ident.create_dataset("lookDirection", data=np.bytes_("Right"))
            ident.create_dataset("orbitPassDirection", data=np.bytes_("Ascending"))
            ident.create_dataset("trackNumber", data=np.array([172], dtype=np.int32))
            ident.create_dataset("frameNumber", data=np.array([45], dtype=np.int32))
            ident.create_dataset("zeroDopplerStartTime", data=np.bytes_(t_start))
            ident.create_dataset("zeroDopplerEndTime", data=np.bytes_(t_stop))

            # Orbit
            orbit = h5.create_group("science/LSAR/RSLC/metadata/orbit")
            n_pts = 5
            times = np.linspace(0, 30, n_pts)
            # Base orbit in ECEF
            pos = np.zeros((n_pts, 3), dtype=np.float64)
            pos[:, 0] = 5000000.0 + orbit_offset
            pos[:, 1] = 2000000.0 + orbit_offset * 0.5
            pos[:, 2] = 4000000.0 + np.linspace(0, 210000, n_pts)
            vel = np.zeros((n_pts, 3), dtype=np.float64)
            vel[:, 2] = 7500.0

            orbit.create_dataset("time", data=times)
            orbit.create_dataset("position", data=pos)
            orbit.create_dataset("velocity", data=vel)
            orbit.create_dataset("referenceEpoch", data=np.bytes_(t_start))

            # Swath
            freqA = h5.create_group("science/LSAR/RSLC/swaths/frequencyA")
            # 1200 azimuth lines, 1500 range samples
            data = np.ones((1200, 1500), dtype=np.complex64)
            freqA.create_dataset("HH", data=data)
            freqA.create_dataset("slantRange", data=np.linspace(800000, 950000, 1500))
            freqA.create_dataset("zeroDopplerTime", data=np.linspace(0, 30, 1200))

    def _create_mock_conf(self, conf_path, proj_dir):
        config = configparser.ConfigParser()
        config["PROJECT_DEFINITION"] = {
            "PROJECTFOLDER": proj_dir.replace("\\", "/"),
            "GRAPHSFOLDER": os.path.join(proj_dir, "graphs").replace("\\", "/")
        }
        config["PROC_OPTIONS"] = {
            "OVERWRITE": "N",
            "SMARTHDD": "Y",
            "PLOTTING": "N"
        }
        config["PROC_PARAMETERS"] = {
            "SENSOR": "NISAR",
            "FREQUENCY": "L",
            "POLARISATION": "HH",
            "MASTER": "",
            "MASTERSEL": "AUTO",
            "DEM_NAME": "Copernicus 30m Global DEM"
        }
        config["AOI_DEFINITION"] = {
            "AOI_MODE": "BBOX",
            "LONMIN": "77.10",
            "LATMIN": "28.50",
            "LONMAX": "77.40",
            "LATMAX": "28.80"
        }
        config["COMPUTING_RESOURCES"] = {
            "CPU": "4",
            "CACHE": "8G"
        }
        config["SNAP_GPT"] = {
            "SNAP_INSTALLATION_FOLDER": "C:/Program Files/esa-snap"
        }
        with open(conf_path, "w", encoding="utf-8") as f:
            config.write(f)

    def test_step_0_prep(self):
        """Test step 0: scanning, HDF5 validation, orbit extraction, directory setup."""
        ret = run_prep(self.conf_path)
        self.assertEqual(ret, 0)

        # Check directories created
        for sd in ["master", "secondaries", "subsets", "coreg", "ifg", "stamps_export", "logs"]:
            self.assertTrue(os.path.isdir(os.path.join(self.project_dir, sd)))

        # Check metadata manifest
        manifest_path = os.path.join(self.project_dir, "nisar_scenes_metadata.json")
        self.assertTrue(os.path.exists(manifest_path))

        # Check files were organized into secondaries/<date>/
        for d in self.dates:
            sec_date = os.path.join(self.project_dir, "secondaries", d)
            self.assertTrue(os.path.isdir(sec_date))
            h5s = glob.glob(os.path.join(sec_date, "*.h5"))
            self.assertEqual(len(h5s), 1)

    def test_automaster_auto_and_manual(self):
        """Test automaster in AUTO mode (minimizing dispersion) and MANUAL mode."""
        # Run step 0 first to organize
        run_prep(self.conf_path)

        # Run AUTO master selection
        ret = run_master_selection(self.conf_path, mode_override="AUTO")
        self.assertEqual(ret, 0)

        # Verify config updated
        cfg = configparser.ConfigParser()
        cfg.read(self.conf_path)
        master_date = cfg["PROC_PARAMETERS"].get("MASTER")
        self.assertIn(master_date, self.dates)
        # Optimal master for 3 dates with linear offsets is the middle date (20240513)
        self.assertEqual(master_date, "20240513")

        # Verify master file moved to master/ folder
        master_dir = os.path.join(self.project_dir, "master")
        master_files = glob.glob(os.path.join(master_dir, "*.h5"))
        self.assertEqual(len(master_files), 1)
        self.assertIn("20240513", master_files[0])

        # Verify baseline table
        tbl_path = os.path.join(self.project_dir, "baselines_table.txt")
        self.assertTrue(os.path.exists(tbl_path))
        with open(tbl_path, "r") as f:
            tbl_text = f.read()
        self.assertIn("20240513", tbl_text)
        self.assertIn("MASTER", tbl_text)

    def test_step_1_subset(self):
        """Test step 1 subsetting AOI parsing and pure-Python HDF5 crop."""
        run_prep(self.conf_path)
        run_master_selection(self.conf_path, mode_override="AUTO")

        ret = run_subset(self.conf_path)
        self.assertEqual(ret, 0)

        # Verify subsets created
        sub_master_dir = os.path.join(self.project_dir, "subsets", "master")
        sub_master_files = glob.glob(os.path.join(sub_master_dir, "*.*"))
        self.assertTrue(len(sub_master_files) > 0)

    def test_mt_prep_nisar(self):
        """Test mt_prep_nisar pure-Python candidate extraction and patch generation."""
        # Create synthetic StaMPS export structure
        stamps_dir = os.path.join(self.project_dir, "stamps_export")
        rslc_dir = os.path.join(stamps_dir, "rslc")
        diff_dir = os.path.join(stamps_dir, "diff0")
        geo_dir = os.path.join(stamps_dir, "geo")
        dem_dir = os.path.join(stamps_dir, "dem")
        for d in [rslc_dir, diff_dir, geo_dir, dem_dir]:
            os.makedirs(d, exist_ok=True)

        w = 100
        l = 150
        master = "20240513"
        dates = ["20240501", "20240513", "20240525"]

        # Master .rslc.par
        par_file = os.path.join(rslc_dir, f"{master}.rslc.par")
        with open(par_file, "w") as f:
            f.write(f"range_samples: {w}\nazimuth_lines: {l}\n")

        # SLC files
        for d in dates:
            slc_f = os.path.join(rslc_dir, f"{d}.rslc")
            # Create SLC with some stable PS pixels
            data = (np.ones((l, w)) * 5.0 + np.random.normal(0, 0.1, (l, w))).astype(np.complex64)
            data.tofile(slc_f)

        # Diff interferograms
        for d in ["20240501", "20240525"]:
            diff_f = os.path.join(diff_dir, f"{master}_{d}.diff")
            ddata = np.exp(1j * np.random.uniform(-np.pi, np.pi, (l, w))).astype(np.complex64)
            ddata.tofile(diff_f)

        # Geo lon/lat & dem.rdc
        lon_f = os.path.join(geo_dir, f"{master}.lon")
        lat_f = os.path.join(geo_dir, f"{master}.lat")
        dem_f = os.path.join(geo_dir, "dem.rdc")

        np.full((l, w), 77.25, dtype=np.float32).tofile(lon_f)
        np.full((l, w), 28.65, dtype=np.float32).tofile(lat_f)
        np.full((l, w), 215.0, dtype=np.float32).tofile(dem_f)

        # Run mt_prep_nisar
        cwd = os.getcwd()
        try:
            os.chdir(stamps_dir)
            prep_args = {
                "master": master,
                "datadir": stamps_dir,
                "da_thresh": 0.4,
                "rg_patches": 2,
                "az_patches": 2,
                "rg_overlap": 10,
                "az_overlap": 10,
                "maskfile": ""
            }
            res = run_mt_prep(prep_args)
            self.assertEqual(res, 0)

            # Check output files in stamps_dir
            self.assertTrue(os.path.exists("processor.txt"))
            self.assertTrue(os.path.exists("width.txt"))
            self.assertTrue(os.path.exists("len.txt"))
            self.assertTrue(os.path.exists("rsc.txt"))
            self.assertTrue(os.path.exists("calamp.out"))
            self.assertTrue(os.path.exists("selpsc.in"))
            self.assertTrue(os.path.exists("patch.list"))

            # Check patch output files
            for p in ["PATCH_1", "PATCH_2", "PATCH_3", "PATCH_4"]:
                self.assertTrue(os.path.isdir(p))
                self.assertTrue(os.path.exists(os.path.join(p, "patch.in")))
                self.assertTrue(os.path.exists(os.path.join(p, "pscands.1.ij")))
                self.assertTrue(os.path.exists(os.path.join(p, "pscands.1.ij.int")))
                self.assertTrue(os.path.exists(os.path.join(p, "pscands.1.da")))
                self.assertTrue(os.path.exists(os.path.join(p, "pscands.1.ll")))
                self.assertTrue(os.path.exists(os.path.join(p, "pscands.1.hgt")))
                self.assertTrue(os.path.exists(os.path.join(p, "pscands.1.ph")))
                self.assertTrue(os.path.exists(os.path.join(p, "mean_amp.flt")))
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
