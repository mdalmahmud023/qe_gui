from __future__ import annotations

from pathlib import Path

QE_RUNS_DIR = Path("qe_runs")
QE_RUN_COMMAND = ("pw.x", "-in", "pw.in")
QE_OUTPUT_FILENAME = "pw.out"
DOS_COMMAND = ("dos.x", "-i", "dos.in")
BANDS_PW_COMMAND = ("pw.x", "-in", "bands_pw.in")
BANDS_PP_COMMAND = ("bands.x", "-i", "bands_pp.in")

DOS_INPUT_FILENAME = "dos.in"
DOS_OUTPUT_FILENAME = "dos.out"
BANDS_PW_INPUT_FILENAME = "bands_pw.in"
BANDS_PW_OUTPUT_FILENAME = "bands_pw.out"
BANDS_PP_INPUT_FILENAME = "bands_pp.in"
BANDS_PP_OUTPUT_FILENAME = "bands_pp.out"
FILBAND_FILENAME = "filband"
FILBAND_GNU_FILENAME = "filband.gnu"

JOB_SUMMARIES_STATE_KEY = "qe_job_summaries"
JOB_OWNER_PW = "pw"
JOB_OWNER_DOS = "dos"
JOB_OWNER_BANDS = "bands"

DOS_BZ_SUM_OPTIONS = ("smearing", "tetrahedra", "tetrahedra_lin", "tetrahedra_opt")
BAND_COORDINATE_MODE_OPTIONS = ("crystal_b", "tpiba_b")
BAND_ALIGNMENT_MODE_OPTIONS = ("vbm", "fermi", "raw")
DEFAULT_BAND_ALIGNMENT_MODE = "vbm"
DEFAULT_BAND_PATH_TEXT = "0.0 0.0 0.0 20 ! G\n0.5 0.0 0.0 20 ! X\n0.5 0.5 0.5 1 ! L\n"

QE_STUDIO_PROVENANCE_FILENAME = "qe_studio_provenance.json"
QE_STUDIO_PROVENANCE_VERSION = 1
QE_INPUT_METADATA_FILENAME = "qe_input_metadata.json"
PROVENANCE_RECORD_INPUT = "pw_input"
PROVENANCE_RECORD_BASE_RUN = "pw_run"
PROVENANCE_RECORD_DOS = "dos_workflow"
PROVENANCE_RECORD_BANDS = "bands_workflow"
