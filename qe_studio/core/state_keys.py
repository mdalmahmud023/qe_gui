from __future__ import annotations

QE_PARAMS_STATE_KEY = "qe_parameters"

ATOMS_STATE_KEY = "structure_atoms"
RAW_ATOMS_STATE_KEY = "structure_raw_atoms"
STRUCTURE_FILE_BYTES_STATE_KEY = "structure_file_bytes"
FILENAME_STATE_KEY = "structure_filename"
SOURCE_KIND_STATE_KEY = "structure_source_kind"
SOURCE_LABEL_STATE_KEY = "structure_source_label"
SOURCE_FORMAT_STATE_KEY = "structure_source_format"
SOURCE_DETAILS_STATE_KEY = "structure_source_details"
RAW_STRUCTURE_SIGNATURE_STATE_KEY = "structure_raw_signature"
ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY = "structure_active_signature"
ERROR_STATE_KEY = "structure_error"
STRUCTURE_MODE_STATE_KEY = "structure_mode"
STRUCTURE_MODE_STATUS_STATE_KEY = "structure_mode_status"
QE_INPUT_TEXT_STATE_KEY = "qe_input_text"
QE_INPUT_INTERNAL_PATH_STATE_KEY = "qe_input_internal_path"
QE_INPUT_UI_PATH_STATE_KEY = "qe_input_ui_path"
PSEUDO_SETTINGS_STATE_KEY = "pseudo_settings"
PSEUDO_DIR_STATE_KEY = "pseudo_dir"
PSEUDO_RESOLVED_MAP_STATE_KEY = "pseudo_resolved_map"
PSEUDO_RESOLVED_ROWS_STATE_KEY = "pseudo_resolved_rows"
PSEUDO_RESOLUTION_STATUS_STATE_KEY = "pseudo_resolution_status"
PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY = "pseudo_structure_signature"

PSEUDO_FUNCTIONAL_WIDGET_KEY = "pseudo_widget_functional"
PSEUDO_MODE_WIDGET_KEY = "pseudo_widget_mode"
PSEUDO_VERSION_WIDGET_KEY = "pseudo_widget_version"
PSEUDO_USE_CACHE_WIDGET_KEY = "pseudo_widget_use_cache"
PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY = "pseudo_widget_fully_relativistic"
BAND_NBND_WIDGET_KEY = "band_nbnd_widget"
BAND_COORDINATE_MODE_WIDGET_KEY = "band_coordinate_mode_widget"
BAND_PATH_WIDGET_KEY = "band_path_widget"
AUTO_BAND_PATH_POINTS_WIDGET_KEY = "auto_band_path_points_widget"
AUTO_BAND_PATH_STATUS_STATE_KEY = "auto_band_path_status"
BAND_ALIGNMENT_MODE_WIDGET_KEY = "band_alignment_mode_widget"
DOS_ALIGNMENT_WIDGET_KEY = "dos_align_to_fermi"

PSEUDO_FUNCTIONAL_OPTIONS = ("PBE", "PBEsol")
PSEUDO_MODE_OPTIONS = ("efficiency", "precision")
PSEUDO_VERSION_OPTIONS = ("1.3.0",)
DEFAULT_PSEUDO_SETTINGS = {
    "functional": "PBE",
    "mode": "efficiency",
    "version": "1.3.0",
    "fully_relativistic": False,
}
STRUCTURE_MODE_OPTIONS = ("source", "primitive")

QE_CALCULATION_OPTIONS = ("scf", "nscf", "relax", "vc-relax")
QE_OCCUPATIONS_OPTIONS = ("smearing", "fixed", "tetrahedra")
QE_SMEARING_OPTIONS = ("gaussian", "methfessel-paxton", "marzari-vanderbilt")
