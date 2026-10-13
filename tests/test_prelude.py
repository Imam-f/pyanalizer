from __future__ import annotations

import ctypes
import math
from pathlib import Path

import pytest

from pyanalyzer import analyze_source
from pyanalyzer import prelude as c


@pytest.mark.parametrize("scalar,native", [
    (c.c_byte, ctypes.c_byte), (c.c_ubyte, ctypes.c_ubyte),
    (c.c_short, ctypes.c_short), (c.c_ushort, ctypes.c_ushort),
    (c.c_int, ctypes.c_int), (c.c_uint, ctypes.c_uint),
    (c.c_long, ctypes.c_long), (c.c_ulong, ctypes.c_ulong),
    (c.c_longlong, ctypes.c_longlong), (c.c_ulonglong, ctypes.c_ulonglong),
    (c.c_size_t, ctypes.c_size_t), (c.c_ssize_t, ctypes.c_ssize_t),
])
def test_native_integer_casts_and_sizes(scalar, native):
    bits = ctypes.sizeof(native) * 8
    for value in (-1, 0, 1, 1 << (bits - 1), (1 << bits) + 3):
        result = scalar(value)
        assert result.value == native(value).value
        assert isinstance(result, scalar)
        assert c.sizeof(result) == ctypes.sizeof(native)
    assert c.sizeof(scalar) == ctypes.sizeof(native)
    assert scalar("12") == 12
    assert scalar(b"-7") == native(-7).value


@pytest.mark.parametrize("signed,unsigned,bits", [
    (c.int8_t, c.uint8_t, 8), (c.int16_t, c.uint16_t, 16),
    (c.int32_t, c.uint32_t, 32), (c.int64_t, c.uint64_t, 64),
])
def test_fixed_width_casts_and_python_arithmetic(signed, unsigned, bits):
    assert signed(1 << (bits - 1)) == -(1 << (bits - 1))
    assert unsigned(-1) == (1 << bits) - 1
    assert unsigned(1 << bits) == 0
    assert c.sizeof(signed) == c.sizeof(unsigned) == bits // 8
    result = unsigned(-1) + 1
    assert type(result) is int
    assert result == 1 << bits
    assert unsigned(result) == 0


def test_boolean_character_and_floating_scalars():
    assert c.c_bool(42).value is True
    assert c.c_bool(0).value is False
    assert c.c_char(b"A").value == b"A"
    assert c.c_wchar("é").value == "é"
    for value in (b"", b"ab"):
        with pytest.raises(ValueError):
            c.c_char(value)
    with pytest.raises(ValueError):
        c.c_wchar("ab")
    number = c.c_float(0.1)
    assert number.value == ctypes.c_float(0.1).value
    assert number.value != 0.1
    assert c.c_double("0.1").value == 0.1
    assert c.c_longdouble(0.1).value == 0.1
    for scalar, native in (
        (c.c_bool, ctypes.c_bool), (c.c_char, ctypes.c_char),
        (c.c_wchar, ctypes.c_wchar), (c.c_float, ctypes.c_float),
        (c.c_double, ctypes.c_double), (c.c_longdouble, ctypes.c_longdouble),
    ):
        assert c.sizeof(scalar) == c.sizeof(scalar()) == ctypes.sizeof(native)
    with pytest.raises(AttributeError):
        number.value = 2.0
    assert c.size_t is c.c_size_t
    assert c.ptrdiff_t is c.c_ssize_t
    assert c.sizeof(b"abc") == c.sizeof(bytearray(3)) == 3


def test_decimal_conversions_and_absolute_values():
    for parse, scalar in ((c.atoi, c.c_int), (c.atol, c.c_long), (c.atoll, c.c_longlong)):
        assert type(parse(b" \t-42suffix")) is scalar
        assert parse(b" \t-42suffix") == -42
        assert parse("+12.5") == 12
        assert parse("invalid") == parse("+ 1") == 0
    assert c.atof(" -.25e+2rest") == -25.0
    assert c.atof(b"1e+") == 1.0
    assert c.atof("invalid") == 0
    assert math.isinf(c.atof("-infinity"))
    assert math.isnan(c.atof("nan"))
    for absolute, scalar in ((c.abs, c.c_int), (c.labs, c.c_long), (c.llabs, c.c_longlong)):
        assert type(absolute(-42)) is scalar
        assert absolute(-42) == 42


def test_allocation_and_memory_operations():
    buffer = c.calloc(2, 3)
    assert buffer == bytearray(6)
    assert c.memcpy(buffer, b"abc", 3) is buffer
    assert buffer == b"abc\0\0\0"
    assert c.memmove(buffer, buffer, 6) is buffer
    assert c.memset(buffer, 257, 2) is buffer
    assert buffer == b"\1\1c\0\0\0"
    assert c.memcmp(b"abc", b"abd", 3) < 0
    assert c.memcmp(b"abc", b"abd", 2) == 0
    assert c.memcmp(b"z", b"a", 1) > 0
    assert c.realloc(buffer, 3) is buffer
    assert buffer == b"\1\1c"
    assert c.realloc(buffer, 5) is buffer
    assert buffer == b"\1\1c\0\0"
    c.free(buffer)
    assert buffer == b""
    assert c.free(None) is None


@pytest.mark.parametrize("operation", [
    lambda buffer: c.memcpy(buffer, b"abc", 3),
    lambda buffer: c.memcpy(buffer, b"", 1),
    lambda buffer: c.memmove(buffer, b"abc", 3),
    lambda buffer: c.memset(buffer, 0, 3),
    lambda buffer: c.memcmp(buffer, b"abc", 3),
    lambda buffer: c.strcpy(buffer, b"ab"),
    lambda buffer: c.strncpy(buffer, b"abc", 3),
    lambda buffer: c.strcat(buffer, b"ab"),
    lambda buffer: c.memcpy(buffer, b"ab", -1),
    lambda buffer: c.realloc(buffer, -1),
])
def test_buffer_capacity_errors_preserve_destination(operation):
    buffer = bytearray(b"a\0")
    with pytest.raises(ValueError):
        operation(buffer)
    assert buffer == b"a\0"


def test_negative_allocation_sizes():
    with pytest.raises(ValueError):
        c.malloc(-1)
    with pytest.raises(ValueError):
        c.calloc(-1, -1)
    with pytest.raises(ValueError):
        c.strncmp(b"a", b"b", -1)


def test_nul_terminated_strings_and_strncpy_padding():
    buffer = c.malloc(10)
    assert c.strcpy(buffer, b"hi\0ignored") is buffer
    assert c.strcat(buffer, b"!") is buffer
    assert buffer[:4] == b"hi!\0"
    assert c.strlen(buffer) == 3
    assert c.strcmp(buffer, b"hi!") == 0
    assert c.strcmp(b"a\0z", b"b") < 0
    assert c.strncmp(b"abc", b"abd", 2) == 0
    assert c.strncmp(b"abc", b"abd", 3) < 0
    assert c.strncmp(b"a", b"b", 0) == 0
    assert c.strncpy(buffer, b"x", 4) is buffer
    assert buffer[:4] == b"x\0\0\0"
    c.strncpy(buffer, b"abcdef", 4)
    assert buffer[:4] == b"abcd"


@pytest.mark.parametrize("function,argument,expected", [
    (c.sqrt, 9, 3), (c.sin, 0, 0), (c.cos, 0, 1), (c.tan, 0, 0),
    (c.exp, 0, 1), (c.log, 1, 0), (c.log10, 100, 2),
    (c.fabs, -3.5, 3.5), (c.floor, -1.5, -2), (c.ceil, -1.5, -1),
])
def test_math_results(function, argument, expected):
    result = function(argument)
    assert type(result) is c.c_double
    assert result == pytest.approx(expected)


def test_math_power_and_domain_errors():
    assert type(c.pow(2, 3)) is c.c_double
    assert c.pow(2, 3) == 8
    with pytest.raises(ValueError):
        c.sqrt(-1)
    with pytest.raises(ValueError):
        c.log(0)


def test_output_helpers(capsys):
    assert c.printf("%s: %d %.1f", "é", c.c_int(7), c.c_double(1.5)) == 9
    assert c.puts(b"ok\0ignored") == 3
    assert c.putchar(321) == 65
    assert capsys.readouterr().out == "é: 7 1.5ok\nA"


def test_analyzer_resolves_prelude_scalars_aliases_and_helpers():
    report = analyze_source('''
from pyanalyzer.prelude import *
number = c_uint(-1)
value = number.value
flag = c_bool(1).value
parsed = atoi("42")
root = sqrt(9)
buffer = malloc(16)
length = strlen(buffer)
width = sizeof(c_uint)
size = size_t(4)
difference = ptrdiff_t(-1)
character = c_char(b"A").value
''', use_stubs=True)
    assert not report.errors
    expected = {
        "number": "pyanalyzer.prelude.c_uint", "value": "int", "flag": "bool",
        "parsed": "pyanalyzer.prelude.c_int", "root": "pyanalyzer.prelude.c_double",
        "buffer": "bytearray", "length": "pyanalyzer.prelude.c_size_t",
        "width": "pyanalyzer.prelude.c_size_t", "size": "pyanalyzer.prelude.c_size_t",
        "difference": "pyanalyzer.prelude.c_ssize_t", "character": "bytes",
    }
    assert {name: report.variables[name].inferred for name in expected} == expected
    assert set(c.__all__).issubset(report.variables)
    assert "ctypes" not in report.variables and "SupportsInt" not in report.variables
    assert Path(report.stub_files["pyanalyzer.prelude"]).name == "prelude.pyi"
