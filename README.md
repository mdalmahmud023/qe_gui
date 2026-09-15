# QE Studio

QE Studio is a Streamlit-based web GUI for Quantum ESPRESSO workflows — covering structure import, input generation, convergence studies, local execution, post-processing, and electronic-properties analysis in a single research-grade application.

## Features

### Projects (Phase 2)

The app opens with **Projects**, offering **Create Project**, **Open Existing**, and **Recent Projects**. Opened projects have **Project**, **Structure**, and **Baseline** pages. Select **Legacy Workspace** for the existing Structure / Setup / Studies / Run / Analysis workflow and its `qe_runs` files. Project calculations, runs, studies, dependency tracking, and legacy migration are not part of Phase 2.

A newly created project contains `project.json`, `project.sqlite3`, and `.staging/`. Scientific directories appear only when records are committed:

```text
My-project/
├── project.json
├── project.sqlite3
├── structures/
│   └── structure_001/
│       ├── atoms.json
│       ├── structure.cif
│       ├── source.cif       # Original imported bytes, where applicable
│       └── metadata.json
├── pseudopotentials/
│   └── set_001/
│       ├── manifest.json
│       └── files/
└── .staging/
```

**Structures:** import CIF/VASP/XSF/XYZ, use the manual builder, or choose a prototype. Preview first, then **Apply Structure** to commit an immutable version. Choose a committed parent, preview primitive/conventional/supercell changes, and **Apply Transformation** to create a child. The same parent stays selected until you explicitly select another version; repeated Apply clicks on the same preview reuse its committed child. **Set Active** is a separate action and persists across restarts. Changing controls or viewer orientation does not create history. Selecting another active structure clears the active baseline pointer but preserves every baseline revision.

`atoms.json` is the authoritative full-precision, ordered ASE snapshot, including PBC, supported arrays, constraints, and metadata. CIF is an interchange representation. Structure hash version 1 hashes the canonical snapshot; the original geometry signature remains available for legacy comparison. Standardization preserves species-uniform site arrays and regenerates CIF source-site labels with recorded provenance. It rejects ambiguous site-specific arrays, constraints, or partial occupancies instead of silently losing them. Supercell transformations use the selected frozen parent, never a previous preview.

**Pseudopotentials and baselines:** resolve SSSP or fully relativistic SG15 UPFs into the reusable download cache, then **Commit Pseudopotential Set** to copy the selected bytes into the project. Each copy is SHA-256 verified; identical species-to-byte mappings reuse an existing set. The global project-mode cache defaults to `$XDG_CACHE_HOME/qe-studio/pseudopotentials` (`~/.cache/qe-studio/pseudopotentials`); `QE_STUDIO_CACHE_DIR` overrides its parent directory. Legacy Workspace retains its existing cache paths. Deleting the global cache does not affect committed project UPFs.

Edit common physical and numerical defaults on **Baseline**, use **Preview Baseline** for validation, then **Commit Baseline Revision**. Each immutable revision references exact structure and pseudo-set IDs. **Set Active Baseline** restores that revision's structure and settings. The existing typed scientific validation remains in use; no QE input generation or execution is added to project mode.

UUIDs, not names or directory paths, define identity. Display names can be duplicated; renaming a display name leaves the directory unchanged. After moving a project, use **Open Existing** at its new location to update Recents. Independent copies sharing one UUID remain unsupported. Authoritative payload paths are project-relative, so moved projects reload without the original cache or repository location.

SQLite schema 2 contains only `project`, `schema_migrations`, `structures`, `pseudo_sets`, `pseudo_entries`, and `baselines`. Opening a valid Phase 1 project applies a transactional schema upgrade; the container marker format remains version 1. Scientific rows reject updates/deletes. JSON sidecars are snapshots, not editable authorities. Files are staged before publication; interrupted unregistered payloads are retained and reported in the overview for inspection, without inventing successful records.

Recents live separately at `$XDG_DATA_HOME/qe-studio/workspace.sqlite3` (default `~/.local/share/qe-studio/workspace.sqlite3`); set `QE_STUDIO_WORKSPACE_DIR` to override that directory. Open Existing refers to the Streamlit host filesystem. Invalid/newer schemas show errors without falling back to another workspace.

Each tab has independent selections and UUID-scoped project widgets. Switching projects clears transient project previews/editors and reloads committed active IDs and settings. Legacy drafts and runner IDs remain isolated in their session bank; switching does not cancel jobs. Browser refresh still resets transient drafts. Conflicting project-detail edits require **Reload Project Details** before saving.

### Structure
- Import structure files through ASE-backed readers:
  - `.cif`
  - VASP `POSCAR` / `CONTCAR`
  - `.xsf`
  - `.xyz` / `extxyz` when a usable 3D periodic cell is present
- Build periodic crystal structures manually from lattice parameters, species, and atomic positions
- Generate curated prototype starting structures:
  - ordered cubic double perovskite `A2BB'X6`
  - cubic perovskite `ABX3`
  - rocksalt `AB`
- Switch between the source cell and a symmetry-reduced primitive cell
- Interactive 3D structure viewer (`py3Dmol`) with labels, style modes, bonds, unit-cell visibility, species filters, and supercell repeats

### Setup
- Resolve SSSP pseudopotentials (PBE / PBEsol, efficiency / precision) from versioned Materials Cloud manifests, cached locally under `qe_runs/pseudopotentials/sssp/`
- Resolve fully relativistic SG15 ONCV PBE FR pseudopotentials for spin-orbit coupling calculations
- Configure all QE `pw.x` input parameters in a form:
  - energy cutoffs (`ecutwfc`, `ecutrho`)
  - k-point grid and occupation settings
  - spin polarization (`nspin=2`, species-specific starting magnetization)
  - spin-orbit coupling (`noncolin`, `lspinorb`)
  - DFT+U via legacy `lda_plus_u` and `Hubbard_U(i)` entries
  - convergence thresholds, mixing, and diagonalization
- Generate a runnable `pw.in` preview in `qe_runs/`
- Provenance tracking: warns when saved inputs are out of sync with the active structure

### Studies
A dedicated Studies page for multi-run convergence workflows that consume the active Setup baseline.

#### Cutoff Convergence
- Sweep `ecutwfc` over a configurable range with fixed k-points and physics settings
- Supports multiple `ecutrho` derivation modes: pseudopotential metadata dual ratios, ratio rules, or manual values
- Recommends the converged `(ecutwfc, ecutrho)` pair within a meV/atom tolerance
- Apply the recommendation directly back to the Setup baseline

#### K-Point Convergence
- Sweep k-point density (isotropic 3D, in-plane 2D, or 1D wire) with fixed cutoffs and physics settings
- Recommends the converged k-grid within a meV/atom tolerance
- Apply the recommendation directly back to the Setup baseline

#### Equation of State (EOS)
- Isotropically scale the active structure over a configurable volume range
- Fit a Birch-Murnaghan equation of state via `scipy.optimize.curve_fit`
- Reports equilibrium volume `V₀`, bulk modulus `B₀`, and equilibrium lattice constant `a₀`
- Apply the equilibrium cell parameters directly back to the active structure

#### Shared Study Features
- All study inputs are generated from the active Setup baseline snapshot (k-points, cutoffs, occupations, pseudopotentials, magnetism)
- Study manifests and summaries are persisted under `qe_runs/convergence/`
- **▶ Run Study** button executes all point inputs sequentially using the same `pw.x` runner infrastructure as the Run page
- Live streaming console with per-point phase labels
- **⏹ Stop** button to terminate the active run
- Baseline-drift detection: warns when the active Setup has changed since the study was generated
- Interactive Plotly result charts for all three study types
- Refresh Results button to re-parse outputs after manual QE runs

### Run
- Launch `pw.x` locally against the generated `qe_runs/pw.in`
- Stream live Quantum ESPRESSO terminal output in the UI; save the full log to `qe_runs/pw.out`
- DOS post-processing: launch `dos.x`, configure energy window and smearing, save `qe_runs/dos.out`
- Band-structure workflow: auto-fill high-symmetry paths from ASE, run a dedicated `pw.x` band calculation, then `bands.x` post-processing

### Analysis
- Detect QE post-processing artifacts in `qe_runs/`
- Render interactive total DOS charts with Plotly and Fermi-alignment toggle
- Render interactive band-structure charts from `filband.gnu` with optional high-symmetry separators
- XML-first Fermi metadata extraction; falls back to `pw.out` regex parsing

---

## Requirements

- Python 3.12 or another compatible Python 3 version
- `pip`
- Quantum ESPRESSO installed locally with `pw.x`, `dos.x`, and `bands.x` on your `PATH`

---

## Install Quantum ESPRESSO

QE Studio can generate input files and studies with Python alone, but running calculations requires a working local Quantum ESPRESSO installation.

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install quantum-espresso
```

If you encounter missing MPI library errors (e.g., `libmpi_mpifh.so.40: cannot open shared object file`):

```bash
sudo apt install libopenmpi-dev openmpi-bin
```

### Conda

```bash
conda install -c conda-forge qe
```

### From source

Build from the upstream QE repository following its own build instructions, then add the resulting binaries to your `PATH`.

### Verify

```bash
which pw.x dos.x bands.x
pw.x -h
```

---

## Setup

Activate (or create) a virtual environment:

```bash
# Use the existing venv
source venv/bin/activate

# Or create a fresh one
python3 -m venv venv
source venv/bin/activate
```

Install Python dependencies:

```bash
pip install -r requirements.txt
```

---

## Run the App

```bash
streamlit run app.py
```

Without activating the venv first:

```bash
venv/bin/streamlit run app.py
```

Streamlit prints a local URL such as `http://localhost:8501`. Open it in your browser, then follow the 5-page workflow:

| Page | Purpose |
|------|---------|
| **Structure** | Import or build a structure, inspect in 3D |
| **Setup** | Resolve pseudopotentials, configure QE parameters, generate `pw.in` |
| **Studies** | Run convergence studies (Cutoff, K-Point, EOS) |
| **Run** | Execute `pw.x`, `dos.x`, `bands.x` locally with live output |
| **Analysis** | DOS and band-structure dashboards |

---

## Quick Test Flow

1. `streamlit run app.py` — confirm the app loads.
2. **Structure**: import a file or use a prototype builder.
3. **Setup**: click `Resolve Pseudopotentials`, adjust a few QE parameters, click `Generate Input File`.
4. **Studies**:
   - Select **Cutoff Convergence** and click **Generate Cutoff Study**. Confirm input directories are created under `qe_runs/convergence/`.
   - Click **▶ Run Study** and watch the live console stream `pw.x` output for each point.
   - Once complete, click **Refresh Results** to see the convergence chart and recommendation.
   - Click **Apply Recommended Cutoff to Setup** to update the Setup baseline.
   - Repeat for **K-Point Convergence** and **Equation of State (EOS)**.
5. **Run**: click `Run Calculation`. Confirm live output appears and `qe_runs/pw.out` is created.
6. **Analysis**: confirm DOS and band-structure charts render when `dos.x` and `bands.x` outputs exist.

---

## Useful Commands

Syntax check:

```bash
python3 -m compileall qe_studio tests app.py
```

Run the test suite:

```bash
PYTHONPATH=. venv/bin/pytest tests/
```

Headless startup check:

```bash
timeout 10s venv/bin/streamlit run app.py --server.headless true --server.address 127.0.0.1
```

---

## Project Layout

```
qe_studio/
├── app.py                      # Streamlit shell, navigation, sidebar
├── core/
│   ├── project_context.py      # Lightweight cross-page state & disk summaries
│   ├── state_keys.py           # All session-state key constants
│   ├── structure_state.py      # Structure commit, signatures, provenance, cell-mode
│   ├── workspace.py            # Directory & filename constants
│   └── ...
├── convergence/
│   ├── models.py               # Data models: sweep specs, study points, EOS fit result
│   ├── cutoff_study.py         # Cutoff sweep generation, manifest, refresh
│   ├── kpoint_study.py         # K-point sweep generation, manifest, refresh
│   ├── eos_study.py            # EOS isotropic scaling, Birch-Murnaghan fitting
│   ├── eos_fit.py              # scipy-based Birch-Murnaghan curve fit
│   ├── result_parser.py        # pw.out energy parser (shared by all study types)
│   ├── recommendation.py       # Convergence recommendation logic
│   ├── study_runner.py         # Converts manifests to JobStep lists for pw.x
│   └── shared.py               # Shared utilities (unique study dir, delta calc)
├── pages/
│   ├── ui_structure.py         # Structure page
│   ├── ui_setup.py             # Setup page
│   ├── ui_studies.py           # Studies page (Cutoff, K-Point, EOS tabs)
│   ├── ui_run.py               # Run page
│   ├── ui_analysis.py          # Analysis page
│   └── shared.py               # Shared page helpers (tables, captions, etc.)
└── services/
    ├── input_generator.py      # CalculationContext, generate_qe_input_text
    ├── qe_runner.py            # Registry-backed subprocess runner + streaming
    ├── pseudopotentials.py     # SSSP & SG15 pseudopotential resolution & cache
    ├── postprocessing.py       # Disk-first QE run inspection, DOS/bands helpers
    ├── electronic_properties.py# Artifact discovery, Fermi parsing, Plotly figures
    ├── plotting/
    │   └── study_plots.py      # Plotly charts for Cutoff, K-Point, EOS studies
    └── studies/
        └── study_summary_view.py  # Baseline-drift detection for all study types
tests/
├── test_eos_study.py
├── test_kpoint_study.py
├── test_cutoff_study.py        # (if present)
└── fixtures/                   # QE-derived test fixtures
requirements.txt
HISTORY.md
```

---

## Notes

- Legacy page storage paths come from `ProjectContext.paths` (`WorkspacePaths`). Legacy Workspace defaults to `qe_runs`; callers can use `build_project_context(Path("/path/to/workspace"))` to supply another root. All three study types retain the legacy `convergence/cutoff` subdirectory. `build_project_context(project=opened_project)` instead exposes project identity and `ProjectPaths`, loading committed structures and baselines without scanning for calculation artifacts or touching runtime state.
- Pseudopotential resolution and cache clearing use the legacy workspace's `pseudopotentials` directory. Input previews and cached pseudopotential paths from another workspace are rejected. Explicit external QE scratch paths retain their legacy behavior.
- The app stores source and active ASE `Atoms` objects in `st.session_state`; a hard browser refresh resets session state.
- All generated inputs, logs, study manifests, and provenance metadata are disk-first under `qe_runs/`; pages reconstruct their state from disk after a refresh.
- The `Cell Representation` selector (source vs. primitive cell) affects newly generated QE inputs and study baselines. Switching cells requires regenerating `pw.in` and any studies.
- The pseudopotential resolver supports SSSP `1.3.0` (PBE/PBEsol, efficiency/precision) and the SG15 ONCV PBE FR set for SOC calculations.
- SOC calculations require fully relativistic pseudopotentials for all species; mixed scalar+FR sets are blocked before input generation.
- DFT+U uses the legacy `lda_plus_u` path with user-supplied Hubbard U values in eV.
- Spin-orbit coupling does not yet support noncollinear magnetic vector setup (`angle1/angle2`) or collinear `nspin=2` combined with SOC.
- Study runs use the same `pw.x` subprocess runner as the main Run page; only one QE job can run at a time.
- The EOS workflow performs isotropic lattice scaling via ASE (`atoms.set_cell(..., scale_atoms=True)`), which preserves fractional coordinates.
- V1 analysis supports total DOS and non-projected `filband.gnu` band plots; PDOS and fat bands are not yet implemented.
