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
- identity `@guarantee` declarations that return proof-carrying `Annotated` types;
- branch-sensitive narrowing through user-defined `TypeIs` predicates;
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

## Typed C-style prelude

`pyanalyzer.prelude` provides runnable, fully annotated Python classes and common
C-style library helpers. Its companion `.pyi` lets `--use-stubs` resolve the same
API without running it.

```python
from pyanalyzer.prelude import c_uint, atoi, malloc, strcpy, strlen, sqrt

wrapped = c_uint(-1)        # Native unsigned-int width; usually 4294967295.
number = atoi(" 42 items")  # c_int(42)
root = sqrt(9)             # c_double(3.0)
buffer = malloc(16)
strcpy(buffer, b"hello")
length = strlen(buffer)    # c_size_t(5)
assert number.value == 42
```

The scalar classes cover booleans, byte/wide characters, signed and unsigned C
integers, `size_t`/`ptrdiff_t`, fixed-width `int8_t` through `uint64_t`, and floating
point types. `sizeof` reports native scalar widths or a byte buffer's length.
Helpers include decimal conversions, allocation and memory operations,
NUL-terminated byte-string operations, common math functions, and
`printf`/`puts`/`putchar`. Try the complete example:

```console
uv run python examples/prelude_demo.py
uv run pyanalyzer --use-stubs examples/prelude_demo.py
```

These are Python simulations. Scalars are immutable, and `.value` is read-only.
Integer construction wraps to the declared width; arithmetic returns ordinary
Python numbers, so cast a result explicitly to wrap it again. `c_float` rounds on
construction; `c_longdouble` uses Python double precision while `sizeof` reports
its native C storage size. Buffers are zero-filled `bytearray` objects rather
than native pointers. Operations raise `ValueError` on negative sizes or capacity
violations, `realloc` resizes in place, and `free` clears the buffer. Byte-string
inputs may omit a trailing NUL; copy helpers supply or pad it as documented.
Conversions implement decimal prefixes, without locale or hexadecimal parsing.
Math errors raise Python exceptions. `printf` uses Python `%` formatting and
counts UTF-8 bytes, and does not support C length modifiers or pointer formatting.
Output uses Python text streams; `putchar` emits its low byte as a Unicode
character. These helpers do not expose native allocation, ABI calls, or C's
undefined behavior.

## Symbolic mathematics prelude

`pyanalyzer.math_prelude` is a separate, fully typed equational proof system.
`Symbol`, `Constant`, `Add`, `Mul`, and `Neg` form immutable expression trees;
`Equality` states an equation. Python `==` compares expression structure, so
`x + y` and `y + x` remain different expressions until related by a proof.

The eight `BASIC_AXIOMS` supply addition and multiplication commutativity,
associativity, and identities, additive inverses, and distributivity. They are
universally quantified equation schemas: `instantiate` requires a binding for
every symbol in the selected schema.

```python
from pyanalyzer.math_prelude import (
    ADD_COMMUTATIVITY, ADD_IDENTITY, ZERO, Symbol, transitivity,
)

x = Symbol("x")
commuted = ADD_COMMUTATIVITY.instantiate(x=ZERO, y=x)  # 0 + x = x + 0
identity = ADD_IDENTITY.instantiate(x=x)              # x + 0 = x
theorem = transitivity(commuted, identity)           # 0 + x = x
assert theorem.check()
print(theorem.conclusion)
```

Inference helpers provide reflexivity, symmetry, transitivity, substitution,
and congruence (replacing equal expressions inside a context). Substitution is
simultaneous. `calculation` proves the value of a ground expression using exact
`Fraction` arithmetic; floating-point constants are rejected.

Proof records are data, and constructing a record does not certify it.
`Proof.check()` recursively checks its premises, rule, and conclusion against
the built-in axioms, rejecting altered conclusions, mismatched transitivity
steps, and cycles. Custom axioms require explicit trust through
`proof.check(axioms=(...))`; passing an empty tuple permits only axiom-free
derivations. The checker verifies derivations relative to the selected axioms,
and does not establish that user-supplied axioms are consistent. This prelude
covers equational rational arithmetic; quantifiers, induction, inequalities,
and automated proof search are outside its scope.

The companion `.pyi` supports external import inference with `--use-stubs`.
Run the example to see derived identities and exact arithmetic:

```console
uv run python examples/math_prelude_demo.py
uv run pyanalyzer --use-stubs examples/math_prelude_demo.py
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
proof-type constructors. A guarantee accepts a value and returns that exact
value at runtime after executing its declaration. Put an `assert` in the
declaration to enforce the guarantee; a failed check raises `AssertionError`.
The decorator creates a generic `Guarantee[T]` callable, so ordinary type
checkers still see the decorated object as accepting and returning the original
value type. Pyanalyzer reports the additional proof as standard `Annotated`
metadata: `positive(value)` is `Annotated[int, (positive,)]`. Applying another
guarantee keeps the base type first and appends to the proof tuple, so
`odd(positive(value))` is `Annotated[int, (positive, odd)]`. Likewise,
`join(value, (positive, odd))` checks each guarantee and returns
`Annotated[int, (positive, odd)]`. Repeated guarantees are de-duplicated while
retaining their first occurrence. Explicit annotations use the same shape,
`Annotated[int, (positive,)]`, which ordinary type checkers treat as `int`.
`prop` remains a compatibility alias for `guarantee`.

Use `is_(value, (positive, odd))` as a conditional proof check. (`is` is a
Python keyword, so the callable uses the conventional trailing underscore.) It
accepts either a tuple or list of guarantees, returns `False` when one of their
assertions fails, and narrows the true branch to
`Annotated[int, (positive, odd)]`. The false branch retains the base `int`
because it cannot determine which guarantee failed.

Functions returning `TypeIs[T]` narrow their first argument when used as a
condition. Pyanalyzer narrows both branches: the true branch intersects the
argument's existing type with `T`, and the false branch excludes `T` where the
remaining type can be represented. It preserves known local subclasses,
understands `not` and direct boolean comparisons, and carries narrowing past a
guard whose other branch returns or raises.

```python
from typing import TypeIs

def is_text(value: object) -> TypeIs[str]:
    return isinstance(value, str)

def consume(value: int | str) -> str:
    if is_text(value):
        # value: str
        return value.strip()
    # value: int
    return str(value)
```

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

from pyanalyzer import guarantee, is_, join

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

if is_(number, [positive, odd]):
    proven = number  # Annotated[int, (positive, odd)]

def require_positive(value: Annotated[int, (positive,)]) -> int:
    return value
```

Python is highly dynamic, so unresolved calls and attributes are reported as
`Unknown`. This analyzer is designed for quick structural inspection and
machine-readable reports; use a full checker such as Pyright or mypy when you
need complete import resolution, generics, protocols, and whole-program correctness.
