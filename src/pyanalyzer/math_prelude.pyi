"""Typed declarations for the symbolic mathematics prelude."""

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
    def __add__(self, other: Expr) -> Add: ...
    def __mul__(self, other: Expr) -> Mul: ...
    def __neg__(self) -> Neg: ...
    def substitute(self, **bindings: Expr) -> Expr: ...
    @abstractmethod
    def __str__(self) -> str: ...

@dataclass(frozen=True)
class Symbol(Expr):
    name: str
    def __post_init__(self) -> None: ...
    def __str__(self) -> str: ...

@dataclass(frozen=True, init=False)
class Constant(Expr):
    value: Fraction
    def __init__(self, value: int | Fraction = ...) -> None: ...
    def __str__(self) -> str: ...

@dataclass(frozen=True)
class Add(Expr):
    left: Expr
    right: Expr
    def __post_init__(self) -> None: ...
    def __str__(self) -> str: ...

@dataclass(frozen=True)
class Mul(Expr):
    left: Expr
    right: Expr
    def __post_init__(self) -> None: ...
    def __str__(self) -> str: ...

@dataclass(frozen=True)
class Neg(Expr):
    operand: Expr
    def __post_init__(self) -> None: ...
    def __str__(self) -> str: ...

@dataclass(frozen=True)
class Equality:
    left: Expr
    right: Expr
    def __post_init__(self) -> None: ...
    def substitute(self, **bindings: Expr) -> Equality: ...
    def __str__(self) -> str: ...

@dataclass(frozen=True)
class Axiom:
    name: str
    schema: Equality
    @property
    def variables(self) -> tuple[str, ...]: ...
    def instantiate(self, **bindings: Expr) -> Proof: ...

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
    conclusion: Equality
    rule: Rule
    premises: tuple[Proof, ...] = ...
    axiom: Axiom | None = ...
    bindings: tuple[tuple[str, Expr], ...] = ...
    context: Expr | None = ...
    variable: str | None = ...
    def check(self, axioms: Iterable[Axiom] | None = ...) -> bool: ...

ZERO: Constant
ONE: Constant
ADD_COMMUTATIVITY: Axiom
ADD_ASSOCIATIVITY: Axiom
ADD_IDENTITY: Axiom
ADD_INVERSE: Axiom
MUL_COMMUTATIVITY: Axiom
MUL_ASSOCIATIVITY: Axiom
MUL_IDENTITY: Axiom
DISTRIBUTIVITY: Axiom
BASIC_AXIOMS: tuple[Axiom, ...]

def reflexivity(expression: Expr) -> Proof: ...
def symmetry(proof: Proof) -> Proof: ...
def transitivity(first: Proof, second: Proof) -> Proof: ...
def congruence(proof: Proof, context: Expr, variable: str = ...) -> Proof: ...
def substitute(proof: Proof, **bindings: Expr) -> Proof: ...
def calculation(expression: Expr) -> Proof: ...
