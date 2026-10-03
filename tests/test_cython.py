from __future__ import annotations

import json
from textwrap import dedent

import pytest

from pyanalyzer import analyze_file, analyze_source
from pyanalyzer.cli import main
from pyanalyzer.gui import build_symbol_tree


def analyze(source: str, filename: str = "example.pyx"):
    report = analyze_source(dedent(source), filename=filename)
    assert not report.errors, report.errors
    return report


def test_declarations_preserve_native_types_values_and_lines():
    report = analyze('''\
        cdef unsigned long count = 1, limit = 8
        cdef double[:] values
        cdef const char *name
        cdef int indices[4]
        cdef double[4] samples
        count += 1
        text = "cdef double fake = 3"
        # cdef int ignored
    ''')
    assert report.variables["count"].inferred == "unsigned long"
    assert report.variables["count"].observed == ["Literal[1]", "Union[unsigned long, int]"]
    assert report.variables["count"].mutability == "mutable"
    assert report.variables["count"].locations == [1, 6]
    assert report.variables["limit"].inferred == "unsigned long"
    assert report.variables["values"].inferred == "double[:]"
    assert report.variables["name"].inferred == "const char*"
    assert report.variables["indices"].inferred == "int[4]"
    assert report.variables["samples"].inferred == "double[4]"
    assert report.variables["text"].inferred == "str"
    assert "fake" not in report.variables
    assert "ignored" not in report.variables


def test_functions_keep_typed_parameters_defaults_locals_and_exception_effects():
    report = analyze('''
        from libc.math cimport sqrt
        cimport numpy as np
        cdef inline double root(double value) except? -1 nogil:
            if value < 0:
                raise ValueError("negative")
            return sqrt(value)

        cpdef double calculate(double value, int scale=2):
            cdef double result = root(value)
            return result * scale

        def wrapper(double value, label="root"):
            return calculate(value)
    ''')
    root, calculate, wrapper = report.functions
    assert root.parameters == {"value": "double"}
    assert root.returns == "double"
    assert calculate.parameters == {"value": "double", "scale": "int"}
    assert calculate.variables["result"].inferred == "double"
    assert wrapper.parameters == {"value": "double", "label": "str"}
    assert wrapper.returns == "double"
    assert all(function.exceptions == ["ValueError"] for function in report.functions)


def test_multiline_functions_and_declarations_keep_original_locations():
    report = analyze('''\
        cpdef double total(
            const double[:, ::1] values,
            unsigned long count=2,
        ) noexcept nogil:
            cdef double result = (
                1.5 + 2.5
            )
            return result
        after = 3
    ''')
    function = report.functions[0]
    assert function.parameters == {"values": "const double[:,::1]", "count": "unsigned long"}
    assert function.variables["result"].locations == [5]
    assert function.parameter_variables["values"].locations == [1]
    assert report.variables["after"].locations == [9]


def test_extension_class_fields_are_instance_attributes_and_gui_symbols():
    report = analyze('''
        cdef class Counter:
            cdef public int value
            cdef readonly double step

            def __init__(self, int value):
                self.value = value
                self.step = 0.5

            cpdef int increment(self, int amount=1):
                self.value += amount
                return self.value
    ''')
    counter = report.classes[0]
    assert counter.class_variables == {}
    assert counter.instance_attributes["value"].inferred == "int"
    assert counter.instance_attributes["step"].inferred == "double"
    assert counter.instance_attributes["value"].mutability == "mutable"
    assert counter.methods[1].parameters == {"self": "Counter", "amount": "int"}
    symbols = {child.name: child for child in build_symbol_tree(report)[0].children}
    assert symbols["value"].kind == "instance attribute"
    assert symbols["step"].type_name == "double"


def test_declaration_blocks_and_external_prototypes():
    report = analyze('''\
        ctypedef unsigned long Size
        cdef:
            int count = 1
            double weight = 2.5
        cdef extern from "math.h" nogil:
            double sqrt(double value) noexcept
        cpdef double total(double[:] values, Size count=*)
    ''', filename="api.pxd")
    assert report.variables["count"].locations == [3]
    assert report.variables["weight"].inferred == "double"
    assert report.variables["Size"].inferred == "type[unsigned long]"
    assert [function.name for function in report.functions] == ["sqrt", "total"]
    assert report.functions[0].returns == "double"
    assert report.functions[1].parameters == {"values": "double[:]", "count": "Size"}


def test_gil_blocks_keep_locals_without_synthetic_variables():
    report = analyze('''
        cdef int calculate(int value) nogil:
            with nogil:
                cdef int doubled = value * 2
            with gil:
                result = doubled
            return result
    ''')
    assert report.functions[0].variables["doubled"].inferred == "int"
    assert report.functions[0].variables["result"].inferred == "int"


def test_memoryview_indexing_and_slicing():
    report = analyze('''
        def first(const double[:] values):
            return values[0]
        def row(double[:, ::1] values):
            return values[0]
        def element(double[:, ::1] values):
            return values[0, 0]
        def column(double[:, ::1] values):
            return values[:, 0]
    ''')
    assert [function.returns for function in report.functions] == [
        "double", "double[::1]", "double", "double[:]",
    ]


def test_cython_functions_use_existing_capture_and_mutation_analysis():
    report = analyze('''
        def outer(int seed):
            cdef int count = seed
            def increment():
                nonlocal count
                count += 1
                return count
            def current():
                return count
            return increment, current
    ''')
    function = report.functions[0]
    assert function.variables["count"].inferred == "int"
    assert function.variables["count"].mutability == "mutable"
    assert function.variables["count"].sharing == "shared"
    assert function.captures[0].captured_by == ["outer.current", "outer.increment"]


@pytest.mark.parametrize("suffix", [".pyx", ".pxd", ".pxi", ".PYX"])
def test_file_detection_and_cli_json(tmp_path, capsys, suffix):
    path = tmp_path / ("example" + suffix)
    path.write_text("cdef int value = 42\n", encoding="utf-8")
    assert analyze_file(path).variables["value"].inferred == "int"
    assert main(["--json", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["variables"]["value"]["inferred"] == "int"


def test_explicit_language_selection_and_python_regression(tmp_path, capsys):
    assert analyze_source("cdef int value", language="cython").variables["value"].inferred == "int"
    assert analyze_source("cdef int value", filename="test.pyx", language="python").errors
    assert analyze_source("cdef int value").errors
    assert analyze_source("def example(x: int) -> int:\n    return x\n", filename="test.pyx").functions[0].returns == "int"
    with pytest.raises(ValueError, match="Unsupported source language"):
        analyze_source("", language="other")
    path = tmp_path / "example.txt"
    path.write_text("cdef int value\n", encoding="utf-8")
    assert main(["--language", "cython", "--json", str(path)]) == 0
    assert not json.loads(capsys.readouterr().out)["errors"]


@pytest.mark.parametrize("source", [
    "cdef struct Point:\n    double x\n",
    "cdef int (*callback)(int)\n",
    'include "other.pxi"\n',
    "cdef int = 1\n",
    "cdef int value =\n",
    "cpdef double calculate(double value\n",
    "cpdef double calculate(double value) arbitrary:\n    return value\n",
])
def test_unsupported_and_malformed_syntax_returns_diagnostics(source):
    report = analyze_source(source, filename="broken.pyx")
    assert report.errors and "broken.pyx:" in report.errors[0]
    assert not report.variables and not report.functions
