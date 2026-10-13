"""Typed Python approximations of C scalars and common library functions.

Integer construction wraps to the native C width; subsequent arithmetic uses
Python's ordinary numeric rules. Cast the result to wrap it again. Scalars are
immutable and expose a read-only ``value``. Buffers are bounds-checked bytearrays,
not native pointers. Floating-point math uses Python double precision.
"""

from __future__ import annotations

import builtins
import ctypes
import math
import re
import sys
from typing import ClassVar, Self, SupportsFloat, SupportsInt, TypeAlias

__all__ = [
    "c_bool", "c_char", "c_wchar", "c_byte", "c_ubyte", "c_short", "c_ushort",
    "c_int", "c_uint", "c_long", "c_ulong", "c_longlong", "c_ulonglong",
    "c_size_t", "c_ssize_t", "c_float", "c_double", "c_longdouble",
    "int8_t", "uint8_t", "int16_t", "uint16_t", "int32_t", "uint32_t",
    "int64_t", "uint64_t", "size_t", "ptrdiff_t", "EOF", "sizeof",
    "abs", "labs", "llabs", "atoi", "atol", "atoll", "atof",
    "malloc", "calloc", "realloc", "free", "memcpy", "memmove", "memset",
    "memcmp", "strlen", "strcmp", "strncmp", "strcpy", "strncpy", "strcat",
    "sqrt", "sin", "cos", "tan", "exp", "log", "log10", "pow", "fabs",
    "floor", "ceil", "printf", "puts", "putchar",
]


class _CInteger(int):
    BITS: ClassVar[int] = 32
    SIGNED: ClassVar[bool] = True

    def __new__(cls: type[Self], value: SupportsInt | str | bytes = 0) -> Self:
        number = int(value) % (1 << cls.BITS)
        if cls.SIGNED and number >= (1 << (cls.BITS - 1)):
            number -= 1 << cls.BITS
        return int.__new__(cls, number)

    @property
    def value(self) -> int:
        return int(self)


class c_bool(_CInteger):
    BITS = ctypes.sizeof(ctypes.c_bool) * 8
    SIGNED = False

    def __new__(cls: type[Self], value: SupportsInt | str | bytes = 0) -> Self:
        return int.__new__(cls, bool(value))

    @property
    def value(self) -> bool:
        return bool(self)


class c_byte(_CInteger):
    BITS = ctypes.sizeof(ctypes.c_byte) * 8


class c_ubyte(c_byte):
    SIGNED = False


class c_short(_CInteger):
    BITS = ctypes.sizeof(ctypes.c_short) * 8


class c_ushort(c_short):
    SIGNED = False


class c_int(_CInteger):
    BITS = ctypes.sizeof(ctypes.c_int) * 8


class c_uint(c_int):
    SIGNED = False


class c_long(_CInteger):
    BITS = ctypes.sizeof(ctypes.c_long) * 8


class c_ulong(c_long):
    SIGNED = False


class c_longlong(_CInteger):
    BITS = ctypes.sizeof(ctypes.c_longlong) * 8


class c_ulonglong(c_longlong):
    SIGNED = False


class c_size_t(_CInteger):
    BITS = ctypes.sizeof(ctypes.c_size_t) * 8
    SIGNED = False


class c_ssize_t(_CInteger):
    BITS = ctypes.sizeof(ctypes.c_ssize_t) * 8


class int8_t(_CInteger):
    BITS = 8


class uint8_t(int8_t):
    SIGNED = False


class int16_t(_CInteger):
    BITS = 16


class uint16_t(int16_t):
    SIGNED = False


class int32_t(_CInteger):
    BITS = 32


class uint32_t(int32_t):
    SIGNED = False


class int64_t(_CInteger):
    BITS = 64


class uint64_t(int64_t):
    SIGNED = False


size_t: TypeAlias = c_size_t
ptrdiff_t: TypeAlias = c_ssize_t


class c_char(bytes):
    def __new__(cls: type[Self], value: bytes = b"\0") -> Self:
        if len(value) != 1:
            raise ValueError("c_char requires exactly one byte")
        return bytes.__new__(cls, value)

    @property
    def value(self) -> bytes:
        return bytes(self)


class c_wchar(str):
    def __new__(cls: type[Self], value: str = "\0") -> Self:
        if len(value) != 1:
            raise ValueError("c_wchar requires exactly one character")
        return str.__new__(cls, value)

    @property
    def value(self) -> str:
        return str(self)


class c_float(float):
    def __new__(cls: type[Self], value: SupportsFloat | str = 0.0) -> Self:
        return float.__new__(cls, ctypes.c_float(float(value)).value)

    @property
    def value(self) -> float:
        return float(self)


class c_double(float):
    def __new__(cls: type[Self], value: SupportsFloat | str = 0.0) -> Self:
        return float.__new__(cls, value)

    @property
    def value(self) -> float:
        return float(self)


class c_longdouble(c_double):
    """Native long-double size, but Python double precision at runtime."""


EOF: int = -1


def sizeof(value: type[_CInteger] | _CInteger | type[c_char] | c_char |
           type[c_wchar] | c_wchar | type[c_float] | c_float |
           type[c_double] | c_double | bytes | bytearray) -> c_size_t:
    """Return native scalar storage size or a byte buffer's current length."""
    kind = value if isinstance(value, type) else type(value)
    if issubclass(kind, _CInteger):
        return c_size_t(kind.BITS // 8)
    for scalar, native in (
        (c_char, ctypes.c_char), (c_wchar, ctypes.c_wchar),
        (c_float, ctypes.c_float), (c_longdouble, ctypes.c_longdouble),
        (c_double, ctypes.c_double),
    ):
        if issubclass(kind, scalar):
            return c_size_t(ctypes.sizeof(native))
    if isinstance(value, (bytes, bytearray)):
        return c_size_t(len(value))
    raise TypeError("sizeof requires a C scalar or byte buffer")


def abs(value: SupportsInt) -> c_int:
    return c_int(builtins.abs(int(value)))


def labs(value: SupportsInt) -> c_long:
    return c_long(builtins.abs(int(value)))


def llabs(value: SupportsInt) -> c_longlong:
    return c_longlong(builtins.abs(int(value)))


def _text(value: str | bytes) -> str:
    return value.decode("ascii", errors="replace") if isinstance(value, bytes) else value


def _integer_prefix(value: str | bytes) -> int:
    match = re.match(r"^[\t\n\v\f\r ]*([+-]?[0-9]+)", _text(value))
    return int(match.group(1)) if match else 0


def atoi(value: str | bytes) -> c_int:
    return c_int(_integer_prefix(value))


def atol(value: str | bytes) -> c_long:
    return c_long(_integer_prefix(value))


def atoll(value: str | bytes) -> c_longlong:
    return c_longlong(_integer_prefix(value))


def atof(value: str | bytes) -> c_double:
    match = re.match(
        r"^[\t\n\v\f\r ]*([+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
        r"(?:[eE][+-]?[0-9]+)?|inf(?:inity)?|nan))", _text(value), re.IGNORECASE,
    )
    return c_double(match.group(1)) if match else c_double(0)


def _count(value: int) -> int:
    if value < 0:
        raise ValueError("size must be non-negative")
    return value


def _bounds(buffer: bytes | bytearray, count: int) -> None:
    if _count(count) > len(buffer):
        raise ValueError("operation exceeds buffer capacity")


def malloc(size: int) -> bytearray:
    """Allocate a zero-filled Python buffer (unlike uninitialized C malloc)."""
    return bytearray(_count(size))


def calloc(count: int, size: int) -> bytearray:
    return malloc(_count(count) * _count(size))


def realloc(buffer: bytearray, size: int) -> bytearray:
    """Resize in place, preserving the prefix and zero-filling new bytes."""
    _count(size)
    if size < len(buffer):
        del buffer[size:]
    else:
        buffer.extend(b"\0" * (size - len(buffer)))
    return buffer


def free(buffer: bytearray | None) -> None:
    """Clear a Python buffer; None is accepted like a C null pointer."""
    if buffer is not None:
        buffer.clear()


def memcpy(destination: bytearray, source: bytes | bytearray, count: int) -> bytearray:
    _bounds(destination, count)
    _bounds(source, count)
    destination[:count] = source[:count]
    return destination


def memmove(destination: bytearray, source: bytes | bytearray, count: int) -> bytearray:
    """Copy through a snapshot, including when source is destination."""
    return memcpy(destination, bytes(source), count)


def memset(destination: bytearray, value: int, count: int) -> bytearray:
    _bounds(destination, count)
    destination[:count] = bytes([value & 0xff]) * count
    return destination


def memcmp(left: bytes | bytearray, right: bytes | bytearray, count: int) -> c_int:
    _bounds(left, count)
    _bounds(right, count)
    return c_int((left[:count] > right[:count]) - (left[:count] < right[:count]))


def _cstring(value: bytes | bytearray) -> bytes:
    return bytes(value).split(b"\0", 1)[0]


def strlen(value: bytes | bytearray) -> c_size_t:
    return c_size_t(len(_cstring(value)))


def strcmp(left: bytes | bytearray, right: bytes | bytearray) -> c_int:
    first, second = _cstring(left), _cstring(right)
    return c_int((first > second) - (first < second))


def strncmp(left: bytes | bytearray, right: bytes | bytearray, count: int) -> c_int:
    _count(count)
    return strcmp(_cstring(left)[:count], _cstring(right)[:count])


def strcpy(destination: bytearray, source: bytes | bytearray) -> bytearray:
    text = _cstring(source) + b"\0"
    return memcpy(destination, text, len(text))


def strncpy(destination: bytearray, source: bytes | bytearray, count: int) -> bytearray:
    """Copy exactly count bytes, padding with NUL; truncation adds no NUL."""
    _bounds(destination, count)
    text = _cstring(source)[:count].ljust(count, b"\0")
    return memcpy(destination, text, count)


def strcat(destination: bytearray, source: bytes | bytearray) -> bytearray:
    prefix = _cstring(destination)
    text = prefix + _cstring(source) + b"\0"
    return memcpy(destination, text, len(text))


def sqrt(value: SupportsFloat) -> c_double:
    return c_double(math.sqrt(float(value)))


def sin(value: SupportsFloat) -> c_double:
    return c_double(math.sin(float(value)))


def cos(value: SupportsFloat) -> c_double:
    return c_double(math.cos(float(value)))


def tan(value: SupportsFloat) -> c_double:
    return c_double(math.tan(float(value)))


def exp(value: SupportsFloat) -> c_double:
    return c_double(math.exp(float(value)))


def log(value: SupportsFloat) -> c_double:
    return c_double(math.log(float(value)))


def log10(value: SupportsFloat) -> c_double:
    return c_double(math.log10(float(value)))


def pow(base: SupportsFloat, exponent: SupportsFloat) -> c_double:
    return c_double(math.pow(float(base), float(exponent)))


def fabs(value: SupportsFloat) -> c_double:
    return c_double(math.fabs(float(value)))


def floor(value: SupportsFloat) -> c_double:
    return c_double(math.floor(float(value)))


def ceil(value: SupportsFloat) -> c_double:
    return c_double(math.ceil(float(value)))


def printf(format: str, *values: str | int | float) -> c_int:
    """Write Python %-formatted text; C length modifiers are unsupported.

    The return value counts UTF-8 bytes. Python stream errors propagate.
    """
    text = format % values
    sys.stdout.write(text)
    return c_int(len(text.encode("utf-8")))


def puts(value: str | bytes) -> c_int:
    """Write UTF-8 text up to the first NUL and append a newline."""
    text = value.decode("utf-8") if isinstance(value, bytes) else value
    text = text.split("\0", 1)[0] + "\n"
    sys.stdout.write(text)
    return c_int(len(text.encode("utf-8")))


def putchar(value: int) -> c_int:
    """Write the low byte as a Unicode character and return that byte."""
    byte = value & 0xff
    sys.stdout.write(chr(byte))
    return c_int(byte)
