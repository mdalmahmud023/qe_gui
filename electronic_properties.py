from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
from typing import Iterable
import xml.etree.ElementTree as ET

import plotly.graph_objects as go


HARTREE_TO_EV = 27.211386245988
DEFAULT_PREFIX = "pwscf"
DEFAULT_QE_OUTPUT = "pw.out"
PUBLICATION_BLUE = "#124E8C"
PUBLICATION_TEXT = "#111827"
PUBLICATION_MUTED = "#4B5563"
PUBLICATION_GRID = "#D1D5DB"
PUBLICATION_SEPARATOR = "#A8B0BD"
PUBLICATION_DOS_HEIGHT = 500
PUBLICATION_BAND_HEIGHT = 600
DEFAULT_PLOTLY_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "toImageButtonOptions": {
        "format": "png",
        "scale": 2,
    },
}


class AnalysisParseError(ValueError):
    """Raised when an analysis artifact cannot be parsed safely."""


@dataclass(frozen=True)
class FermiMetadata:
    energy_ev: float | None
    source_file: Path
    source_type: str
    reference_kind: str
    message: str | None = None


@dataclass(frozen=True)
class DOSData:
    energies_ev: list[float]
    dos_total: list[float]
    source_file: Path
    source_type: str
    fermi_energy_ev: float | None = None
    spin_polarized: bool = False
    fermi_metadata: FermiMetadata | None = None


@dataclass(frozen=True)
class BandData:
    distances: list[float]
    eigenvalues_ev: list[list[float]]
    source_file: Path
    source_type: str
    kpoint_tick_positions: list[float]
    kpoint_tick_labels: list[str]
    fermi_energy_ev: float | None = None
    fermi_metadata: FermiMetadata | None = None


@dataclass(frozen=True)
class BandGapSummary:
    vbm_energy_ev: float
    cbm_energy_ev: float
    gap_energy_ev: float
    vbm_kpoint_index: int
    cbm_kpoint_index: int
    vbm_distance: float
    cbm_distance: float
    is_direct: bool
    is_metallic: bool = False


@dataclass(frozen=True)
class AnalysisFiles:
    run_dir: Path
    qe_input_file: Path | None = None
    dos_file: Path | None = None
    band_file: Path | None = None
    band_input_file: Path | None = None
    xml_metadata_file: Path | None = None
    pw_output_file: Path | None = None

    @property
    def dos_detected(self) -> bool:
        return self.dos_file is not None

    @property
    def bands_detected(self) -> bool:
        return self.band_file is not None

    @property
    def metadata_detected(self) -> bool:
        return self.xml_metadata_file is not None or self.pw_output_file is not None

    @property
    def has_any_artifact(self) -> bool:
        return self.dos_detected or self.bands_detected or self.metadata_detected

    @property
    def is_metadata_only(self) -> bool:
        return self.metadata_detected and not self.dos_detected and not self.bands_detected


def find_available_analysis_files(run_dir: Path) -> AnalysisFiles:
    """Discover plottable QE analysis artifacts and metadata in a run directory."""
    run_dir = Path(run_dir)
    qe_input_file = _find_qe_input_file(run_dir)
    prefix, outdir = _parse_qe_prefix_and_outdir(qe_input_file, run_dir)

    candidate_files = list(_iter_run_files(run_dir))
    dos_file = _pick_best_dos_file(candidate_files)
    band_file = _pick_best_band_file(candidate_files)
    band_input_file = _find_band_input_file(band_file, candidate_files) if band_file else None
    xml_metadata_file = _find_xml_metadata_file(run_dir, prefix, outdir)
    pw_output_file = run_dir / DEFAULT_QE_OUTPUT
    if not pw_output_file.is_file():
        pw_output_file = None

    return AnalysisFiles(
        run_dir=run_dir,
        qe_input_file=qe_input_file,
        dos_file=dos_file,
        band_file=band_file,
        band_input_file=band_input_file,
        xml_metadata_file=xml_metadata_file,
        pw_output_file=pw_output_file,
    )


def extract_qe_fermi_metadata(analysis_files: AnalysisFiles) -> FermiMetadata | None:
    """Return the preferred Fermi metadata for a run, if available."""
    if analysis_files.xml_metadata_file is not None:
        metadata = _extract_fermi_from_xml(analysis_files.xml_metadata_file)
        if metadata is not None:
            return metadata

    if analysis_files.pw_output_file is not None:
        metadata = _extract_fermi_from_pw_out(analysis_files.pw_output_file)
        if metadata is not None:
            return metadata

    return None


def parse_qe_dos(dos_file: Path, analysis_files: AnalysisFiles | None = None) -> DOSData:
    """Parse a QE DOS text file into a structured total-DOS model."""
    dos_file = Path(dos_file)
    if not dos_file.is_file():
        raise AnalysisParseError(f"DOS file not found: {dos_file}")

    header_lines: list[str] = []
    numeric_rows: list[list[float]] = []

    for raw_line in dos_file.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if _is_comment_line(stripped):
            header_lines.append(stripped)
            continue

        parts = stripped.split()
        try:
            numeric_rows.append([float(part) for part in parts])
        except ValueError as exc:
            raise AnalysisParseError(
                f"Malformed DOS row in {dos_file.name}: {raw_line!r}"
            ) from exc

    if not numeric_rows:
        raise AnalysisParseError(f"No numeric DOS data found in {dos_file.name}.")

    column_counts = {len(row) for row in numeric_rows}
    if len(column_counts) != 1:
        raise AnalysisParseError(f"Inconsistent DOS column counts in {dos_file.name}.")

    column_count = column_counts.pop()
    if column_count < 2:
        raise AnalysisParseError(f"DOS data in {dos_file.name} must have at least two columns.")

    spin_polarized = False
    energies_ev = [row[0] for row in numeric_rows]

    if column_count == 2:
        dos_total = [row[1] for row in numeric_rows]
    elif column_count == 3:
        dos_total = [row[1] for row in numeric_rows]
    else:
        spin_columns = _infer_spin_resolved_dos_columns(header_lines, column_count)
        if spin_columns is None:
            raise AnalysisParseError(
                f"Ambiguous {column_count}-column DOS file {dos_file.name}; "
                "only explicit spin-resolved total-DOS headers are accepted."
            )
        up_index, down_index = spin_columns
        dos_total = [row[up_index] + row[down_index] for row in numeric_rows]
        spin_polarized = True

    fermi_metadata = _extract_fermi_from_header_lines(header_lines, dos_file)
    if fermi_metadata is None and analysis_files is not None:
        fermi_metadata = extract_qe_fermi_metadata(analysis_files)

    return DOSData(
        energies_ev=energies_ev,
        dos_total=dos_total,
        source_file=dos_file,
        source_type="qe_dos",
        fermi_energy_ev=fermi_metadata.energy_ev if fermi_metadata else None,
        spin_polarized=spin_polarized,
        fermi_metadata=fermi_metadata,
    )


def parse_qe_band_structure(
    band_file: Path,
    *,
    band_input_file: Path | None = None,
    analysis_files: AnalysisFiles | None = None,
) -> BandData:
    """Parse an observed QE filband.gnu-style file into a structured band model."""
    band_file = Path(band_file)
    if not band_file.is_file():
        raise AnalysisParseError(f"Band-structure file not found: {band_file}")

    band_blocks: list[list[tuple[float, float]]] = []
    current_block: list[tuple[float, float]] = []

    for raw_line in band_file.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if current_block:
                band_blocks.append(current_block)
                current_block = []
            continue

        parts = stripped.split()
        if len(parts) != 2:
            raise AnalysisParseError(
                f"Expected two columns in {band_file.name}, found {len(parts)} in line {raw_line!r}."
            )

        try:
            distance = float(parts[0])
            energy = float(parts[1])
        except ValueError as exc:
            raise AnalysisParseError(
                f"Malformed band row in {band_file.name}: {raw_line!r}"
            ) from exc

        current_block.append((distance, energy))

    if current_block:
        band_blocks.append(current_block)

    if not band_blocks:
        raise AnalysisParseError(f"No band blocks found in {band_file.name}.")

    block_lengths = {len(block) for block in band_blocks}
    if len(block_lengths) != 1:
        raise AnalysisParseError(f"Inconsistent band block lengths in {band_file.name}.")

    distances = [point[0] for point in band_blocks[0]]
    if len(distances) < 2:
        raise AnalysisParseError(f"Band data in {band_file.name} must contain multiple k-points.")

    for block_index, block in enumerate(band_blocks[1:], start=1):
        block_distances = [point[0] for point in block]
        if not _sequences_close(distances, block_distances):
            raise AnalysisParseError(
                f"Band block {block_index + 1} in {band_file.name} does not match the reference k-grid."
            )

    eigenvalues_ev = [
        [band_blocks[band_index][kpoint_index][1] for band_index in range(len(band_blocks))]
        for kpoint_index in range(len(distances))
    ]

    tick_positions: list[float] = []
    tick_labels: list[str] = []
    if band_input_file is not None:
        tick_positions, tick_labels = parse_band_input_ticks(band_input_file, distances)

    fermi_metadata = extract_qe_fermi_metadata(analysis_files) if analysis_files is not None else None

    return BandData(
        distances=distances,
        eigenvalues_ev=eigenvalues_ev,
        source_file=band_file,
        source_type="qe_filband_gnu",
        kpoint_tick_positions=tick_positions,
        kpoint_tick_labels=tick_labels,
        fermi_energy_ev=fermi_metadata.energy_ev if fermi_metadata else None,
        fermi_metadata=fermi_metadata,
    )


def parse_band_input_ticks(band_input_file: Path, distances: list[float]) -> tuple[list[float], list[str]]:
    """Parse K_POINTS *_b cards for high-symmetry separators and optional labels."""
    band_input_file = Path(band_input_file)
    if not band_input_file.is_file():
        return [], []

    lines = band_input_file.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^\s*K_POINTS\s+([A-Za-z0-9_]+)", line, re.IGNORECASE)
        if match is None:
            continue

        mode = match.group(1).lower()
        if mode not in {"crystal_b", "tpiba_b"}:
            return [], []

        count_index = _next_nonempty_line_index(lines, index + 1)
        if count_index is None:
            return [], []

        try:
            point_count = int(lines[count_index].strip().split()[0])
        except ValueError:
            return [], []

        points: list[tuple[int, str | None]] = []
        data_index = count_index + 1
        while data_index < len(lines) and len(points) < point_count:
            raw_line = lines[data_index]
            data_index += 1
            stripped = raw_line.strip()
            if not stripped:
                continue

            label = None
            if "!" in raw_line:
                body, comment = raw_line.split("!", 1)
                label = comment.strip() or None
            elif "#" in raw_line:
                body, comment = raw_line.split("#", 1)
                label = comment.strip() or None
            else:
                body = raw_line

            body_parts = body.split()
            if len(body_parts) < 4:
                return [], []

            try:
                segment_count = int(float(body_parts[3]))
            except ValueError:
                return [], []

            points.append((segment_count, label))

        if len(points) != point_count or len(points) < 2:
            return [], []

        total_segment_points = sum(segment_count for segment_count, _label in points[:-1])
        if total_segment_points != len(distances) - 1:
            return [], []

        boundary_indices = [0]
        cumulative_index = 0
        for segment_count, _label in points[:-1]:
            cumulative_index += segment_count
            boundary_indices.append(min(cumulative_index, len(distances) - 1))

        tick_positions = [distances[idx] for idx in boundary_indices]
        tick_labels = [label or "" for _segment_count, label in points]
        if any(label for label in tick_labels):
            return tick_positions, tick_labels
        return tick_positions, []

    return [], []


def make_dos_figure(dos_data: DOSData, *, align_to_fermi: bool = True) -> go.Figure:
    """Build a publication-friendly Plotly figure for total DOS."""
    x_values = _shifted_values(dos_data.energies_ev, dos_data.fermi_energy_ev, align_to_fermi)
    reference_x = 0.0 if align_to_fermi and dos_data.fermi_energy_ev is not None else dos_data.fermi_energy_ev

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=x_values,
            y=dos_data.dos_total,
            mode="lines",
            name="Total DOS",
            line={"color": PUBLICATION_BLUE, "width": 2.4},
            hovertemplate="Energy: %{x:.3f} eV<br>DOS: %{y:.4f}<extra></extra>",
        )
    )

    figure.update_layout(
        title="Density of States",
        xaxis_title="Energy (eV)",
        yaxis_title="DOS",
        showlegend=False,
        **_base_plot_layout(height=PUBLICATION_DOS_HEIGHT),
    )

    if reference_x is not None:
        figure.add_vline(
            x=reference_x,
            line_dash="dash",
            line_color=PUBLICATION_MUTED,
            line_width=1.4,
        )

    return figure


def infer_band_gap(band_data: BandData, *, tolerance_ev: float = 1.0e-4) -> BandGapSummary | None:
    """Infer a simple band-gap summary from a parsed band structure and its energy reference."""
    if band_data.fermi_energy_ev is None or not band_data.eigenvalues_ev:
        return None

    reference_energy = band_data.fermi_energy_ev
    states: list[tuple[float, int, float]] = []
    for kpoint_index, (distance, energies) in enumerate(zip(band_data.distances, band_data.eigenvalues_ev)):
        for energy in energies:
            states.append((energy, kpoint_index, distance))

    occupied_states = [state for state in states if state[0] <= reference_energy + tolerance_ev]
    unoccupied_states = [state for state in states if state[0] > reference_energy + tolerance_ev]
    if not occupied_states or not unoccupied_states:
        return None

    band_energies = list(zip(*band_data.eigenvalues_ev))
    is_metallic = any(
        min(band) <= reference_energy + tolerance_ev and max(band) >= reference_energy - tolerance_ev
        for band in band_energies
    )

    vbm_energy, vbm_kpoint_index, vbm_distance = max(occupied_states, key=lambda state: state[0])
    cbm_energy, cbm_kpoint_index, cbm_distance = min(unoccupied_states, key=lambda state: state[0])
    gap_energy = max(cbm_energy - vbm_energy, 0.0)
    if is_metallic:
        gap_energy = 0.0

    return BandGapSummary(
        vbm_energy_ev=vbm_energy,
        cbm_energy_ev=cbm_energy,
        gap_energy_ev=gap_energy,
        vbm_kpoint_index=vbm_kpoint_index,
        cbm_kpoint_index=cbm_kpoint_index,
        vbm_distance=vbm_distance,
        cbm_distance=cbm_distance,
        is_direct=vbm_kpoint_index == cbm_kpoint_index,
        is_metallic=is_metallic,
    )


def make_band_figure(
    band_data: BandData,
    *,
    alignment_mode: str = "fermi",
    band_gap: BandGapSummary | None = None,
) -> go.Figure:
    """Build a publication-friendly Plotly figure for band structure."""
    figure = go.Figure()
    energy_shift, reference_y, yaxis_title = _resolve_band_alignment(
        band_data,
        alignment_mode=alignment_mode,
        band_gap=band_gap,
    )

    for band_index, band_energies in enumerate(zip(*band_data.eigenvalues_ev), start=1):
        shifted = _shifted_values(list(band_energies), energy_shift, energy_shift is not None)
        figure.add_trace(
            go.Scatter(
                x=band_data.distances,
                y=shifted,
                mode="lines",
                line={"color": PUBLICATION_BLUE, "width": 1.3},
                name=f"Band {band_index}",
                hovertemplate="k-distance: %{x:.3f}<br>Energy: %{y:.3f} eV<extra></extra>",
                showlegend=False,
            )
        )

    figure.update_layout(
        title="Band Structure",
        xaxis_title="k-path distance",
        yaxis_title=yaxis_title,
        showlegend=False,
        **_base_plot_layout(height=PUBLICATION_BAND_HEIGHT),
    )

    if reference_y is not None:
        figure.add_hline(
            y=reference_y,
            line_dash="dash",
            line_color=PUBLICATION_MUTED,
            line_width=1.4,
        )

    if band_data.kpoint_tick_positions:
        for separator_position in band_data.kpoint_tick_positions[1:-1]:
            figure.add_vline(
                x=separator_position,
                line_dash="dot",
                line_color=PUBLICATION_SEPARATOR,
                line_width=1.0,
            )

        if band_data.kpoint_tick_labels:
            figure.update_xaxes(
                tickmode="array",
                tickvals=band_data.kpoint_tick_positions,
                ticktext=band_data.kpoint_tick_labels,
            )

    return figure


def _resolve_band_alignment(
    band_data: BandData,
    *,
    alignment_mode: str,
    band_gap: BandGapSummary | None,
) -> tuple[float | None, float | None, str]:
    normalized_mode = alignment_mode.strip().lower()
    if normalized_mode == "vbm" and band_gap is not None:
        return band_gap.vbm_energy_ev, 0.0, "Energy relative to VBM (eV)"
    if normalized_mode == "fermi" and band_data.fermi_energy_ev is not None:
        return band_data.fermi_energy_ev, 0.0, "Energy relative to Fermi (eV)"
    if normalized_mode == "raw":
        return None, band_data.fermi_energy_ev, "Energy (eV)"
    return None, band_data.fermi_energy_ev, "Energy (eV)"


def _iter_run_files(run_dir: Path) -> Iterable[Path]:
    skipped_directories = {"pseudopotentials", "__pycache__", ".git", ".pytest_cache"}
    for root, dirnames, filenames in os.walk(run_dir):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in skipped_directories and not dirname.endswith(".save")
        ]
        root_path = Path(root)
        for filename in filenames:
            yield root_path / filename


def _find_qe_input_file(run_dir: Path) -> Path | None:
    for candidate_name in ("pw.in", "espresso.pwi"):
        candidate = run_dir / candidate_name
        if candidate.is_file():
            return candidate
    return None


def _parse_qe_prefix_and_outdir(qe_input_file: Path | None, run_dir: Path) -> tuple[str, Path]:
    prefix = DEFAULT_PREFIX
    outdir = run_dir
    if qe_input_file is None or not qe_input_file.is_file():
        return prefix, outdir

    text = qe_input_file.read_text(encoding="utf-8")
    prefix_match = re.search(r"^\s*prefix\s*=\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE | re.MULTILINE)
    if prefix_match:
        prefix = prefix_match.group(1).strip() or DEFAULT_PREFIX

    outdir_match = re.search(r"^\s*outdir\s*=\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE | re.MULTILINE)
    if outdir_match:
        raw_outdir = outdir_match.group(1).strip()
        outdir = Path(raw_outdir)
        if not outdir.is_absolute():
            outdir = run_dir / outdir

    return prefix, outdir


def _find_xml_metadata_file(run_dir: Path, prefix: str, outdir: Path) -> Path | None:
    save_directories: list[Path] = []
    save_dir = outdir / f"{prefix}.save"
    save_directories.append(save_dir)

    fallback_save_dir = run_dir / f"{prefix}.save"
    if fallback_save_dir not in save_directories:
        save_directories.append(fallback_save_dir)

    for candidate_save_dir in save_directories:
        schema_file = candidate_save_dir / "data-file-schema.xml"
        if schema_file.is_file():
            return schema_file

        if candidate_save_dir.is_dir():
            xml_candidates = sorted(
                path for path in candidate_save_dir.glob("*.xml") if path.is_file()
            )
            if len(xml_candidates) == 1:
                return xml_candidates[0]

    return None


def _pick_best_dos_file(paths: Iterable[Path]) -> Path | None:
    candidates: list[tuple[int, float, Path]] = []
    for path in paths:
        score = _score_dos_candidate(path)
        if score is not None:
            candidates.append((score, path.stat().st_mtime, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _pick_best_band_file(paths: Iterable[Path]) -> Path | None:
    candidates: list[tuple[int, float, Path]] = []
    for path in paths:
        score = _score_band_candidate(path)
        if score is not None:
            candidates.append((score, path.stat().st_mtime, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _find_band_input_file(band_file: Path, paths: Iterable[Path]) -> Path | None:
    band_dir = band_file.parent
    candidates: list[tuple[int, float, Path]] = []
    for path in paths:
        if path.parent != band_dir or not path.is_file():
            continue

        name = path.name.lower()
        if not name.endswith(".in") or "band" not in name or name.startswith("log."):
            continue

        score = _common_prefix_score(path.stem.lower(), band_file.stem.lower())
        if _contains_band_kpoints_card(path):
            score += 20
        if name.endswith("bands_pw.in") or "_pw" in name:
            score += 10
        if name.endswith("bands_pp.in") or "_pp" in name:
            score -= 5
        candidates.append((score, path.stat().st_mtime, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _contains_band_kpoints_card(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return bool(re.search(r"^\s*K_POINTS\s+(crystal_b|tpiba_b)\b", text, re.IGNORECASE | re.MULTILINE))


def _score_dos_candidate(path: Path) -> int | None:
    if not path.is_file():
        return None

    name = path.name.lower()
    suffix = path.suffix.lower()
    if "dos" not in name or "pdos" in name or "projwfc" in name:
        return None
    if suffix in {".in", ".out", ".png", ".jpg", ".jpeg", ".pdf", ".py"}:
        return None
    if name.endswith(".gnu") or name.startswith("plot"):
        return None

    score = 10
    if name.endswith(".dos"):
        score += 10
    if name.endswith(".dos.dat") or ".dos." in name:
        score += 8
    if "total" in name:
        score += 3
    return score


def _score_band_candidate(path: Path) -> int | None:
    if not path.is_file():
        return None

    name = path.name.lower()
    if not name.endswith(".gnu"):
        return None
    if "band" not in name:
        return None
    if name.startswith("plot") or "plot_" in name:
        return None

    score = 10
    if name == "filband.gnu":
        score += 10
    if ".bands." in name or ".band." in name:
        score += 6
    return score


def _extract_fermi_from_header_lines(header_lines: list[str], source_file: Path) -> FermiMetadata | None:
    for line in header_lines:
        match = re.search(
            r"fermi(?:\s+energy)?[^-+0-9]*([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)",
            line,
            re.IGNORECASE,
        )
        if match is None:
            continue
        return FermiMetadata(
            energy_ev=float(match.group(1)),
            source_file=source_file,
            source_type="dos_header",
            reference_kind="fermi_energy",
        )

    return None


def _extract_fermi_from_xml(xml_file: Path) -> FermiMetadata | None:
    try:
        tree = ET.parse(xml_file)
    except ET.ParseError:
        return None

    root = tree.getroot()
    band_structure = _first_descendant_by_local_name(root, "band_structure")
    if band_structure is None:
        return None

    for tag_name in ("fermi_energy", "highestOccupiedLevel"):
        element = _first_child_by_local_name(band_structure, tag_name)
        if element is None or element.text is None:
            continue
        try:
            energy_ha = float(element.text.strip())
        except ValueError:
            continue
        return FermiMetadata(
            energy_ev=energy_ha * HARTREE_TO_EV,
            source_file=xml_file,
            source_type="xml",
            reference_kind=tag_name,
        )

    split_element = _first_child_by_local_name(band_structure, "two_fermi_energies")
    if split_element is None:
        return None

    values = _extract_floats_from_text(split_element.itertext())
    if len(values) < 2:
        return None

    first_ev = values[0] * HARTREE_TO_EV
    second_ev = values[1] * HARTREE_TO_EV
    if math.isclose(first_ev, second_ev, rel_tol=0.0, abs_tol=1.0e-5):
        return FermiMetadata(
            energy_ev=(first_ev + second_ev) / 2.0,
            source_file=xml_file,
            source_type="xml",
            reference_kind="two_fermi_energies",
        )

    return FermiMetadata(
        energy_ev=None,
        source_file=xml_file,
        source_type="xml",
        reference_kind="two_fermi_energies",
        message="XML metadata contains distinct spin-dependent Fermi energies.",
    )


def _extract_fermi_from_pw_out(pw_output_file: Path) -> FermiMetadata | None:
    text = pw_output_file.read_text(encoding="utf-8", errors="ignore")

    match = re.search(
        r"the\s+fermi\s+energy\s+is\s+([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)\s+ev",
        text,
        re.IGNORECASE,
    )
    if match is not None:
        return FermiMetadata(
            energy_ev=float(match.group(1)),
            source_file=pw_output_file,
            source_type="pw.out",
            reference_kind="fermi_energy",
        )

    match = re.search(
        r"highest\s+occupied\s+level\s*\(ev\)\s*:\s*([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)",
        text,
        re.IGNORECASE,
    )
    if match is not None:
        return FermiMetadata(
            energy_ev=float(match.group(1)),
            source_file=pw_output_file,
            source_type="pw.out",
            reference_kind="highestOccupiedLevel",
        )

    match = re.search(
        r"highest\s+occupied,\s*lowest\s+unoccupied\s+level\s*\(ev\)\s*:\s*"
        r"([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)\s+([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)",
        text,
        re.IGNORECASE,
    )
    if match is not None:
        highest = float(match.group(1))
        lowest = float(match.group(2))
        if math.isclose(highest, lowest, rel_tol=0.0, abs_tol=1.0e-5):
            return FermiMetadata(
                energy_ev=(highest + lowest) / 2.0,
                source_file=pw_output_file,
                source_type="pw.out",
                reference_kind="highest_lowest_levels",
            )

    return None


def _infer_spin_resolved_dos_columns(header_lines: list[str], column_count: int) -> tuple[int, int] | None:
    if column_count < 4:
        return None

    header_text = " ".join(header_lines).lower()
    if not header_text:
        return None

    up_markers = ("dosup", "dos_up", "spin up", "up dos", "dos(up)")
    down_markers = ("dosdw", "dos_down", "spin down", "down dos", "dos(down)")
    if not any(marker in header_text for marker in up_markers):
        return None
    if not any(marker in header_text for marker in down_markers):
        return None

    return (1, 2)


def _next_nonempty_line_index(lines: list[str], start_index: int) -> int | None:
    for index in range(start_index, len(lines)):
        if lines[index].strip():
            return index
    return None


def _first_descendant_by_local_name(element: ET.Element, local_name: str) -> ET.Element | None:
    for child in element.iter():
        if _local_name(child.tag) == local_name:
            return child
    return None


def _first_child_by_local_name(element: ET.Element, local_name: str) -> ET.Element | None:
    for child in list(element):
        if _local_name(child.tag) == local_name:
            return child
    return None


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _extract_floats_from_text(chunks: Iterable[str]) -> list[float]:
    values: list[float] = []
    for chunk in chunks:
        for match in re.finditer(r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?", chunk):
            values.append(float(match.group(0)))
    return values


def _is_comment_line(line: str) -> bool:
    return line.startswith(("#", "!", ";"))


def _common_prefix_score(left: str, right: str) -> int:
    score = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        score += 1
    return score


def _sequences_close(first: list[float], second: list[float], tolerance: float = 1.0e-6) -> bool:
    if len(first) != len(second):
        return False
    return all(abs(left - right) <= tolerance for left, right in zip(first, second))


def _shifted_values(values: list[float], reference: float | None, align_to_reference: bool) -> list[float]:
    if not align_to_reference or reference is None:
        return list(values)
    return [value - reference for value in values]


def _base_plot_layout(*, height: int) -> dict:
    return {
        "template": "none",
        "plot_bgcolor": "white",
        "paper_bgcolor": "white",
        "height": height,
        "font": {"family": "Helvetica, Arial, sans-serif", "size": 15, "color": PUBLICATION_TEXT},
        "title_x": 0.02,
        "title_xanchor": "left",
        "title_font": {"size": 23, "color": PUBLICATION_TEXT},
        "margin": {"l": 88, "r": 34, "t": 74, "b": 82},
        "hovermode": "closest",
        "hoverlabel": {
            "bgcolor": "white",
            "bordercolor": PUBLICATION_MUTED,
            "font": {"color": PUBLICATION_TEXT, "size": 14},
        },
        "xaxis": {
            "title": {"font": {"size": 18, "color": PUBLICATION_TEXT}, "standoff": 14},
            "tickfont": {"size": 15, "color": PUBLICATION_TEXT},
            "showline": True,
            "linewidth": 1.6,
            "linecolor": PUBLICATION_TEXT,
            "mirror": False,
            "ticks": "outside",
            "showgrid": False,
            "zeroline": False,
            "tickcolor": PUBLICATION_TEXT,
            "tickwidth": 1.2,
            "automargin": True,
        },
        "yaxis": {
            "title": {"font": {"size": 18, "color": PUBLICATION_TEXT}, "standoff": 14},
            "tickfont": {"size": 15, "color": PUBLICATION_TEXT},
            "showline": True,
            "linewidth": 1.6,
            "linecolor": PUBLICATION_TEXT,
            "mirror": False,
            "ticks": "outside",
            "showgrid": False,
            "zeroline": False,
            "tickcolor": PUBLICATION_TEXT,
            "tickwidth": 1.2,
            "automargin": True,
        },
    }
