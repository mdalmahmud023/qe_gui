from __future__ import annotations

import plotly.graph_objects as go
import pytest
from qe_studio.services.plotting.study_plots import (
    plot_cutoff_convergence,
    plot_eos_study,
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


def test_plot_eos_study_display_modes():
    summary_data = {
        "points": [
            {
                "index": 1,
                "scale_factor": 0.98,
                "volume": 98.0,
                "status": "success",
                "total_energy_ev": -19.8,
                "energy_per_atom_ev": -9.9,
            },
            {
                "index": 2,
                "scale_factor": 1.00,
                "volume": 100.0,
                "status": "success",
                "total_energy_ev": -20.0,
                "energy_per_atom_ev": -10.0,
            },
            {
                "index": 3,
                "scale_factor": 1.02,
                "volume": 102.0,
                "status": "success",
                "total_energy_ev": -19.7,
                "energy_per_atom_ev": -9.85,
            },
        ],
        "fit": {
            "success": True,
            "V0": 100.0,
            "E0": -20.0,
            "B0": 120.0,
            "B0_prime": 4.0,
            "natoms_in_fitted_cell": 2,
        },
    }

    fig_cell_total = plot_eos_study(summary_data, display_mode="cell_total")
    fig_per_atom = plot_eos_study(summary_data, display_mode="per_atom")
    fig_relative = plot_eos_study(summary_data, display_mode="relative_per_atom")

    assert isinstance(fig_cell_total, go.Figure)
    assert isinstance(fig_per_atom, go.Figure)
    assert isinstance(fig_relative, go.Figure)
    assert fig_cell_total.layout.xaxis.title.text == "Volume (Å³)"
    assert fig_cell_total.layout.yaxis.title.text == "Energy (eV)"
    assert fig_per_atom.layout.xaxis.title.text == "Volume/atom (Å³)"
    assert fig_per_atom.layout.yaxis.title.text == "Energy/atom (eV)"
    assert fig_relative.layout.xaxis.title.text == "Volume/atom (Å³/atom)"
    assert fig_relative.layout.yaxis.title.text == "ΔE (meV/atom)"

    cell_trace = fig_cell_total.data[0]
    per_atom_trace = fig_per_atom.data[0]
    relative_trace = fig_relative.data[0]
    assert cell_trace.x == (98.0, 100.0, 102.0)
    assert cell_trace.y == (-19.8, -20.0, -19.7)
    assert per_atom_trace.x == (49.0, 50.0, 51.0)
    assert per_atom_trace.y == (-9.9, -10.0, -9.85)
    assert relative_trace.x == (49.0, 50.0, 51.0)
    assert relative_trace.y == pytest.approx((100.0, 0.0, 150.0))

    assert fig_cell_total.layout.shapes[0].x0 == 100.0
    assert fig_per_atom.layout.shapes[0].x0 == 50.0
    assert fig_relative.layout.shapes[0].x0 == 50.0
    assert "Fitted-Cell Energy" in cell_trace.text[0]
    assert "Energy/atom" in per_atom_trace.text[0]
    assert "ΔE relative to fitted E0" in relative_trace.text[0]
    assert "Reference E0 (fitted cell)" in relative_trace.text[0]
    assert fig_relative.data[0].name == "DFT ΔE"
    assert fig_relative.data[1].name == "Birch-Murnaghan ΔE Fit"

    cell_fit_trace = fig_cell_total.data[1]
    relative_fit_trace = fig_relative.data[1]
    assert list(relative_fit_trace.x) == pytest.approx([float(value) / 2.0 for value in cell_fit_trace.x])
    assert list(relative_fit_trace.y) == pytest.approx([((float(value) + 20.0) * 1000.0) / 2.0 for value in cell_fit_trace.y])
