from __future__ import annotations

import plotly.graph_objects as go
from qe_studio.services.plotting.study_plots import (
    plot_cutoff_convergence,
    plot_kpoint_convergence,
)


def test_plot_cutoff_convergence_success():
    summary_data = {
        "study_status": "complete",
        "points": [
            {
                "index": 1,
                "ecutwfc_ry": 30.0,
                "ecutrho_ry": 240.0,
                "status": "success",
                "total_energy_ry": -100.0,
                "energy_per_atom_ev": -1360.0,
                "delta_mev_per_atom": None,
            },
            {
                "index": 2,
                "ecutwfc_ry": 40.0,
                "ecutrho_ry": 320.0,
                "status": "success",
                "total_energy_ry": -100.1,
                "energy_per_atom_ev": -1361.36,
                "delta_mev_per_atom": 1360.0,
            },
        ],
        "recommendation": {
            "status": "complete",
            "recommended_ecutwfc_ry": 40.0,
            "recommended_ecutrho_ry": 320.0,
            "message": "Looks good",
        }
    }
    
    fig = plot_cutoff_convergence(summary_data)
    
    assert isinstance(fig, go.Figure)
    # Check that there is one trace (the line/scatter trace)
    # Wait, add_vline adds shapes or annotations, but trace count is 1
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.type == "scatter"
    assert trace.x == (30.0, 40.0)
    assert trace.y == (-1360.0, -1361.36)
    
    # Check recommendation line was added (vline is represented as a shape/annotation usually, but we can just check if figure renders without error)
    # Shapes count
    assert len(fig.layout.shapes) == 1
    shape = fig.layout.shapes[0]
    assert shape.x0 == 40.0

def test_plot_cutoff_convergence_incomplete():
    summary_data = {
        "study_status": "incomplete",
        "points": [
            {
                "index": 1,
                "ecutwfc_ry": 30.0,
                "ecutrho_ry": 240.0,
                "status": "success",
                "total_energy_ry": -100.0,
                "energy_per_atom_ev": -1360.0,
                "delta_mev_per_atom": None,
            },
            {
                "index": 2,
                "ecutwfc_ry": 40.0,
                "ecutrho_ry": 320.0,
                "status": "failed",
                "total_energy_ry": None,
                "energy_per_atom_ev": None,
                "delta_mev_per_atom": None,
            },
        ],
        "recommendation": {
            "status": "incomplete",
            "message": "Incomplete",
        }
    }
    
    fig = plot_cutoff_convergence(summary_data)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    trace = fig.data[0]
    # Failed points without energy shouldn't be plotted on the main trace
    assert trace.x == (30.0,)
    
    # No shapes added because no recommendation
    assert not fig.layout.shapes


def test_plot_kpoint_convergence_with_categorical_recommendation():
    summary_data = {
        "study_status": "complete",
        "points": [
            {
                "index": 1,
                "grid_label": "2x2x2",
                "status": "success",
                "total_energy_ry": -100.0,
                "energy_per_atom_ev": -1360.0,
                "delta_mev_per_atom": None,
            },
            {
                "index": 2,
                "grid_label": "4x4x4",
                "status": "success",
                "total_energy_ry": -100.1,
                "energy_per_atom_ev": -1361.0,
                "delta_mev_per_atom": 10.0,
            },
        ],
        "recommendation": {
            "status": "complete",
            "recommended_k_grid": [4, 4, 4],
            "grid_label": "4x4x4",
            "message": "Looks good",
        },
    }

    fig = plot_kpoint_convergence(summary_data)

    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.type == "scatter"
    assert trace.x == ("2x2x2", "4x4x4")
    assert trace.y == (-1360.0, -1361.0)

    assert len(fig.layout.shapes) == 1
    shape = fig.layout.shapes[0]
    assert shape.x0 == "4x4x4"
    assert shape.x1 == "4x4x4"

    assert len(fig.layout.annotations) == 1
    annotation = fig.layout.annotations[0]
    assert annotation.x == "4x4x4"
