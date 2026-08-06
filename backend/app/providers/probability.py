"""Probability provider abstraction (spec §3 Tier-2).

Converts bookmaker prices into per-team expected goals (lambda) so the
projection engine can use market consensus instead of — or blended with — its
own strength model.

Method — **joint fit of two parameters under a Dixon–Coles score model**:

    minimise over (lam_home, lam_away):
          w1 * MSE( 1X2 probabilities )
        + w2 * MSE( over/under probabilities )
        + w3 * MSE( Asian handicap probabilities )
    -> team expected goals = lam_home, lam_away

Every market is priced off ONE score matrix, so the answer is consistent with
all three at once. The previous implementation solved the markets in sequence
(totals -> total goals, then 1X2 -> supremacy), which let the 1X2 residual grow
unchecked because the total was already frozen, and ignored the handicap
entirely — the sharpest of the three markets.

Score matrix (Dixon & Coles 1997, §4):

    P(i, j) = tau(i, j) * Poisson(i; lam_home) * Poisson(j; lam_away)

    tau(0,0) = 1 - lam_home*lam_away*rho     tau(0,1) = 1 + lam_home*rho
    tau(1,0) = 1 + lam_away*rho              tau(1,1) = 1 - rho
    tau      = 1 everywhere else

With rho < 0 this lifts 0-0 and 1-1 and trims 1-0 and 0-1 — the well-documented
failure of the independent double-Poisson, which under-prices low-scoring draws.
`rho` is NOT fitted here: a single match's prices cannot identify it (it is
estimated from a season of results in the paper), so it is a configured
constant — hence "two parameters".

tau is *marginal-preserving*: because P(1; mu) == mu * P(0; mu) exactly, the
corrections on the 0-1 and 0-0 cells cancel when a row is summed. So each team's
own goal distribution stays Poisson(lambda) and only the dependence between the
two changes. Two consequences worth knowing:
  * `team expected goals = lam_home, lam_away` is exact, not an approximation;
  * xpoints.py may keep pricing a clean sheet as exp(-lambda_conceded) — the DC
    grid would give it the same number, so nothing downstream has to change.

Prices are de-vigged **per bookmaker** — implied probabilities are normalised
across that book's own outcomes — and only then combined across books, so one
book's margin never leaks into another's price. Handicap and integer totals
refund the stake on a push, so their de-vigged price is a probability
*conditional on no push* — the model side is conditioned the same way before the
residual is taken.

Books are combined by **median**, not mean. The mean lets a single stale or
mispriced book move the consensus by 1/n; the median ignores it unless it is
near the middle. For 1X2 the median is taken per outcome and then renormalised,
because three independent medians do not sum to 1.

What we deliberately do NOT do: weight books by liquidity or historical
accuracy. The Odds API publishes neither turnover nor settled results, so any
such weight would be a number we invented. Equal-weight median is the strongest
consensus available from the data we actually have.

If no licensed key is configured we return nothing and the engine falls back to
its internal model, clearly labelled `model_estimate` — we never present a model
number as a real market price.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import httpx

from app.config import settings

ODDS_BASE = "https://api.the-odds-api.com/v4"
DEFAULT_TOTAL_GOALS = 2.7      # league-average total — start point for the search
MAX_GOALS = 10                 # truncation for the score grid
# A line must be quoted by at least this share of the books quoting the most
# popular line to be used, so one book's outlier line cannot drag the fit.
MIN_LINE_SUPPORT = 0.25


def _median(xs: list[float]) -> float:
    """Trung vị — chống được một nhà cái treo giá lệch, khác với trung bình."""
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


@dataclass
class MarketMatch:
    home_name: str
    away_name: str
    commence_time: str
    lam_home: float
    lam_away: float
    p_home: float
    p_draw: float
    p_away: float
    total_goals: float
    n_bookmakers: int
    source: str = "odds_api"
    is_market: bool = True
    # --- fit diagnostics (not persisted; surfaced in the sync log) ---
    fit_error: float = 0.0         # weighted mean squared probability error
    dc_rho: float = 0.0            # Dixon–Coles correlation actually applied
    markets_used: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- maths -----
def _pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _score_grid(lam_h: float, lam_a: float, rho: float) -> list[list[float]]:
    """Dixon–Coles corrected score matrix, renormalised to sum to 1.

    Renormalisation absorbs both the grid truncation at MAX_GOALS and the mass
    that tau moves around, so the matrix stays a proper distribution.
    """
    hp = [_pmf(i, lam_h) for i in range(MAX_GOALS + 1)]
    ap = [_pmf(j, lam_a) for j in range(MAX_GOALS + 1)]
    g = [[hp[i] * ap[j] for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]

    if rho:
        # clamp at 0: tau can go negative for absurd lambda/rho combinations
        g[0][0] *= max(0.0, 1.0 - lam_h * lam_a * rho)
        g[0][1] *= max(0.0, 1.0 + lam_h * rho)
        g[1][0] *= max(0.0, 1.0 + lam_a * rho)
        g[1][1] *= max(0.0, 1.0 - rho)

    s = sum(sum(row) for row in g)
    if s <= 0:
        return g
    return [[p / s for p in row] for row in g]


def _outcome_probs(lam_h: float, lam_a: float, rho: float = 0.0) -> tuple[float, float, float]:
    """P(home win), P(draw), P(away win). rho=0 gives the plain double-Poisson."""
    g = _score_grid(lam_h, lam_a, rho)
    ph = pd = pa = 0.0
    for i, row in enumerate(g):
        for j, p in enumerate(row):
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
    return ph, pd, pa


def _distributions(g: list[list[float]]) -> tuple[list[float], list[float]]:
    """(total goals distribution, goal margin distribution) from a score grid.

    `total[t]` is P(home + away == t); `margin[d + MAX_GOALS]` is P(home - away == d).
    Both totals and handicaps are then just tail sums of one of these.
    """
    total = [0.0] * (2 * MAX_GOALS + 1)
    margin = [0.0] * (2 * MAX_GOALS + 1)
    for i, row in enumerate(g):
        for j, p in enumerate(row):
            total[i + j] += p
            margin[i - j + MAX_GOALS] += p
    return total, margin


def _split(dist: list[float], first_value: int, line: float) -> tuple[float, float, float]:
    """(P(X > line), P(X == line), P(X < line)) for a distribution over integers.

    `first_value` is the integer that `dist[0]` refers to. Quarter lines (.25 /
    .75) split the stake across the two neighbouring lines, which is exactly how
    an Asian book settles them, so we average the two outcomes.
    """
    quarters = round(line * 4)
    if quarters % 2:
        lo = _split(dist, first_value, (quarters - 1) / 4)
        hi = _split(dist, first_value, (quarters + 1) / 4)
        return tuple((a + b) / 2 for a, b in zip(lo, hi))  # type: ignore[return-value]

    above = push = below = 0.0
    for k, p in enumerate(dist):
        value = k + first_value
        if value > line:
            above += p
        elif value < line:
            below += p
        else:
            push += p
    return above, push, below


def _no_push_prob(above: float, push: float, below: float) -> float:
    """P(above | not a push) — how a de-vigged Asian price should be read."""
    live = above + below
    return above / live if live > 1e-12 else 0.5


# ------------------------------------------------------------ joint fit -----
def _objective(
    lam_h: float,
    lam_a: float,
    rho: float,
    p_1x2: tuple[float, float, float] | None,
    totals: list[tuple[float, float]],
    handicaps: list[tuple[float, float]],
    w_1x2: float,
    w_totals: float,
    w_handicap: float,
) -> float:
    """Weighted mean squared error across the three markets.

    Each market contributes the *mean* squared error over its own lines, so a
    market quoted on four lines does not silently outweigh 1X2; w1/w2/w3 are the
    only thing that sets the balance. The total is divided by the active weight
    so fixtures missing a market stay comparable.
    """
    g = _score_grid(lam_h, lam_a, rho)
    total_dist, margin_dist = _distributions(g)

    err = 0.0
    active = 0.0

    if p_1x2 is not None and w_1x2 > 0:
        ph = pd = pa = 0.0
        for i, row in enumerate(g):
            for j, p in enumerate(row):
                if i > j:
                    ph += p
                elif i == j:
                    pd += p
                else:
                    pa += p
        model = (ph, pd, pa)
        err += w_1x2 * sum((m - q) ** 2 for m, q in zip(model, p_1x2)) / 3.0
        active += w_1x2

    if totals and w_totals > 0:
        block = 0.0
        for line, p_over in totals:
            block += (_no_push_prob(*_split(total_dist, 0, line)) - p_over) ** 2
        err += w_totals * block / len(totals)
        active += w_totals

    if handicaps and w_handicap > 0:
        block = 0.0
        for point, p_cover in handicaps:
            # home covers when (home - away) > -point
            block += (_no_push_prob(*_split(margin_dist, -MAX_GOALS, -point)) - p_cover) ** 2
        err += w_handicap * block / len(handicaps)
        active += w_handicap

    return err / active if active > 0 else err


def fit_lambdas(
    p_1x2: tuple[float, float, float] | None,
    totals: list[tuple[float, float]] | None = None,
    handicaps: list[tuple[float, float]] | None = None,
    *,
    rho: float | None = None,
    w_1x2: float | None = None,
    w_totals: float | None = None,
    w_handicap: float | None = None,
) -> tuple[float, float, float]:
    """Fit (lam_home, lam_away) to all quoted markets at once.

    `totals` are (line, P(over | no push)) and `handicaps` are (home handicap
    point, P(home covers | no push)), both already de-vigged. Returns
    (lam_home, lam_away, fit_error).

    A missing market simply drops out of the objective — no default is
    substituted. Two free 1X2 probabilities already identify two parameters
    exactly, and measurably well: a 1 percentage point error on the draw price
    moves the fitted total by only ~0.18 goals. Nudging the total towards a
    league average, as the old code did whenever totals were absent, throws that
    real information away.

    Searched in (total, supremacy) space — the objective is far better
    conditioned there than in (lam_home, lam_away), because the markets speak
    almost directly in those two terms — then mapped back. A coarse sweep finds
    the basin and a compass search refines it; no SciPy in the dependency set,
    and a hand-rolled pattern search on two smooth parameters is both adequate
    and deterministic.
    """
    totals = totals or []
    handicaps = handicaps or []
    rho = settings.odds_dixon_coles_rho if rho is None else rho
    w_1x2 = settings.odds_weight_1x2 if w_1x2 is None else w_1x2
    w_totals = settings.odds_weight_totals if w_totals is None else w_totals
    w_handicap = settings.odds_weight_handicap if w_handicap is None else w_handicap

    def cost(total: float, sup: float) -> float:
        lam_h = max((total + sup) / 2, 0.02)
        lam_a = max((total - sup) / 2, 0.02)
        return _objective(
            lam_h, lam_a, rho, p_1x2, totals, handicaps, w_1x2, w_totals, w_handicap
        )

    # 1) coarse sweep for the basin
    best = (DEFAULT_TOTAL_GOALS, 0.0)
    best_cost = cost(*best)
    total = 0.8
    while total <= 6.0001:
        sup = -3.0
        while sup <= 3.0001:
            if abs(sup) < total:            # both lambdas must stay positive
                c = cost(total, sup)
                if c < best_cost:
                    best, best_cost = (total, sup), c
            sup += 0.2
        total += 0.2

    # 2) compass search — shrink the step until the answer is stable to 1e-4
    step = 0.2
    while step > 1e-4:
        improved = False
        for d_total, d_sup in ((step, 0.0), (-step, 0.0), (0.0, step), (0.0, -step)):
            cand = (best[0] + d_total, best[1] + d_sup)
            if cand[0] <= 0.05 or abs(cand[1]) >= cand[0]:
                continue
            c = cost(*cand)
            if c < best_cost - 1e-15:
                best, best_cost, improved = cand, c, True
        if not improved:
            step /= 2

    total, sup = best
    return max((total + sup) / 2, 0.05), max((total - sup) / 2, 0.05), best_cost


# ------------------------------------------------------------ name match ----
_ALIASES = {
    "manchester united": "man utd",
    "manchester city": "man city",
    "tottenham hotspur": "spurs",
    "brighton and hove albion": "brighton",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "leeds united": "leeds",
    "wolverhampton wanderers": "wolves",
    "west ham united": "west ham",
    "afc bournemouth": "bournemouth",
    "sheffield united": "sheffield utd",
}


def normalise_team(name: str) -> str:
    n = (name or "").strip().lower()
    n = _ALIASES.get(n, n)
    n = re.sub(r"\b(fc|afc|association football club)\b", "", n)
    n = re.sub(r"[^a-z' ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def match_team_id(odds_name: str, fpl_teams: dict[int, str]) -> int | None:
    """Map a bookmaker team name onto an FPL team id (None if unsure)."""
    target = normalise_team(odds_name)
    if not target:
        return None
    best_id, best_score = None, 0.0
    for tid, fpl_name in fpl_teams.items():
        cand = normalise_team(fpl_name)
        if cand == target:
            return tid
        # token containment: "hull city" vs "hull", "coventry city" vs "coventry"
        a, b = set(target.split()), set(cand.split())
        if not a or not b:
            continue
        score = len(a & b) / len(a | b)
        if a <= b or b <= a:
            score = max(score, 0.9)
        if score > best_score:
            best_id, best_score = tid, score
    return best_id if best_score >= 0.5 else None


# ------------------------------------------------------- market parsing -----
def _consensus_lines(quotes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Median price per quoted line, dropping thinly-quoted outliers.

    Books do not all hang the same line. Combining prices *across* different
    lines would be meaningless, so each line becomes its own observation for the
    fit; lines quoted by far fewer books than the main one are dropped.

    Within a line the median is used rather than the mean — a single book pricing
    Over 2.5 at 1.60 when everyone else is at 1.90 should not move the consensus.
    """
    if not quotes:
        return []
    grouped: dict[float, list[float]] = {}
    for line, p in quotes:
        grouped.setdefault(line, []).append(p)
    top = max(len(v) for v in grouped.values())
    # A lone quote is only trusted when the market is thin enough that every
    # line is a lone quote — otherwise it needs both a second book behind it and
    # a real share of the main line's support.
    floor = 1 if top < 2 else max(2, math.ceil(MIN_LINE_SUPPORT * top))
    return [
        (line, _median(ps))
        for line, ps in sorted(grouped.items())
        if len(ps) >= floor
    ]


# ------------------------------------------------------------- provider -----
class ProbabilityProvider:
    name = "base"

    def get_matches(self) -> list[MarketMatch]:  # pragma: no cover
        raise NotImplementedError


class OddsAPIProvider(ProbabilityProvider):
    """the-odds-api.com — soccer_epl h2h + totals + spreads (Asian handicap).

    Quota note: a request costs (markets x regions) credits, so adding the
    handicap takes a sync from 2 to 3. Set ODDS_INCLUDE_HANDICAP=false to go
    back to 2 — the fit then runs on 1X2 + totals only.
    """

    name = "odds_api"

    def __init__(self, api_key: str, region: str = "uk") -> None:
        self.api_key = api_key
        self.region = region

    def _markets(self) -> str:
        return "h2h,totals,spreads" if settings.odds_include_handicap else "h2h,totals"

    def _fetch(self) -> list[dict]:
        params = {
            "apiKey": self.api_key,
            "regions": self.region,
            "markets": self._markets(),
            "oddsFormat": "decimal",
        }
        r = httpx.get(
            f"{ODDS_BASE}/sports/soccer_epl/odds",
            params=params,
            timeout=settings.http_timeout_seconds,
        )
        r.raise_for_status()
        return r.json()

    def get_matches(self) -> list[MarketMatch]:
        try:
            raw = self._fetch()
        except Exception:
            return []

        out: list[MarketMatch] = []
        for game in raw:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            h2h_probs: list[tuple[float, float, float]] = []
            total_quotes: list[tuple[float, float]] = []   # (line, p_over | no push)
            hcp_quotes: list[tuple[float, float]] = []     # (home point, p_cover | no push)
            books = game.get("bookmakers", []) or []

            for bk in books:
                for m in bk.get("markets", []):
                    key = m.get("key")
                    if key == "h2h":
                        imp = {}
                        for o in m.get("outcomes", []):
                            price = o.get("price") or 0
                            if price > 1:
                                imp[o["name"]] = 1.0 / price
                        s = sum(imp.values())
                        if s > 0 and home in imp and away in imp:
                            h2h_probs.append(
                                (imp[home] / s, imp.get("Draw", 0.0) / s, imp[away] / s)
                            )
                    elif key == "totals":
                        over = under = line = None
                        for o in m.get("outcomes", []):
                            price = o.get("price") or 0
                            if price <= 1:
                                continue
                            if o.get("name") == "Over":
                                over, line = 1.0 / price, o.get("point")
                            elif o.get("name") == "Under":
                                under = 1.0 / price
                        if over and under and line is not None:
                            total_quotes.append((float(line), over / (over + under)))
                    elif key == "spreads":
                        h_imp = a_imp = point = None
                        for o in m.get("outcomes", []):
                            price = o.get("price") or 0
                            if price <= 1:
                                continue
                            if o.get("name") == home:
                                h_imp, point = 1.0 / price, o.get("point")
                            elif o.get("name") == away:
                                a_imp = 1.0 / price
                        if h_imp and a_imp and point is not None:
                            hcp_quotes.append((float(point), h_imp / (h_imp + a_imp)))

            if not h2h_probs:
                continue

            # Trung vị theo từng kết cục, rồi chuẩn hoá lại: ba trung vị độc lập
            # không tự cộng thành 1, nên bỏ bước chuẩn hoá là đưa một bộ xác suất
            # không hợp lệ vào hàm khớp λ.
            m_home = _median([p[0] for p in h2h_probs])
            m_draw = _median([p[1] for p in h2h_probs])
            m_away = _median([p[2] for p in h2h_probs])
            tot = m_home + m_draw + m_away
            if tot <= 0:
                continue
            p_home, p_draw, p_away = m_home / tot, m_draw / tot, m_away / tot

            totals = _consensus_lines(total_quotes)
            handicaps = _consensus_lines(hcp_quotes)
            lam_h, lam_a, err = fit_lambdas(
                (p_home, p_draw, p_away), totals, handicaps
            )

            used = ["1x2"]
            if totals:
                used.append("ou")
            if handicaps:
                used.append("ah")

            out.append(
                MarketMatch(
                    home_name=home,
                    away_name=away,
                    commence_time=game.get("commence_time", ""),
                    lam_home=round(lam_h, 3),
                    lam_away=round(lam_a, 3),
                    p_home=round(p_home, 4),
                    p_draw=round(p_draw, 4),
                    p_away=round(p_away, 4),
                    total_goals=round(lam_h + lam_a, 3),
                    n_bookmakers=len(books),
                    fit_error=round(err, 8),
                    dc_rho=settings.odds_dixon_coles_rho,
                    markets_used=used,
                )
            )
        return out


def get_probability_provider() -> ProbabilityProvider | None:
    """Configured market provider, or None => engine uses its internal model."""
    if settings.odds_api_key:
        return OddsAPIProvider(settings.odds_api_key)
    return None
