"""Glicko-2 rating system for multi-player (FFA) matches."""
import math

TAU = 0.5
EPSILON = 0.000001
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOL = 0.06


class Player:
    """A player with Glicko-2 rating parameters."""

    def __init__(self, rating=DEFAULT_RATING, rd=DEFAULT_RD, vol=DEFAULT_VOL):
        self.rating = rating
        self.rd = rd
        self.vol = vol

    # Convert to/from Glicko-2 internal scale
    @property
    def mu(self):
        return (self.rating - 1500) / 173.7178

    @mu.setter
    def mu(self, value):
        self.rating = value * 173.7178 + 1500

    @property
    def phi(self):
        return self.rd / 173.7178

    @phi.setter
    def phi(self, value):
        self.rd = value * 173.7178


def _g(phi):
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _E(mu, mu_j, phi_j):
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _compute_volatility(sigma, phi, delta, v):
    """Iterative algorithm (Illinois method) to compute new volatility."""
    a = math.log(sigma * sigma)
    phi2 = phi * phi
    d2 = delta * delta

    def f(x):
        ex = math.exp(x)
        num = ex * (d2 - phi2 - v - ex)
        denom = 2.0 * (phi2 + v + ex) ** 2
        return num / denom - (x - a) / (TAU * TAU)

    A = a
    if d2 > phi2 + v:
        B = math.log(d2 - phi2 - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU

    fA = f(A)
    fB = f(B)

    for _ in range(100):
        if abs(B - A) < EPSILON:
            break
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2.0
        B, fB = C, fC

    return math.exp(B / 2.0)


def rate_1v1(player, opponents, results):
    """Update a single player against a list of opponents with results (1/0.5/0)."""
    if not opponents:
        # No opponents: just increase RD for rating period
        phi_star = math.sqrt(player.phi ** 2 + player.vol ** 2)
        player.phi = phi_star
        return

    mu = player.mu
    phi = player.phi

    # Step 3: compute v (estimated variance)
    v_inv = 0.0
    for opp, s in zip(opponents, results):
        g_phi = _g(opp.phi)
        e = _E(mu, opp.mu, opp.phi)
        v_inv += g_phi * g_phi * e * (1.0 - e)
    v = 1.0 / v_inv

    # Step 4: compute delta
    delta_sum = 0.0
    for opp, s in zip(opponents, results):
        g_phi = _g(opp.phi)
        e = _E(mu, opp.mu, opp.phi)
        delta_sum += g_phi * (s - e)
    delta = v * delta_sum

    # Step 5: new volatility
    new_vol = _compute_volatility(player.vol, phi, delta, v)

    # Step 6: update phi to phi*
    phi_star = math.sqrt(phi ** 2 + new_vol ** 2)

    # Step 7: update phi and mu
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star ** 2) + 1.0 / v)
    new_mu = mu + new_phi ** 2 * delta_sum

    player.vol = new_vol
    player.phi = new_phi
    player.mu = new_mu


def update_ratings(players):
    """Update ratings for a multi-player FFA match.

    Args:
        players: list of dicts with {rating, rd, vol, score}

    Returns:
        list of dicts with updated {rating, rd, vol}
    """
    n = len(players)
    player_objs = [Player(p["rating"], p["rd"], p["vol"]) for p in players]

    # Decompose FFA into pairwise results for each player
    results = []
    for i in range(n):
        opps = []
        scores = []
        for j in range(n):
            if i == j:
                continue
            opps.append(Player(players[j]["rating"], players[j]["rd"], players[j]["vol"]))
            if players[i]["score"] > players[j]["score"]:
                scores.append(1.0)
            elif players[i]["score"] == players[j]["score"]:
                scores.append(0.5)
            else:
                scores.append(0.0)
        results.append((opps, scores))

    # Apply Glicko-2 update to each player
    for i in range(n):
        rate_1v1(player_objs[i], results[i][0], results[i][1])

    return [{"rating": p.rating, "rd": p.rd, "vol": p.vol} for p in player_objs]
