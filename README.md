# pyanalyzer

`pyanalyzer` is a small, dependency-free static analyzer for Python source. It
uses the standard-library AST and **does not import or execute the file being
inspected**.

It reports:

- module, local, class, and instance-attribute types;
- literal evidence and widened types (`Literal[1]` becomes `int` when joined);
- unions created by multiple assignments or branches;
- numeric promotion (`int + float` becomes `float`);
- literal and dynamic keys assigned to dictionaries;
- function and lambda `Callable` types;
- same-file `super()` traversal, including inherited instance attributes and
  return types from resolved base methods;
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
# Equivalent module form:
uv run python -m pyanalyzer examples/demo.py
```

Analyze several files at once by passing multiple paths. The JSON form returns
one object for one input and a list for multiple inputs.

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

## Interpretation and limits

The tool records exact literal observations, then widens them for ordinary
inferred types. For example, assignments of `1` and `"one"` are shown as
observations `Literal[1]` and `Literal['one']`, with the inferred type
`Union[int, str]`. An explicit annotation is preserved as the inferred type.

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
exact value at runtime; it does not validate it. The decorator creates a
generic `Guarantee[T]` callable, so ordinary type checkers see
`positive(value: int) -> int` even without pyanalyzer support. For pyanalyzer,
`positive(value)` is reported as `Refined[int, positive]`, while
`odd(positive(value))` preserves the ordered composition as
`Refined[Refined[int, positive], odd]`. Use `join(value, (odd, positive))`
when the guarantees are unordered; it is also an identity function and is
reported as `Refined[int, {odd, positive}]`. For a refinement annotation that
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
    return value

@guarantee
def odd(value: int) -> int:
    return value

ordered = odd(positive(number))
unordered = join(number, (odd, positive))

def require_positive(value: Annotated[int, positive]) -> int:
    return value
```

Python is highly dynamic, so unresolved calls and attributes are reported as
`Unknown`. This analyzer is designed for quick structural inspection and
machine-readable reports; use a full checker such as Pyright or mypy when you
need import resolution, generics, protocols, and whole-program correctness.
