from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from electronic_properties import parse_qe_band_structure, parse_qe_dos


DEFAULT_PREFIX = "pwscf"
DEFAULT_OUTDIR_VALUE = "."
SOURCE_INPUT_CANDIDATES = ("pw.in", "espresso.pwi")
CARD_HEADERS = (
    "ATOMIC_SPECIES",
    "ATOMIC_POSITIONS",
    "CELL_PARAMETERS",
    "K_POINTS",
    "CONSTRAINTS",
    "OCCUPATIONS",
    "ATOMIC_FORCES",
    "HUBBARD",
)


@dataclass(frozen=True)
class QERunContext:
    run_dir: Path
    source_input_file: Path | None
    source_input_text: str | None
    calculation: str | None
    prefix: str
    outdir: str | None
    resolved_outdir: Path
    effective_outdir: str
    pseudo_dir: str | None
    k_points_mode: str | None
    save_dir: Path | None
    has_xml_metadata: bool

    @property
    def has_save_data(self) -> bool:
        return self.save_dir is not None


@dataclass(frozen=True)
class PrerequisiteCheck:
    ok: bool
    blockers: list[str]
    warnings: list[str]


def read_pw_input_metadata(pw_input_path: Path) -> dict:
    """Read lightweight QE metadata from a saved input file."""
    input_path = Path(pw_input_path)
    text = input_path.read_text(encoding="utf-8")

    return {
        "path": input_path,
        "text": text,
        "calculation": _match_assignment(text, "calculation"),
        "prefix": _match_assignment(text, "prefix"),
        "outdir": _match_assignment(text, "outdir"),
        "pseudo_dir": _match_assignment(text, "pseudo_dir"),
        "k_points_mode": _match_k_points_mode(text),
    }


def resolve_qe_run_context(run_dir: Path) -> QERunContext:
    """Resolve saved QE run metadata from disk, preferring pw.in over espresso.pwi."""
    run_directory = Path(run_dir)
    metadata: dict | None = None

    for candidate_name in SOURCE_INPUT_CANDIDATES:
        candidate_path = run_directory / candidate_name
        if candidate_path.is_file():
            metadata = read_pw_input_metadata(candidate_path)
            break

    if metadata is None:
        resolved_outdir = run_directory
        return QERunContext(
            run_dir=run_directory,
            source_input_file=None,
            source_input_text=None,
            calculation=None,
            prefix=DEFAULT_PREFIX,
            outdir=None,
            resolved_outdir=resolved_outdir,
            effective_outdir=DEFAULT_OUTDIR_VALUE,
            pseudo_dir=None,
            k_points_mode=None,
            save_dir=None,
            has_xml_metadata=False,
        )

    prefix = metadata.get("prefix") or DEFAULT_PREFIX
    outdir = metadata.get("outdir")
    resolved_outdir = _resolve_outdir_path(run_directory, outdir)
    save_dir = find_qe_save_dir(run_directory, prefix, outdir)
    has_xml_metadata = False
    if save_dir is not None:
        has_xml_metadata = any(path.is_file() for path in save_dir.glob("*.xml"))

    return QERunContext(
        run_dir=run_directory,
        source_input_file=metadata["path"],
        source_input_text=metadata["text"],
        calculation=metadata.get("calculation"),
        prefix=prefix,
        outdir=outdir,
        resolved_outdir=resolved_outdir,
        effective_outdir=outdir if outdir else DEFAULT_OUTDIR_VALUE,
        pseudo_dir=metadata.get("pseudo_dir"),
        k_points_mode=metadata.get("k_points_mode"),
        save_dir=save_dir,
        has_xml_metadata=has_xml_metadata,
    )


def find_qe_save_dir(run_dir: Path, prefix: str, outdir: str | None) -> Path | None:
    """Locate a QE prefix.save directory using the saved outdir when present."""
    run_directory = Path(run_dir)
    candidate_directories = [_resolve_outdir_path(run_directory, outdir) / f"{prefix}.save"]
    fallback_directory = run_directory / f"{prefix}.save"
    if fallback_directory not in candidate_directories:
        candidate_directories.append(fallback_directory)

    for candidate in candidate_directories:
        if candidate.is_dir():
            return candidate

    return None


def validate_dos_prerequisites(context: QERunContext) -> PrerequisiteCheck:
    """Check whether DOS post-processing is runnable for the current run directory."""
    blockers: list[str] = []
    warnings: list[str] = []

    if context.source_input_file is None:
        blockers.append("No saved QE input was found in `qe_runs/` (`pw.in` or `espresso.pwi`).")
    if context.save_dir is None:
        blockers.append("No usable `prefix.save` directory was found for the current QE run.")

    if not blockers:
        suitability_messages: list[str] = []
        if (context.calculation or "").lower() != "nscf":
            suitability_messages.append("the source calculation is not `nscf`")
        if (context.k_points_mode or "").lower() != "automatic":
            suitability_messages.append("the source K_POINTS card is not `automatic`")
        if suitability_messages:
            warnings.append(
                "This base workflow may not be ideal for meaningful DOS because "
                + " and ".join(suitability_messages)
                + "."
            )
        if not context.has_xml_metadata:
            warnings.append(
                "QE XML metadata was not found in the save directory, so Fermi metadata may be limited."
            )

    return PrerequisiteCheck(ok=not blockers, blockers=blockers, warnings=warnings)


def validate_bands_prerequisites(
    context: QERunContext,
    *,
    nbnd: int | None = None,
    band_path_text: str | None = None,
) -> PrerequisiteCheck:
    """Check whether a dedicated band-structure workflow can be launched."""
    blockers: list[str] = []
    warnings: list[str] = []

    if context.source_input_file is None:
        blockers.append("No saved QE input was found in `qe_runs/` (`pw.in` or `espresso.pwi`).")
    if context.save_dir is None:
        blockers.append("No usable `prefix.save` directory was found for the current QE run.")
    if (context.calculation or "").lower() != "scf":
        blockers.append("Band workflows require a prior SCF source input in `qe_runs/`.")
    if nbnd is None or int(nbnd) <= 0:
        blockers.append("`nbnd` must be a positive integer.")

    if band_path_text is None or not band_path_text.strip():
        blockers.append("Provide a band-path text block before running the band workflow.")
    else:
        try:
            validate_band_path_rows(band_path_text)
        except ValueError as exc:
            blockers.append(str(exc))

    if context.save_dir is not None and not context.has_xml_metadata:
        warnings.append(
            "QE XML metadata was not found in the save directory, so Fermi metadata may be limited."
        )

    return PrerequisiteCheck(ok=not blockers, blockers=blockers, warnings=warnings)


def sanitize_leaf_filename(filename: str, *, default_name: str) -> str:
    """Keep generated artifacts inside qe_runs by accepting only leaf filenames."""
    candidate = (filename or "").strip()
    if not candidate:
        return default_name

    if candidate in {".", ".."}:
        raise ValueError("Output filename must be a regular leaf filename.")

    if Path(candidate).name != candidate:
        raise ValueError("Output filename must not include directory separators.")

    return candidate


def build_dos_input_text(
    *,
    prefix: str,
    outdir: str,
    fildos: str,
    emin_ev: float,
    emax_ev: float,
    deltae_ev: float,
    bz_sum: str,
    ngauss: int | None = None,
    degauss_ry: float | None = None,
) -> str:
    """Build a minimal dos.x input file."""
    lines = [
        "&DOS",
        f"   prefix = '{prefix}'",
        f"   outdir = '{outdir}'",
        f"   fildos = '{fildos}'",
        f"   Emin = {emin_ev:.6f}",
        f"   Emax = {emax_ev:.6f}",
        f"   DeltaE = {deltae_ev:.6f}",
        f"   bz_sum = '{bz_sum}'",
    ]
    if bz_sum == "smearing":
        if ngauss is None or degauss_ry is None:
            raise ValueError("Smearing DOS runs require both `ngauss` and `degauss`.")
        lines.append(f"   ngauss = {int(ngauss)}")
        lines.append(f"   degauss = {degauss_ry:.6f}")
    lines.extend(("/", ""))
    return "\n".join(lines)


def validate_band_path_rows(band_path_text: str) -> list[str]:
    """Validate rows-only band path input and return normalized lines."""
    normalized_rows: list[str] = []

    for line_number, raw_line in enumerate(band_path_text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue

        body, comment = _split_inline_comment(raw_line.rstrip())
        body_parts = body.split()
        if len(body_parts) != 4:
            raise ValueError(
                f"Band-path row {line_number} must contain exactly four numeric fields before any label."
            )

        try:
            float(body_parts[0])
            float(body_parts[1])
            float(body_parts[2])
            segment_count = int(float(body_parts[3]))
        except ValueError as exc:
            raise ValueError(
                f"Band-path row {line_number} must contain three coordinates and an integer segment count."
            ) from exc

        if segment_count < 0:
            raise ValueError(f"Band-path row {line_number} must use a non-negative segment count.")

        normalized_body = " ".join(body_parts)
        if comment:
            normalized_rows.append(f"{normalized_body} ! {comment}")
        else:
            normalized_rows.append(normalized_body)

    if len(normalized_rows) < 2:
        raise ValueError("Band-path input must contain at least two k-point rows.")

    return normalized_rows


def build_band_k_points_block(coordinate_mode: str, band_path_text: str) -> str:
    """Build the K_POINTS block for a band-path pw.x calculation."""
    rows = validate_band_path_rows(band_path_text)
    lines = [f"K_POINTS {coordinate_mode}", f"{len(rows)}", *rows, ""]
    return "\n".join(lines)


def transform_input_to_bands_pw(
    context: QERunContext,
    *,
    nbnd: int,
    coordinate_mode: str,
    band_path_text: str,
) -> str:
    """Convert a saved base pw.x input into a dedicated band-path pw.x input."""
    if context.source_input_text is None:
        raise ValueError("No saved QE input text is available for band-path generation.")

    text = context.source_input_text
    text = _set_namelist_value(text, "CONTROL", "calculation", "'bands'")
    text = _set_namelist_value(text, "CONTROL", "prefix", f"'{context.prefix}'")
    text = _set_namelist_value(text, "CONTROL", "outdir", f"'{context.effective_outdir}'")
    text = _set_namelist_value(text, "SYSTEM", "nbnd", str(int(nbnd)))
    text = _set_namelist_value(text, "ELECTRONS", "startingpot", "'file'")
    text = _set_namelist_value(text, "ELECTRONS", "startingwfc", "'file'")
    text = _replace_k_points_block(text, build_band_k_points_block(coordinate_mode, band_path_text))

    if not text.endswith("\n"):
        text = f"{text}\n"
    return text


def build_bands_pp_input_text(*, prefix: str, outdir: str, filband: str = "filband") -> str:
    """Build a minimal bands.x post-processing input file."""
    lines = [
        "&BANDS",
        f"   prefix = '{prefix}'",
        f"   outdir = '{outdir}'",
        f"   filband = '{filband}'",
        "/",
        "",
    ]
    return "\n".join(lines)


def validate_dos_output_file(dos_file: Path) -> None:
    """Require a DOS file to exist and be parseable by the existing analysis viewer."""
    candidate = Path(dos_file)
    if not candidate.is_file():
        raise ValueError(f"DOS output file was not created: {candidate}")
    parse_qe_dos(candidate)


def validate_band_output_file(band_file: Path, *, band_input_file: Path | None = None) -> None:
    """Require filband.gnu to exist, be non-empty, and parse cleanly."""
    candidate = Path(band_file)
    if not candidate.is_file():
        raise ValueError(f"Band output file was not created: {candidate}")
    if candidate.stat().st_size <= 0:
        raise ValueError(f"Band output file is empty: {candidate}")
    parse_qe_band_structure(candidate, band_input_file=band_input_file)


def summarize_artifacts(artifacts: dict[str, Path]) -> list[dict[str, str]]:
    """Build a small artifact summary table for UI display."""
    rows: list[dict[str, str]] = []
    for label, path in artifacts.items():
        candidate = Path(path)
        exists = candidate.exists()
        rows.append(
            {
                "Artifact": label,
                "Path": str(candidate),
                "Exists": "Yes" if exists else "No",
                "Size (bytes)": str(candidate.stat().st_size) if exists and candidate.is_file() else "",
            }
        )
    return rows


def _match_assignment(text: str, key: str) -> str | None:
    pattern = re.compile(
        rf"^\s*{re.escape(key)}\s*=\s*['\"]?([^'\",\n]+)['\"]?",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1).strip() or None


def _match_k_points_mode(text: str) -> str | None:
    match = re.search(r"^\s*K_POINTS\s+([A-Za-z0-9_]+)", text, re.IGNORECASE | re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip().lower()


def _resolve_outdir_path(run_dir: Path, outdir: str | None) -> Path:
    if not outdir:
        return Path(run_dir)
    resolved = Path(outdir)
    if not resolved.is_absolute():
        resolved = Path(run_dir) / resolved
    return resolved


def _split_inline_comment(raw_line: str) -> tuple[str, str | None]:
    first_marker_index: int | None = None
    marker = ""
    for candidate in ("!", "#"):
        index = raw_line.find(candidate)
        if index >= 0 and (first_marker_index is None or index < first_marker_index):
            first_marker_index = index
            marker = candidate

    if first_marker_index is None:
        return raw_line.strip(), None

    body = raw_line[:first_marker_index].strip()
    comment = raw_line[first_marker_index + len(marker) :].strip() or None
    return body, comment


def _set_namelist_value(text: str, namelist_name: str, key: str, value: str) -> str:
    block_pattern = re.compile(
        rf"(^\s*&{re.escape(namelist_name)}\b.*?^\s*/\s*$)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    block_match = block_pattern.search(text)
    if block_match is None:
        insertion = f"&{namelist_name}\n   {key} = {value}\n/\n"
        return f"{insertion}\n{text.lstrip()}"

    block_text = block_match.group(1)
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=.*$", re.IGNORECASE | re.MULTILINE)
    replacement_line = f"   {key} = {value}"
    if key_pattern.search(block_text):
        updated_block = key_pattern.sub(replacement_line, block_text, count=1)
    else:
        updated_block = re.sub(r"^\s*/\s*$", f"{replacement_line}\n/", block_text, count=1, flags=re.MULTILINE)

    return f"{text[:block_match.start()]}{updated_block}{text[block_match.end():]}"


def _replace_k_points_block(text: str, replacement_block: str) -> str:
    k_points_match = re.search(r"^\s*K_POINTS\b.*$", text, re.IGNORECASE | re.MULTILINE)
    if k_points_match is None:
        return f"{text.rstrip()}\n\n{replacement_block}"

    start_index = k_points_match.start()
    lines = text.splitlines(keepends=True)
    line_starts: list[int] = []
    cursor = 0
    for line in lines:
        line_starts.append(cursor)
        cursor += len(line)

    start_line_index = 0
    for index, line_start in enumerate(line_starts):
        if line_start == start_index:
            start_line_index = index
            break

    end_line_index = len(lines)
    for index in range(start_line_index + 1, len(lines)):
        stripped = lines[index].strip().upper()
        if any(stripped.startswith(header) for header in CARD_HEADERS if header != "K_POINTS"):
            end_line_index = index
            break

    prefix = "".join(lines[:start_line_index]).rstrip()
    suffix = "".join(lines[end_line_index:]).lstrip("\n")
    if prefix:
        prefix = f"{prefix}\n\n"
    if suffix:
        suffix = f"\n{suffix}"
    return f"{prefix}{replacement_block.rstrip()}{suffix}"
