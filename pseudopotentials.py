"""Helpers for resolving SSSP pseudopotentials from Materials Cloud."""

from __future__ import annotations

import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import requests


DEFAULT_PSEUDO_CACHE_ROOT = Path("qe_runs") / "pseudopotentials" / "sssp"
SSSP_RECORD_SLUGS = {
    "1.3.0": "rcyfm-68h65",
}
SUPPORTED_FUNCTIONALS = ("PBE", "PBEsol")
SUPPORTED_MODES = ("efficiency", "precision")
DOWNLOAD_TIMEOUT_SECONDS = 120


class PseudopotentialResolutionError(RuntimeError):
    """Raised when pseudopotential resolution cannot complete safely."""


def get_unique_elements(atoms) -> list[str]:
    """Return unique chemical symbols in deterministic sorted order."""
    return sorted(set(atoms.get_chemical_symbols()))


def get_structure_signature(atoms) -> dict[str, Any]:
    """Build a lightweight structure signature for invalidation checks."""
    return {
        "elements": get_unique_elements(atoms),
        "formula": atoms.get_chemical_formula(),
        "atom_count": len(atoms),
    }


def has_same_element_set(
    first_signature: dict[str, Any] | None,
    second_signature: dict[str, Any] | None,
) -> bool:
    """Return whether two structure signatures contain the same element set."""
    if not first_signature or not second_signature:
        return False
    return list(first_signature.get("elements", [])) == list(second_signature.get("elements", []))


def get_pseudo_cache_root(cache_root: Path | None = None) -> Path:
    """Return the root directory for the local pseudopotential cache."""
    return Path(cache_root) if cache_root is not None else DEFAULT_PSEUDO_CACHE_ROOT


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


def download_file(url: str, destination: Path) -> Path:
    """Download a remote file atomically to the requested destination path."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            with NamedTemporaryFile(delete=False, dir=destination.parent, prefix=f"{destination.name}.") as tmp:
                temp_path = Path(tmp.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        tmp.write(chunk)
        if temp_path is None:
            raise PseudopotentialResolutionError(f"Failed to create a temporary file for '{destination}'.")
        temp_path.replace(destination)
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


def resolve_sssp_pseudopotentials(
    atoms,
    *,
    version: str,
    functional: str,
    mode: str,
    use_cache: bool,
    cache_root: Path | None = None,
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
    manifest = _ensure_manifest(
        manifest_path=manifest_path,
        manifest_url=manifest_url,
        use_cache=use_cache,
        warnings=warnings,
    )
    resolved_entries = resolve_manifest_entries(manifest, elements)
    resolved_entries = _ensure_resolved_local_paths(
        resolved_entries=resolved_entries,
        archive_path=archive_path,
        archive_url=archive_url,
        extract_dir=extract_dir,
        use_cache=use_cache,
        warnings=warnings,
    )

    ase_map = build_ase_pseudopotentials_map(resolved_entries)
    pseudo_dir = str(extract_dir.resolve())
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
        },
        "structure_signature": structure_signature,
        "pseudo_dir": pseudo_dir,
        "ase_pseudopotentials": ase_map,
        "resolved_entries": resolved_entries,
    }
    resolved_map_path.write_text(json.dumps(serialized_result, indent=2, sort_keys=True))

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


def _get_family_cache_dir(
    version: str,
    functional: str,
    mode: str,
    *,
    cache_root: Path | None = None,
) -> Path:
    return get_pseudo_cache_root(cache_root) / version / functional / mode


def _build_download_url(filename: str, *, version: str) -> str:
    return f"{get_sssp_record_base_url(version)}/{filename}?download=1"


def _ensure_manifest(
    *,
    manifest_path: Path,
    manifest_url: str,
    use_cache: bool,
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    if not use_cache and manifest_path.exists():
        manifest_path.unlink()

    if not manifest_path.exists():
        download_file(manifest_url, manifest_path)

    try:
        return load_sssp_manifest(manifest_path)
    except PseudopotentialResolutionError:
        warnings.append("Cached manifest was invalid. Re-downloading the SSSP manifest.")
        manifest_path.unlink(missing_ok=True)
        download_file(manifest_url, manifest_path)
        return load_sssp_manifest(manifest_path)


def _ensure_archive_downloaded(
    *,
    archive_path: Path,
    archive_url: str,
    force_download: bool,
) -> Path:
    if force_download and archive_path.exists():
        archive_path.unlink()
    if not archive_path.exists():
        download_file(archive_url, archive_path)
    return archive_path


def _ensure_resolved_local_paths(
    *,
    resolved_entries: dict[str, dict[str, Any]],
    archive_path: Path,
    archive_url: str,
    extract_dir: Path,
    use_cache: bool,
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    attempted_repair = False
    force_download = not use_cache

    while True:
        _ensure_archive_downloaded(
            archive_path=archive_path,
            archive_url=archive_url,
            force_download=force_download,
        )
        force_download = False

        try:
            _extract_archive(archive_path, extract_dir, force=attempted_repair or not use_cache)
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
            entry_with_path["local_path"] = str(upf_path.resolve())
            updated_entries[element] = entry_with_path

        if not missing_elements:
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


def _extract_archive(archive_path: Path, extract_dir: Path, *, force: bool) -> Path:
    sentinel_path = extract_dir.parent / ".extract_complete"
    if force:
        shutil.rmtree(extract_dir, ignore_errors=True)
        sentinel_path.unlink(missing_ok=True)

    if extract_dir.exists() and sentinel_path.exists():
        return extract_dir

    shutil.rmtree(extract_dir, ignore_errors=True)
    sentinel_path.unlink(missing_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_extract_all(archive, extract_dir)
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        sentinel_path.unlink(missing_ok=True)
        raise PseudopotentialResolutionError(
            f"Failed to extract archive '{archive_path}': {exc}"
        ) from exc

    sentinel_path.write_text(datetime.now(timezone.utc).isoformat())
    return extract_dir


def _safe_extract_all(archive: tarfile.TarFile, destination: Path) -> None:
    destination_resolved = destination.resolve()
    for member in archive.getmembers():
        member_path = (destination / member.name).resolve()
        try:
            member_path.relative_to(destination_resolved)
        except ValueError as exc:
            raise PseudopotentialResolutionError(
                f"Archive '{archive.name}' contains an unsafe path '{member.name}'."
            ) from exc
    archive.extractall(destination, filter="data")
