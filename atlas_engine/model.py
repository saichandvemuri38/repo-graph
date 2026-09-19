"""Plain data records passed between parsers, the resolver and the store."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Symbol:
    id: str            # "<relpath>::<Qualified.Name>"
    file: str
    kind: str          # module | class | function | method | constant
    name: str
    qname: str
    start: int
    end: int
    signature: str = ""
    summary: str = ""
    body_sha: str = ""   # hash of the symbol's own source lines, used for exact change detection


@dataclass
class RawEdge:
    """An edge as written in the source, before names are resolved across files."""
    type: str          # CALLS | REFERENCES | EXTENDS | IMPORTS
    src: str           # symbol id (file path for IMPORTS)
    target: str        # dotted name as written, e.g. "self.helper", "np.array", "*.run"
    line: int
    level: int = 0     # relative-import level (python)
    aux: str = ""      # extra info (imported names for IMPORTS)
    inferred: bool = False  # receiver type was inferred, so confidence is capped at "med"
    local: bool = False     # head of the name is a local variable/parameter
    ctx: str = ""      # qualified name of the enclosing class, used for self/this/super


@dataclass
class ImportBinding:
    alias: str
    module: str
    name: str | None   # None for "import module"; a name for "from module import name"; "*" for star imports
    level: int
    line: int


@dataclass
class FileParse:
    path: str
    lang: str
    sha: str
    line_count: int
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[RawEdge] = field(default_factory=list)
    imports: list[ImportBinding] = field(default_factory=list)
    error: str = ""
