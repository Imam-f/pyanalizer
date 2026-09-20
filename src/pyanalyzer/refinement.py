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
        """Return *value* itself without executing the declaration body."""
        return value


def guarantee(declaration: Callable[[Value], Any]) -> Guarantee[Value]:
    """Declare an identity function carrying one static guarantee.

    The decorated function's body is not a runtime validator.  The resulting
    :class:`Guarantee` remains callable and returns its argument unchanged;
    ``pyanalyzer`` reads its name as the guarantee attached to that value.
    """
    return Guarantee(declaration)


# ``prop`` remains available for existing source files.  Prefer ``guarantee``
# for new code, which better communicates the non-validating behavior.
prop = guarantee


def join(value: Value, guarantees: Iterable[Guarantee[Any]]) -> Value:
    """Attach several guarantees to *value* without imposing an order.

    This is also an identity at runtime.  For example,
    ``join(number, (odd, positive))`` has the same value as ``number`` while
    the analyzer records an unordered pair of guarantees.
    """

    # Accept and deliberately ignore the iterable so generators are not
    # consumed merely to carry static information.
    del guarantees
    return value
