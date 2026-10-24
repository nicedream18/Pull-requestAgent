# PRGuard Agent System Design

> 本文档详细说明 PRGuard 多 Agent 系统的设计哲学、Agent 职责、任务路由和协调机制。

## 1. 核心设计原则

### 1.1 Agent 职责隔离（Single Responsibility）

每个 Agent 只负责一个特定的代码审查维度，遵循「能证明才发现」的原则。这样做的好处是：

- **降低假阳性率**：Agent 不会跨越自己的界限去猜测
- **便于调试和改进**：每个 Agent 的问题可以独立追踪和修复
- **支持演进**：新增 Agent 不会影响现有 Agent 的行为

### 1.2 动态路由（Dynamic Routing）

根据 PR 的风险等级动态选择需要的 Agent，而不是所有 PR 都用所有 Agent。这样做的优势是：

- **性能优化**：文档改动只需要 CodeQualityAgent，无需运行安全检查
- **成本控制**：减少 LLM API 调用次数
- **用户体验**：降低反馈延迟

### 1.3 并行执行（Concurrent Execution）

所有被选中的 Agent 并行运行（受信号量限制），而不是串行执行：

- 大幅降低总体执行时间（3-4 个 Agent 从 30s 降到 10s）
- 每个 Agent 有独立的超时和重试机制，互不影响
- 错误隔离：一个 Agent 失败不会导致整个系统崩溃

---

## 2. Agent 角色与职责

### 2.1 CodeQualityAgent（必需）

**角色**：代码质量审查员

**职责**：发现代码中可以证明的逻辑、异常安全性和边界缺陷

**允许的问题类型**（`issue_types`）：
- `null_dereference`：空指针访问
- `missing_exception_handling`：缺少异常处理
- `boundary_condition`：边界条件错误（如 off-by-one）
- `resource_leak`：资源泄漏
- `division_by_zero`：除以零
- `other`：其他逻辑缺陷

**严格的界限规则**（`rules`）：
```
A defect must state the input or state that triggers it. 
Missing type hints, missing docstrings and naming preferences are advisory, never defects.
```

**核心要求**：
- 每个缺陷必须有明确的触发条件（trigger condition）
- 必须给出期望行为（expected_behavior）和实际行为（actual_behavior）
- 缺乏类型注解、缺少文档等是建议（advisory），不是缺陷（defect）

**示例**（假想）：
```json
{
  "issue_type": "null_dereference",
  "finding_type": "defect",
  "trigger_condition": "user 参数为 None 时",
  "expected_behavior": "应该抛出异常或返回错误",
  "actual_behavior": "直接访问 user.id，导致 AttributeError",
  "evidence": "Line 42: return user.id"
}
```

---

### 2.2 SecurityAgent（高风险 PR）

**角色**：应用安全审查员

**职责**：发现可利用的注入、不安全的命令执行、秘密泄露和权限缺陷

**允许的问题类型**：
- `sql_injection`：SQL 注入
- `unsafe_command`：不安全的命令执行（如 shell=True）
- `hardcoded_secret`：硬编码的秘密（密钥、密码、令牌）
- `overly_permissive_file_mode`：过于宽松的文件权限
- `other`：其他安全问题

**严格的界限规则**：
```
Name the source of the untrusted input and the sink it reaches. 
Parameterized queries, argument vectors and least-privilege modes are safe: do not report them.
```

**关键点**：
- **必须** 说出不可信输入的来源和危险函数的接收点
- **不能** 报告已经使用参数化查询、argument vectors 或最小权限的代码
- 例如 `cursor.execute(query, params)` 是安全的，但 `cursor.execute(f"SELECT * FROM users WHERE id={user_id}")` 是不安全的

**示例**：
```json
{
  "issue_type": "sql_injection",
  "finding_type": "defect",
  "trigger_condition": "search_term 来自用户输入，未验证",
  "expected_behavior": "应该使用参数化查询",
  "actual_behavior": "使用字符串插值：cursor.execute(f'SELECT * FROM items WHERE name={search_term}')",
  "evidence": "Line 15: cursor.execute(f'SELECT * FROM items WHERE name={search_term}')"
}
```

---

### 2.3 TestImpactAgent（非文档改动）

**角色**：测试影响分析员

**职责**：判断改动是否进行了不可逆或多资源行为的测试

**允许的问题类型**：
- `missing_test_coverage`：缺少测试覆盖
- `other`

**严格的界限规则**（这是最容易出错的地方）：
```
Report missing_test_coverage as a defect ONLY when:
  - 改动的代码执行不可逆或多资源行为（多个变更、外部写入、部分失败状态）
  - AND 这种行为没有测试

A plain helper function with no tests is an advisory, not a defect.
Test gaps outside the diff are advisory.
```

**重要区分**：
- ❌ **不是缺陷**：简单的辅助函数没有测试
- ❌ **不是缺陷**：diff 之外的测试缺口
- ✅ **是缺陷**：删除库存会更新数据库 + 调用外部 API，但没有测试这个组合行为

**示例**（是缺陷）：
```json
{
  "issue_type": "missing_test_coverage",
  "finding_type": "defect",
  "trigger_condition": "when order.cancel() is called",
  "expected_behavior": "应该测试数据库回滚 + 库存恢复 + 用户通知的完整流程",
  "actual_behavior": "新增 order.cancel() 执行三个操作但没有测试",
  "evidence": "Line 32-45: database update + inventory restore + notification call, no test"
}
```

---

### 2.4 ContextAgent（高风险 PR）

**角色**：代码上下文分析员

**职责**：解释改动如何与相关文件交互，并发现跨文件的破坏

**允许的问题类型**：
- `cross_file_breakage`：跨文件的破坏
- `other`

**严格的界限规则**：
```
If the related context is insufficient to prove breakage, return an advisory or an empty list.
Never invent call sites that are not in the supplied context.
```

**关键点**：
- 这个 Agent 会收到改动文件 + 相关的调用方文件（由 context builder 提供）
- 只能根据提供的上下文做出判断，**不能虚构**不存在的调用点
- 如果提供的上下文不足以证明破坏，返回 advisory 或空列表

**示例**：
```json
{
  "issue_type": "cross_file_breakage",
  "finding_type": "defect",
  "trigger_condition": "when utils.parse_date() changes return format from string to datetime",
  "expected_behavior": "应该更新所有调用点的处理逻辑",
  "actual_behavior": "caller.py:42 仍然假设返回值是 string，调用 .split()",
  "evidence": "caller.py:42: date_str = parse_date(user_input).split('-')"
}
```

---

## 3. 动态路由算法

### 3.1 风险分类（Risk Classification）

首先，系统对 PR 进行确定性的风险分类，分为三个等级：

#### LOW（低风险）
- 所有改动文件都是文档（`.md`, `.rst`, `.txt`）
- 或改动很小（≤ 2 个文件，≤ 40 行）

#### MEDIUM（中风险）
- 改动 3-8 个文件，或 40-150 行代码
- 但没有安全敏感的标记

#### HIGH/LARGE（高风险）
- 改动 ≥ 40 个文件 或 ≥ 600 行（LARGE）
- 或改动涉及安全敏感关键词：auth, permission, token, secret, password, crypto, payment, sql, query, shell, exec 等（HIGH）
- 或 diff 中包含危险模式：`eval(`, `exec(`, `subprocess`, `cursor.execute`, 等（HIGH）

```python
def classify_risk(snapshot: PRSnapshot) -> RiskLevel:
    changed = {item.path.lower() for item in snapshot.files}
    total_changes = sum(item.changes for item in snapshot.files)
    
    # 检查文件数量和改动大小
    if total_changes >= 600 or len(changed) >= 40:
        return RiskLevel.LARGE
    
    # 检查安全敏感关键词
    security_tokens = ("auth", "permission", "token", "secret", ...)
    if any(token in path for path in changed for token in security_tokens):
        return RiskLevel.HIGH
    
    # 检查 diff 中的危险模式
    if any(pattern in snapshot.diff.lower() for pattern in (...)):
        return RiskLevel.HIGH
    
    # 检查规模
    if len(changed) > 8 or total_changes >= 150:
        return RiskLevel.MEDIUM
    
    # 检查是否只有文档改动
    if all(path.endswith((".md", ".rst", ".txt")) for path in changed):
        return RiskLevel.LOW
    
    return RiskLevel.LOW
```

### 3.2 路由决策（Routing Decision）

系统支持三种路由策略：

#### Strategy 1: `single`
```
仅运行: CodeQualityAgent
用途: 快速单 Agent 审查
场景: 测试/演示
```

#### Strategy 2: `static`
```
运行: CodeQualityAgent + TestImpactAgent + SecurityAgent + ContextAgent
用途: 完整的全面审查
场景: 关键项目，要求最高覆盖率
```

#### Strategy 3: `dynamic`（推荐）
```
根据 PR 属性动态选择 Agent：

1. CodeQualityAgent（总是运行）

2. TestImpactAgent 的选择规则：
   - 如果 PR 仅包含文档改动 → 不运行
   - 否则 → 运行

3. SecurityAgent & ContextAgent 的选择规则：
   - 如果 risk_level 为 HIGH 或 LARGE → 都运行
   - 否则 → 都不运行

示例：
  - PR: 更新 README → [CodeQualityAgent]
  - PR: 添加新的工具函数 → [CodeQualityAgent, TestImpactAgent]
  - PR: 修改密钥生成逻辑 → [CodeQualityAgent, TestImpactAgent, SecurityAgent, ContextAgent]
```

```python
def route_agents(snapshot: PRSnapshot, risk: RiskLevel, strategy: str) -> list[str]:
    if strategy == "single":
        return ["CodeQualityAgent"]
    
    if strategy == "static":
        return list(AGENTS)
    
    # Dynamic routing
    selected = ["CodeQualityAgent"]
    
    # 检查是否只有文档改动
    docs_only = all(item.path.lower().endswith((".md", ".rst", ".txt")) 
                    for item in snapshot.files)
    
    if not docs_only:
        selected.append("TestImpactAgent")
    
    if risk in {RiskLevel.HIGH, RiskLevel.LARGE}:
        selected.extend(["SecurityAgent", "ContextAgent"])
    
    return selected
```

---

## 4. Agent 协调机制

### 4.1 并行执行

所有被选中的 Agent 并行运行，受以下限制：

```python
CONCURRENCY_LIMIT = 4  # 同时最多 4 个 Agent
TIMEOUT_PER_AGENT = 30  # 每个 Agent 30 秒超时
MAX_RETRIES = 1  # 失败重试 1 次
```

**优点**：
- 3-4 个 Agent 的总执行时间 ≈ 1 个 Agent 的时间
- 单个 Agent 的超时/失败不会拖累其他 Agent

### 4.2 错误隔离

- 每个 Agent 运行在独立的 Task 中
- 如果一个 Agent 出错（超时、异常），不会中断其他 Agent
- 失败的 Agent 的发现结果为空列表 `[]`

### 4.3 上下文传递

所有 Agent 共享同一份上下文信息：
- PR diff（改动了什么）
- 改动文件列表
- 对于 ContextAgent：相关的调用方文件内容

---

## 5. 质量保证机制

### 5.1 三层验证

发现在进入报告前需要通过三层检查：

**Layer 1: 基本验证**
- Schema 检查：JSON 格式正确
- 路径检查：`file_path` 必须在改动文件列表中
- 行号检查：`start_line` 在合理范围内
- 证据检查：引用的代码必须在源文件中找到
- 去重检查：相同的缺陷不能报告两次

**Layer 2: AST 级别的缺陷验证**
- 对于每种 `issue_type`，有特定的验证规则
- 例如 SQL 注入：必须有参数化查询的检查
- 例如 资源泄漏：必须有 `with open` 或 `try/finally` 的验证
- 如果验证失败，发现被降级为 advisory，加上 `needs_human_review` 标记

**Layer 3: 批评家（可选）**
- 目前故意未实现，因为需要额外的 token 预算
- 用途：对低置信度或冲突的发现进行人工评估

### 5.2 发现分类

每个发现都被分为两种类型：

- **defect**：确认的缺陷，需要修复
- **advisory**：建议，不一定要修复

判断规则：
- ✅ defect：能通过所有验证层的发现
- ⚠️ advisory：触发验证条件但证据不足，或超出 Agent 职责边界的发现

---

## 6. 为什么这个设计有效

### 6.1 假阳性率从 93% 降到 20%

**问题链**：
1. v1 Prompt 没有要求 `trigger_condition`、`expected_behavior`、`actual_behavior`
2. 模型随意报告没有源的发现
3. 路径字段写成 `unknown`、空字符串或不存在的文件名

**解决方案**：
- v2 Prompt 添加了明确的分类要求
- 三层验证中的 Layer 1 拒绝了 21 个没有有效路径的发现
- Layer 2 AST 验证通过语法树而不是文本匹配来验证缺陷

**结果**：
- 从 28 个发现中 7 个正确（25% 精度）
- 到 8 个发现中 8 个正确（100% 精度）在这 8 个上

### 6.2 Agent 职责隔离防止越界

**案例**：TestImpactAgent 最容易犯的错误
- ❌ v1：「这个函数没有测试」→ 作为缺陷报告（假阳性）
- ✅ v2：添加规则：「只有在不可逆行为（多资源变更）没有测试时才报告缺陷」

结果：同样的 PR，虚假的「缺少测试」警告从 29 个减少到几个。

### 6.3 动态路由降低成本

- 文档改动 PR（1% 成本）：只需要 1 个 Agent
- 普通代码改动 PR（3% 成本）：2 个 Agent  
- 高风险改动 PR（100% 成本）：4 个 Agent

平均成本 ≈ 2.73 次 Agent 调用/PR，而 static 策略需要 4 次。

---

## 7. 设计决策的权衡

### 为什么不用单一的大 Agent？

**缺点**：
- 模型需要跨越多个领域的专业知识，质量下降
- 难以调试：不知道哪个领域出错了
- 难以改进：改进某个领域会影响其他领域

**多 Agent 的优点**：
- 每个 Agent 可以专注于一个领域，prompt 更清晰
- 问题定位快：某个 issue_type 的假阳性来自某个 Agent
- 演进快：改进一个 Agent 的 prompt 无需重新评估其他 Agent

### 为什么 Agent 运行时并行而不是依赖？

**串行的缺点**：
- 总执行时间 = sum(each agent time)，通常 30-40 秒
- Agent 1 失败会阻止 Agent 2 运行
- 反馈延迟长

**并行的优点**：
- 总执行时间 ≈ max(each agent time)，通常 10-15 秒
- 错误隔离
- 更好的用户体验

**权衡**：
- 并行运行需要更多的 API 调用并发（但对于 4 个 Agent 没有问题）
- 需要 semaphore 和 timeout 来防止资源耗尽

### 为什么不用 ReAct/Tool Use 而用 Flow？

**Tool Use 的问题**：
- Agent 可能会尝试执行代码（安全隐患）
- Agent 可能会试图修改 GitHub（不在范围内）
- 工具调用会添加多层不确定性

**CrewAI Flow 的优点**：
- 显式的任务定义和结果验证
- 没有代码执行或工具调用的风险
- 清晰的错误处理和重试逻辑

---

## 总结

PRGuard 的 Agent 设计体现了以下原则：

1. **职责隔离** → 每个 Agent 只做一件事，做好它
2. **确定性路由** → 根据客观的 PR 属性，不是随机的
3. **多层验证** → 从 schema 到 AST 到人工评审，逐层收紧
4. **并行优化** → 在保持正确性的前提下最小化延迟

这个设计是基于第一次在线运行中 68 个假阳性的分析而来的，代表了从「研究原型」到「可靠系统」的演进过程。
