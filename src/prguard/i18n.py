from __future__ import annotations

from prguard.models import Finding


# Offline rules use stable English phrases internally so the evaluation matcher
# remains reproducible. Reports and API responses expose the localized copy.
_FINDING_TRANSLATIONS: dict[str, tuple[str, str, str]] = {
    "SQL injection through string interpolation": (
        "字符串插值导致 SQL 注入",
        "用户输入被直接插入 SQL，攻击者可以改变查询结构并执行非预期查询。",
        "使用参数化查询绑定输入值，不要通过字符串拼接构造 SQL。",
    ),
    "Shell command injection": (
        "Shell 命令注入",
        "不可信输入被传入 Shell 执行路径，攻击者可能执行任意系统命令。",
        "使用经过校验的参数数组，并保持 Shell 执行关闭。",
    ),
    "World-writable file permissions": (
        "文件权限对所有用户可写",
        "文件被设置为所有用户可读、可写、可执行，可能导致未授权篡改或执行。",
        "只授予业务所需的最小文件权限，避免使用 0777。",
    ),
    "Hard-coded credential": (
        "硬编码凭证",
        "源码中包含疑似凭证的敏感值，提交后可能被仓库访问者或日志读取。",
        "将凭证移入密钥管理服务或环境变量，并轮换已经暴露的凭证。",
    ),
    "Loop exceeds collection bounds": (
        "循环可能越过集合边界",
        "循环会访问等于集合长度的索引，最后一次迭代可能触发越界异常。",
        "使用 `range(len(items))`，或直接遍历集合元素。",
    ),
    "Possible null dereference": (
        "可能发生空值解引用",
        "可选的 user 对象在访问属性前没有检查 None，可能触发 AttributeError。",
        "访问 user 属性前先处理 None 情况，或使用明确的默认值。",
    ),
    "Malformed JSON is not handled": (
        "未处理格式错误的 JSON",
        "无效 JSON 会抛出异常并越过当前解析边界，调用方可能收到未处理的错误。",
        "捕获 JSONDecodeError，并返回清晰的输入校验错误。",
    ),
    "File handle may leak": (
        "文件句柄可能泄露",
        "打开的文件没有交给上下文管理器管理，异常或提前返回时可能无法关闭。",
        "使用 `with open(...)`，确保所有执行路径都会关闭文件句柄。",
    ),
    "Possible division by zero": (
        "可能发生除零错误",
        "除数没有经过校验，传入零值时会触发 ZeroDivisionError。",
        "执行除法前校验除数，并为零值返回明确的业务错误。",
    ),
    "Transfer failure paths lack tests": (
        "转账失败路径缺少测试",
        "该转账逻辑会修改两个账户，但当前没有覆盖扣款失败、入账失败和部分失败回滚的测试。",
        "补充非法金额、扣款失败、入账失败以及部分失败回滚场景的测试。",
    ),
    "Explicit IGNORE policy is indistinguishable from no policy, so flow actions still fail hard under IGNORE": (
        "显式 IGNORE 策略与未设置策略无法区分",
        "当前条件无法区分工具显式声明 IGNORE 策略和完全未声明策略的情况，可能让 Flow Action 在 IGNORE 场景下仍然强制失败。",
        "显式解析工具失败策略，区分未设置策略与 IGNORE，并根据最终策略决定是否抛出异常。",
    ),
    "IGNORE policy behavior is untested; no test asserts flow continues silently without an event": (
        "IGNORE 策略行为缺少测试",
        "现有测试覆盖了 WARN 和 RAISE，但没有验证 IGNORE 是否能够静默继续执行且不发送失败事件。",
        "增加 IGNORE 策略测试，断言 Flow 正常继续，并且不会发送 ToolFailureDetectedEvent。",
    ),
    "No test for a tool with explicit policy returning a normal result (detect_tool_failure returns None)": (
        "缺少显式策略工具正常返回值的测试",
        "现有测试只覆盖工具返回 ToolFailure 的情况，没有验证声明显式策略的工具在正常返回值时仍能原样通过。",
        "增加正常返回值测试，确认显式策略不会改变成功工具调用的结果。",
    ),
    "RAISE path does not assert ToolFailureDetectedEvent is emitted before aborting": (
        "RAISE 路径未验证失败事件在中止前发出",
        "测试只断言 RAISE 会抛出异常，没有验证 ToolFailureDetectedEvent 是否会在 Flow 中止前发出。",
        "订阅 ToolFailureDetectedEvent，并断言 RAISE 抛出异常前已经记录对应事件。",
    ),
    "IGNORE policy branch lacks test coverage in flow tool action failure policy tests": (
        "工具失败策略测试缺少 IGNORE 分支覆盖",
        "测试文件覆盖了 WARN 和 RAISE，但没有覆盖文档中声明的 IGNORE 静默继续行为。",
        "增加 IGNORE 工具测试，断言 Flow 正常返回且事件列表为空。",
    ),
    "WARN policy test does not validate policy field of ToolFailureDetectedEvent": (
        "WARN 策略测试未校验失败事件字段",
        "测试只断言事件列表非空，没有校验事件中的策略值和失败信息是否与 WARN 工具一致。",
        "校验事件的 policy、failure.message 和 failure.code，确保可观测数据准确。",
    ),
    "handle_tool_failure import is a per-call local import in a hot tool-execution path": (
        "热点工具执行路径中按调用导入 handle_tool_failure",
        "handle_tool_failure 位于 run 方法的局部导入块中，每次工具调用都会重复执行模块查找。",
        "如果不存在循环依赖，将导入移动到模块级；否则记录必须使用局部导入的原因。",
    ),
    "WARN policy test only asserts events is non-empty; policy/failure/code on emitted event are unverified": (
        "WARN 测试未验证事件中的策略和失败字段",
        "测试只检查 events 非空，没有确认事件数量、策略值以及失败消息和代码是否正确。",
        "断言事件数量为一，并校验 WARN 策略、失败消息和失败代码。",
    ),
    "IGNORE policy path is untested though the change claims silent continuation for it": (
        "IGNORE 策略路径未测试",
        "变更声称 IGNORE 会静默继续，但没有测试 Flow 是否继续以及是否不会发送失败事件。",
        "增加 IGNORE 工具测试，断言 Flow 继续完成且事件列表为空。",
    ),
    "Tool failure policy resolution depends on getattr, not documented BaseTool contract, so subclass overrides or non-attrs tools silently diverge": (
        "失败策略解析依赖 getattr，可能导致工具行为偏离约定",
        "通过 getattr 判断策略存在性，可能混淆继承默认值、属性描述器和显式策略，导致不同工具走错失败处理分支。",
        "使用 BaseTool 约定的策略解析方法，明确区分未设置策略、继承策略和显式策略。",
    ),
    "IGNORE policy is documented as silent but has no test, so a silent abort or silent record could pass unnoticed": (
        "文档声明 IGNORE 静默执行，但缺少测试",
        "没有测试确认 IGNORE 不会中止 Flow，也不会发送 ToolFailureDetectedEvent。",
        "增加 IGNORE 策略测试，验证 Flow 返回结果且不产生失败事件。",
    ),
    "WARN assertion is weak: `assert events` does not verify the event's policy/failure fields or that exactly one event is emitted": (
        "WARN 断言过弱，未校验事件内容",
        "仅断言 events 非空无法发现重复事件、错误策略或错误失败载荷。",
        "断言只产生一个事件，并校验其策略、失败消息和失败代码。",
    ),
    "RAISE assertion depends on match text from the failure message but nothing verifies the ToolFailureDetectedEvent is emitted before abort": (
        "RAISE 断言未验证中止前的失败事件",
        "当前断言只检查异常文本，没有确认失败事件在 RAISE 中止前已经发送。",
        "在 RAISE 测试中订阅并校验 ToolFailureDetectedEvent。",
    ),
    "Presence-based policy check may silently change fail-hard semantics for inherited/default policies": (
        "基于属性存在性的策略判断可能改变默认强制失败语义",
        "只检查属性值是否非 None，无法区分显式策略、继承策略和基类默认值。",
        "确认 BaseTool 的默认策略约定，并使用显式哨兵或策略比较区分这些情况。",
    ),
    "Default fail-hard branch for tools without explicit policy lacks regression test": (
        "未设置显式策略的默认强制失败分支缺少回归测试",
        "修改后的逻辑保留了未声明策略时的强制失败分支，但现有测试没有覆盖该默认路径，后续回归可能无法被发现。",
        "增加未设置 tool_failure_policy 的工具测试，断言返回 ToolFailure 时 Flow 会抛出 ToolExecutionFailedError。",
    ),
}


def localize_finding(finding: Finding) -> Finding:
    """Return Chinese presentation text while preserving evidence and schema data."""
    translation = _FINDING_TRANSLATIONS.get(finding.title)
    if not translation:
        return finding
    title, description, suggestion = translation
    return finding.model_copy(update={
        "title": title,
        "description": description,
        "suggestion": suggestion,
    })


def localize_findings(findings: list[Finding]) -> list[Finding]:
    return [localize_finding(finding) for finding in findings]


def localize_finding_payload(payload: dict) -> dict:
    """Localize a serialized finding when reading jobs created before this change."""
    try:
        return localize_finding(Finding.model_validate(payload)).model_dump(mode="json")
    except Exception:
        return payload
