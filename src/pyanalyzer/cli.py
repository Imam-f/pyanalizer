"""Command-line interface and human-readable report renderer."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .analyzer import analyze_file
from .model import AnalysisReport, FunctionReport, PropReport, TypeReport


class _Palette:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    BLUE = "\x1b[34m"
    CYAN = "\x1b[36m"

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def paint(self, value: object, *styles: str) -> str:
        text = str(value)
        if not self.enabled:
            return text
        return f"{''.join(styles)}{text}{self.RESET}"

    def heading(self, value: object) -> str:
        return self.paint(value, self.BOLD, self.CYAN)

    def keyword(self, value: object) -> str:
        return self.paint(value, self.BLUE)

    def name(self, value: object) -> str:
        return self.paint(value, self.BOLD)

    def type(self, value: object) -> str:
        return self.paint(value, self.GREEN)

    def detail(self, value: object) -> str:
        return self.paint(value, self.DIM)

    def predicate(self, value: object) -> str:
        return self.paint(value, self.YELLOW)

    def error(self, value: object) -> str:
        return self.paint(value, self.BOLD, self.RED)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyanalyzer",
        description="Statically inspect Python types, dict shapes, callables, and closure captures.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="Python files to analyze")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colorize text output (default: auto)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reports: list[AnalysisReport] = []
    failed = False
    for path in args.paths:
        if not path.is_file():
            print(f"pyanalyzer: file not found: {path}", file=sys.stderr)
            failed = True
            continue
        report = analyze_file(path)
        reports.append(report)
        failed |= bool(report.errors)

    if args.json:
        payload = reports[0].to_dict() if len(reports) == 1 else [r.to_dict() for r in reports]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        color = _color_enabled(args.color)
        print("\n\n".join(_render(report, color=color) for report in reports))
    return 1 if failed else 0


def _color_enabled(mode: str) -> bool:
    if mode == "never" or "NO_COLOR" in os.environ:
        return False
    if mode == "always":
        return True
    return sys.stdout.isatty() or "FORCE_COLOR" in os.environ


def _type_line(name: str, value: TypeReport, colors: _Palette, indent: str = "  ") -> str:
    observed = ", ".join(value.observed)
    locations = ", ".join(map(str, value.locations))
    evidence = colors.detail(f"observed: {observed}; lines: {locations}")
    return f"{indent}{colors.name(name)}: {colors.type(value.inferred)}  [{evidence}]"


def _render_function(function: FunctionReport, colors: _Palette, indent: str = "") -> list[str]:
    prefix = f"{indent}  "
    detail = f"{colors.keyword('function')} {colors.name(function.qualified_name)}"
    lines = [f"{indent}{detail}: {colors.type(function.signature)}"]

    if function.props:
        _add_group(lines, [f"{prefix}{colors.heading('local guarantees')}"])
        for prop in function.props:
            lines.extend(_render_prop(prop, colors, prefix + "  "))

    if function.variables:
        group = [f"{prefix}{colors.heading('locals')}"]
        group.extend(_type_line(name, value, colors, prefix + "  ") for name, value in function.variables.items())
        _add_group(lines, group)

    if function.exceptions:
        group = [f"{prefix}{colors.heading('raises')}"]
        group.extend(f"{prefix}  {colors.error(exception)}" for exception in function.exceptions)
        _add_group(lines, group)

    if function.dicts:
        group = [f"{prefix}{colors.heading('dictionaries')}"]
        for mapping in function.dicts.values():
            keys = ", ".join(f"{key}: {colors.type(value)}" for key, value in mapping.keys.items()) or "(none)"
            group.append(f"{prefix}  {colors.name(mapping.name)}: {{{keys}}}")
            if mapping.dynamic_keys:
                group.append(f"{prefix}    {colors.detail('dynamic keys')}: {', '.join(mapping.dynamic_keys)}")
        _add_group(lines, group)

    if function.captures:
        group = [f"{prefix}{colors.heading('captures')}"]
        for capture in function.captures:
            users = ", ".join(capture.captured_by)
            state = "mutable" if capture.mutable else "read-only"
            group.append(
                f"{prefix}  {colors.name(capture.name)}: {colors.keyword(capture.kind)}, "
                f"{colors.predicate(state)} -> {users}"
            )
        _add_group(lines, group)

    if function.super_calls:
        group = [f"{prefix}{colors.heading('super traversal')}"]
        group.extend(f"{prefix}  {colors.keyword('super')} -> {colors.name(target)}" for target in function.super_calls)
        _add_group(lines, group)

    if function.refinements:
        group = [f"{prefix}{colors.heading('refinements')}"]
        for refinement in function.refinements:
            branches = []
            if refinement.when_true:
                branches.append(f"{colors.keyword('true')}: {colors.predicate(refinement.when_true)}")
            if refinement.when_false:
                branches.append(f"{colors.keyword('false')}: {colors.predicate(refinement.when_false)}")
            location = colors.detail(f"line {refinement.line}, {refinement.condition}")
            group.append(
                f"{prefix}  {colors.name(refinement.variable)} with {colors.name(refinement.checker)} "
                f"[{location}]"
            )
            group.append(f"{prefix}    {'; '.join(branches)}")
        _add_group(lines, group)

    if function.nested_functions:
        group = [f"{prefix}{colors.heading('nested functions')}"]
        for index, nested in enumerate(function.nested_functions):
            if index:
                group.append("")
            group.extend(_render_function(nested, colors, prefix + "  "))
        _add_group(lines, group)
    return lines


def _render_prop(prop: PropReport, colors: _Palette, indent: str = "") -> list[str]:
    signature = (
        f"{colors.keyword('guarantee')} {colors.name(prop.qualified_name)}"
        f"({prop.parameter}: {colors.type(prop.parameter_type)}) -> {colors.type(prop.returns)}"
    )
    lines = [f"{indent}{signature}"]
    lines.append(f"{indent}  {colors.detail('constructs')}: {colors.type(prop.constructed_type)}")
    if prop.predicate:
        lines.append(f"{indent}  {colors.detail('predicate')}: {colors.predicate(prop.predicate)}")
    if prop.exceptions:
        lines.append(
            f"{indent}  {colors.detail('raises')}: "
            + " | ".join(colors.error(exception) for exception in prop.exceptions)
        )
    return lines


def _render(report: AnalysisReport, *, color: bool = False) -> str:
    colors = _Palette(color)
    blocks: list[list[str]] = [[colors.heading(report.filename)]]
    if report.errors:
        blocks.append([f"  {colors.error('error')}: {error}" for error in report.errors])
        return "\n\n".join("\n".join(block) for block in blocks)

    if report.variables:
        block = [colors.heading("variables")]
        block.extend(_type_line(name, value, colors) for name, value in report.variables.items())
        blocks.append(block)

    if report.dicts:
        block = [colors.heading("dictionaries")]
        for mapping in report.dicts.values():
            keys = ", ".join(f"{key}: {colors.type(value)}" for key, value in mapping.keys.items()) or "(none)"
            block.append(f"  {colors.name(mapping.name)}: {{{keys}}}")
            if mapping.dynamic_keys:
                block.append(f"    {colors.detail('dynamic keys')}: {', '.join(mapping.dynamic_keys)}")
        blocks.append(block)

    if report.props:
        block = [colors.heading("guarantees")]
        for index, prop in enumerate(report.props):
            if index:
                block.append("")
            block.extend(_render_prop(prop, colors, "  "))
        blocks.append(block)

    if report.functions:
        block = [colors.heading("functions")]
        for index, function in enumerate(report.functions):
            if index:
                block.append("")
            block.extend(_render_function(function, colors, "  "))
        blocks.append(block)

    if report.classes:
        block = [colors.heading("classes")]
        for class_index, class_ in enumerate(report.classes):
            if class_index:
                block.append("")
            bases = f"({', '.join(class_.bases)})" if class_.bases else ""
            block.append(f"  {colors.keyword('class')} {colors.name(class_.qualified_name)}{bases}")
            if class_.class_variables:
                block.append(f"    {colors.heading('class variables')}")
                block.extend(
                    _type_line(name, value, colors, "      ") for name, value in class_.class_variables.items()
                )
            if class_.instance_attributes:
                block.append(f"    {colors.heading('instance attributes')}")
                block.extend(
                    _type_line(name, value, colors, "      ") for name, value in class_.instance_attributes.items()
                )
            if class_.methods:
                block.append("")
                block.append(f"    {colors.heading('methods')}")
                for method_index, method in enumerate(class_.methods):
                    if method_index:
                        block.append("")
                    block.extend(_render_function(method, colors, "      "))
        blocks.append(block)

    if len(blocks) == 1:
        blocks.append([f"  {colors.detail('(no analyzable definitions or assignments)')}"])
    return "\n\n".join("\n".join(block) for block in blocks)


def _add_group(lines: list[str], group: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")
    lines.extend(group)


if __name__ == "__main__":
    raise SystemExit(main())
