# QE Studio History

This file is the running project history and AI handoff context.

When future work is done, append a new dated entry and keep the "Current State" and "Next Likely Steps" sections updated.

## Project Summary

- Project name: QE Studio
- Goal: build a web-based GUI for Quantum ESPRESSO using Python, Streamlit, and ASE
- Current stage: multipage QE workflow with structure setup, runnable QE input generation, live execution, post-processing runners, and electronic-properties analysis

## Current State

- Streamlit app entrypoint exists at `app.py` and now acts as a shared app shell built on `st.Page(...)` and `st.navigation(...)`.
- The app uses a wide layout and a 4-page workflow:
  - `Structure`
  - `Setup`
  - `Run`
  - `Analysis`
- The sidebar is now a persistent project summary visible on every page.
- The `Structure` page now provides a shared `Import / Build Structure` workflow with:
  - ASE-backed file import for CIF, VASP `POSCAR` / `CONTCAR`, XSF, and periodic XYZ / extXYZ
  - a manual crystal builder for lattice parameters plus atomic positions
  - a prototype builder for ordered cubic double perovskites `A2BB'X6`
- Structure parsing, builder validation, and prototype generation now live in `structure_sources.py`.
- Structure commit logic, deterministic structure signatures, provenance tracking, and invalidation rules now live in `structure_state.py`.
- Source and active ASE `Atoms` objects are stored in `st.session_state`.
- The active structure can be switched between the source cell and a symmetry-reduced primitive cell.
- The `Structure` page renders the active structure with `py3Dmol`.
- The `Setup` page handles:
  - SSSP pseudopotential resolution
  - QE input parameter collection
  - generated `pw.in` preview
- The app resolves and caches SSSP pseudopotentials from versioned Materials Cloud manifests and archives.
- The setup flow generates runnable Quantum ESPRESSO input files through ASE once pseudopotentials are resolved.
- The `Run` page handles:
  - base `pw.x` execution
  - DOS post-processing
  - band-structure workflow execution
  - live logs and artifact summaries
- The app can launch `pw.x`, `dos.x`, and `bands.x` locally, stream live terminal output, and save logs under `qe_runs/`.
- The app records deterministic structure provenance under `qe_runs/qe_studio_provenance.json` and surfaces stale / out-of-sync warnings on `Setup`, `Run`, and `Analysis`.
- The `Analysis` page detects QE analysis artifacts in `qe_runs/` and renders DOS and band-structure charts from real QE post-processing files.
- `project_context.py` now centralizes lightweight session-plus-disk summaries for the shared shell and page prerequisites.
- Heavy DOS and band parsing remains inside `ui_analysis.py`; the shared context stays summary-only.
- Dependencies are listed in `requirements.txt`.

## File Map

- `app.py`: shared Streamlit shell, navigation, workflow indicator, and persistent sidebar summary
- `project_context.py`: lightweight cross-page state and disk summary helpers
- `structure_sources.py`: structure import adapters, manual-builder validation, and prototype generation
- `structure_state.py`: structure state, signatures, provenance, cell-mode switching, and invalidation helpers
- `ui_structure.py`: Structure page UI
- `ui_setup.py`: Setup page UI
- `ui_run.py`: Run page UI
- `ui_analysis.py`: Analysis page UI
- `electronic_properties.py`: QE analysis discovery, parsing, and Plotly helpers
- `postprocessing.py`: disk-first QE run inspection, DOS/bands input generation, and output validation helpers
- `qe_runner.py`: registry-backed QE subprocess execution and live output streaming
- `pseudopotentials.py`: SSSP pseudopotential resolution and cache helpers
- `structure_tools.py`: primitive-cell reduction and automatic band-path helpers
- `tests/test_project_context.py`: coverage for lightweight shared context summaries
- `requirements.txt`: direct Python dependencies
- `README.md`: setup, run, and test instructions
- `HISTORY.md`: persistent project context for humans and AI

## Implemented History

### 2026-04-06

- Reworked the `Structure` page from a CIF-only upload flow into a multi-source `Import / Build Structure` workflow.
- Added ASE-backed file import for:
  - CIF
  - VASP `POSCAR` / `CONTCAR`
  - XSF
  - periodic XYZ / extXYZ when a valid 3D lattice is present
- Added a manual crystal builder with validation for:
  - lattice parameters
  - coordinate mode
  - atomic species and positions
  - duplicate / malformed rows
  - optional formula consistency checks
- Added a first prototype builder for ordered cubic double perovskites `A2BB'X6`.
- Split structure-specific logic out of `project_context.py` into:
  - `structure_sources.py` for structure ingestion and builder validation
  - `structure_state.py` for structure commit logic, source / primitive mode handling, deterministic signatures, provenance, and stale-state invalidation
- Replaced the old `uploaded` structure-mode language with `source`, while keeping legacy compatibility where needed.
- Added a deterministic structure signature based on normalized cell, wrapped fractional positions, species, and atom count.
- Added persisted provenance tracking in `qe_runs/qe_studio_provenance.json` for:
  - generated `pw.in`
  - base `pw.x` runs
  - DOS workflow outputs
  - bands workflow outputs
- Added stale / out-of-sync warnings across `Setup`, `Run`, and `Analysis` when the active structure no longer matches saved input or artifacts.
- Updated tests to cover:
  - structure signatures
  - file import formats
  - manual builder validation
  - prototype generation
  - provenance and invalidation behavior
- Added `numpy` as a direct runtime dependency for structure normalization and validation helpers.

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

### 2026-04-02

- Refactored QE Studio from a single-page Streamlit app into a 4-page workflow using `st.Page(...)` and `st.navigation(...)`.
- Replaced the old sidebar uploader layout with a shared app shell:
  - persistent sidebar project summary
  - top navigation
  - compact workflow-step indicator
- Added `project_context.py` to centralize:
  - shared state keys
  - structure/session helpers
  - pseudopotential summary state
  - generated-input summary state
  - job summary state
  - lightweight artifact discovery summaries
- Kept `project_context.py` summary-only:
  - no DOS parsing
  - no band parsing
  - no figure generation
  - no heavy log reads
- Added `ui_structure.py` for:
  - CIF upload
  - uploaded vs primitive-cell controls
  - compact structure summary
  - `py3Dmol` visualization
- Added `ui_setup.py` for:
  - pseudopotential resolution UI
  - QE input form
  - generated `pw.in` preview with disk fallback
- Added `ui_run.py` for:
  - base `pw.x` execution
  - DOS runner
  - band workflow runner
  - live logs
  - artifact tables
- Added `ui_analysis.py` for:
  - artifact summary row
  - DOS viewer
  - band viewer
  - band-gap summary
  - alignment controls
- Preserved the existing runner registry/session-state architecture and kept the `Run` page as the only operational/log-heavy page.
- Preserved disk-first behavior under `qe_runs/` so pages remain robust across refreshes.
- Added `tests/test_project_context.py` to verify the new shared context behavior and guard against heavy DOS/band parsing in the context layer.
- Updated `README.md` to document the new multipage layout and page responsibilities.
- Improved the shared workflow indicator styling for better readability in dark mode.
- Promoted the `Upload a CIF file` heading on the `Structure` page so it aligns visually with `Structure Summary`.
- Updated DOS and band Plotly figures for a more publication-ready style:
  - explicit light paper background
  - dark high-contrast labels for dark-mode compatibility
  - stronger typography and axis styling
  - taller chart proportions for better readability
  - Streamlit Plotly theme override disabled in the Analysis page so figure styling remains consistent
- Reduced the on-screen footprint of Analysis charts:
  - centered DOS and band figures in narrower layout columns
  - lowered default figure heights so charts no longer dominate the full screen
  - kept the publication-style colors, labels, and export behavior
- Added a dedicated `Publication Export` figure mode on the `Analysis` page:
  - keeps `Interactive` as the default on-screen preset
  - adds larger typography and tighter margins for export-focused figures
  - lets the Plotly export button use either PNG or SVG output
  - applies fixed export widths for DOS and band figures to support more consistent journal-ready output
- Moved Analysis `Figure options` into a collapsed expander by default so the results dashboard stays cleaner until export controls are needed.
- Tightened the shared sidebar top spacing so the project panel aligns more cleanly with the top navigation and main app frame.
- Shifted the full sidebar content block upward so the `QE Studio` heading aligns more closely with the built-in sidebar collapse arrow.

## Known Limitations

- Only the first structure in a supported multi-structure file is loaded.
- The prototype builder currently supports only ordered cubic double perovskites `A2BB'X6`.
- Database-backed import, pasted structure text, and QE-input import are not implemented yet.
- No persistent storage exists for the active structure or builder drafts beyond the active Streamlit session.
- A hard browser refresh resets Streamlit session state.
- Manual pseudopotential override does not exist yet.
- Offline-only pseudopotential resolution does not exist yet.
- Live UI streaming depends on QE executables flushing stdout/stderr as they run.
- V1 analysis does not support PDOS, fat bands, or projected band weights.
- The shared sidebar intentionally shows only lightweight summaries; full DOS/band parsing happens only on the `Analysis` page.

## Next Likely Steps

- Add database-backed structure import through Materials Project, COD, or OPTIMADE.
- Add pasted-structure-text and QE-input import adapters under the same source-selector workflow.
- Add more crystal prototypes after the ordered cubic double perovskite builder is stable.
- Add support for selecting among multiple structures in supported multi-structure files if needed.
- Add explicit cancel/terminate controls for active Quantum ESPRESSO runs.
- Continue polishing the shared shell styling and card layouts for a more dashboard-like presentation.
- Add manual pseudopotential override controls.
- Add offline mode and explicit cache cleanup tools.
- Add structure export or conversion features.
- Extend electronic-properties analysis to PDOS from `projwfc.x`.
- Add projected or fat-band visualization.
- Add run history or recent-artifact navigation inside the shared project frame.

## AI Context Notes

- The repo is no longer centered on a single large `app.py`; the UI is split across dedicated page modules plus `project_context.py`.
- `project_context.py` should remain summary-focused; structure ingestion, validation, signatures, provenance, and invalidation now live in `structure_sources.py` and `structure_state.py`.
- The local virtual environment directory `venv/` exists and has already been used to install dependencies.
- There is no git repository initialized at the moment.
- The current architecture should preserve the lightweight shared context rule:
  - summary-only work in `project_context.py`
  - heavy DOS/band parsing only in `ui_analysis.py`
