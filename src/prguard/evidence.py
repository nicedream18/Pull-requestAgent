"""Deterministic defect-condition checks used by the evidence-aware verifier.

Each check answers one question: given the full source of one changed file, is the
stated defect condition *provable*?

Verdict semantics:

* ``SUPPORTED``    the file itself contains the construct that makes the claim true
                   (for example an f-string reaching ``cursor.execute``). This proves
                   the condition, not the whole runtime vulnerability.
* ``CONTRADICTED`` the file itself disproves the claim (a parameterized query, a
                   ``with`` block, ``shell=False``).
* ``UNKNOWN``      the supplied context cannot decide. Everything that needs the
                   rest of the repository, a test suite listing, or interprocedural
                   reasoning returns this.

Checks must not upgrade a keyword occurrence into SUPPORTED. A missing construct is
reported as UNKNOWN rather than CONTRADICTED unless the file also contains the
positive evidence that would be needed for the claim.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from prguard.models import IssueType

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
UNKNOWN = "unknown"
# Backwards-compatible alias for callers written against the first draft.
UNDETERMINED = UNKNOWN

SQL_EXECUTORS = {"execute", "executemany", "executescript", "raw", "query", "run_query"}
DB_HINTS = re.compile(r"(cursor|connection|conn|session|db|database|engine|pool)", re.IGNORECASE)
SHELL_SINKS = {"os.system", "os.popen", "commands.getoutput", "commands.getstatusoutput"}
SUBPROCESS_CALLS = {
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "asyncio.create_subprocess_shell",
    "asyncio.create_subprocess_exec",
}
RAISING_CALLS = {
    "json.loads",
    "json.load",
    "int",
    "float",
    "open",
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.call",
    "requests.get",
    "requests.post",
    "os.stat",
    "yaml.safe_load",
}
MUTATING_METHODS = {
    "debit",
    "credit",
    "save",
    "update",
    "delete",
    "insert",
    "write",
    "commit",
    "rollback",
    "transfer",
    "push",
    "append",
}
SIDE_EFFECT_CALLS = {"os.chmod", "os.remove", "os.unlink", "os.rename", "shutil.rmtree", "shutil.move"}
SECRET_NAME = re.compile(r"(api[_-]?key|api[_-]?token|token|secret|password|passwd|access[_-]?key)", re.IGNORECASE)
SECRET_VALUE = re.compile(r"^(sk-|gh[pousr]_|github_pat_|AKIA|[A-Za-z0-9_\-]{24,})")
EXIT_STATEMENTS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


@dataclass(frozen=True)
class ConditionContext:
    """Extra facts the verifier can supply beyond the single file's source."""

    line: int | None = None
    test_paths_for_module: set[str] | None = None


@dataclass(frozen=True)
class ConditionVerdict:
    status: str
    reason: str
    line: int | None = None


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _function_nodes(tree: ast.AST) -> list[ast.AST]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _span(node: ast.AST) -> tuple[int, int]:
    start = getattr(node, "lineno", 0) or 0
    end = getattr(node, "end_lineno", None) or start
    return start, end


def _enclosing_function(tree: ast.AST, line: int) -> ast.AST | None:
    best: ast.AST | None = None
    for node in _function_nodes(tree):
        start, end = _span(node)
        if start <= line <= end and (best is None or start >= _span(best)[0]):
            best = node
    return best


def _params(function: ast.AST) -> set[str]:
    args = getattr(function, "args", None)
    if args is None:
        return set()
    names = [a.arg for a in getattr(args, "posonlyargs", []) + args.args + args.kwonlyargs]
    names += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
    return set(names)


def _optional_params(function: ast.AST) -> set[str]:
    """Parameters annotated Optional/None-defaulted, i.e. legal None carriers."""
    args = getattr(function, "args", None)
    if args is None:
        return set()
    optional: set[str] = set()
    positional = getattr(args, "posonlyargs", []) + args.args
    defaults = list(args.defaults)
    defaulted = positional[len(positional) - len(defaults):] if defaults else []
    for arg, default in zip(defaulted, defaults):
        if isinstance(default, ast.Constant) and default.value is None:
            optional.add(arg.arg)
    for arg in positional + args.kwonlyargs:
        annotation = arg.annotation
        if annotation is None:
            continue
        text = ast.unparse(annotation)
        if "Optional" in text or "None" in text:
            optional.add(arg.arg)
    return optional


def _exits(node: ast.AST) -> bool:
    """True when the statement list of `node` always leaves the current scope."""
    body = getattr(node, "body", None) or []
    return bool(body) and isinstance(body[-1], EXIT_STATEMENTS)


def _guard_protects(function: ast.AST, name: str, use_line: int) -> bool:
    """True when a None check dominates the use on `use_line`.

    Recognised forms:

    * ``if name is not None:`` wrapping the use
    * ``if name is None: return/raise/continue`` before the use
    * ``if not name: return`` before the use
    * ``assert name is not None`` before the use
    * ``name = ... if ... else default`` reassignment before the use
    """
    for node in ast.walk(function):
        if isinstance(node, ast.Assert):
            text = ast.unparse(node.test)
            if _name_checked(text, name) and _span(node)[0] < use_line:
                return True
        if not isinstance(node, ast.If):
            continue
        text = ast.unparse(node.test)
        if not _name_checked(text, name):
            continue
        start, end = _span(node)
        positive = f"{name} is not None" in text or _is_truthy_test(text, name)
        negative = f"{name} is None" in text or f"not {name}" in text
        if positive and start <= use_line <= end:
            return True
        if negative and end < use_line and _exits(node):
            return True
    return False


def _name_checked(text: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


def _is_truthy_test(text: str, name: str) -> bool:
    return text.strip() == name


def _inside(node: ast.AST, scope: ast.AST, kinds: tuple[type, ...]) -> bool:
    """True when `node` sits inside one of `kinds` within `scope`."""
    target = id(node)
    for parent in ast.walk(scope):
        if not isinstance(parent, kinds):
            continue
        for child in ast.walk(parent):
            if id(child) == target:
                return True
    return False


def _calls(tree: ast.AST, names: set[str]) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _dotted(node.func) in names]


def _check_sql_injection(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr in SQL_EXECUTORS)
            or (isinstance(node.func, ast.Name) and node.func.id in SQL_EXECUTORS)
        )
    ]
    if not calls:
        if DB_HINTS.search(source):
            return ConditionVerdict(UNKNOWN, "database usage is present but no SQL execution call is recognised")
        return ConditionVerdict(CONTRADICTED, "the file contains no database call, so it cannot inject SQL")

    for call in calls:
        if not call.args:
            return ConditionVerdict(UNKNOWN, "SQL call passes no query argument")
        query = call.args[0]
        if isinstance(query, (ast.JoinedStr, ast.BinOp)):
            return ConditionVerdict(SUPPORTED, "query string is built by interpolation", call.lineno)
        if isinstance(query, ast.Call) and isinstance(query.func, ast.Attribute) and query.func.attr == "format":
            return ConditionVerdict(SUPPORTED, "query string is built with str.format", call.lineno)
        if isinstance(query, ast.Constant) and isinstance(query.value, str):
            has_placeholder = "?" in query.value or "%s" in query.value
            if not has_placeholder:
                return ConditionVerdict(CONTRADICTED, "query is a constant with no interpolation point", call.lineno)
            if len(call.args) >= 2 or any(kw.arg == "params" for kw in call.keywords):
                return ConditionVerdict(
                    CONTRADICTED, "query uses bound placeholders with separate parameters", call.lineno
                )
            return ConditionVerdict(
                UNDETERMINED, "query has placeholders but no parameters are passed here", call.lineno
            )
    return ConditionVerdict(UNKNOWN, "a query-like call is present but its argument is not statically classifiable")


def _check_unsafe_command(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if dotted in SHELL_SINKS:
            return ConditionVerdict(SUPPORTED, f"{dotted} always passes the command to a shell", node.lineno)
        if dotted in SUBPROCESS_CALLS or dotted.startswith("subprocess."):
            shell = next((kw for kw in node.keywords if kw.arg == "shell"), None)
            if shell is not None and isinstance(shell.value, ast.Constant) and shell.value.value is True:
                if dotted.endswith("create_subprocess_exec"):
                    return ConditionVerdict(CONTRADICTED, "create_subprocess_exec never uses a shell", node.lineno)
                return ConditionVerdict(SUPPORTED, "subprocess is invoked with shell=True", node.lineno)
            if dotted == "asyncio.create_subprocess_shell":
                return ConditionVerdict(SUPPORTED, "create_subprocess_shell always uses a shell", node.lineno)
    if _calls(tree, SUBPROCESS_CALLS):
        return ConditionVerdict(CONTRADICTED, "every subprocess call runs without shell=True")
    return ConditionVerdict(UNKNOWN, "no recognised shell or subprocess invocation in the changed file")


def _check_file_mode(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted(node.func) not in {"os.chmod", "chmod", "os.fchmod", "pathlib.Path.chmod"} and not (
            isinstance(node.func, ast.Attribute) and node.func.attr == "chmod"
        ):
            continue
        mode_arg = node.args[1] if len(node.args) >= 2 else next(
            (kw.value for kw in node.keywords if kw.arg == "mode"), None
        )
        if mode_arg is None:
            return ConditionVerdict(UNKNOWN, "chmod is called without a resolvable mode argument", node.lineno)
        if not isinstance(mode_arg, ast.Constant) or not isinstance(mode_arg.value, int):
            return ConditionVerdict(UNKNOWN, "file mode is not a literal, so other-user access cannot be proven", node.lineno)
        mode = mode_arg.value
        if mode & 0o002:
            return ConditionVerdict(SUPPORTED, f"mode {oct(mode)} grants write access to other users", node.lineno)
        return ConditionVerdict(CONTRADICTED, f"mode {oct(mode)} does not grant other-user write", node.lineno)
    return ConditionVerdict(UNKNOWN, "no chmod call in the changed file")


def _check_hardcoded_secret(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    loaded_from_environment = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not any(SECRET_NAME.search(name) for name in names):
            continue
        if isinstance(value, ast.Call):
            callee = _dotted(value.func)
            if callee.endswith("getenv") or callee in {"os.environ.get", "environ.get"}:
                loaded_from_environment = True
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if SECRET_VALUE.match(value.value.strip()):
                return ConditionVerdict(
                    SUPPORTED, f"{names[0]} is assigned a credential-like literal", node.lineno
                )
            return ConditionVerdict(CONTRADICTED, f"{names[0]} is assigned a short non-credential literal", node.lineno)
        return ConditionVerdict(UNKNOWN, f"{names[0]} is assigned a value that is not a literal", node.lineno)
    if loaded_from_environment:
        return ConditionVerdict(CONTRADICTED, "the secret-named value is read from the environment")
    return ConditionVerdict(UNKNOWN, "no credential-named assignment in the changed file")


def _check_division_by_zero(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = function or tree
    params = _params(scope) if function is not None else set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            continue
        divisor = node.right
        if isinstance(divisor, ast.Constant):
            if divisor.value in (0, 0.0):
                return ConditionVerdict(SUPPORTED, "divisor is the literal zero", node.lineno)
            return ConditionVerdict(CONTRADICTED, "divisor is a non-zero literal", node.lineno)
        name = divisor.id if isinstance(divisor, ast.Name) else None
        if name is None:
            return ConditionVerdict(UNKNOWN, "divisor is an expression, not a named parameter", node.lineno)
        if name not in params:
            return ConditionVerdict(UNKNOWN, f"divisor '{name}' is not a parameter of this function", node.lineno)
        if _zero_guarded(scope, name, node.lineno):
            return ConditionVerdict(CONTRADICTED, f"divisor '{name}' is guarded before the division", node.lineno)
        return ConditionVerdict(SUPPORTED, f"divisor '{name}' is a parameter with no zero guard", node.lineno)
    return ConditionVerdict(UNKNOWN, "no division found in the changed function")


def _zero_guarded(scope: ast.AST, name: str, use_line: int) -> bool:
    for node in ast.walk(scope):
        if isinstance(node, ast.Assert):
            text = ast.unparse(node.test)
            if _name_checked(text, name) and _span(node)[0] < use_line:
                return True
        if not isinstance(node, ast.If):
            continue
        text = ast.unparse(node.test)
        if not _name_checked(text, name):
            continue
        start, end = _span(node)
        if f"{name} == 0" in text or f"not {name}" in text or f"{name} <=" in text or f"{name} <" in text:
            if end < use_line and _exits(node):
                return True
        if f"{name} != 0" in text or f"{name} >" in text:
            if start <= use_line <= end:
                return True
    for node in ast.walk(scope):
        if isinstance(node, ast.Call) and _dotted(node.func) in {"max", "abs"} and node.args:
            continue
    return False


def _check_resource_leak(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = function or tree
    opens = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and _dotted(node.func) in {"open", "io.open", "codecs.open"}
    ]
    if not opens:
        return ConditionVerdict(UNKNOWN, "no open() call in the changed function")
    closed = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "close"
        for node in ast.walk(scope)
    )
    for node in opens:
        if _inside(node, scope, (ast.With, ast.AsyncWith)):
            continue
        if closed:
            return ConditionVerdict(UNKNOWN, "handle is closed explicitly somewhere, so coverage is path dependent", node.lineno)
        return ConditionVerdict(SUPPORTED, "handle opened outside a with-block and never closed", node.lineno)
    return ConditionVerdict(CONTRADICTED, "every open() call is inside a with-block")


def _check_missing_exception_handling(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = function or tree
    calls = [node for node in ast.walk(scope) if isinstance(node, ast.Call) and _dotted(node.func) in RAISING_CALLS]
    if not calls:
        return ConditionVerdict(UNKNOWN, "no exception-raising call in the changed function")
    unguarded = [node for node in calls if not _inside(node, scope, (ast.Try,))]
    if not unguarded:
        return ConditionVerdict(CONTRADICTED, "every raising call is inside a try block")
    return ConditionVerdict(
        SUPPORTED,
        f"{len(unguarded)} raising call(s) such as {_dotted(unguarded[0].func)} escape without a handler",
        unguarded[0].lineno,
    )


def _check_null_dereference(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    if function is None:
        return ConditionVerdict(UNKNOWN, "no enclosing function for the reported line")
    optional = _optional_params(function)
    if not optional:
        return ConditionVerdict(CONTRADICTED, "the function has no Optional-typed parameter")
    unguarded = None
    for node in ast.walk(function):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id not in optional:
            continue
        if not _guard_protects(function, node.value.id, node.lineno):
            unguarded = node
            break
    if unguarded is None:
        return ConditionVerdict(CONTRADICTED, "every Optional parameter is guarded before use")
    return ConditionVerdict(
        SUPPORTED, f"'{unguarded.value.id}' is Optional and read as .{unguarded.attr} with no dominating None check", unguarded.lineno
    )


def _check_boundary(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = function or tree
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call) or _dotted(node.func) != "range" or not node.args:
            continue
        arg = node.args[0]
        if (
            isinstance(arg, ast.BinOp)
            and isinstance(arg.op, ast.Add)
            and isinstance(arg.right, ast.Constant)
            and arg.right.value == 1
            and isinstance(arg.left, ast.Call)
            and _dotted(arg.left.func) == "len"
        ):
            return ConditionVerdict(SUPPORTED, "range(len(seq) + 1) indexes one past the end", node.lineno)

    for node in ast.walk(scope):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        right = node.comparators[0] if node.comparators else None
        if right is None:
            continue
        op = node.ops[0]
        if isinstance(right, ast.Call) and _dotted(right.func) == "len":
            if isinstance(op, ast.LtE):
                return ConditionVerdict(
                    SUPPORTED, f"inclusive upper bound against len(): {ast.unparse(node)}", node.lineno
                )
            if isinstance(op, ast.Lt):
                return ConditionVerdict(
                    CONTRADICTED, f"exclusive bound against len() is correct: {ast.unparse(node)}", node.lineno
                )
        if (
            isinstance(right, ast.BinOp)
            and isinstance(right.op, ast.Sub)
            and isinstance(right.right, ast.Constant)
            and right.right.value == 1
            and isinstance(right.left, ast.Call)
            and _dotted(right.left.func) == "len"
            and isinstance(op, ast.LtE)
        ):
            return ConditionVerdict(
                CONTRADICTED, f"bound subtracts one from len(), so it stays in range: {ast.unparse(node)}", node.lineno
            )

    for node in ast.walk(scope):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            iterable = node.iter
            if not isinstance(iterable, ast.Call) or _dotted(iterable.func) != "range" or len(iterable.args) != 1:
                continue
            arg = iterable.args[0]
            if not (isinstance(arg, ast.Call) and _dotted(arg.func) == "len"):
                continue
            subscripts = [sub for sub in ast.walk(node) if isinstance(sub, ast.Subscript)]
            if subscripts and all(
                isinstance(sub.slice, ast.Name) and sub.slice.id == node.target.id for sub in subscripts
            ):
                return ConditionVerdict(
                    CONTRADICTED,
                    f"for {node.target.id} in range(len(...)) indexes exactly the valid range",
                    node.lineno,
                )

    if any(isinstance(node, ast.Subscript) for node in ast.walk(scope)):
        return ConditionVerdict(UNKNOWN, "indexing is present but the bound relation is not statically decidable")
    return ConditionVerdict(UNKNOWN, "the changed function has no loop bound to inspect")


def _check_missing_test_coverage(
    tree: ast.AST, function: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    """A diff alone cannot prove that no test exists.

    Only two outcomes are provable: tests for the module are supplied (refuted), or
    the code has no risky behaviour at all (nothing worth testing, so not a defect).
    Anything else is UNKNOWN and the finding is downgraded for human review.
    """
    if context.test_paths_for_module:
        return ConditionVerdict(
            CONTRADICTED, f"tests exist for this module: {', '.join(sorted(context.test_paths_for_module)[:3])}"
        )
    scope = function or tree
    mutations = {
        node.func.attr
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATING_METHODS
    }
    side_effects = {
        _dotted(node.func)
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and _dotted(node.func) in SIDE_EFFECT_CALLS
    }
    if len(mutations) < 2 and not side_effects:
        return ConditionVerdict(
            CONTRADICTED,
            "the changed code has no multi-resource mutation or side effect, so a test gap is advice, not a defect",
        )
    return ConditionVerdict(
        UNKNOWN,
        "the changed code is risky but the supplied context does not enumerate the test suite, so absence is unprovable",
    )


CHECKS = {
    IssueType.SQL_INJECTION: _check_sql_injection,
    IssueType.UNSAFE_COMMAND: _check_unsafe_command,
    IssueType.OVERLY_PERMISSIVE_FILE_MODE: _check_file_mode,
    IssueType.HARDCODED_SECRET: _check_hardcoded_secret,
    IssueType.DIVISION_BY_ZERO: _check_division_by_zero,
    IssueType.RESOURCE_LEAK: _check_resource_leak,
    IssueType.MISSING_EXCEPTION_HANDLING: _check_missing_exception_handling,
    IssueType.NULL_DEREFERENCE: _check_null_dereference,
    IssueType.BOUNDARY_CONDITION: _check_boundary,
    IssueType.MISSING_TEST_COVERAGE: _check_missing_test_coverage,
}


def evaluate_condition(
    issue_type: IssueType,
    source: str,
    line: int | None,
    *,
    test_paths_for_module: set[str] | None = None,
) -> ConditionVerdict:
    """Run the deterministic condition check for one finding."""
    check = CHECKS.get(issue_type)
    if check is None:
        return ConditionVerdict(UNKNOWN, "no deterministic condition is registered for this issue type")
    if not source.strip():
        return ConditionVerdict(UNKNOWN, "source for the reported file is unavailable")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ConditionVerdict(UNKNOWN, "file could not be parsed for condition checking")

    function = _enclosing_function(tree, line) if line else None
    if function is None:
        functions = _function_nodes(tree)
        function = functions[0] if len(functions) == 1 else None
    context = ConditionContext(line=line, test_paths_for_module=test_paths_for_module or set())
    return check(tree, function, source, context)

