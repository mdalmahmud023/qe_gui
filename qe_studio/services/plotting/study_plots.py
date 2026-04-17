from __future__ import annotations

from typing import Any

import plotly.graph_objects as go


_EOS_DISPLAY_MODE_CELL_TOTAL = "cell_total"
_EOS_DISPLAY_MODE_PER_ATOM = "per_atom"


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
    resolved_mode = (
        _EOS_DISPLAY_MODE_PER_ATOM
        if display_mode == _EOS_DISPLAY_MODE_PER_ATOM and natoms is not None
        else _EOS_DISPLAY_MODE_CELL_TOTAL
    )
    divisor = natoms if resolved_mode == _EOS_DISPLAY_MODE_PER_ATOM and natoms is not None else 1
    if resolved_mode == _EOS_DISPLAY_MODE_PER_ATOM:
        return {
            "display_mode": resolved_mode,
            "natoms": natoms,
            "divisor": divisor,
            "xaxis_title": "Volume/atom (Å³)",
            "yaxis_title": "Energy/atom (eV)",
            "volume_label": "Volume/atom",
            "energy_label": "Energy/atom",
            "annotation_label": "Equilibrium Volume/atom",
        }
    return {
        "display_mode": _EOS_DISPLAY_MODE_CELL_TOTAL,
        "natoms": natoms,
        "divisor": 1,
        "xaxis_title": "Volume (Å³)",
        "yaxis_title": "Energy (eV)",
        "volume_label": "Fitted-Cell Volume",
        "energy_label": "Fitted-Cell Energy",
        "annotation_label": "Equilibrium Volume",
    }


def plot_cutoff_convergence(summary_data: dict[str, Any]) -> go.Figure:
    """Create a Plotly figure for a cutoff convergence study.

    Args:
        summary_data: The parsed cutoff study summary JSON payload.

    Returns:
        A Plotly Figure object.
    """
    points = summary_data.get("points", [])
    recommendation = summary_data.get("recommendation", {})

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
                line=dict(color="#1f77b4", width=2),
                marker=dict(size=8, color="#1f77b4", symbol="circle"),
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

    fig.update_layout(
        title="Cutoff Convergence",
        xaxis_title="Wavefunction Cutoff (Ry)",
        yaxis_title="Energy per Atom (eV)",
        hovermode="closest",
        template="plotly_white",
        margin=dict(l=40, r=40, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig


def plot_kpoint_convergence(summary_data: dict[str, Any]) -> go.Figure:
    """Create a Plotly figure for a k-point convergence study.

    Args:
        summary_data: The parsed k-point study summary JSON payload.

    Returns:
        A Plotly Figure object.
    """
    points = summary_data.get("points", [])
    recommendation = summary_data.get("recommendation", {})

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
                line=dict(color="#2ca02c", width=2),
                marker=dict(size=8, color="#2ca02c", symbol="circle"),
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

    fig.update_layout(
        title="K-Point Convergence",
        xaxis_title="K-Point Grid",
        yaxis_title="Energy per Atom (eV)",
        hovermode="closest",
        template="plotly_white",
        margin=dict(l=40, r=40, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(type='category')
    )

    return fig


def plot_eos_study(summary_data: dict[str, Any], *, display_mode: str = _EOS_DISPLAY_MODE_CELL_TOTAL) -> go.Figure:
    """Create a Plotly figure for an Equation of State (EOS) study.

    Args:
        summary_data: The parsed EOS study summary JSON payload.
        display_mode: Whether to display fitted-cell totals or derived per-atom values.

    Returns:
        A Plotly Figure object.
    """
    points = summary_data.get("points", [])
    fit_data = summary_data.get("fit", {})
    display_context = _resolve_eos_display_context(summary_data, display_mode)
    divisor = display_context["divisor"]

    valid_v = []
    valid_e = []
    hover_texts = []
    
    for point in points:
        volume = point.get("volume")
        total_energy_ev = point.get("total_energy_ev")
        energy_per_atom_ev = point.get("energy_per_atom_ev")
        scale = point.get("scale_factor")
        status = str(point.get("status", "unknown")).replace("_", " ")
        natoms = display_context["natoms"]
        if display_context["display_mode"] == _EOS_DISPLAY_MODE_PER_ATOM:
            display_volume = None if volume is None else float(volume) / divisor
            display_energy = (
                float(total_energy_ev) / divisor
                if total_energy_ev is not None
                else energy_per_atom_ev
            )
        else:
            display_volume = volume
            display_energy = total_energy_ev
        
        hover_parts = [
            f"<b>Scale:</b> {scale:.4f}",
            f"<b>{display_context['volume_label']}:</b> {display_volume:.2f} Å³" if display_volume is not None else "",
            f"<b>Status:</b> {status}",
        ]
        if display_energy is not None:
            hover_parts.append(f"<b>{display_context['energy_label']}:</b> {float(display_energy):.6f} {display_context['yaxis_title'].split('(')[1].rstrip(')')}")
        if natoms is not None:
            hover_parts.append(f"<b>Fitted-Cell Atoms:</b> {natoms}")
            
        hover_texts.append("<br>".join(part for part in hover_parts if part))
        
        if display_energy is not None and display_volume is not None:
            valid_v.append(display_volume)
            valid_e.append(float(display_energy))

    fig = go.Figure()

    # Raw DFT points
    if valid_v and valid_e:
        fig.add_trace(
            go.Scatter(
                x=valid_v,
                y=valid_e,
                mode="markers",
                name="DFT Data",
                marker=dict(size=10, color="#d62728", symbol="circle"),
                hoverinfo="text",
                text=[
                    hover_texts[points.index(p)]
                    for p in points
                    if (
                        (p.get("total_energy_ev") is not None and p.get("volume") is not None)
                        or (
                            display_context["display_mode"] == _EOS_DISPLAY_MODE_PER_ATOM
                            and p.get("energy_per_atom_ev") is not None
                            and p.get("volume") is not None
                        )
                    )
                ],
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
            if display_context["display_mode"] == _EOS_DISPLAY_MODE_PER_ATOM:
                v_plot_total = np.linspace(v_min * divisor - 0.1 * v_range * divisor, v_max * divisor + 0.1 * v_range * divisor, 200)
                e_plot_total = birch_murnaghan(v_plot_total, E0, V0, B0_ev_ang3, B0_prime)
                v_plot = v_plot_total / divisor
                e_plot = e_plot_total / divisor
            else:
                v_plot = np.linspace(v_min - 0.1 * v_range, v_max + 0.1 * v_range, 200)
                e_plot = birch_murnaghan(v_plot, E0, V0, B0_ev_ang3, B0_prime)
            
            fig.add_trace(
                go.Scatter(
                    x=v_plot,
                    y=e_plot,
                    mode="lines",
                    name="Birch-Murnaghan Fit",
                    line=dict(color="#1f77b4", width=2),
                    hoverinfo="skip"
                )
            )

    # If fit available, draw V0 line
    V0 = fit_data.get("V0")
    if fit_data.get("success") and V0 is not None:
        display_v0 = float(V0) / divisor if display_context["display_mode"] == _EOS_DISPLAY_MODE_PER_ATOM else float(V0)
        fig.add_vline(
            x=display_v0,
            line_width=2,
            line_dash="dash",
            line_color="green",
            annotation_text=f"{display_context['annotation_label']} ({display_v0:.2f} Å³)",
            annotation_position="top right",
        )

    fig.update_layout(
        title="Equation of State (EOS)",
        xaxis_title=display_context["xaxis_title"],
        yaxis_title=display_context["yaxis_title"],
        hovermode="closest",
        template="plotly_white",
        margin=dict(l=40, r=40, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig
