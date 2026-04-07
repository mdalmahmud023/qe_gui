# QE Studio

QE Studio is a Streamlit-based web GUI for working toward Quantum ESPRESSO workflows.

The current version can:
- import structure files through ASE-backed readers:
  - `.cif`
  - VASP `POSCAR` / `CONTCAR`
  - `.xsf`
  - `.xyz` / `extxyz` when a usable 3D periodic cell is present
- build periodic crystal structures manually from lattice parameters, species, and atomic positions
- generate curated prototype starting structures for:
  - ordered cubic double perovskite `A2BB'X6`
  - cubic perovskite `ABX3`
  - rocksalt `AB`
- store the source and active ASE `Atoms` objects in Streamlit session state
- show a persistent project summary in the sidebar across all pages
- use a clean 4-page workflow with top navigation: `Structure`, `Setup`, `Run`, and `Analysis`
- switch between the source cell and a symmetry-reduced primitive cell before generating QE inputs
- show a compact active-structure banner so the current committed structure stays visible while drafting
- render the active structure in 3D with `py3Dmol`
- inspect the active structure with viewer controls for:
  - labels
  - style mode
  - bonds
  - unit-cell visibility
  - species visibility
  - viewer-only supercell repeat
- detect unique chemical elements from the active structure
- resolve recommended SSSP pseudopotentials from versioned Materials Cloud manifests and archives
- cache extracted `.UPF` files locally under `qe_runs/pseudopotentials/sssp/`
- collect basic Quantum ESPRESSO input parameters in a form
- generate runnable Quantum ESPRESSO input files in `qe_runs/` once pseudopotentials are resolved
- run `pw.x` locally against the generated `qe_runs/pw.in`
- stream live Quantum ESPRESSO terminal output in the Streamlit UI and save the full log to `qe_runs/pw.out`
- launch dedicated DOS and band-structure follow-up workflows inside `qe_runs/`
- auto-fill a high-symmetry band path from the active cell using ASE when possible
- track deterministic structure provenance for pseudopotentials, generated input, and QE artifacts
- show warnings when saved inputs or artifacts are out of sync with the current active structure
- detect QE post-processing artifacts in `qe_runs/`
- render interactive total DOS and `filband.gnu` band-structure charts with Plotly when suitable files exist

## Requirements

- Python 3.12 or another compatible Python 3 version
- `pip`
- Quantum ESPRESSO installed locally with the `pw.x`, `dos.x`, and `bands.x` executables available on your `PATH`

## Install Quantum ESPRESSO

QE Studio can generate input files with Python alone, but the `Run Calculation` button requires a working local Quantum ESPRESSO installation.

Common installation options:

### Debian or Ubuntu

```bash
sudo apt update
sudo apt install quantum-espresso
```

If you encounter missing MPI library errors (e.g., `libmpi_mpifh.so.40: cannot open shared object file`) when running `pw.x`, you will also need to install OpenMPI system dependencies:

```bash
sudo apt install libopenmpi-dev openmpi-bin
```

### Conda

```bash
conda install -c conda-forge qe
```

### From source

If you need a custom build, MPI support, or a newer system-specific build, install Quantum ESPRESSO from source following its upstream build instructions, then make sure the resulting `pw.x` binary is on your `PATH`.

### Verify the installation

Confirm the executable is visible before starting the app:

```bash
which pw.x dos.x bands.x
pw.x -h
```

If one or more executables are missing, add the directory containing the Quantum ESPRESSO binaries to your shell `PATH` before launching Streamlit.

## Setup

If you want to use the existing virtual environment in this project:

```bash
source venv/bin/activate
```

If you want to create a fresh one instead:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

`requirements.txt` installs only the Python packages used by QE Studio. It does not install Quantum ESPRESSO itself.

## Run The App

From the project root, run:

```bash
streamlit run app.py
```

If you are using the bundled virtual environment and do not want to activate it first:

```bash
venv/bin/streamlit run app.py
```

By default Streamlit will print a local URL such as:

```text
http://localhost:8501
```

Open that URL in your browser, then use the `Structure` page to import or build a structure.

The app now uses top navigation with four pages:
- `Structure`: active-structure banner, centered import/build draft workflow, and a full-width inspection workspace with a larger viewer plus side settings
- `Setup`: pseudopotentials, QE input generation, `pw.in` preview
- `Run`: `pw.x`, DOS, bands, live logs, artifact status
- `Analysis`: DOS and band dashboards

Before using `Run Calculation` or the post-processing runners, make sure `pw.x`, `dos.x`, and `bands.x` are still available in the same shell environment that launches Streamlit.

## Quick Test Flow

1. Start the app with `streamlit run app.py`.
2. Confirm the page loads with a wide layout, top navigation, and a persistent sidebar project summary.
3. Open the `Structure` page.
4. Create an active structure using one of the available modes:
   - `Import File`
   - `Manual Builder`
   - `Prototype Builder`
5. If you use `Prototype Builder`, choose a prototype family and build it explicitly.
6. Confirm the active-structure banner updates only after explicit apply and shows:
   - formula
   - atom count
   - source type
   - source label
   - active cell mode
7. Confirm the sidebar shows:
   - source label
   - source type
   - chemical formula
   - atom count
   - unit cell parameters
8. Confirm the `Structure` page shows the interactive 3D structure viewer.
9. Change viewer controls such as labels, style mode, bonds, unit-cell visibility, and species visibility, and confirm the viewer updates without changing the active-structure summary.
10. Optionally switch `Cell Representation` to `Primitive Cell` and confirm the active structure metadata updates.
11. Open the `Setup` page and confirm the `Pseudopotentials` section shows the detected elements.
12. Click `Resolve Pseudopotentials`.
13. Confirm the selected SSSP manifest and archive are cached under `qe_runs/pseudopotentials/sssp/1.3.0/...`.
14. Confirm a success message and resolved pseudopotential table appear.
15. Confirm the QE input form appears on the `Setup` page.
16. Change a few values and click `Generate Input File`.
17. Confirm `qe_runs/espresso.pwi` and `qe_runs/pw.in` are created and the generated preview appears.
18. Open the `Run` page and click `Run Calculation`.
19. Confirm live Quantum ESPRESSO output appears in the `Run` page while the process runs.
20. Confirm the final full output is saved to `qe_runs/pw.out`.
21. Confirm the `Run` page shows the post-processing sections for DOS and band structure.
22. Use `Auto-Fill High-Symmetry Path` in the band workflow and confirm the band-path textarea is populated with `crystal_b` rows.
23. Run the DOS workflow and confirm `qe_runs/dos.in`, `qe_runs/dos.out`, and the selected DOS data file are created.
24. Confirm live DOS output appears in the DOS console while `dos.x` runs.
25. Run the band-structure workflow and confirm `qe_runs/bands_pw.in`, `qe_runs/bands_pw.out`, `qe_runs/bands_pp.in`, `qe_runs/bands_pp.out`, `qe_runs/filband`, and `qe_runs/filband.gnu` are created.
26. Confirm live output clearly labels the `bands pw.x` and `bands.x` phases.
27. Open the `Analysis` page.
28. Confirm the top summary row reports DOS detection, band detection, Fermi source, and the run directory.
29. If only `pw.out` or QE XML metadata exists, confirm the page shows an explicit empty-state message explaining that no plottable DOS or band data is available yet.
30. If a DOS file from `dos.x` exists, confirm the interactive DOS plot renders with Plotly and a Fermi-alignment toggle.
31. If a `filband.gnu` file exists, confirm the interactive band-structure plot renders with Plotly and optional high-symmetry separators from the matching band input file.

## Useful Checks

Syntax check:

```bash
python3 -m compileall qe_studio tests app.py
```

Unit tests:

```bash
python3 -m unittest discover -s tests
```

Headless startup check:

```bash
timeout 10s venv/bin/streamlit run app.py --server.headless true --server.address 127.0.0.1
```

## Current Files

- `app.py`: thin Streamlit entry shim that preserves `streamlit run app.py`
- `qe_studio/core/`: session-state keys, structure/provenance state helpers, workspace constants, and project-context assembly
- `qe_studio/pages/`: Structure, Setup, Run, and Analysis page UIs plus shared page helpers
- `qe_studio/services/electronic_properties.py`: QE analysis artifact discovery, parsing, and Plotly figure helpers
- `qe_studio/services/postprocessing.py`: disk-first QE run inspection, DOS/bands input generation, and output validation helpers
- `qe_studio/services/pseudopotentials.py`: SSSP pseudopotential resolution and cache helpers
- `qe_studio/services/`: QE workflow services such as runner, structure import/build helpers, viewer helpers, and prototype catalog
- `requirements.txt`: Python dependencies
- `HISTORY.md`: running project history and AI context handoff

## Notes

- The app currently loads the first structure block from a supported multi-structure file.
- The source and active structures are stored in `st.session_state`, which preserves them across normal Streamlit reruns within the same session.
- Viewer controls on the `Structure` page are display-only. Species filters, bonds, labels, and supercell repeat do not modify the committed active structure or downstream QE inputs.
- The `Import / Build Draft` panel now uses the same full-width outer box as the other main panels, with the draft controls centered inside it, and the active inspection workspace sits underneath it with a larger visualization pane and a denser settings pane.
- A hard browser refresh still resets Streamlit session state.
- Generated inputs, logs, analysis artifacts, and deterministic provenance metadata remain disk-first under `qe_runs/`, so the `Run` and `Analysis` pages can reconstruct their state from disk after refresh.
- The `Cell Representation` selector affects newly generated QE inputs. If you switch between source and primitive cells, regenerate `pw.in` and rerun the source SCF before post-processing.
- The pseudopotential resolver currently supports SSSP `1.3.0` for `PBE/PBEsol` and `efficiency/precision`.
- QE input generation is blocked until pseudopotentials are resolved for the active structure/settings combination, and previously resolved pseudopotentials are marked stale when the active structure changes.
- DOS and band post-processing runners are disk-driven and inspect `qe_runs/pw.in` first, then `qe_runs/espresso.pwi` if needed.
- Electronic-properties analysis is disk-driven and looks for DOS and band artifacts inside `qe_runs/`.
- The `Setup`, `Run`, and `Analysis` pages show explicit warnings when saved `pw.in` data or QE artifacts are out of sync with the current active structure.
- V1 analysis support is limited to total DOS and non-projected `filband.gnu` band plots.
- The app prefers structured QE XML metadata from `prefix.save/data-file-schema.xml` for Fermi alignment and falls back to `pw.out` only when XML metadata is unavailable.
