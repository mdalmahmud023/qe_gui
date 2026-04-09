"""
Modular Quantum ESPRESSO input generation pipeline for QE Studio.

Architecture
============
This module is a **pure service layer** — it has no Streamlit imports, no session-state
access, and no global side-effects.  The UI page (``pages/ui_setup.py``) calls this
module and handles all session-state bookkeeping itself.

Pipeline overview::

    atoms (ASE)                 resolved SSSP entries (dict)
        │                             │
        ▼                             ▼
    classify_structure()      suggest_cutoffs_from_pseudo_entries()
        │                     suggest_kpoints_from_cell()
        ▼
    CalculationContext          ← assembled by the caller
        │
        ▼
    build_qe_input_namelist()   ← pure dict from context
        │
        ▼
    generate_qe_input_text()    ← orchestrates ASE write + clean
        │
        ▼
    pw.in + espresso.pwi


Public API (stable)
-------------------
Data models:
    Dimensionality          Enum  — 0D / 1D / 2D / 3D
    ElectronicCharacter     Enum  — metal / insulator / unknown
    MagneticCharacter       Enum  — magnetic / nonmagnetic / unknown
    StructureClassification  dataclass — output of classify_structure()
    NumericalSettings       dataclass — convergence / mixing knobs
    PhysicalOptions         dataclass — smearing, magnetism, etc.
    CalculationContext      dataclass — full input to the builder

Service functions:
    classify_structure(atoms) -> StructureClassification
    suggest_cutoffs_from_pseudo_entries(resolved_entries) -> tuple[float, float]
    suggest_kpoints_from_cell(atoms, *, target_kpoint_density) -> list[int]
    build_qe_input_namelist(ctx) -> dict
    generate_qe_input_text(atoms, ctx, pseudopotentials, pseudo_dir, *, run_dir)
    clean_qe_input_text(input_text, *, calc_type) -> str
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ASE types are used for type hints only; the actual import is deferred to the
# caller so that tests without ASE can still import the data models.
try:
    from ase import Atoms
    from ase.calculators.espresso import Espresso, EspressoProfile

    _ASE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ASE_AVAILABLE = False
    Atoms = Any  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Vacuum threshold (Å) for detecting a non-periodic dimension.
VACUUM_THRESHOLD_ANGSTROM: float = 8.0

#: Fraction of a cell vector that must be vacuum for it to be "non-periodic".
VACUUM_FRACTION_THRESHOLD: float = 0.40

#: Elements with partially filled d or f shells are potentially magnetic.
_POTENTIALLY_MAGNETIC_ELEMENTS: frozenset[str] = frozenset(
    [
        "Cr", "Mn", "Fe", "Co", "Ni",  # 3d transition metals
        "Tc", "Ru", "Rh", "Pd",         # 4d
        "Os", "Ir", "Pt",               # 5d (weaker, but possible)
        "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd",  # rare-earth 4f
        "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
        "U", "Pu",                       # actinides
    ]
)

#: Elements whose electronegativity / typical chemistry suggest metallic character.
#: This is a heuristic, not a DFT calculation.
_TYPICALLY_METALLIC_ELEMENTS: frozenset[str] = frozenset(
    [
        "Li", "Na", "K", "Rb", "Cs", "Fr",             # alkali
        "Be", "Mg", "Ca", "Sr", "Ba", "Ra",             # alkaline earth
        "Al", "Ga", "In", "Sn", "Tl", "Pb", "Bi",      # post-transition
        "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", # 3d TM
        "Cu", "Zn", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", # 4d TM
        "Rh", "Pd", "Ag", "Cd", "Hf", "Ta", "W", "Re", # 5d TM
        "Os", "Ir", "Pt", "Au", "Hg",
        "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd",  # lanthanides
        "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
        "Ac", "Th", "U", "Pu",                           # actinides
    ]
)

#: Required QE namelists for each calculation type.
_REQUIRED_NAMELISTS: dict[str, frozenset[str]] = {
    "scf": frozenset({"&CONTROL", "&SYSTEM", "&ELECTRONS"}),
    "nscf": frozenset({"&CONTROL", "&SYSTEM", "&ELECTRONS"}),
    "relax": frozenset({"&CONTROL", "&SYSTEM", "&ELECTRONS", "&IONS"}),
    "vc-relax": frozenset({"&CONTROL", "&SYSTEM", "&ELECTRONS", "&IONS", "&CELL"}),
    "md": frozenset({"&CONTROL", "&SYSTEM", "&ELECTRONS", "&IONS"}),
    "vc-md": frozenset({"&CONTROL", "&SYSTEM", "&ELECTRONS", "&IONS", "&CELL"}),
}

#: Conservative fallback energy cutoffs (Ry) when no SSSP data is available.
FALLBACK_ECUTWFC_RY: float = 40.0
FALLBACK_ECUTRHO_RY: float = 160.0

#: Default k-point density (2π/Δk in Å⁻¹) for automatic MP grid suggestion.
DEFAULT_KPOINT_DENSITY: float = 0.04


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Dimensionality(str, Enum):
    """Physical dimensionality of the simulation cell."""

    D0 = "0D"  #: Molecule or cluster (all directions non-periodic or vacuum)
    D1 = "1D"  #: Nanowire or nanotube
    D2 = "2D"  #: Slab or monolayer
    D3 = "3D"  #: Bulk crystal


class ElectronicCharacter(str, Enum):
    """Heuristic classification of the likely electronic character."""

    METAL = "metal"
    INSULATOR = "insulator"
    UNKNOWN = "unknown"


class MagneticCharacter(str, Enum):
    """Heuristic classification of the likely magnetic character."""

    MAGNETIC = "magnetic"
    NONMAGNETIC = "nonmagnetic"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructureClassification:
    """
    Lightweight heuristic classification of an ASE Atoms object.

    All fields are heuristic estimates — final electronic character must come
    from a self-consistent calculation.

    Parameters
    ----------
    dimensionality:
        Estimated physical dimensionality (0D–3D).
    electronic_character:
        Likely metallic or insulating based on element composition.
    magnetic_character:
        Potentially magnetic if any element has d/f shell electrons.
    periodic_directions:
        Boolean mask [x, y, z] indicating which cell directions are periodic.
    vacuum_directions:
        Indices of cell directions that appear to be vacuum gaps.
    notes:
        Human-readable explanation of the classification decisions.
    """

    dimensionality: Dimensionality
    electronic_character: ElectronicCharacter
    magnetic_character: MagneticCharacter
    periodic_directions: tuple[bool, bool, bool]
    vacuum_directions: tuple[int, ...]
    notes: list[str]


@dataclass
class NumericalSettings:
    """
    Convergence and mixing parameters for the SCF cycle.

    Parameters
    ----------
    ecutwfc:
        Plane-wave kinetic energy cutoff for wavefunctions (Ry).
    ecutrho:
        Kinetic energy cutoff for the charge density (Ry).
    conv_thr:
        SCF energy convergence threshold (Ry).  A value of 1e-8 Ry is
        appropriate for forces; 1e-6 Ry is acceptable for energies only.
    mixing_beta:
        Linear mixing coefficient (0–1).  Lower values (0.3–0.4) improve
        convergence for magnetic, oxide, or defect systems.
    diagonalization:
        Diagonalization algorithm — ``"david"`` (default) or ``"cg"``.
    """

    ecutwfc: float = FALLBACK_ECUTWFC_RY
    ecutrho: float = FALLBACK_ECUTRHO_RY
    conv_thr: float = 1.0e-6
    mixing_beta: float = 0.7
    diagonalization: str = "david"

    def validate(self) -> None:
        """Raise ValueError on clearly invalid settings."""
        if self.ecutwfc <= 0:
            raise ValueError(f"ecutwfc must be positive, got {self.ecutwfc}")
        if self.ecutrho <= 0:
            raise ValueError(f"ecutrho must be positive, got {self.ecutrho}")
        if self.ecutrho < self.ecutwfc:
            raise ValueError(
                f"ecutrho ({self.ecutrho}) should not be smaller than ecutwfc ({self.ecutwfc})"
            )
        if not (0.0 < self.conv_thr < 1.0):
            raise ValueError(f"conv_thr must be in (0, 1), got {self.conv_thr}")
        if not (0.0 < self.mixing_beta <= 1.0):
            raise ValueError(f"mixing_beta must be in (0, 1], got {self.mixing_beta}")
        if self.diagonalization not in ("david", "cg", "ppcg", "rmm-davidson"):
            raise ValueError(f"Unknown diagonalization algorithm: {self.diagonalization!r}")


@dataclass
class PhysicalOptions:
    """
    Physical approximations and occupation options.

    Parameters
    ----------
    smearing:
        Occupation smearing type.  Use ``"gaussian"`` for insulators,
        ``"methfessel-paxton"`` for metals, or ``"marzari-vanderbilt"`` (cold
        smearing) for metals where high accuracy matters.
    degauss:
        Smearing width in Ry.  Typical values: 0.01–0.02 for metals,
        0.001–0.005 for insulators with smearing forced by spin.
    k_points_grid:
        Monkhorst-Pack grid as ``[kx, ky, kz]``.  Use ``[1, 1, 1]``
        for Γ-only calculations (molecules).
    k_points_shift:
        Monkhorst-Pack shift vector ``[sx, sy, sz]`` where each component
        is 0 (unshifted) or 1 (shifted by half a mesh spacing).
    """

    smearing: str = "gaussian"
    degauss: float = 0.01
    k_points_grid: list[int] = field(default_factory=lambda: [4, 4, 4])
    k_points_shift: list[int] = field(default_factory=lambda: [0, 0, 0])

    def validate(self) -> None:
        """Raise ValueError on clearly invalid settings."""
        valid_smearing = {
            "gaussian",
            "methfessel-paxton",
            "marzari-vanderbilt",
            "fermi-dirac",
        }
        if self.smearing not in valid_smearing:
            raise ValueError(
                f"Unknown smearing type {self.smearing!r}. "
                f"Valid choices: {', '.join(sorted(valid_smearing))}"
            )
        if self.degauss < 0:
            raise ValueError(f"degauss must be non-negative, got {self.degauss}")
        if len(self.k_points_grid) != 3 or any(k < 1 for k in self.k_points_grid):
            raise ValueError(
                f"k_points_grid must be a list of three positive integers, got {self.k_points_grid}"
            )
        if len(self.k_points_shift) != 3 or any(s not in (0, 1) for s in self.k_points_shift):
            raise ValueError(
                f"k_points_shift must be a list of three values in {{0, 1}}, "
                f"got {self.k_points_shift}"
            )


@dataclass(frozen=True)
class CalculationContext:
    """
    Complete, self-contained description of a single QE pw.x calculation.

    This is the canonical input to :func:`build_qe_input_namelist` and
    :func:`generate_qe_input_text`.  It holds **everything** needed to produce
    a deterministic ``pw.in`` without accessing session state or the filesystem.

    Parameters
    ----------
    calculation:
        QE calculation keyword: ``"scf"``, ``"nscf"``, ``"relax"``, or
        ``"vc-relax"``.
    numerical:
        Convergence and mixing settings.
    physical:
        Smearing, k-points, and other physical approximations.
    classification:
        Optional heuristic classification of the structure.  Used by the
        default-policy helpers to set smarter defaults but not required.
    """

    calculation: str
    numerical: NumericalSettings
    physical: PhysicalOptions
    classification: StructureClassification | None = None

    def validate(self) -> None:
        """Raise ValueError if the context is inconsistent."""
        valid_calculations = {"scf", "nscf", "relax", "vc-relax", "md", "vc-md"}
        if self.calculation not in valid_calculations:
            raise ValueError(
                f"Unknown calculation type {self.calculation!r}. "
                f"Valid choices: {', '.join(sorted(valid_calculations))}"
            )
        self.numerical.validate()
        self.physical.validate()


# ---------------------------------------------------------------------------
# Structure classification
# ---------------------------------------------------------------------------


def classify_structure(atoms: "Atoms") -> StructureClassification:
    """
    Classify an ASE Atoms object heuristically.

    The classification is **fast** (no DFT) and intentionally conservative —
    the goal is to guide default-parameter selection, not to predict physics.

    Parameters
    ----------
    atoms:
        The structure to classify.

    Returns
    -------
    StructureClassification
        Dimensionality, likely electronic character, likely magnetic character,
        periodic direction flags, vacuum direction indices, and human-readable notes.
    """
    notes: list[str] = []
    elements: set[str] = set(atoms.get_chemical_symbols())

    # --- Periodic boundary conditions and vacuum detection ---
    pbc = atoms.pbc  # shape (3,) bool array
    cell = atoms.cell.array  # 3×3 matrix in Å

    vacuum_directions: list[int] = []
    periodic_directions: list[bool] = list(pbc)

    for axis in range(3):
        vector_length = float(math.sqrt(sum(cell[axis, i] ** 2 for i in range(3))))
        if vector_length < 0.1:
            # Degenerate cell vector — treat as vacuum
            vacuum_directions.append(axis)
            periodic_directions[axis] = False
            continue

        if not pbc[axis]:
            vacuum_directions.append(axis)
            periodic_directions[axis] = False
            continue

        # Even if pbc is True, check for an unusually large cell vector that
        # is likely a vacuum padding along this axis.
        if _is_vacuum_axis(atoms, axis, vector_length):
            vacuum_directions.append(axis)
            notes.append(
                f"Axis {axis} appears to be a vacuum gap "
                f"(cell vector ≈ {vector_length:.1f} Å, >{VACUUM_FRACTION_THRESHOLD*100:.0f}% vacuum)."
            )

    periodic_count = sum(periodic_directions)
    if periodic_count == 3:
        dimensionality = Dimensionality.D3
        notes.append("3D periodic bulk structure.")
    elif periodic_count == 2:
        dimensionality = Dimensionality.D2
        notes.append("2D slab or monolayer — consider Γ-point or dense 2D k-mesh.")
    elif periodic_count == 1:
        dimensionality = Dimensionality.D1
        notes.append("1D chain or nanowire — dense k-sampling along periodic axis.")
    else:
        dimensionality = Dimensionality.D0
        notes.append("0D molecule or cluster — consider Γ-only k-sampling.")

    # --- Electronic character heuristic ---
    metallic_elements = elements & _TYPICALLY_METALLIC_ELEMENTS
    if metallic_elements and not (elements - _TYPICALLY_METALLIC_ELEMENTS):
        # All elements are typically metallic
        electronic_character = ElectronicCharacter.METAL
        notes.append(
            f"Likely metallic: all elements ({', '.join(sorted(metallic_elements))}) "
            "are typically metallic."
        )
    elif metallic_elements:
        # Mixed — could be metallic compound or insulating oxide
        electronic_character = ElectronicCharacter.UNKNOWN
        notes.append(
            f"Mixed composition (metallic: {', '.join(sorted(metallic_elements))}, "
            f"non-metallic: {', '.join(sorted(elements - metallic_elements))}). "
            "Electronic character is unknown without a calculation."
        )
    else:
        # No typically metallic elements → probably insulating / semiconducting
        electronic_character = ElectronicCharacter.INSULATOR
        notes.append(
            "No typically metallic elements found — likely insulating or semiconducting."
        )

    # --- Magnetic character heuristic ---
    magnetic_elements = elements & _POTENTIALLY_MAGNETIC_ELEMENTS
    if magnetic_elements:
        magnetic_character = MagneticCharacter.MAGNETIC
        notes.append(
            f"Potentially magnetic: {', '.join(sorted(magnetic_elements))} "
            "have partially filled d or f shells."
        )
    else:
        magnetic_character = MagneticCharacter.NONMAGNETIC
        notes.append("No d/f-shell elements — likely non-magnetic.")

    return StructureClassification(
        dimensionality=dimensionality,
        electronic_character=electronic_character,
        magnetic_character=magnetic_character,
        periodic_directions=tuple(periodic_directions),  # type: ignore[arg-type]
        vacuum_directions=tuple(vacuum_directions),
        notes=notes,
    )


def _is_vacuum_axis(atoms: "Atoms", axis: int, vector_length: float) -> bool:
    """Return True if a cell axis appears to be mostly vacuum."""
    if vector_length < VACUUM_THRESHOLD_ANGSTROM:
        return False

    positions = atoms.get_positions()
    if len(positions) == 0:
        return False

    # Project all atomic positions onto this axis direction
    cell_vector = atoms.cell.array[axis]
    unit_vector = cell_vector / vector_length
    projections = [float(sum(pos[i] * unit_vector[i] for i in range(3))) for pos in positions]

    atom_span = max(projections) - min(projections)
    vacuum_estimate = vector_length - atom_span
    return vacuum_estimate / vector_length > VACUUM_FRACTION_THRESHOLD


# ---------------------------------------------------------------------------
# Scientific suggestion helpers
# ---------------------------------------------------------------------------


def suggest_cutoffs_from_pseudo_entries(
    resolved_entries: dict[str, dict[str, Any]],
) -> tuple[float, float]:
    """
    Suggest ecutwfc and ecutrho from SSSP manifest data.

    The SSSP manifest stores per-element recommended energy cutoffs.  This
    function reads those values and returns the **element-wise maxima**, which
    is the conservative choice recommended by the SSSP documentation.

    Parameters
    ----------
    resolved_entries:
        Mapping of element symbol → manifest entry dict.  Each entry may
        contain ``"cutoff"`` (wavefunction cutoff in Ry) and ``"dual"``
        (ratio ecutrho / ecutwfc).  If both are absent, falls back to the
        module-level defaults.

    Returns
    -------
    (ecutwfc_ry, ecutrho_ry):
        Suggested wavefunction and charge-density cutoffs in Ry.
    """
    if not resolved_entries:
        return FALLBACK_ECUTWFC_RY, FALLBACK_ECUTRHO_RY

    max_ecutwfc = FALLBACK_ECUTWFC_RY
    max_ecutrho = FALLBACK_ECUTRHO_RY

    for element, entry in resolved_entries.items():
        # SSSP manifest stores cutoffs in Ry
        raw_cutoff = entry.get("cutoff")
        raw_dual = entry.get("dual")

        if raw_cutoff is None:
            continue

        try:
            ecutwfc = float(raw_cutoff)
        except (TypeError, ValueError):
            continue

        if ecutwfc <= 0:
            continue

        max_ecutwfc = max(max_ecutwfc, ecutwfc)

        if raw_dual is not None:
            try:
                dual = float(raw_dual)
                max_ecutrho = max(max_ecutrho, ecutwfc * dual)
            except (TypeError, ValueError):
                max_ecutrho = max(max_ecutrho, ecutwfc * 4.0)
        else:
            # SSSP default dual is 4 for norm-conserving, 8 for ultrasoft
            max_ecutrho = max(max_ecutrho, ecutwfc * 4.0)

    # Round to clean values (nearest 5 Ry)
    ecutwfc_ry = _round_to_nearest(max_ecutwfc, 5.0)
    ecutrho_ry = _round_to_nearest(max_ecutrho, 5.0)

    return ecutwfc_ry, ecutrho_ry


def suggest_kpoints_from_cell(
    atoms: "Atoms",
    *,
    target_kpoint_density: float = DEFAULT_KPOINT_DENSITY,
) -> list[int]:
    """
    Suggest a Monkhorst-Pack k-point grid from cell geometry.

    The grid is computed so that the reciprocal-space k-point density is
    approximately ``target_kpoint_density`` points per Å⁻¹ along each
    reciprocal lattice vector.

    For non-periodic directions (vacuum slab, molecule), the grid is set to 1.

    Parameters
    ----------
    atoms:
        The structure for which to suggest a grid.
    target_kpoint_density:
        Desired number of k-points per Å⁻¹ of reciprocal-space length.
        The default 0.04 Å⁻¹ gives roughly 8×8×8 for a 3 Å cubic cell
        (typical metal) and 4×4×4 for a 6 Å cell.

    Returns
    -------
    [kx, ky, kz]:
        Suggested Monkhorst-Pack grid.  Each component is at least 1.
    """
    if target_kpoint_density <= 0:
        raise ValueError(f"target_kpoint_density must be positive, got {target_kpoint_density}")

    pbc = list(atoms.pbc)
    cell = atoms.cell.array  # 3×3 in Å

    # |a*_i| = 2π / |a_i|  in Å⁻¹ (for orthogonal cells this is exact; for
    # non-orthogonal cells we use the real-space vector length as a proxy which
    # slightly over-samples, but is safe).
    grid: list[int] = []
    for axis in range(3):
        if not pbc[axis]:
            grid.append(1)
            continue

        vector_length = float(math.sqrt(sum(cell[axis, i] ** 2 for i in range(3))))
        if vector_length < 1e-6:
            grid.append(1)
            continue

        # |b_i| ≈ 2π / |a_i|
        reciprocal_length = 2.0 * math.pi / vector_length
        k_count = max(1, int(math.ceil(reciprocal_length / target_kpoint_density)))
        grid.append(k_count)

    return grid


# ---------------------------------------------------------------------------
# Default policy
# ---------------------------------------------------------------------------


def apply_default_policy(
    classification: StructureClassification,
    numerical: NumericalSettings,
    physical: PhysicalOptions,
) -> tuple[NumericalSettings, PhysicalOptions]:
    """
    Apply science-driven adjustments to numerical and physical settings.

    This function encapsulates the **default policy logic** — the set of rules
    that map a structure classification onto recommended parameter values.
    It does **not** override values that were explicitly set by the user (i.e.
    it is called only when the user has not specified a parameter).

    Current rules
    -------------
    - 0D structures → k-grid forced to [1, 1, 1] (Γ only)
    - Likely metals → switch smearing to ``"methfessel-paxton"``, degauss 0.02
    - Likely insulators → keep ``"gaussian"``, tighten degauss to 0.005
    - Mixed / unknown → keep ``"gaussian"`` smearing with default degauss
    - No changes to cutoffs (caller should use suggest_cutoffs_from_pseudo_entries)

    Parameters
    ----------
    classification:
        Result of :func:`classify_structure`.
    numerical:
        Base numerical settings (may be mutated via dataclass replacement).
    physical:
        Base physical options (may be mutated via dataclass replacement).

    Returns
    -------
    (numerical, physical):
        Adjusted settings — new objects, originals are not mutated.
    """
    import dataclasses

    phys_updates: dict[str, Any] = {}
    num_updates: dict[str, Any] = {}

    # k-grid: force Γ-only for molecules
    if classification.dimensionality == Dimensionality.D0:
        phys_updates["k_points_grid"] = [1, 1, 1]

    # Smearing policy based on electronic character
    if classification.electronic_character == ElectronicCharacter.METAL:
        if physical.smearing == "gaussian":
            # Methfessel-Paxton reduces Pulay noise for metals
            phys_updates["smearing"] = "methfessel-paxton"
            phys_updates["degauss"] = 0.02
    elif classification.electronic_character == ElectronicCharacter.INSULATOR:
        # Tighter degauss prevents artificial broadening of the gap
        if physical.smearing == "gaussian" and physical.degauss > 0.005:
            phys_updates["degauss"] = 0.005

    if phys_updates:
        physical = dataclasses.replace(physical, **phys_updates)
    if num_updates:  # Reserved for future numeric policy rules
        numerical = dataclasses.replace(numerical, **num_updates)

    return numerical, physical


# ---------------------------------------------------------------------------
# Input builder
# ---------------------------------------------------------------------------


def build_qe_input_namelist(ctx: CalculationContext) -> dict[str, dict[str, Any]]:
    """
    Build the ASE-style nested namelist dict for a QE pw.x calculation.

    This function is **pure** — it receives a :class:`CalculationContext` and
    returns a plain Python dict that can be passed directly to
    ``ase.calculators.espresso.Espresso(input_data=...)``.

    Parameters
    ----------
    ctx:
        Fully populated calculation context.

    Returns
    -------
    dict:
        ``{"control": {...}, "system": {...}, "electrons": {...}, ...}``

    Notes
    -----
    The ``ATOMIC_SPECIES``, ``ATOMIC_POSITIONS``, ``CELL_PARAMETERS``, and
    ``K_POINTS`` card sections are written by ASE based on the ``Atoms`` object
    and the ``kpts`` argument to ``Espresso()``.
    """
    calc_type = ctx.calculation
    num = ctx.numerical
    phys = ctx.physical

    # &CONTROL
    control: dict[str, Any] = {
        "calculation": calc_type,
    }

    # &SYSTEM
    system: dict[str, Any] = {
        "ecutwfc": num.ecutwfc,
        "ecutrho": num.ecutrho,
        "smearing": phys.smearing,
        "degauss": phys.degauss,
    }
    if phys.degauss > 0:
        system["occupations"] = "smearing"

    # &ELECTRONS
    electrons: dict[str, Any] = {
        "conv_thr": num.conv_thr,
        "mixing_beta": num.mixing_beta,
        "diagonalization": num.diagonalization,
    }

    namelist: dict[str, dict[str, Any]] = {
        "control": control,
        "system": system,
        "electrons": electrons,
    }

    # &IONS — required for structural relaxation
    if calc_type in ("relax", "md", "vc-relax", "vc-md"):
        namelist["ions"] = {
            "ion_dynamics": "bfgs",
        }

    # &CELL — required for variable-cell relaxation
    if calc_type in ("vc-relax", "vc-md"):
        namelist["cell"] = {
            "cell_dynamics": "bfgs",
        }

    return namelist


def generate_qe_input_text(
    atoms: "Atoms",
    ctx: CalculationContext,
    pseudopotentials: dict[str, str],
    pseudo_dir: str | Path,
    *,
    run_dir: Path,
) -> tuple[str, Path, Path]:
    """
    Generate a clean ``pw.in`` file and return its text and file paths.

    Orchestrates:
    1. Building the namelist dict via :func:`build_qe_input_namelist`.
    2. Writing through ASE ``Espresso.write_inputfiles()``.
    3. Cleaning the raw ASE output via :func:`clean_qe_input_text`.
    4. Writing the cleaned text to ``pw.in`` (UI path).

    Parameters
    ----------
    atoms:
        ASE Atoms object representing the structure.
    ctx:
        Calculation context (holds all settings).
    pseudopotentials:
        Element → UPF filename mapping for ASE.
    pseudo_dir:
        Absolute or relative path to the directory containing UPF files.
    run_dir:
        Working directory for QE (output is written inside this directory).

    Returns
    -------
    (preview_text, internal_path, ui_path):
        - ``preview_text``: the cleaned ``pw.in`` content as a string
        - ``internal_path``: ASE's raw ``espresso.pwi``
        - ``ui_path``: the cleaned ``pw.in``
    """
    if not _ASE_AVAILABLE:
        raise RuntimeError(
            "ASE is not installed. Install it with: pip install ase"
        )

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    input_data = build_qe_input_namelist(ctx)
    pseudo_dir_path = Path(pseudo_dir)
    relative_pseudo_dir = Path(os.path.relpath(pseudo_dir_path, run_dir))

    profile = EspressoProfile(command="pw.x", pseudo_dir=relative_pseudo_dir)
    calculator = Espresso(
        directory=run_dir,
        profile=profile,
        input_data=input_data,
        kpts=tuple(ctx.physical.k_points_grid),
        pseudopotentials=pseudopotentials,
    )

    atoms_copy = atoms.copy()
    atoms_copy.calc = calculator
    calculator.write_inputfiles(atoms_copy, properties=["energy"])

    internal_path = run_dir / "espresso.pwi"
    ui_path = run_dir / "pw.in"

    generated_text = internal_path.read_text(encoding="utf-8")
    preview_text = clean_qe_input_text(generated_text, calc_type=ctx.calculation)
    ui_path.write_text(preview_text, encoding="utf-8")

    return preview_text, internal_path, ui_path


def clean_qe_input_text(input_text: str, *, calc_type: str) -> str:
    """
    Remove empty namelists from a QE ``pw.in`` file.

    ASE sometimes writes namelists that contain no parameters (typically
    ``&IONS /`` or ``&CELL /`` for an ``scf`` run).  This function strips
    those empty blocks while preserving all required namelists for the given
    calculation type.

    Parameters
    ----------
    input_text:
        Raw QE input text, e.g. as written by ``Espresso.write_inputfiles()``.
    calc_type:
        QE calculation type string (``"scf"``, ``"relax"``, ``"vc-relax"``, …).
        Used to determine which namelists are required.

    Returns
    -------
    str:
        Cleaned input text with trailing newline.
    """
    normalised_calc = calc_type.lower().strip()
    required = _REQUIRED_NAMELISTS.get(normalised_calc, frozenset({"&CONTROL", "&SYSTEM", "&ELECTRONS"}))

    def _replacer(match: re.Match) -> str:
        namelist_name = match.group(1).upper()
        if namelist_name in required:
            return match.group(0)
        return ""

    cleaned = re.sub(r"(?ms)^(&[A-Z_]+)\n(?:\s*\n)*\s*/\n", _replacer, input_text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return f"{cleaned}\n"


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_default_context(
    atoms: "Atoms",
    *,
    calculation: str = "scf",
    resolved_entries: dict[str, dict[str, Any]] | None = None,
    target_kpoint_density: float = DEFAULT_KPOINT_DENSITY,
    apply_policy: bool = True,
) -> CalculationContext:
    """
    Build a :class:`CalculationContext` with science-driven defaults.

    This is the recommended entry-point for programmatic use (tests, CLI tools,
    batch scripts).  The UI page uses it as a starting point and then applies
    user widget overrides on top.

    Parameters
    ----------
    atoms:
        The structure to compute defaults for.
    calculation:
        QE calculation type.
    resolved_entries:
        Optional SSSP manifest entries for cutoff suggestion.
    target_kpoint_density:
        Reciprocal-space k-point density for the grid suggestion.
    apply_policy:
        If True, calls :func:`apply_default_policy` to apply classification-
        driven adjustments on top of the raw defaults.

    Returns
    -------
    CalculationContext
    """
    classification = classify_structure(atoms)

    if resolved_entries:
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries(resolved_entries)
    else:
        ecutwfc, ecutrho = FALLBACK_ECUTWFC_RY, FALLBACK_ECUTRHO_RY

    kgrid = suggest_kpoints_from_cell(atoms, target_kpoint_density=target_kpoint_density)

    numerical = NumericalSettings(
        ecutwfc=ecutwfc,
        ecutrho=ecutrho,
    )
    physical = PhysicalOptions(
        k_points_grid=kgrid,
    )

    if apply_policy:
        numerical, physical = apply_default_policy(classification, numerical, physical)

    return CalculationContext(
        calculation=calculation,
        numerical=numerical,
        physical=physical,
        classification=classification,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _round_to_nearest(value: float, step: float) -> float:
    """Round ``value`` up to the nearest multiple of ``step``."""
    return math.ceil(value / step) * step
