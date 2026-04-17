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
    classify_structure()      select_cutoffs(entries, preset=STANDARD)
        │                     suggest_kpoints_from_cell()
        ▼                             │
    CalculationContext  ◄─────────────┘   (includes CutoffDecision)
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
    Dimensionality           Enum  — 0D / 1D / 2D / 3D
    ElectronicCharacter      Enum  — metal / insulator / unknown
    MagneticCharacter        Enum  — magnetic / nonmagnetic / unknown
    VdwMethod                Enum  — curated van der Waals correction options
    ControlSettings          dataclass — QE CONTROL prefix/outdir settings
    FeatureReason            dataclass — stable code + human-readable explanation
    FeatureState             dataclass — requested/rendered feature metadata
    StructureClassification  dataclass — output of classify_structure()
    NumericalSettings        dataclass — convergence / mixing knobs
    PhysicalOptions          dataclass — smearing, magnetism, etc.
    HubbardUSpeciesParameter  dataclass — one user-defined U value by species
    HubbardUSettings          dataclass — DFT+U configuration and metadata export
    VdwSettings               dataclass — vdW method selection and metadata export
    CalculationContext        dataclass — full input to the builder

Re-exported from cutoff_presets (import directly from there for type hints):
    CutoffPreset             Enum — quick / standard / publication
    CutoffDecision           dataclass — full provenance of a cutoff choice

Service functions:
    classify_structure(atoms) -> StructureClassification
    get_atomic_species_order(atoms) -> tuple[str, ...]
    select_cutoffs(resolved_entries, *, preset) -> CutoffDecision
    suggest_cutoffs_from_pseudo_entries(resolved_entries) -> tuple[float, float]
        (backward-compatible wrapper around select_cutoffs)
    suggest_kpoints_from_cell(atoms, *, target_kpoint_density) -> list[int]
    build_qe_input_namelist(ctx) -> dict
    generate_qe_input_text(atoms, ctx, pseudopotentials, pseudo_dir, *, run_dir)
    clean_qe_input_text(input_text, *, calc_type) -> str

Spin-orbit coupling (SOC) is supported through the same service layer. SOC is
modeled as a dedicated noncollinear mode where
``noncolin=.true.`` and ``lspinorb=.true.``, and generation is blocked unless
all resolved pseudopotentials advertise fully relativistic support.

van der Waals (vdW) support is modeled through a dedicated :class:`VdwSettings`
domain model. Semiempirical methods render through ``vdw_corr`` while curated
nonlocal presets render through ``input_dft``.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from .cutoff_presets import (
    HEURISTIC_ECUTRHO_RY,
    HEURISTIC_ECUTWFC_RY,
    PRESET_CONV_THR,
    PRESET_DESCRIPTIONS,
    PRESET_MULTIPLIERS,
    CutoffDecision,
    CutoffPreset,
)
from .pseudopotentials import ensure_soc_compatible_pseudopotentials, inspect_soc_compatibility
from .qe_control import (
    DEFAULT_OUTDIR,
    DEFAULT_PREFIX_FALLBACK,
    default_qe_prefix,
    describe_qe_prefix_sanitization,
    normalize_qe_outdir,
    resolve_qe_outdir,
    sanitize_qe_prefix,
)

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
    "bands": frozenset({"&CONTROL", "&SYSTEM", "&ELECTRONS"}),
}

#: Conservative fallback energy cutoffs (Ry) when no SSSP data is available.
#: These are heuristic values — not derived from any specific pseudopotential.
#: They match the heuristic defaults defined in :mod:`cutoff_presets`.
FALLBACK_ECUTWFC_RY: float = HEURISTIC_ECUTWFC_RY
FALLBACK_ECUTRHO_RY: float = HEURISTIC_ECUTRHO_RY

#: Default k-point spacing (Δk in Å⁻¹) for automatic MP grid suggestion for standard workflows (SCF/relax).
DEFAULT_KPOINT_SPACING_SCF: float = 0.15
#: Default k-point spacing (Δk in Å⁻¹) for dense workflows (NSCF/DOS).
DEFAULT_KPOINT_SPACING_DOS: float = 0.05

ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS: frozenset[str] = frozenset({"crystal", "angstrom"})
FEATURE_STATE_STATUSES: frozenset[str] = frozenset({"rendered", "adjusted", "skipped", "blocked"})


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Dimensionality(str, Enum):
    """Physical dimensionality of the simulation cell."""

    D0 = "0D"  #: Molecule or cluster (all directions non-periodic or vacuum)
    D1 = "1D"  #: Nanowire or nanotube
    D2 = "2D"  #: Slab or monolayer
    D3 = "3D"  #: Bulk crystal


class WorkflowType(str, Enum):
    """Supported Quantum ESPRESSO computational workflows."""

    SCF = "scf"
    NSCF = "nscf"
    RELAX = "relax"
    VC_RELAX = "vc-relax"
    BANDS = "bands"
    DOS = "dos"


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


class VdwMethod(str, Enum):
    """Curated van der Waals correction options supported by QE Studio."""

    NONE = "none"
    D2 = "grimme-d2"
    D3_ZERO = "grimme-d3-zero"
    D3_BJ = "grimme-d3-bj"
    TS = "ts"
    MBD = "mbd"
    VDW_DF = "vdw-df"
    VDW_DF2 = "vdw-df2"
    RVV10 = "rvv10"


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
    magnetic_elements: tuple[str, ...]
    periodic_directions: tuple[bool, bool, bool]
    vacuum_directions: tuple[int, ...]
    notes: list[str]


@dataclass(frozen=True)
class ControlSettings:
    """QE CONTROL settings tracked separately from physics options."""

    requested_prefix: str = DEFAULT_PREFIX_FALLBACK
    prefix: str = DEFAULT_PREFIX_FALLBACK
    requested_outdir: str = DEFAULT_OUTDIR
    outdir: str = DEFAULT_OUTDIR

    def validate(self) -> None:
        if not self.prefix or not self.prefix.strip():
            raise ValueError("Rendered QE prefix must be non-empty")
        if self.prefix != sanitize_qe_prefix(self.prefix):
            raise ValueError(f"Rendered QE prefix must already be sanitized, got {self.prefix!r}")
        if not self.outdir or not self.outdir.strip():
            raise ValueError("Rendered QE outdir must be non-empty")

    def resolve_outdir(self, run_dir: Path | str) -> Path:
        """Resolve the rendered outdir against one run directory."""
        return resolve_qe_outdir(run_dir, self.outdir)

    def to_metadata(self, *, run_dir: Path | str) -> dict[str, Any]:
        """Return a stable metadata payload for CONTROL settings."""
        return {
            "requested_prefix": self.requested_prefix,
            "rendered_prefix": self.prefix,
            "requested_outdir": self.requested_outdir,
            "rendered_outdir": self.outdir,
            "resolved_outdir": str(self.resolve_outdir(run_dir)),
        }


@dataclass(frozen=True)
class FeatureReason:
    """Stable machine-readable reason metadata for rendered feature states."""

    code: str
    message: str

    def validate(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("FeatureReason.code must be non-empty")
        if not self.message or not self.message.strip():
            raise ValueError("FeatureReason.message must be non-empty")

    def to_metadata(self) -> dict[str, str]:
        """Return a stable JSON-friendly representation."""
        return {
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class FeatureState:
    """Requested versus rendered state for one user-visible feature."""

    requested: dict[str, Any]
    rendered: dict[str, Any]
    status: str = "rendered"
    reasons: tuple[FeatureReason, ...] = ()

    def validate(self) -> None:
        if self.status not in FEATURE_STATE_STATUSES:
            raise ValueError(
                f"FeatureState.status must be one of {sorted(FEATURE_STATE_STATUSES)}, got {self.status!r}"
            )
        for reason in self.reasons:
            reason.validate()

    def to_metadata(self) -> dict[str, Any]:
        """Return a stable JSON-friendly representation."""
        self.validate()
        return {
            "requested": dict(self.requested),
            "rendered": dict(self.rendered),
            "status": self.status,
            "reasons": [reason.to_metadata() for reason in self.reasons],
        }


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


@dataclass(frozen=True)
class HubbardUSpeciesParameter:
    """One species-level Hubbard U parameter in eV."""

    species: str
    u_ev: float
    source: str = "user_defined"

    def validate(self) -> None:
        """Raise ValueError if the Hubbard U parameter is invalid."""
        if not self.species or not self.species.strip():
            raise ValueError("Hubbard U species labels must be non-empty")
        if not math.isfinite(self.u_ev):
            raise ValueError(f"Hubbard U for {self.species!r} must be finite, got {self.u_ev!r}")
        if self.u_ev <= 0:
            raise ValueError(f"Hubbard U for {self.species!r} must be positive, got {self.u_ev!r}")
        if not self.source or not self.source.strip():
            raise ValueError("Hubbard U parameter source labels must be non-empty")

    def to_metadata(self) -> dict[str, Any]:
        """Return a stable, provenance-friendly representation."""
        return {
            "species": self.species,
            "u_ev": self.u_ev,
            "source": self.source,
        }


@dataclass(frozen=True)
class HubbardUSettings:
    """Minimal species-based DFT+U configuration."""

    METHOD_WARNING: ClassVar[str] = (
        "Hubbard U values are method-dependent and must be chosen and validated by the user."
    )

    enabled: bool = False
    species_parameters: dict[str, HubbardUSpeciesParameter] = field(default_factory=dict)

    @property
    def warning_message(self) -> str:
        """Return the standard DFT+U method-dependence warning."""
        return self.METHOD_WARNING

    def validate(self, species_order: tuple[str, ...] | None = None) -> None:
        """Raise ValueError if the DFT+U settings are invalid."""
        if not self.enabled:
            return

        if not self.species_parameters:
            raise ValueError("DFT+U is enabled but no Hubbard U species are configured")

        allowed_species = set(species_order or ())
        for configured_species, parameter in self.species_parameters.items():
            parameter.validate()
            if configured_species != parameter.species:
                raise ValueError(
                    f"Hubbard U parameter key {configured_species!r} does not match species {parameter.species!r}"
                )
            if species_order is not None and configured_species not in allowed_species:
                raise ValueError(
                    f"Hubbard U configured for unknown species {configured_species!r}; "
                    f"known species: {', '.join(species_order)}"
                )

    def to_metadata(self, *, species_order: tuple[str, ...] | None = None) -> dict[str, Any]:
        """Return a stable metadata export describing the rendered DFT+U settings."""
        ordered_species = tuple(species_order or tuple(self.species_parameters))
        species_parameters: dict[str, dict[str, Any]] = {}
        if self.enabled:
            for species in ordered_species:
                parameter = self.species_parameters.get(species)
                if parameter is not None:
                    species_parameters[species] = parameter.to_metadata()

        return {
            "enabled": self.enabled,
            "syntax": "legacy_system",
            "units": "eV",
            "method_warning": self.warning_message,
            "species_order": list(ordered_species),
            "species_parameters": species_parameters,
        }


@dataclass(frozen=True)
class VdwSettings:
    """Curated van der Waals correction configuration."""

    SEMIEMPIRICAL_DEFAULTS_NOTE: ClassVar[str] = (
        "Using Quantum ESPRESSO defaults for optional D2/D3/TS/MBD tuning parameters in this phase."
    )
    NONLOCAL_WARNING: ClassVar[str] = (
        "Nonlocal vdW methods are rendered through input_dft and therefore override the base XC functional "
        "instead of adding a separate correction."
    )

    method: VdwMethod = VdwMethod.NONE

    @property
    def normalized_method(self) -> VdwMethod:
        """Return the vdW method as a validated enum."""
        if isinstance(self.method, VdwMethod):
            return self.method
        try:
            return VdwMethod(str(self.method))
        except ValueError as exc:
            raise ValueError(f"Unknown vdW method {self.method!r}") from exc

    @property
    def enabled(self) -> bool:
        """Return whether a vdW correction is active."""
        return self.normalized_method is not VdwMethod.NONE

    @property
    def backend(self) -> str:
        """Return the QE rendering backend for the selected method."""
        return str(_VDW_METHOD_RENDER_REGISTRY[self.normalized_method]["backend"])

    @property
    def warning_message(self) -> str | None:
        """Return any contextual warning or note for the selected method."""
        return _VDW_METHOD_RENDER_REGISTRY[self.normalized_method].get("warning")

    def validate(self) -> None:
        """Raise ValueError if the vdW configuration is invalid."""
        method = self.normalized_method
        if method not in _VDW_METHOD_RENDER_REGISTRY:
            raise ValueError(f"Unknown vdW method {method!r}")
        system_fields = _VDW_METHOD_RENDER_REGISTRY[method]["system"]
        has_vdw_corr = "vdw_corr" in system_fields
        has_input_dft = "input_dft" in system_fields
        if has_vdw_corr and has_input_dft:
            raise ValueError("vdW methods must render through either vdw_corr or input_dft, not both")

    def to_metadata(self) -> dict[str, Any]:
        """Return a stable provenance-friendly representation."""
        method = self.normalized_method
        return {
            "enabled": self.enabled,
            "method": method.value,
            "backend": self.backend,
            "rendered_system_fields": render_vdw_system_fields(self),
            "warning": self.warning_message,
        }


_VDW_METHOD_RENDER_REGISTRY: dict[VdwMethod, dict[str, Any]] = {
    VdwMethod.NONE: {
        "backend": "none",
        "system": {},
        "warning": None,
    },
    VdwMethod.D2: {
        "backend": "semiempirical",
        "system": {"vdw_corr": "grimme-d2"},
        "warning": VdwSettings.SEMIEMPIRICAL_DEFAULTS_NOTE,
    },
    VdwMethod.D3_ZERO: {
        "backend": "semiempirical",
        "system": {"vdw_corr": "grimme-d3", "dftd3_version": 3},
        "warning": VdwSettings.SEMIEMPIRICAL_DEFAULTS_NOTE,
    },
    VdwMethod.D3_BJ: {
        "backend": "semiempirical",
        "system": {"vdw_corr": "grimme-d3", "dftd3_version": 4},
        "warning": VdwSettings.SEMIEMPIRICAL_DEFAULTS_NOTE,
    },
    VdwMethod.TS: {
        "backend": "semiempirical",
        "system": {"vdw_corr": "ts"},
        "warning": VdwSettings.SEMIEMPIRICAL_DEFAULTS_NOTE,
    },
    VdwMethod.MBD: {
        "backend": "semiempirical",
        "system": {"vdw_corr": "mbd"},
        "warning": VdwSettings.SEMIEMPIRICAL_DEFAULTS_NOTE,
    },
    VdwMethod.VDW_DF: {
        "backend": "nonlocal_functional",
        "system": {"input_dft": "vdW-DF"},
        "warning": VdwSettings.NONLOCAL_WARNING,
    },
    VdwMethod.VDW_DF2: {
        "backend": "nonlocal_functional",
        "system": {"input_dft": "vdW-DF2"},
        "warning": VdwSettings.NONLOCAL_WARNING,
    },
    VdwMethod.RVV10: {
        "backend": "nonlocal_functional",
        "system": {"input_dft": "rVV10"},
        "warning": VdwSettings.NONLOCAL_WARNING,
    },
}


@dataclass
class PhysicalOptions:
    """
    Physical approximations and occupation options.

    Parameters
    ----------
    occupations:
        Electronic occupations: ``"smearing"``, ``"fixed"``, or ``"tetrahedra"``.
    smearing:
        Occupation smearing type (ignored if occupations is not ``"smearing"``).
        Use ``"gaussian"`` for insulators, ``"methfessel-paxton"`` for metals,
        or ``"marzari-vanderbilt"`` (cold smearing) for metals where high accuracy matters.
    degauss:
        Smearing width in Ry.  Typical values: 0.01–0.02 for metals,
        0.001–0.005 for insulators with smearing forced by spin.
    k_points_grid:
        Monkhorst-Pack grid as ``[kx, ky, kz]``.  Use ``[1, 1, 1]``
        for Γ-only calculations (molecules).
    k_points_shift:
        Monkhorst-Pack shift vector ``[sx, sy, sz]`` where each component
        is 0 (unshifted) or 1 (shifted by half a mesh spacing).
    noncolin:
        Enable QE noncollinear mode. The current SOC implementation uses this
        only as part of the minimal SOC path.
    lspinorb:
        Enable QE spin-orbit coupling. Requires ``noncolin=True``.
    """

    occupations: str = "smearing"
    smearing: str = "gaussian"
    degauss: float = 0.01
    k_points_grid: list[int] = field(default_factory=lambda: [4, 4, 4])
    k_points_shift: list[int] = field(default_factory=lambda: [0, 0, 0])
    nspin: int = 1
    starting_magnetization: dict[str, float] = field(default_factory=dict)
    noncolin: bool = False
    lspinorb: bool = False

    def validate(self) -> None:
        """Raise ValueError on clearly invalid settings."""
        valid_occupations = {"smearing", "fixed", "tetrahedra", "tetrahedra_lin", "tetrahedra_opt"}
        if self.occupations not in valid_occupations:
            raise ValueError(
                f"Unknown occupations type {self.occupations!r}. "
                f"Valid choices: {', '.join(sorted(valid_occupations))}"
            )

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
        if self.lspinorb and not self.noncolin:
            raise ValueError("lspinorb=True requires noncolin=True")
        if (self.noncolin or self.lspinorb) and self.nspin != 1:
            raise ValueError("SOC/noncollinear mode requires nspin=1")
        if (self.noncolin or self.lspinorb) and self.starting_magnetization:
            raise ValueError("SOC/noncollinear mode is incompatible with starting_magnetization")


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
    cutoff_decision:
        Full provenance record for the ``ecutwfc`` / ``ecutrho`` selection.
        Set by :func:`make_default_context` when pseudopotential entries are
        available.  ``None`` when the context was constructed manually without
        invoking :func:`select_cutoffs`.

        Inspect :attr:`CutoffDecision.source` to distinguish metadata-driven
        (``"sssp_metadata"``) from heuristic (``"heuristic_fallback"``)
        decisions.
    resolved_entries:
        Optional raw resolved pseudopotential metadata, including ``local_path``
        for each extracted UPF. This is used by the service layer to validate
        SOC compatibility before any QE input file is written.
    species_order:
        Atomic-species order matching ASE/QE ``ATOMIC_SPECIES`` rendering.
        Needed to map species-level Hubbard U values onto ``Hubbard_U(i)``.
    hubbard_u:
        Species-based DFT+U settings. Phase 6C uses legacy ``&SYSTEM``
        rendering via ``lda_plus_u`` and ``Hubbard_U(i)`` only.
    vdw:
        van der Waals correction settings. Semiempirical methods render via
        ``vdw_corr`` while curated nonlocal presets render via ``input_dft``.
    control:
        QE CONTROL prefix / outdir settings used consistently across generation
        and downstream metadata/discovery.
    feature_states:
        Prepared requested-versus-rendered metadata for user-visible features.
    atomic_positions_coordinate_mode:
        Coordinate system used when serializing the QE ``ATOMIC_POSITIONS``
        card. Supported values are ``"crystal"`` and ``"angstrom"``.
    """

    calculation: str
    numerical: NumericalSettings
    physical: PhysicalOptions
    classification: StructureClassification | None = None
    cutoff_decision: CutoffDecision | None = None
    resolved_entries: dict[str, dict[str, Any]] | None = None
    species_order: tuple[str, ...] | None = None
    hubbard_u: HubbardUSettings = field(default_factory=HubbardUSettings)
    vdw: VdwSettings = field(default_factory=VdwSettings)
    control: ControlSettings = field(default_factory=ControlSettings)
    feature_states: dict[str, FeatureState] = field(default_factory=dict)
    atomic_positions_coordinate_mode: str = "crystal"

    def validate(self) -> None:
        """Raise ValueError if the context is inconsistent."""
        valid_calculations = {"scf", "nscf", "relax", "vc-relax", "md", "vc-md", "bands"}
        if self.calculation not in valid_calculations:
            raise ValueError(
                f"Unknown calculation type {self.calculation!r}. "
                f"Valid choices: {', '.join(sorted(valid_calculations))}"
            )
        self.numerical.validate()
        self.physical.validate()
        self.control.validate()
        if self.physical.nspin == 2 and self.physical.starting_magnetization and not self.species_order:
            raise ValueError(
                "Spin-polarized starting_magnetization rendering requires species_order when values are present"
            )
        if self.hubbard_u.enabled and not self.species_order:
            raise ValueError("DFT+U rendering requires species_order when Hubbard U is enabled")
        self.hubbard_u.validate(self.species_order)
        self.vdw.validate()
        for feature_state in self.feature_states.values():
            feature_state.validate()
        if self.atomic_positions_coordinate_mode not in ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS:
            raise ValueError(
                "atomic_positions_coordinate_mode must be one of "
                f"{sorted(ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS)}, "
                f"got {self.atomic_positions_coordinate_mode!r}"
            )


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
        magnetic_elements=tuple(magnetic_elements),
        periodic_directions=tuple(periodic_directions),  # type: ignore[arg-type]
        vacuum_directions=tuple(vacuum_directions),
        notes=notes,
    )


def get_atomic_species_order(atoms: "Atoms") -> tuple[str, ...]:
    """Return the first-occurrence species order used by ASE ``ATOMIC_SPECIES`` output."""
    seen: set[str] = set()
    ordered_species: list[str] = []
    for symbol in atoms.get_chemical_symbols():
        if symbol in seen:
            continue
        seen.add(symbol)
        ordered_species.append(symbol)
    return tuple(ordered_species)


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


def select_cutoffs(
    resolved_entries: dict[str, dict[str, Any]],
    *,
    preset: CutoffPreset = CutoffPreset.STANDARD,
) -> CutoffDecision:
    """
    Select ``ecutwfc`` and ``ecutrho`` from SSSP manifest data with quality-preset control.

    This is the **primary** cutoff-selection function.  It reads the
    ``cutoff_wfc`` and ``cutoff_rho`` fields that the SSSP manifest stores for
    each element, takes the element-wise maxima (the conservative choice
    recommended by the SSSP documentation), and then scales both values by the
    preset multiplier.

    The full decision — including per-element attribution, the preset applied,
    and whether metadata or a heuristic was used — is returned as a
    :class:`~qe_studio.services.cutoff_presets.CutoffDecision` so that callers
    can surface it in provenance records and UI displays.

    SSSP manifest field names
    -------------------------
    The SSSP 1.x manifest JSON uses **``cutoff_wfc``** and **``cutoff_rho``**
    (both in Ry) as direct recommended values.  This function reads these fields
    exactly as stored — it does **not** assume ``ecutrho = 4 × ecutwfc`` unless
    ``cutoff_rho`` is absent from an entry (in which case a conservative 4×
    fallback is applied and a warning is recorded).

    Parameters
    ----------
    resolved_entries:
        Mapping of element symbol → manifest entry dict as returned by
        :func:`~qe_studio.services.pseudopotentials.resolve_manifest_entries`.
        Each entry must contain ``"cutoff_wfc"`` (wavefunction cutoff in Ry)
        and ideally ``"cutoff_rho"`` (charge-density cutoff in Ry).  Entries
        missing ``cutoff_wfc`` are skipped with a warning.
    preset:
        Quality tier to apply.  Determines the multiplier applied to the
        recommended cutoffs and the suggested SCF convergence threshold.
        Defaults to :attr:`CutoffPreset.STANDARD`.

    Returns
    -------
    CutoffDecision:
        Frozen dataclass with ``ecutwfc_ry``, ``ecutrho_ry``, ``preset``,
        ``source``, ``method``, per-element attribution, and any warnings.

    Notes
    -----
    When ``resolved_entries`` is empty or all entries lack ``cutoff_wfc``,
    the function falls back to heuristic defaults (40 Ry / 160 Ry for
    *standard*) and sets ``source = "heuristic_fallback"``.
    """
    multiplier = PRESET_MULTIPLIERS[preset]
    warnings: list[str] = []
    per_element: dict[str, dict[str, Any]] = {}

    # --- Pass 1: read and validate per-element SSSP data ---
    candidate_ecutwfc: float | None = None
    candidate_ecutrho: float | None = None
    limiting_element_wfc: str | None = None
    limiting_element_rho: str | None = None

    for element, entry in resolved_entries.items():
        raw_wfc = entry.get("cutoff_wfc")
        raw_rho = entry.get("cutoff_rho")

        # --- Parse wavefunction cutoff ---
        if raw_wfc is None:
            warnings.append(
                f"[{element}] Missing 'cutoff_wfc' in SSSP manifest entry — element skipped."
            )
            continue
        try:
            elem_wfc = float(raw_wfc)
        except (TypeError, ValueError):
            warnings.append(
                f"[{element}] 'cutoff_wfc' = {raw_wfc!r} is not a valid number — element skipped."
            )
            continue
        if elem_wfc <= 0:
            warnings.append(
                f"[{element}] 'cutoff_wfc' = {elem_wfc} is not positive — element skipped."
            )
            continue

        # --- Parse charge-density cutoff ---
        if raw_rho is None:
            # SSSP metadata missing cutoff_rho: apply conservative 4× heuristic
            elem_rho = elem_wfc * 4.0
            warnings.append(
                f"[{element}] 'cutoff_rho' absent in SSSP manifest. "
                f"Using heuristic 4× fallback: {elem_rho:.1f} Ry."
            )
        else:
            try:
                elem_rho = float(raw_rho)
            except (TypeError, ValueError):
                elem_rho = elem_wfc * 4.0
                warnings.append(
                    f"[{element}] 'cutoff_rho' = {raw_rho!r} is not a valid number. "
                    f"Using heuristic 4× fallback: {elem_rho:.1f} Ry."
                )

        # --- Track per-element data ---
        per_element[element] = {
            "cutoff_wfc_ry": elem_wfc,
            "cutoff_rho_ry": elem_rho,
            "pseudopotential": entry.get("pseudopotential", "unknown"),
            "filename": entry.get("filename", "unknown"),
            "limiting": False,  # updated after finding maxima
        }

        # --- Track running maxima ---
        if candidate_ecutwfc is None or elem_wfc > candidate_ecutwfc:
            candidate_ecutwfc = elem_wfc
            limiting_element_wfc = element
        if candidate_ecutrho is None or elem_rho > candidate_ecutrho:
            candidate_ecutrho = elem_rho
            limiting_element_rho = element

    # --- Pass 2: apply preset multiplier and round ---
    if candidate_ecutwfc is None or candidate_ecutrho is None:
        # No usable SSSP data — full heuristic fallback
        if resolved_entries:
            warnings.append(
                "All SSSP manifest entries were skipped (missing or invalid 'cutoff_wfc'). "
                "Using conservative heuristic fallback values."
            )
        scaled_wfc = HEURISTIC_ECUTWFC_RY * multiplier
        scaled_rho = HEURISTIC_ECUTRHO_RY * multiplier
        ecutwfc_ry = _round_to_nearest(scaled_wfc, 5.0)
        ecutrho_ry = _round_to_nearest(scaled_rho, 5.0)
        source = "heuristic_fallback"
        method = (
            f"Heuristic fallback ({preset.value} preset, {multiplier:.2f}×): "
            f"ecutwfc = {scaled_wfc:.1f} Ry → {ecutwfc_ry:.0f} Ry, "
            f"ecutrho = {scaled_rho:.1f} Ry → {ecutrho_ry:.0f} Ry. "
            "No SSSP metadata was available — these values are conservative "
            "estimates not tied to any specific pseudopotential."
        )
    else:
        # Metadata-driven path
        scaled_wfc = candidate_ecutwfc * multiplier
        scaled_rho = candidate_ecutrho * multiplier
        ecutwfc_ry = _round_to_nearest(scaled_wfc, 5.0)
        ecutrho_ry = _round_to_nearest(scaled_rho, 5.0)
        source = "sssp_metadata"
        n_elements = len(per_element)
        method = (
            f"SSSP metadata ({preset.value} preset, {multiplier:.2f}×): "
            f"max over {n_elements} element(s) — "
            f"ecutwfc limited by {limiting_element_wfc} "
            f"({candidate_ecutwfc:.1f} Ry → {ecutwfc_ry:.0f} Ry), "
            f"ecutrho limited by {limiting_element_rho} "
            f"({candidate_ecutrho:.1f} Ry → {ecutrho_ry:.0f} Ry)."
        )
        # Mark limiting elements
        if limiting_element_wfc and limiting_element_wfc in per_element:
            per_element[limiting_element_wfc] = dict(
                per_element[limiting_element_wfc], limiting=True
            )
        if (
            limiting_element_rho
            and limiting_element_rho in per_element
            and limiting_element_rho != limiting_element_wfc
        ):
            per_element[limiting_element_rho] = dict(
                per_element[limiting_element_rho], limiting=True
            )

    return CutoffDecision(
        ecutwfc_ry=ecutwfc_ry,
        ecutrho_ry=ecutrho_ry,
        preset=preset,
        source=source,
        method=method,
        per_element=per_element,
        warnings=warnings,
    )


def suggest_cutoffs_from_pseudo_entries(
    resolved_entries: dict[str, dict[str, Any]],
) -> tuple[float, float]:
    """
    Suggest ecutwfc and ecutrho from SSSP manifest data.

    .. deprecated::
        Prefer :func:`select_cutoffs` which returns a full
        :class:`~qe_studio.services.cutoff_presets.CutoffDecision` with
        provenance.  This wrapper is kept for backward compatibility.

    The SSSP manifest stores per-element recommended energy cutoffs in the
    ``cutoff_wfc`` and ``cutoff_rho`` fields.  This function reads those values
    and returns the **element-wise maxima** (standard-preset, 1.00× multiplier),
    which is the conservative choice recommended by the SSSP documentation.

    Parameters
    ----------
    resolved_entries:
        Mapping of element symbol → manifest entry dict.  Each entry should
        contain ``"cutoff_wfc"`` (wavefunction cutoff in Ry) and
        ``"cutoff_rho"`` (charge-density cutoff in Ry) as stored in the SSSP
        manifest JSON.  If these fields are absent, falls back to heuristic
        module-level defaults.

    Returns
    -------
    (ecutwfc_ry, ecutrho_ry):
        Suggested wavefunction and charge-density cutoffs in Ry.
    """
    decision = select_cutoffs(resolved_entries, preset=CutoffPreset.STANDARD)
    return decision.ecutwfc_ry, decision.ecutrho_ry


def suggest_kpoints_from_cell(
    atoms: "Atoms",
    *,
    classification: StructureClassification | None = None,
    target_kpoint_spacing: float = DEFAULT_KPOINT_SPACING_SCF,
) -> list[int]:
    """
    Suggest a Monkhorst-Pack k-point grid from cell geometry.

    The grid is computed so that the reciprocal-space k-point spacing is
    appropriately bounded by ``target_kpoint_spacing`` (in Å⁻¹).

    For non-periodic / vacuum directions, the grid is strictly set to 1.

    Parameters
    ----------
    atoms:
        The structure for which to suggest a grid.
    classification:
        Optional structure classification. If provided, its ``vacuum_directions``
        are aggressively used to force purely Gamma-point (kz=1) integration
        across non-periodic bounds.
    target_kpoint_spacing:
        Desired maximum spacing between k-points in reciprocal space (Å⁻¹).
        The default 0.15 Å⁻¹ gives roughly 4×4×4 for a 6 Å cell. Dense
        workflows like DOS or NSCF typically use 0.05 Å⁻¹.

    Returns
    -------
    [kx, ky, kz]:
        Suggested Monkhorst-Pack grid.  Each component is at least 1.
    """
    if target_kpoint_spacing <= 0:
        raise ValueError(f"target_kpoint_spacing must be positive, got {target_kpoint_spacing}")

    pbc = list(atoms.pbc)
    cell = atoms.cell.array  # 3×3 in Å

    # |a*_i| = 2π / |a_i|  in Å⁻¹ (for orthogonal cells this is exact; for
    # non-orthogonal cells we use the real-space vector length as a proxy which
    # slightly over-samples, but is safe).
    grid: list[int] = []
    for axis in range(3):
        # Force 1 if explicitly vacuum/non-periodic
        if not pbc[axis] or (classification and axis in classification.vacuum_directions):
            grid.append(1)
            continue

        vector_length = float(math.sqrt(sum(cell[axis, i] ** 2 for i in range(3))))
        if vector_length < 1e-6:
            grid.append(1)
            continue

        # |b_i| ≈ 2π / |a_i|
        reciprocal_length = 2.0 * math.pi / vector_length
        k_count = max(1, int(math.ceil(reciprocal_length / target_kpoint_spacing)))
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
    - Likely insulators → switch occupations to ``"fixed"``
    - Likely metals → switch occupations to ``"smearing"``, smearing to ``"methfessel-paxton"``, degauss 0.02
    - Mixed / unknown → switch occupations to ``"smearing"``, keep ``"gaussian"`` with default degauss
    - No changes to cutoffs (caller should use suggest_cutoffs_from_pseudo_entries / select_cutoffs)

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
        phys_updates["occupations"] = "smearing"
        if physical.smearing == "gaussian":
            # Methfessel-Paxton reduces Pulay noise for metals
            phys_updates["smearing"] = "methfessel-paxton"
            phys_updates["degauss"] = 0.02
    elif classification.electronic_character == ElectronicCharacter.INSULATOR:
        phys_updates["occupations"] = "fixed"
    elif classification.electronic_character == ElectronicCharacter.UNKNOWN:
        if physical.occupations != "smearing":
            phys_updates["occupations"] = "smearing"

    # Spin policy based on magnetic character
    if classification.magnetic_character == MagneticCharacter.MAGNETIC:
        if physical.nspin == 1:
            phys_updates["nspin"] = 2
            # Initialize modest ferromagnetism across magnetic elements to break symmetry
            starting_mag = {}
            for elem in classification.magnetic_elements:
                starting_mag[elem] = 0.5
            phys_updates["starting_magnetization"] = starting_mag

    if phys_updates:
        physical = dataclasses.replace(physical, **phys_updates)
    if num_updates:  # Reserved for future numeric policy rules
        numerical = dataclasses.replace(numerical, **num_updates)

    return numerical, physical


def build_control_settings(
    atoms: "Atoms" | None,
    *,
    requested_prefix: object = None,
    requested_outdir: object = None,
) -> tuple[ControlSettings, tuple[FeatureReason, ...]]:
    """Build rendered CONTROL settings plus stable reason metadata."""
    reasons: list[FeatureReason] = []

    if requested_prefix is None or not str(requested_prefix).strip():
        requested_prefix_text = default_qe_prefix(atoms)
        reasons.append(
            FeatureReason(
                code="auto_default_applied",
                message="Applied the default structure-derived QE prefix.",
            )
        )
    else:
        requested_prefix_text = str(requested_prefix).strip()

    prefix_info = describe_qe_prefix_sanitization(requested_prefix_text)
    if prefix_info["sanitized"]:
        reasons.append(
            FeatureReason(
                code="sanitized_prefix",
                message="Sanitized the requested QE prefix to a lowercase ASCII-safe value.",
            )
        )
    if prefix_info["truncated"]:
        reasons.append(
            FeatureReason(
                code="prefix_truncated",
                message="Truncated the sanitized QE prefix to the supported maximum length.",
            )
        )

    if requested_outdir is None or not str(requested_outdir).strip():
        requested_outdir_text = DEFAULT_OUTDIR
        reasons.append(
            FeatureReason(
                code="auto_default_applied",
                message="Applied the default QE scratch outdir.",
            )
        )
    else:
        requested_outdir_text = str(requested_outdir).strip()

    return (
        ControlSettings(
            requested_prefix=requested_prefix_text,
            prefix=str(prefix_info["rendered"]),
            requested_outdir=requested_outdir_text,
            outdir=normalize_qe_outdir(requested_outdir_text, default=DEFAULT_OUTDIR),
        ),
        tuple(reasons),
    )


def build_calculation_context_from_request(
    atoms: "Atoms",
    request: dict[str, Any],
    *,
    resolved_entries: dict[str, dict[str, Any]] | None = None,
    atomic_positions_coordinate_mode: str = "crystal",
) -> CalculationContext:
    """Build a fully typed generation context from a flat request dictionary."""
    classification = classify_structure(atoms)
    cutoff_decision = select_cutoffs(resolved_entries or {})
    species_order = get_atomic_species_order(atoms)
    known_species = set(species_order)

    numerical = NumericalSettings(
        ecutwfc=float(request["ecutwfc"]),
        ecutrho=float(request["ecutrho"]),
        conv_thr=float(request.get("conv_thr", 1.0e-6)),
        mixing_beta=float(request.get("mixing_beta", 0.7)),
    )

    requested_calc = str(request.get("calculation", "scf"))
    rendered_calc = requested_calc
    workflow_reasons: list[FeatureReason] = []
    if requested_calc in {"dos", "bands"}:
        rendered_calc = "scf"
        workflow_reasons.append(
            FeatureReason(
                code="auto_default_applied",
                message="Mapped the workflow request onto the base pw.x calculation used for input generation.",
            )
        )

    control, control_reasons = build_control_settings(
        atoms,
        requested_prefix=request.get("prefix"),
        requested_outdir=request.get("outdir"),
    )

    requested_magnetization_payload = _normalize_requested_starting_magnetization(
        request.get("starting_magnetization") or {}
    )
    requested_magnetization = {
        species: float(payload["value"])
        for species, payload in requested_magnetization_payload.items()
    }
    rendered_magnetization: dict[str, float] = {}
    spin_reasons: list[FeatureReason] = []
    stale_magnetization_species = sorted(set(requested_magnetization_payload) - known_species)
    if stale_magnetization_species:
        spin_reasons.append(
            FeatureReason(
                code="stale_species_settings_pruned",
                message=(
                    "Pruned species-dependent magnetic settings for species that are no longer present: "
                    f"{', '.join(stale_magnetization_species)}."
                ),
            )
        )
    for species in species_order:
        if species in requested_magnetization:
            rendered_magnetization[species] = requested_magnetization[species]

    requested_nspin = 2 if int(request.get("nspin", 1)) == 2 else 1
    requested_soc_enabled = bool(request.get("noncolin", False) or request.get("lspinorb", False))
    soc_report = inspect_soc_compatibility(resolved_entries or {}) if requested_soc_enabled and resolved_entries else None
    soc_reasons: list[FeatureReason] = []
    rendered_noncolin = False
    rendered_lspinorb = False
    rendered_nspin = requested_nspin
    if requested_soc_enabled:
        if requested_nspin == 2 or bool(rendered_magnetization):
            soc_reasons.append(
                FeatureReason(
                    code="soc_spin_conflict",
                    message="Spin-orbit coupling cannot be rendered together with collinear spin polarization or starting magnetization in this phase.",
                )
            )
        if not resolved_entries:
            soc_reasons.append(
                FeatureReason(
                    code="soc_incompatible_pseudos",
                    message="Spin-orbit coupling requires resolved pseudopotential metadata with local UPF paths.",
                )
            )
        elif soc_report is not None and not soc_report.all_compatible:
            soc_reasons.append(
                FeatureReason(
                    code="soc_incompatible_pseudos",
                    message=soc_report.message,
                )
            )

        if not soc_reasons:
            rendered_noncolin = True
            rendered_lspinorb = True
            rendered_nspin = 1
            rendered_magnetization = {}

    if rendered_nspin != 2:
        rendered_magnetization = {}

    requested_hubbard_payload = _normalize_requested_hubbard_u(
        request.get("hubbard_u") or {}
    )
    dftu_reasons: list[FeatureReason] = []
    rendered_hubbard_parameters: dict[str, HubbardUSpeciesParameter] = {}
    stale_hubbard_species = sorted(set(requested_hubbard_payload) - known_species)
    if stale_hubbard_species:
        dftu_reasons.append(
            FeatureReason(
                code="stale_species_settings_pruned",
                message=(
                    "Pruned species-dependent Hubbard U settings for species that are no longer present: "
                    f"{', '.join(stale_hubbard_species)}."
                ),
            )
        )
    ignored_nonpositive_hubbard_species: list[str] = []
    for species in species_order:
        payload = requested_hubbard_payload.get(species)
        if payload is None:
            continue
        u_ev = float(payload["u_ev"])
        if u_ev > 0.0:
            rendered_hubbard_parameters[species] = HubbardUSpeciesParameter(
                species=species,
                u_ev=u_ev,
                source=str(payload["source"]),
            )
        else:
            ignored_nonpositive_hubbard_species.append(species)
    requested_dftu_enabled = bool(request.get("dftu_enabled", False))
    if ignored_nonpositive_hubbard_species and requested_dftu_enabled:
        dftu_reasons.append(
            FeatureReason(
                code="dftu_missing_positive_u",
                message=(
                    "Ignored non-positive Hubbard U values for: "
                    f"{', '.join(ignored_nonpositive_hubbard_species)}."
                ),
            )
        )
    rendered_hubbard_u = HubbardUSettings(
        enabled=requested_dftu_enabled and bool(rendered_hubbard_parameters),
        species_parameters=rendered_hubbard_parameters if requested_dftu_enabled else {},
    )
    if requested_dftu_enabled and not rendered_hubbard_parameters:
        dftu_reasons.append(
            FeatureReason(
                code="dftu_missing_positive_u",
                message="DFT+U was requested, but no valid positive Hubbard U values remain after validation.",
            )
        )

    requested_vdw_method = request.get("vdw_method", VdwMethod.NONE.value)
    vdw_reasons: list[FeatureReason] = []
    vdw_conflict = _has_conflicting_vdw_state(request)
    if vdw_conflict:
        rendered_vdw = VdwSettings(method=VdwMethod.NONE)
        vdw_reasons.append(
            FeatureReason(
                code="vdw_conflicting_state",
                message="Stale or conflicting vdW state was detected and prevented rendering a vdW correction.",
            )
        )
    else:
        try:
            rendered_vdw = VdwSettings(
                method=(
                    requested_vdw_method
                    if isinstance(requested_vdw_method, VdwMethod)
                    else VdwMethod(str(requested_vdw_method))
                )
            )
        except ValueError:
            rendered_vdw = VdwSettings(method=VdwMethod.NONE)
            vdw_reasons.append(
                FeatureReason(
                    code="vdw_conflicting_state",
                    message="The saved vdW state is not supported by the current curated vdW selector.",
                )
            )

    physical = PhysicalOptions(
        occupations=str(request["occupations"]),
        smearing=str(request["smearing"]),
        degauss=float(request["degauss"]),
        k_points_grid=[int(value) for value in request["k_points_grid"]],
        nspin=rendered_nspin,
        starting_magnetization=rendered_magnetization,
        noncolin=rendered_noncolin,
        lspinorb=rendered_lspinorb,
    )

    soc_requested_state = {
        "enabled": requested_soc_enabled,
        "noncolin": bool(request.get("noncolin", False)),
        "lspinorb": bool(request.get("lspinorb", False)),
    }
    soc_rendered_state = {
        "enabled": bool(rendered_noncolin or rendered_lspinorb),
        "noncolin": rendered_noncolin,
        "lspinorb": rendered_lspinorb,
        "compatibility": {
            "checked": bool(requested_soc_enabled),
            "all_compatible": bool(soc_report.all_compatible) if soc_report is not None else False,
            "records": [
                {
                    "element": record.element,
                    "filename": record.filename,
                    "compatible": record.compatible,
                    "reason": record.reason,
                }
                for record in soc_report.records
            ]
            if soc_report is not None
            else [],
        },
    }

    feature_states = {
        "control": FeatureState(
            requested={
                "prefix": control.requested_prefix,
                "outdir": control.requested_outdir,
            },
            rendered={
                "prefix": control.prefix,
                "outdir": control.outdir,
            },
            status="adjusted" if control_reasons else "rendered",
            reasons=control_reasons,
        ),
        "workflow": FeatureState(
            requested={"workflow": requested_calc},
            rendered={"workflow": rendered_calc},
            status="adjusted" if workflow_reasons else "rendered",
            reasons=tuple(workflow_reasons),
        ),
        "spin": FeatureState(
            requested={
                "nspin": requested_nspin,
                "starting_magnetization": {
                    species: dict(payload)
                    for species, payload in requested_magnetization_payload.items()
                },
            },
            rendered={
                "nspin": rendered_nspin,
                "starting_magnetization": dict(rendered_magnetization),
            },
            status="adjusted" if spin_reasons else "rendered",
            reasons=tuple(spin_reasons),
        ),
        "soc": FeatureState(
            requested=soc_requested_state,
            rendered=soc_rendered_state,
            status="blocked" if soc_reasons else "rendered",
            reasons=tuple(soc_reasons),
        ),
        "dftu": FeatureState(
            requested={
                "enabled": requested_dftu_enabled,
                "species_parameters": {
                    species: dict(payload)
                    for species, payload in requested_hubbard_payload.items()
                },
            },
            rendered=rendered_hubbard_u.to_metadata(species_order=species_order),
            status=(
                "skipped"
                if requested_dftu_enabled and not rendered_hubbard_u.enabled
                else "adjusted" if dftu_reasons else "rendered"
            ),
            reasons=tuple(dftu_reasons),
        ),
        "vdw": FeatureState(
            requested={
                "method": str(requested_vdw_method),
                "enabled": str(requested_vdw_method) != VdwMethod.NONE.value,
            },
            rendered={
                "method": rendered_vdw.normalized_method.value,
                "enabled": rendered_vdw.enabled,
                "backend": rendered_vdw.backend,
                "rendered_system_fields": render_vdw_system_fields(rendered_vdw),
            },
            status="blocked" if vdw_reasons else "rendered",
            reasons=tuple(vdw_reasons),
        ),
    }

    return CalculationContext(
        calculation=rendered_calc,
        numerical=numerical,
        physical=physical,
        classification=classification,
        cutoff_decision=cutoff_decision,
        resolved_entries=resolved_entries,
        species_order=species_order,
        hubbard_u=rendered_hubbard_u,
        vdw=rendered_vdw,
        control=control,
        feature_states=feature_states,
        atomic_positions_coordinate_mode=atomic_positions_coordinate_mode,
    )


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
    The ``ATOMIC_SPECIES``, ``CELL_PARAMETERS``, and ``K_POINTS`` card sections
    are written by ASE based on the ``Atoms`` object and the ``kpts`` argument
    to ``Espresso()``. ``ATOMIC_POSITIONS`` is normalized afterwards so the
    emitted coordinate system follows ``ctx.atomic_positions_coordinate_mode``.
    """
    calc_type = ctx.calculation
    num = ctx.numerical
    phys = ctx.physical

    # &CONTROL
    control: dict[str, Any] = {
        "calculation": calc_type,
        "prefix": ctx.control.prefix,
        "outdir": ctx.control.outdir,
    }

    # &SYSTEM
    system: dict[str, Any] = {
        "ecutwfc": num.ecutwfc,
        "ecutrho": num.ecutrho,
        "occupations": phys.occupations,
    }
    if phys.occupations == "smearing":
        system["smearing"] = phys.smearing
        system["degauss"] = phys.degauss

    if phys.noncolin:
        system["noncolin"] = True
    if phys.lspinorb:
        system["lspinorb"] = True
    if phys.nspin == 2 and not phys.noncolin:
        system["nspin"] = 2
        for index, species in enumerate(ctx.species_order or (), start=1):
            if species not in phys.starting_magnetization:
                continue
            system[f"starting_magnetization({index})"] = float(phys.starting_magnetization[species])
    if ctx.hubbard_u.enabled:
        system["lda_plus_u"] = True
        for index, species in enumerate(ctx.species_order or (), start=1):
            parameter = ctx.hubbard_u.species_parameters.get(species)
            if parameter is not None:
                system[f"Hubbard_U({index})"] = parameter.u_ev
    system.update(render_vdw_system_fields(ctx.vdw))

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


def render_vdw_system_fields(vdw: VdwSettings) -> dict[str, Any]:
    """Render vdW settings into QE ``&SYSTEM`` fields."""
    method = vdw.normalized_method
    return dict(_VDW_METHOD_RENDER_REGISTRY[method]["system"])


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

    _validate_generation_context(ctx)

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
    generated_text = _replace_atomic_positions_block(
        generated_text,
        atoms_copy,
        coordinate_mode=ctx.atomic_positions_coordinate_mode,
    )
    internal_path.write_text(generated_text, encoding="utf-8")
    preview_text = clean_qe_input_text(generated_text, calc_type=ctx.calculation)
    ui_path.write_text(preview_text, encoding="utf-8")

    return preview_text, internal_path, ui_path


def generate_workflow_inputs(
    atoms: "Atoms",
    base_ctx: CalculationContext,
    pseudopotentials: dict[str, str],
    pseudo_dir: str | Path,
    *,
    workflow: WorkflowType | str = WorkflowType.SCF,
    run_dir: Path,
) -> list[tuple[str, str, Path, Path]]:
    """
    Generate multiple step inputs for a full workflow setup.
    
    Returns a list of tuples: (step_name, preview_text, internal_path, ui_path)
    """
    import dataclasses
    workflow_val = WorkflowType(workflow)
    steps = []

    _validate_generation_context(base_ctx)

    if base_ctx.physical.nspin == 2:
        atoms = atoms.copy()
        magmoms = [
            base_ctx.physical.starting_magnetization.get(atom.symbol, 0.0)
            for atom in atoms
        ]
        atoms.set_initial_magnetic_moments(magmoms)

    # Helper to generate a single step dict based on the base context
    def _add_step(name: str, calc_type: str, spacing: float | None = None) -> None:
        # Re-derive dense grids if necessary, or just use base_ctx limits for SCF
        if spacing is not None:
            dense_grid = suggest_kpoints_from_cell(
                atoms,
                classification=base_ctx.classification,
                target_kpoint_spacing=spacing,
            )
            step_phys = dataclasses.replace(base_ctx.physical, k_points_grid=dense_grid)
        else:
            step_phys = base_ctx.physical
            
        step_ctx = dataclasses.replace(
            base_ctx,
            calculation=calc_type,
            physical=step_phys,
        )
        
        # Write to sub-directories if there are multiple steps, or just to prefix
        # We will write temporary inputs by renaming pw.in appropriately.
        text, internal, ui = generate_qe_input_text(
            atoms, step_ctx, pseudopotentials, pseudo_dir, run_dir=run_dir
        )
        
        # Move UI path to a step-specific name if it's a workflow
        if workflow_val in (WorkflowType.DOS, WorkflowType.BANDS):
            step_ui_file = ui.with_name(f"pw_{name}.in")
            ui.rename(step_ui_file)
            ui = step_ui_file
            
        steps.append((name, text, internal, ui))

    if workflow_val == WorkflowType.DOS:
        _add_step("scf", "scf")
        _add_step("nscf", "nscf", spacing=DEFAULT_KPOINT_SPACING_DOS)
    elif workflow_val == WorkflowType.BANDS:
        _add_step("scf", "scf")
        _add_step("bands", "bands", spacing=DEFAULT_KPOINT_SPACING_DOS)
    else:
        # Standard 1:1 mapped workflows
        _add_step(workflow_val.value, workflow_val.value)
        
    return steps


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


def _replace_atomic_positions_block(input_text: str, atoms: "Atoms", *, coordinate_mode: str) -> str:
    """Replace ASE's ATOMIC_POSITIONS block with the requested coordinate system."""
    replacement_block = _build_atomic_positions_block(atoms, coordinate_mode=coordinate_mode)
    block_pattern = re.compile(
        r"(?ms)^ATOMIC_POSITIONS\b[^\n]*\n.*?(?=\n\s*\n|\Z)",
    )
    updated_text, replacements = block_pattern.subn(replacement_block.rstrip("\n"), input_text, count=1)
    if replacements == 0:
        raise ValueError("Failed to locate the ATOMIC_POSITIONS block in the generated QE input.")
    return updated_text


def _build_atomic_positions_block(atoms: "Atoms", *, coordinate_mode: str) -> str:
    """Build a QE ATOMIC_POSITIONS card for the selected coordinate system."""
    if coordinate_mode == "crystal":
        coordinates = atoms.get_scaled_positions(wrap=False)
    elif coordinate_mode == "angstrom":
        coordinates = atoms.get_positions()
    else:
        raise ValueError(
            "atomic_positions_coordinate_mode must be one of "
            f"{sorted(ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS)}, got {coordinate_mode!r}"
        )

    lines = [f"ATOMIC_POSITIONS {coordinate_mode}"]
    for symbol, position in zip(atoms.get_chemical_symbols(), coordinates, strict=False):
        lines.append(
            f"{symbol}  "
            f"{_normalize_formatted_coordinate(position[0]):.10f} "
            f"{_normalize_formatted_coordinate(position[1]):.10f} "
            f"{_normalize_formatted_coordinate(position[2]):.10f}"
        )
    return "\n".join(lines) + "\n"


def _normalize_formatted_coordinate(value: float) -> float:
    """Clamp signed zero and tiny formatting noise for stable text output."""
    normalized = float(value)
    if abs(normalized) < 5.0e-11:
        return 0.0
    return normalized


def _validate_generation_context(ctx: CalculationContext) -> None:
    """Validate a generation context, including SOC/pseudopotential checks."""
    ctx.validate()
    if ctx.species_order is not None:
        known_species = set(ctx.species_order)
        stale_magnetization_species = sorted(set(ctx.physical.starting_magnetization) - known_species)
        if stale_magnetization_species:
            raise ValueError(
                "Stale species-dependent magnetic settings remain for unknown species: "
                f"{', '.join(stale_magnetization_species)}."
            )
        stale_hubbard_species = sorted(set(ctx.hubbard_u.species_parameters) - known_species)
        if stale_hubbard_species:
            raise ValueError(
                "Stale species-dependent Hubbard U settings remain for unknown species: "
                f"{', '.join(stale_hubbard_species)}."
            )
    if ctx.feature_states:
        soc_state = ctx.feature_states.get("soc")
        if soc_state is not None and soc_state.status == "blocked":
            raise ValueError(
                "Spin-orbit coupling settings prevent QE input generation: "
                + "; ".join(reason.message for reason in soc_state.reasons)
            )
        vdw_state = ctx.feature_states.get("vdw")
        if vdw_state is not None and vdw_state.status == "blocked":
            raise ValueError("Stale or conflicting vdW settings prevent QE input generation.")
    if not (ctx.physical.noncolin or ctx.physical.lspinorb):
        return
    if not ctx.resolved_entries:
        raise ValueError(
            "Spin-orbit coupling requires resolved pseudopotential metadata with local UPF paths."
        )
    ensure_soc_compatible_pseudopotentials(ctx.resolved_entries)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_default_context(
    atoms: "Atoms",
    *,
    calculation: str = "scf",
    resolved_entries: dict[str, dict[str, Any]] | None = None,
    preset: CutoffPreset = CutoffPreset.STANDARD,
    target_kpoint_spacing: float | None = None,
    apply_policy: bool = True,
) -> CalculationContext:
    """
    Build a :class:`CalculationContext` with science-driven defaults.

    This is the recommended entry-point for programmatic use (tests, CLI tools,
    batch scripts).  The UI page uses it as a starting point and then applies
    user widget overrides on top.

    Cutoff selection
    ----------------
    When ``resolved_entries`` are provided this function calls
    :func:`select_cutoffs` to derive ``ecutwfc`` / ``ecutrho`` directly from
    the SSSP manifest data (``cutoff_wfc`` / ``cutoff_rho`` fields).  The
    ``preset`` argument controls the quality tier:

    - ``CutoffPreset.QUICK``       — 0.90× SSSP recommended, conv_thr = 1e-6
    - ``CutoffPreset.STANDARD``    — 1.00× SSSP recommended, conv_thr = 1e-6
    - ``CutoffPreset.PUBLICATION`` — 1.25× SSSP recommended, conv_thr = 1e-8

    When ``resolved_entries`` is ``None`` or empty, conservative heuristic
    defaults (scaled by the preset multiplier) are used and the decision is
    labeled ``source = "heuristic_fallback"`` in the returned context.

    The full :class:`~qe_studio.services.cutoff_presets.CutoffDecision` is
    stored in :attr:`CalculationContext.cutoff_decision` so callers can surface
    the decision in provenance records and UI displays.

    Parameters
    ----------
    atoms:
        The structure to compute defaults for.
    calculation:
        QE calculation type.
    resolved_entries:
        Optional SSSP manifest entries for cutoff suggestion.  Should be the
        ``"resolved_entries"`` value returned by
        :func:`~qe_studio.services.pseudopotentials.resolve_sssp_pseudopotentials`.
    preset:
        Quality tier for cutoff selection.  Defaults to
        :attr:`CutoffPreset.STANDARD`.
    target_kpoint_spacing:
        Reciprocal-space k-point spacing (Å⁻¹) for the grid suggestion. If None,
        a sensible default is chosen based on the calculation type (0.15 for scf, 0.05 for nscf).
    apply_policy:
        If True, calls :func:`apply_default_policy` to apply classification-
        driven adjustments on top of the raw defaults.

    Returns
    -------
    CalculationContext:
        Fully populated context with ``cutoff_decision`` attached.
    """
    import dataclasses

    classification = classify_structure(atoms)
    species_order = get_atomic_species_order(atoms)

    # --- Cutoff selection ---
    cutoff_decision = select_cutoffs(resolved_entries or {}, preset=preset)
    ecutwfc = cutoff_decision.ecutwfc_ry
    ecutrho = cutoff_decision.ecutrho_ry

    # The publication preset implies a tighter SCF convergence threshold.
    suggested_conv_thr = PRESET_CONV_THR[preset]

    if target_kpoint_spacing is None:
        if calculation in ("nscf", "dos"):
            target_kpoint_spacing = DEFAULT_KPOINT_SPACING_DOS
        else:
            target_kpoint_spacing = DEFAULT_KPOINT_SPACING_SCF

    kgrid = suggest_kpoints_from_cell(
        atoms,
        classification=classification,
        target_kpoint_spacing=target_kpoint_spacing,
    )

    numerical = NumericalSettings(
        ecutwfc=ecutwfc,
        ecutrho=ecutrho,
        conv_thr=suggested_conv_thr,
    )
    physical = PhysicalOptions(
        k_points_grid=kgrid,
    )

    if apply_policy:
        numerical, physical = apply_default_policy(classification, numerical, physical)

    # Use the first step's specific calculation execution mapping if applicable
    calc_type = calculation
    if calculation in ("dos", "bands"):
        calc_type = "scf"

    control, control_reasons = build_control_settings(atoms)
    workflow_status = "adjusted" if calc_type != calculation else "rendered"
    workflow_reasons: tuple[FeatureReason, ...] = ()
    if workflow_status == "adjusted":
        workflow_reasons = (
            FeatureReason(
                code="auto_default_applied",
                message="Mapped the workflow request onto the base pw.x calculation used for default generation.",
            ),
        )

    return CalculationContext(
        calculation=calc_type,
        numerical=numerical,
        physical=physical,
        classification=classification,
        cutoff_decision=cutoff_decision,
        resolved_entries=resolved_entries,
        species_order=species_order,
        control=control,
        feature_states={
            "control": FeatureState(
                requested={
                    "prefix": control.requested_prefix,
                    "outdir": control.requested_outdir,
                },
                rendered={
                    "prefix": control.prefix,
                    "outdir": control.outdir,
                },
                status="adjusted" if control_reasons else "rendered",
                reasons=control_reasons,
            ),
            "workflow": FeatureState(
                requested={"workflow": calculation},
                rendered={"workflow": calc_type},
                status=workflow_status,
                reasons=workflow_reasons,
            ),
            "spin": FeatureState(
                requested={
                    "nspin": physical.nspin,
                    "starting_magnetization": dict(physical.starting_magnetization),
                },
                rendered={
                    "nspin": physical.nspin,
                    "starting_magnetization": dict(physical.starting_magnetization),
                },
            ),
            "soc": FeatureState(
                requested={
                    "noncolin": bool(physical.noncolin),
                    "lspinorb": bool(physical.lspinorb),
                    "enabled": bool(physical.noncolin or physical.lspinorb),
                },
                rendered={
                    "noncolin": bool(physical.noncolin),
                    "lspinorb": bool(physical.lspinorb),
                    "enabled": bool(physical.noncolin or physical.lspinorb),
                },
            ),
            "dftu": FeatureState(
                requested={"enabled": False, "species_parameters": {}},
                rendered={"enabled": False, "species_parameters": {}},
            ),
            "vdw": FeatureState(
                requested={"method": VdwMethod.NONE.value, "enabled": False},
                rendered={"method": VdwMethod.NONE.value, "enabled": False},
            ),
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_requested_starting_magnetization(raw_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize saved/requested starting magnetization payloads."""
    normalized: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_state, dict):
        return normalized

    for fallback_species, payload in raw_state.items():
        if isinstance(payload, dict):
            species = str(payload.get("species") or fallback_species)
            raw_value = payload.get("value", payload.get("magnetization", payload.get("u_ev", 0.0)))
            source = str(payload.get("source") or "user_defined")
        else:
            species = str(fallback_species)
            raw_value = payload
            source = "user_defined"
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        normalized[species] = {
            "species": species,
            "value": value,
            "source": source,
        }
    return normalized


def _normalize_requested_hubbard_u(raw_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize saved/requested Hubbard U payloads without dropping zero values."""
    normalized: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_state, dict):
        return normalized

    for fallback_species, payload in raw_state.items():
        if not isinstance(payload, dict):
            continue
        species = str(payload.get("species") or fallback_species)
        try:
            u_ev = float(payload.get("u_ev", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(u_ev):
            continue
        normalized[species] = {
            "species": species,
            "u_ev": u_ev,
            "source": str(payload.get("source") or "user_defined"),
        }
    return normalized


def _has_conflicting_vdw_state(request: dict[str, Any]) -> bool:
    """Return whether one request contains a stale/conflicting vdW representation."""
    legacy_state = request.get("vdw_state")
    if isinstance(legacy_state, dict):
        return "vdw_corr" in legacy_state and "input_dft" in legacy_state
    return bool("vdw_corr" in request and "input_dft" in request)


def _round_to_nearest(value: float, step: float) -> float:
    """Round ``value`` up to the nearest multiple of ``step``."""
    return math.ceil(value / step) * step
