# QE Studio

QE Studio is a Streamlit-based web GUI for Quantum ESPRESSO workflows — covering structure import, input generation, convergence studies, local execution, post-processing, and electronic-properties analysis in a single research-grade application.

## Features

### Projects (Phase 5)

The app opens with **Projects**, offering **Create Project**, **Open Existing**, and **Recent Projects**. Opened projects have **Project**, **Structure**, **Baseline**, **Calculations**, and **Studies** pages. Selected calculations and results explain compatibility with the active baseline and their recorded upstream sources. Select **Legacy Workspace** for the existing Structure / Setup / Studies / Run / Analysis workflow and its `qe_runs` files. A general workflow graph and legacy migration remain future work.

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

Edit common physical and numerical defaults on **Baseline**, use **Preview Baseline** for validation, then **Commit Baseline Revision**. Each immutable revision references exact structure and pseudo-set IDs. **Set Active Baseline** restores that revision's structure and settings. Calculations inherit the selected baseline and apply saved overrides through the existing typed scientific validation and generator.

UUIDs, not names or directory paths, define identity. Display names can be duplicated; renaming a display name leaves the directory unchanged. After moving a project, use **Open Existing** at its new location to update Recents. Independent copies sharing one UUID remain unsupported. Authoritative payload paths are project-relative, so moved projects reload without the original cache or repository location.

SQLite schema 6 adds only `baseline_recommendations` to the existing project, scientific definition, calculation, execution, and study tables. Existing parent-run, study membership, consumed-run, and EOS lineage records remain authoritative. Opening a valid older project applies transactional schema upgrades; the container marker format remains version 1. Structures/pseudos/baselines remain immutable; ordinary calculation science becomes immutable on first attempt creation. Study point settings are bound to their frozen experiment during generation. JSON sidecars are snapshots, not editable authorities. Files are staged before publication; interrupted unregistered payloads are retained and reported in the overview for inspection, without inventing successful records.

Recents live separately at `$XDG_DATA_HOME/qe-studio/workspace.sqlite3` (default `~/.local/share/qe-studio/workspace.sqlite3`); set `QE_STUDIO_WORKSPACE_DIR` to override that directory. Open Existing refers to the Streamlit host filesystem. Invalid/newer schemas show errors without falling back to another workspace.

Each tab has independent selections and UUID-scoped project widgets. Switching projects clears transient project previews/editors and reloads committed active IDs and settings. Legacy drafts and runner IDs remain isolated in their session bank; switching does not cancel jobs. Browser refresh still resets transient drafts. Conflicting project-detail edits require **Reload Project Details** before saving.

### Project calculations and attempts

On **Calculations**, choose a kind and baseline, then **Create Calculation**. The browser filters SCF, NSCF, relax, vc-relax, bands, and DOS and provides **Overview / Parameters / Input / Runs / Results** tabs. The project `/setup` route opens this same editor. Parameters currently use a JSON overrides editor; common defaults remain on Baseline. **Save Draft** persists changes with revision checks; **Reload Editor** resolves another tab's conflicting edit.

Editing and regenerating preserve the calculation UUID and display number. A saved edit increments `draft_revision`; when it exceeds `generated_revision`, the UI shows **Input needs regeneration** and blocks execution. Generation retains prior snapshots in separate revision directories. Creating the first run atomically freezes the baseline, overrides, resolved request, exact input hashes, and generator version/source fingerprint. Thereafter **Duplicate Calculation** starts a new editable definition; names and notes remain editable on the original.

```text
calculations/scf_001/
├── calculation.json
├── input/
│   └── revision_001_<id>/
│       ├── pw.in
│       ├── espresso.pwi
│       └── metadata.json
└── runs/
    ├── run_001/
    │   ├── run.json
    │   ├── pw.in
    │   ├── pw.out
    │   ├── exit.json
    │   ├── tmp/
    │   ├── logs/
    │   └── results/
    └── run_002/
```

Each attempt copies the exact generated inputs into its own working directory. Retrying the same calculation creates another attempt and preserves prior logs, outputs, and scratch. Managed prefixes and relative pseudo/scratch paths keep project execution local to that project. Legacy custom scratch behavior remains unchanged.

NSCF and bands require an explicit completed SCF parent; DOS requires a completed NSCF parent. Create NSCF from SCF and then DOS from that NSCF to run the DOS workflow. Children inherit the parent's baseline, physical model, and cutoffs, with supported numerical/workflow overrides. The parent restart tree is hash-verified and copied into child scratch, with a recorded copy receipt. Bands persists two ordered steps (`pw.x`, then `bands.x`). Results select exact run artifacts; no project-wide filename search combines outputs or Fermi metadata.

Project attempts share a single local execution slot across projects and tabs, using the existing `qe_runner` thread engine. Worker callbacks persist statuses, step exit codes, scientific validation, and logs even when the page is closed. Cancellation targets the process group and prevents the next step. This coordinator currently targets Linux local execution, using `flock` and `/proc` process identity; Legacy Workspace keeps its independent execution behavior. There is no persistent daemon, automatic queue dispatch, or automatic retry. After controller restart, uncertain attempts become **interrupted**; a surviving process still blocks a new project execution owner. Closing a browser or switching projects does not cancel a job.

The default suite uses small Python subprocesses with synthetic QE output to verify orchestration. A real-QE smoke test is opt-in and needs `pw.x`, `dos.x`, `bands.x`, and a compatible silicon UPF:

```bash
QE_STUDIO_REAL_QE=1 QE_STUDIO_TEST_UPF=/absolute/path/to/Si.UPF \
  .venv/bin/python -m pytest tests/test_project_execution_real.py -q
```

The local real-QE attempt on 2026-09-15 stalled in Open MPI startup before QE produced output. Real SCF/NSCF/DOS/bands saved-data transfer remains unverified on this host; the opt-in fixture records that gap rather than certifying it through synthetic outputs.

### Project convergence and EOS studies

On **Studies**, create a cutoff, k-point, or EOS experiment against an exact baseline revision. **Save Study Draft** edits the specification with revision checks. **Generate Study Points** freezes it and creates ordinary SCF calculations with `origin=study_point`. Once generation starts, changes require **Duplicate Study**. If generation is interrupted, **Resume Generation** continues the same specification and reuses committed points; partial experiments cannot run until generation finishes.

The initial specification editor accepts JSON. Range controls use the same scientific sweep models as Legacy Workspace. Explicit lists are also supported: `ecutwfc_values_ry`, `grids` (three integers per grid), or EOS `scales`. Cutoff points override only varied cutoff values; k-point points override only the resolved grid. Duplicate effective grids are collapsed, including repeated Gamma requests in 0D mode. Grid sequences must not decrease any axis. EOS requires at least four distinct increasing scales, keeps full-precision identities, and creates a structure plus baseline for each scaled point, preserving the reference baseline's UPFs and settings. Every EOS point is explicitly SCF and uses the frozen reference structure.

```text
studies/cutoff/cutoff_001/
├── manifest.json                  # Specification and point calculation IDs
└── results/
    ├── result_001_<id>/summary.json
    └── result_002_<id>/summary.json

calculations/scf_012/               # A study point's normal calculation
├── input/...
└── runs/run_001/...
```

**Overview / Points / Results / Plot** expose the specification, calculation/run relationships, energy, and status. **Open Calculation** opens a point's normal editor/attempt view. Study points are hidden in the ordinary calculation browser until **Show study calculations** is enabled.

**Run Pending / Failed Points** creates or resumes normal queued attempts through the project coordinator. One execution slot remains owned for the batch, so ordinary project jobs and studies cannot overlap. Failed points do not prevent subsequent points from running; retries preserve earlier attempts and skip points whose latest attempt completed. **Stop Study** cancels the current process group and queued points. After controller restart, queued points require an explicit resume and uncertain running attempts become interrupted; no background daemon or automatic retry was added. Legacy study execution remains independent.

Every **Refresh Study Results** appends a new result revision with exact calculation/run IDs, registered output hashes, parser/algorithm fingerprints, and a timestamp. It uses each point's latest attempt; a failed retry does not silently reuse an older success. The service API additionally accepts an explicit ordinal-to-run selection for historical analysis. Missing or modified outputs produce incomplete/integrity statuses, never an invented running status. Existing cutoff/k-point recommendation algorithms, total-cell EOS fitting, conventional-cell geometry reporting, and Plotly display/export modes are retained. Old results remain browsable without active Setup or available UPF payloads.

An EOS result may contain a fit from at least four successful points while other points remain missing; it is labeled **partial_fit**. **Create Equilibrium Structure** derives an idempotent child from that selected fit and the frozen reference. It does not replace the active structure; use **Structure → Set Active** to adopt it. Applying a recommendation to a new baseline requires a complete, compatible result.

### Compatibility and provenance

Freshness policy **version 1** evaluates the selected item on demand. It reports **current**, **stale**, or **unknown**, with structured reasons and an evaluation key designed for future caching. It does not scan every calculation in the sidebar, persist replacement statuses, or change completed runs. The comparison defaults to the project's active baseline; service callers can supply a specific comparison baseline.

| Dimension | Meaning |
|---|---|
| Execution | Historical queued/running/completed/failed/cancelled/interrupted outcome |
| Freshness | Scientific compatibility with the selected baseline |
| Availability | Whether a required file or restart tree is present/readable |
| Integrity | Whether available bytes match their recorded SHA-256 fingerprint |

Two different baseline IDs can be current when their relevant scientific content matches. The service compares committed structure and pseudo-set fingerprints and the resolved scientific settings. Spin/SOC, active Hubbard U, vdW, occupations, and numerical settings are included; names, notes, display settings, source labels, disabled Hubbard settings, and unused smearing settings are excluded. SCF numerical overrides are compared with the selected baseline's values, so a calculation intentionally using different cutoffs can be stale for that baseline while remaining a valid historical calculation. A structure-only comparison ignores numerical settings.

NSCF/bands/DOS preserve workflow-specific grids and occupation/convergence overrides while inheriting compatibility from their exact parent chain. Study compatibility compares fixed settings: cutoff studies exclude `ecutwfc`/`ecutrho`, k-point studies exclude the swept grid, and EOS points compare the frozen reference baseline rather than their deliberately scaled geometries. Saved study results use this same fixed-experiment projection and expose their exact consumed runs/output hashes. Incomplete provenance yields unknown; missing payload bytes alone do not alter scientific freshness.

**Compatibility, provenance, and file checks** shows typed `parent_run`, `consumes_artifact`, `consumed_run`, `derived_from`, and `recommended_by` relationships. Study membership stays in `study_points`; no duplicate membership edges or arbitrary graph editor were added. Integrity failures show expected and found hashes prominently. A changed UPF set can cause staleness, while damaged bytes in an existing set are an integrity problem and block creating new calculations or generating/running inputs with that set. Historical records remain inspectable.

Missing restart data disables restart-dependent work without hiding an intact historical DOS plot or changing a completed run's status. File auditing may hash large restart trees when the selected run is inspected; there is no background integrity cache in this phase.

**Duplicate using current baseline** creates a new editable calculation using current baseline physics and ordinary SCF numerics. It retains workflow-specific band/DOS controls and supported NSCF/bands grids/occupations. Parent-dependent copies require explicit parent selection again; the old parent is not silently rebound.

For a complete compatible study result, **Apply Recommendation to New Baseline** creates a baseline and its result/field provenance in one transaction. Cutoff results record cutoff fields, k-point results record the grid, and EOS results record the derived structure plus its exact fit-result lineage. Integrity failures block application. Missing original outputs alone do not erase a preserved recommendation: applying it can use the immutable stored summary and source fingerprints, provided current structure/UPF payloads remain valid. Use **Baseline → Set Active Baseline** to adopt the new revision explicitly.

Baseline history shows recommendation origins inherited through unchanged fields, including successive cutoff and grid recommendations. These links record where values came from; they are not a blanket claim that every ancestral experiment remains compatible after later edits. Recommendation application, duplication, and freshness evaluation never rewrite old calculations or study results. Legacy Workspace retains its existing behavior.

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
- Legacy Workspace stores source and active ASE `Atoms` objects in `st.session_state`; a hard browser refresh resets that state. Project mode reloads committed structures and calculations from its database and payloads.
- Legacy generated inputs, logs, study manifests, and provenance metadata remain under `qe_runs/`. Project calculations own separate input snapshots and attempt directories.
- The `Cell Representation` selector (source vs. primitive cell) affects newly generated QE inputs and study baselines. Switching cells requires regenerating `pw.in` and any studies.
- The pseudopotential resolver supports SSSP `1.3.0` (PBE/PBEsol, efficiency/precision) and the SG15 ONCV PBE FR set for SOC calculations.
- SOC calculations require fully relativistic pseudopotentials for all species; mixed scalar+FR sets are blocked before input generation.
- DFT+U uses the legacy `lda_plus_u` path with user-supplied Hubbard U values in eV.
- Spin-orbit coupling does not yet support noncollinear magnetic vector setup (`angle1/angle2`) or collinear `nspin=2` combined with SOC.
- Project studies and ordinary project calculations share the coordinator and runner engine. Legacy studies and the legacy Run page retain their existing behavior and files.
- The EOS workflow performs isotropic lattice scaling via ASE (`atoms.set_cell(..., scale_atoms=True)`), which preserves fractional coordinates.
- V1 analysis supports total DOS and non-projected `filband.gnu` band plots; PDOS and fat bands are not yet implemented.
