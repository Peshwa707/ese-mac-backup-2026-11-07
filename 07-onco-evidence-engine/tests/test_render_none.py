"""Regression: a missing structured-case field must not render as 'None'."""

from __future__ import annotations

from core.config.vertical import load_vertical
from core.models import Report
from core.render import render


def test_none_field_not_rendered_as_string():
    report = Report(
        id="r1",
        vertical="oncology",
        structured_case={"diagnosis": "lung adenocarcinoma", "stage": None, "ecog": None,
                         "biomarkers": []},
        disclaimer="x",
    )
    html = render.render_html(report, load_vertical("oncology"))
    # The present field renders; absent (None/empty) fields do not emit "None".
    assert "lung adenocarcinoma" in html
    assert ">None<" not in html
    assert "None" not in html.split("Structured case")[1].split("</section>")[0]


def test_list_field_rendered_as_join():
    report = Report(
        id="r2",
        vertical="oncology",
        structured_case={"biomarkers": ["EGFR exon 19", "PD-L1 80%"]},
        disclaimer="x",
    )
    html = render.render_html(report, load_vertical("oncology"))
    assert "EGFR exon 19, PD-L1 80%" in html
    assert "[" not in html.split("Structured case")[1].split("</section>")[0]
