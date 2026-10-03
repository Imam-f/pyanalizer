# pyanalyzer

`pyanalyzer` is a small, dependency-free static analyzer for Python and Cython source. It
uses the standard-library AST and **does not import or execute the file being
inspected**.

It reports:

- module, local, class, and instance-attribute types;
- per-variable `const`/`mutable` and `single`/`shared` state, including the
  scopes sharing captured variables and attributes;
- literal evidence and widened types (`Literal[1]` becomes `int` when joined);
- unions created by multiple assignments or branches;
- numeric promotion (`int + float` becomes `float`);
- literal and dynamic keys assigned to dictionaries;
- function and lambda `Callable` types;
- same-file `super()` traversal, including inherited instance attributes and
  return types from resolved base methods;
- class member origins (class body, `__init__`, and other methods), plus
  flattened inherited class variables, instance attributes, and methods;
- identity `@guarantee` declarations that construct refinement types;
- nested-function captures, including `single` versus `shared` and `mutable`
  versus `read-only` captures.
- explicit function exceptions and their propagation through same-file callers,
  with handled exceptions removed by `try`/`except`.

## Run it with uv

No manual virtual-environment setup is needed:

```console
uv run pyanalyzer examples/demo.py
uv run pyanalyzer --color always examples/demo.py
uv run pyanalyzer --json examples/demo.py
# Open the interactive syntax-highlighted symbol inspector:
uv run pyanalyzer-gui examples/demo.py
# Equivalent module form:
uv run python -m pyanalyzer examples/demo.py
# Analyze Cython source without installing Cython or a C compiler:
uv run pyanalyzer examples/demo.pyx
```

Analyze several files at once by passing multiple paths. The JSON form returns
one object for one input and a list for multiple inputs.

The GUI analyzes edits as you type. Hover a symbol in the code editor or the
right sidebar to see its inferred type, state, source locations, sharing, and
other available properties. Matching symbol occurrences are highlighted, and
double-clicking a sidebar symbol with a known location jumps to its line.

Human-readable output uses color automatically in an interactive terminal and
adds whitespace between variables, guarantees, functions, classes, and their detail
groups. Use `--color always` to preserve ANSI colors through a pipe or
`--color never` for plain text. The standard `NO_COLOR` and `FORCE_COLOR`
environment variables are also honored.

## Develop and test

```console
uv sync --dev
uv run pytest
```

The public Python API is also intentionally small:

```python
from pyanalyzer import analyze_file, analyze_source

report = analyze_file("example.py")
print(report.to_dict())
```

## Cython source

Files ending in `.pyx`, `.pxd`, or `.pxi` automatically use the Cython reader.
The CLI and GUI accept these files, and the GUI highlights Cython keywords.
Use `--language cython` to analyze files with another extension, or select it
explicitly for source snippets:

```python
report = analyze_source("cdef double value = 1.5", language="cython")
assert report.variables["value"].inferred == "double"
```

The reader supports `cdef` variables and declaration blocks, typed parameters
on `def`/`cdef`/`cpdef` functions, function prototypes, `cdef class` instance
fields and methods, `cimport`, simple `ctypedef` aliases, `cdef extern from`
blocks, and `with gil`/`with nogil` blocks. It preserves C type names, pointer
and array declarations, typed memoryviews, and source line numbers. Memoryview
indexing reports element types; partial indexing and slicing report remaining
dimensions. Function qualifiers such as `inline`, `noexcept`, and `except? -1`
are accepted; exception reports still describe explicit Python raises and
same-file propagation. See the [Cython language reference](https://docs.cython.org/en/latest/src/userguide/language_basics.html)
for the declaration syntax.

This is a structural reader for a subset of Cython, not a Cython compiler or
C type checker. It does not expand includes,
validate C arithmetic conversions, or model GIL and ABI behavior. C/C++
structs, unions, enums, fused types, function pointers, and casts produce
diagnostics. Other expressions use the analyzer's conservative Python
inference, with unresolved operations reported as `Unknown`. Analyze included `.pxi` files
directly. Ordinary Python annotations in Cython's pure Python mode remain
available through the Python reader; Cython decorator and helper-call semantics
are not interpreted.

## External dependency stubs

Enable external import inference with `--use-stubs`. The analyzer reads `.pyi`
and supported `.pxd` declarations without importing dependencies or executing
their code. The GUI also has a **Use stubs** checkbox.

```console
# Search beside the source, the nearest project .venv, and the active Python path:
uv run pyanalyzer --use-stubs app.py
# Select another virtual environment (Windows and Unix layouts are supported):
uv run pyanalyzer --venv C:/projects/myapp/.venv app.py
# Supply custom declarations; repeat --stub-path to add more locations:
uv run pyanalyzer --stub-path ./stubs --venv .venv app.py
# A single stub file is accepted, with its filename used as the module name:
uv run pyanalyzer --stub-path ./stubs/dependency.pyi app.py
uv run pyanalyzer-gui --use-stubs --venv .venv app.py
```

`--stub-path` and `--venv` enable stub lookup automatically. Custom stub paths
take priority, followed by source/package directories and the selected
environment. If no environment is selected, lookup also searches the nearest
ancestor's `.venv` and the analyzer's current `sys.path`. Selecting `--venv`
excludes the analyzer's ambient Python path. Installed stub-only packages such
as `dependency-stubs` are searched before adjacent `dependency` stubs, and
`.pyi` declarations are preferred to `.pxd` declarations in each location.
No dependency installation, environment activation, `.pth` execution, or
dependency interpreter invocation occurs.

For example, given `stubs/dependency.pyi` containing
`def read() -> list[str]: ...`, `from dependency import read; result = read()`
reports `result` as `list[str]` when stub lookup is enabled. Resolution covers
module aliases, submodules, relative imports in packages, explicit re-exports,
declared variables, constructors, inherited class members, properties, and
Cython `cimport` declarations. Overloaded functions conservatively return a
union of all declared returns; argument matching and generic substitution are
not implemented. Only declaration files are used; inline annotations in
dependency `.py` implementations and bundled typeshed are not provided.
Missing declarations remain `Unknown`, while unreadable or malformed stubs
appear in report errors. Text output lists loaded stubs, and JSON records
their paths in `stub_files`.

The same options are available in the Python API:

```python
report = analyze_file("app.py", use_stubs=True)
report = analyze_file("app.py", stub_paths=["stubs"], venv=".venv")
report = analyze_source("from dependency import read\nresult = read()",
                        stub_paths=["stubs"])
```

## Interpretation and limits

The tool records exact literal observations, then widens them for ordinary
inferred types. For example, assignments of `1` and `"one"` are shown as
observations `Literal[1]` and `Literal['one']`, with the inferred type
`Union[int, str]`. An explicit annotation is preserved as the inferred type.

Every reported binding carries variable state. `const` means no reassignment
or value mutation was detected; `mutable` covers repeated assignment,
augmented assignment, item/attribute writes, and common mutating calls such as
`append` or `update`. This is a static observation, not a claim that the
runtime object is deeply immutable. `shared` follows capture ownership: more
than one nested scope or class method uses the variable or attribute. The
`shared_with` list identifies those scopes; otherwise the state is `single`.
Function parameters receive the same state analysis.

A capture is `shared` when more than one nested function captures the same
owner-local variable. It is mutable when a closure uses `nonlocal`, assigns
through an attribute/subscript, or calls a common mutating method such as
`append`, `add`, or `update`.

Direct calls such as `super().__init__(...)` and `super().method(...)` are
resolved through same-file base classes. The report records the selected base
method, incorporates attributes assigned by that method, and follows further
cooperative `super()` calls. External or dynamically selected bases remain
explicitly unresolved.

Functions decorated with `@guarantee` are guarantees and implicit
refinement-type constructors. A guarantee accepts a value and returns that
exact value at runtime after executing its declaration. Put an `assert` in the
declaration to enforce the guarantee; a failed check raises `AssertionError`.
The declaration still returns the original value. The decorator creates a generic `Guarantee[T]` callable,
so ordinary type checkers see the decorated object as accepting and returning
the original value type. For pyanalyzer,
`positive(value)` is reported as `Refined[int, positive]`, while
`odd(positive(value))` preserves the ordered composition as
`Refined[Refined[int, positive], odd]`. Use `join(value, (odd, positive))`
when the guarantees are unordered; it checks every guarantee and is reported
as `Refined[int, {odd, positive}]`. For a refinement annotation that
also passes ordinary type checking, use `Annotated[int, positive]`; the type
checker treats it as `int`, while pyanalyzer reports `Refined[int, positive]`.
`prop` remains a compatibility alias for `guarantee`.

Exception effects are appended to callable types. A function that explicitly
raises `ValueError` is shown as `Callable[..., T] raises ValueError`, and a
same-file caller receives that effect transitively. Propagation covers forward
references, nested functions, methods, constructors, and `super()` calls.
Matching `try`/`except` handlers remove caught effects, including built-in and
same-file custom exception subclasses; bare re-raises preserve them. Assertions
contribute `AssertionError`. The analysis stays local and does not guess
undocumented exceptions from external functions or ordinary Python operators.

```python
from typing import Annotated

from pyanalyzer import guarantee, join

@guarantee
def positive(value: int) -> int:
    assert value > 0
    return value

@guarantee
def odd(value: int) -> int:
    assert value % 2 != 0
    return value

ordered = odd(positive(number))
unordered = join(number, (odd, positive))

def require_positive(value: Annotated[int, positive]) -> int:
    return value
```

Python is highly dynamic, so unresolved calls and attributes are reported as
`Unknown`. This analyzer is designed for quick structural inspection and
machine-readable reports; use a full checker such as Pyright or mypy when you
need complete import resolution, generics, protocols, and whole-program correctness.
