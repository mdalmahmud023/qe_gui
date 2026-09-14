# QE Studio History

This file is the running project history and AI handoff context.

When future work is done, append a new dated entry and keep the "Current State" and "Next Likely Steps" sections updated.

## Project Summary

- Project name: QE Studio
- Goal: build a web-based GUI for Quantum ESPRESSO using Python, Streamlit, and ASE
- Current stage: 5-page workflow covering structure import, QE input generation, multi-run convergence studies (Cutoff, K-Point, EOS), live execution, post-processing, and electronic-properties analysis

## Current State

- Phase 0 workspace paths are explicit: `ProjectContext.paths` routes generation, study discovery, pseudopotential caches, execution, and provenance. Default startup still uses `qe_runs`; SQLite projects and session namespacing are not implemented yet.
- Streamlit app entrypoint exists at `app.py` and acts as a shared app shell built on `st.Page(...)` and `st.navigation(...)`.
- The app uses a wide layout and a **5-page workflow**:
  - `Structure`
  - `Setup`
  - `Studies`
  - `Run`
  - `Analysis`
- The sidebar is a persistent project summary visible on every page.
- The `Structure` page handles ASE-backed file import (CIF, VASP, XSF, periodic XYZ), a manual crystal builder, and curated prototype families (double perovskite, perovskite, rocksalt). Interactive 3D viewer with `py3Dmol`.
- The `Setup` page handles SSSP and SG15 ONCV FR pseudopotential resolution, QE parameter configuration (cutoffs, k-points, occupations, spin, SOC, DFT+U), and `pw.in` generation. All parameters are captured in a `CalculationContext` dataclass through `services/input_generator.py`.
- The `Studies` page provides three independent convergence workflows, each consuming the active Setup baseline snapshot:
  - **Cutoff Convergence**: sweeps `ecutwfc`/`ecutrho`, recommends the converged pair, and can apply it back to Setup.
  - **K-Point Convergence**: sweeps k-grid density (isotropic 3D, 2D in-plane, 1D wire), recommends the converged grid, and can apply it back to Setup.
  - **Equation of State (EOS)**: isotropically scales the active structure, fits a Birch-Murnaghan EOS via `scipy`, reports V₀/B₀/a₀, and can apply the equilibrium cell back to the active structure.
  - All study types have a **▶ Run Study** button that launches all point inputs sequentially as chained `pw.x` jobs with live console streaming and a **⏹ Stop** button.
  - Baseline-drift detection warns when the active Setup has changed since a study was generated.
  - Interactive Plotly result charts and data tables for all three study types.
- The `Run` page handles base `pw.x` execution, DOS post-processing (`dos.x`), band-structure workflow (`pw.x` + `bands.x`), live logs, and artifact summaries.
- The `Analysis` page renders interactive total DOS and non-projected band-structure charts from real QE output files.
- All generated inputs, study manifests/summaries, logs, and provenance metadata are disk-first under `qe_runs/`.
- `services/input_generator.py` contains `CalculationContext`, `NumericalSettings`, `PhysicalOptions`, and `ControlSettings` dataclasses plus `generate_qe_input_text`.
- `convergence/` package contains models, study generators, result parsers, EOS fitting, recommendation logic, and `study_runner.py` which converts manifests into `JobStep` lists for `run_chained_jobs`.
- Dependencies are listed in `requirements.txt`.

## File Map

- `app.py`: shared Streamlit shell, navigation, workflow indicator, and persistent sidebar summary
- `qe_studio/core/project_context.py`: lightweight cross-page state and disk summary helpers
- `qe_studio/core/state_keys.py`: all session-state key constants
- `qe_studio/core/structure_state.py`: structure state, signatures, provenance, cell-mode switching, and invalidation helpers
- `qe_studio/core/workspace.py`: directory and filename constants
- `qe_studio/pages/ui_structure.py`: Structure page UI
- `qe_studio/pages/ui_setup.py`: Setup page UI
- `qe_studio/pages/ui_studies.py`: Studies page UI (Cutoff, K-Point, EOS tabs + Run Study controls)
- `qe_studio/pages/ui_run.py`: Run page UI
- `qe_studio/pages/ui_analysis.py`: Analysis page UI
- `qe_studio/pages/shared.py`: shared page helpers (tables, captions, status messages)
- `qe_studio/convergence/models.py`: data models — sweep specs, study points, PointResult, EosFitResult
- `qe_studio/convergence/cutoff_study.py`: cutoff sweep generation, manifest, and refresh
- `qe_studio/convergence/kpoint_study.py`: k-point sweep generation, manifest, and refresh
- `qe_studio/convergence/eos_study.py`: EOS isotropic scaling, manifest, and refresh
- `qe_studio/convergence/eos_fit.py`: Birch-Murnaghan equation of state fitting via scipy
- `qe_studio/convergence/result_parser.py`: pw.out total-energy parser (shared by all study types)
- `qe_studio/convergence/recommendation.py`: convergence recommendation logic
- `qe_studio/convergence/study_runner.py`: converts study manifests into JobStep lists for pw.x
- `qe_studio/convergence/shared.py`: shared utilities (unique study dir, successive delta calculation)
- `qe_studio/services/input_generator.py`: CalculationContext and generate_qe_input_text
- `qe_studio/services/qe_runner.py`: registry-backed QE subprocess execution and live output streaming
- `qe_studio/services/pseudopotentials.py`: SSSP and SG15 ONCV FR pseudopotential resolution and cache helpers
- `qe_studio/services/postprocessing.py`: disk-first QE run inspection, DOS/bands input generation, and output validation helpers
- `qe_studio/services/electronic_properties.py`: QE analysis discovery, parsing, and Plotly figure helpers
- `qe_studio/services/plotting/study_plots.py`: Plotly charts for Cutoff, K-Point, and EOS studies
- `qe_studio/services/studies/study_summary_view.py`: baseline-drift detection for all study types
- `tests/`: pytest test suite covering convergence models, EOS fitting, k-point recommendations, and more
- `requirements.txt`: direct Python dependencies
- `README.md`: setup, run, and test instructions
- `HISTORY.md`: persistent project context for humans and AI

## Implemented History

### 2026-09-14 — Phase 0: explicit workspace paths

- Added immutable `WorkspacePaths` and a derived `ProjectContext.paths` property without creating directories or changing the default layout.
- Routed Setup, all study types, base QE execution, DOS, and bands through the supplied context; passed explicit cache roots for both SSSP and SG15 resolution and cache clearing.
- Guarded session input previews and pseudopotential paths against workspace mismatch, including local-path metadata and symlink targets.
- Corrected the unreachable Setup success handler so generated input paths, preview state, and workspace provenance are actually recorded.
- Added two-workspace integration tests using real Streamlit page actions, generation, parsing, and filesystem writes with mocked downloads and subprocesses. Tests cover relative/absolute roots, unrelated-CWD sentinels, and legacy external scratch paths.
- No legacy data migration, database, navigation change, or runner redesign is included.

### 2026-04-07

- Reworked the `Structure` page into a more explicit draft-vs-active workspace.
- Expanded the Prototype Builder from one hardcoded form into a curated family system backed by `prototype_catalog.py`.
- Added curated prototype families for:
  - ordered cubic double perovskite `A2BB'X6`
  - cubic perovskite `ABX3`
  - rocksalt `AB`
- Refactored the Prototype Builder UI so users choose a prototype family first and then see only that family’s fields.
- Added family-scoped prototype draft state so switching prototype families preserves each family’s inputs and reset only clears the current family draft.
- Standardized prototype metadata so active-structure summaries show human-facing family details instead of raw internal prototype keys.
- Added a compact active-structure banner that keeps the current committed structure visible while users work in the draft area.
- Split the page into:
  - `Import / Build Draft`
  - `Inspect Active Structure`
- Moved the inspection workspace below the draft panel and centered the draft builder in the page.
- Expanded the draft panel so its outer box matches the width of the other major Structure-page panels while keeping the form content centered inside it.
- Rebalanced the inspection workspace so the visualization area is larger than the settings area.
- Tightened the settings panel layout so label/style selectors stay readable and the bond/unit-cell toggles sit below them instead of wrapping awkwardly.
- Fixed draft reset actions so file, manual, and prototype drafts reset through nonce-based rerendering instead of mutating active Streamlit widget keys after instantiation.
- Fixed the manual-builder atom table so `st.data_editor` owns its widget state instead of receiving a plain list through the same session-state key, resolving a crash when opening the manual builder after the nonce-based reset refactor.
- Moved the active `Clear Structure` action and active warnings into the active banner instead of the draft source panel.
- Changed draft apply behavior so newly applied structures always become active in `Source Cell`.
- Added `structure_viewer.py` to hold viewer-only rendering logic and keep `ui_structure.py` from mixing scientific structure state with display state.
- Upgraded the `py3Dmol` viewer with:
  - label modes
  - style modes
  - heuristic bond toggle
  - unit-cell toggle
  - species visibility filters
  - small viewer-only supercell repeats
  - optional axes in advanced controls
- Added explicit UI messaging that bonds and supercell repeats are viewer-only and do not change QE inputs or committed structure state.
- Lightened the shared workflow indicator so it behaves more like a compact progress strip than a set of large dashboard cards.
- Added tests for:
  - viewer filtering
  - viewer repeat expansion
  - viewer HTML generation
  - explicit reset-to-source behavior when applying a new structure

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

### 2026-04-17

- Added a dedicated **Studies** page between Setup and Run, giving convergence workflows a first-class home in the app navigation.
- Moved cutoff convergence out of the Setup page into the new Studies page.
- Added **K-Point Convergence** study: sweeps k-grid density (isotropic 3D, 2D in-plane, 1D wire) with fixed cutoffs; recommends the converged grid; apply-to-Setup button.
- Added **Equation of State (EOS)** study: isotropically scales the active structure over a configurable volume range; fits a Birch-Murnaghan EOS via `scipy.optimize.curve_fit`; reports V₀, B₀, a₀; apply-equilibrium-volume button.
- Added `convergence/` package with dedicated modules for each study type: `cutoff_study.py`, `kpoint_study.py`, `eos_study.py`, `eos_fit.py`, `result_parser.py`, `recommendation.py`, `shared.py`, and `models.py`.
- Added `convergence/study_runner.py`: converts any study manifest into a list of `JobStep` objects for `run_chained_jobs`, enabling sequential `pw.x` execution for each study point.
- Added **▶ Run Study** button to each study type's results panel: launches all point inputs sequentially, streams live `pw.x` console output per point, and shows a **⏹ Stop** button to terminate the active run.
- Added `_study_live_console_fragment` (`@st.fragment(run_every=1)`) for non-blocking study run output streaming independent of the main Run page.
- Added baseline-drift detection (`services/studies/study_summary_view.py`) that warns when the active Setup baseline has changed since a study was generated, covering structure, pseudopotentials, k-points, and cutoffs.
- Refactored QE input generation into a structured service layer (`services/input_generator.py`) with `CalculationContext`, `NumericalSettings`, `PhysicalOptions`, and `ControlSettings` dataclasses.
- Fixed `parse_cutoff_point_output` calls in `eos_study.py` to pass the required `atom_count` keyword argument; stored `atom_count` in EOS manifests at generation time.
- Added interactive Plotly charts for all three study types via `services/plotting/study_plots.py`.
- Added `pytest` test coverage for EOS models, sweep spec validation, Birch-Murnaghan fitting edge cases, and study generation.
- Fixed Studies page to read k-points, cutoffs, and all physics settings from the active Setup baseline snapshot rather than defaults.
- Updated the app navigation from 4 pages to 5 pages.

## Known Limitations

- Only the first structure in a supported multi-structure file is loaded.
- The prototype builder is intentionally curated and currently supports only double perovskites, cubic perovskites, and rocksalt.
- No persistent storage beyond the active Streamlit session; a hard browser refresh resets session state.
- Manual pseudopotential override does not exist yet.
- Offline-only pseudopotential resolution does not exist yet.
- Live UI streaming depends on QE executables flushing stdout/stderr as they run.
- Only one QE job (studies or main Run page) can run at a time; global run lock is shared.
- EOS workflow currently only supports isotropic scaling; anisotropic or axis-specific volume scans are not implemented.
- Study `Run Study` feature requires `pw.x` on PATH; no MPI or job-scheduler integration exists.
- V1 analysis does not support PDOS, fat bands, or projected band weights.
- SOC calculations do not yet support noncollinear magnetic vector setup (`angle1/angle2`).
- Advanced structure-inspection overlays such as coordination polyhedra are not implemented yet.
- The shared sidebar intentionally shows only lightweight summaries; full DOS/band parsing happens only on the `Analysis` page.

## Next Likely Steps

- Project Phase 1: add persistent project identity and SQLite storage, Project Manager, and project/entity session-state isolation on top of the explicit path boundary.
- End-to-end validation: run a full Cutoff → K-Point → EOS workflow on a real structure and verify parsed energies, recommendations, and EOS fitting.
- Add MPI support option for study runs (e.g., `mpirun -n N pw.x -in pw.in`).
- Add anisotropic EOS scaling modes (e.g., uniaxial or biaxial strain).
- Add PDOS support via `projwfc.x` post-processing.
- Add projected / fat-band visualization.
- Add database-backed structure import (Materials Project, COD, OPTIMADE).
- Add pasted-structure-text and QE-input import adapters.
- Add the next curated prototype families (e.g., fluorite `AX2`).
- Add manual pseudopotential override controls.
- Add offline mode and explicit cache cleanup tools.
- Add structure export or conversion features.
- Add run history or recent-artifact navigation inside the shared project frame.

## AI Context Notes

- The app is organized as a proper Python package under `qe_studio/` with sub-packages `core/`, `pages/`, `services/`, and `convergence/`.
- `pages/ui_studies.py` owns all convergence study UI; `convergence/` owns all study service logic.
- `services/input_generator.py` owns the `CalculationContext` dataclass and `generate_qe_input_text`. Always use these when generating study inputs — never build raw QE namelist dicts directly in UI code.
- `services/qe_runner.py` is the single subprocess execution backend; both the Run page and the Studies Run Study feature share it.
- `core/project_context.py` should remain summary-only; no heavy parsing or figure generation.
- `core/state_keys.py` holds all session-state key string constants; add new ones there rather than scattering them across pages.
- The local virtual environment directory `venv/` exists and has already been used to install dependencies.
- Use `PYTHONPATH=. venv/bin/pytest tests/` to run the test suite.
- There is a `.git` repository initialized in the project root.
