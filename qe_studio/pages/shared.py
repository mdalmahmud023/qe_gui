from __future__ import annotations

from collections.abc import Iterable, Sequence

import streamlit as st


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
