"""TrueSkill rating system for multi-player (FFA) matches.
Keeps the same interface as the old Glicko-2 module for compatibility."""
import math
from itertools import combinations

# TrueSkill parameters
MU = 1500.0        # Default mean (maps to "elo" column)
SIGMA = 250.0      # Default uncertainty (maps to "rd" column)
BETA = 125.0       # Performance variance (half of SIGMA)
TAU = 5.0          # Dynamics factor (additive uncertainty per game)
DRAW_PROB = 0.0    # No draws in ants


def _v(t, eps=0.0):
    """V function (truncated Gaussian correction for win)."""
    from math import erf, sqrt, exp, pi
    denom = 0.5 * (1 + erf((t - eps) / sqrt(2)))
    if denom < 1e-10:
        return -t + eps
    return (1.0 / sqrt(2 * pi)) * exp(-0.5 * (t - eps) ** 2) / denom


def _w(t, eps=0.0):
    """W function (multiplicative correction)."""
    vt = _v(t, eps)
    return vt * (vt + t - eps)


def update_ratings(players):
    """Update ratings for a multi-player FFA match using TrueSkill.

    Args:
        players: list of dicts with {rating, rd, vol, score}
                 rating = mu, rd = sigma, vol = ignored (kept for compat)

    Returns:
        list of dicts with updated {rating, rd, vol}
    """
    n = len(players)
    if n < 2:
        return [{"rating": p["rating"], "rd": p["rd"], "vol": p.get("vol", 0.06)} for p in players]

    # Build ranking from scores (higher score = better rank = lower rank number)
    scores = [p["score"] for p in players]
    # Rank: 0 = best. Ties get same rank.
    sorted_indices = sorted(range(n), key=lambda i: -scores[i])
    ranks = [0] * n
    for pos, idx in enumerate(sorted_indices):
        if pos > 0 and scores[idx] == scores[sorted_indices[pos - 1]]:
            ranks[idx] = ranks[sorted_indices[pos - 1]]
        else:
            ranks[idx] = pos

    # Current parameters with dynamics factor
    mus = [p["rating"] for p in players]
    sigmas = [math.sqrt((p["rd"]) ** 2 + TAU ** 2) for p in players]

    # Compute updates via pairwise comparisons
    mu_updates = [0.0] * n
    sigma_sq_updates = [0.0] * n

    for i, j in combinations(range(n), 2):
        # Determine winner
        if ranks[i] < ranks[j]:
            winner, loser = i, j
        elif ranks[j] < ranks[i]:
            winner, loser = j, i
        else:
            # Draw - no update for this pair
            continue

        mu_w, mu_l = mus[winner], mus[loser]
        sig_w, sig_l = sigmas[winner], sigmas[loser]

        c = math.sqrt(sig_w ** 2 + sig_l ** 2 + 2 * BETA ** 2)
        t = (mu_w - mu_l) / c

        vt = _v(t)
        wt = _w(t)

        # Scale by number of opponents (so larger games don't over-update)
        scale = 1.0 / (n - 1)

        # Winner update
        mu_updates[winner] += scale * (sig_w ** 2 / c) * vt
        sigma_sq_updates[winner] += scale * (sig_w ** 2 / c ** 2) * wt

        # Loser update
        mu_updates[loser] -= scale * (sig_l ** 2 / c) * vt
        sigma_sq_updates[loser] += scale * (sig_l ** 2 / c ** 2) * wt

    # Apply updates
    results = []
    for i in range(n):
        new_mu = mus[i] + mu_updates[i]
        new_sigma_sq = sigmas[i] ** 2 * (1.0 - sigma_sq_updates[i])
        new_sigma = math.sqrt(max(new_sigma_sq, 10.0))  # floor to prevent collapse

        results.append({
            "rating": new_mu,
            "rd": new_sigma,
            "vol": players[i].get("vol", 0.06),  # kept for DB compat, unused
        })

    return results
