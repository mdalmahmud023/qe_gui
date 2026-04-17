import logging
import numpy as np
from scipy.optimize import curve_fit
from .models import EosFitResult

logger = logging.getLogger(__name__)

# Constants
EV_TO_JOULES = 1.602176634e-19
ANGSTROM3_TO_M3 = 1e-30
EV_ANG3_TO_GPA = EV_TO_JOULES / ANGSTROM3_TO_M3 / 1e9


def birch_murnaghan(V, E0, V0, B0, B0_prime):
    """
    Birch-Murnaghan equation of state.
    V: Array of volumes (Angstrom^3)
    E0: Equilibrium energy (eV)
    V0: Equilibrium volume (Angstrom^3)
    B0: Bulk modulus (eV/Angstrom^3)
    B0_prime: Derivative of bulk modulus w.r.t pressure
    """
    eta = (V0 / V) ** (2.0 / 3.0)
    E = E0 + (9.0 * V0 * B0 / 16.0) * (
        (eta - 1.0) ** 3 * B0_prime + (eta - 1.0) ** 2 * (6.0 - 4.0 * eta)
    )
    return E


def fit_eos(volumes: list[float], energies: list[float], model_name: str = "birch_murnaghan") -> EosFitResult:
    """
    Fit an Equation of State model to Volume-Energy data.
    Returns an EosFitResult containing E0, V0, B0 (in GPa), and warnings/errors.
    """
    warnings = []
    errors = []

    if len(volumes) != len(energies):
        errors.append("Mismatch in number of volumes and energies.")
        return EosFitResult(available=False, success=False, model=model_name, errors=errors)

    if len(volumes) < 4:
        errors.append(f"Insufficient points for EOS fitting. Need at least 4, got {len(volumes)}.")
        return EosFitResult(available=False, success=False, model=model_name, errors=errors)

    if model_name != "birch_murnaghan":
        errors.append(f"Unsupported EOS model: {model_name}")
        return EosFitResult(available=False, success=False, model=model_name, errors=errors)

    V = np.array(volumes)
    E = np.array(energies)

    # Initial guesses
    min_idx = np.argmin(E)
    E0_guess = E[min_idx]
    V0_guess = V[min_idx]
    
    # Check if the minimum is at the edge of the sampled range
    if min_idx == 0 or min_idx == len(E) - 1:
        warnings.append("Energy minimum is at the edge of the sampled volume range. The fit may be unreliable or underdetermined. Consider expanding the scale range.")

    # Parabolic fit for B0 guess
    # E(V) = a * V^2 + b * V + c
    try:
        p_guess = np.polyfit(V, E, 2)
        a, b, c = p_guess
        # d2E/dV2 = 2a -> B = V * d2E/dV2 = V * 2a
        B0_guess = V0_guess * 2 * a
    except Exception:
        B0_guess = 1.0  # Fallback

    # B0_guess can be negative if the parabola is inverted (e.g., terrible data)
    if B0_guess < 0:
        B0_guess = 0.1
        warnings.append("Initial parabolic fit yielded negative bulk modulus; data might be noisy or poorly shaped.")

    B0_prime_guess = 4.0  # Typical value for most solids

    initial_guess = [E0_guess, V0_guess, B0_guess, B0_prime_guess]

    try:
        popt, pcov = curve_fit(
            birch_murnaghan, 
            V, 
            E, 
            p0=initial_guess, 
            maxfev=10000,
            bounds=(
                [-np.inf, 0, 0, 0], 
                [np.inf, np.inf, np.inf, 20]
            )
        )
    except RuntimeError as e:
        errors.append(f"Curve fitting failed to converge: {e}")
        return EosFitResult(available=False, success=False, model=model_name, warnings=warnings, errors=errors)
    except Exception as e:
        errors.append(f"Unexpected error during curve fitting: {e}")
        return EosFitResult(available=False, success=False, model=model_name, warnings=warnings, errors=errors)

    E0_fit, V0_fit, B0_fit, B0_prime_fit = popt

    # Convert B0 from eV/Ang^3 to GPa
    B0_gpa = B0_fit * EV_ANG3_TO_GPA

    # Final sanity checks on the fitted values
    if V0_fit < V.min() * 0.5 or V0_fit > V.max() * 2.0:
        warnings.append(f"Fitted equilibrium volume ({V0_fit:.2f}) is far outside the sampled range ({V.min():.2f} - {V.max():.2f}).")
        
    return EosFitResult(
        available=True,
        success=True,
        model=model_name,
        E0=E0_fit,
        V0=V0_fit,
        B0=B0_gpa,
        B0_prime=B0_prime_fit,
        warnings=warnings,
        errors=errors
    )
