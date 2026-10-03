# cython: language_level=3
from libc.math cimport sqrt

ctypedef unsigned long Count

cdef double checked_root(double value) except? -1:
    if value < 0:
        raise ValueError("value must be nonnegative")
    return sqrt(value)


cpdef double root(double value):
    cdef double result = checked_root(value)
    return result


def first(const double[:] values):
    return values[0]


cdef class Counter:
    cdef public int value

    def __init__(self, int value=0):
        self.value = value

    cpdef int increment(self, int amount=1):
        self.value += amount
        return self.value
