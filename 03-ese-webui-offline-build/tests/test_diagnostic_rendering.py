"""Regression tests for stage-1 diagnostic result rendering in the WebUI.

Covers the review finding that the doctor diagnostic's structured result
(passed/exit_code/stderr/report) was fetched by the frontend and then
discarded -- only the run/artifact list was refreshed, so an operator could
never see *why* doctor failed from the UI itself. There is no browser/JS
test runner in this offline-only, no-network package, so these tests treat
the served static/index.html and static/app.js as the UI's actual contract
and assert, by inspecting those served assets, that:

  1. index.html exposes a dedicated, visible element for the diagnostic
     result (not just the generic artifacts dump), and
  2. app.js actually captures and renders the doctor action's HTTP response
     into that element -- using its passed/exit_code/stderr/report fields --
     instead of discarding the response and only refreshing the run/artifact
     list.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from server import create_server


ENGINE_ROOT = Path("/Users/neurologic/Projects/evidence-synthesis-engine-screen-prove")


def _server(tmp_path):
    server = create_server(engine_root=ENGINE_ROOT, data_root=tmp_path / "runs")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _static_text(server, path: str) -> str:
    request = Request(f"http://127.0.0.1:{server.server_port}{path}")
    with urlopen(request) as response:
        return response.read().decode("utf-8")


def test_index_html_has_a_visible_diagnostic_result_element(tmp_path):
    server, thread = _server(tmp_path)
    try:
        html = _static_text(server, "/")
    finally:
        server.shutdown()
        thread.join()

    match = re.search(r'<[a-z]+[^>]*\bid="diagnostic-result"[^>]*>', html)
    assert match, "index.html must expose a #diagnostic-result element for the doctor action's response"
    tag = match.group(0)
    assert "hidden" not in tag, "the diagnostic result element must be visible by default, not hidden"
    assert "display:none" not in tag.replace(" ", ""), (
        "the diagnostic result element must be visible by default, not display:none"
    )


def test_app_js_renders_the_doctor_response_instead_of_discarding_it(tmp_path):
    server, thread = _server(tmp_path)
    try:
        js = _static_text(server, "/static/app.js")
    finally:
        server.shutdown()
        thread.join()

    action_handler = re.search(
        r'data-action[^\n]*\n?.*?addEventListener\("click".*?\}\)\);', js, re.S
    )
    assert action_handler, "could not find the [data-action] click handler in app.js"
    handler_body = action_handler.group(0)

    # The response from POST /api/runs/:id/:action must be captured into a
    # variable -- a bare `await request(...)` with no assignment is exactly
    # the "discards the response" bug this test guards against.
    assert re.search(r"(const|let|var)\s+\w+\s*=\s*await request\(", handler_body), (
        "the action handler must capture the diagnostic response instead of "
        "discarding it with a bare `await request(...)`"
    )

    # ...and that captured response must actually be rendered somewhere
    # using the fields the doctor action returns, not just used to refresh
    # the run/artifact list.
    assert "diagnostic-result" in js, "app.js must write the doctor response into #diagnostic-result"
    for field in ("passed", "exit_code", "stderr", "report"):
        assert field in js, f"app.js must render the doctor diagnostic's `{field}` field"
