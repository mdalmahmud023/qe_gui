from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


CONVERGENCE_SCHEMA_VERSION = 1
CUTOFF_STUDY_MANIFEST_FILENAME = "cutoff_study_manifest.json"
CUTOFF_STUDY_SUMMARY_FILENAME = "cutoff_study_summary.json"
KPOINT_STUDY_MANIFEST_FILENAME = "kpoint_study_manifest.json"
KPOINT_STUDY_SUMMARY_FILENAME = "kpoint_study_summary.json"
EOS_STUDY_MANIFEST_FILENAME = "eos_study_manifest.json"
EOS_STUDY_SUMMARY_FILENAME = "eos_study_summary.json"
CUTOFF_POINT_OUTPUT_FILENAME = "pw.out"
KPOINT_POINT_OUTPUT_FILENAME = "pw.out"
EOS_POINT_OUTPUT_FILENAME = "pw.out"
EOS_FIT_ENERGY_BASIS_CELL_TOTAL_EV = "cell_total_ev"
EOS_FIT_VOLUME_BASIS_CELL_TOTAL_ANG3 = "cell_total_ang3"
RATIO_RULE_CUSTOM = "custom"
CUTOFF_RATIO_RULE_OPTIONS = ("4x", "6x", "8x", "12x", RATIO_RULE_CUSTOM)
_FLOAT_TOLERANCE = 1.0e-9


class ConvergenceWorkflowType(str, Enum):
    """Supported scientific convergence workflows."""

    CUTOFF = "cutoff"
    KPOINT = "kpoint"
    EOS = "eos"


class EcutrhoMode(str, Enum):
    """How point-specific ``ecutrho`` values are derived for a cutoff sweep."""

    METADATA_DUAL = "metadata_dual"
    RATIO_RULE = "ratio_rule"
    MANUAL_ABSOLUTE = "manual_absolute"
    MANUAL_RATIO = "manual_ratio"


@dataclass(frozen=True)
class CutoffSweepSpec:
    """Scientific specification for one plane-wave cutoff convergence sweep."""

    start_ecutwfc_ry: float
    stop_ecutwfc_ry: float
    step_ry: float
    tolerance_mev_per_atom: float
    ecutrho_mode: str
    ratio_rule: str = "8x"
    custom_ratio: float | None = None
    manual_ecutrho_ry: float | None = None
    manual_ratio: float | None = None

    @property
    def normalized_ecutrho_mode(self) -> EcutrhoMode:
        return self.ecutrho_mode if isinstance(self.ecutrho_mode, EcutrhoMode) else EcutrhoMode(str(self.ecutrho_mode))

    def validate(self) -> None:
        if self.start_ecutwfc_ry <= 0:
            raise ValueError("Cutoff sweep start ecutwfc must be positive.")
        if self.stop_ecutwfc_ry <= 0:
            raise ValueError("Cutoff sweep stop ecutwfc must be positive.")
        if self.stop_ecutwfc_ry + _FLOAT_TOLERANCE < self.start_ecutwfc_ry:
            raise ValueError("Cutoff sweep stop ecutwfc must be greater than or equal to the start value.")
        if self.step_ry <= 0:
            raise ValueError("Cutoff sweep step size must be positive.")
        if self.tolerance_mev_per_atom <= 0:
            raise ValueError("Cutoff convergence tolerance must be positive.")

        mode = self.normalized_ecutrho_mode
        if self.ratio_rule not in CUTOFF_RATIO_RULE_OPTIONS:
            raise ValueError(
                "Cutoff ratio rule must be one of "
                f"{', '.join(CUTOFF_RATIO_RULE_OPTIONS)}, got {self.ratio_rule!r}."
            )
        if mode == EcutrhoMode.RATIO_RULE and self.selected_ratio_rule_value() <= 0:
            raise ValueError("The selected ecutrho ratio rule must resolve to a positive ratio.")
        if mode == EcutrhoMode.MANUAL_RATIO:
            if self.manual_ratio is None or self.manual_ratio <= 0:
                raise ValueError("Manual ecutrho ratio mode requires a positive manual ratio.")
        if mode == EcutrhoMode.MANUAL_ABSOLUTE:
            if self.manual_ecutrho_ry is None or self.manual_ecutrho_ry <= 0:
                raise ValueError("Manual absolute ecutrho mode requires a positive ecutrho value.")

        self.ecutwfc_values()

    def ecutwfc_values(self) -> list[float]:
        """Return the sweep grid and require that the last point lands on ``stop`` exactly."""
        values: list[float] = []
        step_index = 0
        while True:
            candidate = self.start_ecutwfc_ry + step_index * self.step_ry
            if candidate > self.stop_ecutwfc_ry + _FLOAT_TOLERANCE:
                break
            if abs(candidate - self.stop_ecutwfc_ry) <= _FLOAT_TOLERANCE:
                values.append(float(self.stop_ecutwfc_ry))
                break
            values.append(float(round(candidate, 10)))
            step_index += 1
            if step_index > 10000:
                raise ValueError("Cutoff sweep produced too many points; check the sweep bounds.")

        if not values or abs(values[-1] - self.stop_ecutwfc_ry) > _FLOAT_TOLERANCE:
            raise ValueError("Cutoff sweep must land exactly on the stop ecutwfc value.")
        return values

    def selected_ratio_rule_value(self) -> float:
        """Return the numeric ratio implied by the ratio-rule controls."""
        if self.ratio_rule == RATIO_RULE_CUSTOM:
            if self.custom_ratio is None:
                raise ValueError("Custom cutoff ratio selected without a custom ratio value.")
            return float(self.custom_ratio)
        return float(str(self.ratio_rule).rstrip("xX"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_ecutwfc_ry": self.start_ecutwfc_ry,
            "stop_ecutwfc_ry": self.stop_ecutwfc_ry,
            "step_ry": self.step_ry,
            "tolerance_mev_per_atom": self.tolerance_mev_per_atom,
            "ecutrho_mode": self.normalized_ecutrho_mode.value,
            "ratio_rule": self.ratio_rule,
            "custom_ratio": self.custom_ratio,
            "manual_ecutrho_ry": self.manual_ecutrho_ry,
            "manual_ratio": self.manual_ratio,
            "ecutwfc_values_ry": self.ecutwfc_values(),
        }


@dataclass(frozen=True)
class CutoffStudyPoint:
    """One generated input point inside a cutoff convergence study."""

    index: int
    ecutwfc_ry: float
    ecutrho_ry: float
    run_dir: Path
    input_paths: dict[str, Path] = field(default_factory=dict)
    metadata_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ecutwfc_ry": self.ecutwfc_ry,
            "ecutrho_ry": self.ecutrho_ry,
            "run_dir": str(self.run_dir),
            "input_paths": {key: str(value) for key, value in self.input_paths.items()},
            "metadata_path": str(self.metadata_path) if self.metadata_path is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CutoffStudyPoint":
        return cls(
            index=int(payload["index"]),
            ecutwfc_ry=float(payload["ecutwfc_ry"]),
            ecutrho_ry=float(payload["ecutrho_ry"]),
            run_dir=Path(payload["run_dir"]),
            input_paths={key: Path(value) for key, value in dict(payload.get("input_paths", {})).items()},
            metadata_path=Path(payload["metadata_path"]) if payload.get("metadata_path") else None,
        )


@dataclass(frozen=True)
class PointResult:
    """Parsed scientific result for one cutoff-study point."""

    status: str
    total_energy_ry: float | None = None
    total_energy_ev: float | None = None
    energy_per_atom_ev: float | None = None
    delta_mev_per_atom: float | None = None
    warnings: tuple[str, ...] = ()
    output_path: Path | None = None
    job_done: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total_energy_ry": self.total_energy_ry,
            "total_energy_ev": self.total_energy_ev,
            "energy_per_atom_ev": self.energy_per_atom_ev,
            "delta_mev_per_atom": self.delta_mev_per_atom,
            "warnings": list(self.warnings),
            "output_path": str(self.output_path) if self.output_path is not None else None,
            "job_done": self.job_done,
        }


@dataclass(frozen=True)
class CutoffRecommendation:
    """Recommendation payload for one fully or partially parsed cutoff study."""

    status: str
    recommended_ecutwfc_ry: float | None = None
    recommended_ecutrho_ry: float | None = None
    message: str = ""
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "recommended_ecutwfc_ry": self.recommended_ecutwfc_ry,
            "recommended_ecutrho_ry": self.recommended_ecutrho_ry,
            "message": self.message,
            "warnings": list(self.warnings),
        }


class DimensionalityMode(str, Enum):
    """How the k-point grid should be varied based on dimensionality."""

    ISOTROPIC_3D = "3d_isotropic"
    IN_PLANE_2D = "2d_in_plane"
    WIRE_1D = "1d_wire"
    MOLECULE_0D = "0d_molecule"


@dataclass(frozen=True)
class KPointSweepSpec:
    """Scientific specification for one k-point convergence sweep."""

    mode: str
    start_value: int
    stop_value: int
    step: int
    tolerance_mev_per_atom: float
    baseline_k_grid: tuple[int, int, int] = (1, 1, 1)

    @property
    def normalized_mode(self) -> DimensionalityMode:
        return self.mode if isinstance(self.mode, DimensionalityMode) else DimensionalityMode(str(self.mode))

    def validate(self) -> None:
        if self.start_value <= 0:
            raise ValueError("K-point sweep start value must be positive.")
        if self.stop_value <= 0:
            raise ValueError("K-point sweep stop value must be positive.")
        if self.stop_value < self.start_value:
            raise ValueError("K-point sweep stop value must be greater than or equal to the start value.")
        if self.step <= 0:
            raise ValueError("K-point sweep step size must be positive.")
        if self.tolerance_mev_per_atom <= 0:
            raise ValueError("K-point convergence tolerance must be positive.")
            
        self.k_point_values()

    def k_point_values(self) -> list[int]:
        """Return the sweep grid of values to use."""
        values: list[int] = []
        step_index = 0
        while True:
            candidate = self.start_value + step_index * self.step
            if candidate > self.stop_value:
                break
            values.append(int(candidate))
            step_index += 1
            if step_index > 1000:
                raise ValueError("K-point sweep produced too many points; check the sweep bounds.")
                
        if not values or values[-1] != self.stop_value:
            raise ValueError("K-point sweep must land exactly on the stop value.")
        return values
        
    def generate_grid_for_value(self, value: int) -> tuple[int, int, int]:
        mode = self.normalized_mode
        if mode == DimensionalityMode.ISOTROPIC_3D:
            return (value, value, value)
        elif mode == DimensionalityMode.IN_PLANE_2D:
            return (value, value, 1)
        elif mode == DimensionalityMode.WIRE_1D:
            return (1, 1, value)
        else:
            return (1, 1, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.normalized_mode.value,
            "start_value": self.start_value,
            "stop_value": self.stop_value,
            "step": self.step,
            "tolerance_mev_per_atom": self.tolerance_mev_per_atom,
            "baseline_k_grid": list(self.baseline_k_grid),
            "k_point_values": self.k_point_values(),
        }


@dataclass(frozen=True)
class KPointStudyPoint:
    """One generated input point inside a k-point convergence study."""

    index: int
    k_points_grid: tuple[int, int, int]
    grid_label: str
    run_dir: Path
    input_paths: dict[str, Path] = field(default_factory=dict)
    metadata_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "k_points_grid": list(self.k_points_grid),
            "grid_label": self.grid_label,
            "run_dir": str(self.run_dir),
            "input_paths": {key: str(value) for key, value in self.input_paths.items()},
            "metadata_path": str(self.metadata_path) if self.metadata_path is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "KPointStudyPoint":
        k_grid = payload["k_points_grid"]
        return cls(
            index=int(payload["index"]),
            k_points_grid=(int(k_grid[0]), int(k_grid[1]), int(k_grid[2])),
            grid_label=str(payload["grid_label"]),
            run_dir=Path(payload["run_dir"]),
            input_paths={key: Path(value) for key, value in dict(payload.get("input_paths", {})).items()},
            metadata_path=Path(payload["metadata_path"]) if payload.get("metadata_path") else None,
        )


@dataclass(frozen=True)
class KPointRecommendation:
    """Recommendation payload for one fully or partially parsed k-point study."""

    status: str
    recommended_k_grid: tuple[int, int, int] | None = None
    grid_label: str = ""
    message: str = ""
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "recommended_k_grid": list(self.recommended_k_grid) if self.recommended_k_grid else None,
            "grid_label": self.grid_label,
            "message": self.message,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class EosSweepSpec:
    """Scientific specification for an Equation of State volume sweep."""

    mode: str  # "isotropic_scale"
    start_scale: float
    stop_scale: float
    num_points: int
    fit_model: str = "birch_murnaghan"

    def validate(self) -> None:
        if self.mode != "isotropic_scale":
            raise ValueError(f"Unsupported EOS mode: {self.mode}")
        if self.num_points < 3:
            raise ValueError("EOS sweep must have at least 3 points.")
        if self.start_scale <= 0 or self.stop_scale <= 0:
            raise ValueError("Scale factors must be strictly positive.")

    def scale_values(self) -> list[float]:
        """Return the precise list of scale factors to sample."""
        if self.num_points == 1:
            return [(self.start_scale + self.stop_scale) / 2.0]
        step = (self.stop_scale - self.start_scale) / (self.num_points - 1)
        return [self.start_scale + i * step for i in range(self.num_points)]


@dataclass(frozen=True)
class EosStudyPoint:
    """A single scaling point in an EOS study."""

    index: int
    scale_factor: float
    volume: float
    label: str
    run_dir: Path


@dataclass(frozen=True)
class EosFitResult:
    """The result of fitting an equation of state to the study data."""

    available: bool
    success: bool
    model: str
    E0: float | None = None
    V0: float | None = None
    B0: float | None = None
    B0_prime: float | None = None
    a0: float | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EosRecommendation:
    """The final recommendation from an EOS study."""

    status: str  # "complete", "failed", "unconverged"
    fit_result: EosFitResult
    recommended_volume: float | None = None
    recommended_a0: float | None = None
    reason: str | None = None
