from __future__ import annotations

from pathlib import Path
import re
import unicodedata

DEFAULT_PREFIX_FALLBACK = "qe_studio"
LEGACY_DEFAULT_PREFIX = "pwscf"
DEFAULT_OUTDIR = "./tmp"
LEGACY_OUTDIR = "."
DEFAULT_PREFIX_MAX_LENGTH = 64
_ASSIGNMENT_TEMPLATE = r"^\s*{key}\s*=\s*['\"]([^'\"]+)['\"]"


def default_qe_prefix(atoms) -> str:
    """Return a safe default QE prefix derived from the structure formula."""
    if atoms is None:
        return DEFAULT_PREFIX_FALLBACK
    try:
        formula = atoms.get_chemical_formula()
    except Exception:
        formula = None
    return sanitize_qe_prefix(formula, fallback=DEFAULT_PREFIX_FALLBACK)


def sanitize_qe_prefix(
    raw: object,
    *,
    fallback: str = DEFAULT_PREFIX_FALLBACK,
    max_length: int = DEFAULT_PREFIX_MAX_LENGTH,
) -> str:
    """Return a QE-safe prefix using a conservative lowercase ASCII policy."""
    if max_length < 1:
        raise ValueError(f"max_length must be positive, got {max_length}")

    sanitized_fallback = _sanitize_qe_prefix_value(str(fallback), max_length=max_length)
    candidate = "" if raw is None else str(raw)
    sanitized = _sanitize_qe_prefix_value(candidate, max_length=max_length)
    return sanitized or sanitized_fallback or DEFAULT_PREFIX_FALLBACK


def describe_qe_prefix_sanitization(
    raw: object,
    *,
    fallback: str = DEFAULT_PREFIX_FALLBACK,
    max_length: int = DEFAULT_PREFIX_MAX_LENGTH,
) -> dict[str, object]:
    """Return the rendered prefix plus machine-friendly sanitization flags."""
    candidate = "" if raw is None else str(raw)
    unlimited = _sanitize_qe_prefix_value(candidate, max_length=max(max_length, len(candidate) + 8))
    rendered = sanitize_qe_prefix(candidate, fallback=fallback, max_length=max_length)
    fallback_rendered = sanitize_qe_prefix(fallback, fallback=DEFAULT_PREFIX_FALLBACK, max_length=max_length)
    return {
        "requested": candidate,
        "rendered": rendered,
        "sanitized": bool(candidate.strip()) and rendered != candidate.strip(),
        "truncated": bool(unlimited) and len(unlimited) > max_length and rendered == unlimited[:max_length].rstrip("_"),
        "used_fallback": not bool(unlimited) and rendered == fallback_rendered,
    }


def normalize_qe_outdir(raw: object, *, default: str = DEFAULT_OUTDIR) -> str:
    """Return a normalized rendered outdir string."""
    candidate = "" if raw is None else str(raw).strip()
    normalized_default = str(default).strip() or DEFAULT_OUTDIR
    return candidate or normalized_default


def resolve_qe_outdir(run_dir: Path | str, rendered_outdir: object) -> Path:
    """Resolve a rendered QE outdir value against one run directory."""
    run_dir_path = Path(run_dir)
    normalized = normalize_qe_outdir(rendered_outdir)
    outdir_path = Path(normalized)
    if outdir_path.is_absolute():
        return outdir_path
    return run_dir_path / outdir_path


def parse_qe_control_from_input(text: str | None) -> dict[str, str]:
    """Parse CONTROL prefix/outdir from one QE input text block."""
    candidate_text = text or ""
    prefix_match = re.search(
        _ASSIGNMENT_TEMPLATE.format(key="prefix"),
        candidate_text,
        re.IGNORECASE | re.MULTILINE,
    )
    outdir_match = re.search(
        _ASSIGNMENT_TEMPLATE.format(key="outdir"),
        candidate_text,
        re.IGNORECASE | re.MULTILINE,
    )
    prefix = prefix_match.group(1).strip() if prefix_match else DEFAULT_PREFIX_FALLBACK
    outdir = outdir_match.group(1).strip() if outdir_match else DEFAULT_OUTDIR
    return {
        "prefix": prefix or DEFAULT_PREFIX_FALLBACK,
        "outdir": normalize_qe_outdir(outdir, default=DEFAULT_OUTDIR),
    }


def _sanitize_qe_prefix_value(value: str, *, max_length: int) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9_]+", "_", ascii_value.lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if normalized and not normalized[0].isalpha():
        normalized = f"qe_{normalized}"
    if len(normalized) > max_length:
        normalized = normalized[:max_length].rstrip("_")
    return normalized
