from __future__ import annotations

from textwrap import dedent

import pytest

from pyanalyzer import Guarantee, analyze_source, guarantee, join, prop
from pyanalyzer.cli import _render


# --- API and rendering ------------------------------------------------------

def test_guarantee_is_a_typed_runtime_identity() -> None:
    @guarantee
    def positive(value: int) -> int:
        return value

    value = object()
    assert isinstance(positive, Guarantee)
    assert positive(value) is value
    assert positive.__pyanalyzer_prop__ is True
    assert positive.__pyanalyzer_guarantee__ is True
    assert join(value, (positive,)) is value
    assert prop is guarantee


def test_asserting_guarantees_perform_real_runtime_checks() -> None:
    @guarantee
    def positive(value: int) -> int:
        assert value > 0, "value must be positive"
        return value

    @guarantee
    def odd(value: int) -> int:
        assert value % 2 != 0, "value must be odd"
        return value

    assert positive(3) == 3
    assert odd(3) == 3
    assert join(3, (positive, odd)) == 3
    with pytest.raises(AssertionError, match="positive"):
        positive(-1)
    with pytest.raises(AssertionError, match="odd"):
        join(2, (positive, odd))


def test_validating_guarantee_exceptions_bubble_to_returning_function() -> None:
    report = analyze_source(
        dedent(
            """
            from typing import Annotated

            @guarantee
            def positive(value: int) -> int:
                assert value > 0
                return value

            def checked(value: int) -> Annotated[int, positive]:
                return positive(value)

            def checked_or_zero(value: int) -> int:
                try:
                    return positive(value)
                except AssertionError:
                    return 0
            """
        )
    )

    assert report.props[0].predicate == "value > 0"
    assert report.props[0].exceptions == ["AssertionError"]
    assert report.functions[0].signature == "Callable[[int], Refined[int, positive]] raises AssertionError"
    assert report.functions[1].exceptions == []


def test_text_renderer_has_spacing_and_optional_color() -> None:
    report = analyze_source(
        dedent(
            """
            @guarantee
            def positive(value: int) -> int:
                return value

            def use(value: int):
                return positive(value)
            """
        ),
        filename="sample.py",
    )

    plain = _render(report, color=False)
    colored = _render(report, color=True)
    assert "sample.py\n\nguarantees\n" in plain
    assert "\n\nfunctions\n" in plain
    assert "constructs: Refined[int, positive]" in plain
    assert "\x1b[" not in plain
    assert "\x1b[" in colored


# --- Type inference and data structures ------------------------------------

def test_literals_unions_promotion_and_dict_keys() -> None:
    report = analyze_source(
        dedent(
            """
            value = 1
            if flag:
                value = "one"
            ratio = 1 + 2.5
            config = {"host": "localhost", "port": 8000}
            config["debug"] = True
            config[key] = 3
            """
        )
    )

    assert report.variables["value"].inferred == "Union[int, str]"
    assert report.variables["value"].observed == ["Literal[1]", "Literal['one']"]
    assert report.variables["ratio"].inferred == "float"
    assert report.dicts["config"].keys == {
        "'host'": "str",
        "'port'": "int",
        "'debug'": "bool",
    }
    assert report.dicts["config"].dynamic_keys == ["key"]

    alias = analyze_source("from typing import Literal\nMode = Literal['fast', 'safe']")
    assert alias.variables["Mode"].inferred == "Literal['fast', 'safe']"


def test_callable_class_and_attributes() -> None:
    report = analyze_source(
        dedent(
            """
            def convert(value: int) -> str:
                return str(value)

            callback = convert

            class Record:
                category = "event"

                def __init__(self, name: str):
                    self.name = name
                    self.tags = []
            """
        )
    )

    assert report.variables["callback"].inferred == "Callable[[int], str]"
    assert report.functions[0].signature == "Callable[[int], str]"
    record = report.classes[0]
    assert record.class_variables["category"].inferred == "str"
    assert record.instance_attributes["name"].inferred == "str"
    assert record.instance_attributes["tags"].inferred == "list[Unknown]"
    assert record.methods[0].parameters["self"] == "Record"


def test_repeated_dict_key_becomes_union() -> None:
    report = analyze_source("data = {'value': 1}\ndata['value'] = 'later'")
    assert report.dicts["data"].keys["'value'"] == "Union[int, str]"


def test_single_shared_and_mutable_captures() -> None:
    report = analyze_source(
        dedent(
            """
            def outer():
                counter = 0
                items = []
                label = "result"

                def increment():
                    nonlocal counter
                    counter += 1
                    items.append(counter)
                    return label

                def size():
                    return len(items)

                return increment, size
            """
        )
    )

    captures = {capture.name: capture for capture in report.functions[0].captures}
    assert captures["counter"].kind == "single"
    assert captures["counter"].mutable is True
    assert captures["items"].kind == "shared"
    assert captures["items"].mutable is True
    assert captures["label"].kind == "single"
    assert captures["label"].mutable is False


def test_annotations_and_syntax_errors() -> None:
    report = analyze_source("choice: int | str = 1")
    assert report.variables["choice"].inferred == "int | str"

    broken = analyze_source("def nope(", filename="broken.py")
    assert broken.errors


# --- Inheritance and refinements -------------------------------------------

def test_super_calls_traverse_base_methods() -> None:
    report = analyze_source(
        dedent(
            """
            class Base:
                def __init__(self, identifier: int):
                    self.identifier = identifier

                def title(self) -> str:
                    return "base"

                def unrelated(self):
                    self.not_initialized = True

            class Child(Base):
                def __init__(self, identifier: int):
                    super().__init__(identifier)
                    self.active = True

                def title(self):
                    return super().title()
            """
        )
    )

    child = next(class_ for class_ in report.classes if class_.name == "Child")
    assert set(child.instance_attributes) == {"identifier", "active", "not_initialized"}
    init = next(method for method in child.methods if method.name == "__init__")
    title = next(method for method in child.methods if method.name == "title")
    assert init.super_calls == ["Base.__init__"]
    assert title.super_calls == ["Base.title"]
    assert title.returns == "str"


def test_guarantees_refine_identity_transform_results_and_annotations() -> None:
    report = analyze_source(
        dedent(
            """
            from typing import Annotated

            @guarantee
            def positive(value: int) -> int:
                return value

            @guarantee
            def odd(value: int) -> int:
                return value

            def inspect(number: int):
                one = positive(number)
                ordered = odd(positive(number))
                unordered = join(number, (positive, odd))

            def accepts_positive(number: Annotated[int, positive]):
                return number
            """
        )
    )

    props = {prop.name: prop for prop in report.props}
    assert props["positive"].returns == "int"
    assert props["positive"].predicate is None
    assert props["positive"].constructed_type == "Refined[int, positive]"

    inspect = report.functions[0]
    assert inspect.variables["one"].inferred == "Refined[int, positive]"
    assert inspect.variables["ordered"].inferred == "Refined[Refined[int, positive], odd]"
    assert inspect.variables["unordered"].inferred == "Refined[int, {odd, positive}]"
    assert report.functions[1].parameters["number"] == "Refined[int, positive]"


def test_join_deduplicates_and_stably_orders_guarantees() -> None:
    report = analyze_source(
        dedent(
            """
            @guarantee
            def positive(value: int) -> int:
                return value

            @guarantee
            def odd(value: int) -> int:
                return value

            value = join(1, (positive, odd, positive))
            """
        )
    )

    assert report.variables["value"].inferred == "Refined[Literal[1], {odd, positive}]"


# --- Exception effects ------------------------------------------------------

def test_exceptions_bubble_through_calls_and_catches_remove_them() -> None:
    report = analyze_source(
        dedent(
            """
            def forward():
                return leaf(False)

            def leaf(flag: bool):
                if flag:
                    raise ValueError("bad value")
                raise TypeError("bad type")

            def middle(flag: bool):
                return leaf(flag)

            def catches_one(flag: bool):
                try:
                    return middle(flag)
                except ValueError:
                    return None

            def catches_all(flag: bool):
                try:
                    return middle(flag)
                except Exception:
                    return None

            def reraises(flag: bool):
                try:
                    return leaf(flag)
                except (ValueError, TypeError):
                    raise

            def validates(value: int):
                assert value > 0
                return value
            """
        )
    )

    functions = {function.name: function for function in report.functions}
    assert functions["leaf"].exceptions == ["TypeError", "ValueError"]
    assert functions["forward"].exceptions == ["TypeError", "ValueError"]
    assert functions["middle"].exceptions == ["TypeError", "ValueError"]
    assert functions["catches_one"].exceptions == ["TypeError"]
    assert functions["catches_all"].exceptions == []
    assert functions["reraises"].exceptions == ["TypeError", "ValueError"]
    assert functions["validates"].exceptions == ["AssertionError"]
    assert functions["middle"].signature.endswith("raises TypeError | ValueError")


def test_method_super_nested_and_parameter_calls_bubble_exceptions() -> None:
    report = analyze_source(
        dedent(
            """
            class Base:
                def run(self):
                    raise LookupError("missing")

            class Child(Base):
                def run(self):
                    return super().run()

            def invoke(child: Child):
                return child.run()

            def outer():
                def inner():
                    raise RuntimeError("nested")
                return inner()
            """
        )
    )

    child = next(class_ for class_ in report.classes if class_.name == "Child")
    run = next(method for method in child.methods if method.name == "run")
    functions = {function.name: function for function in report.functions}
    assert run.exceptions == ["LookupError"]
    assert functions["invoke"].exceptions == ["LookupError"]
    assert functions["outer"].exceptions == ["RuntimeError"]


def test_constructor_effects_and_custom_exception_hierarchy() -> None:
    report = analyze_source(
        dedent(
            """
            class DomainError(ValueError):
                pass

            class Resource:
                def __init__(self):
                    raise DomainError("unavailable")

            def create():
                return Resource()

            def create_safely():
                try:
                    return Resource()
                except ValueError:
                    return None
            """
        )
    )

    functions = {function.name: function for function in report.functions}
    assert functions["create"].exceptions == ["DomainError"]
    assert functions["create_safely"].exceptions == []


# --- Mutability and class-member state -------------------------------------

def test_variables_track_mutability_and_capture_sharing() -> None:
    report = analyze_source(
        dedent(
            """
            constant = 1
            config = {}
            config["enabled"] = True

            def outer(seed: int, items: list[int]):
                counter = 0
                counter = 1
                bag = []
                stable = "name"
                items.append(seed)

                def first():
                    bag.append(seed)
                    return stable

                def second():
                    return len(bag) + seed

                return first, second
            """
        )
    )

    assert report.variables["constant"].mutability == "const"
    assert report.variables["config"].mutability == "mutable"
    outer = report.functions[0]
    assert outer.variables["counter"].mutability == "mutable"
    assert outer.variables["bag"].mutability == "mutable"
    assert outer.variables["bag"].sharing == "shared"
    assert outer.variables["stable"].mutability == "const"
    assert outer.variables["stable"].sharing == "single"
    assert outer.parameter_variables["items"].mutability == "mutable"
    assert outer.parameter_variables["seed"].mutability == "const"
    assert outer.parameter_variables["seed"].sharing == "shared"


def test_instance_attributes_track_cross_method_mutation_and_sharing() -> None:
    report = analyze_source(
        dedent(
            """
            class Store:
                def __init__(self):
                    self.items = []

                def add(self, item: int):
                    self.items.append(item)
            """
        )
    )

    items = report.classes[0].instance_attributes["items"]
    assert items.mutability == "mutable"
    assert items.sharing == "shared"
    assert items.shared_with == ["Store.__init__", "Store.add"]


def test_classes_track_member_origins_and_flatten_base_members() -> None:
    report = analyze_source(
        dedent(
            """
            class Root:
                root_setting = "root"

                def __init__(self):
                    self.root_value = 1

                def enable_root_feature(self):
                    self.root_feature = True

            class Base(Root):
                base_setting = 2

                def __init__(self):
                    self.base_value = "base"

                def configure(self):
                    self.configured = True

            class Child(Base):
                child_setting = 3.0

                def __init__(self):
                    self.child_value = []

                def attach(self):
                    self.attachment = object()
            """
        )
    )

    child = next(class_ for class_ in report.classes if class_.name == "Child")
    assert set(child.class_body_attributes) == {"child_setting"}
    assert set(child.initializer_attributes) == {"child_value"}
    assert set(child.dynamic_attributes) == {"attachment"}
    assert set(child.inherited_class_variables) == {"root_setting", "base_setting"}
    assert set(child.inherited_instance_attributes) == {
        "root_value", "root_feature", "base_value", "configured"
    }
    assert set(child.inherited_methods) == {"Root.enable_root_feature", "Base.configure"}
    assert set(child.class_variables) == {"root_setting", "base_setting", "child_setting"}
    assert set(child.instance_attributes) == {
        "root_value", "root_feature", "base_value", "configured", "child_value", "attachment"
    }
    assert {method.name for method in child.methods} == {
        "__init__", "enable_root_feature", "configure", "attach"
    }

    rendered = _render(report, color=False)
    assert "inherited members" in rendered
    assert "flattened instance attributes" in rendered
