"""Per-browser context banks around the legacy, unprefixed scientific UI.

Switch before rendering widgets. Only domain values are restored: Streamlit
button and uploader widget states must never be assigned programmatically.
Nothing here cancels jobs or modifies the process-local runner registry.
"""
from __future__ import annotations

from . import state_keys
from .workspace import JOB_SUMMARIES_STATE_KEY
from ..services.qe_runner import RUN_STATE_KEYS

PREFIX = "_qe_workspace_"
ACTIVE_KEY = PREFIX + "active"
BANKS_KEY = PREFIX + "banks"
LOCATION_KEY = PREFIX + "location"
PENDING_KEY = PREFIX + "pending"
LEGACY = "legacy"
MANAGER = "manager"

DOMAIN_KEYS = {value for name, value in vars(state_keys).items() if name.endswith("_STATE_KEY")}
DOMAIN_KEYS.update(RUN_STATE_KEYS)
DOMAIN_KEYS.update({JOB_SUMMARIES_STATE_KEY, "pseudo_resolved_entries", "study_active_run_id",
                    "study_active_run_owner", "structure_active_notice_messages",
                    "structure_file_draft_nonce", "structure_manual_draft_nonce",
                    "structure_prototype_draft_nonce", "_pseudo_widget_sync_pending"})


def widget_key(project_id: str, name: str) -> str:
    return f"project:{project_id}:{name}"


def activate_context(state, identity: str, root: str | None = None) -> None:
    """Bind a context within this tab; folder names never select state banks."""
    previous = state.get(ACTIVE_KEY, LEGACY)
    if previous != identity:
        banks = state.setdefault(BANKS_KEY, {})
        banks[previous] = {key: state[key] for key in DOMAIN_KEYS if key in state}
        for key in list(state):
            if not str(key).startswith(PREFIX):
                del state[key]
        for key, value in banks.get(identity, {}).items():
            state[key] = value
    state[ACTIVE_KEY] = identity
    state[LOCATION_KEY] = root


def request_context(state, identity: str, root: str | None = None) -> None:
    # Applied on the next full rerun, before any widgets are instantiated.
    state[PENDING_KEY] = (identity, root)


def apply_pending_context(state) -> None:
    pending = state.pop(PENDING_KEY, None)
    if pending is not None:
        activate_context(state, *pending)
    elif ACTIVE_KEY not in state:
        activate_context(state, MANAGER)
