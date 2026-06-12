from __future__ import annotations

import textwrap
from pathlib import Path

from core.parsing import TreeSitterParser
from schemas import Language, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"
FIXTURES = Path(__file__).parent / "fixtures" / "sample_repo_java"


def _parse(source: str, file_path: str = "src/App.java") -> list:
    return TreeSitterParser(Language.JAVA).parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def test_module_unit_first_with_full_source() -> None:
    units = _parse("class A {}\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "src.App"
    assert units[0].name == "App"
    assert units[0].language == Language.JAVA
    assert units[0].content == "class A {}\n"
    assert units[0].line_start == 1


def test_package_declaration_is_transparent() -> None:
    # D-16: qnames stay path-based; the package emits no unit.
    units = _parse(
        """
        package com.example.app;

        class A {}
        """,
        file_path="src/main/java/com/example/app/A.java",
    )
    qnames = {u.qualified_name for u in units}
    assert qnames == {
        "src.main.java.com.example.app.A",
        "src.main.java.com.example.app.A.A",
    }


def test_module_docstring_from_leading_block_comment() -> None:
    units = _parse(
        """
        /* App entry point. */
        package com.example;

        class A {}
        """
    )
    assert units[0].docstring == "App entry point."


def test_file_header_javadoc_above_package_is_module_docstring() -> None:
    # A /** */ above `package` documents the file, not a declaration.
    units = _parse(
        """
        /** Copyright header. */
        package com.example;

        class A {}
        """
    )
    assert units[0].docstring == "Copyright header."


def test_javadoc_above_first_class_belongs_to_class_not_module() -> None:
    units = _parse(
        """
        /** Doc for A. */
        class A {}
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    assert units[0].docstring is None
    assert by_qname["src.App.A"].docstring == "Doc for A."


def test_class_extends_and_implements_both_in_bases() -> None:
    # Java divergence from TS: `implements` IS inheritance here.
    units = _parse(
        """
        class Dog extends Pet implements Animal, Comparable<Dog> {}
        class Plain implements pkg.IFace {}
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.App.Dog"].kind == UnitKind.CLASS
    assert by_qname["src.App.Dog"].bases == ["Animal", "Comparable", "Pet"]
    assert by_qname["src.App.Plain"].bases == ["pkg.IFace"]


def test_methods_constructor_and_signatures() -> None:
    units = _parse(
        """
        public class Service {
            /** Builds it. */
            public Service(Config cfg) {}

            /** Handles a request. */
            @Override
            public String handle(Request req, int n) { return null; }

            static <T> T identity(T x) { return x; }

            void fire() {}
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    ctor = by_qname["src.App.Service.Service"]
    assert ctor.kind == UnitKind.METHOD
    assert ctor.signature == "Service(Config cfg)"  # no return type on ctors
    assert ctor.docstring == "Builds it."
    handle = by_qname["src.App.Service.handle"]
    assert handle.kind == UnitKind.METHOD
    assert handle.signature == "handle(Request req, int n): String"
    assert handle.docstring == "Handles a request."
    assert handle.parent_qualified_name == "src.App.Service"
    assert by_qname["src.App.Service.identity"].signature == "identity<T>(T x): T"
    assert by_qname["src.App.Service.fire"].signature == "fire(): void"


def test_annotated_method_span_includes_annotation() -> None:
    units = _parse(
        """
        class A {
            @Override
            public String toString() { return ""; }
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    method = by_qname["src.App.A.toString"]
    assert method.content.lstrip().startswith("@Override")


def test_static_final_upper_fields_are_constants() -> None:
    units = _parse(
        """
        class C {
            public static final int MAX_RETRIES = 5;
            static final int A = 1, B = 2;
            private static final int legCount = 4;
            public final int UPPER_BUT_NOT_STATIC = 1;
            private int age;
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.App.C.MAX_RETRIES"].kind == UnitKind.CONSTANT
    assert by_qname["src.App.C.A"].kind == UnitKind.CONSTANT
    assert by_qname["src.App.C.B"].kind == UnitKind.CONSTANT
    assert "src.App.C.legCount" not in by_qname  # not UPPER
    assert "src.App.C.UPPER_BUT_NOT_STATIC" not in by_qname  # not static
    assert "src.App.C.age" not in by_qname


def test_interface_with_implicit_constant_and_methods() -> None:
    units = _parse(
        """
        /** Behavior contract. */
        public interface Animal {
            int MAX_AGE = 100;
            String speak();
            default int twice(int n) { return n * 2; }
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    iface = by_qname["src.App.Animal"]
    assert iface.kind == UnitKind.CLASS
    assert iface.docstring == "Behavior contract."
    # Interface fields are implicitly static final.
    assert by_qname["src.App.Animal.MAX_AGE"].kind == UnitKind.CONSTANT
    speak = by_qname["src.App.Animal.speak"]
    assert speak.kind == UnitKind.METHOD
    assert speak.signature == "speak(): String"
    assert by_qname["src.App.Animal.twice"].signature == "twice(int n): int"


def test_enum_unit_emitted_constants_skipped_members_extracted() -> None:
    units = _parse(
        """
        enum Status {
            OPEN, CLOSED;
            static final int CODE = 1;
            public String label() { return name().toLowerCase(); }
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.App.Status"].kind == UnitKind.CLASS
    # Enum constants are covered by the enum CLASS unit — no own units.
    assert "src.App.Status.OPEN" not in by_qname
    assert "src.App.Status.CLOSED" not in by_qname
    # Members behind enum_body_declarations still surface.
    assert by_qname["src.App.Status.CODE"].kind == UnitKind.CONSTANT
    label = by_qname["src.App.Status.label"]
    assert label.kind == UnitKind.METHOD
    # Chained through a call result -> outer skipped, inner kept.
    assert label.calls == ["name"]


def test_record_and_annotation_declarations() -> None:
    units = _parse(
        """
        record Point(int x, int y) {
            static final int DIM = 2;
            R scale(int f) { return null; }
        }

        @interface Marker { String value(); }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.App.Point"].kind == UnitKind.CLASS
    assert by_qname["src.App.Point.DIM"].kind == UnitKind.CONSTANT
    assert by_qname["src.App.Point.scale"].kind == UnitKind.METHOD
    assert by_qname["src.App.Marker"].kind == UnitKind.CLASS


def test_record_compact_constructor_is_method() -> None:
    units = _parse(
        """
        record Range(int lo, int hi) {
            Range {
                check(lo);
            }
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    compact = by_qname["src.App.Range.Range"]
    assert compact.kind == UnitKind.METHOD
    assert "check" in compact.calls


def test_nested_classes_flatten_onto_qname_chain() -> None:
    units = _parse(
        """
        class Outer {
            static class Inner {
                void run() {}
                static class Deepest {}
            }
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    inner = by_qname["src.App.Outer.Inner"]
    assert inner.kind == UnitKind.CLASS
    # EDGE_RULES forbids Class-DEFINES->Class: nested types keep the
    # nested qname but parent on the MODULE.
    assert inner.parent_qualified_name == "src.App"
    run = by_qname["src.App.Outer.Inner.run"]
    assert run.kind == UnitKind.METHOD
    # Members still parent on the (nested) class itself.
    assert run.parent_qualified_name == "src.App.Outer.Inner"
    deepest = by_qname["src.App.Outer.Inner.Deepest"]
    assert deepest.parent_qualified_name == "src.App"


def test_import_variants_single_wildcard_static() -> None:
    units = _parse(
        """
        package com.example;

        import java.util.List;
        import java.util.*;
        import static java.lang.Math.max;

        class A {}
        """
    )
    assert units[0].imports == sorted({
        "java.util.List",     # single-type import — dotted as written
        "java.util",          # wildcard — the package, asterisk dropped
        "java.lang.Math.max", # static import — full path
    })


def test_calls_and_references_extracted() -> None:
    units = _parse(
        """
        class C {
            void run(Request input) {
                User user = fetchUser(input);
                api.client.refresh(user);
                this.helper();
                obj.a().b();
            }
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    run = by_qname["src.App.C.run"]
    # field_access chains reconstructed; chains through call results skip
    # the outer link but keep the inner call.
    assert run.calls == sorted({
        "fetchUser", "api.client.refresh", "this.helper", "obj.a",
    })
    # references include params, locals, and type identifiers.
    assert "input" in run.references
    assert "user" in run.references
    assert "User" in run.references


def test_object_creation_recorded_as_call() -> None:
    units = _parse(
        """
        class C {
            void build() {
                Object a = new Helper();
                Object b = new pkg.Helper(1);
                Object c = new java.util.ArrayList<String>();
                Object d = new Box<>();
            }
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    calls = by_qname["src.App.C.build"].calls
    assert "Helper" in calls
    assert "pkg.Helper" in calls
    assert "java.util.ArrayList" in calls  # type args stripped
    assert "Box" in calls                  # diamond stripped


def test_calls_inside_lambdas_attributed_to_enclosing_method() -> None:
    units = _parse(
        """
        class C {
            void each(List<String> items) {
                items.forEach(item -> process(item));
            }
        }
        """
    )
    by_qname = {u.qualified_name: u for u in units}
    calls = by_qname["src.App.C.each"].calls
    assert "items.forEach" in calls
    assert "process" in calls


def test_children_sorted_by_line() -> None:
    units = _parse(
        """
        class B { void z() {} void a() {} }
        class A {}
        """
    )
    starts = [u.line_start for u in units[1:]]
    assert starts == sorted(starts)


def test_determinism_same_source_same_units() -> None:
    source = """
        package com.example;

        import java.util.List;

        /** Doc. */
        class A extends B implements C {
            static final int K = 1;
            void m() { helper(); }
        }
    """
    first = _parse(source)
    second = _parse(source)
    # created_at/updated_at are wall-clock envelope fields — everything
    # content-derived (unit_id, source_sha, spans, relations) must match.
    volatile = {"created_at", "updated_at"}
    assert [u.model_dump(exclude=volatile) for u in first] == [
        u.model_dump(exclude=volatile) for u in second
    ]


def test_syntax_error_still_returns_module_unit() -> None:
    units = _parse("class Broken { void m( { if (x {\nclass Ok {}\n")
    assert units[0].kind == UnitKind.MODULE


def test_fixture_dog_java() -> None:
    source = (FIXTURES / "Dog.java").read_text()
    units = TreeSitterParser(Language.JAVA).parse_file(
        source=source, repo_id=REPO, file_path="Dog.java", commit_sha=COMMIT
    )
    by_qname = {u.qualified_name: u for u in units}
    assert units[0].docstring == (
        "Dog — exercises extends + implements, constructor, constants, nested class."
    )
    assert units[0].imports == sorted({
        "java.util.List", "java.util", "java.lang.Math.max",
    })
    dog = by_qname["Dog.Dog"]
    assert dog.kind == UnitKind.CLASS
    assert dog.bases == ["Animal", "Comparable", "Pet"]
    assert dog.docstring == "A loyal dog."
    assert by_qname["Dog.Dog.SOUND"].kind == UnitKind.CONSTANT
    assert "Dog.Dog.legCount" not in by_qname
    ctor = by_qname["Dog.Dog.Dog"]
    assert ctor.kind == UnitKind.METHOD
    assert ctor.signature == "Dog(int age)"
    assert "max" in ctor.calls
    speak = by_qname["Dog.Dog.speak"]
    assert speak.signature == "speak(): String"
    assert speak.docstring == "What the dog says."
    assert "SOUND.toUpperCase" in speak.calls
    collar = by_qname["Dog.Dog.Collar"]
    assert collar.kind == UnitKind.CLASS
    assert collar.parent_qualified_name == "Dog"  # nested type -> module
    assert by_qname["Dog.Dog.Collar.tighten"].kind == UnitKind.METHOD


def test_fixture_animal_java() -> None:
    source = (FIXTURES / "Animal.java").read_text()
    units = TreeSitterParser(Language.JAVA).parse_file(
        source=source, repo_id=REPO, file_path="Animal.java", commit_sha=COMMIT
    )
    by_qname = {u.qualified_name: u for u in units}
    animal = by_qname["Animal.Animal"]
    assert animal.kind == UnitKind.CLASS
    assert animal.docstring == "Behavior contract for animals."
    assert by_qname["Animal.Animal.MAX_AGE"].kind == UnitKind.CONSTANT
    assert by_qname["Animal.Animal.speak"].signature == "speak(): String"


def test_fixture_kennel_java_cross_class_instantiation() -> None:
    source = (FIXTURES / "Kennel.java").read_text()
    units = TreeSitterParser(Language.JAVA).parse_file(
        source=source, repo_id=REPO, file_path="Kennel.java", commit_sha=COMMIT
    )
    by_qname = {u.qualified_name: u for u in units}
    assert units[0].imports == ["java.util.ArrayList", "java.util.List"]
    adopt = by_qname["Kennel.Kennel.adopt"]
    assert adopt.signature == "adopt(int age): Dog"
    assert "Dog" in adopt.calls       # new Dog(age)
    assert "dogs.add" in adopt.calls
