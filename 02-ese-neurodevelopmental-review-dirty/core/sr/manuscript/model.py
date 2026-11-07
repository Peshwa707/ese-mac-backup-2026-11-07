"""Intermediate document model — authored once, serialized to MD / DOCX / HTML.

Each manuscript component is a `Component` holding an ordered list of typed
blocks. Serializers dispatch on block type, so adding a format never touches the
assembler and adding a component never touches the serializers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


@dataclass
class Heading:
    level: int          # 1..3
    text: str


@dataclass
class Paragraph:
    text: str


@dataclass
class BulletList:
    items: list[str]


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]
    caption: str = ""


@dataclass
class Figure:
    caption: str = ""
    svg_path: str = ""   # path to an SVG copied into figures/ (referenced by file)


@dataclass
class Note:
    """A callout box — used for honesty notices and [AUTHOR TO COMPLETE] markers."""
    text: str
    kind: str = "info"   # info | warning | todo


@dataclass
class CodeBlock:
    text: str
    lang: str = ""


Block = Union[Heading, Paragraph, BulletList, Table, Figure, Note, CodeBlock]


@dataclass
class Component:
    key: str             # filename stem, e.g. "04_included_abstracts"
    title: str           # human title, e.g. "Included Studies — Abstract List"
    blocks: list[Block] = field(default_factory=list)
