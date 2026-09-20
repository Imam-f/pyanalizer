from __future__ import annotations

from textwrap import dedent

from pyanalyzer import analyze_source
from pyanalyzer.gui import build_symbol_tree


def test_symbol_tree_exposes_hover_properties_and_nested_symbols() -> None:
    report = analyze_source(
        dedent(
            """
            config = {"port": 8000}

            def connect(host: str):
                attempts = 2
                return host

            class Client:
                timeout = 10

                def __init__(self, name: str):
                    self.name = name
            """
        )
    )

    symbols = build_symbol_tree(report)
    by_name = {symbol.name: symbol for symbol in symbols}

    config = by_name["config"]
    assert ("Dictionary keys", "'port': int") in config.properties
    assert config.line == 2

    connect_children = {child.name: child for child in by_name["connect"].children}
    assert connect_children["host"].kind == "parameter"
    assert connect_children["attempts"].type_name == "int"

    class_children = {child.name: child for child in by_name["Client"].children}
    assert class_children["timeout"].kind == "class attribute"
    assert class_children["name"].kind == "instance attribute"
