from __future__ import annotations

import plotly.graph_objects as go

from qe_studio.pages import shared, ui_analysis, ui_studies
from qe_studio.services.electronic_properties import (
    INTERACTIVE_FIGURE_MODE,
    PUBLICATION_EXPORT_FIGURE_MODE,
)


class _ContextColumn:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


def test_make_plotly_chart_config_uses_analysis_export_scales() -> None:
    interactive_png = shared.make_plotly_chart_config(
        "qe_studio_cutoff_convergence",
        export_format="png",
        export_height=500,
        figure_mode=INTERACTIVE_FIGURE_MODE,
    )
    publication_png = shared.make_plotly_chart_config(
        "qe_studio_kpoint_convergence",
        export_format="png",
        export_height=560,
        export_width=980,
        figure_mode=PUBLICATION_EXPORT_FIGURE_MODE,
    )
    publication_svg = shared.make_plotly_chart_config(
        "qe_studio_eos",
        export_format="svg",
        export_height=560,
        export_width=980,
        figure_mode=PUBLICATION_EXPORT_FIGURE_MODE,
    )

    assert interactive_png["displaylogo"] is False
    assert interactive_png["responsive"] is True
    assert interactive_png["toImageButtonOptions"] == {
        "format": "png",
        "scale": 2,
        "filename": "qe_studio_cutoff_convergence",
        "height": 500,
    }
    assert publication_png["toImageButtonOptions"] == {
        "format": "png",
        "scale": 3,
        "filename": "qe_studio_kpoint_convergence",
        "height": 560,
        "width": 980,
    }
    assert publication_svg["toImageButtonOptions"] == {
        "format": "svg",
        "scale": 1,
        "filename": "qe_studio_eos",
        "height": 560,
        "width": 980,
    }


def test_render_centered_plotly_chart_disables_streamlit_theme(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_columns(weights):
        captured["weights"] = list(weights)
        return [_ContextColumn(), _ContextColumn(), _ContextColumn()]

    def fake_plotly_chart(figure, **kwargs):
        captured["figure"] = figure
        captured["kwargs"] = kwargs

    monkeypatch.setattr(shared.st, "columns", fake_columns)
    monkeypatch.setattr(shared.st, "plotly_chart", fake_plotly_chart)

    figure = go.Figure()
    figure.update_layout(height=560)
    shared.render_centered_plotly_chart(
        figure,
        filename="qe_studio_eos",
        export_format="svg",
        figure_mode=PUBLICATION_EXPORT_FIGURE_MODE,
        export_width=980,
    )

    assert captured["weights"] == shared.PLOT_COLUMN_WEIGHTS
    assert captured["figure"] is figure
    kwargs = captured["kwargs"]
    assert kwargs["theme"] is None
    assert kwargs["use_container_width"] is True
    assert kwargs["config"]["toImageButtonOptions"]["filename"] == "qe_studio_eos"
    assert kwargs["config"]["toImageButtonOptions"]["format"] == "svg"
    assert kwargs["config"]["toImageButtonOptions"]["height"] == 560
    assert kwargs["config"]["toImageButtonOptions"]["width"] == 980


def test_studies_figure_state_is_independent_from_analysis() -> None:
    assert ui_studies.STUDIES_FIGURE_MODE_WIDGET_KEY != ui_analysis.ANALYSIS_FIGURE_MODE_WIDGET_KEY
    assert ui_studies.STUDIES_EXPORT_FORMAT_WIDGET_KEY != ui_analysis.ANALYSIS_EXPORT_FORMAT_WIDGET_KEY
    assert {
        ui_studies.CUTOFF_STUDY_EXPORT_FILENAME,
        ui_studies.KPOINT_STUDY_EXPORT_FILENAME,
        ui_studies.EOS_STUDY_EXPORT_FILENAME,
    } == {
        "qe_studio_cutoff_convergence",
        "qe_studio_kpoint_convergence",
        "qe_studio_eos",
    }
