# Extraction Rules — turning one source file into a shard

Read `graph-schema.md` first. You are acting as a careful parser. You are not a summarizer.

## Procedure (per file)

1. **Read the whole file** with the Read tool. If it is longer than 2000 lines, read it in ranges until you have seen every line.
2. **Take the hash** from the value the orchestrator gave you. If none was given, run `shasum -a 256 <file>` and keep the first 12 hex characters.
3. **List definitions** in source order: classes, functions, methods, nested functions, module-level constants. Add the `<module>` pseudo-symbol when the file has top-level executable statements.
4. **For each definition**, capture the exact start and end line from the Read output, the signature and a summary.
5. **List edges** by walking each definition body:
   - every call expression → `CALLS`
   - every import statement → `IMPORTS`
   - every base class → `EXTENDS`
   - every function or class used as a value (callback, decorator, `map(f, ...)`, dict of handlers) → `REFERENCES`
6. **Write the shard** in one Write call to the path given by the schema.
7. **Verify**: re-read the shard. Confirm exactly one FILE record, every SYM has a `DEFINES` edge, and no line contains stray pipes or newlines.

## Hard rules

- **Never guess a line number.** Every number must come from Read output.
- **Do not resolve names across files.** Write the destination as it appears in code. Use `?name` when it is not defined in this file. The linker agent resolves those later.
- **Only same-file resolution is allowed at extraction time.** If the call target is defined in this same file and is unambiguous, write its full ID with confidence `high`.
- **Imports:** for a relative or project import, write the destination as the target file's relpath if you can tell which file it is, else `?module.path`. For standard-library or third-party imports write `ext:<top-level module>`.
- **Skip noise:** do not create CALLS edges for language builtins (`print`, `len`, `range`, `isinstance`, `super`, `console.log`, and so on). See the builtin list in `resolution-rules.md`.
- **Method calls on unknown objects** (`obj.run()` where `obj` is a parameter) → `?obj.run`, confidence `low`. Do not guess the class.
- **Do not invent** symbols that are not in the file. If the file is empty, write only the FILE record and a comment line `# empty file`.
- **Dynamic behavior** (`getattr`, `eval`, reflection, decorators that register handlers): note it as a comment line in the shard, `# dynamic: <line> <what>`, and do not create an edge.

## Python specifics

- Methods: `Class.method`. Nested classes: `Outer.Inner.method`.
- `self.helper()` inside a method → `<relpath>::<Class>.helper` when that method exists in the same class (high). Otherwise `?self.helper`.
- `@staticmethod` and `@classmethod` are still kind `method`. Note the decorator in the summary.
- Decorators produce a `REFERENCES` edge from the decorated symbol to the decorator when it is a project symbol.
- `from a.b import c` → `IMPORTS` to `?a.b` and keep a comment line `# import-map: c <- a.b` so the linker can resolve `c(...)` calls later. Do the same for `import a.b as m` → `# import-map: m <- a.b`.
- `if __name__ == "__main__":` → calls inside it belong to `<module>`.
- Lambdas and comprehensions belong to the enclosing symbol.

## JavaScript / TypeScript specifics

- `export function`, `export const f = () =>`, class methods and object-literal methods that are exported all count as symbols.
- `import { a as b } from "./x"` → `# import-map: b <- ./x#a`.
- React components are functions of kind `function`. JSX usage `<Foo />` is a `CALLS` edge to `?Foo`.

## Other languages

Use the same shape. Map functions, methods, classes and interfaces to the kinds above (interfaces → `class`, note "interface" in the summary). If you are unsure how a construct maps, choose the closest kind and add a `# note:` comment line.

## Report back

Reply with one line per file: `<relpath>|<sha12>|<symbol-count>|<edge-count>|ok` or `...|failed|<reason>`. Nothing else.
