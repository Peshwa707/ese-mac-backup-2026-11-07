"""Risk-of-bias traffic-light figure (SVG) from RoB assessments.

Rows = studies, columns = domains (union, ordered) + Overall; each cell colored by
judgment. Tools differ in their domain sets, so a study only fills the columns its
tool defines; other cells are left blank (not green) — absence is not low risk.
"""

from __future__ import annotations

from core.sr.models import RoBAssessment

_COLOR = {
    "low": "#3a8a3a",
    "some concerns": "#d8a93a",
    "moderate": "#d8a93a",
    "high": "#b34a4a",
    "serious": "#b34a4a",
    "critical": "#7a1f1f",
    "no information": "#9aa5b5",
}
_DOMAIN_ORDER = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "S1", "S2", "S3", "S4"]


def _color(judgment: str) -> str:
    return _COLOR.get((judgment or "").strip().lower(), "#d0d0d0")


def traffic_light_svg(assessments: list[RoBAssessment]) -> str:
    cols = [k for k in _DOMAIN_ORDER if any(d.key == k for a in assessments for d in a.domains)]
    headers = cols + ["Overall"]
    x0, y0, cw, rh, r = 150, 50, 40, 20, 8
    width = x0 + len(headers) * cw + 20
    height = y0 + len(assessments) * rh + 30
    out = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"font-family='Helvetica,Arial,sans-serif' font-size='11'>",
        f"<text x='10' y='20' font-weight='bold' font-size='13'>Risk of Bias — traffic-light "
        f"(AI Reviewer-1 draft, n={len(assessments)})</text>",
    ]
    for j, h in enumerate(headers):
        cx = x0 + j * cw + cw // 2
        out.append(f"<text x='{cx}' y='{y0-6}' text-anchor='middle' font-weight='bold'>{h}</text>")
    for i, a in enumerate(assessments):
        cy = y0 + i * rh + rh // 2
        label = (a.pmid or "")[:14]
        out.append(f"<text x='10' y='{cy+4}'>{label}</text>")
        by_key = {d.key: d.judgment for d in a.domains}
        for j, key in enumerate(cols):
            if key in by_key:
                cx = x0 + j * cw + cw // 2
                out.append(f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='{_color(by_key[key])}' "
                           f"stroke='#444' stroke-width='0.5'/>")
        cx = x0 + len(cols) * cw + cw // 2
        out.append(f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='{_color(a.overall)}' "
                   f"stroke='#444' stroke-width='0.5'/>")
    # legend
    ly = height - 12
    for k, (lab, col) in enumerate({"Low": "#3a8a3a", "Some concerns/Moderate": "#d8a93a",
                                    "High/Serious": "#b34a4a", "No info": "#9aa5b5"}.items()):
        lx = 10 + k * 150
        out.append(f"<circle cx='{lx}' cy='{ly-4}' r='6' fill='{col}'/>"
                   f"<text x='{lx+12}' y='{ly}'>{lab}</text>")
    out.append("</svg>")
    return "\n".join(out)
