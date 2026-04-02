from __future__ import annotations

from dataclasses import dataclass

from ase import Atoms
from ase.dft.kpoints import parse_path_string
import spglib


@dataclass(frozen=True)
class PrimitiveCellResult:
    atoms: Atoms
    changed: bool
    message: str


@dataclass(frozen=True)
class AutoBandPathResult:
    rows_text: str
    path_string: str
    special_point_labels: list[str]
    used_first_section_only: bool
    points_per_segment: int


def make_primitive_atoms(
    atoms: Atoms,
    *,
    symprec: float = 1.0e-5,
    angle_tolerance: float = -1.0,
    no_idealize: bool = False,
) -> PrimitiveCellResult:
    """Return a standardized primitive cell using spglib."""
    cell = (
        atoms.cell.array,
        atoms.get_scaled_positions(wrap=True),
        atoms.numbers,
    )
    standardized = spglib.standardize_cell(
        cell,
        to_primitive=True,
        no_idealize=no_idealize,
        symprec=symprec,
        angle_tolerance=angle_tolerance,
    )
    if standardized is None:
        raise ValueError("Could not determine a primitive cell for this structure.")

    lattice, scaled_positions, numbers = standardized
    primitive_atoms = Atoms(
        numbers=numbers,
        cell=lattice,
        scaled_positions=scaled_positions,
        pbc=atoms.pbc,
    )
    changed = len(primitive_atoms) != len(atoms) or not primitive_atoms.cell.array.round(8).tolist() == atoms.cell.array.round(8).tolist()
    message = (
        f"Using primitive cell with {len(primitive_atoms)} atoms instead of the uploaded {len(atoms)}-atom cell."
        if changed
        else "The uploaded structure already looks like a primitive cell."
    )
    return PrimitiveCellResult(atoms=primitive_atoms, changed=changed, message=message)


def generate_band_path_rows(
    atoms: Atoms,
    *,
    points_per_segment: int = 20,
) -> AutoBandPathResult:
    """Generate a QE crystal_b band path from the active cell."""
    if points_per_segment < 1:
        raise ValueError("Points per segment must be at least 1.")

    band_path = atoms.cell.bandpath()
    sections = parse_path_string(band_path.path)
    if not sections:
        raise ValueError("ASE could not determine a band path for this cell.")

    # QE K_POINTS *_b is easiest to express as one continuous section.
    labels = sections[0]
    rows: list[str] = []
    for index, label in enumerate(labels):
        coordinates = band_path.special_points[label]
        segment_points = 1 if index == len(labels) - 1 else points_per_segment
        rows.append(
            f"{coordinates[0]:.6f} {coordinates[1]:.6f} {coordinates[2]:.6f} "
            f"{segment_points} ! {label}"
        )

    return AutoBandPathResult(
        rows_text="\n".join(rows),
        path_string=band_path.path,
        special_point_labels=list(labels),
        used_first_section_only=len(sections) > 1,
        points_per_segment=points_per_segment,
    )
