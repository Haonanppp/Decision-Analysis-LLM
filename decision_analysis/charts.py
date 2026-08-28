"""Static SVG charts for the report - stdlib only, no plotting library.

Palette and mark rules follow the reference data-viz instance: categorical
hues assigned in fixed slot order (never cycled), an ordinal blue ramp for
rank distributions, thin marks with 2px surface gaps between stacked fills,
text in ink tokens (never the series color). Charts commit to light mode and
paint their surface explicitly, since they ship as standalone SVG files.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .analysis import AnalysisResult, FlipPoint, MonteCarloResult
from .casefile import CaseFile

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
CATEGORICAL = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]
# Ordinal blue ramp (dark -> light), steps 700..250: rank 1 darkest.
ORDINAL_BLUE = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#86b6ef"]

FONT = 'font-family="system-ui, -apple-system, \'Segoe UI\', sans-serif"'

WIDTH = 680
LABEL_W = 175
RIGHT_W = 130
BAR_H = 20
ROW_GAP = 34
SEG_GAP = 2.0


def _clip(name: str, limit: int = 25) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _text(x: float, y: float, s: str, size: int = 11, fill: str = INK,
          anchor: str = "start", weight: str = "normal",
          title: str | None = None) -> str:
    """SVG text; when `title` is given (the untruncated original), a native
    <title> child makes the full string appear as a hover tooltip."""
    tooltip = f"<title>{escape(title)}</title>" if title else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}" {FONT}>'
        f"{tooltip}{escape(s)}</text>"
    )


def _label(x: float, y: float, full: str, limit: int = 25, **kw) -> str:
    """Clipped label that carries the full text as a hover tooltip."""
    clipped = _clip(full, limit)
    return _text(x, y, clipped, title=full if clipped != full else None, **kw)


def _svg(height: float, body: list[str]) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
        f'height="{height:.0f}" viewBox="0 0 {WIDTH} {height:.0f}">',
        f'<rect width="{WIDTH}" height="{height:.0f}" fill="{SURFACE}" rx="8"/>',
        *body,
        "</svg>",
    ]
    return "\n".join(parts)


def _legend_row(y: float, entries: list[tuple[str, str]]) -> tuple[list[str], float]:
    """Legend rows of (label, color) pairs with 10px swatches, wrapping when
    the entries would overflow the chart width. Returns (body, next_free_y)."""
    body: list[str] = []
    x, row_y = 16.0, y
    for label, color in entries:
        text = _clip(label, 16)
        entry_w = 14 + len(text) * 6.4 + 22
        if x + entry_w > WIDTH - 8 and x > 16.0:
            x, row_y = 16.0, row_y + 18
        body.append(
            f'<rect x="{x:.1f}" y="{row_y - 9:.1f}" width="10" height="10" rx="2" fill="{color}"/>'
        )
        body.append(_label(x + 14, row_y, label, limit=16, size=11,
                           fill=INK_SECONDARY))
        x += entry_w
    return body, row_y + 18


def stacked_contribution_chart(case: CaseFile, result: AnalysisResult) -> str:
    """Weighted contribution of each criterion to each alternative's utility."""
    alts = [a for a in case.alternatives if a.id in result.ranking]
    alts.sort(key=lambda a: result.ranking.index(a.id))
    plot_w = WIDTH - LABEL_W - RIGHT_W
    body = [_text(16, 24, "Weighted utility by criterion", size=13, weight="600")]
    legend, top = _legend_row(
        46, [(c.name, CATEGORICAL[i % len(CATEGORICAL)]) for i, c in enumerate(case.criteria)]
    )
    body += legend
    for row, alt in enumerate(alts):
        y = top + row * ROW_GAP
        body.append(_label(LABEL_W - 10, y + BAR_H - 6, alt.name, fill=INK, anchor="end"))
        x = float(LABEL_W)
        for i, crit in enumerate(case.criteria):
            seg = crit.weight * result.normalized[alt.id][crit.id] * plot_w
            if seg > SEG_GAP:
                body.append(
                    f'<rect x="{x:.1f}" y="{y}" width="{seg - SEG_GAP:.1f}" '
                    f'height="{BAR_H}" fill="{CATEGORICAL[i % len(CATEGORICAL)]}"/>'
                )
            x += seg
        body.append(_text(x + 6, y + BAR_H - 6, f"{result.utilities[alt.id]:.3f}", fill=INK))
    height = top + len(alts) * ROW_GAP + 12
    return _svg(height, body)


def rank_probability_chart(case: CaseFile, mc: MonteCarloResult) -> str:
    """Per-alternative distribution over final ranks (ordinal blue ramp)."""
    alt_by_id = {a.id: a for a in case.alternatives}
    order = sorted(mc.p_best, key=mc.p_best.get, reverse=True)
    n_ranks = len(order)
    colors = ORDINAL_BLUE[:n_ranks] if n_ranks <= len(ORDINAL_BLUE) else (
        ORDINAL_BLUE + [ORDINAL_BLUE[-1]] * (n_ranks - len(ORDINAL_BLUE))
    )
    plot_w = WIDTH - LABEL_W - RIGHT_W
    body = [_text(16, 24, "Probability of each final rank", size=13, weight="600")]
    legend, top = _legend_row(46, [(f"Rank {k + 1}", colors[k]) for k in range(n_ranks)])
    body += legend
    for row, aid in enumerate(order):
        y = top + row * ROW_GAP
        body.append(_label(LABEL_W - 10, y + BAR_H - 6, alt_by_id[aid].name, anchor="end"))
        x = float(LABEL_W)
        for k in range(n_ranks):
            seg = mc.rank_probs[aid][k] * plot_w
            if seg > SEG_GAP:
                body.append(
                    f'<rect x="{x:.1f}" y="{y}" width="{seg - SEG_GAP:.1f}" '
                    f'height="{BAR_H}" fill="{colors[k]}"/>'
                )
            x += seg
        body.append(
            _text(LABEL_W + plot_w + 8, y + BAR_H - 6,
                  f"P(best) {mc.p_best[aid] * 100:.0f}%", fill=INK)
        )
    height = top + n_ranks * ROW_GAP + 12
    return _svg(height, body)


def utility_distribution_chart(case: CaseFile, mc) -> str:
    """The honest picture behind P(best): each alternative's utility
    distribution as nested interval bars (p5-p95 light, p25-p75 solid,
    median tick) on a shared 0..1 axis - overlap is visible at a glance."""
    alt_by_id = {a.id: a for a in case.alternatives}
    order = sorted(mc.p_best, key=mc.p_best.get, reverse=True)
    plot_w = WIDTH - LABEL_W - RIGHT_W
    top = 44
    body = [_text(16, 24, "Utility distributions (overlap = genuine contest)",
                  size=13, weight="600")]
    axis_y = top + len(order) * ROW_GAP + 4
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        x = LABEL_W + tick * plot_w
        body.append(
            f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{axis_y}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        body.append(_text(x, axis_y + 14, f"{tick:g}", size=10, fill=MUTED,
                          anchor="middle"))
    for row, aid in enumerate(order):
        y = top + row * ROW_GAP
        q = mc.utility_quantiles[aid]
        body.append(_label(LABEL_W - 10, y + BAR_H - 6, alt_by_id[aid].name, anchor="end"))
        x5 = LABEL_W + q["p5"] * plot_w
        x95 = LABEL_W + q["p95"] * plot_w
        x25 = LABEL_W + q["p25"] * plot_w
        x75 = LABEL_W + q["p75"] * plot_w
        x50 = LABEL_W + q["p50"] * plot_w
        body.append(
            f'<rect x="{x5:.1f}" y="{y + 6}" width="{max(x95 - x5, 1):.1f}" '
            f'height="{BAR_H - 12}" fill="#9ec5f4" rx="3"/>'
        )
        body.append(
            f'<rect x="{x25:.1f}" y="{y + 3}" width="{max(x75 - x25, 1):.1f}" '
            f'height="{BAR_H - 6}" fill="{CATEGORICAL[0]}" rx="3"/>'
        )
        body.append(
            f'<line x1="{x50:.1f}" y1="{y}" x2="{x50:.1f}" y2="{y + BAR_H}" '
            f'stroke="{INK}" stroke-width="2"/>'
        )
        body.append(
            _text(LABEL_W + plot_w + 8, y + BAR_H - 6,
                  f"P(best) {mc.p_best[aid] * 100:.0f}%", fill=INK_SECONDARY)
        )
    return _svg(axis_y + 28, body)


def voi_chart(case: CaseFile, voi) -> str:
    """Switch probability per uncertain cell - which research could
    actually change the decision. Worth-resolving cells in full color."""
    alt_by_id = {a.id: a for a in case.alternatives}
    crit_by_id = {c.id: c for c in case.criteria}
    cells = voi.cells
    plot_w = WIDTH - LABEL_W - RIGHT_W
    top = 44
    body = [_text(16, 24, "P(decision changes if this uncertainty is resolved)",
                  size=13, weight="600")]
    axis_y = top + len(cells) * ROW_GAP + 4
    # 0..50% axis (switch probs are rarely higher; keeps small bars legible)
    max_axis = 0.5
    for tick in (0.0, 0.1, 0.25, 0.5):
        x = LABEL_W + tick / max_axis * plot_w
        body.append(
            f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{axis_y}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        body.append(_text(x, axis_y + 14, f"{tick * 100:.0f}%", size=10,
                          fill=MUTED, anchor="middle"))
    threshold_x = LABEL_W + 0.05 / max_axis * plot_w
    body.append(
        f'<line x1="{threshold_x:.1f}" y1="{top - 6}" x2="{threshold_x:.1f}" '
        f'y2="{axis_y}" stroke="{BASELINE}" stroke-width="1" '
        f'stroke-dasharray="3,3"/>'
    )
    for row, c in enumerate(cells):
        y = top + row * ROW_GAP
        full_label = f"{crit_by_id[c.crit_id].name} / {alt_by_id[c.alt_id].name}"
        body.append(_label(LABEL_W - 10, y + BAR_H - 6, full_label, anchor="end"))
        width = min(c.switch_prob, max_axis) / max_axis * plot_w
        color = CATEGORICAL[0] if c.worth_resolving() else "#9ec5f4"
        if width > 0.5:
            body.append(
                f'<rect x="{LABEL_W}" y="{y + 2}" width="{width:.1f}" '
                f'height="{BAR_H - 4}" fill="{color}" rx="3"/>'
            )
        body.append(
            _text(LABEL_W + width + 8, y + BAR_H - 6,
                  f"{c.switch_prob * 100:.0f}%", fill=INK)
        )
    return _svg(axis_y + 28, body)


def risk_profile_chart(case: CaseFile, result) -> str:
    """Risk profiles for a bet: each alternative's CDF of the realized
    payoff - 'what is the chance I end up at or below X'."""
    alt_by_id = {a.id: a for a in case.alternatives}
    samples = result.payoff_samples or {}
    order = result.ranking
    all_values = [v for s in samples.values() for v in (s[0], s[-1]) if s]
    lo, hi = min(all_values), max(all_values)
    span = (hi - lo) or 1.0
    lo -= span * 0.03
    hi += span * 0.03
    span = hi - lo
    plot_w = WIDTH - LABEL_W - RIGHT_W
    plot_h = 150
    body = [_text(16, 24, "Risk profiles: P(payoff ≤ x)", size=13, weight="600")]
    legend, top = _legend_row(
        46,
        [(alt_by_id[aid].name, CATEGORICAL[i % len(CATEGORICAL)])
         for i, aid in enumerate(order)],
    )
    body += legend
    axis_y = top + plot_h
    for frac in (0.0, 0.5, 1.0):
        y = top + (1 - frac) * plot_h
        body.append(
            f'<line x1="{LABEL_W}" y1="{y:.1f}" x2="{LABEL_W + plot_w}" '
            f'y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        body.append(_text(LABEL_W - 8, y + 4, f"{frac * 100:.0f}%", size=10,
                          fill=MUTED, anchor="end"))
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        x = LABEL_W + frac * plot_w
        value = lo + frac * span
        body.append(_text(x, axis_y + 16, f"{value:,.0f}", size=10, fill=MUTED,
                          anchor="middle"))
    for i, aid in enumerate(order):
        s = samples.get(aid, [])
        if not s:
            continue
        n = len(s)
        step = max(n // 150, 1)
        points = []
        for j in range(0, n, step):
            x = LABEL_W + (s[j] - lo) / span * plot_w
            y = top + (1 - (j + 1) / n) * plot_h
            points.append(f"{x:.1f},{y:.1f}")
        points.append(f"{LABEL_W + (s[-1] - lo) / span * plot_w:.1f},{top:.1f}")
        body.append(
            f'<polyline points="{" ".join(points)}" fill="none" '
            f'stroke="{CATEGORICAL[i % len(CATEGORICAL)]}" stroke-width="2"/>'
        )
    return _svg(axis_y + 30, body)


def allocation_split_chart(case: CaseFile, result) -> str:
    """Recommended allocation amounts as horizontal bars (single hue)."""
    alloc = case.frame.get("allocation", {})
    name_by_id = {it["id"]: it["name"] for it in alloc.get("items", [])}
    total = result.total
    ordered = sorted(result.recommended.items(), key=lambda kv: -kv[1])
    plot_w = WIDTH - LABEL_W - RIGHT_W
    top = 40
    body = [_text(16, 24, "Recommended split", size=13, weight="600")]
    for row, (iid, amount) in enumerate(ordered):
        y = top + row * ROW_GAP
        body.append(
            _label(LABEL_W - 10, y + BAR_H - 6, name_by_id.get(iid, iid), anchor="end")
        )
        width = amount / total * plot_w
        if width > 0.5:
            body.append(
                f'<rect x="{LABEL_W}" y="{y}" width="{width:.1f}" '
                f'height="{BAR_H}" rx="4" fill="{CATEGORICAL[0]}"/>'
            )
        body.append(
            _text(LABEL_W + width + 8, y + BAR_H - 6,
                  f"{amount:,.0f}  ({amount / total:.0%})", fill=INK)
        )
    height = top + len(ordered) * ROW_GAP + 12
    return _svg(height, body)


def allocation_robustness_chart(case: CaseFile, result, alphas) -> str:
    """Grouped bars: each item's amount at every diminishing-returns alpha
    (ordinal blue ramp, darkest = strongest diminishing returns)."""
    alloc = case.frame.get("allocation", {})
    name_by_id = {it["id"]: it["name"] for it in alloc.get("items", [])}
    total = result.total
    ordered = [iid for iid, _ in sorted(result.recommended.items(), key=lambda kv: -kv[1])]
    colors = ORDINAL_BLUE[: len(alphas)]
    bar_h = 8
    group_h = len(alphas) * (bar_h + 2) + 12
    plot_w = WIDTH - LABEL_W - RIGHT_W
    body = [_text(16, 24, "Split under different diminishing-returns strengths",
                  size=13, weight="600")]
    legend, top = _legend_row(
        46, [(f"alpha {a}", colors[k]) for k, a in enumerate(alphas)]
    )
    body += legend
    for row, iid in enumerate(ordered):
        y = top + row * group_h
        body.append(
            _label(LABEL_W - 10, y + group_h / 2, name_by_id.get(iid, iid), anchor="end")
        )
        for k, a in enumerate(alphas):
            amount = result.splits[a].get(iid, 0.0)
            width = amount / total * plot_w
            by = y + k * (bar_h + 2)
            if width > 0.5:
                body.append(
                    f'<rect x="{LABEL_W}" y="{by:.1f}" width="{width:.1f}" '
                    f'height="{bar_h}" fill="{colors[k]}"/>'
                )
            body.append(
                _text(LABEL_W + width + 6, by + bar_h - 1, f"{amount:,.0f}",
                      size=9, fill=INK_SECONDARY)
            )
    height = top + len(ordered) * group_h + 12
    return _svg(height, body)


def flip_threshold_chart(case: CaseFile, result: AnalysisResult) -> str:
    """Nearest weight-flip threshold per criterion (tornado-style)."""
    alt_by_id = {a.id: a for a in case.alternatives}
    crit_by_id = {c.id: c for c in case.criteria}
    nearest: dict[str, FlipPoint] = {}
    for f in result.flip_points:  # already sorted by distance
        nearest.setdefault(f.criterion_id, f)
    flips = list(nearest.values())
    plot_w = WIDTH - LABEL_W - RIGHT_W
    top = 44
    body = [_text(16, 24, "Weight change needed to flip the winner", size=13, weight="600")]
    axis_y = top + len(flips) * ROW_GAP + 4
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        x = LABEL_W + tick * plot_w
        body.append(
            f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{axis_y}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        body.append(_text(x, axis_y + 14, f"{tick * 100:.0f}%", size=10, fill=MUTED, anchor="middle"))
    for row, f in enumerate(flips):
        y = top + row * ROW_GAP
        crit = crit_by_id[f.criterion_id]
        body.append(_label(LABEL_W - 10, y + BAR_H - 6, crit.name, anchor="end"))
        x_cur = LABEL_W + f.current_weight * plot_w
        x_flip = LABEL_W + f.flip_weight * plot_w
        x0, x1 = min(x_cur, x_flip), max(x_cur, x_flip)
        body.append(
            f'<rect x="{x0:.1f}" y="{y + 4}" width="{max(x1 - x0, 1.5):.1f}" '
            f'height="{BAR_H - 8}" fill="{CATEGORICAL[0]}" rx="2"/>'
        )
        body.append(
            f'<line x1="{x_cur:.1f}" y1="{y}" x2="{x_cur:.1f}" y2="{y + BAR_H}" '
            f'stroke="{INK}" stroke-width="2"/>'
        )
        winner_full = alt_by_id[f.new_winner_id].name
        pct = f"{f.flip_weight * 100:.0f}%"
        body.append(
            _text(LABEL_W + plot_w + 8, y + BAR_H - 6,
                  f"→ {_clip(winner_full, 14)} @ {pct}",
                  fill=INK_SECONDARY,
                  title=f"→ {winner_full} @ {pct}"
                  if len(winner_full) > 14 else None)
        )
    if not flips:
        body.append(_text(LABEL_W, top + 16, "No single-weight change flips the winner.",
                          fill=INK_SECONDARY))
    height = axis_y + 28
    return _svg(height, body)
