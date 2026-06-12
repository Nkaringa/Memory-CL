from __future__ import annotations

import textwrap
from pathlib import Path

from core.parsing import TreeSitterParser
from schemas import Language, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"

FIXTURES = Path(__file__).parent / "fixtures" / "sample_repo_rust"


def _parse(source: str, file_path: str = "src/geometry.rs") -> list:
    return TreeSitterParser(Language.RUST).parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


# ---------------------------------------------------------------------------
# Module unit
# ---------------------------------------------------------------------------
def test_module_unit_first_with_full_source() -> None:
    units = _parse("pub fn f() {}\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "src.geometry"
    assert units[0].name == "geometry"
    assert units[0].language == Language.RUST
    assert units[0].content == "pub fn f() {}\n"
    assert units[0].line_start == 1


def test_mod_rs_collapses_module_qname() -> None:
    units = _parse("pub fn f() {}\n", file_path="src/db/mod.rs")
    assert units[0].qualified_name == "src.db"
    assert units[0].name == "db"


def test_module_docstring_from_inner_doc_comments() -> None:
    units = _parse("""
        //! Geometry primitives.
        //! Second line.

        pub fn f() {}
    """)
    assert units[0].docstring == "Geometry primitives.\nSecond line."


def test_outer_doc_on_first_item_is_not_module_docstring() -> None:
    units = _parse("""
        /// Belongs to the fn, not the module.
        pub fn f() {}
    """)
    assert units[0].docstring is None


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------
def test_function_items_with_signatures() -> None:
    units = _parse("""
        pub fn add(a: i32, b: i32) -> i32 { a + b }
        pub async fn fetch(url: &str) -> String { url.to_string() }
        fn identity<T: Clone>(x: T) -> T { x }
        fn no_ret(x: u8) {}
    """)
    by_qname = {u.qualified_name: u for u in units}
    add = by_qname["src.geometry.add"]
    assert add.kind == UnitKind.FUNCTION
    assert add.signature == "fn add(a: i32, b: i32) -> i32"
    assert add.parent_qualified_name == "src.geometry"
    assert by_qname["src.geometry.fetch"].signature == "async fn fetch(url: &str) -> String"
    assert by_qname["src.geometry.identity"].signature == "fn identity<T: Clone>(x: T) -> T"
    assert by_qname["src.geometry.no_ret"].signature == "fn no_ret(x: u8)"


def test_doc_comment_run_becomes_docstring() -> None:
    units = _parse("""
        /// Adds two numbers.
        /// Overflow-checked? No.
        pub fn add(a: i32, b: i32) -> i32 { a + b }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.geometry.add"].docstring == "Adds two numbers.\nOverflow-checked? No."


def test_doc_comment_skips_attributes_but_not_blank_lines() -> None:
    units = _parse("""
        /// Documented despite the derive.
        #[derive(Debug, Clone)]
        pub struct Foo { x: u8 }

        /// Orphaned by the blank line below.

        pub fn f() {}
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.geometry.Foo"].docstring == "Documented despite the derive."
    assert by_qname["src.geometry.f"].docstring is None


# ---------------------------------------------------------------------------
# Type declarations
# ---------------------------------------------------------------------------
def test_struct_enum_trait_union_become_classes() -> None:
    units = _parse("""
        pub struct Point { x: f64 }
        pub enum Shape { Circle(f64) }
        pub trait Area { fn area(&self) -> f64; }
        pub union Bits { f: f32, i: i32 }
    """)
    by_qname = {u.qualified_name: u for u in units}
    for name in ("Point", "Shape", "Area", "Bits"):
        unit = by_qname[f"src.geometry.{name}"]
        assert unit.kind == UnitKind.CLASS
        assert unit.parent_qualified_name == "src.geometry"


def test_trait_default_method_emitted_required_signature_skipped() -> None:
    units = _parse("""
        pub trait Area {
            fn area(&self) -> f64;
            /// Default description.
            fn describe(&self) -> String { String::from("shape") }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    describe = by_qname["src.geometry.Area.describe"]
    assert describe.kind == UnitKind.METHOD
    assert describe.parent_qualified_name == "src.geometry.Area"
    assert describe.docstring == "Default description."
    # Required (body-less) method declarations carry no executable content.
    assert "src.geometry.Area.area" not in by_qname


# ---------------------------------------------------------------------------
# Impl blocks
# ---------------------------------------------------------------------------
def test_inherent_impl_methods_parented_on_type() -> None:
    units = _parse("""
        pub struct Point { x: f64 }

        impl Point {
            /// Builds a point.
            pub fn new(x: f64) -> Self { Point { x } }
            fn norm(&self) -> f64 { self.x }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    new = by_qname["src.geometry.Point.new"]
    assert new.kind == UnitKind.METHOD
    assert new.parent_qualified_name == "src.geometry.Point"
    assert new.signature == "fn new(x: f64) -> Self"
    assert new.docstring == "Builds a point."
    assert by_qname["src.geometry.Point.norm"].kind == UnitKind.METHOD
    # Inherent impl adds no bases.
    assert by_qname["src.geometry.Point"].bases == []


def test_trait_impl_methods_under_type_and_trait_in_bases() -> None:
    units = _parse("""
        pub struct Point { x: f64 }

        pub trait Area { fn area(&self) -> f64; }

        impl Area for Point {
            fn area(&self) -> f64 { self.x }
        }

        impl std::fmt::Display for Point {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                write!(f, "{}", self.x)
            }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    area = by_qname["src.geometry.Point.area"]
    assert area.kind == UnitKind.METHOD
    assert area.parent_qualified_name == "src.geometry.Point"
    assert by_qname["src.geometry.Point.fmt"].kind == UnitKind.METHOD
    # Trait impls in the same file merge into the struct's bases.
    assert by_qname["src.geometry.Point"].bases == sorted(["Area", "std.fmt.Display"])
    # The trait's own CLASS unit gains no bases from being implemented.
    assert by_qname["src.geometry.Area"].bases == []


def test_trait_impl_for_foreign_type_emits_methods_only() -> None:
    # `Point` is declared in another file — methods still parent on it,
    # but no CLASS unit (and therefore no bases) is fabricated here.
    units = _parse("""
        use crate::geometry::Point;

        impl Render for Point {
            fn render(&self) -> String { String::new() }
        }
    """, file_path="src/render.rs")
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.render.Point.render"].kind == UnitKind.METHOD
    assert "src.render.Point" not in by_qname


def test_generic_impl_uses_bare_type_name() -> None:
    units = _parse("""
        pub struct Holder<T> { value: T }

        impl<T: Clone> Holder<T> {
            fn get(&self) -> T { self.value.clone() }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.geometry.Holder.get"].parent_qualified_name == "src.geometry.Holder"


def test_const_inside_impl_is_constant_under_type() -> None:
    units = _parse("""
        pub struct Point { x: f64 }

        impl Point {
            pub const ORIGIN_X: f64 = 0.0;
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    c = by_qname["src.geometry.Point.ORIGIN_X"]
    assert c.kind == UnitKind.CONSTANT
    assert c.parent_qualified_name == "src.geometry.Point"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def test_all_const_and_static_items_are_constants() -> None:
    units = _parse("""
        pub const MAX_RETRIES: usize = 5;
        const lowercase_const: u8 = 1;
        static GLOBAL_NAME: &str = "hi";
    """)
    by_qname = {u.qualified_name: u for u in units}
    # The `const`/`static` keyword is explicit — no UPPER_CASE gate.
    assert by_qname["src.geometry.MAX_RETRIES"].kind == UnitKind.CONSTANT
    assert by_qname["src.geometry.lowercase_const"].kind == UnitKind.CONSTANT
    assert by_qname["src.geometry.GLOBAL_NAME"].kind == UnitKind.CONSTANT


# ---------------------------------------------------------------------------
# Inline modules
# ---------------------------------------------------------------------------
def test_inline_mod_nests_qnames_one_level() -> None:
    units = _parse("""
        mod inner {
            pub fn nested_fn() -> u8 { 1 }
            pub const INNER_C: u8 = 2;
            pub struct InnerS;

            mod deeper {
                pub fn too_deep() {}
            }
        }

        mod declared_elsewhere;
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.geometry.inner.nested_fn"].kind == UnitKind.FUNCTION
    assert by_qname["src.geometry.inner.INNER_C"].kind == UnitKind.CONSTANT
    assert by_qname["src.geometry.inner.InnerS"].kind == UnitKind.CLASS
    # One level of nesting only (parity with Python's class-in-module).
    assert not any("too_deep" in q for q in by_qname)
    # `mod x;` declarations have no body — nothing to emit.
    assert not any("declared_elsewhere" in q for q in by_qname)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
def test_use_declaration_variants() -> None:
    units = _parse("""
        use std::collections::HashMap;
        use std::io::{self, Read, Write};
        use crate::geometry::*;
        use serde::Serialize as Ser;
        use a::{b, c::d, e as f};
        use simple;
    """)
    assert units[0].imports == sorted({
        "std.collections.HashMap",
        "std.io",            # {self, ...} imports the prefix itself
        "std.io.Read",
        "std.io.Write",
        "crate.geometry",    # glob records the glob'd module
        "serde.Serialize",   # original path, not the `as` alias
        "a.b",
        "a.c.d",
        "a.e",
        "simple",
    })


def test_use_inside_inline_mod_not_collected() -> None:
    units = _parse("""
        mod inner {
            use std::collections::HashMap;
        }
    """)
    assert units[0].imports == []


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------
def test_calls_extracted_macros_skipped() -> None:
    units = _parse("""
        fn caller() {
            plain(1);
            a::b::func(2);
            obj.method(3);
            self.helper();
            Point::new(0.0, 1.0);
            Vec::<u8>::new();
            chain().next();
            println!("skipped");
            vec![1, 2];
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    calls = by_qname["src.geometry.caller"].calls
    assert calls == sorted({
        "plain",
        "a.b.func",
        "obj.method",
        "self.helper",
        "Point.new",
        "Vec.new",   # turbofish path unwrapped
        "chain",     # chained `.next()` target is a call result — skipped
    })
    # Macro invocations never appear as calls.
    assert not any("println" in c or "vec" in c for c in calls)


def test_calls_in_methods_and_references() -> None:
    units = _parse("""
        pub struct Point { x: f64 }

        impl Point {
            fn area(&self) -> f64 {
                let scaled = helper(self.x);
                self.norm();
                scaled
            }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    area = by_qname["src.geometry.Point.area"]
    assert "helper" in area.calls
    assert "self.norm" in area.calls
    assert "scaled" in area.references


# ---------------------------------------------------------------------------
# Determinism + error tolerance
# ---------------------------------------------------------------------------
def test_parse_is_deterministic() -> None:
    src = (FIXTURES / "geometry.rs").read_text()
    first = _parse(src, file_path="geometry.rs")
    second = _parse(src, file_path="geometry.rs")
    # created_at/updated_at are wall-clock — everything else must match.
    timestamps = {"created_at", "updated_at"}
    assert [u.model_dump(exclude=timestamps) for u in first] == [
        u.model_dump(exclude=timestamps) for u in second
    ]


def test_syntax_error_still_returns_module_and_healthy_units() -> None:
    units = _parse("""
        fn broken( { if x {

        pub const STILL_HERE: u8 = 1;
    """)
    assert units[0].kind == UnitKind.MODULE
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.geometry.STILL_HERE"].kind == UnitKind.CONSTANT


def test_children_sorted_by_line() -> None:
    units = _parse("""
        fn b() {}
        fn a() {}
        const C: u8 = 1;
    """)
    starts = [u.line_start for u in units[1:]]
    assert starts == sorted(starts)


# ---------------------------------------------------------------------------
# Fixture repo end-to-end
# ---------------------------------------------------------------------------
def test_fixture_geometry_units() -> None:
    src = (FIXTURES / "geometry.rs").read_text()
    units = _parse(src, file_path="geometry.rs")
    by_qname = {u.qualified_name: u for u in units}
    assert units[0].docstring == "Geometry primitives — exercises structs, traits, impls and consts."
    assert units[0].imports == sorted({"std.fmt", "std.fmt.Display"})
    assert by_qname["geometry.Point"].kind == UnitKind.CLASS
    assert by_qname["geometry.Point"].bases == sorted(["Area", "Display"])
    assert by_qname["geometry.SCALE"].kind == UnitKind.CONSTANT
    assert by_qname["geometry.Area.describe"].kind == UnitKind.METHOD
    assert by_qname["geometry.Point.origin"].kind == UnitKind.METHOD
    assert by_qname["geometry.Point.area"].parent_qualified_name == "geometry.Point"
    assert "self.norm" in by_qname["geometry.Point.area"].calls


def test_fixture_report_units() -> None:
    src = (FIXTURES / "report.rs").read_text()
    units = _parse(src, file_path="report.rs")
    by_qname = {u.qualified_name: u for u in units}
    assert units[0].imports == sorted({
        "crate.geometry.Area",
        "crate.geometry.Point",
        "std.collections.HashMap",
    })
    assert by_qname["report.render"].kind == UnitKind.FUNCTION
    assert "point.area" in by_qname["report.render"].calls
    assert "render" in by_qname["report.build_report"].calls
    assert "HashMap.new" in by_qname["report.build_report"].calls
    assert by_qname["report.REPORT_TITLE"].kind == UnitKind.CONSTANT
    assert by_qname["report.summary.count"].kind == UnitKind.FUNCTION
