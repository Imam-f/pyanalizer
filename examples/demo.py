"""Run with: uv run pyanalyzer --use-stubs --color always examples/demo.py"""

from collections.abc import Callable
from fractions import Fraction
from typing import Annotated, Literal, TypeIs

from pyanalyzer import guarantee, is_, join
from pyanalyzer import math_prelude as symbolic
from pyanalyzer import prelude as c


# --- Bindings and mutable state ---------------------------------------------

Mode = Literal["fast", "safe"]
# One write and no detected mutation: const, single.
demo_label = "type-analysis demo"

# The subscript write makes this binding mutable. Two functions below read it,
# so the report also marks it shared and lists both readers.
settings = {"mode": "fast", "retries": 3}
settings["verbose"] = True


# --- Settings readers -------------------------------------------------------

def configured_mode() -> str:
    assert isinstance(settings["mode"], str)
    return settings["mode"]


def configured_retries() -> int:
    assert isinstance(settings["retries"], int)
    return settings["retries"]


# --- Closures and captures --------------------------------------------------

def make_handlers(prefix: str) -> tuple[Callable[[str], str], Callable[[], int]]:
    history: list[str] = []
    separator = ": "

    def format_message(message: str) -> str:
        history.append(message)
        return prefix + separator + message

    def count() -> int:
        return len(history)

    return format_message, count


# --- TypeIs narrowing -------------------------------------------------------

def is_text(value: object) -> TypeIs[str]:
    return isinstance(value, str)


def describe_identifier(value: int | str) -> str:
    if is_text(value):
        # The true branch intersects ``int | str`` with ``str``.
        text_value = value
        return text_value.upper()

    # The false branch excludes ``str``, leaving ``int``.
    number_value = value
    return f"identifier-{number_value}"


def normalize_text(value: int | str) -> str:
    if not is_text(value):
        return str(value)

    # The returning guard leaves only the ``str`` path reachable here.
    guarded_text = value
    return guarded_text.strip()


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
#   positive_sample -> Annotated[int, (positive,)]
#   ordered_sample  -> Annotated[int, (positive, odd)]
#   joined_sample   -> Annotated[int, (positive, odd)]
sample_number = 7
positive_sample = positive(sample_number)
ordered_sample = odd(positive(sample_number))
joined_sample = join(sample_number, (positive, odd))


def positive_result(value: int) -> Annotated[int, (positive,)]:
    """Return type: Annotated[int, (positive,)]."""
    return positive(value)


def positive_odd_result(value: int) -> Annotated[int, (positive, odd)]:
    """Return type: Annotated[int, (positive, odd)]."""
    return join(value, (positive, odd))


def describe_proven_number(value: int) -> str:
    if is_(value, [positive, odd]):
        # proven_value: Annotated[int, (positive, odd)]
        proven_value = value
        return f"positive odd number: {proven_value}"

    # A false result keeps the original base type because either proof may
    # have failed.
    unproven_value = value
    return f"unproven number: {unproven_value}"


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

    # join records the supplied guarantees in one proof tuple.
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
# pyanalyzer preserves the proof tuple in the inferred type.
def require_positive(value: Annotated[int, (positive,)]) -> int:
    return value


promoted = 2 + 0.5


# --- Runnable C-style prelude ----------------------------------------------

c_wrapped_unsigned = c.c_uint(-1)
c_single_precision = c.c_float(0.1)
c_parsed_integer = c.atoi(" 42 items")
c_square_root = c.sqrt(9)
c_integer_width = c.sizeof(c.c_uint)

c_buffer = c.malloc(16)
c.strcpy(c_buffer, b"hello")
c.strcat(c_buffer, b"!")
c_string_length = c.strlen(c_buffer)
assert c_buffer[:c_string_length] == b"hello!"


# --- Symbolic mathematical axioms and checked proofs ------------------------

math_symbol = symbolic.Symbol("a")
math_commuted = symbolic.ADD_COMMUTATIVITY.instantiate(x=symbolic.ZERO, y=math_symbol)
math_identity = symbolic.ADD_IDENTITY.instantiate(x=math_symbol)

# Derive 0 + a = a using 0 + a = a + 0 and a + 0 = a.
math_left_identity = symbolic.transitivity(math_commuted, math_identity)
assert math_left_identity.conclusion == symbolic.Equality(symbolic.ZERO + math_symbol, math_symbol)
assert math_left_identity.check()

# Replace equal expressions inside a larger context, then specialize a theorem.
math_lifted_identity = symbolic.congruence(
    math_left_identity, symbolic.Symbol("hole") * symbolic.ONE, variable="hole",
)
math_specialized_identity = symbolic.substitute(
    math_left_identity, a=symbolic.Symbol("b") + symbolic.ONE,
)
math_reversed_identity = symbolic.symmetry(math_specialized_identity)
assert math_lifted_identity.check()
assert math_reversed_identity.check()

# Exact ground arithmetic: 1/3 + 2/3 = 1.
math_exact_sum = symbolic.calculation(
    symbolic.Constant(Fraction(1, 3)) + symbolic.Constant(Fraction(2, 3)),
)
assert math_exact_sum.conclusion.right == symbolic.ONE
assert math_exact_sum.check()

# Constructing a proof record does not establish its conclusion.
math_unjustified = symbolic.Proof(
    symbolic.Equality(symbolic.ZERO, symbolic.ONE), symbolic.Rule.REFLEXIVITY,
)
assert not math_unjustified.check()
