from __future__ import annotations

import textwrap

from core.parsing import TreeSitterParser
from schemas import Language, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"


def _parse(source: str, file_path: str = "src/app.js") -> list:
    lang = (
        Language.TYPESCRIPT
        if file_path.endswith((".ts", ".tsx", ".mts", ".cts"))
        else Language.JAVASCRIPT
    )
    return TreeSitterParser(lang).parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def test_module_unit_first_with_full_source() -> None:
    units = _parse("const x = 1;\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "src.app"
    assert units[0].name == "app"
    assert units[0].language == Language.JAVASCRIPT
    assert units[0].content == "const x = 1;\n"
    assert units[0].line_start == 1


def test_index_file_collapses_module_qname() -> None:
    units = _parse("const x = 1;\n", file_path="src/utils/index.js")
    assert units[0].qualified_name == "src.utils"
    assert units[0].name == "utils"


def test_typescript_module_language() -> None:
    units = _parse("const x: number = 1;\n", file_path="src/lib.ts")
    assert units[0].language == Language.TYPESCRIPT


def test_syntax_error_still_returns_module_unit() -> None:
    # Broken function followed by a healthy const — error-tolerant parse.
    units = _parse("function broken( { if (x {\nconst X = 1;\n")
    assert units[0].kind == UnitKind.MODULE


def test_module_docstring_from_leading_block_comment() -> None:
    units = _parse("""
        /* App entry point. */
        const x = 1;
    """)
    assert units[0].docstring == "App entry point."


def test_function_declarations() -> None:
    units = _parse("""
        function plain(a, b) { return a; }
        async function fetcher(url) { return url; }
        function* gen(x) { yield x; }
        export default function App() { return null; }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.plain"].kind == UnitKind.FUNCTION
    assert by_qname["src.app.plain"].signature == "plain(a, b)"
    assert by_qname["src.app.fetcher"].signature == "async fetcher(url)"
    assert by_qname["src.app.gen"].kind == UnitKind.FUNCTION
    assert by_qname["src.app.App"].kind == UnitKind.FUNCTION


def test_arrow_and_function_expression_bindings() -> None:
    units = _parse("""
        const useAuth = (token) => token;
        const bare = x => x;
        const legacy = function(a) { return a; };
        export const exported = async () => 1;
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.useAuth"].kind == UnitKind.FUNCTION
    assert by_qname["src.app.useAuth"].signature == "useAuth(token)"
    assert by_qname["src.app.bare"].signature == "bare(x)"
    assert by_qname["src.app.legacy"].kind == UnitKind.FUNCTION
    assert by_qname["src.app.exported"].signature == "async exported()"


def test_class_with_methods_fields_and_extends() -> None:
    units = _parse("""
        class Service extends BaseService {
          static VERSION = "1";
          constructor(cfg) { this.cfg = cfg; }
          async handle(req) { return req; }
          onClick = () => 1;
        }
        class Plain extends React.Component {}
    """)
    by_qname = {u.qualified_name: u for u in units}
    svc = by_qname["src.app.Service"]
    assert svc.kind == UnitKind.CLASS
    assert svc.bases == ["BaseService"]
    assert by_qname["src.app.Service.constructor"].kind == UnitKind.METHOD
    assert by_qname["src.app.Service.handle"].kind == UnitKind.METHOD
    assert by_qname["src.app.Service.handle"].signature == "async handle(req)"
    # Class-property arrow functions are methods in practice (React).
    assert by_qname["src.app.Service.onClick"].kind == UnitKind.METHOD
    # UPPER_CASE static field is a constant.
    assert by_qname["src.app.Service.VERSION"].kind == UnitKind.CONSTANT
    # parent chain
    assert by_qname["src.app.Service.handle"].parent_qualified_name == "src.app.Service"
    # member-expression base
    assert by_qname["src.app.Plain"].bases == ["React.Component"]


def test_top_level_constants_upper_case_only() -> None:
    units = _parse("""
        const MAX_RETRIES = 5;
        const lower = 1;
        export const NAMED_EXPORT = 2;
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.MAX_RETRIES"].kind == UnitKind.CONSTANT
    assert by_qname["src.app.NAMED_EXPORT"].kind == UnitKind.CONSTANT
    assert "src.app.lower" not in by_qname


def test_jsdoc_becomes_docstring() -> None:
    units = _parse("""
        /**
         * Fetches the user.
         * @param token auth token
         */
        const useAuth = (token) => token;
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.useAuth"].docstring == "Fetches the user.\n@param token auth token"


def test_typescript_signatures_and_skipped_type_decls() -> None:
    units = _parse(
        """
        interface Props { name: string }
        type Alias = string;
        enum Color { Red }
        export function score(a: number, b: number): number { return a + b; }
        class Svc extends Base implements IFace {
          handle(req: Request): void {}
        }
        """,
        file_path="src/lib.ts",
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.lib.score"].signature == "score(a: number, b: number): number"
    assert by_qname["src.lib.Svc"].bases == ["Base"]  # implements is not inheritance
    assert by_qname["src.lib.Svc.handle"].kind == UnitKind.METHOD
    # Type-level declarations carry no runtime logic — skipped.
    assert "src.lib.Props" not in by_qname
    assert "src.lib.Alias" not in by_qname
    assert "src.lib.Color" not in by_qname


def test_tsx_component_extracted() -> None:
    units = _parse(
        "export default function App() { return <div>hi</div>; }\n",
        file_path="src/App.tsx",
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.App.App"].kind == UnitKind.FUNCTION


def test_children_sorted_by_line() -> None:
    units = _parse("""
        function b() {}
        function a() {}
        const C = 1;
    """)
    rest = units[1:]
    starts = [u.line_start for u in rest]
    assert starts == sorted(starts)


def test_abstract_class_extracted() -> None:
    units = _parse(
        """
        abstract class Base {
          handle(req: Request): void {}
        }
        export abstract class Exported {}
        """,
        file_path="src/abs.ts",
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.abs.Base"].kind == UnitKind.CLASS
    assert by_qname["src.abs.Base.handle"].kind == UnitKind.METHOD
    assert by_qname["src.abs.Exported"].kind == UnitKind.CLASS


def test_js_private_field_method_extracted() -> None:
    units = _parse("""
        class P {
          #hidden = () => 1;
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.P.#hidden"].kind == UnitKind.METHOD


def test_calls_and_references_extracted() -> None:
    units = _parse("""
        function run(input) {
          const user = fetchUser(input);
          api.client.refresh(user);
          this.helper();
          obj["dynamic"]();
          return user;
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    run = by_qname["src.app.run"]
    # Dotted chains reconstructed; unresolvable subscript call skipped.
    assert run.calls == sorted({"fetchUser", "api.client.refresh", "this.helper"})
    # Identifier references include params and locals (validator dedupes+sorts).
    assert "input" in run.references
    assert "user" in run.references


def test_calls_inside_nested_closures_attributed_to_outer_fn() -> None:
    units = _parse("""
        const handler = () => {
          items.forEach(item => process(item));
        };
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert "process" in by_qname["src.app.handler"].calls
    assert "items.forEach" in by_qname["src.app.handler"].calls


def test_import_statement_variants() -> None:
    units = _parse(
        """
        import React from "react";
        import { useState, useEffect as ue } from "react";
        import * as path from "node:path";
        import "./styles.css";
        import { score } from "./scorer";
        import { deep } from "../shared/util";
        const fs = require("fs");
        const local = require("./local");
        export { helper } from "./util";
        """,
        file_path="app/ats/run.js",
    )
    module = units[0]
    assert module.imports == sorted({
        "react",                    # default import — module only
        "react.useState",
        "react.useEffect",          # original name, not the alias
        "node:path",                # namespace import — module only
        "app.ats.styles.css",       # side-effect relative import, resolved
        "app.ats.scorer.score",     # named relative import
        "app.shared.util.deep",     # ../ resolution
        "fs",                       # require, bare
        "app.ats.local",            # require, relative
        "app.ats.util.helper",      # re-export is an import
    })


def test_relative_import_index_collapse_and_root_escape() -> None:
    units = _parse(
        """
        import { x } from "./utils/index";
        import { y } from "../../outside";
        """,
        file_path="src/app.js",
    )
    module = units[0]
    # ./utils/index collapses; ../../ escapes the repo root — kept verbatim.
    assert module.imports == sorted({"src.utils.x", "../../outside.y"})


def test_bare_specifier_subpath_uses_dots() -> None:
    units = _parse('import merge from "lodash/merge";\n')
    assert units[0].imports == ["lodash.merge"]


def test_new_expression_recorded_as_call() -> None:
    units = _parse("""
        function build() {
          const svc = new Service();
          const w = new pkg.Worker(1);
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    calls = by_qname["src.app.build"].calls
    assert "Service" in calls
    assert "pkg.Worker" in calls


def test_typescript_generic_signature() -> None:
    units = _parse(
        "export function identity<T>(x: T): T { return x; }\n",
        file_path="src/gen.ts",
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.gen.identity"].signature == "identity<T>(x: T): T"


def test_degenerate_import_specifiers_skipped() -> None:
    units = _parse(
        """
        import x from "../";
        import y from "";
        import { ok } from "./real";
        """,
        file_path="src/app.js",
    )
    assert units[0].imports == ["src.real.ok"]
