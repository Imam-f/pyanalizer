"""Run normally, or analyze with: uv run pyanalyzer --use-stubs examples/prelude_demo.py"""

from pyanalyzer.prelude import (
    atoi, c_float, c_uint, malloc, printf, sizeof, sqrt, strcat, strcpy, strlen,
)

wrapped = c_uint(-1)
single_precision = c_float(0.1)
parsed = atoi("  -42 trailing text")
root = sqrt(9)
width = sizeof(c_uint)

buffer = malloc(32)
strcpy(buffer, b"Hello")
strcat(buffer, b", C!")
length = strlen(buffer)

if __name__ == "__main__":
    printf("%s (%d bytes)\n", buffer[:length].decode("ascii"), length)
    printf("uint=%d, width=%d, parsed=%d, sqrt=%.1f\n", wrapped, width, parsed, root)
