"""Lower a conservative subset of Cython syntax to the analyzer's Python AST.

This is a source reader, not a compiler. Token boundaries protect comments and
strings, and replacements retain line counts for reports and diagnostics.
Unsupported C/C++ constructs raise SyntaxError rather than disappearing.
"""

from __future__ import annotations

import ast
import io
import re
import token
import tokenize


CYTHON_SUFFIXES = {".pyx", ".pxd", ".pxi"}
CYTHON_KEYWORDS = {
    "cdef", "cpdef", "ctypedef", "cimport", "nogil", "gil", "public",
    "readonly", "extern", "inline", "bint", "double", "char", "short",
    "long", "unsigned", "signed", "void", "sizeof",
}

Tokens = list[tokenize.TokenInfo]


def _render(tokens: Tokens) -> str:
    return tokenize.untokenize([(item.type, item.string) for item in tokens]).strip()


def _type_text(tokens: Tokens) -> str:
    return re.sub(r"\s*([.\[\],:*&])\s*", r"\1", _render(tokens))


def _top_indices(tokens: Tokens):
    depth = 0
    for index, item in enumerate(tokens):
        if depth == 0:
            yield index
        if item.string in {"(", "[", "{"}:
            depth += 1
        elif item.string in {")", "]", "}"}:
            depth -= 1


def _find(tokens: Tokens, value: str) -> int | None:
    return next((index for index in _top_indices(tokens) if tokens[index].string == value), None)


def _split(tokens: Tokens, separator: str) -> list[Tokens]:
    pieces = []
    start = 0
    for index in _top_indices(tokens):
        if tokens[index].string == separator:
            pieces.append(tokens[start:index])
            start = index + 1
    pieces.append(tokens[start:])
    return pieces


class _Reader:
    def __init__(self, source: str, filename: str) -> None:
        self.source = source
        self.filename = filename
        self.declarations: set[int] = set()
        self.blocks: set[int] = set()
        self.extension_classes: set[int] = set()

    def error(self, tokens: Tokens, message: str) -> SyntaxError:
        item = tokens[0]
        return SyntaxError(
            message,
            (self.filename, item.start[0], item.start[1] + 1, item.line),
        )

    def declaration(self, tokens: Tokens, *, parameter: bool = False) -> str:
        pieces = _split(tokens, ",")
        declarations = []
        base: Tokens = []
        for index, piece in enumerate(pieces):
            if not piece:
                raise self.error(tokens, "Incomplete Cython declaration")
            equals = _find(piece, "=")
            head = piece if equals is None else piece[:equals]
            names = [i for i in _top_indices(head) if head[i].type == token.NAME]
            if not names:
                raise self.error(tokens, "Unsupported Cython declarator")
            name_index = names[-1]
            prefix, suffix = head[:name_index], head[name_index + 1:]
            if any(item.string not in {"*", "&"} for item in prefix) and index:
                raise self.error(tokens, "Unsupported mixed Cython declaration")
            if index == 0:
                base = prefix.copy()
                while base and base[-1].string in {"*", "&"}:
                    base.pop()
            type_tokens = prefix if index == 0 else base + prefix
            if parameter and not type_tokens and not suffix:
                declarations.append(_render(piece))
                continue
            if not type_tokens or (suffix and suffix[0].string != "["):
                raise self.error(tokens, "Unsupported Cython declarator")
            annotation = _type_text(type_tokens + suffix)
            if not annotation:
                raise self.error(tokens, "Missing Cython type")
            value = ""
            if equals is not None:
                default = piece[equals + 1:]
                if not default:
                    raise self.error(tokens, "Missing Cython initializer")
                value = " = ..." if parameter and _render(default) == "*" else " = " + _render(default)
            declarations.append(f"{head[name_index].string}: {annotation!r}{value}")
        return (", " if parameter else "; ").join(declarations)

    def function(self, tokens: Tokens, *, python_def: bool = False) -> str:
        opening = _find(tokens, "(")
        if opening is None or opening == 0 or tokens[opening - 1].type != token.NAME:
            raise self.error(tokens, "Unsupported Cython function declarator")
        depth = 1
        closing = opening + 1
        while closing < len(tokens) and depth:
            if tokens[closing].string == "(":
                depth += 1
            elif tokens[closing].string == ")":
                depth -= 1
            if depth:
                closing += 1
        if depth:
            raise self.error(tokens, "Unclosed Cython parameter list")
        parameters = []
        for parameter in _split(tokens[opening + 1:closing], ","):
            if not parameter:
                continue
            # Ordinary Python annotations, *args, and keyword-only markers.
            if _find(parameter, ":") is not None or parameter[0].string in {"*", "**", "/"}:
                parameters.append(_render(parameter))
            else:
                parameters.append(self.declaration(parameter, parameter=True))
        suffix = tokens[closing + 1:]
        colon = _find(suffix, ":")
        modifiers = suffix if colon is None else suffix[:colon]
        result_type = tokens[:opening - 1]
        if python_def:
            if modifiers and modifiers[0].string == "->":
                result_type = modifiers[1:]
                modifiers = []
            else:
                result_type = []
        # Exception specifications control the C ABI, not Python exception effects.
        modifier_text = _render(modifiers)
        if modifier_text and not re.fullmatch(
            r"(?:(?:except\s*(?:\?\s*)?(?:\*|[-+]?\s*\d+)|noexcept|nogil|gil)\s*)+",
            modifier_text,
        ):
            raise self.error(modifiers, "Unsupported Cython function modifier")
        returns = f" -> {_type_text(result_type)!r}" if result_type else ("" if python_def else " -> 'object'")
        body = "..." if colon is None else _render(suffix[colon + 1:])
        return f"def {tokens[opening - 1].string}({', '.join(parameters)}){returns}: {body}"

    def statement(self, tokens: Tokens, *, declaration_block: bool = False) -> tuple[str | None, bool]:
        first = tokens[0].string
        if first == "cimport" or (first == "from" and any(t.string == "cimport" for t in tokens)):
            return _render([
                item._replace(string="import") if item.string == "cimport" else item
                for item in tokens
            ]), False
        if first == "include":
            raise self.error(tokens, "Cython include expansion is not supported; analyze the .pxi file directly")
        if first == "with" and len(tokens) == 3 and tokens[1].string in {"gil", "nogil"}:
            self.blocks.add(tokens[0].start[0])
            return "if True:", False
        if first in {"cdef", "cpdef", "ctypedef"}:
            rest = tokens[1:]
            while rest and rest[0].string in {"public", "readonly", "api", "inline"}:
                rest = rest[1:]
            if not rest:
                raise self.error(tokens, "Incomplete Cython declaration")
            if rest[0].string == "class" and first == "cdef":
                self.extension_classes.add(tokens[0].start[0])
                return _render(rest), False
            if rest[0].string == ":" or rest[0].string == "extern":
                if rest[-1].string != ":":
                    raise self.error(tokens, "Unsupported Cython declaration block")
                self.blocks.add(tokens[0].start[0])
                return "if True:", True
            if rest[0].string in {"struct", "union", "enum", "cppclass", "fused", "packed"}:
                raise self.error(tokens, f"Unsupported Cython {rest[0].string} declaration")
            opening = _find(rest, "(")
            equals = _find(rest, "=")
            if opening is not None and (equals is None or opening < equals):
                if first == "ctypedef":
                    raise self.error(tokens, "Unsupported Cython function type alias")
                return self.function(rest), False
            if first == "ctypedef":
                # Keep the alias name as evidence without resolving external types.
                declaration = self.declaration(rest)
                if ";" in declaration or " = " in declaration:
                    raise self.error(tokens, "Unsupported Cython type alias")
                name, annotation = declaration.split(": ", 1)
                alias_type = f"type[{ast.literal_eval(annotation)}]"
                return f"{name}: {alias_type!r}", False
            self.declarations.add(tokens[0].start[0])
            return self.declaration(rest), False
        if first == "def":
            return self.function(tokens[1:], python_def=True), False
        if declaration_block:
            if _find(tokens, "(") is not None:
                return self.function(tokens), False
            self.declarations.add(tokens[0].start[0])
            return self.declaration(tokens), False
        return None, False

    def parse(self) -> ast.Module:
        lines = self.source.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))

        def offset(position: tuple[int, int]) -> int:
            return offsets[position[0] - 1] + position[1]

        replacements: list[tuple[int, int, str]] = []
        contexts = [False]
        pending_context = False
        statement: Tokens = []
        try:
            for item in tokenize.generate_tokens(io.StringIO(self.source).readline):
                if item.type == token.INDENT:
                    contexts.append(pending_context)
                    pending_context = False
                elif item.type == token.DEDENT:
                    contexts.pop()
                elif item.type in {token.NEWLINE, token.ENDMARKER}:
                    if not statement:
                        continue
                    replacement, pending_context = self.statement(statement, declaration_block=contexts[-1])
                    if replacement is not None:
                        start, end = offset(statement[0].start), offset(statement[-1].end)
                        old = self.source[start:end]
                        replacement += "\n" * (old.count("\n") - replacement.count("\n"))
                        replacements.append((start, end, replacement))
                    statement = []
                elif item.type not in {tokenize.NL, token.COMMENT, token.ENCODING}:
                    statement.append(item)
        except tokenize.TokenError as exc:
            message, (line, column) = exc.args
            raise SyntaxError(message, (self.filename, line, column + 1, "")) from exc
        normalized = self.source
        for start, end, replacement in reversed(replacements):
            normalized = normalized[:start] + replacement + normalized[end:]
        tree = ast.parse(normalized, filename=self.filename, type_comments=True)
        reader = self

        class Restore(ast.NodeTransformer):
            def visit_If(self, node: ast.If):
                self.generic_visit(node)
                if node.lineno in reader.blocks:
                    return node.body
                return node

            def visit_AnnAssign(self, node: ast.AnnAssign):
                if node.lineno in reader.declarations:
                    node.cython_declaration = True
                return node

            def visit_ClassDef(self, node: ast.ClassDef):
                self.generic_visit(node)
                node.cython_extension = node.lineno in reader.extension_classes
                return node

        return Restore().visit(tree)


def parse_cython(source: str, filename: str) -> ast.Module:
    return _Reader(source, filename).parse()
