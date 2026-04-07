from __future__ import annotations

from dataclasses import dataclass
import io
from typing import Iterable, Sequence

from ase import Atoms
from ase.io import write
import numpy as np
import py3Dmol


LABEL_MODE_OFF = "off"
LABEL_MODE_ELEMENT = "element"
LABEL_MODE_ELEMENT_INDEX = "element_index"
LABEL_MODE_OPTIONS = (
    LABEL_MODE_OFF,
    LABEL_MODE_ELEMENT,
    LABEL_MODE_ELEMENT_INDEX,
)

STYLE_MODE_BALL_AND_STICK = "ball_and_stick"
STYLE_MODE_STICK = "stick"
STYLE_MODE_SPACEFILL = "spacefill"
STYLE_MODE_OPTIONS = (
    STYLE_MODE_BALL_AND_STICK,
    STYLE_MODE_STICK,
    STYLE_MODE_SPACEFILL,
)

VIEWER_LABEL_MODE_WIDGET_KEY = "structure_viewer_label_mode"
VIEWER_STYLE_MODE_WIDGET_KEY = "structure_viewer_style_mode"
VIEWER_SHOW_BONDS_WIDGET_KEY = "structure_viewer_show_bonds"
VIEWER_SHOW_UNIT_CELL_WIDGET_KEY = "structure_viewer_show_unit_cell"
VIEWER_SHOW_AXES_WIDGET_KEY = "structure_viewer_show_axes"
VIEWER_VISIBLE_SPECIES_WIDGET_KEY = "structure_viewer_visible_species"
VIEWER_SUPERCELL_MODE_WIDGET_KEY = "structure_viewer_supercell_mode"
VIEWER_SUPERCELL_A_WIDGET_KEY = "structure_viewer_supercell_a"
VIEWER_SUPERCELL_B_WIDGET_KEY = "structure_viewer_supercell_b"
VIEWER_SUPERCELL_C_WIDGET_KEY = "structure_viewer_supercell_c"

SUPERCELL_MODE_PRESET_111 = "preset_111"
SUPERCELL_MODE_PRESET_211 = "preset_211"
SUPERCELL_MODE_PRESET_222 = "preset_222"
SUPERCELL_MODE_CUSTOM = "custom"
SUPERCELL_MODE_OPTIONS = (
    SUPERCELL_MODE_PRESET_111,
    SUPERCELL_MODE_PRESET_211,
    SUPERCELL_MODE_PRESET_222,
    SUPERCELL_MODE_CUSTOM,
)
SUPERCELL_PRESET_REPEATS = {
    SUPERCELL_MODE_PRESET_111: (1, 1, 1),
    SUPERCELL_MODE_PRESET_211: (2, 1, 1),
    SUPERCELL_MODE_PRESET_222: (2, 2, 2),
}
MAX_SUPERCELL_REPEAT = 3


@dataclass(frozen=True)
class ViewerSettings:
    label_mode: str = LABEL_MODE_OFF
    style_mode: str = STYLE_MODE_BALL_AND_STICK
    show_bonds: bool = True
    show_unit_cell: bool = True
    show_axes: bool = False
    visible_species: tuple[str, ...] = ()
    supercell_repeat: tuple[int, int, int] = (1, 1, 1)


def get_sorted_species(atoms: Atoms | None) -> list[str]:
    """Return sorted unique species for viewer filter controls."""
    if atoms is None:
        return []
    return sorted(set(atoms.get_chemical_symbols()))


def normalize_visible_species(
    atoms: Atoms | None,
    selected_species: Sequence[str] | None,
) -> tuple[str, ...]:
    """Return a valid visible-species selection for the provided atoms."""
    available_species = get_sorted_species(atoms)
    if not available_species:
        return ()

    if selected_species is None:
        return tuple(available_species)

    available_set = set(available_species)
    filtered_species = [species for species in selected_species if species in available_set]
    if selected_species and not filtered_species:
        return tuple(available_species)
    return tuple(filtered_species)


def normalize_supercell_repeat(values: Iterable[int | float | str]) -> tuple[int, int, int]:
    """Clamp supercell repeat values into a small safe viewer-only range."""
    normalized_values: list[int] = []
    for value in values:
        try:
            normalized_value = int(value)
        except (TypeError, ValueError):
            normalized_value = 1
        normalized_values.append(max(1, min(MAX_SUPERCELL_REPEAT, normalized_value)))
    while len(normalized_values) < 3:
        normalized_values.append(1)
    return tuple(normalized_values[:3])  # type: ignore[return-value]


def get_supercell_repeat_for_mode(
    preset_mode: str,
    *,
    custom_repeat: Sequence[int | float | str] | None = None,
) -> tuple[int, int, int]:
    """Resolve one viewer-only supercell repeat tuple from preset or custom input."""
    if preset_mode == SUPERCELL_MODE_CUSTOM:
        return normalize_supercell_repeat(custom_repeat or (1, 1, 1))
    return SUPERCELL_PRESET_REPEATS.get(preset_mode, (1, 1, 1))


def format_supercell_repeat(repeat: Sequence[int]) -> str:
    """Format a repeat tuple for compact UI display."""
    normalized = normalize_supercell_repeat(repeat)
    return f"{normalized[0]}x{normalized[1]}x{normalized[2]}"


def build_display_atoms(
    atoms: Atoms | None,
    *,
    visible_species: Sequence[str] | None,
    supercell_repeat: Sequence[int] = (1, 1, 1),
) -> Atoms | None:
    """Return a viewer-only filtered and repeated copy of the active structure."""
    if atoms is None:
        return None

    normalized_species = set(normalize_visible_species(atoms, visible_species))
    kept_indices = [
        index
        for index, symbol in enumerate(atoms.get_chemical_symbols())
        if symbol in normalized_species
    ]
    if not kept_indices:
        return None

    display_atoms = atoms[kept_indices]
    repeat = normalize_supercell_repeat(supercell_repeat)
    if repeat != (1, 1, 1):
        display_atoms = display_atoms.repeat(repeat)
        display_atoms.pbc = atoms.pbc
    return display_atoms


def get_effective_show_bonds(style_mode: str, show_bonds: bool) -> bool:
    """Return whether bond sticks should be rendered for the selected style."""
    if style_mode == STYLE_MODE_STICK:
        return True
    if style_mode == STYLE_MODE_SPACEFILL:
        return False
    return bool(show_bonds)


def style_supports_bond_toggle(style_mode: str) -> bool:
    """Return whether the bonds toggle is meaningful for the selected style."""
    return style_mode == STYLE_MODE_BALL_AND_STICK


def _atoms_to_cif_text(atoms: Atoms) -> str:
    cif_buffer = io.BytesIO()
    write(cif_buffer, atoms, format="cif")
    return cif_buffer.getvalue().decode("utf-8")


def _get_style_payload(style_mode: str, show_bonds: bool) -> dict[str, dict[str, float]]:
    effective_show_bonds = get_effective_show_bonds(style_mode, show_bonds)
    if style_mode == STYLE_MODE_STICK:
        return {"stick": {"radius": 0.18}}
    if style_mode == STYLE_MODE_SPACEFILL:
        return {"sphere": {"scale": 0.7}}
    if effective_show_bonds:
        return {"sphere": {"scale": 0.32}, "stick": {"radius": 0.16}}
    return {"sphere": {"scale": 0.38}}


def _get_label_text(symbol: str, atom_index: int, label_mode: str) -> str:
    if label_mode == LABEL_MODE_ELEMENT:
        return symbol
    if label_mode == LABEL_MODE_ELEMENT_INDEX:
        return f"{symbol}{atom_index + 1}"
    return ""


def _label_font_color() -> str:
    return "#111827"


def _add_atom_labels(viewer, atoms: Atoms, label_mode: str) -> None:
    if label_mode not in LABEL_MODE_OPTIONS or label_mode == LABEL_MODE_OFF:
        return

    for atom_index, atom in enumerate(atoms):
        label_text = _get_label_text(atom.symbol, atom_index, label_mode)
        viewer.addLabel(
            label_text,
            {
                "position": {
                    "x": float(atom.position[0]),
                    "y": float(atom.position[1]),
                    "z": float(atom.position[2]),
                },
                "fontSize": 12,
                "fontColor": _label_font_color(),
                "backgroundColor": "#f8fafc",
                "backgroundOpacity": 0.82,
                "borderThickness": 0.5,
                "borderColor": "#94a3b8",
                "inFront": True,
            },
        )


def _add_axes(viewer, atoms: Atoms) -> None:
    if len(atoms) == 0:
        return

    positions = np.asarray(atoms.positions, dtype=float)
    position_extent = float(np.ptp(positions, axis=0).max()) if positions.size else 0.0
    cell_lengths = [float(value) for value in atoms.cell.lengths()] if atoms.cell.rank > 0 else [0.0]
    axis_length = max([1.0, position_extent * 0.35, max(cell_lengths) * 0.25 if cell_lengths else 0.0])

    axis_specs = (
        ("x", "#ef4444", (axis_length, 0.0, 0.0)),
        ("y", "#22c55e", (0.0, axis_length, 0.0)),
        ("z", "#3b82f6", (0.0, 0.0, axis_length)),
    )
    for label, color, end_point in axis_specs:
        viewer.addLine(
            {
                "start": {"x": 0.0, "y": 0.0, "z": 0.0},
                "end": {"x": end_point[0], "y": end_point[1], "z": end_point[2]},
                "color": color,
                "dashed": False,
            }
        )
        viewer.addLabel(
            label.upper(),
            {
                "position": {"x": end_point[0], "y": end_point[1], "z": end_point[2]},
                "fontSize": 12,
                "fontColor": color,
                "showBackground": False,
                "inFront": True,
            },
        )


def build_structure_viewer_html(
    atoms: Atoms,
    *,
    settings: ViewerSettings | None = None,
    width: int | str = "100%",
    height: int = 520,
) -> str:
    """Build static `py3Dmol` HTML for the active structure viewer."""
    viewer_settings = settings or ViewerSettings()
    viewer = py3Dmol.view(width=width, height=height)
    viewer.addModel(_atoms_to_cif_text(atoms), "cif")
    viewer.setStyle(_get_style_payload(viewer_settings.style_mode, viewer_settings.show_bonds))
    viewer.setBackgroundColor("white")
    if viewer_settings.show_unit_cell and atoms.cell.rank > 0 and atoms.cell.volume > 0:
        viewer.addUnitCell()
    if viewer_settings.show_axes:
        _add_axes(viewer, atoms)
    _add_atom_labels(viewer, atoms, viewer_settings.label_mode)
    viewer.zoomTo()
    viewer.render()
    return viewer._make_html()
