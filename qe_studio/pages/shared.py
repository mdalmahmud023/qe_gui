from __future__ import annotations

from collections.abc import Iterable, Sequence

import streamlit as st

from ..services.electronic_properties import (
    DEFAULT_PLOTLY_CONFIG,
    INTERACTIVE_FIGURE_MODE,
    PUBLICATION_EXPORT_FIGURE_MODE,
)


PLOT_COLUMN_WEIGHTS = [0.85, 5.3, 0.85]
FIGURE_MODE_OPTIONS = [INTERACTIVE_FIGURE_MODE, PUBLICATION_EXPORT_FIGURE_MODE]
EXPORT_FORMAT_OPTIONS = ["png", "svg"]


def make_plotly_chart_config(
    filename: str,
    *,
    export_format: str = "png",
    export_height: int | None = None,
    export_width: int | None = None,
    figure_mode: str = INTERACTIVE_FIGURE_MODE,
) -> dict:
    """Return Plotly export settings with a figure-specific filename."""
    export_options = dict(DEFAULT_PLOTLY_CONFIG["toImageButtonOptions"])
    export_options["filename"] = filename
    export_options["format"] = export_format
    export_options["scale"] = (
        1
        if export_format == "svg"
        else (3 if figure_mode == PUBLICATION_EXPORT_FIGURE_MODE else 2)
    )
    if export_height is not None:
        export_options["height"] = export_height
    if export_width is not None:
        export_options["width"] = export_width
    config = dict(DEFAULT_PLOTLY_CONFIG)
    config["toImageButtonOptions"] = export_options
    return config


def render_plotly_figure_options(
    *,
    figure_mode_widget_key: str,
    export_format_widget_key: str,
) -> tuple[str, str]:
    """Render shared interactive/publication and PNG/SVG figure controls."""
    with st.expander("Figure options", expanded=False):
        col1, col2 = st.columns([1.7, 1.2])
        figure_mode = col1.radio(
            "Figure mode",
            options=FIGURE_MODE_OPTIONS,
            horizontal=True,
            format_func=lambda mode: {
                INTERACTIVE_FIGURE_MODE: "Interactive",
                PUBLICATION_EXPORT_FIGURE_MODE: "Publication Export",
            }[mode],
            key=figure_mode_widget_key,
        )
        export_format = col2.selectbox(
            "Export format",
            options=EXPORT_FORMAT_OPTIONS,
            format_func=lambda file_format: {
                "png": "PNG",
                "svg": "SVG",
            }[file_format],
            key=export_format_widget_key,
        )
        if figure_mode == PUBLICATION_EXPORT_FIGURE_MODE:
            st.caption(
                "Publication Export uses larger fonts, tighter margins, and fixed export sizing. "
                "Use the Plotly camera button to save the current figure; SVG is recommended for journal layouts and PDF workflows."
            )
        else:
            st.caption(
                "Interactive keeps a compact on-screen layout. The Plotly camera button exports the current figure using the selected format."
            )
    return figure_mode, export_format


def render_centered_plotly_chart(
    figure,
    *,
    filename: str,
    export_format: str,
    figure_mode: str,
    export_width: int | None = None,
    column_weights: Sequence[float] = PLOT_COLUMN_WEIGHTS,
) -> None:
    """Render a theme-independent Plotly figure in a centered layout."""
    _left, center, _right = st.columns(column_weights)
    with center:
        st.plotly_chart(
            figure,
            use_container_width=True,
            config=make_plotly_chart_config(
                filename,
                export_format=export_format,
                export_height=int(figure.layout.height) if figure.layout.height is not None else None,
                export_width=export_width,
                figure_mode=figure_mode,
            ),
            theme=None,
        )


def render_status_messages(messages: Iterable[tuple[str, str]]) -> None:
    """Render a sequence of `(level, message)` tuples with Streamlit status widgets."""
    for level, message in messages:
        if not message:
            continue
        renderer = getattr(st, level, st.info)
        renderer(message)


def render_table(rows: Sequence[dict[str, str]], *, title: str | None = None) -> None:
    """Render a simple dataframe when rows are available."""
    if title:
        st.markdown(title)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)


def render_code_output(text: str) -> None:
    """Render a plain-text code block."""
    st.code(text, language="text")


def render_path_captions(items: Sequence[tuple[str, str]]) -> None:
    """Render small labeled path captions."""
    for label, path in items:
        st.caption(f"{label}: `{path}`")
