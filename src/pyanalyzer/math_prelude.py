"""A small symbolic equational proof system for exact rational arithmetic.

Axioms are universally quantified equation schemas. Instantiation and inference
build proof trees; ``Proof.check()`` checks every step against the selected
axioms. Expressions retain their syntax rather than simplifying automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

__all__ = [
    "Expr", "Symbol", "Constant", "Add", "Mul", "Neg", "Equality", "Axiom",
    "Rule", "Proof", "ZERO", "ONE", "ADD_COMMUTATIVITY", "ADD_ASSOCIATIVITY",
    "ADD_IDENTITY", "ADD_INVERSE", "MUL_COMMUTATIVITY", "MUL_ASSOCIATIVITY",
    "MUL_IDENTITY", "DISTRIBUTIVITY", "BASIC_AXIOMS", "reflexivity", "symmetry",
    "transitivity", "congruence", "substitute", "calculation",
]


class Expr(ABC):
    """A symbolic expression; use Equality rather than == to state equations."""

    def __add__(self, other: Expr) -> Add:
        return Add(self, other)

    def __mul__(self, other: Expr) -> Mul:
        return Mul(self, other)

    def __neg__(self) -> Neg:
        return Neg(self)

    def substitute(self, **bindings: Expr) -> Expr:
        """Replace free symbols simultaneously, without recursive replacement."""
        _check_bindings(bindings)
        return _replace(self, bindings)

    @abstractmethod
    def __str__(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class Symbol(Expr):
    name: str

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise ValueError("symbol names must be Python identifiers")

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, init=False)
class Constant(Expr):
    value: Fraction

    def __init__(self, value: int | Fraction = 0) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, Fraction)):
            raise TypeError("constants require exact integers or Fractions")
        object.__setattr__(self, "value", Fraction(value))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class Add(Expr):
    left: Expr
    right: Expr

    def __post_init__(self) -> None:
        _check_operands(self.left, self.right)

    def __str__(self) -> str:
        return f"({self.left} + {self.right})"


@dataclass(frozen=True)
class Mul(Expr):
    left: Expr
    right: Expr

    def __post_init__(self) -> None:
        _check_operands(self.left, self.right)

    def __str__(self) -> str:
        return f"({self.left} * {self.right})"


@dataclass(frozen=True)
class Neg(Expr):
    operand: Expr

    def __post_init__(self) -> None:
        _check_operands(self.operand)

    def __str__(self) -> str:
        return f"(-{self.operand})"


def _check_operands(*operands: Expr) -> None:
    if any(not isinstance(operand, (Symbol, Constant, Add, Mul, Neg)) for operand in operands):
        raise TypeError("operands must be symbolic expressions")


def _check_bindings(bindings: dict[str, Expr]) -> None:
    for name, expression in bindings.items():
        Symbol(name)
        _check_operands(expression)


def _replace(expression: Expr, bindings: dict[str, Expr]) -> Expr:
    if isinstance(expression, Symbol):
        return bindings.get(expression.name, expression)
    if isinstance(expression, Add):
        return Add(_replace(expression.left, bindings), _replace(expression.right, bindings))
    if isinstance(expression, Mul):
        return Mul(_replace(expression.left, bindings), _replace(expression.right, bindings))
    if isinstance(expression, Neg):
        return Neg(_replace(expression.operand, bindings))
    if isinstance(expression, Constant):
        return expression
    raise TypeError("unsupported expression")


def _symbols(expression: Expr) -> set[str]:
    if isinstance(expression, Symbol):
        return {expression.name}
    if isinstance(expression, (Add, Mul)):
        return _symbols(expression.left) | _symbols(expression.right)
    if isinstance(expression, Neg):
        return _symbols(expression.operand)
    if isinstance(expression, Constant):
        return set()
    raise TypeError("unsupported expression")


def _evaluate(expression: Expr) -> Fraction:
    if isinstance(expression, Constant):
        return expression.value
    if isinstance(expression, Add):
        return _evaluate(expression.left) + _evaluate(expression.right)
    if isinstance(expression, Mul):
        return _evaluate(expression.left) * _evaluate(expression.right)
    if isinstance(expression, Neg):
        return -_evaluate(expression.operand)
    raise ValueError("calculation requires an expression without symbols")


@dataclass(frozen=True)
class Equality:
    left: Expr
    right: Expr

    def __post_init__(self) -> None:
        _check_operands(self.left, self.right)

    def substitute(self, **bindings: Expr) -> Equality:
        return Equality(self.left.substitute(**bindings), self.right.substitute(**bindings))

    def __str__(self) -> str:
        return f"{self.left} = {self.right}"


@dataclass(frozen=True)
class Axiom:
    name: str
    schema: Equality

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(sorted(_symbols(self.schema.left) | _symbols(self.schema.right)))

    def instantiate(self, **bindings: Expr) -> Proof:
        """Bind every schema variable exactly once to obtain an equation."""
        _check_bindings(bindings)
        if set(bindings) != set(self.variables):
            raise ValueError(f"{self.name} requires exactly these bindings: {self.variables}")
        return Proof(self.schema.substitute(**bindings), Rule.AXIOM,
                     axiom=self, bindings=tuple(sorted(bindings.items())))


class Rule(Enum):
    AXIOM = "axiom"
    REFLEXIVITY = "reflexivity"
    SYMMETRY = "symmetry"
    TRANSITIVITY = "transitivity"
    CONGRUENCE = "congruence"
    SUBSTITUTION = "substitution"
    CALCULATION = "calculation"


@dataclass(frozen=True)
class Proof:
    """An untrusted proof record. Call check() before accepting its conclusion."""

    conclusion: Equality
    rule: Rule
    premises: tuple[Proof, ...] = ()
    axiom: Axiom | None = None
    bindings: tuple[tuple[str, Expr], ...] = ()
    context: Expr | None = None
    variable: str | None = None

    def check(self, axioms: Iterable[Axiom] | None = None) -> bool:
        """Validate the whole tree; custom axioms require explicit opt-in."""
        trusted = frozenset(BASIC_AXIOMS if axioms is None else axioms)
        return _check_proof(self, trusted, set(), set())


def _check_proof(proof: Proof, trusted: frozenset[Axiom], active: set[int],
                 checked: set[int]) -> bool:
    identity = id(proof)
    if identity in checked:
        return True
    if identity in active:
        return False
    active.add(identity)
    try:
        if not isinstance(proof, Proof) or not isinstance(proof.conclusion, Equality):
            return False
        if not all(_check_proof(premise, trusted, active, checked) for premise in proof.premises):
            return False
        if not _valid_step(proof, trusted):
            return False
        checked.add(identity)
        return True
    except (TypeError, ValueError, AttributeError):
        return False
    finally:
        active.remove(identity)


def _valid_step(proof: Proof, trusted: frozenset[Axiom]) -> bool:
    if proof.rule != Rule.AXIOM and proof.axiom is not None:
        return False
    if proof.rule not in (Rule.AXIOM, Rule.SUBSTITUTION) and proof.bindings:
        return False
    if proof.rule != Rule.CONGRUENCE and (proof.context is not None or proof.variable is not None):
        return False
    bindings = dict(proof.bindings)
    if len(bindings) != len(proof.bindings):
        return False
    _check_bindings(bindings)
    equation = proof.conclusion
    premises = proof.premises
    if proof.rule == Rule.AXIOM:
        return (not premises and proof.axiom is not None and proof.axiom in trusted
                and set(bindings) == set(proof.axiom.variables)
                and equation == proof.axiom.schema.substitute(**bindings))
    if proof.rule == Rule.REFLEXIVITY:
        return not premises and equation.left == equation.right
    if proof.rule == Rule.SYMMETRY:
        return (len(premises) == 1 and equation == Equality(
            premises[0].conclusion.right, premises[0].conclusion.left))
    if proof.rule == Rule.TRANSITIVITY:
        return (len(premises) == 2
                and premises[0].conclusion.right == premises[1].conclusion.left
                and equation == Equality(premises[0].conclusion.left, premises[1].conclusion.right))
    if proof.rule == Rule.CONGRUENCE:
        if len(premises) != 1 or proof.context is None or proof.variable is None:
            return False
        Symbol(proof.variable)
        premise = premises[0].conclusion
        return equation == Equality(
            proof.context.substitute(**{proof.variable: premise.left}),
            proof.context.substitute(**{proof.variable: premise.right}))
    if proof.rule == Rule.SUBSTITUTION:
        return len(premises) == 1 and equation == premises[0].conclusion.substitute(**bindings)
    if proof.rule == Rule.CALCULATION:
        return not premises and equation.right == Constant(_evaluate(equation.left))
    return False


def reflexivity(expression: Expr) -> Proof:
    return Proof(Equality(expression, expression), Rule.REFLEXIVITY)


def symmetry(proof: Proof) -> Proof:
    return Proof(Equality(proof.conclusion.right, proof.conclusion.left), Rule.SYMMETRY, (proof,))


def transitivity(first: Proof, second: Proof) -> Proof:
    if first.conclusion.right != second.conclusion.left:
        raise ValueError("transitivity requires matching middle expressions")
    return Proof(Equality(first.conclusion.left, second.conclusion.right), Rule.TRANSITIVITY,
                 (first, second))


def congruence(proof: Proof, context: Expr, variable: str = "x") -> Proof:
    """Replace every occurrence of variable in context with each equation side."""
    Symbol(variable)
    equation = Equality(context.substitute(**{variable: proof.conclusion.left}),
                        context.substitute(**{variable: proof.conclusion.right}))
    return Proof(equation, Rule.CONGRUENCE, (proof,), context=context, variable=variable)


def substitute(proof: Proof, **bindings: Expr) -> Proof:
    equation = proof.conclusion.substitute(**bindings)
    return Proof(equation, Rule.SUBSTITUTION, (proof,), bindings=tuple(sorted(bindings.items())))


def calculation(expression: Expr) -> Proof:
    """Prove a ground expression's value using exact rational arithmetic."""
    return Proof(Equality(expression, Constant(_evaluate(expression))), Rule.CALCULATION)


ZERO = Constant(0)
ONE = Constant(1)
_x, _y, _z = Symbol("x"), Symbol("y"), Symbol("z")

ADD_COMMUTATIVITY = Axiom("addition_commutativity", Equality(_x + _y, _y + _x))
ADD_ASSOCIATIVITY = Axiom("addition_associativity", Equality((_x + _y) + _z, _x + (_y + _z)))
ADD_IDENTITY = Axiom("addition_identity", Equality(_x + ZERO, _x))
ADD_INVERSE = Axiom("addition_inverse", Equality(_x + -_x, ZERO))
MUL_COMMUTATIVITY = Axiom("multiplication_commutativity", Equality(_x * _y, _y * _x))
MUL_ASSOCIATIVITY = Axiom("multiplication_associativity", Equality((_x * _y) * _z, _x * (_y * _z)))
MUL_IDENTITY = Axiom("multiplication_identity", Equality(_x * ONE, _x))
DISTRIBUTIVITY = Axiom("distributivity", Equality(_x * (_y + _z), (_x * _y) + (_x * _z)))

BASIC_AXIOMS: tuple[Axiom, ...] = (
    ADD_COMMUTATIVITY, ADD_ASSOCIATIVITY, ADD_IDENTITY, ADD_INVERSE,
    MUL_COMMUTATIVITY, MUL_ASSOCIATIVITY, MUL_IDENTITY, DISTRIBUTIVITY,
)
