"""Run with: uv run pyanalyzer --color always examples/demo.py"""

from collections.abc import Callable
from typing import Annotated, Literal

from pyanalyzer import guarantee, join


# --- Bindings and mutable state ---------------------------------------------

Mode = Literal["fast", "safe"]
# One write and no detected mutation: const, single.
demo_label = "type-analysis demo"

# The subscript write makes this binding mutable. Two functions below read it,
# so the report also marks it shared and lists both readers.
settings = {"mode": "fast", "retries": 3}
settings["verbose"] = True


def configured_mode() -> str:
    assert isinstance(settings["mode"], str)
    return settings["mode"]


def configured_retries() -> int:
    assert isinstance(settings["retries"], int)
    return settings["retries"]


def make_handlers(prefix: str) -> tuple[Callable[[str], str], Callable[[], int]]:
    history: list[str] = []
    separator = ": "

    def format_message(message: str) -> str:
        history.append(message)
        return prefix + separator + message

    def count() -> int:
        return len(history)

    return format_message, count


# --- Guarantees and refinements ---------------------------------------------

@guarantee
def positive(value: int) -> int:
    assert value > 0, "value must be positive"
    return value


@guarantee
def odd(value: int) -> int:
    assert value % 2 != 0, "value must be odd"
    return value


# Refinement showcase. These appear directly in the analyzer's variables section:
#
#   positive_sample -> Refined[int, positive]
#   ordered_sample  -> Refined[Refined[int, positive], odd]
#   joined_sample   -> Refined[int, {odd, positive}]
sample_number = 7
positive_sample = positive(sample_number)
ordered_sample = odd(positive(sample_number))
joined_sample = join(sample_number, (positive, odd))


def positive_result(value: int) -> Annotated[int, positive]:
    """Return type: Refined[int, positive]."""
    return positive(value)


def positive_odd_result(value: int) -> Annotated[int, positive, odd]:
    """Return type: Refined[int, {odd, positive}]."""
    return join(value, (positive, odd))


# --- Classes and inheritance ------------------------------------------------

class WorkItem:
    def __init__(self, source: str):
        self.source = source

    def label(self) -> str:
        return "work item"


class NumberJob(WorkItem):
    kind = "numeric-analysis"

    def __init__(self, source: str, value: int, values: list[int]):
        super().__init__(source)
        self.value = value
        self.values = values

    def label(self) -> str:
        return super().label()


def describe(job: NumberJob) -> str:
    # Nested guarantees are ordered: odd is applied after positive.
    ordered = odd(positive(job.value))

    # join expresses the same guarantees as an unordered set.
    unordered = join(job.value, (odd, positive))
    return f"ordered={ordered}, unordered={unordered}"


# --- Exception effects ------------------------------------------------------

def require_values(values: list[int]) -> list[int]:
    if not values:
        raise ValueError("at least one value is required")
    return values


def first_value(job: NumberJob) -> int:
    # ValueError bubbles from require_values into this function's type.
    return require_values(job.values)[0]


def first_or_default(job: NumberJob, default: int = 0) -> int:
    # Handling ValueError removes it from this function's exception effect.
    try:
        return first_value(job)
    except ValueError:
        return default


# Standard ``Annotated`` keeps this valid for ordinary type checkers while
# pyanalyzer reads it as ``Refined[int, positive]``.
def require_positive(value: Annotated[int, positive]) -> int:
    return value


promoted = 2 + 0.5
