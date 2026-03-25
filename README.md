# QE Studio

QE Studio is a Streamlit-based web GUI for working toward Quantum ESPRESSO workflows.

The current version can:
- upload a `.cif` file
- parse the structure with ASE
- store the parsed ASE `Atoms` object in Streamlit session state
- show basic structure metadata in the sidebar
- render the parsed structure in 3D with `py3Dmol`
- collect basic Quantum ESPRESSO input parameters in a form
- generate preview Quantum ESPRESSO input files in `qe_runs/`

## Requirements

- Python 3.12 or another compatible Python 3 version
- `pip`

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
6. Confirm the QE input form appears below the viewer.
7. Change a few values and click `Generate Input File`.
8. Confirm a success message appears and a preview warning is shown.
9. Confirm the generated QE input text appears in a code block.
10. Confirm `qe_runs/espresso.pwi` and `qe_runs/pw.in` are created.

## Useful Checks

Syntax check:

```bash
python3 -m py_compile app.py
```

Headless startup check:

```bash
timeout 10s venv/bin/streamlit run app.py --server.headless true --server.address 127.0.0.1
```

## Current Files

- `app.py`: Streamlit app entrypoint
- `requirements.txt`: Python dependencies
- `HISTORY.md`: running project history and AI context handoff

## Notes

- The app currently loads the first structure block from a CIF file.
- The parsed structure is stored in `st.session_state`, which preserves it across normal Streamlit reruns within the same session.
- A hard browser refresh still resets Streamlit session state.
- The QE parameter form stores values in session state and generates preview QE input files through ASE.
- The generated input is preview-only until real pseudopotential filenames are supplied.
