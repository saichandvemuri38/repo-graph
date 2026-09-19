# Resolution Rules — linking `?name` edges to real symbols

The linker agent applies these rules to every EDGE whose destination starts with `?`. The rules are ordered. Stop at the first one that gives a single answer.

## Order of resolution

1. **Same-file scope** (`high`): the name is defined in the same file, in scope. Prefer the innermost enclosing scope, then the enclosing class, then the module.
2. **Instance/class methods** (`high`): `self.x` or `this.x` → look in the enclosing class, then follow `EXTENDS` edges upward (nearest ancestor first). If found, resolve to that method.
3. **Import map** (`high`): use the `# import-map:` comment lines in the source file's shard.
   - `c <- a.b` and a call to `c(...)` → symbol `c` in the file that `a.b` maps to.
   - `m <- a.b` and a call to `m.f(...)` → symbol `f` in that file.
   - Convert dotted module paths to file paths (`a/b.py`, `a/b/__init__.py`, `a/b/index.ts`) and check they exist with Glob.
4. **Unique global name** (`med`): exactly one symbol in the whole graph has this name, and it is a function, class or method. Use it.
5. **Multiple candidates** (`low`): pick by proximity in this order: same directory, then same top-level package, then the shortest path distance. If one candidate wins clearly, resolve to it with `low`. If there is no clear winner, leave the edge as `?name` and record all candidates in `unresolved.md`.
6. **No candidate**: leave as `?name`. Record it in `unresolved.md` with reason `no-definition` or `external`.

## Confidence meaning

| Level | Means | Used in impact reports as |
|---|---|---|
| `high` | Proven by import, scope or the class hierarchy | Certain |
| `med` | Unique name match with no import evidence | Probable |
| `low` | Chosen by proximity, or method call on an unknown object | Possible — always flagged |

## Never do

- Never invent a candidate. Every resolved ID must exist as a `SYM` record. Check with Grep before writing it.
- Never resolve to a symbol with a different arity or obviously different meaning just because the names match. When unsure, use `low` or leave unresolved.
- Never edit `SYM` or `FILE` records. The linker only rewrites the destination and confidence of EDGE lines.

## Builtins and ignorable names

Python: `print len range enumerate zip map filter sorted reversed sum min max abs any all isinstance issubclass type int str float bool list dict set tuple open super iter next input repr format hash id round divmod pow getattr setattr hasattr callable` and common container methods (`append extend pop get items keys values update add remove join split strip lower upper format`).
JS/TS: `console.* Math.* JSON.* Object.* Array.* Promise.* setTimeout fetch parseInt parseFloat require`.
Method names on containers and strings are ignorable only when the receiver is clearly a builtin type. Otherwise resolve normally.

## When a file is removed or renamed

- Delete its shard and its manifest line.
- Any EDGE elsewhere whose destination was a symbol from that file goes back to `?<name>` with confidence `low`, then goes through resolution again.

## When a new symbol appears

Check `unresolved.md` for entries whose name matches the new symbol. Re-run resolution on those source edges. Old unresolved edges in unchanged files often become resolvable.
