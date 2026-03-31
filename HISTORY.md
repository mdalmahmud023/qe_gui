# QE Studio History

This file is the running project history and AI handoff context.

When future work is done, append a new dated entry and keep the "Current State" and "Next Likely Steps" sections updated.

## Project Summary

- Project name: QE Studio
- Goal: build a web-based GUI for Quantum ESPRESSO using Python, Streamlit, and ASE
- Current stage: structure visualization, SSSP pseudopotential resolution, runnable QE input generation, and first-pass electronic-properties analysis

## Current State

- Streamlit app entrypoint exists at `app.py`.
- The page uses a wide layout.
- The sidebar contains:
  - app title and description
  - `.cif` file uploader
  - structure metadata after parsing
- Uploaded CIF files are parsed with `ase.io.read(...)`.
- Parsed ASE `Atoms` objects are stored in `st.session_state`.
- The main area renders the structure with `py3Dmol`.
- The main area also includes a QE parameter form for basic calculation settings.
- The app resolves and caches SSSP pseudopotentials from versioned Materials Cloud manifests and archives.
- The form now generates runnable Quantum ESPRESSO input files through ASE once pseudopotentials are resolved.
- The app can now launch `pw.x` locally, stream live terminal output, and save the full run log to `qe_runs/pw.out`.
- The app can now detect QE analysis artifacts in `qe_runs/` and render first-pass DOS and band-structure charts from real QE post-processing files.
- Dependencies are listed in `requirements.txt`.

## File Map

- `app.py`: main Streamlit application
- `electronic_properties.py`: QE analysis discovery, parsing, and Plotly helpers
- `requirements.txt`: direct Python dependencies
- `README.md`: setup, run, and test instructions
- `HISTORY.md`: persistent project context for humans and AI

## Implemented History

### 2026-03-24

- Created the initial Streamlit scaffold in `app.py`.
- Set page layout to `wide`.
- Added sidebar branding for `QE Studio`.
- Added `.cif`-only file uploader.
- Added main-area placeholder title for structure visualization.

### 2026-03-24

- Created `requirements.txt`.
- Added direct dependencies:
  - `streamlit`
  - `ase`
  - `py3Dmol`

### 2026-03-24

- Implemented CIF parsing from uploaded file bytes using ASE.
- Added session-state storage for:
  - parsed ASE `Atoms` object
  - uploaded CIF bytes
  - uploaded filename
  - parse error message
- Added sidebar display for:
  - file name
  - chemical formula
  - atom count
  - unit cell parameters
- Added interactive 3D visualization using `py3Dmol`.
- Render path converts the parsed ASE structure back into an in-memory CIF string for viewing.

### 2026-03-24

- Performed verification steps:
  - `python3 -m py_compile app.py`
  - installed dependencies into the local `venv`
  - ASE to `py3Dmol` smoke test using an in-memory structure
  - headless Streamlit startup test with `venv/bin/streamlit run app.py`

### 2026-03-24

- Added a QE parameter form below the 3D viewer.
- Added fields for:
  - calculation type
  - `ecutwfc`
  - `ecutrho`
  - smearing type
  - `degauss`
  - k-point grid (`kx`, `ky`, `kz`)
- Added a `Generate Input File` submit button.
- Stored submitted QE parameters in `st.session_state` for the next input-generation step.

### 2026-03-25

- Wired the QE form submission to ASE's `Espresso` calculator.
- Added nested `input_data` mapping for:
  - `control.calculation`
  - `system.ecutwfc`
  - `system.ecutrho`
  - `system.smearing`
  - `system.degauss`
  - `system.occupations = "smearing"` only when smearing is enabled through `degauss > 0`
- Mapped the k-point grid to `kpts=(kx, ky, kz)`.
- Used `EspressoProfile(command="pw.x", pseudo_dir=...)` and `Espresso(directory="qe_runs", ...)`.
- Generated the internal ASE file at `qe_runs/espresso.pwi`.
- Added a cleaned UI-facing copy at `qe_runs/pw.in`.
- Displayed the generated input text in the Streamlit app with a warning that placeholder pseudopotentials are preview-only.

### 2026-03-25

- Added `pseudopotentials.py` to resolve SSSP pseudopotentials from versioned Materials Cloud JSON manifests and `.tar.gz` archives.
- Added support for SSSP `1.3.0` with:
  - `PBE + efficiency`
  - `PBE + precision`
  - `PBEsol + efficiency`
  - `PBEsol + precision`
- Added a `Pseudopotentials` section to the main UI with:
  - detected element display
  - SSSP family selectors
  - cache usage toggle
  - resolve button
  - cache-clear button
- Added local pseudopotential caching under `qe_runs/pseudopotentials/sssp/`.
- Added manifest validation, archive extraction, cache repair, and resolved-map serialization.
- Stored resolved pseudopotential settings, pseudo directory, element-to-UPF map, resolution status, and structure signature in `st.session_state`.
- Replaced placeholder pseudopotential handling with real SSSP resolution for runnable QE input generation.
- Added stale invalidation behavior for:
  - pseudopotential family setting changes
  - uploaded CIF changes with the same element set
- Added full clear invalidation when a newly uploaded CIF changes the element set.
- Added `unittest` coverage for helper functions, cache writes, and cache-repair behavior.
- Added `requests` to `requirements.txt`.

### 2026-03-30

- Added `qe_runner.py` for registry-backed Quantum ESPRESSO subprocess execution and queue-based output streaming.
- Added a `Run Calculation` section below the generated QE input preview.
- Launches Quantum ESPRESSO with `["pw.x", "-in", "pw.in"]` using `cwd="qe_runs"`.
- Streams live combined stdout/stderr into the Streamlit UI while writing the full log to `qe_runs/pw.out`.
- Stores durable run state in Streamlit session state and resolves live worker state through a module-level `RUN_REGISTRY`.
- Disables QE input regeneration while a calculation is active and resets run state when a new `pw.in` is generated.
- Added unit tests for run event handling, rolling terminal tails, registry cleanup, and runtime-loss handling.
- Expanded setup documentation to include local Quantum ESPRESSO installation and `pw.x` verification steps.

### 2026-03-31

- Added `electronic_properties.py` for QE artifact discovery, XML-first Fermi metadata extraction, DOS parsing, `filband.gnu` parsing, and Plotly figure creation.
- Added an `Electronic Properties Analysis` expander below the run section in `app.py`.
- The analysis UI now:
  - shows a summary row for DOS detection, band detection, Fermi source, and run directory
  - shows an explicit metadata-only empty state when only `pw.out` and/or QE XML metadata exists
  - renders total DOS and non-projected band-structure plots with Plotly
  - exposes per-plot Fermi-alignment toggles and PNG export defaults through Plotly config
- DOS parsing behavior:
  - supports 2-column total DOS
  - supports 3-column total DOS plus integrated DOS
  - accepts 4+ column DOS only when headers explicitly identify spin-resolved total DOS
- Fermi metadata behavior:
  - prefers QE XML from `prefix.save/data-file-schema.xml`
  - converts XML Hartree energies to eV
  - falls back to `pw.out` regex parsing only if XML metadata is unavailable
- Band parsing behavior:
  - supports the observed `filband.gnu` text layout used by the bundled real QE sample fixture
  - optionally extracts high-symmetry separators from `K_POINTS crystal_b` / `tpiba_b` in a matching band input file
- Added `tests/test_electronic_properties.py` and QE-derived fixtures for XML metadata and `filband.gnu` parsing coverage.
- Added `plotly` to `requirements.txt`.

## Known Limitations

- Only the first structure in a CIF file is loaded.
- No structure editing tools exist yet.
- No persistent storage exists beyond the active Streamlit session.
- A hard browser refresh resets Streamlit session state.
- Manual pseudopotential override does not exist yet.
- Offline-only pseudopotential resolution does not exist yet.
- Live UI streaming depends on `pw.x` flushing stdout/stderr as it runs.
- DOS analysis does not run `dos.x`; it only parses existing DOS artifacts.
- Band analysis does not run `bands.x`; it only parses existing `filband.gnu`-style artifacts.
- V1 analysis does not support PDOS, fat bands, or projected band weights.

## Next Likely Steps

- Add explicit empty and error states in the main area for invalid uploads.
- Improve the 3D viewer layout and responsiveness.
- Add support for selecting among multiple structures in CIF files if needed.
- Add manual pseudopotential override controls.
- Add explicit cancel/terminate controls for active Quantum ESPRESSO runs.
- Add offline mode and explicit cache cleanup tools.
- Add structure export or conversion features.
- Extend electronic-properties analysis to PDOS from `projwfc.x`.
- Add projected or fat-band visualization.
- Add optional post-processing job launching for `dos.x` and `bands.x`.

## AI Context Notes

- The repo is currently very small and centered on `app.py`.
- The local virtual environment directory `venv/` exists and has already been used to install dependencies.
- There is no git repository initialized at the moment.
- Prefer preserving the current minimal structure unless the app grows enough to justify splitting `app.py` into modules.
