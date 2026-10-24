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
    # Test files retrieved for this module, with their contents. Presence alone is
    # not proof that a specific behaviour path is covered, so the contents matter.
    test_sources: dict[str, str] | None = None
    # Name of the function that encloses the reported line, when resolvable.
    function_name: str | None = None
    # The finding's own words about which behaviour fails, used to decide whether a
    # retrieved failure-path test actually covers the claimed failure.
    claim_text: str = ""


@dataclass(frozen=True)
class ConditionVerdict:
    status: str
    reason: str
    line: int | None = None


def _resolve_scope(tree: ast.AST, line: int | None) -> tuple[ast.AST | None, bool, ast.AST | None]:
    """Return ``(function, resolved, scope_root)`` for a reported line.

    ``function`` is the enclosing function when the line sits inside one.
    ``scope_root`` is what a check must walk: the function's body, or the module-level
    statements when the finding is about top-level code.

    ``resolved`` is False when the line cannot be tied to a single block, which
    happens when the file has several top-level definitions and the finding gives no
    usable line. Checks must treat that as UNKNOWN rather than scanning the whole
    file and borrowing a defect from an unrelated location.
    """
    functions = _function_nodes(tree)
    if line is not None:
        function = _enclosing_function(tree, line)
        if function is not None:
            return function, True, function
        return None, True, _module_scope(tree)
    if len(functions) == 1:
        return functions[0], True, functions[0]
    if not functions:
        return None, True, _module_scope(tree)
    return None, False, None


class _ModuleScope(ast.AST):
    """A synthetic node holding only module-level statements, not nested definitions."""

    _fields: tuple[str, ...] = ("body",)

    def __init__(self, body: list[ast.AST]) -> None:
        self.body = body
        self.lineno = 1


def _module_scope(tree: ast.AST) -> ast.AST:
    body = [
        node
        for node in getattr(tree, "body", [])
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return _ModuleScope(body)


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


LOCATE_SLACK = 1
SCOPE_SLACK = 3


def _locate(nodes: list[ast.AST], line: int | None) -> ast.AST | None:
    """Pick the node the finding actually points at.

    A check must never use evidence from another call site to judge this one, so a
    candidate is only accepted when it can be tied to the reported line:

    * the line is inside the node's span, or
    * exactly one candidate sits within ``LOCATE_SLACK`` lines, or
    * the scope holds a single candidate within ``SCOPE_SLACK`` lines (the common
      "reported the enclosing statement" case).

    Anything else, including two equally close candidates, returns ``None`` so the
    caller can answer ``UNKNOWN`` instead of borrowing unrelated evidence.
    """
    if not nodes:
        return None
    if line is None:
        return nodes[0] if len(nodes) == 1 else None

    inside = [node for node in nodes if _span(node)[0] <= line <= _span(node)[1]]
    if inside:
        return min(inside, key=lambda node: abs(_span(node)[0] - line))

    ordered = sorted(nodes, key=lambda node: abs(_span(node)[0] - line))
    best = ordered[0]
    distance = abs(_span(best)[0] - line)
    if distance == 0:
        return best
    limit = LOCATE_SLACK if len(nodes) > 1 else SCOPE_SLACK
    if distance > limit:
        return None
    ties = [node for node in ordered if abs(_span(node)[0] - line) == distance]
    return ties[0] if len(ties) == 1 else None


def _check_sql_injection(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = scope or tree
    calls = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr in SQL_EXECUTORS)
            or (isinstance(node.func, ast.Name) and node.func.id in SQL_EXECUTORS)
        )
    ]
    if not calls:
        if DB_HINTS.search(source):
            return ConditionVerdict(UNKNOWN, "database usage is present but no SQL execution call is recognised")
        return ConditionVerdict(CONTRADICTED, "the reported block contains no database call, so it cannot inject SQL")

    call = _locate(calls, context.line)
    if call is None:
        return ConditionVerdict(
            UNKNOWN,
            f"the reported line is not tied to one of the {len(calls)} query calls in this block, "
            "so another call site cannot be used as evidence",
        )
    if not call.args:
        return ConditionVerdict(UNKNOWN, "SQL call passes no query argument", call.lineno)

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
            UNKNOWN, "query has placeholders but no parameters are passed here", call.lineno
        )
    return ConditionVerdict(UNKNOWN, "the query argument is not statically classifiable", call.lineno)


def _check_unsafe_command(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = scope or tree
    sinks = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call)
        and (
            _dotted(node.func) in SHELL_SINKS
            or _dotted(node.func) in SUBPROCESS_CALLS
            or _dotted(node.func).startswith("subprocess.")
        )
    ]
    if not sinks:
        return ConditionVerdict(UNKNOWN, "no recognised shell or subprocess invocation in the reported block")

    call = _locate(sinks, context.line)
    if call is None:
        return ConditionVerdict(
            UNKNOWN,
            f"the reported line is not tied to one of the {len(sinks)} process calls in this block",
        )

    dotted = _dotted(call.func)
    if dotted in SHELL_SINKS:
        return ConditionVerdict(SUPPORTED, f"{dotted} always passes the command to a shell", call.lineno)
    if dotted == "asyncio.create_subprocess_shell":
        return ConditionVerdict(SUPPORTED, "create_subprocess_shell always uses a shell", call.lineno)
    if dotted.endswith("create_subprocess_exec"):
        return ConditionVerdict(CONTRADICTED, "create_subprocess_exec never uses a shell", call.lineno)
    if any(kw.arg is None for kw in call.keywords):
        # `**kwargs` may carry shell=True, so absence here proves nothing.
        return ConditionVerdict(
            UNKNOWN, "a keyword-argument unpacking could supply shell=True, so this call cannot be cleared", call.lineno
        )
    shell = next((kw for kw in call.keywords if kw.arg == "shell"), None)
    if shell is not None and isinstance(shell.value, ast.Constant) and shell.value.value is True:
        return ConditionVerdict(SUPPORTED, "subprocess is invoked with shell=True", call.lineno)
    if any(kw.arg == "shell" for kw in call.keywords):
        return ConditionVerdict(CONTRADICTED, "shell is passed a non-true value", call.lineno)
    if len(call.args) >= 2 and isinstance(call.args[0], ast.Constant):
        return ConditionVerdict(CONTRADICTED, "a constant string program is executed without a shell", call.lineno)
    return ConditionVerdict(CONTRADICTED, "subprocess runs without shell=True", call.lineno)


def _check_file_mode(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = scope or tree
    calls = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call)
        and (
            _dotted(node.func) in {"os.chmod", "chmod", "os.fchmod", "pathlib.Path.chmod"}
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "chmod")
        )
    ]
    if not calls:
        return ConditionVerdict(UNKNOWN, "no chmod call in the reported block")

    call = _locate(calls, context.line)
    if call is None:
        return ConditionVerdict(
            UNKNOWN, f"the reported line is not tied to one of the {len(calls)} chmod calls in this block"
        )
    mode_arg = call.args[1] if len(call.args) >= 2 else next(
        (kw.value for kw in call.keywords if kw.arg == "mode"), None
    )
    if mode_arg is None:
        return ConditionVerdict(UNKNOWN, "chmod is called without a resolvable mode argument", call.lineno)
    if not isinstance(mode_arg, ast.Constant) or not isinstance(mode_arg.value, int):
        return ConditionVerdict(UNKNOWN, "file mode is not a literal, so other-user access cannot be proven", call.lineno)
    mode = mode_arg.value
    if mode & 0o002:
        return ConditionVerdict(SUPPORTED, f"mode {oct(mode)} grants write access to other users", call.lineno)
    return ConditionVerdict(CONTRADICTED, f"mode {oct(mode)} does not grant other-user write", call.lineno)


def _check_hardcoded_secret(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = scope or tree
    assignments = []
    for node in ast.walk(scope):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(t, ast.Name) and SECRET_NAME.search(t.id) for t in targets):
            assignments.append(node)
    if not assignments:
        return ConditionVerdict(UNKNOWN, "no credential-named assignment in the reported scope")

    node = _locate(assignments, context.line)
    if node is None:
        return ConditionVerdict(
            UNKNOWN, f"the reported line is not tied to one of the {len(assignments)} credential assignments"
        )
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [t.id for t in targets if isinstance(t, ast.Name)]
    label = names[0] if names else "the credential"
    value = node.value
    if isinstance(value, ast.Call):
        callee = _dotted(value.func)
        if callee.endswith("getenv") or callee in {"os.environ.get", "environ.get"}:
            return ConditionVerdict(CONTRADICTED, f"{label} is read from the environment", node.lineno)
        return ConditionVerdict(UNKNOWN, f"{label} is assigned a call result that cannot be inspected", node.lineno)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        if SECRET_VALUE.match(value.value.strip()):
            return ConditionVerdict(SUPPORTED, f"{label} is assigned a credential-like literal", node.lineno)
        return ConditionVerdict(CONTRADICTED, f"{label} is assigned a short non-credential literal", node.lineno)
    return ConditionVerdict(UNKNOWN, f"{label} is assigned a value that is not a literal", node.lineno)


def _check_division_by_zero(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = scope or tree
    function = scope if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    params = _params(function) if function is not None else set()
    divisions = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod))
    ]
    if not divisions:
        return ConditionVerdict(UNKNOWN, "no division found in the reported block")

    node = _locate(divisions, context.line)
    if node is None:
        return ConditionVerdict(
            UNKNOWN, f"the reported line is not tied to one of the {len(divisions)} divisions in this block"
        )
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


def _check_resource_leak(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = scope or tree
    opens = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and _dotted(node.func) in {"open", "io.open", "codecs.open"}
    ]
    if not opens:
        return ConditionVerdict(UNKNOWN, "no open() call in the reported block")

    node = _locate(opens, context.line)
    if node is None:
        return ConditionVerdict(
            UNKNOWN, f"the reported line is not tied to one of the {len(opens)} open() calls in this block"
        )
    if _inside(node, scope, (ast.With, ast.AsyncWith)):
        return ConditionVerdict(CONTRADICTED, "the reported open() call is inside a with-block", node.lineno)
    closed = any(
        isinstance(other, ast.Call)
        and isinstance(other.func, ast.Attribute)
        and other.func.attr == "close"
        and other.lineno >= node.lineno
        for other in ast.walk(scope)
    )
    if closed:
        return ConditionVerdict(UNKNOWN, "a close() call exists on a later path, so coverage is path dependent", node.lineno)
    return ConditionVerdict(SUPPORTED, "the reported open() call is outside a with-block and never closed", node.lineno)


def _check_missing_exception_handling(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    scope = scope or tree
    calls = [node for node in ast.walk(scope) if isinstance(node, ast.Call) and _dotted(node.func) in RAISING_CALLS]
    if not calls:
        return ConditionVerdict(UNKNOWN, "no exception-raising call in the reported block")

    node = _locate(calls, context.line)
    if node is None:
        return ConditionVerdict(
            UNKNOWN, f"the reported line is not tied to one of the {len(calls)} raising calls in this block"
        )
    if _inside(node, scope, (ast.Try,)):
        return ConditionVerdict(CONTRADICTED, "the reported call is inside a try block", node.lineno)
    return ConditionVerdict(
        SUPPORTED, f"{_dotted(node.func)} escapes without a handler at this location", node.lineno
    )


def _check_null_dereference(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    function = scope if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    if function is None:
        return ConditionVerdict(CONTRADICTED, "module-level code has no parameter that could be Optional")
    optional = _optional_params(function)
    if not optional:
        return ConditionVerdict(CONTRADICTED, "the function has no Optional-typed parameter")

    reads = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in optional
    ]
    if not reads:
        return ConditionVerdict(CONTRADICTED, "no Optional parameter is read as an attribute")

    node = _locate(reads, context.line)
    if node is None:
        return ConditionVerdict(
            UNKNOWN, f"the reported line is not tied to one of the {len(reads)} dereferences in this function"
        )
    if _guard_protects(function, node.value.id, node.lineno):
        return ConditionVerdict(CONTRADICTED, f"'{node.value.id}' is guarded before this dereference", node.lineno)
    return ConditionVerdict(
        SUPPORTED,
        f"'{node.value.id}' is Optional and read as .{node.attr} with no dominating None check",
        node.lineno,
    )


def _check_boundary(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    """Confirm an off-by-one only when the extra iteration actually indexes out of range."""
    scope = scope or tree
    loops = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and isinstance(node.iter, ast.Call)
        and _dotted(node.iter.func) == "range"
        and len(node.iter.args) == 1
    ]
    if not loops:
        if any(isinstance(node, ast.Subscript) for node in ast.walk(scope)):
            return ConditionVerdict(UNKNOWN, "indexing is present but no loop bound can be inspected")
        return ConditionVerdict(UNKNOWN, "the reported block has no loop bound to inspect")

    loop = _locate(loops, context.line)
    if loop is None:
        return ConditionVerdict(
            UNKNOWN, f"the reported line is not tied to one of the {len(loops)} loops in this block"
        )
    index = loop.target.id
    arg = loop.iter.args[0]

    if (
        isinstance(arg, ast.BinOp)
        and isinstance(arg.op, ast.Add)
        and isinstance(arg.right, ast.Constant)
        and arg.right.value == 1
        and isinstance(arg.left, ast.Call)
        and _dotted(arg.left.func) == "len"
    ):
        reads = [
            sub
            for sub in ast.walk(loop)
            if isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Name) and sub.slice.id == index
        ]
        if not reads:
            return ConditionVerdict(
                CONTRADICTED,
                f"range(len(...) + 1) iterates once more but {index} is never used to index, so it cannot raise",
                loop.lineno,
            )
        guard = _dominating_guard(loop, index, reads)
        if guard is not None:
            return ConditionVerdict(
                CONTRADICTED,
                f"the extra iteration is dominated by a bounds check at line {guard.lineno}",
                reads[0].lineno,
            )
        return ConditionVerdict(
            SUPPORTED,
            f"range(len(...) + 1) with {index} used as an index reads one past the end",
            loop.lineno,
        )

    for node in ast.walk(loop):
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

    subscripts = [sub for sub in ast.walk(loop) if isinstance(sub, ast.Subscript)]
    if subscripts and all(
        isinstance(sub.slice, ast.Name) and sub.slice.id == index for sub in subscripts
    ):
        return ConditionVerdict(
            CONTRADICTED, f"for {index} in range(len(...)) indexes exactly the valid range", loop.lineno
        )
    return ConditionVerdict(UNKNOWN, "the loop bound relation is not statically decidable")


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
    return False


def _nested_loop_node_ids(loop: ast.For) -> set[int]:
    """Ids of every node inside a loop nested in `loop`.

    A `break` inside a nested loop leaves that inner loop, not the loop under
    analysis, so a guard found there must not be used as evidence for the outer one.
    """
    ids: set[int] = set()
    for child in ast.walk(loop):
        if child is loop or not isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
            continue
        for sub in ast.walk(child):
            ids.add(id(sub))
    return ids


def _body_always_exits(body: list[ast.stmt]) -> bool:
    """True only when every path through `body` leaves the current scope.

    A statement that merely *contains* a break or return is not enough: statements
    before an unconditional exit fall through to it, but an `if` without an `else`
    cannot be assumed to exit.
    """
    for statement in body:
        if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return True
        if isinstance(statement, ast.If) and statement.orelse:
            if _body_always_exits(statement.body) and _body_always_exits(statement.orelse):
                return True
    return False


def _loop_bound_is_len_plus_one(loop: ast.For, index: str) -> bool:
    """`for i in range(len(x) + 1)` is the only shape where `i == len(x)` is the bad value."""
    iterable = loop.iter
    if not isinstance(iterable, ast.Call) or _dotted(iterable.func) != "range" or len(iterable.args) != 1:
        return False
    arg = iterable.args[0]
    return (
        isinstance(arg, ast.BinOp)
        and isinstance(arg.op, ast.Add)
        and isinstance(arg.right, ast.Constant)
        and arg.right.value == 1
        and isinstance(arg.left, ast.Call)
        and _dotted(arg.left.func) == "len"
    )


def _rejects_out_of_range(test: ast.AST, index: str, loop: ast.For) -> bool:
    """True when the condition sends an out-of-range `index` out of the iteration.

    Only these forms are sufficient for `range(len(x) + 1)`:

    * ``i >= len(x)``          rejects i == len(x) and above
    * ``i > len(x) - 1``       same set, written differently
    * ``i == len(x)``          rejects exactly the one value that loop can produce

    ``i > len(x)`` is *not* sufficient: it lets i == len(x) through, which is the
    out-of-range iteration itself.
    """
    if not isinstance(test, ast.Compare) or not isinstance(test.left, ast.Name):
        return False
    if test.left.id != index or len(test.ops) != 1 or not test.comparators:
        return False
    op = test.ops[0]
    right = test.comparators[0]

    if isinstance(right, ast.Call) and _dotted(right.func) == "len":
        if isinstance(op, ast.GtE):
            return True
        if isinstance(op, ast.Eq):
            return _loop_bound_is_len_plus_one(loop, index)
        return False

    if (
        isinstance(op, ast.Gt)
        and isinstance(right, ast.BinOp)
        and isinstance(right.op, ast.Sub)
        and isinstance(right.left, ast.Call)
        and _dotted(right.left.func) == "len"
        and isinstance(right.right, ast.Constant)
        and right.right.value == 1
    ):
        return True
    return False


def _guards_positive(test: ast.AST, index: str) -> bool:
    """True only for `if i < len(seq):`, which excludes the index equal to len(seq).

    `i <= len(seq)` must not qualify: it admits the out-of-range iteration.
    """
    if not isinstance(test, ast.Compare) or not isinstance(test.left, ast.Name):
        return False
    if test.left.id != index or len(test.ops) != 1 or not test.comparators:
        return False
    right = test.comparators[0]
    if not (isinstance(right, ast.Call) and _dotted(right.func) == "len"):
        return False
    return isinstance(test.ops[0], ast.Lt)


def _dominating_guard(loop: ast.For, index: str, reads: list[ast.Subscript]) -> ast.If | None:
    """Return the bounds check that protects **every** read of `index`.

    A guard must cover all reads, not just one of them, and a guard that lives inside
    a nested loop cannot protect the outer loop's reads.
    """
    if not reads:
        return None
    nested = _nested_loop_node_ids(loop)
    read_lines = [_span(read)[0] for read in reads]

    for node in ast.walk(loop):
        if not isinstance(node, ast.If) or id(node) in nested:
            continue
        if not _name_checked(ast.unparse(node.test), index):
            continue
        start, end = _span(node)

        if _rejects_out_of_range(node.test, index, loop) and _body_always_exits(node.body):
            # Reject-style guard: it must run before every read it protects.
            if all(end < line for line in read_lines):
                return node
            continue

        if _guards_positive(node.test, index):
            # Positive guard: every protected read must sit inside its body.
            if all(start <= line <= end for line in read_lines):
                return node
    return None


RAISES_HELPERS = {"raises", "assertRaises", "assertRaisesRegex", "raises"}


def _dotted_call_name(expr: ast.AST) -> str:
    target = expr.func if isinstance(expr, ast.Call) else expr
    return _dotted(target).split(".")[-1]


def _is_raises_context(expr: ast.AST) -> bool:
    return _dotted_call_name(expr) in RAISES_HELPERS


def _asserted_exceptions(expr: ast.AST) -> list[str]:
    """Exception names passed to a raises helper, e.g. `pytest.raises(ValueError)`."""
    if not isinstance(expr, ast.Call) or not expr.args:
        return []
    first = expr.args[0]
    names: list[str] = []
    if isinstance(first, ast.Name):
        names.append(first.id)
    elif isinstance(first, ast.Attribute):
        names.append(first.attr)
    elif isinstance(first, ast.Tuple):
        for element in first.elts:
            if isinstance(element, ast.Name):
                names.append(element.id)
            elif isinstance(element, ast.Attribute):
                names.append(element.attr)
    return names


def _raises_blocks_for(test_source: str, name: str) -> list[list[str]]:
    """Exception names of every raises-block that wraps a call to `name`."""
    try:
        tree = ast.parse(test_source)
    except (SyntaxError, ValueError):
        return []
    blocks: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        context = [item.context_expr for item in node.items if _is_raises_context(item.context_expr)]
        if not context:
            continue
        calls_it = any(
            isinstance(sub, ast.Call) and _dotted_call_name(sub) == name for sub in ast.walk(node)
        )
        if calls_it:
            blocks.append([exc for expr in context for exc in _asserted_exceptions(expr)])
    return blocks


def _test_calls_function(test_source: str, name: str) -> bool:
    try:
        tree = ast.parse(test_source)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted_call_name(node) == name:
            return True
    return False


def _claim_names_failure(claim: str, exceptions: list[str]) -> bool:
    """True when the finding's own wording names the asserted exception.

    A `pytest.raises(ValueError)` block proves that *some* failure is tested. It only
    refutes this finding when the finding is about that same failure, which is what
    the exception name appearing in the claim establishes.
    """
    if not exceptions:
        # `pytest.raises(...)` without a resolvable type, or `raises(match=...)`.
        return False
    lowered = claim.lower()
    return any(name.lower() in lowered for name in exceptions)


def _check_missing_test_coverage(
    tree: ast.AST, scope: ast.AST | None, source: str, context: "ConditionContext"
) -> ConditionVerdict:
    """Three levels, because a diff cannot prove the absence of a test.

    * a retrieved test asserts an exception around the changed function -> CONTRADICTED
    * a retrieved test calls it, but only on the happy path -> UNKNOWN, because the
      claimed failure path is still untested
    * a retrieved test merely mentions it, or no test was retrieved -> UNKNOWN
    * the changed code has no risky behaviour -> CONTRADICTED, there is nothing to cover

    Reference is not coverage, and coverage of a normal path is not coverage of the
    failure path the finding is about.
    """
    scope = scope or tree
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

    tests = {path: text for path, text in (context.test_sources or {}).items() if text}
    name = context.function_name
    if not tests:
        return ConditionVerdict(
            UNKNOWN,
            "the changed code is risky but no test content was retrieved, and a bounded context cannot prove absence",
        )
    if not name:
        return ConditionVerdict(
            UNKNOWN,
            "test files were retrieved but the reported line does not identify which function they should cover",
        )

    failure_blocks = {path: _raises_blocks_for(text, name) for path, text in tests.items()}
    with_failure = {path: blocks for path, blocks in failure_blocks.items() if blocks}
    if with_failure:
        matching = sorted(
            path
            for path, blocks in with_failure.items()
            if any(_claim_names_failure(context.claim_text, exceptions) for exceptions in blocks)
        )
        if matching:
            asserted = sorted(
                {exc for blocks in with_failure.values() for exceptions in blocks for exc in exceptions}
            )
            return ConditionVerdict(
                CONTRADICTED,
                f"a retrieved test asserts {', '.join(asserted) or 'an exception'} around {name}, "
                f"which is the failure this finding describes: {matching[0]}",
            )
        return ConditionVerdict(
            UNKNOWN,
            f"a retrieved test asserts an exception around {name}, but the finding does not name that "
            "failure, so the specific claimed behaviour remains unverified",
        )

    calling = sorted(path for path, text in tests.items() if _test_calls_function(text, name))
    if calling:
        return ConditionVerdict(
            UNKNOWN,
            f"a retrieved test calls {name} on the happy path only ({calling[0]}), so the claimed "
            "failure path is still unproven",
        )
    return ConditionVerdict(
        UNKNOWN,
        f"retrieved test files ({', '.join(sorted(tests)[:2])}) do not call {name}, and indirect "
        "coverage cannot be ruled out from the supplied text",
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
    test_sources: dict[str, str] | None = None,
    claim_text: str = "",
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

    function, resolved, scope = _resolve_scope(tree, line)
    if not resolved:
        return ConditionVerdict(
            UNKNOWN,
            "the reported location does not identify a single function, so no in-file evidence can be tied to it",
        )
    context = ConditionContext(
        line=line,
        test_sources=test_sources or {},
        function_name=getattr(function, "name", None) if function is not None else None,
        claim_text=claim_text,
    )
    if scope is None:
        return ConditionVerdict(UNKNOWN, "no analyzable scope for the reported line")
    return check(tree, scope, source, context)

