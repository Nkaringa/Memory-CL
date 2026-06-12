"""C# extraction tests (multilang batch-2, Task 2).

Mirrors tests/test_treesitter_parser.py's idiom: inline sources through
TreeSitterParser(Language.CSHARP), exact qnames/kinds/signatures/bases/
imports/calls/docstrings, plus the fixture repo at
tests/fixtures/sample_repo_csharp/ and one real-world Unity shape.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from core.parsing import TreeSitterParser
from schemas import Language, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"
FIXTURES = Path(__file__).parent / "fixtures" / "sample_repo_csharp"


def _parse(source: str, file_path: str = "src/app.cs") -> list:
    return TreeSitterParser(Language.CSHARP).parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def _parse_fixture(rel_path: str) -> list:
    source = (FIXTURES / rel_path).read_text(encoding="utf-8")
    return TreeSitterParser(Language.CSHARP).parse_file(
        source=source,
        repo_id=REPO,
        file_path=rel_path,
        commit_sha=COMMIT,
    )


# ---------------------------------------------------------------------------
# Module unit
# ---------------------------------------------------------------------------
def test_module_unit_first_with_full_source() -> None:
    units = _parse("public class A {}\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "src.app"
    assert units[0].name == "app"
    assert units[0].language == Language.CSHARP
    assert units[0].content == "public class A {}\n"
    assert units[0].line_start == 1


def test_syntax_error_still_returns_module_unit() -> None:
    units = _parse("public class Broken { void f( {\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "src.app"


# ---------------------------------------------------------------------------
# Module docstring
# ---------------------------------------------------------------------------
def test_module_docstring_from_leading_line_comment_run() -> None:
    units = _parse("""
        // Entry point.
        // Second line.

        public class App {}
    """)
    assert units[0].docstring == "Entry point.\nSecond line."


def test_module_docstring_from_leading_block_comment() -> None:
    units = _parse("""
        /* App entry point. */
        public class App {}
    """)
    assert units[0].docstring == "App entry point."


def test_doc_run_above_first_decl_belongs_to_decl_not_module() -> None:
    units = _parse("""
        /// <summary>The app.</summary>
        public class App {}
    """)
    assert units[0].docstring is None
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.App"].docstring == "The app."


# ---------------------------------------------------------------------------
# Namespaces (transparent — design D-16)
# ---------------------------------------------------------------------------
def test_block_namespace_is_transparent() -> None:
    units = _parse("""
        namespace Game.Core
        {
            public class Player {}
        }
    """)
    qnames = [u.qualified_name for u in units]
    assert qnames == ["src.app", "src.app.Player"]  # no namespace unit/segment


def test_file_scoped_namespace_is_transparent() -> None:
    units = _parse("""
        namespace Game.Core;

        public class Player {}
    """)
    qnames = [u.qualified_name for u in units]
    assert qnames == ["src.app", "src.app.Player"]


def test_nested_namespaces_descend() -> None:
    units = _parse("namespace A { namespace B { class C {} } }\n")
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.C"].kind == UnitKind.CLASS
    assert by_qname["src.app.C"].parent_qualified_name == "src.app"


# ---------------------------------------------------------------------------
# Type declarations
# ---------------------------------------------------------------------------
def test_all_type_decl_kinds_are_class_units() -> None:
    units = _parse("""
        public class A {}
        public struct B {}
        public interface IC {}
        public record D(int X);
        public record struct E(float Y);
        public enum F { One, Two }
    """)
    by_qname = {u.qualified_name: u for u in units}
    for name in ("A", "B", "IC", "D", "E", "F"):
        assert by_qname[f"src.app.{name}"].kind == UnitKind.CLASS


def test_bases_from_base_list() -> None:
    units = _parse("""
        public class Enemy : Game.Core.Base<int>, IFoo {}
        public record Student(string Name) : Person(Name), ITagged;
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.Enemy"].bases == ["Game.Core.Base", "IFoo"]
    assert by_qname["src.app.Student"].bases == ["ITagged", "Person"]


def test_nested_class() -> None:
    units = _parse("""
        public class Outer
        {
            private class Inner
            {
                public void Go() {}
            }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    inner = by_qname["src.app.Outer.Inner"]
    assert inner.kind == UnitKind.CLASS
    # EDGE_RULES forbids Class-DEFINES->Class: nested types keep the
    # nested qname but parent on the MODULE.
    assert inner.parent_qualified_name == "src.app"
    go = by_qname["src.app.Outer.Inner.Go"]
    assert go.kind == UnitKind.METHOD
    # Members still parent on the (nested) class itself.
    assert go.parent_qualified_name == "src.app.Outer.Inner"


# ---------------------------------------------------------------------------
# Methods / constructors / local functions
# ---------------------------------------------------------------------------
def test_methods_constructors_and_signatures() -> None:
    units = _parse("""
        public class Svc
        {
            public Svc(int port) {}
            public string Render(int n) { return n.ToString(); }
            public async Task<bool> SaveAsync(string key) { return true; }
            public T Echo<T>(T value) where T : class { return value; }
            public int Twice(int x) => x * 2;
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    ctor = by_qname["src.app.Svc.Svc"]
    assert ctor.kind == UnitKind.METHOD
    assert ctor.signature == "Svc(int port)"
    assert by_qname["src.app.Svc.Render"].signature == "Render(int n) -> string"
    assert (
        by_qname["src.app.Svc.SaveAsync"].signature
        == "async SaveAsync(string key) -> Task<bool>"
    )
    assert by_qname["src.app.Svc.Echo"].signature == "Echo<T>(T value) -> T"
    # Expression-bodied (`=>`) methods are still methods.
    assert by_qname["src.app.Svc.Twice"].kind == UnitKind.METHOD
    assert by_qname["src.app.Svc.Twice"].parent_qualified_name == "src.app.Svc"


def test_local_function_folds_into_enclosing_method() -> None:
    units = _parse("""
        public class Calc
        {
            public double Area(double r)
            {
                double Square(double x) { return Helper(x) * x; }
                return 3.14 * Square(r);
            }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    # Python parity: local functions are NOT units (emitting them would
    # also create the EDGE_RULES-forbidden Method-DEFINES->Function edge).
    assert "src.app.Calc.Area.Square" not in by_qname
    # Their calls/references attribute to the enclosing method instead:
    # both the call *to* Square and the calls *inside* Square land on Area.
    area = by_qname["src.app.Calc.Area"]
    assert area.calls == sorted({"Helper", "Square"})
    assert "Square" in area.references


def test_interface_methods_emitted_without_bodies() -> None:
    units = _parse("""
        public interface IMeasurable
        {
            double Area();
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    area = by_qname["src.app.IMeasurable.Area"]
    assert area.kind == UnitKind.METHOD
    assert area.signature == "Area() -> double"
    assert area.calls == []


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def test_const_fields_always_constant_static_readonly_only_upper() -> None:
    units = _parse("""
        public class Cfg
        {
            public const int MAX_HP = 100;
            private const string label = "cfg";
            public static readonly int SPEED_CAP = 10;
            public static readonly int defaultSpeed = 5;
            private static int counter = 0;
            public readonly int slot = 1;
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.Cfg.MAX_HP"].kind == UnitKind.CONSTANT
    assert by_qname["src.app.Cfg.label"].kind == UnitKind.CONSTANT  # const wins
    assert by_qname["src.app.Cfg.SPEED_CAP"].kind == UnitKind.CONSTANT
    assert "src.app.Cfg.defaultSpeed" not in by_qname  # static readonly camel
    assert "src.app.Cfg.counter" not in by_qname  # plain static
    assert "src.app.Cfg.slot" not in by_qname  # readonly without static


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
def test_properties_with_bodies_are_methods_auto_properties_skipped() -> None:
    units = _parse("""
        public class P
        {
            private double _r;
            public double Diameter => _r * 2;
            public double Radius
            {
                get { return _r; }
                set { _r = Clamp(value); }
            }
            public string Tag { get; set; } = "x";
            public int Hits { get; private set; }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    diameter = by_qname["src.app.P.Diameter"]
    assert diameter.kind == UnitKind.METHOD
    assert diameter.name == "Diameter"
    assert diameter.signature == "Diameter -> double"
    radius = by_qname["src.app.P.Radius"]
    assert radius.kind == UnitKind.METHOD
    assert radius.calls == ["Clamp"]
    assert "src.app.P.Tag" not in by_qname  # auto-property (initializer != body)
    assert "src.app.P.Hits" not in by_qname  # auto-property


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
def test_using_directive_forms() -> None:
    units = _parse("""
        using System;
        using UnityEngine;
        using System.Collections.Generic;
        using static System.Math;
        using Vec = System.Numerics.Vector2;

        public class A {}
    """)
    assert units[0].imports == sorted({
        "System",
        "UnityEngine",
        "System.Collections.Generic",
        "System.Math",                 # using static -> target type
        "System.Numerics.Vector2",     # alias -> target, not alias name
    })


def test_using_directives_inside_namespace_blocks() -> None:
    units = _parse("""
        namespace App
        {
            using System.IO;

            public class A {}
        }
    """)
    assert units[0].imports == ["System.IO"]


# ---------------------------------------------------------------------------
# Calls and references
# ---------------------------------------------------------------------------
def test_invocation_and_object_creation_calls() -> None:
    units = _parse("""
        public class Runner
        {
            public void Run()
            {
                Physics.Raycast(origin);
                shoot();
                base.Start();
                this.helper.Go();
                var a = new UnityEngine.GameObject("g");
                var b = new List<int>();
                GetComponent<Rigidbody>().AddForce(v);
                string.Join(",", parts);
            }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    run = by_qname["src.app.Runner.Run"]
    assert run.calls == sorted({
        "Physics.Raycast",
        "shoot",
        "base.Start",
        "this.helper.Go",
        "UnityEngine.GameObject",  # object creation, qualified
        "List",                    # object creation, generic arity dropped
        "GetComponent",            # generic invocation; chained .AddForce on a
                                   # call result is unresolvable -> skipped
        "string.Join",             # predefined-type receiver
    })
    assert "origin" in run.references
    assert "parts" in run.references


# ---------------------------------------------------------------------------
# Doc comments
# ---------------------------------------------------------------------------
def test_triple_slash_docs_cleaned_of_xml_tags() -> None:
    units = _parse("""
        /// <summary>
        /// Moves the player.
        /// </summary>
        /// <param name="dx">delta x</param>
        public class Mover
        {
            /// <summary>Computes the area.</summary>
            /// <returns>area in square units</returns>
            public double Area() { return 0; }
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.Mover"].docstring == "Moves the player.\ndelta x"
    assert (
        by_qname["src.app.Mover.Area"].docstring
        == "Computes the area.\narea in square units"
    )


def test_detached_comment_run_is_not_a_docstring() -> None:
    units = _parse("""
        public class A
        {
            private int _x; // trailing note

            public void Go() {}
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.A.Go"].docstring is None


def test_docs_survive_attributes_on_the_declaration() -> None:
    units = _parse("""
        /// <summary>Serialized thing.</summary>
        [Serializable]
        public class Thing {}
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.Thing"].docstring == "Serialized thing."


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_deterministic_output_and_sorted_children() -> None:
    source = """
        using B;
        using A;

        namespace N
        {
            public class Z { public void M() {} }
            public class A { public const int K = 1; }
        }
    """
    first = _parse(source)
    second = _parse(source)
    assert [(u.unit_id, u.qualified_name, u.kind) for u in first] == [
        (u.unit_id, u.qualified_name, u.kind) for u in second
    ]
    children = first[1:]
    assert children == sorted(children, key=lambda u: (u.line_start, u.name))
    assert first[0].imports == ["A", "B"]  # sorted by the schema validator


# ---------------------------------------------------------------------------
# Real-world Unity shape (probed against ~/Desktop/void/void/Assets)
# ---------------------------------------------------------------------------
def test_monobehaviour_pattern_extracts() -> None:
    # Shape mirrors a real probed file (FollowCamera.cs / NodeMarker.cs):
    # MonoBehaviour subclass, [SerializeField] fields, static readonly ids,
    # expression-bodied setters, Unity lifecycle methods.
    units = _parse(
        """
        using UnityEngine;

        namespace KnowledgeKingdom.Game
        {
            /// <summary>Trails a target from an offset.</summary>
            public class FollowCamera : MonoBehaviour
            {
                [SerializeField] private Transform target;
                [SerializeField] private float smooth = 5f;

                private static readonly int EMISSION_ID = Shader.PropertyToID("_EmissionColor");

                public void SetTarget(Transform t) => target = t;

                private void LateUpdate()
                {
                    if (target == null) return;
                    transform.position = Vector3.Lerp(
                        transform.position, target.position, smooth * Time.deltaTime);
                    transform.LookAt(target.position + Vector3.up);
                }
            }
        }
        """,
        file_path="Assets/Scripts/Game/FollowCamera.cs",
    )
    by_qname = {u.qualified_name: u for u in units}
    module = "Assets.Scripts.Game.FollowCamera"
    assert units[0].qualified_name == module
    assert units[0].imports == ["UnityEngine"]
    cam = by_qname[f"{module}.FollowCamera"]
    assert cam.kind == UnitKind.CLASS
    assert cam.bases == ["MonoBehaviour"]
    assert cam.docstring == "Trails a target from an offset."
    assert by_qname[f"{module}.FollowCamera.EMISSION_ID"].kind == UnitKind.CONSTANT
    set_target = by_qname[f"{module}.FollowCamera.SetTarget"]
    assert set_target.kind == UnitKind.METHOD
    assert set_target.signature == "SetTarget(Transform t) -> void"
    late = by_qname[f"{module}.FollowCamera.LateUpdate"]
    assert late.kind == UnitKind.METHOD
    assert late.calls == sorted({"Vector3.Lerp", "transform.LookAt"})
    # [SerializeField] instance fields are not constants.
    assert f"{module}.FollowCamera.target" not in by_qname


# ---------------------------------------------------------------------------
# Fixture repo
# ---------------------------------------------------------------------------
def test_fixture_shapes_file() -> None:
    units = _parse_fixture("Geometry/Shapes.cs")
    by_qname = {u.qualified_name: u for u in units}
    module = by_qname["Geometry.Shapes"]
    assert module.kind == UnitKind.MODULE
    assert module.docstring == (
        "Geometry primitives for the sample repo.\n"
        "Exercises namespaces, bases, constants, doc comments, and local functions."
    )
    assert module.imports == sorted({
        "System",
        "System.Collections.Generic",
        "System.Math",
        "System.Numerics.Vector2",
    })
    circle = by_qname["Geometry.Shapes.Circle"]
    assert circle.kind == UnitKind.CLASS
    assert circle.bases == ["IMeasurable", "Shape"]
    assert circle.docstring == "A circle that can report its area."
    assert by_qname["Geometry.Shapes.Circle.UNIT_RADIUS"].kind == UnitKind.CONSTANT
    assert by_qname["Geometry.Shapes.Circle.label"].kind == UnitKind.CONSTANT
    assert by_qname["Geometry.Shapes.Circle.GOLDEN"].kind == UnitKind.CONSTANT
    assert "Geometry.Shapes.Circle.defaultRadius" not in by_qname
    assert by_qname["Geometry.Shapes.Circle.Circle"].signature == "Circle(double radius)"
    area = by_qname["Geometry.Shapes.Circle.Area"]
    assert area.docstring == "Computes the area.\narea in square units"
    assert area.calls == ["Square"]
    # Local functions are not units; Square's body folds into Area.
    assert "Geometry.Shapes.Circle.Area.Square" not in by_qname
    assert by_qname["Geometry.Shapes.Circle.Diameter"].kind == UnitKind.METHOD
    assert by_qname["Geometry.Shapes.Circle.Radius"].calls == ["Clamp"]
    assert "Geometry.Shapes.Circle.Label" not in by_qname  # auto-property
    cache = by_qname["Geometry.Shapes.Circle.Cache"]
    assert cache.kind == UnitKind.CLASS
    assert cache.parent_qualified_name == "Geometry.Shapes"  # nested -> module
    assert by_qname["Geometry.Shapes.Circle.Cache.Clear"].kind == UnitKind.METHOD


def test_fixture_enemy_ai_file() -> None:
    units = _parse_fixture("Game/EnemyAI.cs")
    by_qname = {u.qualified_name: u for u in units}
    enemy = by_qname["Game.EnemyAI.EnemyAI"]
    assert enemy.bases == ["MonoBehaviour"]
    assert by_qname["Game.EnemyAI.EnemyAI.FIRE_HASH"].kind == UnitKind.CONSTANT
    assert "Game.EnemyAI.EnemyAI.player" not in by_qname  # [SerializeField] field
    assert "Game.EnemyAI.EnemyAI.IsAlerted" not in by_qname  # auto-property
    update = by_qname["Game.EnemyAI.EnemyAI.Update"]
    assert update.calls == sorted({
        "Vector3.MoveTowards",
        "Vector3.Distance",
        "Fire",
    })
    fire = by_qname["Game.EnemyAI.EnemyAI.Fire"]
    assert fire.docstring == "Spawns a projectile aimed at the player."
    assert fire.calls == sorted({"GameObject", "GetComponent"})
    assert by_qname["Game.EnemyAI.EnemyAI.Alert"].kind == UnitKind.METHOD


def test_fixture_models_file_scoped_namespace() -> None:
    units = _parse_fixture("Models.cs")
    by_qname = {u.qualified_name: u for u in units}
    assert units[0].qualified_name == "Models"
    measurable = by_qname["Models.IMeasurable"]
    assert measurable.kind == UnitKind.CLASS
    assert measurable.docstring == "Anything with a measurable area."
    assert by_qname["Models.IMeasurable.Area"].kind == UnitKind.METHOD
    point = by_qname["Models.Point"]
    assert point.kind == UnitKind.CLASS
    assert point.docstring == "Immutable point record."
    assert by_qname["Models.NamedPoint"].bases == ["Point"]
    assert by_qname["Models.ShapeKind"].kind == UnitKind.CLASS
