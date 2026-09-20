"""Run with: uv run pyanalyzer --color always examples/demo.py"""

from collections.abc import Callable
from typing import Annotated, Literal

from pyanalyzer import guarantee, join


Mode = Literal["fast", "safe"]
settings = {"mode": "fast", "retries": 3}
settings["verbose"] = True


def make_handlers(prefix: str) -> tuple[Callable[[str], str], Callable[[], int]]:
    history: list[str] = []
    separator = ": "

    def format_message(message: str) -> str:
        history.append(message)
        return prefix + separator + message

    def count() -> int:
        return len(history)

    return format_message, count


@guarantee
def positive(value: int) -> int:
    return value


@guarantee
def odd(value: int) -> int:
    return value


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
