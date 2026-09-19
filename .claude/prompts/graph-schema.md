# Graph Schema (CodeAtlas) — v1

> **Who writes this format:** the CodeAtlas engine (`atlas_engine/`) exports every graph as these shards on each index, straight from its SQLite database (`.claude/atlas/graph/atlas.db`, the source of truth). The fallback agents (`graph-indexer`, `graph-linker`) write the same format for languages the engine cannot parse. The engine adds one thing the agents may see: `EDGE` lines whose destination is `?name` are unresolved calls, and a `# parse-error:` comment marks a file recovered by a tolerant scan.

The code graph is stored as plain text under `.claude/atlas/graph/`. Every record is one line, pipe-delimited, so it can be searched with Grep. This file is the single source of truth for the format. Every agent must follow it exactly.

## Identifiers

| Thing | ID format | Example |
|---|---|---|
| File | `<relpath>` from the repo root, forward slashes | `sorting/quick.py` |
| Symbol | `<relpath>::<Qualified.Name>` | `sorting/quick.py::Sorter.partition` |
| Module pseudo-symbol | `<relpath>::<module>` | `sorting/quick.py::<module>` |
| Nested function | `<relpath>::outer.inner` | `a.py::build.helper` |
| Overload / redefinition | append `#2`, `#3` | `a.py::f#2` |
| External package | `ext:<module>` | `ext:numpy` |
| Unresolved reference | `?<name as written>` | `?partition`, `?self.helper` |

- The `<module>` pseudo-symbol represents top-level statements. Calls made at import time or inside `if __name__ == "__main__":` belong to it.
- If a field value contains `|`, replace it with `¦`. Newlines inside fields are never allowed.

## Shard files

One shard per source file: `.claude/atlas/graph/shards/<relpath with "/" replaced by "__">.graph.md`
Example: `sorting/quick.py` → `.claude/atlas/graph/shards/sorting__quick.py.graph.md`

A shard contains only comment lines (`# ...`) and these three record types.

```
FILE|<relpath>|<language>|<sha12>|<line-count>
SYM|<id>|<kind>|<start>-<end>|<signature>|<summary>
EDGE|<TYPE>|<src-id>|<dst-id or ?name or ext:module>|<line>|<confidence>
```

### FILE
- `sha12`: the first 12 hex characters of the file's SHA-256 (from `shasum -a 256`).
- Exactly one FILE record, and it is the first record.

### SYM
- `kind`: `module`, `class`, `function`, `method`, `constant`.
- `start-end`: 1-based inclusive line numbers copied from the Read tool output. Never estimated.
- `signature`: the def/class line as written, with the body removed, trimmed to 160 characters.
- `summary`: at most 100 characters, plain English, saying what it does. Derive it from the code and docstring. Do not invent behavior.

### EDGE types
| Type | src → dst | Meaning |
|---|---|---|
| `DEFINES` | file or class → symbol | Symbol is defined directly inside it |
| `IMPORTS` | file → file / `ext:module` | Import statement (the line is the import line) |
| `CALLS` | symbol → symbol | Direct call to a function, method or constructor |
| `EXTENDS` | class → class | Inheritance |
| `REFERENCES` | symbol → symbol | Non-call use: passed as a callback, assigned, decorated with, returned |

- `line`: 1-based line of the call or reference site.
- `confidence`: `high`, `med` or `low` (see `resolution-rules.md`).
- Every symbol must have exactly one `DEFINES` edge from its file or its enclosing class.

## Global files (all in `.claude/atlas/graph/`)

`manifest.md` — one line per indexed file:
```
<relpath>|<sha12>|<indexed-at ISO date>|<status>
```
status: `pending`, `extracted` or `linked`.

`meta.md` — `key: value` lines: `schemaVersion`, `lastFullIndex`, `lastLink`, `lastCluster`, `fileCount`, `symbolCount`, `edgeCount`, `unresolvedCount`, `languages`.

`unresolved.md`:
```
UNRES|<src-id>|<name as written>|<line>|<candidate ids, comma-separated, or none>|<reason>
```

`clusters.md`:
```
CLUSTER|<cluster-id>|<label>|<member-count>|<cohesion: low/med/high>|<summary>
MEMBER|<cluster-id>|<symbol-id>
```

`processes.md`:
```
PROCESS|<process-id>|<name>|<entry-symbol-id>|<step-count>|<summary>
STEP|<process-id>|<order>|<symbol-id>
```

## Useful Grep patterns (regex)

| Question | Pattern (search in `.claude/atlas/graph/shards/`) |
|---|---|
| Where is symbol X defined? | `^SYM\|<id>\|` |
| Who calls X? (callers) | `^EDGE\|CALLS\|[^\|]*\|<id>\|` |
| What does X call? (callees) | `^EDGE\|CALLS\|<id>\|` |
| Who extends class X? | `^EDGE\|EXTENDS\|[^\|]*\|<id>\|` |
| Who imports file F? | `^EDGE\|IMPORTS\|[^\|]*\|<relpath>\|` |
| All symbols with a name | `^SYM\|[^\|]*::([^\|]*\.)?<name>\|` |
