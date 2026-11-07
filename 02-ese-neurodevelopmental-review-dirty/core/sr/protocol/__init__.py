"""Protocol-writer: rough draft -> validated ProtocolSpec -> review protocol + vertical.

The agent (interactive skill or native llm.parse) fills a ProtocolSpec; the compiler
renders BOTH the human protocol.md and the runnable engine vertical YAML deterministically,
so the engine config is never free-typed. validate_vertical() round-trips the result through
load_sr_config and the poolable-field registry.
"""

from core.sr.protocol.compile import compile_vertical, render_protocol_md
from core.sr.protocol.spec import ProtocolSpec
from core.sr.protocol.validate import validate_vertical

__all__ = ["ProtocolSpec", "compile_vertical", "render_protocol_md", "validate_vertical"]
