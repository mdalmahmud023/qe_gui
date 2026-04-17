from __future__ import annotations

from typing import Any

import plotly.graph_objects as go


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
                text=[hover_texts[points.index(p)] for p in points if p.get("energy_per_atom_ev") is not None],
            )
        )

    recommended_grid_label = recommendation.get("grid_label")
    rec_status = recommendation.get("status")

    if rec_status == "complete" and recommended_grid_label:
        fig.add_vline(
            x=recommended_grid_label,
            line_width=2,
            line_dash="dash",
            line_color="green",
            annotation_text=f"Suggested Minimum Grid ({recommended_grid_label})",
            annotation_position="top right",
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
