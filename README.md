# QE Studio

QE Studio is a Streamlit-based web GUI for working toward Quantum ESPRESSO workflows.

The current version can:
- upload a `.cif` file
- parse the structure with ASE
- store the parsed ASE `Atoms` object in Streamlit session state
- show a persistent project summary in the sidebar across all pages
- use a clean 4-page workflow with top navigation: `Structure`, `Setup`, `Run`, and `Analysis`
- switch between the uploaded cell and a symmetry-reduced primitive cell before generating QE inputs
- render the parsed structure in 3D with `py3Dmol`
- detect unique chemical elements from the uploaded structure
- resolve recommended SSSP pseudopotentials from versioned Materials Cloud manifests and archives
- cache extracted `.UPF` files locally under `qe_runs/pseudopotentials/sssp/`
- collect basic Quantum ESPRESSO input parameters in a form
- generate runnable Quantum ESPRESSO input files in `qe_runs/` once pseudopotentials are resolved
- run `pw.x` locally against the generated `qe_runs/pw.in`
- stream live Quantum ESPRESSO terminal output in the Streamlit UI and save the full log to `qe_runs/pw.out`
- launch dedicated DOS and band-structure follow-up workflows inside `qe_runs/`
- auto-fill a high-symmetry band path from the active cell using ASE when possible
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

Open that URL in your browser, then use the `Structure` page to upload a `.cif` file.

The app now uses top navigation with four pages:
- `Structure`: upload, cell mode, 3D viewer, structure summary
- `Setup`: pseudopotentials, QE input generation, `pw.in` preview
- `Run`: `pw.x`, DOS, bands, live logs, artifact status
- `Analysis`: DOS and band dashboards

Before using `Run Calculation` or the post-processing runners, make sure `pw.x`, `dos.x`, and `bands.x` are still available in the same shell environment that launches Streamlit.

## Quick Test Flow

1. Start the app with `streamlit run app.py`.
2. Confirm the page loads with a wide layout, top navigation, and a persistent sidebar project summary.
3. Open the `Structure` page.
4. Upload a valid `.cif` file.
5. Confirm the sidebar shows:
   - file name
   - chemical formula
   - atom count
   - unit cell parameters
6. Confirm the `Structure` page shows the interactive 3D structure viewer.
7. Optionally switch `Cell Mode` to `Primitive Cell` and confirm the structure metadata updates.
8. Open the `Setup` page and confirm the `Pseudopotentials` section shows the detected elements.
9. Click `Resolve Pseudopotentials`.
10. Confirm the selected SSSP manifest and archive are cached under `qe_runs/pseudopotentials/sssp/1.3.0/...`.
11. Confirm a success message and resolved pseudopotential table appear.
12. Confirm the QE input form appears on the `Setup` page.
13. Change a few values and click `Generate Input File`.
14. Confirm `qe_runs/espresso.pwi` and `qe_runs/pw.in` are created and the generated preview appears.
15. Open the `Run` page and click `Run Calculation`.
16. Confirm live Quantum ESPRESSO output appears in the `Run` page while the process runs.
17. Confirm the final full output is saved to `qe_runs/pw.out`.
18. Confirm the `Run` page shows the post-processing sections for DOS and band structure.
19. Use `Auto-Fill High-Symmetry Path` in the band workflow and confirm the band-path textarea is populated with `crystal_b` rows.
20. Run the DOS workflow and confirm `qe_runs/dos.in`, `qe_runs/dos.out`, and the selected DOS data file are created.
21. Confirm live DOS output appears in the DOS console while `dos.x` runs.
22. Run the band-structure workflow and confirm `qe_runs/bands_pw.in`, `qe_runs/bands_pw.out`, `qe_runs/bands_pp.in`, `qe_runs/bands_pp.out`, `qe_runs/filband`, and `qe_runs/filband.gnu` are created.
23. Confirm live output clearly labels the `bands pw.x` and `bands.x` phases.
24. Open the `Analysis` page.
25. Confirm the top summary row reports DOS detection, band detection, Fermi source, and the run directory.
26. If only `pw.out` or QE XML metadata exists, confirm the page shows an explicit empty-state message explaining that no plottable DOS or band data is available yet.
27. If a DOS file from `dos.x` exists, confirm the interactive DOS plot renders with Plotly and a Fermi-alignment toggle.
28. If a `filband.gnu` file exists, confirm the interactive band-structure plot renders with Plotly and optional high-symmetry separators from the matching band input file.

## Useful Checks

Syntax check:

```bash
python3 -m py_compile app.py electronic_properties.py postprocessing.py pseudopotentials.py qe_runner.py
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

- `app.py`: Streamlit app entrypoint
- `project_context.py`: lightweight shared session/disk summaries and cross-page helpers
- `ui_structure.py`: Structure page UI
- `ui_setup.py`: Setup page UI
- `ui_run.py`: Run page UI
- `ui_analysis.py`: Analysis page UI
- `electronic_properties.py`: QE analysis artifact discovery, parsing, and Plotly figure helpers
- `postprocessing.py`: disk-first QE run inspection, DOS/bands input generation, and output validation helpers
- `pseudopotentials.py`: SSSP pseudopotential resolution and cache helpers
- `structure_tools.py`: primitive-cell reduction and automatic band-path helpers
- `requirements.txt`: Python dependencies
- `HISTORY.md`: running project history and AI context handoff

## Notes

- The app currently loads the first structure block from a CIF file.
- The parsed structure is stored in `st.session_state`, which preserves it across normal Streamlit reruns within the same session.
- A hard browser refresh still resets Streamlit session state.
- Generated inputs, logs, and analysis artifacts remain disk-first under `qe_runs/`, so the `Run` and `Analysis` pages can reconstruct their state from disk after refresh.
- The `Cell Mode` selector affects newly generated QE inputs. If you switch between uploaded and primitive cells, regenerate `pw.in` and rerun the source SCF before post-processing.
- The pseudopotential resolver currently supports SSSP `1.3.0` for `PBE/PBEsol` and `efficiency/precision`.
- QE input generation is blocked until pseudopotentials are resolved for the active structure/settings combination.
- DOS and band post-processing runners are disk-driven and inspect `qe_runs/pw.in` first, then `qe_runs/espresso.pwi` if needed.
- Electronic-properties analysis is disk-driven and looks for DOS and band artifacts inside `qe_runs/`.
- V1 analysis support is limited to total DOS and non-projected `filband.gnu` band plots.
- The app prefers structured QE XML metadata from `prefix.save/data-file-schema.xml` for Fermi alignment and falls back to `pw.out` only when XML metadata is unavailable.
