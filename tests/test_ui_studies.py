from __future__ import annotations

import pytest
from qe_studio.pages import ui_studies

def test_ui_studies_importable():
    assert ui_studies is not None
