from __future__ import annotations

from pathlib import Path

import streamlit as st

from electronic_properties import (
    DEFAULT_PLOTLY_CONFIG,
    AnalysisParseError,
    BandData,
    BandGapSummary,
    DOSData,
    FermiMetadata,
    extract_qe_fermi_metadata,
    infer_band_gap,
    make_band_figure,
    make_dos_figure,
    parse_qe_band_structure,
    parse_qe_dos,
)
from project_context import (
    BAND_ALIGNMENT_MODE_OPTIONS,
    BAND_ALIGNMENT_MODE_WIDGET_KEY,
    DOS_ALIGNMENT_WIDGET_KEY,
    ProjectContext,
)

ANALYSIS_PLOT_COLUMN_WEIGHTS = [0.85, 5.3, 0.85]


def render_page(ctx: ProjectContext) -> None:
    """Render the Analysis page."""
    st.subheader("Analysis")
    st.caption("Review the generated electronic-property artifacts as a clean results dashboard.")

    analysis_files = ctx.analysis_files
    if not analysis_files.has_any_artifact:
        st.info(
            "No electronic-property artifacts are available yet. Run the base calculation and any desired DOS or band workflows from the Run page."
        )
        return

    fermi_metadata = extract_qe_fermi_metadata(analysis_files)
    render_artifact_summary_row(ctx.run_dir, analysis_files, fermi_metadata)

    if analysis_files.is_metadata_only:
        st.info(
            "QE metadata was detected in this run directory, but no plottable DOS or `filband.gnu` data is available yet. Generate DOS and/or band-structure outputs from the Run page."
        )

    st.divider()
    render_dos_subsection(analysis_files)
    st.divider()
    render_band_subsection(analysis_files)


def format_fermi_source_label(fermi_metadata: FermiMetadata | None) -> str:
    """Return a compact UI label for the active Fermi metadata source."""
    if fermi_metadata is None:
        return "Unavailable"
    if fermi_metadata.energy_ev is None:
        return f"{fermi_metadata.source_type} (no single reference)"
    return f"{fermi_metadata.source_type}: {fermi_metadata.reference_kind}"


def make_plotly_chart_config(filename: str) -> dict:
    """Return Plotly export settings with a figure-specific filename."""
    export_options = dict(DEFAULT_PLOTLY_CONFIG["toImageButtonOptions"])
    export_options["filename"] = filename
    config = dict(DEFAULT_PLOTLY_CONFIG)
    config["toImageButtonOptions"] = export_options
    return config


def render_centered_plotly_chart(figure, *, filename: str) -> None:
    """Render an analysis figure in a centered, narrower layout."""
    _left, center, _right = st.columns(ANALYSIS_PLOT_COLUMN_WEIGHTS)
    with center:
        st.plotly_chart(
            figure,
            use_container_width=True,
            config=make_plotly_chart_config(filename),
            theme=None,
        )


def render_artifact_summary_row(run_dir: Path, analysis_files, fermi_metadata: FermiMetadata | None) -> None:
    """Render a compact summary of detected analysis artifacts."""
    col1, col2, col3, col4 = st.columns(4)
    col1.caption("DOS detected")
    col1.write("Yes" if analysis_files.dos_detected else "No")
    col2.caption("Bands detected")
    col2.write("Yes" if analysis_files.bands_detected else "No")
    col3.caption("Fermi source")
    col3.write(format_fermi_source_label(fermi_metadata))
    col4.caption("Run directory")
    col4.write(str(run_dir))


def render_dos_summary(dos_data: DOSData) -> None:
    """Render compact numeric DOS metadata."""
    col1, col2, col3, col4 = st.columns(4)
    col1.caption("Source file")
    col1.write(str(dos_data.source_file))
    col2.caption("Fermi energy")
    if dos_data.fermi_energy_ev is None:
        col2.write("Unavailable")
    else:
        col2.write(f"{dos_data.fermi_energy_ev:.4f} eV")
    col3.caption("Spin polarized")
    col3.write("Yes" if dos_data.spin_polarized else "No")
    col4.caption("Energy window")
    col4.write(f"{min(dos_data.energies_ev):.3f} to {max(dos_data.energies_ev):.3f} eV")


def render_band_summary(band_data: BandData) -> None:
    """Render compact numeric band metadata."""
    n_kpoints = len(band_data.distances)
    n_bands = len(band_data.eigenvalues_ev[0]) if band_data.eigenvalues_ev else 0
    flattened_energies = [value for row in band_data.eigenvalues_ev for value in row]

    col1, col2, col3, col4 = st.columns(4)
    col1.caption("Source file")
    col1.write(str(band_data.source_file))
    col2.caption("Fermi energy")
    if band_data.fermi_energy_ev is None:
        col2.write("Unavailable")
    else:
        col2.write(f"{band_data.fermi_energy_ev:.4f} eV")
    col3.caption("Bands / k-points")
    col3.write(f"{n_bands} / {n_kpoints}")
    col4.caption("Energy window")
    col4.write(f"{min(flattened_energies):.3f} to {max(flattened_energies):.3f} eV")


def render_band_gap_summary(band_gap: BandGapSummary | None) -> None:
    """Render a compact band-gap summary derived from the parsed band structure."""
    col1, col2, col3, col4 = st.columns(4)
    col1.caption("Band gap")
    col2.caption("Gap type")
    col3.caption("VBM")
    col4.caption("CBM")

    if band_gap is None:
        col1.write("Unavailable")
        col2.write("Unavailable")
        col3.write("Unavailable")
        col4.write("Unavailable")
        return

    col1.write(f"{band_gap.gap_energy_ev:.4f} eV")
    if band_gap.is_metallic:
        col2.write("Metallic / zero-gap")
    else:
        col2.write("Direct" if band_gap.is_direct else "Indirect")
    col3.write(f"{band_gap.vbm_energy_ev:.4f} eV at k={band_gap.vbm_distance:.3f}")
    col4.write(f"{band_gap.cbm_energy_ev:.4f} eV at k={band_gap.cbm_distance:.3f}")


def get_default_band_alignment_mode(
    band_data: BandData,
    band_gap: BandGapSummary | None,
) -> str:
    """Choose a sensible default energy reference for the current band plot."""
    if band_gap is not None and not band_gap.is_metallic:
        return "vbm"
    if band_data.fermi_energy_ev is not None:
        return "fermi"
    return "raw"


def resolve_band_alignment_mode(
    requested_mode: str,
    band_data: BandData,
    band_gap: BandGapSummary | None,
) -> tuple[str, str | None]:
    """Map a requested UI alignment mode onto a valid plotting mode plus any UX note."""
    normalized_mode = requested_mode.strip().lower()
    if normalized_mode not in BAND_ALIGNMENT_MODE_OPTIONS:
        normalized_mode = "raw"

    if normalized_mode == "vbm":
        if band_gap is None:
            fallback_mode = "fermi" if band_data.fermi_energy_ev is not None else "raw"
            return (
                fallback_mode,
                "VBM alignment requires a detectable valence-band maximum and conduction-band minimum.",
            )
        if band_gap.is_metallic:
            fallback_mode = "fermi" if band_data.fermi_energy_ev is not None else "raw"
            return (
                fallback_mode,
                "VBM alignment is not used for metallic or zero-gap band structures, so the plot falls back to the available reference.",
            )

    if normalized_mode == "fermi" and band_data.fermi_energy_ev is None:
        return "raw", "No single Fermi reference was found, so raw band energies are shown."

    return normalized_mode, None


def render_dos_subsection(analysis_files) -> None:
    """Render the DOS subsection with graceful parsing failure handling."""
    st.markdown("### DOS")
    if analysis_files.dos_file is None:
        st.info("DOS data has not been generated yet. Run QE DOS post-processing from the Run page to produce a DOS file.")
        return

    try:
        dos_data = parse_qe_dos(analysis_files.dos_file, analysis_files=analysis_files)
    except AnalysisParseError as exc:
        st.error(f"Failed to parse DOS data from `{analysis_files.dos_file}`: {exc}")
        return
    except Exception as exc:
        st.error(f"Unexpected DOS parsing error for `{analysis_files.dos_file}`: {exc}")
        return

    render_dos_summary(dos_data)

    if dos_data.fermi_metadata is not None:
        st.caption(
            "Fermi metadata source: "
            f"`{dos_data.fermi_metadata.source_file}` ({dos_data.fermi_metadata.source_type})"
        )
        if dos_data.fermi_metadata.message:
            st.info(dos_data.fermi_metadata.message)

    align_to_fermi = st.checkbox(
        "Align DOS to Fermi level (0 eV)",
        value=dos_data.fermi_energy_ev is not None,
        disabled=dos_data.fermi_energy_ev is None,
        key=DOS_ALIGNMENT_WIDGET_KEY,
    )
    if dos_data.fermi_energy_ev is None:
        st.info("No Fermi energy was found for DOS alignment, so raw energies are shown.")

    figure = make_dos_figure(dos_data, align_to_fermi=align_to_fermi)
    render_centered_plotly_chart(figure, filename="qe_studio_dos")


def render_band_subsection(analysis_files) -> None:
    """Render the band-structure subsection with graceful parsing failure handling."""
    st.markdown("### Band Structure")
    if analysis_files.band_file is None:
        st.info(
            "Band-structure data is unavailable. A dedicated band calculation and `filband.gnu` output are required."
        )
        return

    try:
        band_data = parse_qe_band_structure(
            analysis_files.band_file,
            band_input_file=analysis_files.band_input_file,
            analysis_files=analysis_files,
        )
    except AnalysisParseError as exc:
        st.error(f"Failed to parse band data from `{analysis_files.band_file}`: {exc}")
        return
    except Exception as exc:
        st.error(f"Unexpected band parsing error for `{analysis_files.band_file}`: {exc}")
        return

    band_gap = infer_band_gap(band_data)

    render_band_summary(band_data)
    render_band_gap_summary(band_gap)

    if band_data.fermi_metadata is not None:
        st.caption(
            "Fermi metadata source: "
            f"`{band_data.fermi_metadata.source_file}` ({band_data.fermi_metadata.source_type})"
        )
        if band_data.fermi_metadata.message:
            st.info(band_data.fermi_metadata.message)
    if analysis_files.band_input_file is not None:
        st.caption(f"Band-path input: `{analysis_files.band_input_file}`")

    default_alignment_mode = get_default_band_alignment_mode(band_data, band_gap)
    requested_alignment_mode = st.selectbox(
        "Energy reference",
        options=BAND_ALIGNMENT_MODE_OPTIONS,
        index=BAND_ALIGNMENT_MODE_OPTIONS.index(default_alignment_mode),
        format_func=lambda mode: {
            "vbm": "Align to VBM",
            "fermi": "Align to Fermi",
            "raw": "Raw energies",
        }[mode],
        key=BAND_ALIGNMENT_MODE_WIDGET_KEY,
    )
    alignment_mode, alignment_note = resolve_band_alignment_mode(
        requested_alignment_mode,
        band_data,
        band_gap,
    )
    if alignment_note:
        st.info(alignment_note)

    figure = make_band_figure(
        band_data,
        alignment_mode=alignment_mode,
        band_gap=band_gap,
    )
    render_centered_plotly_chart(figure, filename="qe_studio_bands")
