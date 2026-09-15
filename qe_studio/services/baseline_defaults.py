"""Shared scientific editor defaults; no session or page dependencies."""
from .input_generator import make_default_context, select_cutoffs, VdwMethod
from .qe_control import DEFAULT_OUTDIR, default_qe_prefix
_DEFAULT_CONV_THR = 1e-6
_DEFAULT_MIXING_BETA = 0.7

def _default_starting_magnetization_state(starting_magnetization: dict[str, float]) -> dict[str, dict[str, object]]:
    """Return the saved-form representation for species-wise starting magnetization."""
    return {
        species: {
            "species": species,
            "value": float(value),
            "source": "heuristic_default",
        }
        for species, value in starting_magnetization.items()
    }


def default_qe_parameters(
    resolved_entries: dict | None = None,
    atoms=None,
) -> dict:
    """Return physics-aware defaults for the QE input form. When ``atoms`` are available, uses :func:`~qe_studio.services.input_generator.make_default_context`
    which applies chemistry-aware policies (occupations, smearing, k-grid) based
    on the structure classification.
    """
    if atoms is not None:
        try:
            ctx = make_default_context(atoms, resolved_entries=resolved_entries)
            return {
                "calculation": "scf",
                "prefix": ctx.control.requested_prefix,
                "prefix_source": "auto",
                "outdir": ctx.control.requested_outdir,
                "outdir_source": "auto",
                "ecutwfc": ctx.numerical.ecutwfc,
                "ecutrho": ctx.numerical.ecutrho,
                "occupations": ctx.physical.occupations,
                "smearing": ctx.physical.smearing,
                "degauss": ctx.physical.degauss,
                "nspin": ctx.physical.nspin,
                "spin_source": "auto",
                "starting_magnetization": _default_starting_magnetization_state(ctx.physical.starting_magnetization),
                "noncolin": ctx.physical.noncolin,
                "lspinorb": ctx.physical.lspinorb,
                "dftu_enabled": ctx.hubbard_u.enabled,
                "hubbard_u": ctx.hubbard_u.to_metadata(species_order=ctx.species_order)["species_parameters"],
                "vdw_method": ctx.vdw.normalized_method.value,
                "k_points_grid": ctx.physical.k_points_grid,
                "conv_thr": ctx.numerical.conv_thr,
                "mixing_beta": ctx.numerical.mixing_beta,
            }
        except Exception:
            pass

    ecutwfc = 40.0
    ecutrho = 160.0
    k_grid = [4, 4, 4]

    if resolved_entries:
        try:
            decision = select_cutoffs(resolved_entries)
            ecutwfc = decision.ecutwfc_ry
            ecutrho = decision.ecutrho_ry
        except Exception:
            pass

    return {
        "calculation": "scf",
        "prefix": default_qe_prefix(atoms),
        "prefix_source": "auto",
        "outdir": DEFAULT_OUTDIR,
        "outdir_source": "auto",
        "ecutwfc": ecutwfc,
        "ecutrho": ecutrho,
        "occupations": "smearing",
        "smearing": "gaussian",
        "degauss": 0.01,
        "nspin": 1,
        "spin_source": "auto",
        "starting_magnetization": {},
        "noncolin": False,
        "lspinorb": False,
        "dftu_enabled": False,
        "hubbard_u": {},
        "vdw_method": VdwMethod.NONE.value,
        "k_points_grid": k_grid,
        "conv_thr": _DEFAULT_CONV_THR,
        "mixing_beta": _DEFAULT_MIXING_BETA,
    }


