from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path

import pytest

from pyanalyzer import analyze_source
from pyanalyzer import math_prelude as m


def test_expressions_are_immutable_and_structurally_equal():
    x, y = m.Symbol("x"), m.Symbol("y")
    assert x + y == m.Add(x, y)
    assert x * y == m.Mul(x, y)
    assert -x == m.Neg(x)
    assert x + y != y + x
    assert str(m.Equality(x + m.ZERO, x)) == "(x + 0) = x"
    assert m.Constant(1) == m.Constant(Fraction(1, 1))
    assert hash(x + m.ONE) == hash(m.Symbol("x") + m.Constant(1))
    with pytest.raises(FrozenInstanceError):
        x.name = "changed"
    with pytest.raises(ValueError):
        m.Symbol("not a symbol")
    with pytest.raises(TypeError):
        m.Constant(0.1)
    with pytest.raises(TypeError):
        m.Constant(True)
    with pytest.raises(TypeError):
        m.Add(x, 1)


def test_symbol_substitution_is_simultaneous():
    x, y = m.Symbol("x"), m.Symbol("y")
    expression = (x + y) * -x
    assert expression.substitute(x=y, y=m.ONE) == (y + m.ONE) * -y
    assert expression.substitute() == expression
    with pytest.raises(TypeError):
        expression.substitute(x=42)


@pytest.mark.parametrize("axiom", m.BASIC_AXIOMS)
def test_every_builtin_axiom_can_be_instantiated(axiom):
    bindings = {name: m.Symbol(name + "_value") + m.ONE for name in axiom.variables}
    proof = axiom.instantiate(**bindings)
    assert proof.check()
    assert proof.conclusion == axiom.schema.substitute(**bindings)
    assert proof.check(axioms=(axiom,))
    assert not proof.check(axioms=())
    assert not replace(proof, conclusion=m.Equality(m.ZERO, m.ONE)).check()


def test_axioms_require_complete_bindings_and_known_schema():
    with pytest.raises(ValueError):
        m.ADD_COMMUTATIVITY.instantiate(x=m.ONE)
    with pytest.raises(ValueError):
        m.ADD_IDENTITY.instantiate(x=m.ONE, y=m.ZERO)
    fake = m.Axiom(m.ADD_IDENTITY.name, m.Equality(m.ZERO, m.ONE)).instantiate()
    assert not fake.check()
    assert fake.check(axioms=(fake.axiom,))


def test_derive_left_identity_from_commutativity_and_right_identity():
    x = m.Symbol("x")
    commuted = m.ADD_COMMUTATIVITY.instantiate(x=m.ZERO, y=x)
    identity = m.ADD_IDENTITY.instantiate(x=x)
    theorem = m.transitivity(commuted, identity)
    assert theorem.conclusion == m.Equality(m.ZERO + x, x)
    assert theorem.check()
    assert theorem.check(axioms=(m.ADD_COMMUTATIVITY, m.ADD_IDENTITY))
    assert not theorem.check(axioms=(m.ADD_IDENTITY,))
    with pytest.raises(ValueError):
        m.transitivity(identity, commuted)


def test_equality_rules_preserve_validity():
    x, y = m.Symbol("x"), m.Symbol("y")
    original = m.ADD_COMMUTATIVITY.instantiate(x=x, y=y)
    reverse = m.symmetry(original)
    assert reverse.conclusion == m.Equality(y + x, x + y)
    assert reverse.check()
    assert m.transitivity(original, reverse).conclusion == m.Equality(x + y, x + y)
    assert m.reflexivity(x).check(axioms=())
    specialized = m.substitute(original, x=y + m.ONE, y=-x)
    assert specialized.conclusion == m.Equality((y + m.ONE) + -x, -x + (y + m.ONE))
    assert specialized.check()


def test_congruence_handles_multiple_occurrences_and_nested_operators():
    a = m.Symbol("a")
    premise = m.ADD_IDENTITY.instantiate(x=a)
    hole = m.Symbol("hole")
    context = -(hole * hole) + m.ONE
    theorem = m.congruence(premise, context, variable="hole")
    assert theorem.conclusion == m.Equality(-((a + m.ZERO) * (a + m.ZERO)) + m.ONE,
                                            -(a * a) + m.ONE)
    assert theorem.check()
    assert m.congruence(premise, m.ONE).check()


def test_ground_calculation_uses_exact_rationals():
    expression = (m.Constant(Fraction(1, 3)) + m.Constant(Fraction(2, 3))) * -m.Constant(2)
    theorem = m.calculation(expression)
    assert theorem.conclusion == m.Equality(expression, m.Constant(-2))
    assert theorem.check(axioms=())
    assert not replace(theorem, conclusion=m.Equality(expression, m.ONE)).check()
    with pytest.raises(ValueError):
        m.calculation(m.Symbol("unknown") + m.ONE)


@pytest.mark.parametrize("rule", list(m.Rule))
def test_unjustified_false_equations_are_rejected(rule):
    assert not m.Proof(m.Equality(m.ZERO, m.ONE), rule).check()


def test_checker_rejects_invalid_premises_and_extraneous_metadata():
    x = m.Symbol("x")
    bad = m.Proof(m.Equality(x, m.ZERO), m.Rule.REFLEXIVITY)
    assert not m.symmetry(bad).check()
    assert not m.substitute(bad, x=m.ONE).check()
    assert not m.congruence(bad, m.Symbol("x") + m.ONE).check()
    good = m.reflexivity(x)
    assert not replace(good, premises=(good,)).check()
    assert not replace(good, axiom=m.ADD_IDENTITY).check()
    assert not replace(good, bindings=(("x", m.ONE),)).check()
    assert not replace(good, context=x).check()
    axiom = m.ADD_IDENTITY.instantiate(x=x)
    assert not replace(axiom, bindings=()).check()
    assert not replace(axiom, bindings=(("x", x), ("x", m.ONE))).check()
    assert not replace(m.symmetry(axiom), premises=()).check()
    assert not replace(m.congruence(axiom, x), variable=None).check()


def test_checker_rejects_a_transitivity_record_with_mismatched_middle():
    x = m.Symbol("x")
    first = m.ADD_IDENTITY.instantiate(x=x)
    second = m.reflexivity(m.ONE)
    forged = m.Proof(m.Equality(first.conclusion.left, m.ONE), m.Rule.TRANSITIVITY,
                     (first, second))
    assert not forged.check()


def test_checker_handles_shared_proofs_and_rejects_cycles():
    proof = m.reflexivity(m.Symbol("x"))
    assert m.transitivity(proof, proof).check()
    cycle = m.symmetry(proof)
    object.__setattr__(cycle, "premises", (cycle,))
    assert not cycle.check()


def test_analyzer_resolves_symbolic_prelude_declarations():
    report = analyze_source('''
from pyanalyzer.math_prelude import *
x = Symbol("x")
zero = ZERO
equation = Equality(x, x)
axiom = ADD_IDENTITY
proof = axiom.instantiate(x=x)
conclusion = proof.conclusion
valid = proof.check()
reverse = symmetry(proof)
trivial = reflexivity(x)
''', use_stubs=True)
    assert not report.errors
    expected = {
        "x": "pyanalyzer.math_prelude.Symbol", "zero": "pyanalyzer.math_prelude.Constant",
        "equation": "pyanalyzer.math_prelude.Equality", "axiom": "pyanalyzer.math_prelude.Axiom",
        "proof": "pyanalyzer.math_prelude.Proof", "conclusion": "pyanalyzer.math_prelude.Equality",
        "valid": "bool", "reverse": "pyanalyzer.math_prelude.Proof",
        "trivial": "pyanalyzer.math_prelude.Proof",
    }
    assert {name: report.variables[name].inferred for name in expected} == expected
    assert set(m.__all__).issubset(report.variables)
    assert "Fraction" not in report.variables
    assert Path(report.stub_files["pyanalyzer.math_prelude"]).name == "math_prelude.pyi"
