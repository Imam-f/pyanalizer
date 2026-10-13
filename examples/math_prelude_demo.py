"""Prove symbolic identities; analyze with --use-stubs to resolve the API."""

from fractions import Fraction

from pyanalyzer.math_prelude import (
    ADD_COMMUTATIVITY, ADD_IDENTITY, ONE, ZERO, Constant, Equality, Symbol,
    calculation, congruence, substitute, symmetry, transitivity,
)

a = Symbol("a")
commuted = ADD_COMMUTATIVITY.instantiate(x=ZERO, y=a)
identity = ADD_IDENTITY.instantiate(x=a)

# Derive 0 + a = a from 0 + a = a + 0 and a + 0 = a.
left_identity = transitivity(commuted, identity)
assert left_identity.conclusion == Equality(ZERO + a, a)
assert left_identity.check()

# The same equality remains true inside a larger expression.
lifted = congruence(left_identity, Symbol("hole") * ONE, variable="hole")
assert lifted.check()

# Substitute an expression for a, then reverse the equation.
specialized = substitute(left_identity, a=Symbol("b") + ONE)
reversed_identity = symmetry(specialized)
assert reversed_identity.check()

# Ground arithmetic is calculated exactly, without floating-point rounding.
exact = calculation(Constant(Fraction(1, 3)) + Constant(Fraction(2, 3)))
assert exact.conclusion.right == ONE
assert exact.check()

if __name__ == "__main__":
    for theorem in (left_identity, lifted, reversed_identity, exact):
        print(theorem.conclusion)
