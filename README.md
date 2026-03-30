# QE Studio

QE Studio is a Streamlit-based web GUI for working toward Quantum ESPRESSO workflows.

The current version can:
- upload a `.cif` file
- parse the structure with ASE
- store the parsed ASE `Atoms` object in Streamlit session state
- show basic structure metadata in the sidebar
- render the parsed structure in 3D with `py3Dmol`
- detect unique chemical elements from the uploaded structure
- resolve recommended SSSP pseudopotentials from versioned Materials Cloud manifests and archives
- cache extracted `.UPF` files locally under `qe_runs/pseudopotentials/sssp/`
- collect basic Quantum ESPRESSO input parameters in a form
- generate runnable Quantum ESPRESSO input files in `qe_runs/` once pseudopotentials are resolved
- run `pw.x` locally against the generated `qe_runs/pw.in`
- stream live Quantum ESPRESSO terminal output in the Streamlit UI and save the full log to `qe_runs/pw.out`

## Requirements

- Python 3.12 or another compatible Python 3 version
- `pip`
- Quantum ESPRESSO installed locally with the `pw.x` executable available on your `PATH`

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
which pw.x
pw.x -h
```

If `which pw.x` prints nothing, add the directory containing `pw.x` to your shell `PATH` before launching Streamlit.

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

Open that URL in your browser, then upload a `.cif` file from the sidebar.

Before using `Run Calculation`, make sure `pw.x` is still available in the same shell environment that launches Streamlit.

## Quick Test Flow

1. Start the app with `streamlit run app.py`.
2. Confirm the page loads with a wide layout.
3. Upload a valid `.cif` file from the sidebar.
4. Confirm the sidebar shows:
   - file name
   - chemical formula
   - atom count
   - unit cell parameters
5. Confirm the main area shows the interactive 3D structure viewer.
6. Confirm the `Pseudopotentials` section appears below the viewer and shows the detected elements.
7. Click `Resolve Pseudopotentials`.
8. Confirm the selected SSSP manifest and archive are cached under `qe_runs/pseudopotentials/sssp/1.3.0/...`.
9. Confirm a success message and resolved pseudopotential table appear.
10. Confirm the QE input form appears below the pseudopotential section.
11. Change a few values and click `Generate Input File`.
12. Confirm a success message appears and the generated QE input text appears in a code block.
13. Confirm `qe_runs/espresso.pwi` and `qe_runs/pw.in` are created.
14. Click `Run Calculation`.
15. Confirm live Quantum ESPRESSO output appears in the app while the process runs.
16. Confirm the final full output is saved to `qe_runs/pw.out`.

## Useful Checks

Syntax check:

```bash
python3 -m py_compile app.py pseudopotentials.py
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
- `pseudopotentials.py`: SSSP pseudopotential resolution and cache helpers
- `requirements.txt`: Python dependencies
- `HISTORY.md`: running project history and AI context handoff

## Notes

- The app currently loads the first structure block from a CIF file.
- The parsed structure is stored in `st.session_state`, which preserves it across normal Streamlit reruns within the same session.
- A hard browser refresh still resets Streamlit session state.
- The pseudopotential resolver currently supports SSSP `1.3.0` for `PBE/PBEsol` and `efficiency/precision`.
- QE input generation is blocked until pseudopotentials are resolved for the active structure/settings combination.
