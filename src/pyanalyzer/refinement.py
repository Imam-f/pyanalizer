"""Runtime helpers for guarantee-based refinement types."""

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
    """Declare a validating identity function carrying a refinement guarantee.

    The declaration body is executed, so it can enforce its condition with an
    ``assert`` before returning the value. The resulting :class:`Guarantee`
    returns its argument unchanged on success; ``pyanalyzer`` attaches the
    guarantee name to the successful value's type.
    """
    return Guarantee(declaration)


# ``prop`` remains available for existing source files.  Prefer ``guarantee``
# for new code, which better communicates the validating refinement behavior.
prop = guarantee


def join(value: Value, guarantees: Iterable[Guarantee[Any]]) -> Value:
    """Attach several guarantees to *value* without imposing an order.

    Every guarantee is checked, and the original value is returned. For example,
    ``join(number, (odd, positive))`` has the same value as ``number`` while
    the analyzer records an unordered pair of guarantees.
    """

    for check in guarantees:
        check(value)
    return value
