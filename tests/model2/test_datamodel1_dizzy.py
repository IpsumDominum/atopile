from unittest.mock import MagicMock

import pytest

from atopile.dev.parse import parse_as_file, parser_from_src_code
from atopile.model2 import errors
from atopile.model2.datamodel1 import (
    COMPONENT,
    INTERFACE,
    MODULE,
    PIN,
    SIGNAL,
    Dizzy,
    Import,
    Link,
    Object,
    Replace,
    KeyOptItem,
    KeyOptMap,
)

# =========================
# test individual functions
# =========================


# test Totally_an_integer
@pytest.mark.parametrize(
    "input", ["1.1", "hello", "False", "None", "True", "true", "false"]
)
def test_Totally_an_integer_errors(input):
    mock_ctx = MagicMock()
    getText = MagicMock()
    getText.return_value = input
    mock_ctx.getText = getText

    with pytest.raises(errors.AtoTypeError):
        dizzy = Dizzy(True)
        dizzy.visitTotally_an_integer(mock_ctx)


@pytest.mark.parametrize(
    ("input", "output"),
    [
        ("0", 0),
        ("1", 1),
        ("5", 5),
    ],
)
def test_Totally_an_integer_passes(input, output):
    mock_ctx = MagicMock()
    getText = MagicMock()
    getText.return_value = input
    mock_ctx.getText = getText

    dizzy = Dizzy(True)
    assert output == dizzy.visitTotally_an_integer(mock_ctx)


# test visitName
@pytest.mark.parametrize(
    ("input", "output"), [("0", 0), ("1", 1), ("5", 5), ("hello", "hello")]
)
def test_visitName(input, output):
    mock_ctx = MagicMock()
    getText = MagicMock()
    getText.return_value = input
    mock_ctx.getText = getText

    dizzy = Dizzy(True)
    assert output == dizzy.visitName(mock_ctx)


# TODO: check for a..b error at model 1 level
def test_visitAttr():
    parser = parser_from_src_code("a.b.c")
    ctx = parser.attr()

    dizzy = Dizzy(True)
    assert ("a", "b", "c") == dizzy.visitAttr(ctx)


@pytest.mark.parametrize(
    ("input", "output"),
    [
        ("a", ("a",)),
        ("a.b", ("a", "b")),
        ("a.b.c", ("a", "b", "c")),
    ],
)
def test_visitName_or_attr(input, output):
    parser = parser_from_src_code(input)
    ctx = parser.name_or_attr()

    dizzy = Dizzy(True)
    assert output == dizzy.visitName_or_attr(ctx)


@pytest.mark.parametrize(
    ("input", "output"),
    [
        ("0", ("0",)),
        ("1", ("1",)),
        ("3", ("3",)),
    ],
)
def test_visit_ref_helper_totally_an_integer(input, output):
    parser = parser_from_src_code(input)
    ctx = parser.totally_an_integer()

    dizzy = Dizzy(True)
    assert output == dizzy.visit_ref_helper(ctx)


@pytest.mark.parametrize(
    ("input", "output"),
    [
        ("a", ("a",)),
        ("a.b", ("a", "b")),
        ("a.b.c", ("a", "b", "c")),
    ],
)
def test_visit_ref_helper_name_or_attr(input, output):
    parser = parser_from_src_code(input)
    ctx = parser.name_or_attr()

    dizzy = Dizzy(True)
    assert output == dizzy.visit_ref_helper(ctx)


def test_visit_ref_helper_name():
    parser = parser_from_src_code("sparkles")
    ctx = parser.name()

    dizzy = Dizzy(True)
    assert ("sparkles",) == dizzy.visit_ref_helper(ctx)


# =============
# test compiler
# =============


def test_interface():
    tree = parse_as_file(
        """
        interface interface1:
            signal signal_a
            signal signal_b
        """
    )
    dizzy = Dizzy(True)
    results = dizzy.visitFile_input(tree)
    results.src_ctx = None
    assert results.supers == MODULE
    assert len(results.locals_) == 1
    assert results.locals_[0].ref == ("interface1",)
    interface: Object = results.locals_[0].value
    assert interface.supers == INTERFACE
    assert len(interface.locals_) == 2
    assert interface.locals_[0].ref == ("signal_a",)
    assert interface.locals_[0].value.supers == SIGNAL
    assert interface.locals_[1].ref == ("signal_b",)
    assert interface.locals_[1].value.supers == SIGNAL


def test_visitSignaldef_stmt():
    parser = parser_from_src_code("signal signal_a")
    ctx = parser.signaldef_stmt()

    dizzy = Dizzy(True)
    ret = dizzy.visitSignaldef_stmt(ctx)
    assert isinstance(ret, tuple)

    assert len(ret) == 1
    assert ret[0].ref == ("signal_a",)

    assert isinstance(ret[0].value, Object)
    assert ret[0].value.supers == SIGNAL


def test_visitPindef_stmt():
    parser = parser_from_src_code("pin pin_a")
    ctx = parser.pindef_stmt()

    dizzy = Dizzy(True)
    ret = dizzy.visitPindef_stmt(ctx)
    assert isinstance(ret, tuple)
    assert len(ret) == 1
    assert ret[0].ref == ("pin_a",)
    assert isinstance(ret[0].value, Object)
    assert ret[0].value.supers == PIN


# Connect statement return a tuple as there might be signal or pin instantiation within it
def test_visitConnect_stmt_simple():
    parser = parser_from_src_code("pin_a ~ pin_b")
    ctx = parser.connect_stmt()

    dizzy = Dizzy(True)
    ret = dizzy.visitConnect_stmt(ctx)
    assert len(ret[0]) == 2
    link = ret[0][1]
    assert link.source == ("pin_a",)
    assert link.target == ("pin_b",)


def test_visitRetype_stmt():
    parser = parser_from_src_code("a -> b")
    ctx = parser.retype_stmt()

    dizzy = Dizzy(True)
    ret = dizzy.visitRetype_stmt(ctx)
    assert len(ret) == 1
    assert ret[0] == (None, Replace(original=("a",), replacement=("b",)))


def test_visitConnect_stmt_instance():
    parser = parser_from_src_code("pin pin_a ~ signal sig_b")
    ctx = parser.connect_stmt()

    dizzy = Dizzy(True)
    ret = dizzy.visitConnect_stmt(ctx)

    assert isinstance(ret, tuple)
    assert len(ret) == 3

    assert ret[0].ref is None
    assert isinstance(ret[0].value, Link)
    assert ret[0].value.source == ("pin_a",)
    assert ret[0].value.target == ("sig_b",)

    assert ret[1].ref == ("pin_a",)
    assert isinstance(ret[1].value, Object)
    assert ret[1].value.supers == PIN

    assert ret[2].ref == ("sig_b",)
    assert isinstance(ret[2].value, Object)
    assert ret[2].value.supers == SIGNAL


def test_visitImport_stmt():
    parser = parser_from_src_code("import Module1 from 'test_import.ato'")
    ctx = parser.import_stmt()

    dizzy = Dizzy(True)
    ret = dizzy.visitImport_stmt(ctx)
    assert len(ret) == 1
    assert ret[0] == (("Module1",), Import(what=("Module1",), from_="test_import.ato"))


def test_visitBlockdef():
    parser = parser_from_src_code(
        """
        component comp1 from comp2:
            signal signal_a
        """.strip()
    )
    ctx = parser.blockdef()

    dizzy = Dizzy(True)
    results = dizzy.visitBlockdef(ctx)

    assert results.ref == ("comp1",)

    comp1: Object = results.value
    assert isinstance(comp1, Object)
    assert comp1.supers == (("comp2",),)
    assert len(comp1.locals_) == 1

    assert comp1.locals_[0].ref == ("signal_a",)
    comp2: Object = comp1.locals_[0].value
    assert isinstance(comp2, Object)
    assert comp2.supers == SIGNAL


def test_visitAssign_stmt_value():
    parser = parser_from_src_code("foo.bar = 35")
    ctx = parser.assign_stmt()

    dizzy = Dizzy(True)
    results = dizzy.visitAssign_stmt(ctx)
    assert len(results) == 1
    assert results[0] == (("foo", "bar"), 35)


def test_visitAssign_stmt_string():
    parser = parser_from_src_code('foo.bar = "baz"')
    ctx = parser.assign_stmt()

    dizzy = Dizzy(True)
    results = dizzy.visitAssign_stmt(ctx)
    assert len(results) == 1
    assert results[0] == (("foo", "bar"), "baz")


def test_visitNew_stmt():
    parser = parser_from_src_code("new Bar")
    ctx = parser.new_stmt()

    dizzy = Dizzy(True)
    results = dizzy.visitNew_stmt(ctx)
    assert isinstance(results, Object)
    assert results.supers == (("Bar",),)
    assert results.locals_ == ()


def test_visitModule1LayerDeep():
    tree = parse_as_file(
        """
        component comp1:
            signal signal_a
            signal signal_b
            signal_a ~ signal_b
        """
    )
    dizzy = Dizzy(True)
    results = dizzy.visitFile_input(tree)
    assert isinstance(results, Object)
    assert results.supers == MODULE
    assert len(results.locals_) == 1
    assert results.locals_[0].ref == ("comp1",)
    comp1: Object = results.locals_[0].value
    assert comp1.supers == COMPONENT
    assert len(comp1.locals_) == 3
    assert comp1.locals_[0].ref == ("signal_a",)
    assert isinstance(comp1.locals_[0].value, Object)
    assert comp1.locals_[0].value.supers == SIGNAL
    assert comp1.locals_[1].ref == ("signal_b",)
    assert isinstance(comp1.locals_[1].value, Object)
    assert comp1.locals_[1].value.supers == SIGNAL
    assert comp1.locals_[2].ref is None
    assert isinstance(comp1.locals_[2].value, Link)
    assert comp1.locals_[2].value.source == ("signal_a",)
    assert comp1.locals_[2].value.target == ("signal_b",)


def test_visitModule_pin_to_signal():
    tree = parse_as_file(
        """
        component comp1:
            signal signal_a ~ pin p1
        """
    )
    dizzy = Dizzy(True)
    results = dizzy.visitFile_input(tree)
    assert isinstance(results, Object)
    assert results.supers == MODULE
    assert len(results.locals_) == 1

    assert results.locals_[0].ref == ("comp1",)
    comp1: Object = results.locals_[0].value
    assert comp1.supers == COMPONENT
    assert len(comp1.locals_) == 3

    assert comp1.locals_[0].ref is None
    link = comp1.locals_[0].value
    assert isinstance(link, Link)
    assert link.source == ("signal_a",)
    assert link.target == ("p1",)

    assert comp1.locals_[1].ref == ("signal_a",)
    assert isinstance(comp1.locals_[1].value, Object)
    assert comp1.locals_[1].value.supers == SIGNAL

    assert comp1.locals_[2].ref == ("p1",)
    assert isinstance(comp1.locals_[2].value, Object)
    assert comp1.locals_[2].value.supers == PIN