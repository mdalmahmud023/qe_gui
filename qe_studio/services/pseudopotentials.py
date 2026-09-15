"""Helpers for resolving and caching pseudopotentials for QE Studio.

The resolver manages standard SSSP manifest/archive downloads as well as an
alternate fully relativistic SG15 ONCV path for SOC-ready inputs. It also
exposes compatibility checks for downstream workflows such as spin-orbit
coupling (SOC), which requires fully relativistic pseudopotentials for every
species in the calculation.
"""

from __future__ import annotations

import json
import re
import shutil
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import requests

from ..core.legacy_structure_identity import get_structure_signature, get_unique_elements, has_same_element_set


DEFAULT_PSEUDO_CACHE_ROOT = Path("qe_runs") / "pseudopotentials" / "sssp"
DEFAULT_PSEUDO_BASE_CACHE_ROOT = DEFAULT_PSEUDO_CACHE_ROOT.parent
SSSP_RECORD_SLUGS = {
    "1.3.0": "rcyfm-68h65",
}
SUPPORTED_FUNCTIONALS = ("PBE", "PBEsol")
SUPPORTED_MODES = ("efficiency", "precision")
DOWNLOAD_TIMEOUT_SECONDS = 120
FULLY_RELATIVISTIC_SOURCE = "sg15_oncv_fr"
FULLY_RELATIVISTIC_FUNCTIONALS = ("PBE",)
FULLY_RELATIVISTIC_MODE = "fully_relativistic"
FULLY_RELATIVISTIC_VERSION = "sg15-oncv-2020-02-06"
SG15_ONCV_UPF_INDEX_URL = "http://quantum-simulation.org/potentials/sg15_oncv/upf"


class PseudopotentialResolutionError(RuntimeError):
    """Raised when pseudopotential resolution cannot complete safely."""


class PseudopotentialCompatibilityError(ValueError):
    """Raised when resolved pseudopotentials are incompatible with a workflow."""


@dataclass(frozen=True)
class SocCompatibilityRecord:
    """Compatibility result for one resolved UPF file."""

    element: str
    filename: str
    local_path: str | None
    compatible: bool
    reason: str


@dataclass(frozen=True)
class SocCompatibilityReport:
    """Compatibility report for using the resolved UPFs in minimal SOC mode."""

    records: tuple[SocCompatibilityRecord, ...]
    message: str

    @property
    def all_compatible(self) -> bool:
        """Return whether every resolved pseudopotential passed the SOC check."""
        return bool(self.records) and all(record.compatible for record in self.records)

    @property
    def incompatible_records(self) -> tuple[SocCompatibilityRecord, ...]:
        """Return the subset of records that failed the SOC check."""
        return tuple(record for record in self.records if not record.compatible)


def get_pseudo_cache_root(cache_root: Path | None = None) -> Path:
    """Return the root directory for the local pseudopotential cache."""
    return Path(cache_root) if cache_root is not None else DEFAULT_PSEUDO_CACHE_ROOT


def get_pseudo_base_cache_root(cache_root: Path | None = None) -> Path:
    """Return the root directory under which all pseudo families are cached."""
    return Path(cache_root) if cache_root is not None else DEFAULT_PSEUDO_BASE_CACHE_ROOT


def get_sssp_family_key(version: str, functional: str, mode: str) -> str:
    """Return the canonical versioned SSSP family key."""
    _validate_sssp_selection(version, functional, mode)
    return f"SSSP_{version}_{functional}_{mode}"


def get_sssp_manifest_filename(version: str, functional: str, mode: str) -> str:
    """Return the manifest filename for a supported SSSP family."""
    return f"{get_sssp_family_key(version, functional, mode)}.json"


def get_sssp_archive_filename(version: str, functional: str, mode: str) -> str:
    """Return the archive filename for a supported SSSP family."""
    return f"{get_sssp_family_key(version, functional, mode)}.tar.gz"


def get_sssp_record_base_url(version: str) -> str:
    """Return the Materials Cloud record base URL for a supported SSSP version."""
    try:
        slug = SSSP_RECORD_SLUGS[version]
    except KeyError as exc:
        raise PseudopotentialResolutionError(
            f"Unsupported SSSP version '{version}'. Supported versions: {', '.join(SSSP_RECORD_SLUGS)}."
        ) from exc
    return f"https://archive.materialscloud.org/records/{slug}/files"


def download_file(
    url: str,
    destination: Path,
    *,
    progress_callback: Callable[[float, str], None] | None = None,
    label: str | None = None,
) -> Path:
    """Download a remote file atomically to the requested destination path."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    display_label = label or f"Downloading {destination.name}"
    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            total_bytes = int(response.headers.get("content-length", "0") or 0)
            bytes_downloaded = 0
            _emit_progress(progress_callback, 0.0, f"{display_label}...")
            with NamedTemporaryFile(delete=False, dir=destination.parent, prefix=f"{destination.name}.") as tmp:
                temp_path = Path(tmp.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        tmp.write(chunk)
                        bytes_downloaded += len(chunk)
                        if total_bytes > 0:
                            downloaded_mb = bytes_downloaded / (1024 * 1024)
                            total_mb = total_bytes / (1024 * 1024)
                            _emit_progress(
                                progress_callback,
                                bytes_downloaded / total_bytes,
                                f"{display_label}: {downloaded_mb:.1f} / {total_mb:.1f} MB",
                            )
        if temp_path is None:
            raise PseudopotentialResolutionError(f"Failed to create a temporary file for '{destination}'.")
        temp_path.replace(destination)
        _emit_progress(progress_callback, 1.0, f"{display_label}: complete")
    except (OSError, requests.RequestException) as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise PseudopotentialResolutionError(f"Failed to download '{url}': {exc}") from exc

    return destination


def load_sssp_manifest(path: Path) -> dict[str, dict[str, Any]]:
    """Load and validate an SSSP manifest JSON file."""
    try:
        manifest = json.loads(path.read_text())
    except OSError as exc:
        raise PseudopotentialResolutionError(f"Failed to read manifest '{path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PseudopotentialResolutionError(f"Manifest '{path}' is not valid JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise PseudopotentialResolutionError(f"Manifest '{path}' must be a JSON object keyed by element symbol.")

    return manifest


def resolve_manifest_entries(manifest: dict[str, Any], elements: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve manifest entries for the requested elements or fail clearly."""
    resolved_entries: dict[str, dict[str, Any]] = {}
    missing_elements: list[str] = []
    invalid_entries: list[str] = []

    for element in elements:
        entry = manifest.get(element)
        if entry is None:
            missing_elements.append(element)
            continue
        if not isinstance(entry, dict) or not entry.get("filename"):
            invalid_entries.append(element)
            continue
        resolved_entries[element] = dict(entry)

    if missing_elements or invalid_entries:
        problems: list[str] = []
        if missing_elements:
            problems.append(f"elements not found in manifest: {', '.join(missing_elements)}")
        if invalid_entries:
            problems.append(f"manifest entries missing filenames: {', '.join(invalid_entries)}")
        raise PseudopotentialResolutionError("; ".join(problems))

    return resolved_entries


def ensure_sssp_archive_extracted(archive_path: Path, extract_dir: Path) -> Path:
    """Extract an SSSP archive if needed and return the extraction directory."""
    return _extract_archive(archive_path, extract_dir, force=False)


def find_extracted_upf(extract_dir: Path, filename: str) -> Path | None:
    """Find a specific UPF file by exact basename inside an extracted archive."""
    direct_path = extract_dir / filename
    if direct_path.is_file():
        return direct_path

    matches = [path for path in extract_dir.rglob("*") if path.is_file() and path.name == filename]
    if not matches:
        return None
    return matches[0]


def build_ase_pseudopotentials_map(resolved_entries: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Build the ASE element-to-filename pseudopotential mapping."""
    ase_map: dict[str, str] = {}
    for element, entry in resolved_entries.items():
        filename = entry.get("filename")
        if not filename:
            raise PseudopotentialResolutionError(
                f"Resolved pseudopotential entry for '{element}' does not contain a filename."
            )
        ase_map[element] = filename
    return ase_map


def inspect_soc_compatibility(resolved_entries: dict[str, dict[str, Any]]) -> SocCompatibilityReport:
    """Inspect resolved UPFs and report whether they support SOC mode.

    Compatibility is determined from each extracted UPF's metadata. Newer UPFs
    advertise SOC support through ``PP_HEADER`` fields such as ``has_so`` and
    ``relativistic="full"``. Older UPFs can require a fallback inspection of
    textual metadata such as ``PP_INFO`` for explicit scalar- or
    fully-relativistic annotations.
    """
    if not resolved_entries:
        return SocCompatibilityReport(
            records=(),
            message=(
                "Spin-orbit coupling requires resolved pseudopotential metadata with local "
                "UPF paths so compatibility can be validated."
            ),
        )

    records: list[SocCompatibilityRecord] = []
    for element, entry in sorted(resolved_entries.items()):
        filename = str(entry.get("filename") or "unknown")
        local_path_value = entry.get("local_path")
        if not local_path_value:
            records.append(
                SocCompatibilityRecord(
                    element=element,
                    filename=filename,
                    local_path=None,
                    compatible=False,
                    reason="missing extracted UPF path",
                )
            )
            continue

        upf_path = Path(local_path_value)
        if not upf_path.is_file():
            records.append(
                SocCompatibilityRecord(
                    element=element,
                    filename=filename,
                    local_path=str(upf_path),
                    compatible=False,
                    reason="resolved UPF file is missing",
                )
            )
            continue

        try:
            upf_text = upf_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            records.append(
                SocCompatibilityRecord(
                    element=element,
                    filename=filename,
                    local_path=str(upf_path),
                    compatible=False,
                    reason=f"failed to read UPF: {exc}",
                )
            )
            continue

        compatible, reason = _inspect_upf_soc_support(upf_text)

        records.append(
            SocCompatibilityRecord(
                element=element,
                filename=filename,
                local_path=str(upf_path),
                compatible=compatible,
                reason=reason,
            )
        )

    return SocCompatibilityReport(
        records=tuple(records),
        message=_format_soc_compatibility_message(records),
    )


def ensure_soc_compatible_pseudopotentials(
    resolved_entries: dict[str, dict[str, Any]],
) -> SocCompatibilityReport:
    """Raise a clear error if the resolved pseudopotentials cannot be used for SOC."""
    report = inspect_soc_compatibility(resolved_entries)
    if not report.all_compatible:
        raise PseudopotentialCompatibilityError(report.message)
    return report


def clear_sssp_family_cache(
    version: str,
    functional: str,
    mode: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    """Delete the cache subtree for a selected SSSP family."""
    family_cache_dir = _get_family_cache_dir(version, functional, mode, cache_root=cache_root)
    if family_cache_dir.exists():
        shutil.rmtree(family_cache_dir)
    return family_cache_dir


def clear_fully_relativistic_family_cache(
    functional: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    """Delete the cache subtree for the fully relativistic SG15 family."""
    family_cache_dir = _get_fully_relativistic_family_cache_dir(functional, cache_root=cache_root)
    if family_cache_dir.exists():
        shutil.rmtree(family_cache_dir)
    return family_cache_dir


def clear_pseudopotential_family_cache(
    *,
    functional: str,
    mode: str,
    version: str,
    fully_relativistic: bool,
    cache_root: Path | None = None,
) -> Path:
    """Delete the cache subtree for the selected pseudopotential family."""
    if fully_relativistic:
        return clear_fully_relativistic_family_cache(functional, cache_root=cache_root)
    return clear_sssp_family_cache(version, functional, mode, cache_root=cache_root)


def resolve_sssp_pseudopotentials(
    atoms,
    *,
    version: str,
    functional: str,
    mode: str,
    use_cache: bool,
    cache_root: Path | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Resolve, cache, and validate SSSP pseudopotentials for the given structure."""
    _validate_sssp_selection(version, functional, mode)

    structure_signature = get_structure_signature(atoms)
    elements = structure_signature["elements"]
    if not elements:
        raise PseudopotentialResolutionError("The structure does not contain any chemical elements.")

    family_cache_dir = _get_family_cache_dir(version, functional, mode, cache_root=cache_root)
    family_cache_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = family_cache_dir / "manifest.json"
    archive_path = family_cache_dir / "archive.tar.gz"
    extract_dir = family_cache_dir / "extracted"
    resolved_map_path = family_cache_dir / "resolved_map.json"

    manifest_url = _build_download_url(get_sssp_manifest_filename(version, functional, mode), version=version)
    archive_url = _build_download_url(get_sssp_archive_filename(version, functional, mode), version=version)

    warnings: list[str] = []
    _emit_progress(progress_callback, 0.02, "Preparing SSSP resolution...")
    manifest = _ensure_manifest(
        manifest_path=manifest_path,
        manifest_url=manifest_url,
        use_cache=use_cache,
        warnings=warnings,
        progress_callback=_make_scaled_progress_callback(progress_callback, 0.05, 0.15),
    )
    _emit_progress(progress_callback, 0.18, "Resolving required elements from the manifest...")
    resolved_entries = resolve_manifest_entries(manifest, elements)
    resolved_entries = _ensure_resolved_local_paths(
        resolved_entries=resolved_entries,
        archive_path=archive_path,
        archive_url=archive_url,
        extract_dir=extract_dir,
        use_cache=use_cache,
        warnings=warnings,
        progress_callback=progress_callback,
    )

    ase_map = build_ase_pseudopotentials_map(resolved_entries)
    _emit_progress(progress_callback, 0.96, "Writing local pseudopotential map...")
    pseudo_dir = str(extract_dir)
    table_rows = [
        {
            "element": element,
            "filename": entry["filename"],
            "local_path": entry["local_path"],
            "functional": functional,
            "mode": mode,
            "version": version,
        }
        for element, entry in resolved_entries.items()
    ]

    serialized_result = {
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "version": version,
            "functional": functional,
            "mode": mode,
            "fully_relativistic": False,
        },
        "structure_signature": structure_signature,
        "pseudo_dir": pseudo_dir,
        "ase_pseudopotentials": ase_map,
        "resolved_entries": resolved_entries,
    }
    resolved_map_path.write_text(json.dumps(serialized_result, indent=2, sort_keys=True))
    _emit_progress(progress_callback, 1.0, "Pseudopotential resolution complete.")

    return {
        "settings": serialized_result["settings"],
        "structure_signature": structure_signature,
        "pseudo_dir": pseudo_dir,
        "ase_pseudopotentials": ase_map,
        "resolved_entries": resolved_entries,
        "table_rows": table_rows,
        "warnings": warnings,
        "cache_paths": {
            "family_cache_dir": str(family_cache_dir.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "archive_path": str(archive_path.resolve()),
            "extract_dir": pseudo_dir,
            "resolved_map_path": str(resolved_map_path.resolve()),
        },
    }


def resolve_fully_relativistic_pseudopotentials(
    atoms,
    *,
    functional: str,
    use_cache: bool,
    cache_root: Path | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Resolve fully relativistic SG15 ONCV UPFs for the given structure."""
    _validate_fully_relativistic_selection(functional)

    structure_signature = get_structure_signature(atoms)
    elements = structure_signature["elements"]
    if not elements:
        raise PseudopotentialResolutionError("The structure does not contain any chemical elements.")

    family_cache_dir = _get_fully_relativistic_family_cache_dir(functional, cache_root=cache_root)
    family_cache_dir.mkdir(parents=True, exist_ok=True)

    index_path = family_cache_dir / "index.html"
    extract_dir = family_cache_dir / "extracted"
    resolved_map_path = family_cache_dir / "resolved_map.json"

    warnings: list[str] = [
        (
            "Fully relativistic SG15 ONCV UPFs do not include SSSP cutoff metadata. "
            "QE Studio will use heuristic cutoffs unless you override them."
        )
    ]
    _emit_progress(progress_callback, 0.02, "Preparing fully relativistic pseudopotential resolution...")
    available_filenames = _ensure_fully_relativistic_index(
        index_path=index_path,
        use_cache=use_cache,
        warnings=warnings,
        progress_callback=_make_scaled_progress_callback(progress_callback, 0.05, 0.20),
    )
    resolved_entries = resolve_fully_relativistic_index_entries(
        available_filenames,
        elements,
        functional=functional,
    )
    resolved_entries = _ensure_fully_relativistic_local_paths(
        resolved_entries=resolved_entries,
        extract_dir=extract_dir,
        use_cache=use_cache,
        progress_callback=_make_scaled_progress_callback(progress_callback, 0.21, 0.95),
    )

    ensure_soc_compatible_pseudopotentials(resolved_entries)
    ase_map = build_ase_pseudopotentials_map(resolved_entries)
    pseudo_dir = str(extract_dir)
    table_rows = [
        {
            "element": element,
            "filename": entry["filename"],
            "local_path": entry["local_path"],
            "functional": functional,
            "mode": FULLY_RELATIVISTIC_MODE,
            "version": FULLY_RELATIVISTIC_VERSION,
            "source": FULLY_RELATIVISTIC_SOURCE,
        }
        for element, entry in resolved_entries.items()
    ]

    serialized_result = {
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "version": FULLY_RELATIVISTIC_VERSION,
            "functional": functional,
            "mode": FULLY_RELATIVISTIC_MODE,
            "fully_relativistic": True,
            "source": FULLY_RELATIVISTIC_SOURCE,
        },
        "structure_signature": structure_signature,
        "pseudo_dir": pseudo_dir,
        "ase_pseudopotentials": ase_map,
        "resolved_entries": resolved_entries,
    }
    resolved_map_path.write_text(json.dumps(serialized_result, indent=2, sort_keys=True))
    _emit_progress(progress_callback, 1.0, "Fully relativistic pseudopotential resolution complete.")

    return {
        "settings": serialized_result["settings"],
        "structure_signature": structure_signature,
        "pseudo_dir": pseudo_dir,
        "ase_pseudopotentials": ase_map,
        "resolved_entries": resolved_entries,
        "table_rows": table_rows,
        "warnings": warnings,
        "cache_paths": {
            "family_cache_dir": str(family_cache_dir.resolve()),
            "index_path": str(index_path.resolve()),
            "extract_dir": pseudo_dir,
            "resolved_map_path": str(resolved_map_path.resolve()),
        },
    }


def _validate_sssp_selection(version: str, functional: str, mode: str) -> None:
    if version not in SSSP_RECORD_SLUGS:
        raise PseudopotentialResolutionError(
            f"Unsupported SSSP version '{version}'. Supported versions: {', '.join(SSSP_RECORD_SLUGS)}."
        )
    if functional not in SUPPORTED_FUNCTIONALS:
        raise PseudopotentialResolutionError(
            f"Unsupported functional '{functional}'. Supported values: {', '.join(SUPPORTED_FUNCTIONALS)}."
        )
    if mode not in SUPPORTED_MODES:
        raise PseudopotentialResolutionError(
            f"Unsupported mode '{mode}'. Supported values: {', '.join(SUPPORTED_MODES)}."
        )


def _validate_fully_relativistic_selection(functional: str) -> None:
    if functional not in FULLY_RELATIVISTIC_FUNCTIONALS:
        raise PseudopotentialResolutionError(
            "Fully relativistic pseudopotential resolution currently supports "
            f"{', '.join(FULLY_RELATIVISTIC_FUNCTIONALS)} only."
        )


def _get_family_cache_dir(
    version: str,
    functional: str,
    mode: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    return get_pseudo_cache_root(cache_root) / version / functional / mode


def _get_fully_relativistic_family_cache_dir(
    functional: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    return get_pseudo_base_cache_root(cache_root) / "sg15_oncv" / functional / FULLY_RELATIVISTIC_MODE


def _build_download_url(filename: str, *, version: str) -> str:
    return f"{get_sssp_record_base_url(version)}/{filename}?download=1"


def _build_fully_relativistic_download_url(filename: str) -> str:
    return f"{SG15_ONCV_UPF_INDEX_URL}/{filename}"


def _ensure_manifest(
    *,
    manifest_path: Path,
    manifest_url: str,
    use_cache: bool,
    warnings: list[str],
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, dict[str, Any]]:
    if not use_cache and manifest_path.exists():
        manifest_path.unlink()

    if not manifest_path.exists():
        download_file(
            manifest_url,
            manifest_path,
            progress_callback=progress_callback,
            label="Downloading manifest",
        )
    else:
        _emit_progress(progress_callback, 1.0, "Using cached manifest.")

    try:
        return load_sssp_manifest(manifest_path)
    except PseudopotentialResolutionError:
        warnings.append("Cached manifest was invalid. Re-downloading the SSSP manifest.")
        manifest_path.unlink(missing_ok=True)
        download_file(
            manifest_url,
            manifest_path,
            progress_callback=progress_callback,
            label="Re-downloading manifest",
        )
        return load_sssp_manifest(manifest_path)


def _ensure_archive_downloaded(
    *,
    archive_path: Path,
    archive_url: str,
    force_download: bool,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    if force_download and archive_path.exists():
        archive_path.unlink()
    if not archive_path.exists():
        download_file(
            archive_url,
            archive_path,
            progress_callback=progress_callback,
            label="Downloading SSSP archive",
        )
    else:
        _emit_progress(progress_callback, 1.0, "Using cached archive.")
    return archive_path


def _ensure_fully_relativistic_index(
    *,
    index_path: Path,
    use_cache: bool,
    warnings: list[str],
    progress_callback: Callable[[float, str], None] | None = None,
) -> list[str]:
    if not use_cache and index_path.exists():
        index_path.unlink()

    if not index_path.exists():
        download_file(
            SG15_ONCV_UPF_INDEX_URL,
            index_path,
            progress_callback=progress_callback,
            label="Downloading SG15 file index",
        )
    else:
        _emit_progress(progress_callback, 1.0, "Using cached SG15 file index.")

    try:
        return load_fully_relativistic_index(index_path)
    except PseudopotentialResolutionError:
        warnings.append("Cached SG15 file index was invalid. Re-downloading the index.")
        index_path.unlink(missing_ok=True)
        download_file(
            SG15_ONCV_UPF_INDEX_URL,
            index_path,
            progress_callback=progress_callback,
            label="Re-downloading SG15 file index",
        )
        return load_fully_relativistic_index(index_path)


def _ensure_resolved_local_paths(
    *,
    resolved_entries: dict[str, dict[str, Any]],
    archive_path: Path,
    archive_url: str,
    extract_dir: Path,
    use_cache: bool,
    warnings: list[str],
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, dict[str, Any]]:
    attempted_repair = False
    force_download = not use_cache

    while True:
        archive_download_progress = _make_scaled_progress_callback(progress_callback, 0.20, 0.75)
        extract_progress = _make_scaled_progress_callback(progress_callback, 0.76, 0.93)
        _ensure_archive_downloaded(
            archive_path=archive_path,
            archive_url=archive_url,
            force_download=force_download,
            progress_callback=archive_download_progress,
        )
        force_download = False

        try:
            _extract_archive(
                archive_path,
                extract_dir,
                force=attempted_repair or not use_cache,
                progress_callback=extract_progress,
            )
        except PseudopotentialResolutionError as exc:
            if attempted_repair:
                raise exc
            warnings.append("Cached archive extraction failed. Re-downloading the archive and retrying.")
            attempted_repair = True
            force_download = True
            continue

        missing_elements: list[str] = []
        updated_entries: dict[str, dict[str, Any]] = {}
        for element, entry in resolved_entries.items():
            entry_with_path = dict(entry)
            upf_path = find_extracted_upf(extract_dir, entry["filename"])
            if upf_path is None:
                missing_elements.append(element)
                continue
            entry_with_path["local_path"] = str(upf_path)
            updated_entries[element] = entry_with_path

        if not missing_elements:
            _emit_progress(progress_callback, 0.95, "Validated extracted UPF files.")
            return updated_entries

        if attempted_repair:
            raise PseudopotentialResolutionError(
                "Matching UPF files were missing after archive extraction for: "
                f"{', '.join(missing_elements)}."
            )

        warnings.append(
            "Some UPF files were missing after using the cached archive. Re-downloading the archive and retrying."
        )
        attempted_repair = True
        force_download = True


def _ensure_fully_relativistic_local_paths(
    *,
    resolved_entries: dict[str, dict[str, Any]],
    extract_dir: Path,
    use_cache: bool,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, dict[str, Any]]:
    extract_dir.mkdir(parents=True, exist_ok=True)
    total_entries = max(len(resolved_entries), 1)
    updated_entries: dict[str, dict[str, Any]] = {}

    for index, (element, entry) in enumerate(sorted(resolved_entries.items()), start=1):
        destination = extract_dir / entry["filename"]
        if not use_cache and destination.exists():
            destination.unlink()
        if not destination.exists():
            download_file(
                entry["download_url"],
                destination,
                progress_callback=None,
                label=f"Downloading {entry['filename']}",
            )
        entry_with_path = dict(entry)
        entry_with_path["local_path"] = str(destination)
        updated_entries[element] = entry_with_path
        _emit_progress(
            progress_callback,
            index / total_entries,
            f"Downloading fully relativistic UPFs: {index} / {total_entries} files",
        )

    return updated_entries


def _extract_archive(
    archive_path: Path,
    extract_dir: Path,
    *,
    force: bool,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Path:
    sentinel_path = extract_dir.parent / ".extract_complete"
    if force:
        shutil.rmtree(extract_dir, ignore_errors=True)
        sentinel_path.unlink(missing_ok=True)

    if extract_dir.exists() and sentinel_path.exists():
        _emit_progress(progress_callback, 1.0, "Using cached extracted UPF files.")
        return extract_dir

    shutil.rmtree(extract_dir, ignore_errors=True)
    sentinel_path.unlink(missing_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_extract_all(archive, extract_dir, progress_callback=progress_callback)
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        sentinel_path.unlink(missing_ok=True)
        raise PseudopotentialResolutionError(
            f"Failed to extract archive '{archive_path}': {exc}"
        ) from exc

    sentinel_path.write_text(datetime.now(timezone.utc).isoformat())
    return extract_dir


def _safe_extract_all(
    archive: tarfile.TarFile,
    destination: Path,
    progress_callback: Callable[[float, str], None] | None = None,
) -> None:
    destination_resolved = destination.resolve()
    members = archive.getmembers()
    member_count = max(len(members), 1)
    _emit_progress(progress_callback, 0.0, "Extracting SSSP archive...")
    for index, member in enumerate(members, start=1):
        member_path = (destination / member.name).resolve()
        try:
            member_path.relative_to(destination_resolved)
        except ValueError as exc:
            raise PseudopotentialResolutionError(
                f"Archive '{archive.name}' contains an unsafe path '{member.name}'."
            ) from exc
        archive.extract(member, destination, filter="data")
        _emit_progress(
            progress_callback,
            index / member_count,
            f"Extracting SSSP archive: {index} / {member_count} files",
        )


def _emit_progress(
    progress_callback: Callable[[float, str], None] | None,
    fraction: float,
    message: str,
) -> None:
    if progress_callback is None:
        return
    clamped_fraction = max(0.0, min(1.0, fraction))
    progress_callback(clamped_fraction, message)


def _make_scaled_progress_callback(
    progress_callback: Callable[[float, str], None] | None,
    start: float,
    end: float,
) -> Callable[[float, str], None] | None:
    if progress_callback is None:
        return None

    def scaled_callback(fraction: float, message: str) -> None:
        clamped_fraction = max(0.0, min(1.0, fraction))
        progress_callback(start + ((end - start) * clamped_fraction), message)

    return scaled_callback


def _extract_upf_pp_header(text: str) -> str | None:
    """Return the raw ``PP_HEADER`` tag or block from a UPF file."""
    match = re.search(r"<PP_HEADER\b.*?(?:/>|>.*?</PP_HEADER>)", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(0)
    return None


def load_fully_relativistic_index(path: Path) -> list[str]:
    """Load and validate the SG15 fully relativistic directory index."""
    try:
        html = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise PseudopotentialResolutionError(f"Failed to read SG15 index '{path}': {exc}") from exc

    filenames = sorted(set(re.findall(r'href="([^"]+\.upf)"', html, flags=re.IGNORECASE)))
    if not filenames:
        raise PseudopotentialResolutionError(f"SG15 index '{path}' did not contain any UPF filenames.")
    return filenames


def resolve_fully_relativistic_index_entries(
    available_filenames: list[str],
    elements: list[str],
    *,
    functional: str,
) -> dict[str, dict[str, Any]]:
    """Resolve SG15 fully relativistic filenames for the requested elements."""
    _validate_fully_relativistic_selection(functional)
    resolved_entries: dict[str, dict[str, Any]] = {}
    missing_elements: list[str] = []

    for element in elements:
        filename = _select_latest_fully_relativistic_filename(element, available_filenames, functional=functional)
        if filename is None:
            missing_elements.append(element)
            continue
        resolved_entries[element] = {
            "filename": filename,
            "pseudopotential": "SG15_ONCV",
            "source": FULLY_RELATIVISTIC_SOURCE,
            "download_url": _build_fully_relativistic_download_url(filename),
        }

    if missing_elements:
        raise PseudopotentialResolutionError(
            "Fully relativistic SG15 pseudopotentials were not available for: "
            f"{', '.join(missing_elements)}."
        )

    return resolved_entries


def _select_latest_fully_relativistic_filename(
    element: str,
    available_filenames: list[str],
    *,
    functional: str,
) -> str | None:
    """Select the latest SG15 fully relativistic filename for an element."""
    if functional != "PBE":
        return None

    pattern = re.compile(
        rf"^{re.escape(element)}_ONCV_PBE_FR-(\d+(?:\.\d+)*)\.upf$",
        flags=re.IGNORECASE,
    )
    candidates: list[tuple[tuple[int, ...], str]] = []
    for filename in available_filenames:
        match = pattern.match(filename)
        if match is None:
            continue
        version_tuple = tuple(int(part) for part in match.group(1).split("."))
        candidates.append((version_tuple, filename))

    if not candidates:
        return None

    candidates.sort()
    return candidates[-1][1]


def _extract_upf_header_value(header_text: str, key: str) -> str | None:
    """Extract one field from a raw ``PP_HEADER`` snippet."""
    patterns = [
        rf"\b{re.escape(key)}\s*=\s*['\"]?([^'\"\s>/]+)",
        rf"<{re.escape(key)}>\s*([^<]+?)\s*</{re.escape(key)}>",
        rf"^\s*{re.escape(key)}\s*[:=]?\s*['\"]?([A-Za-z0-9_.+-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, header_text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return None


def _is_truthy_value(value: str | None) -> bool:
    """Return whether a UPF boolean-ish token should be treated as true."""
    if value is None:
        return False
    return value.strip().lower() in {"t", "true", ".true.", "1", "yes", "y"}


def _inspect_upf_soc_support(text: str) -> tuple[bool, str]:
    """Classify one UPF file as SOC-capable or not with a user-facing reason."""
    header_text = _extract_upf_pp_header(text)
    if header_text is not None:
        has_so_value = _extract_upf_header_value(header_text, "has_so")
        relativistic_value = _extract_upf_header_value(header_text, "relativistic")
        has_so = _is_truthy_value(has_so_value)
        relativistic_full = (relativistic_value or "").strip().lower() == "full"

        if has_so or relativistic_full:
            reason_parts: list[str] = []
            if has_so_value is not None:
                reason_parts.append(f"has_so={has_so_value!r}")
            if relativistic_value is not None:
                reason_parts.append(f"relativistic={relativistic_value!r}")
            reason = ", ".join(reason_parts) if reason_parts else "PP_HEADER advertises spin-orbit support"
            return True, reason

        if relativistic_value is not None:
            return False, f"PP_HEADER reports relativistic={relativistic_value!r}, not 'full'"
        if has_so_value is not None:
            return False, f"PP_HEADER reports has_so={has_so_value!r}, not a truthy value"

    legacy_result = _inspect_legacy_upf_soc_support(text)
    if legacy_result is not None:
        return legacy_result

    if header_text is None:
        return False, "PP_HEADER not found"
    return False, "UPF metadata does not advertise spin-orbit support"


def _inspect_legacy_upf_soc_support(text: str) -> tuple[bool, str] | None:
    """Inspect older UPF metadata when explicit XML-style SOC fields are absent."""
    if re.search(r"<PP_SPIN_ORB\b", text, flags=re.IGNORECASE):
        return True, "UPF contains PP_SPIN_ORB data"
    if re.search(r"\bscalar[-\s]+relativistic calculation\b", text, flags=re.IGNORECASE):
        return False, "UPF metadata reports a scalar-relativistic calculation"
    if re.search(r"\bfully[-\s]+relativistic(?:\s+calculation)?\b", text, flags=re.IGNORECASE):
        return True, "UPF metadata reports a fully relativistic calculation"
    return None


def _format_soc_compatibility_message(records: list[SocCompatibilityRecord]) -> str:
    """Build a user-facing SOC compatibility summary."""
    if not records:
        return (
            "Spin-orbit coupling requires resolved pseudopotential metadata with local "
            "UPF paths so compatibility can be validated."
        )

    incompatible = [record for record in records if not record.compatible]
    if not incompatible:
        return "All resolved pseudopotentials advertise fully relativistic spin-orbit support."

    details = "; ".join(
        f"{record.element} ({record.filename}): {record.reason}"
        for record in incompatible
    )
    return (
        "Spin-orbit coupling requires fully relativistic pseudopotentials for all species. "
        f"Use fully relativistic UPFs for every element before enabling SOC. "
        f"Incompatible pseudopotentials: {details}"
    )
