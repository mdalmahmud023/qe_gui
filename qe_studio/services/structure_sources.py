from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Any

from ase import Atoms
from ase.cell import Cell
from ase.data import atomic_numbers
from ase.formula import Formula
from ase.io import read
from ase.spacegroup import crystal
import numpy as np

from .structure_formula import PREFERRED_FORMULA_SOURCE_DETAIL_KEY, format_formula_sequence


FILE_FORMAT_AUTO = "auto"
FILE_FORMAT_CIF = "cif"
FILE_FORMAT_VASP = "vasp"
FILE_FORMAT_XSF = "xsf"
FILE_FORMAT_XYZ = "xyz"
SUPPORTED_FILE_FORMATS = (
    FILE_FORMAT_AUTO,
    FILE_FORMAT_CIF,
    FILE_FORMAT_VASP,
    FILE_FORMAT_XSF,
    FILE_FORMAT_XYZ,
)

MANUAL_COORDINATE_MODE_FRACTIONAL = "fractional"
MANUAL_COORDINATE_MODE_CARTESIAN = "cartesian"
MANUAL_COORDINATE_MODES = (
    MANUAL_COORDINATE_MODE_FRACTIONAL,
    MANUAL_COORDINATE_MODE_CARTESIAN,
)


@dataclass(frozen=True)
class StructureSourceResult:
    atoms: Atoms
    source_kind: str
    source_label: str
    source_format: str | None = None
    source_details: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()


def _validate_chemical_symbol(symbol: str) -> str:
    normalized = (symbol or "").strip()
    if not normalized:
        raise ValueError("Atom rows must include a chemical symbol.")
    candidate = normalized[0].upper() + normalized[1:].lower()
    if candidate not in atomic_numbers:
        raise ValueError(f"Invalid chemical symbol: {normalized!r}")
    return candidate


def _normalize_uploaded_format(filename: str, requested_format: str) -> tuple[str, list[str]]:
    requested = (requested_format or FILE_FORMAT_AUTO).strip().lower()
    if requested not in SUPPORTED_FILE_FORMATS:
        raise ValueError(f"Unsupported file format selection: {requested_format!r}")

    normalized_name = Path(filename or "").name
    lower_name = normalized_name.lower()
    suffix = Path(lower_name).suffix
    stem = Path(lower_name).name

    if requested != FILE_FORMAT_AUTO:
        if requested == FILE_FORMAT_XYZ:
            return requested, ["extxyz", "xyz"]
        return requested, [requested]

    if suffix == ".cif":
        return FILE_FORMAT_CIF, [FILE_FORMAT_CIF]
    if suffix == ".xsf":
        return FILE_FORMAT_XSF, [FILE_FORMAT_XSF]
    if suffix in {".vasp", ".poscar", ".contcar"} or stem in {"poscar", "contcar"}:
        return FILE_FORMAT_VASP, [FILE_FORMAT_VASP]
    if suffix == ".xyz":
        return FILE_FORMAT_XYZ, ["extxyz", "xyz"]

    raise ValueError(
        "Could not auto-detect the structure format from the filename. "
        "Select the file format explicitly before importing."
    )


def _read_uploaded_atoms(filename: str, file_bytes: bytes, candidate_formats: list[str]) -> Atoms:
    if not file_bytes:
        raise ValueError("The selected file is empty.")

    suffix = Path(filename or "").suffix
    errors: list[str] = []
    for candidate_format in candidate_formats:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or f".{candidate_format}") as tmp:
                tmp.write(file_bytes)
                temp_path = Path(tmp.name)
            atoms = read(temp_path, format=candidate_format, index=0)
            if isinstance(atoms, list):
                if not atoms:
                    raise ValueError("No structures were found in the selected file.")
                atoms = atoms[0]
            return atoms
        except Exception as exc:
            errors.append(f"{candidate_format}: {exc}")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    raise ValueError("Failed to parse the uploaded structure file. " + " ".join(errors))


def _validate_periodic_atoms(atoms: Atoms, *, source_description: str) -> Atoms:
    if atoms is None or len(atoms) == 0:
        raise ValueError(f"{source_description} does not contain any atoms.")
    if atoms.cell.rank < 3:
        raise ValueError(f"{source_description} does not define a full 3D lattice.")
    if float(atoms.cell.volume) <= 1.0e-8:
        raise ValueError(f"{source_description} has a zero or near-zero cell volume.")

    validated_atoms = atoms.copy()
    validated_atoms.pbc = (True, True, True)
    return validated_atoms


def import_structure_file(
    *,
    filename: str,
    file_bytes: bytes,
    requested_format: str = FILE_FORMAT_AUTO,
) -> StructureSourceResult:
    """Import one supported crystal structure file into an ASE `Atoms` object."""
    resolved_format, candidate_formats = _normalize_uploaded_format(filename, requested_format)
    atoms = _read_uploaded_atoms(filename, file_bytes, candidate_formats)
    atoms = _validate_periodic_atoms(atoms, source_description=f"{filename or 'The uploaded file'}")
    return StructureSourceResult(
        atoms=atoms,
        source_kind="file",
        source_label=Path(filename or "structure").name,
        source_format=resolved_format,
        source_details={"requested_format": requested_format},
    )


def _normalize_manual_rows(rows: Any) -> list[dict[str, Any]]:
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        records = rows.to_dict("records")
    elif isinstance(rows, list):
        records = rows
    else:
        raise ValueError("Unsupported atom-table data from the manual builder.")

    normalized_rows: list[dict[str, Any]] = []
    for row in records:
        normalized_rows.append(
            {
                "symbol": (row.get("symbol") if isinstance(row, dict) else ""),
                "x": row.get("x") if isinstance(row, dict) else None,
                "y": row.get("y") if isinstance(row, dict) else None,
                "z": row.get("z") if isinstance(row, dict) else None,
            }
        )
    return normalized_rows


def _wrap_fractional_positions(fractional_positions: np.ndarray) -> tuple[np.ndarray, bool]:
    wrapped = np.mod(np.asarray(fractional_positions, dtype=float), 1.0)
    wrapped[np.isclose(wrapped, 1.0, atol=1.0e-8)] = 0.0
    changed = not np.allclose(wrapped, np.asarray(fractional_positions, dtype=float), atol=1.0e-8)
    return wrapped, changed


def _find_duplicate_sites(cell: Cell, wrapped_fractional_positions: np.ndarray, *, tolerance: float = 1.0e-3) -> bool:
    site_count = len(wrapped_fractional_positions)
    if site_count < 2:
        return False

    cell_matrix = np.asarray(cell.array, dtype=float)
    for first_index in range(site_count - 1):
        for second_index in range(first_index + 1, site_count):
            delta = wrapped_fractional_positions[first_index] - wrapped_fractional_positions[second_index]
            delta -= np.round(delta)
            cartesian_delta = delta @ cell_matrix
            if float(np.linalg.norm(cartesian_delta)) < tolerance:
                return True
    return False


def build_manual_structure(
    *,
    a: float,
    b: float,
    c: float,
    alpha: float,
    beta: float,
    gamma: float,
    coordinate_mode: str,
    atom_rows: Any,
    formula_text: str = "",
) -> StructureSourceResult:
    """Build a periodic crystal structure from lattice parameters plus atom rows."""
    if coordinate_mode not in MANUAL_COORDINATE_MODES:
        raise ValueError("Select either fractional or cartesian coordinates.")
    if min(float(a), float(b), float(c)) <= 0.0:
        raise ValueError("Lattice lengths `a`, `b`, and `c` must all be positive.")
    if not all(0.0 < float(angle) < 180.0 for angle in (alpha, beta, gamma)):
        raise ValueError("Lattice angles must be strictly between 0 and 180 degrees.")

    cell = Cell.fromcellpar([float(a), float(b), float(c), float(alpha), float(beta), float(gamma)])
    if float(cell.volume) <= 1.0e-8:
        raise ValueError("The provided lattice parameters produce a zero or near-zero cell volume.")

    normalized_rows = _normalize_manual_rows(atom_rows)
    clean_symbols: list[str] = []
    clean_coordinates: list[list[float]] = []

    for row_number, row in enumerate(normalized_rows, start=1):
        symbol_text = (row.get("symbol") or "").strip()
        values = [row.get("x"), row.get("y"), row.get("z")]
        row_is_blank = not symbol_text and all(value in (None, "") for value in values)
        if row_is_blank:
            continue
        if not symbol_text or any(value in (None, "") for value in values):
            raise ValueError(f"Atom row {row_number} is partially filled. Complete it or clear it.")

        symbol = _validate_chemical_symbol(symbol_text)
        try:
            coordinates = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Atom row {row_number} contains malformed coordinates.") from exc
        if not all(np.isfinite(coordinates)):
            raise ValueError(f"Atom row {row_number} contains non-finite coordinates.")

        clean_symbols.append(symbol)
        clean_coordinates.append(coordinates)

    if not clean_symbols:
        raise ValueError("Enter at least one atom row before building the structure.")

    if formula_text.strip():
        expected_counts = Formula(formula_text.strip(), strict=True).count()
        actual_counts: dict[str, int] = {}
        for symbol in clean_symbols:
            actual_counts[symbol] = actual_counts.get(symbol, 0) + 1
        if expected_counts != actual_counts:
            raise ValueError(
                "The provided formula does not match the atom table. "
                f"Expected {expected_counts}, but found {actual_counts}."
            )

    warnings: list[str] = []
    if coordinate_mode == MANUAL_COORDINATE_MODE_FRACTIONAL:
        input_fractional_positions = np.asarray(clean_coordinates, dtype=float)
        wrapped_fractional_positions, wrapped_changed = _wrap_fractional_positions(input_fractional_positions)
        atoms = Atoms(
            symbols=clean_symbols,
            cell=cell,
            scaled_positions=wrapped_fractional_positions,
            pbc=(True, True, True),
        )
    else:
        cartesian_positions = np.asarray(clean_coordinates, dtype=float)
        atoms = Atoms(
            symbols=clean_symbols,
            cell=cell,
            positions=cartesian_positions,
            pbc=(True, True, True),
        )
        wrapped_fractional_positions, wrapped_changed = _wrap_fractional_positions(atoms.get_scaled_positions(wrap=False))
        atoms.set_scaled_positions(wrapped_fractional_positions)

    if wrapped_changed:
        warnings.append("Some atomic positions were wrapped back into the unit cell.")

    if _find_duplicate_sites(cell, wrapped_fractional_positions):
        raise ValueError("Duplicate or overlapping atom sites were detected after wrapping into the unit cell.")

    atoms = _validate_periodic_atoms(atoms, source_description="The manual structure")
    return StructureSourceResult(
        atoms=atoms,
        source_kind="manual",
        source_label="Manual Builder",
        source_format=None,
        source_details={
            "coordinate_mode": coordinate_mode,
            "formula_text": formula_text.strip() or None,
            PREFERRED_FORMULA_SOURCE_DETAIL_KEY: formula_text.strip() or None,
        },
        warnings=tuple(warnings),
    )


def build_ordered_double_perovskite(
    *,
    a_site: str,
    b_site: str,
    b_prime_site: str,
    x_site: str,
    lattice_parameter: float,
    x_parameter: float = 0.25,
) -> StructureSourceResult:
    """Build an ordered cubic double perovskite / elpasolite `A2BB'X6` model."""
    a_symbol = _validate_chemical_symbol(a_site)
    b_symbol = _validate_chemical_symbol(b_site)
    b_prime_symbol = _validate_chemical_symbol(b_prime_site)
    x_symbol = _validate_chemical_symbol(x_site)

    if float(lattice_parameter) <= 0.0:
        raise ValueError("The cubic lattice parameter `a` must be positive.")
    if b_symbol == b_prime_symbol:
        raise ValueError("For the ordered double-perovskite prototype, `B` and `B'` must be different elements.")
    if not 0.0 <= float(x_parameter) <= 0.5:
        raise ValueError("The advanced anion coordinate must stay between 0.0 and 0.5.")

    atoms = crystal(
        symbols=(a_symbol, b_symbol, b_prime_symbol, x_symbol),
        basis=[
            (0.25, 0.25, 0.25),
            (0.0, 0.0, 0.0),
            (0.5, 0.5, 0.5),
            (float(x_parameter), 0.0, 0.0),
        ],
        spacegroup=225,
        cellpar=[float(lattice_parameter), float(lattice_parameter), float(lattice_parameter), 90.0, 90.0, 90.0],
    )
    atoms = _validate_periodic_atoms(atoms, source_description="The prototype structure")
    return _build_prototype_result(
        atoms,
        source_label="Ordered Double Perovskite (A2BB'X6)",
        family_key="ordered_double_perovskite_a2bbx6",
        family_label="Ordered Double Perovskite",
        formula_template="A2BB'X6",
        source_details={
            "a_site": a_symbol,
            "b_site": b_symbol,
            "b_prime_site": b_prime_symbol,
            "x_site": x_symbol,
            "lattice_parameter": float(lattice_parameter),
            "x_parameter": float(x_parameter),
            PREFERRED_FORMULA_SOURCE_DETAIL_KEY: format_formula_sequence(
                (
                    (a_symbol, 2),
                    (b_symbol, 1),
                    (b_prime_symbol, 1),
                    (x_symbol, 6),
                )
            ),
        },
    )


def build_cubic_perovskite(
    *,
    a_site: str,
    b_site: str,
    x_site: str,
    lattice_parameter: float,
) -> StructureSourceResult:
    """Build an ideal cubic perovskite `ABX3` model."""
    a_symbol = _validate_chemical_symbol(a_site)
    b_symbol = _validate_chemical_symbol(b_site)
    x_symbol = _validate_chemical_symbol(x_site)

    if float(lattice_parameter) <= 0.0:
        raise ValueError("The cubic lattice parameter `a` must be positive.")

    atoms = crystal(
        symbols=(a_symbol, b_symbol, x_symbol),
        basis=[
            (0.0, 0.0, 0.0),
            (0.5, 0.5, 0.5),
            (0.5, 0.5, 0.0),
        ],
        spacegroup=221,
        cellpar=[float(lattice_parameter), float(lattice_parameter), float(lattice_parameter), 90.0, 90.0, 90.0],
    )
    atoms = _validate_periodic_atoms(atoms, source_description="The prototype structure")
    return _build_prototype_result(
        atoms,
        source_label="Cubic Perovskite (ABX3)",
        family_key="cubic_perovskite_abx3",
        family_label="Cubic Perovskite",
        formula_template="ABX3",
        source_details={
            "a_site": a_symbol,
            "b_site": b_symbol,
            "x_site": x_symbol,
            "lattice_parameter": float(lattice_parameter),
            PREFERRED_FORMULA_SOURCE_DETAIL_KEY: format_formula_sequence(
                (
                    (a_symbol, 1),
                    (b_symbol, 1),
                    (x_symbol, 3),
                )
            ),
        },
    )


def build_rocksalt(
    *,
    a_site: str,
    b_site: str,
    lattice_parameter: float,
) -> StructureSourceResult:
    """Build a cubic rocksalt `AB` model."""
    a_symbol = _validate_chemical_symbol(a_site)
    b_symbol = _validate_chemical_symbol(b_site)

    if float(lattice_parameter) <= 0.0:
        raise ValueError("The cubic lattice parameter `a` must be positive.")

    atoms = crystal(
        symbols=(a_symbol, b_symbol),
        basis=[
            (0.0, 0.0, 0.0),
            (0.5, 0.5, 0.5),
        ],
        spacegroup=225,
        cellpar=[float(lattice_parameter), float(lattice_parameter), float(lattice_parameter), 90.0, 90.0, 90.0],
    )
    atoms = _validate_periodic_atoms(atoms, source_description="The prototype structure")
    return _build_prototype_result(
        atoms,
        source_label="Rocksalt (AB)",
        family_key="rocksalt_ab",
        family_label="Rocksalt",
        formula_template="AB",
        source_details={
            "a_site": a_symbol,
            "b_site": b_symbol,
            "lattice_parameter": float(lattice_parameter),
            PREFERRED_FORMULA_SOURCE_DETAIL_KEY: format_formula_sequence(
                (
                    (a_symbol, 1),
                    (b_symbol, 1),
                )
            ),
        },
    )


def _build_prototype_result(
    atoms: Atoms,
    *,
    source_label: str,
    family_key: str,
    family_label: str,
    formula_template: str,
    source_details: dict[str, Any],
) -> StructureSourceResult:
    details = {
        "_prototype_family_key": family_key,
        "prototype_family": family_label,
        "formula_template": formula_template,
    }
    details.update(source_details)
    return StructureSourceResult(
        atoms=atoms,
        source_kind="prototype",
        source_label=source_label,
        source_format=None,
        source_details=details,
    )
