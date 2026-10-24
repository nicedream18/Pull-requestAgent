"""Deterministic defect-condition checks used by the evidence-aware verifier.

Each check answers one question: given the full source of the changed file, is the
stated defect condition actually reachable? Checks are AST based. Keyword matching
is only used to *refute* a claim (for example a parameterized query), never as
proof that a defect exists.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from prguard.models import IssueType

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
UNDETERMINED = "undetermined"

SQL_EXECUTORS = {"execute", "executemany", "executescript", "raw", "query"}
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
SECRET_NAME = re.compile(r"(api[_-]?key|api[_-]?token|token|secret|password|passwd|access[_-]?key)", re.IGNORECASE)
SECRET_VALUE = re.compile(r"^(sk-|gh[pousr]_|github_pat_|AKIA|[A-Za-z0-9_\-]{24,})")


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
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _enclosing_function(tree: ast.AST, line: int) -> ast.AST | None:
    best: ast.AST | None = None
    for node in _function_nodes(tree):
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", 10**6) or 10**6
        if start <= line <= end and (best is None or start >= getattr(best, "lineno", 0)):
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


def _has_none_guard(function: ast.AST, name: str) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.If):
            text = ast.unparse(node.test)
            if f"{name} is not None" in text or f"not {name}" in text or f"{name} is None" in text:
                return True
        if isinstance(node, ast.Assert) and f"{name} is not None" in ast.unparse(node.test):
            return True
    return False


def _inside(node: ast.AST, tree: ast.AST, kinds: tuple[type, ...]) -> bool:
    """True when `node` sits inside one of `kinds` in the same function body."""
    target = id(node)
    for parent in ast.walk(tree):
        if not isinstance(parent, kinds):
            continue
        for child in ast.walk(parent):
            if id(child) == target:
                return True
    return False


def _calls(tree: ast.AST, names: set[str]) -> list[ast.Call]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func) in names:
            found.append(node)
    return found


def _check_sql_injection(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
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
        return ConditionVerdict(CONTRADICTED, "no SQL execution call found in the changed file")
    for call in calls:
        if not call.args:
            continue
        query = call.args[0]
        # f-string / %-format / .format(...) all splice values into SQL syntax.
        if isinstance(query, (ast.JoinedStr, ast.BinOp)):
            return ConditionVerdict(SUPPORTED, "query string is built by interpolation", call.lineno)
        if isinstance(query, ast.Call) and isinstance(query.func, ast.Attribute) and query.func.attr == "format":
            return ConditionVerdict(SUPPORTED, "query string is built with str.format", call.lineno)
        if isinstance(query, ast.Constant) and isinstance(query.value, str):
            has_placeholder = "?" in query.value or "%s" in query.value
            if has_placeholder and len(call.args) >= 2:
                return ConditionVerdict(
                    CONTRADICTED, "query uses bound placeholders with separate parameters", call.lineno
                )
            if not has_placeholder:
                return ConditionVerdict(CONTRADICTED, "query is a constant without user input", call.lineno)
            if has_placeholder and len(call.args) == 1:
                return ConditionVerdict(
                    CONTRADICTED, "query has placeholders but parameters are not passed as arguments", call.lineno
                )
    return ConditionVerdict(UNDETERMINED, "SQL call present but argument shape is not classifiable")


def _check_unsafe_command(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    shell_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if dotted == "os.system":
            return ConditionVerdict(SUPPORTED, "os.system passes the command to a shell", node.lineno)
        if dotted.startswith(("subprocess.", "asyncio.create_subprocess_")):
            shell = next((kw for kw in node.keywords if kw.arg == "shell"), None)
            if shell is not None and isinstance(shell.value, ast.Constant) and shell.value.value is True:
                shell_calls.append(node)
    if shell_calls:
        return ConditionVerdict(SUPPORTED, "subprocess is invoked with shell=True", shell_calls[0].lineno)
    if _calls(tree, {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output"}):
        return ConditionVerdict(CONTRADICTED, "subprocess runs without shell=True")
    return ConditionVerdict(CONTRADICTED, "no shell invocation found")


def _check_file_mode(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if dotted not in {"os.chmod", "chmod", "Path.chmod", "os.fchmod"}:
            continue
        if len(node.args) < 2:
            continue
        mode_arg = node.args[1]
        if not isinstance(mode_arg, ast.Constant) or not isinstance(mode_arg.value, int):
            return ConditionVerdict(UNDETERMINED, "file mode is not a literal, cannot prove world access", node.lineno)
        mode = mode_arg.value
        if mode & 0o002:
            return ConditionVerdict(SUPPORTED, f"mode {oct(mode)} grants write access to other users", node.lineno)
        return ConditionVerdict(CONTRADICTED, f"mode {oct(mode)} does not grant other-user write", node.lineno)
    return ConditionVerdict(CONTRADICTED, "no chmod call found")


def _check_hardcoded_secret(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        if not SECRET_VALUE.match(value.value.strip()):
            continue
        for target in targets:
            if isinstance(target, ast.Name) and SECRET_NAME.search(target.id):
                return ConditionVerdict(SUPPORTED, f"{target.id} is assigned a credential-like literal", node.lineno)
    return ConditionVerdict(CONTRADICTED, "no credential-like literal assignment found")


def _check_division_by_zero(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    scope = function or tree
    params = _params(scope) if function is not None else set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            continue
        divisor = node.right
        if isinstance(divisor, ast.Constant) and divisor.value not in (0, 0.0):
            continue
        name = divisor.id if isinstance(divisor, ast.Name) else None
        if name and name in params:
            guarded = any(
                isinstance(other, ast.If) and name in ast.unparse(other.test)
                for other in ast.walk(scope)
            )
            if not guarded:
                return ConditionVerdict(SUPPORTED, f"divisor '{name}' is an unchecked parameter", node.lineno)
            return ConditionVerdict(CONTRADICTED, f"divisor '{name}' has a guard before use", node.lineno)
        if isinstance(divisor, ast.Constant) and divisor.value in (0, 0.0):
            return ConditionVerdict(SUPPORTED, "divisor is the literal zero", node.lineno)
    return ConditionVerdict(UNDETERMINED, "no parameter-driven division found in the changed function")


def _check_resource_leak(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    scope = function or tree
    opens = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and _dotted(node.func) in {"open", "io.open", "codecs.open"}
    ]
    if not opens:
        return ConditionVerdict(UNDETERMINED, "no open() call found in the changed function")
    closed = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "close"
        for node in ast.walk(scope)
    )
    for node in opens:
        if _inside(node, scope, (ast.With, ast.AsyncWith)):
            continue
        if closed:
            return ConditionVerdict(CONTRADICTED, "handle is closed explicitly on a later path", node.lineno)
        return ConditionVerdict(SUPPORTED, "handle opened outside a with-block and never closed", node.lineno)
    return ConditionVerdict(CONTRADICTED, "every open() call is inside a with-block")


def _check_missing_exception_handling(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    scope = function or tree
    calls = [node for node in ast.walk(scope) if isinstance(node, ast.Call) and _dotted(node.func) in RAISING_CALLS]
    if not calls:
        return ConditionVerdict(UNDETERMINED, "no exception-raising call found in the changed function")
    unguarded = [node for node in calls if not _inside(node, scope, (ast.Try,))]
    if not unguarded:
        return ConditionVerdict(CONTRADICTED, "every raising call is inside a try block")
    return ConditionVerdict(
        SUPPORTED,
        f"{len(unguarded)} raising call(s) such as {_dotted(unguarded[0].func)} escape without a handler",
        unguarded[0].lineno,
    )


def _check_null_dereference(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    if function is None:
        return ConditionVerdict(UNDETERMINED, "no enclosing function found")
    optional = _optional_params(function)
    if not optional:
        return ConditionVerdict(CONTRADICTED, "no Optional-typed parameter in the changed function")
    for node in ast.walk(function):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id in optional and not _has_none_guard(function, node.value.id):
            return ConditionVerdict(
                SUPPORTED, f"'{node.value.id}' is Optional but read as .{node.attr} without a None check", node.lineno
            )
    return ConditionVerdict(CONTRADICTED, "every Optional parameter is guarded before use")


def _check_boundary(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    scope = function or tree
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call) or _dotted(node.func) != "range" or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
            left, right = arg.left, arg.right
            if (
                isinstance(right, ast.Constant)
                and right.value == 1
                and isinstance(left, ast.Call)
                and _dotted(left.func) == "len"
            ):
                return ConditionVerdict(SUPPORTED, "range(len(seq) + 1) indexes one past the end", node.lineno)
    for node in ast.walk(scope):
        if isinstance(node, ast.Compare):
            text = ast.unparse(node)
            if "<=" in text and "len(" in text:
                return ConditionVerdict(SUPPORTED, f"inclusive bound on a length: {text}", node.lineno)
    return ConditionVerdict(CONTRADICTED, "no off-by-one loop or inclusive length bound found")


def _check_missing_test_coverage(tree: ast.AST, function: ast.AST | None) -> ConditionVerdict:
    """Test gaps are only defects when the changed code carries real behavior risk."""
    scope = function or tree
    mutations = {
        node.func.attr
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATING_METHODS
    }
    writes = {
        node.func.attr
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"writelines", "write", "chmod", "unlink", "remove"}
    }
    if len(mutations) >= 2:
        return ConditionVerdict(
            SUPPORTED,
            f"changed code performs multiple state mutations ({', '.join(sorted(mutations))}) with no test",
            function.lineno if function is not None else None,
        )
    if writes:
        return ConditionVerdict(SUPPORTED, f"changed code performs side-effecting writes ({', '.join(sorted(writes))})")
    return ConditionVerdict(
        UNDETERMINED,
        "no behavior risk in the changed code, so a test gap is advice for the diff author, not a defect",
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


def evaluate_condition(issue_type: IssueType, source: str, line: int | None) -> ConditionVerdict:
    """Run the deterministic condition check for one finding."""
    check = CHECKS.get(issue_type)
    if check is None:
        return ConditionVerdict(UNDETERMINED, "no deterministic condition is registered for this issue type")
    if not source.strip():
        return ConditionVerdict(UNDETERMINED, "source for the reported file is unavailable")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ConditionVerdict(UNDETERMINED, "file could not be parsed for condition checking")

    function = _enclosing_function(tree, line) if line else None
    if function is None:
        functions = _function_nodes(tree)
        function = functions[0] if len(functions) == 1 else None
    return check(tree, function)
