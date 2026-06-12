from __future__ import annotations

import textwrap
from pathlib import Path

from core.parsing import TreeSitterParser
from schemas import Language, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"

FIXTURES = Path(__file__).parent / "fixtures" / "sample_repo_go"


def _parse(source: str, file_path: str = "pkg/server/app.go") -> list:
    return TreeSitterParser(Language.GO).parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def _parse_fixture(rel_path: str) -> list:
    source = (FIXTURES / rel_path).read_text(encoding="utf-8")
    return TreeSitterParser(Language.GO).parse_file(
        source=source,
        repo_id=REPO,
        file_path=rel_path,
        commit_sha=COMMIT,
    )


# ---------------------------------------------------------------------------
# Module unit
# ---------------------------------------------------------------------------
def test_module_unit_first_with_full_source() -> None:
    units = _parse("package app\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "pkg.server.app"
    assert units[0].name == "app"
    assert units[0].language == Language.GO
    assert units[0].content == "package app\n"
    assert units[0].line_start == 1


def test_module_docstring_from_package_doc() -> None:
    units = _parse("""
        // Package app does things.
        // Second line of the doc.
        package app
    """)
    assert units[0].docstring == "Package app does things.\nSecond line of the doc."


def test_module_docstring_requires_adjacency() -> None:
    units = _parse("""
        // Detached license header.

        package app
    """)
    assert units[0].docstring is None


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------
def test_function_declaration() -> None:
    units = _parse("""
        package app

        // Compute computes.
        func Compute(a int, b int) (int, error) {
            return a + b, nil
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    fn = by_qname["pkg.server.app.Compute"]
    assert fn.kind == UnitKind.FUNCTION
    assert fn.parent_qualified_name == "pkg.server.app"
    assert fn.signature == "func Compute(a int, b int) (int, error)"
    assert fn.docstring == "Compute computes."


def test_generic_function_signature() -> None:
    units = _parse("""
        package app

        func Map[T any, U any](in []T, f func(T) U) []U { return nil }
    """)
    by_qname = {u.qualified_name: u for u in units}
    sig = by_qname["pkg.server.app.Map"].signature
    assert sig == "func Map[T any, U any](in []T, f func(T) U) []U"


# ---------------------------------------------------------------------------
# Methods + receiver parenting
# ---------------------------------------------------------------------------
def test_method_pointer_receiver_parented_on_stripped_type() -> None:
    units = _parse("""
        package app

        type Handler struct {
            count int
        }

        // Greet greets.
        func (h *Handler) Greet(name string) string {
            return name
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    m = by_qname["pkg.server.app.Handler.Greet"]
    assert m.kind == UnitKind.METHOD
    # Pointer `*` stripped — resolves to the in-file Handler CLASS unit.
    assert m.parent_qualified_name == "pkg.server.app.Handler"
    assert by_qname["pkg.server.app.Handler"].kind == UnitKind.CLASS
    assert m.signature == "func (h *Handler) Greet(name string) string"
    assert m.docstring == "Greet greets."


def test_method_value_receiver() -> None:
    units = _parse("""
        package app

        func (h Handler) Count() int { return h.count }
    """)
    by_qname = {u.qualified_name: u for u in units}
    m = by_qname["pkg.server.app.Handler.Count"]
    assert m.kind == UnitKind.METHOD
    assert m.parent_qualified_name == "pkg.server.app.Handler"
    assert m.signature == "func (h Handler) Count() int"


def test_method_generic_receiver_uses_base_type() -> None:
    units = _parse("""
        package app

        func (p *Pair[T]) Sum() int { return 0 }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert "pkg.server.app.Pair.Sum" in by_qname
    assert by_qname["pkg.server.app.Pair.Sum"].kind == UnitKind.METHOD


# ---------------------------------------------------------------------------
# Types -> CLASS
# ---------------------------------------------------------------------------
def test_struct_class_with_embedded_bases() -> None:
    units = _parse("""
        package app

        // Handler handles requests.
        type Handler struct {
            http.Client
            *strings.Builder
            Embedded
            Name string
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    cls = by_qname["pkg.server.app.Handler"]
    assert cls.kind == UnitKind.CLASS
    assert cls.parent_qualified_name == "pkg.server.app"
    # Embedded types -> bases (pointer stripped); named fields are not bases.
    assert cls.bases == sorted({"http.Client", "strings.Builder", "Embedded"})
    assert cls.docstring == "Handler handles requests."
    assert cls.signature is None


def test_interface_class_with_embedded_bases() -> None:
    units = _parse("""
        package app

        // Greeter greets.
        type Greeter interface {
            io.Reader
            Greet(name string) string
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    cls = by_qname["pkg.server.app.Greeter"]
    assert cls.kind == UnitKind.CLASS
    assert cls.bases == ["io.Reader"]
    assert cls.docstring == "Greeter greets."
    # Interface method signatures are not separate units.
    assert "pkg.server.app.Greeter.Greet" not in by_qname


def test_grouped_type_block_and_aliases_skipped() -> None:
    units = _parse("""
        package app

        type (
            // Pair doc.
            Pair struct{ X, Y int }
            Alias = string
            Named int
        )
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["pkg.server.app.Pair"].kind == UnitKind.CLASS
    assert by_qname["pkg.server.app.Pair"].docstring == "Pair doc."
    # type_alias nodes and non-composite defined types carry no members.
    assert "pkg.server.app.Alias" not in by_qname
    assert "pkg.server.app.Named" not in by_qname


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def test_single_const_camelcase_is_constant() -> None:
    # Go convention is CamelCase — the UPPER rule is deliberately relaxed.
    units = _parse("""
        package app

        // DefaultName is used when no name is given.
        const DefaultName = "world"

        const maxRetries = 3
    """)
    by_qname = {u.qualified_name: u for u in units}
    c = by_qname["pkg.server.app.DefaultName"]
    assert c.kind == UnitKind.CONSTANT
    assert c.docstring == "DefaultName is used when no name is given."
    # Even unexported lowercase consts are constants in Go.
    assert by_qname["pkg.server.app.maxRetries"].kind == UnitKind.CONSTANT


def test_grouped_const_block_iota_and_multi_name_specs() -> None:
    units = _parse("""
        package app

        const (
            // StateIdle means nothing in flight.
            StateIdle = iota
            StateBusy
            Width, Height = 640, 480
        )
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["pkg.server.app.StateIdle"].kind == UnitKind.CONSTANT
    assert by_qname["pkg.server.app.StateIdle"].docstring == "StateIdle means nothing in flight."
    # iota continuation spec still yields its own unit, no doc.
    assert by_qname["pkg.server.app.StateBusy"].kind == UnitKind.CONSTANT
    assert by_qname["pkg.server.app.StateBusy"].docstring is None
    # `A, B = x, y` -> one unit per name.
    assert by_qname["pkg.server.app.Width"].kind == UnitKind.CONSTANT
    assert by_qname["pkg.server.app.Height"].kind == UnitKind.CONSTANT


def test_var_declarations_are_skipped() -> None:
    units = _parse("""
        package app

        var GlobalThing = 42
        var MAX_UPPER = 1
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert "pkg.server.app.GlobalThing" not in by_qname
    assert "pkg.server.app.MAX_UPPER" not in by_qname


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
def test_import_variants_dotted_paths() -> None:
    units = _parse("""
        package app

        import "fmt"

        import (
            "net/http"
            myjson "encoding/json"
            _ "database/sql"
            . "math"
        )
    """)
    module = units[0]
    assert module.imports == sorted({
        "fmt",            # single form
        "net.http",       # "/" -> "."
        "encoding.json",  # aliased — the PATH is recorded, not the alias
        "database.sql",   # blank `_` import included
        "math",           # dot import included
    })


def test_third_party_import_path_dotted() -> None:
    units = _parse("""
        package app

        import "github.com/stretchr/testify/assert"
    """)
    assert units[0].imports == ["github.com.stretchr.testify.assert"]


# ---------------------------------------------------------------------------
# Calls + references
# ---------------------------------------------------------------------------
def test_calls_and_references_extracted() -> None:
    units = _parse("""
        package app

        func run(input string) string {
            user := fetchUser(input)
            api.client.refresh(user)
            h.Greet(strings.ToUpper(user))
            obj.m()(1)
            arr[0](2)
            return user
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    run = by_qname["pkg.server.app.run"]
    # Selector chains reconstructed dotted. `obj.m()(1)`: the OUTER call's
    # callee is a call result (unresolvable, skipped) but the inner
    # `obj.m()` is a real call. Index-expression callees are skipped.
    assert run.calls == sorted({
        "fetchUser",
        "api.client.refresh",
        "h.Greet",
        "strings.ToUpper",
        "obj.m",
    })
    assert "input" in run.references
    assert "user" in run.references


def test_method_body_calls_attributed_to_method() -> None:
    units = _parse("""
        package app

        func (h *Handler) Serve() {
            h.log.Print("x")
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert "h.log.Print" in by_qname["pkg.server.app.Handler.Serve"].calls


# ---------------------------------------------------------------------------
# Doc comments
# ---------------------------------------------------------------------------
def test_doc_comment_blank_line_detaches() -> None:
    units = _parse("""
        package app

        // Not a doc for f.

        func f() {}
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["pkg.server.app.f"].docstring is None


def test_trailing_comment_not_taken_as_doc() -> None:
    units = _parse("""
        package app

        const A = 1 // trailing note
        func f() {}
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["pkg.server.app.f"].docstring is None


def test_multiline_doc_run_cleaned() -> None:
    units = _parse("""
        package app

        // First line.
        //
        // Third line after a // spacer.
        func f() {}
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["pkg.server.app.f"].docstring == "First line.\nThird line after a // spacer."


# ---------------------------------------------------------------------------
# Ordering, determinism, error tolerance
# ---------------------------------------------------------------------------
def test_children_sorted_by_line() -> None:
    units = _parse("""
        package app

        func b() {}
        func a() {}
        const C = 1
    """)
    starts = [u.line_start for u in units[1:]]
    assert starts == sorted(starts)


def test_parse_is_deterministic() -> None:
    source = """
        package app

        const X = 1

        type T struct{ V int }

        func (t *T) Get() int { return t.V }

        func Use() int {
            t := &T{V: X}
            return t.Get()
        }
    """
    first = _parse(source)
    second = _parse(source)
    volatile = {"created_at", "updated_at"}
    assert [u.model_dump(exclude=volatile) for u in first] == [
        u.model_dump(exclude=volatile) for u in second
    ]
    assert [u.unit_id for u in first] == [u.unit_id for u in second]


def test_syntax_error_still_returns_module_and_healthy_units() -> None:
    units = _parse("""
        package app

        func healthy() {}

        )))garbage(((
    """)
    assert units[0].kind == UnitKind.MODULE
    qnames = {u.qualified_name for u in units}
    assert "pkg.server.app.healthy" in qnames


# ---------------------------------------------------------------------------
# Fixture round-trip (tests/fixtures/sample_repo_go)
# ---------------------------------------------------------------------------
def test_fixture_handler_go() -> None:
    units = _parse_fixture("server/handler.go")
    by_qname = {u.qualified_name: u for u in units}

    module = by_qname["server.handler"]
    assert module.kind == UnitKind.MODULE
    assert module.docstring == "Package server implements a tiny greeting server."
    assert module.imports == ["fmt", "strings"]

    assert by_qname["server.handler.DefaultName"].kind == UnitKind.CONSTANT

    cls = by_qname["server.handler.Handler"]
    assert cls.kind == UnitKind.CLASS
    assert cls.bases == ["Logger"]

    fn = by_qname["server.handler.NewHandler"]
    assert fn.kind == UnitKind.FUNCTION
    assert fn.signature == "func NewHandler(name string) *Handler"
    assert "strings.TrimSpace" in fn.calls

    greet = by_qname["server.handler.Handler.Greet"]
    assert greet.kind == UnitKind.METHOD
    assert greet.parent_qualified_name == "server.handler.Handler"
    assert "fmt.Sprintf" in greet.calls

    count = by_qname["server.handler.Handler.Count"]
    assert count.kind == UnitKind.METHOD
    assert count.parent_qualified_name == "server.handler.Handler"


def test_fixture_greeter_go() -> None:
    units = _parse_fixture("server/greeter.go")
    by_qname = {u.qualified_name: u for u in units}

    assert by_qname["server.greeter"].imports == ["fmt"]
    assert by_qname["server.greeter.Greeter"].kind == UnitKind.CLASS
    assert by_qname["server.greeter.Logger"].kind == UnitKind.CLASS
    assert by_qname["server.greeter.Logger.Log"].parent_qualified_name == "server.greeter.Logger"

    assert by_qname["server.greeter.StateIdle"].kind == UnitKind.CONSTANT
    assert by_qname["server.greeter.StateBusy"].kind == UnitKind.CONSTANT

    # Cross-function calls inside the package: GreetAll -> NewHandler/h.Greet.
    greet_all = by_qname["server.greeter.GreetAll"]
    assert greet_all.kind == UnitKind.FUNCTION
    assert "NewHandler" in greet_all.calls
    assert "h.Greet" in greet_all.calls
