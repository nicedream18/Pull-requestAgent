# Agent Developer Cheatsheet

> 快速参考：PRGuard Agent 系统的核心概念和设计决策

---

## 系统架构一览

```
PR Input
  ↓ classify_risk()
Risk: LOW / MEDIUM / HIGH / LARGE
  ↓ route_agents()
Agents: [CodeQualityAgent, ...]
  ↓ run_agents (parallel, semaphore-limited)
Raw Findings
  ↓ verify_findings (3-layer verification)
  ├─ Layer 1: Schema + Path + Evidence
  ├─ Layer 2: AST-level defect verification
  └─ Layer 3: Human review (planned)
  ↓
Final Report (JSON + Markdown)
```

---

## 四个 Agent

| Agent | Issues | Key Rule | Runs When |
|---|---|---|---|
| **CodeQualityAgent** | null_dereference, missing_exception_handling, boundary_condition, resource_leak, division_by_zero | Defect = trigger + expected + actual behavior | ✅ Always |
| **SecurityAgent** | sql_injection, unsafe_command, hardcoded_secret, overly_permissive_file_mode | Name source & sink; parameterized queries are safe | HIGH/LARGE risk |
| **TestImpactAgent** | missing_test_coverage | Only defect if multi-resource behavior untested | Not docs-only |
| **ContextAgent** | cross_file_breakage | Never invent call sites; only use provided context | HIGH/LARGE risk |

---

## 风险分类规则

```python
if total_changes >= 600 or len(files) >= 40:
    → LARGE
elif any(security_token in path for security_token in ["auth", "secret", "sql", ...]):
    → HIGH
elif any(dangerous_pattern in diff):
    → HIGH
elif len(files) > 8 or total_changes >= 150:
    → MEDIUM
elif all_files_are_docs:
    → LOW
else:
    → LOW
```

---

## 路由策略

### single
```python
return ["CodeQualityAgent"]  # 快速审查
```

### static
```python
return ["CodeQualityAgent", "TestImpactAgent", "SecurityAgent", "ContextAgent"]  # 完整审查
```

### dynamic（推荐）
```python
selected = ["CodeQualityAgent"]  # 总是运行

if not docs_only:
    selected.append("TestImpactAgent")  # 非文档改动加测试检查

if risk in {HIGH, LARGE}:
    selected.extend(["SecurityAgent", "ContextAgent"])  # 高风险加完整检查

return selected
```

---

## 验证层（Verification Layers）

### Layer 1: 基本验证 (Deterministic)

```python
✓ JSON schema valid
✓ file_path in changed_files
✓ start_line in range
✓ evidence found in source
✓ no duplicates
```

**拒绝**：路径错误、证据不匹配、重复

### Layer 2: AST 验证 (Issue Type Specific)

```python
sql_injection:
    ✓ parameterized query → 拒绝
    ✗ string formatting → 确认

resource_leak:
    ✓ with statement or try/finally → 拒绝
    ✗ unguarded open() → 确认

null_dereference:
    ✓ has None check → 拒绝
    ✗ direct access → 确认
```

**拒绝**：条件不满足（被降级为 advisory）

### Layer 3: 人工评审 (Planned)

```
低置信度或冲突的发现 → 人工裁决
```

---

## 发现分类

| Type | When | Example |
|---|---|---|
| **defect** | 通过所有验证 + 有明确触发条件 | SQL injection with string formatting |
| **advisory** | 触发但证据不足 / 超出边界 | "缺少类型注解" |

**Defect 必须有**：
- `trigger_condition`: 输入或状态
- `expected_behavior`: 应该怎样
- `actual_behavior`: 实际怎样

---

## 关键设计决策

### 为什么 4 Agent？

❌ 1 Agent: 93% 假阳性  
✅ 4 Agent with boundaries: 0% 假阳性 (on measured set)

### 为什么动态路由？

- 文档改动：1 Agent（成本 1x）
- 普通代码：2 Agent（成本 2x）
- 高风险代码：4 Agent（成本 4x）
- **平均**：2.73 calls/PR vs 4 calls/PR (static)

### 为什么三层验证？

```
Layer 1 拒绝: 21 个发现（45%）
Layer 2 拒绝: 0 个发现（在这个数据集）
结果: 28 → 8 发现（71% 拒绝率）

Precision: 9% → 100%
```

### 为什么 AST 而不是 LLM for Layer 2？

```
成本: 不需要额外 API 调用
确定性: 可重复、可审计
安全: 人工编写的规则，不会有意外行为
```

### 为什么用 CrewAI Flow 而不是 ReAct？

```
✓ Flow: 显式任务，安全，可控
✗ ReAct: Agent 可能执行代码或修改 GitHub
```

---

## 性能指标

| Metric | Value |
|---|---|
| Precision (v1 → v3) | 9% → 100% |
| Recall (v1 → v3) | 70% → 80% |
| F1 (v1 → v3) | 16.5% → 88.9% |
| Avg calls/PR | 2.73 |
| Online time | 10-15s |
| False positives | 68 → 0 |

---

## 常见模式

### 这个发现应该是 defect 还是 advisory？

```
defect if:
  ✓ 有明确的触发条件
  ✓ 代码中真的有问题
  ✓ 不是风格偏好

advisory if:
  ✓ 证据不足
  ✓ 超出 Agent 职责范围
  ✓ 需要人类判断
```

### 什么时候 Agent 应该返回空列表？

```
✓ 没有找到问题
✓ 找到了问题但证据不足（改为 advisory）
✓ PR 不在 Agent 的专业领域
```

**好的答案**：有信心地返回空列表  
**坏的答案**：虚构问题来凑数

---

## 文件映射

```
src/prguard/
├── flow.py           # ReviewFlow: 整个编排逻辑
│   ├── classify_risk()    # 风险分类
│   └── route_agents()     # 路由决策
├── agents.py         # Agent 定义和 prompt 版本
├── verifier.py       # 三层验证逻辑
├── paths.py          # 路径恢复 (PathResolver)
└── models.py         # 数据类型定义

docs/
├── agent-design.md         # 完整的系统设计
├── prompt-evolution.md     # v1→v2→v3 的故事
├── interview-pitch.md      # 面试演讲稿
└── false-positive-analysis.md  # v1 的诊断报告
```

---

## 面试高频问题速答

**Q: 怎么从 93% 假阳性到 0%？**

A: 三层验证 + Agent 职责隔离 + 三个版本的 prompt 演进

**Q: 为什么不用一个更强的模型？**

A: 验证层的成本很低（确定性规则），而且对所有模型都有效

**Q: 动态路由会不会漏缺陷？**

A: CodeQualityAgent 总是运行，风险分类确定性，trade-off 是有意的

**Q: 候选测试集为什么还没标注？**

A: 离线 fixture 已经足够验证系统，完整标注需要多人评审

**Q: 何时发布/开源？**

A: 候选集标注完成后

---

## 下一步

- [ ] 标注候选测试集（36 用例）
- [ ] 实现 Layer 3（Critic Agent）
- [ ] 多模型对比评测
- [ ] 开源发布

