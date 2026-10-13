"""Runtime helpers for guarantee-based proof annotations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import update_wrapper
from typing import Any, Generic, TypeVar


Value = TypeVar("Value")


class Guarantee(Generic[Value]):
    """A typed, callable declaration of a guarantee about a value.

    ``Guarantee[T]`` is intentionally an ordinary generic class so type
    checkers that do not understand pyanalyzer can still see that calling it
    accepts and returns ``T``.  The wrapped value itself is never boxed.
    """

    def __init__(self, declaration: Callable[[Value], Any]) -> None:
        self.declaration = declaration
        update_wrapper(self, declaration)
        # Keep the original marker for consumers that already inspect it.
        self.__pyanalyzer_prop__ = True
        self.__pyanalyzer_guarantee__ = True

    def __call__(self, value: Value) -> Value:
        """Validate *value* and return that exact value when the check passes."""
        self.declaration(value)
        return value


def guarantee(declaration: Callable[[Value], Any]) -> Guarantee[Value]:
    """Declare a validating identity function carrying a proof guarantee.

    The declaration body is executed, so it can enforce its condition with an
    ``assert`` before returning the value. The resulting :class:`Guarantee`
    returns its argument unchanged on success; ``pyanalyzer`` attaches the
    guarantee name to the successful value's ``Annotated`` proof tuple.
    """
    return Guarantee(declaration)


# ``prop`` remains available for existing source files.  Prefer ``guarantee``
# for new code, which better communicates the validating proof behavior.
prop = guarantee


def join(value: Value, guarantees: Iterable[Guarantee[Any]]) -> Value:
    """Attach several guarantees to *value* without imposing an order.

    Every guarantee is checked, and the original value is returned. For example,
    ``join(number, (odd, positive))`` has the same value as ``number`` while
    the analyzer records both guarantees in one proof tuple.
    """

    for check in guarantees:
        check(value)
    return value


def is_(
    value: Value,
    guarantees: tuple[Guarantee[Any], ...] | list[Guarantee[Any]],
) -> bool:
    """Return whether *value* satisfies every supplied guarantee.

    Failed assertions become ``False`` so this helper can be used as a type
    narrowing condition. Unexpected exceptions raised by a guarantee still
    propagate. Pyanalyzer attaches the successful guarantees as proof metadata
    on the true branch.
    """
    try:
        for check in guarantees:
            check(value)
    except AssertionError:
        return False
    return True
