from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

from ..electronic_properties import (
    INTERACTIVE_FIGURE_MODE,
    build_plot_layout,
    is_publication_export_mode,
)

_EOS_DISPLAY_MODE_CELL_TOTAL = "cell_total"
_EOS_DISPLAY_MODE_PER_ATOM = "per_atom"
_EOS_DISPLAY_MODE_RELATIVE_PER_ATOM = "relative_per_atom"
INTERACTIVE_STUDY_FIGURE_HEIGHT = 500
PUBLICATION_STUDY_FIGURE_HEIGHT = 560


def _study_plot_layout(
    *,
    figure_mode: str,
    title: str,
    xaxis_title: str,
    yaxis_title: str,
    categorical_xaxis: bool = False,
) -> dict[str, Any]:
    """Return the shared light layout with study-specific labels and legend."""
    publication_mode = is_publication_export_mode(figure_mode)
    layout = build_plot_layout(
        height=PUBLICATION_STUDY_FIGURE_HEIGHT if publication_mode else INTERACTIVE_STUDY_FIGURE_HEIGHT,
        figure_mode=figure_mode,
    )
    layout.update(
        {
            "title": title,
            "xaxis_title": xaxis_title,
            "yaxis_title": yaxis_title,
            "legend": {
                "orientation": "h",
                "yanchor": "bottom",
                "y": 1.02,
                "xanchor": "right",
                "x": 1,
            },
        }
    )
    if categorical_xaxis:
        layout["xaxis"] = {**layout["xaxis"], "type": "category"}
    return layout


def _add_categorical_reference_line(
    fig: go.Figure,
    *,
    x_value: str,
    annotation_text: str,
    line_color: str = "green",
) -> None:
    """Add a vertical reference line for a categorical x-axis."""
    fig.add_shape(
        type="line",
        x0=x_value,
        x1=x_value,
        y0=0,
        y1=1,
        xref="x",
        yref="paper",
        line=dict(width=2, dash="dash", color=line_color),
    )
    fig.add_annotation(
        x=x_value,
        y=1,
        xref="x",
        yref="paper",
        text=annotation_text,
        showarrow=False,
        yshift=10,
        font=dict(color=line_color),
        bgcolor="rgba(255,255,255,0.85)",
    )


def _coerce_positive_int(value: object) -> int | None:
    """Return a positive integer or ``None`` when unavailable."""
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return None
    return candidate if candidate > 0 else None


def _resolve_eos_display_context(summary_data: dict[str, Any], display_mode: str) -> dict[str, Any]:
    """Return derived plotting metadata for one EOS display mode."""
    fit_data = summary_data.get("fit", {})
    natoms = _coerce_positive_int(fit_data.get("natoms_in_fitted_cell"))
    if display_mode in {_EOS_DISPLAY_MODE_PER_ATOM, _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM} and natoms is not None:
        resolved_mode = str(display_mode)
    else:
        resolved_mode = _EOS_DISPLAY_MODE_CELL_TOTAL
    divisor = natoms if resolved_mode in {_EOS_DISPLAY_MODE_PER_ATOM, _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM} and natoms is not None else 1
    reference_energy_total_ev = fit_data.get("E0")
    if resolved_mode == _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM:
        return {
            "display_mode": resolved_mode,
            "natoms": natoms,
            "divisor": divisor,
            "reference_energy_total_ev": float(reference_energy_total_ev) if reference_energy_total_ev is not None else None,
            "xaxis_title": "Volume/atom (Å³/atom)",
            "yaxis_title": "ΔE (meV/atom)",
            "volume_label": "Volume/atom",
            "volume_unit": "Å³/atom",
            "energy_label": "ΔE relative to fitted E0",
            "energy_unit": "meV/atom",
            "annotation_label": "Equilibrium Volume/atom",
            "annotation_unit": "Å³/atom",
            "point_trace_name": "DFT ΔE",
            "fit_trace_name": "Birch-Murnaghan ΔE Fit",
        }
    if resolved_mode == _EOS_DISPLAY_MODE_PER_ATOM:
        return {
            "display_mode": resolved_mode,
            "natoms": natoms,
            "divisor": divisor,
            "reference_energy_total_ev": None,
            "xaxis_title": "Volume/atom (Å³)",
            "yaxis_title": "Energy/atom (eV)",
            "volume_label": "Volume/atom",
            "volume_unit": "Å³/atom",
            "energy_label": "Energy/atom",
            "energy_unit": "eV/atom",
            "annotation_label": "Equilibrium Volume/atom",
            "annotation_unit": "Å³/atom",
            "point_trace_name": "DFT Data",
            "fit_trace_name": "Birch-Murnaghan Fit",
        }
    return {
        "display_mode": _EOS_DISPLAY_MODE_CELL_TOTAL,
        "natoms": natoms,
        "divisor": 1,
        "reference_energy_total_ev": None,
        "xaxis_title": "Volume (Å³)",
        "yaxis_title": "Energy (eV)",
        "volume_label": "Fitted-Cell Volume",
        "volume_unit": "Å³",
        "energy_label": "Fitted-Cell Energy",
        "energy_unit": "eV",
        "annotation_label": "Equilibrium Volume",
        "annotation_unit": "Å³",
        "point_trace_name": "DFT Data",
        "fit_trace_name": "Birch-Murnaghan Fit",
    }


def _transform_eos_total_values_for_display(
    *,
    volume: object,
    total_energy_ev: object,
    display_context: dict[str, Any],
) -> tuple[float | None, float | None]:
    """Project one fitted-cell total ``(V, E)`` point into one EOS display mode."""
    try:
        display_volume = float(volume) / float(display_context["divisor"])
    except (TypeError, ValueError, ZeroDivisionError):
        display_volume = None

    try:
        total_energy = float(total_energy_ev)
    except (TypeError, ValueError):
        return display_volume, None

    if display_context["display_mode"] == _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM:
        reference_energy_total_ev = display_context.get("reference_energy_total_ev")
        if reference_energy_total_ev is None:
            return display_volume, None
        return display_volume, ((total_energy - float(reference_energy_total_ev)) * 1000.0) / float(display_context["divisor"])
    if display_context["display_mode"] == _EOS_DISPLAY_MODE_PER_ATOM:
        return display_volume, total_energy / float(display_context["divisor"])
    return display_volume, total_energy


def plot_cutoff_convergence(
    summary_data: dict[str, Any],
    *,
    figure_mode: str = INTERACTIVE_FIGURE_MODE,
) -> go.Figure:
    """Create a Plotly figure for a cutoff convergence study.

    Args:
        summary_data: The parsed cutoff study summary JSON payload.

    Returns:
        A Plotly Figure object.
    """
    points = summary_data.get("points", [])
    recommendation = summary_data.get("recommendation", {})
    publication_mode = is_publication_export_mode(figure_mode)

    # Extract valid points for the line plot
    # A point must have a valid energy_per_atom_ev to be plotted on the main line.
    valid_x = []
    valid_y = []
    hover_texts = []
    
    # We also keep track of all points for the scatter plot, 
    # but we will only draw lines between successful ones.
    for point in points:
        ecutwfc = point.get("ecutwfc_ry")
        ecutrho = point.get("ecutrho_ry")
        energy_ev = point.get("energy_per_atom_ev")
        total_energy_ry = point.get("total_energy_ry")
        delta_mev = point.get("delta_mev_per_atom")
        status = str(point.get("status", "unknown")).replace("_", " ")
        
        # Build tooltip text
        hover_parts = [
            f"<b>ecutwfc:</b> {ecutwfc} Ry",
            f"<b>ecutrho:</b> {ecutrho} Ry",
            f"<b>Status:</b> {status}",
        ]
        if total_energy_ry is not None:
            hover_parts.append(f"<b>Total Energy:</b> {total_energy_ry:.6f} Ry")
        if energy_ev is not None:
            hover_parts.append(f"<b>Energy/Atom:</b> {energy_ev:.6f} eV")
        if delta_mev is not None:
            hover_parts.append(f"<b>Δ Energy:</b> {delta_mev:.2f} meV/atom")
            
        hover_texts.append("<br>".join(hover_parts))
        
        # We only plot points that have an energy_per_atom_ev on the Y axis
        if energy_ev is not None:
            valid_x.append(ecutwfc)
            valid_y.append(energy_ev)

    fig = go.Figure()

    # Add the main trace for successful points
    if valid_x and valid_y:
        fig.add_trace(
            go.Scatter(
                x=valid_x,
                y=valid_y,
                mode="lines+markers",
                name="Energy/Atom",
                line=dict(color="#1f77b4", width=2.4 if publication_mode else 2),
                marker=dict(size=10 if publication_mode else 8, color="#1f77b4", symbol="circle"),
                hoverinfo="text",
                text=[hover_texts[points.index(p)] for p in points if p.get("energy_per_atom_ev") is not None],
            )
        )

    # Add missing/failed points as distinct markers at the bottom of the graph or skip them
    # For now, we skip drawing failed points but they will be visible in the summary table.

    # Add recommendation line if available
    recommended_ecutwfc = recommendation.get("recommended_ecutwfc_ry")
    rec_status = recommendation.get("status")

    if rec_status == "complete" and recommended_ecutwfc is not None:
        fig.add_vline(
            x=recommended_ecutwfc,
            line_width=2,
            line_dash="dash",
            line_color="green",
            annotation_text=f"Suggested Minimum Cutoff ({recommended_ecutwfc:g} Ry)",
            annotation_position="top right",
        )

    fig.update_layout(**_study_plot_layout(
        figure_mode=figure_mode,
        title="Cutoff Convergence",
        xaxis_title="Wavefunction Cutoff (Ry)",
        yaxis_title="Energy per Atom (eV)",
    ))

    return fig


def plot_kpoint_convergence(
    summary_data: dict[str, Any],
    *,
    figure_mode: str = INTERACTIVE_FIGURE_MODE,
) -> go.Figure:
    """Create a Plotly figure for a k-point convergence study.

    Args:
        summary_data: The parsed k-point study summary JSON payload.

    Returns:
        A Plotly Figure object.
    """
    points = summary_data.get("points", [])
    recommendation = summary_data.get("recommendation", {})
    publication_mode = is_publication_export_mode(figure_mode)

    valid_x = []
    valid_y = []
    hover_texts = []
    valid_hover_texts = []
    
    for point in points:
        grid_label = point.get("grid_label")
        energy_ev = point.get("energy_per_atom_ev")
        total_energy_ry = point.get("total_energy_ry")
        delta_mev = point.get("delta_mev_per_atom")
        status = str(point.get("status", "unknown")).replace("_", " ")
        
        hover_parts = [
            f"<b>Grid:</b> {grid_label}",
            f"<b>Status:</b> {status}",
        ]
        if total_energy_ry is not None:
            hover_parts.append(f"<b>Total Energy:</b> {total_energy_ry:.6f} Ry")
        if energy_ev is not None:
            hover_parts.append(f"<b>Energy/Atom:</b> {energy_ev:.6f} eV")
        if delta_mev is not None:
            hover_parts.append(f"<b>Δ Energy:</b> {delta_mev:.2f} meV/atom")
            
        hover_texts.append("<br>".join(hover_parts))
        
        if energy_ev is not None:
            valid_x.append(grid_label)
            valid_y.append(energy_ev)
            valid_hover_texts.append(hover_texts[-1])

    fig = go.Figure()

    if valid_x and valid_y:
        fig.add_trace(
            go.Scatter(
                x=valid_x,
                y=valid_y,
                mode="lines+markers",
                name="Energy/Atom",
                line=dict(color="#2ca02c", width=2.4 if publication_mode else 2),
                marker=dict(size=10 if publication_mode else 8, color="#2ca02c", symbol="circle"),
                hoverinfo="text",
                text=valid_hover_texts,
            )
        )

    recommended_grid_label = recommendation.get("grid_label")
    if not recommended_grid_label:
        recommended_k_grid = recommendation.get("recommended_k_grid")
        if recommended_k_grid:
            recommended_grid_label = "x".join(str(value) for value in recommended_k_grid)
    rec_status = recommendation.get("status")

    if rec_status == "complete" and recommended_grid_label:
        _add_categorical_reference_line(
            fig,
            x_value=str(recommended_grid_label),
            annotation_text=f"Suggested Minimum Grid ({recommended_grid_label})",
        )

    fig.update_layout(**_study_plot_layout(
        figure_mode=figure_mode,
        title="K-Point Convergence",
        xaxis_title="K-Point Grid",
        yaxis_title="Energy per Atom (eV)",
        categorical_xaxis=True,
    ))

    return fig


def plot_eos_study(
    summary_data: dict[str, Any],
    *,
    display_mode: str = _EOS_DISPLAY_MODE_CELL_TOTAL,
    figure_mode: str = INTERACTIVE_FIGURE_MODE,
) -> go.Figure:
    """Create a Plotly figure for an Equation of State (EOS) study.

    Args:
        summary_data: The parsed EOS study summary JSON payload.
        display_mode: Whether to display fitted-cell totals, per-atom values, or relative energy.

    Returns:
        A Plotly Figure object.
    """
    points = summary_data.get("points", [])
    fit_data = summary_data.get("fit", {})
    display_context = _resolve_eos_display_context(summary_data, display_mode)
    divisor = display_context["divisor"]
    publication_mode = is_publication_export_mode(figure_mode)

    valid_v = []
    valid_e = []
    valid_hover_texts = []
    
    for point in points:
        volume = point.get("volume")
        total_energy_ev = point.get("total_energy_ev")
        energy_per_atom_ev = point.get("energy_per_atom_ev")
        scale = point.get("scale_factor")
        status = str(point.get("status", "unknown")).replace("_", " ")
        natoms = display_context["natoms"]
        display_volume, display_energy = _transform_eos_total_values_for_display(
            volume=volume,
            total_energy_ev=total_energy_ev,
            display_context=display_context,
        )
        if display_context["display_mode"] == _EOS_DISPLAY_MODE_PER_ATOM and display_energy is None and energy_per_atom_ev is not None:
            display_energy = float(energy_per_atom_ev)
        
        hover_parts = [
            f"<b>Scale:</b> {scale:.4f}",
            (
                f"<b>{display_context['volume_label']}:</b> "
                f"{display_volume:.2f} {display_context['volume_unit']}"
            ) if display_volume is not None else "",
            f"<b>Status:</b> {status}",
        ]
        if display_energy is not None:
            hover_parts.append(
                f"<b>{display_context['energy_label']}:</b> "
                f"{float(display_energy):.6f} {display_context['energy_unit']}"
            )
        if display_context["display_mode"] == _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM:
            reference_energy_total_ev = display_context.get("reference_energy_total_ev")
            if reference_energy_total_ev is not None:
                hover_parts.append(f"<b>Reference E0 (fitted cell):</b> {float(reference_energy_total_ev):.6f} eV")
        if natoms is not None:
            hover_parts.append(f"<b>Fitted-Cell Atoms:</b> {natoms}")
        
        if display_energy is not None and display_volume is not None:
            valid_v.append(display_volume)
            valid_e.append(float(display_energy))
            valid_hover_texts.append("<br>".join(part for part in hover_parts if part))

    fig = go.Figure()

    # Raw DFT points
    if valid_v and valid_e:
        fig.add_trace(
            go.Scatter(
                x=valid_v,
                y=valid_e,
                mode="markers",
                name=str(display_context["point_trace_name"]),
                marker=dict(size=12 if publication_mode else 10, color="#d62728", symbol="circle"),
                hoverinfo="text",
                text=valid_hover_texts,
            )
        )

    # Fitted Curve
    if fit_data.get("success"):
        import numpy as np
        from qe_studio.convergence.eos_fit import birch_murnaghan
        
        V0 = fit_data.get("V0")
        E0 = fit_data.get("E0")
        B0 = fit_data.get("B0")
        B0_prime = fit_data.get("B0_prime")
        
        if all(x is not None for x in [V0, E0, B0, B0_prime]) and valid_v:
            # We need to convert B0 from GPa back to eV/Ang^3 to use the function properly
            # Or we can just use the B0 in eV/Ang^3. Wait, B0 is saved in GPa!
            EV_TO_JOULES = 1.602176634e-19
            ANGSTROM3_TO_M3 = 1e-30
            EV_ANG3_TO_GPA = EV_TO_JOULES / ANGSTROM3_TO_M3 / 1e9
            
            B0_ev_ang3 = B0 / EV_ANG3_TO_GPA
            
            v_min = min(valid_v)
            v_max = max(valid_v)
            v_range = v_max - v_min
            
            # Extend curve slightly beyond data points
            if display_context["display_mode"] in {_EOS_DISPLAY_MODE_PER_ATOM, _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM}:
                v_plot_total = np.linspace(v_min * divisor - 0.1 * v_range * divisor, v_max * divisor + 0.1 * v_range * divisor, 200)
                e_plot_total = birch_murnaghan(v_plot_total, E0, V0, B0_ev_ang3, B0_prime)
                transformed_pairs = [
                    _transform_eos_total_values_for_display(
                        volume=volume_total,
                        total_energy_ev=energy_total,
                        display_context=display_context,
                    )
                    for volume_total, energy_total in zip(v_plot_total, e_plot_total, strict=False)
                ]
                v_plot = [pair[0] for pair in transformed_pairs if pair[0] is not None and pair[1] is not None]
                e_plot = [pair[1] for pair in transformed_pairs if pair[0] is not None and pair[1] is not None]
            else:
                v_plot = np.linspace(v_min - 0.1 * v_range, v_max + 0.1 * v_range, 200)
                e_plot = birch_murnaghan(v_plot, E0, V0, B0_ev_ang3, B0_prime)
            
            fig.add_trace(
                go.Scatter(
                    x=v_plot,
                    y=e_plot,
                    mode="lines",
                    name=str(display_context["fit_trace_name"]),
                    line=dict(color="#1f77b4", width=2.4 if publication_mode else 2),
                    hoverinfo="skip"
                )
            )

    # If fit available, draw V0 line
    V0 = fit_data.get("V0")
    if fit_data.get("success") and V0 is not None:
        display_v0 = float(V0) / divisor if display_context["display_mode"] == _EOS_DISPLAY_MODE_PER_ATOM else float(V0)
        if display_context["display_mode"] == _EOS_DISPLAY_MODE_RELATIVE_PER_ATOM:
            display_v0 = float(V0) / divisor
        fig.add_vline(
            x=display_v0,
            line_width=2,
            line_dash="dash",
            line_color="green",
            annotation_text=f"{display_context['annotation_label']} ({display_v0:.2f} {display_context['annotation_unit']})",
            annotation_position="top right",
        )

    fig.update_layout(**_study_plot_layout(
        figure_mode=figure_mode,
        title="Equation of State (EOS)",
        xaxis_title=display_context["xaxis_title"],
        yaxis_title=display_context["yaxis_title"],
    ))

    return fig
