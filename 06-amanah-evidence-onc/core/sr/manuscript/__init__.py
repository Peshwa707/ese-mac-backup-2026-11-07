"""Manuscript export: turn a grounded SRRun into per-component documents in
Markdown (editable), DOCX, and PDF, saved as a project folder under the engine
output base (~/Documents/EvidenceEngine/<vertical>/ by default).

One intermediate document model (model.py) is authored once by assemble.py and
serialized three ways (render_md / render_docx / render_html→PDF) so the formats
never drift. Data-driven components are fully grounded; not-yet-done sections
(RoB / statistics / GRADE) are explicit editable stubs, never invented content.
"""
