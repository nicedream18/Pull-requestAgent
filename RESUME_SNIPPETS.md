# 简历表述版本

> 针对 Agent Developer Engineer 岗位的简历描述，提供多个版本供选择

---

## 版本 1：简洁版（1-2 行）

**适用于**：空间受限（如 LinkedIn）或竞争激烈的简历

```
PRGuard: 多 Agent 代码审查系统
• 设计了四个专家 Agent 的协调架构，通过三层验证将精度从 9% 提升到 100%；
  优化了 Agent 路由策略，平均成本降低到 2.73x 调用/PR
```

---

## 版本 2：标准版（3-4 行）

**适用于**：标准的简历项目描述

```
PRGuard - 多 Agent 代码审查系统 (Python, CrewAI)
• 架构设计：设计了四个职责隔离的 Agent（代码质量、安全、测试影响、上下文分析），
  实现动态路由和并行执行，平均每个 PR 的 Agent 调用从 4 次优化到 2.73 次
  
• 质量优化：建立了三层验证体系（schema 检查 → AST 级别验证 → 人工评审），
  通过系统分析第一次运行的 68 个假阳性，精度从 9% 改进到 100%（F1: 16.5% → 88.9%）
  
• 技术实现：使用 CrewAI Flows 进行 Agent 编排，实现了确定性的路径恢复和去重算法，
  所有验证规则可审计，无代码执行或权限修改的安全隐患
```

---

## 版本 3：详细版（5-6 行）

**适用于**：有更多空间的简历，或重点突出 Agent 系统设计

```
PRGuard - 多 Agent 智能代码审查系统 (Python, CrewAI, FastAPI, Vue)

• 系统设计：设计并实现了四个专家 Agent（CodeQualityAgent, SecurityAgent, 
  TestImpactAgent, ContextAgent）的协调系统。每个 Agent 有明确的职责边界和闭合词表的问题类型，
  通过动态路由算法根据 PR 风险等级智能选择需要的 Agent，实现了并行执行和错误隔离

• 问题诊断与优化：对第一次在线运行的 68 个假阳性进行了系统的手工分类，
  发现根本原因包括：45% 的测量问题（缺少关键字段）、42% 的 Agent 越界、13% 的真实缺陷。
  针对每个根本原因设计了对应的解决方案，通过三层验证体系（Schema 验证 → AST 级别缺陷验证 → 人工评审）
  将精度从 9% 改进到 100%

• 质量保证：建立了完整的 Ablation 测试框架，验证了每一层改进的具体贡献；
  实现了 PathResolver 智能路径恢复和基于语义信号的去重，而非文本相似度；
  所有验证规则基于语法树和确定性规则，可完全审计和重现

• 性能与成本：将 Agent 调用成本从 static 策略的 4x 优化到 dynamic 的 2.73x，
  同时保持了 80% 的回召率；完整的离线测试集（15 个 fixture）和候选集（36 个 use case）
```

---

## 版本 4：面试官友好版

**适用于**：想让面试官快速看出亮点的简历

```
PRGuard - 多 Agent 代码审查系统 | 精度优化: 9% → 100% | Agent 设计、Prompt 工程、质量验证

• 多 Agent 系统设计：实现了四个职责隔离的 Agent，通过确定性风险分类和动态路由算法，
  在保证覆盖率的前提下将成本优化 27%（从 4.0x 到 2.73x Agent 调用/PR）

• 从 68 个假阳性到 0：对第一次运行的失败进行根本原因分析，
  通过改进 Prompt（添加 trigger_condition/expected_behavior 要求）、
  定义职责边界（闭合词表）、建立三层验证体系（Schema → AST → 人工），
  将精度从 9% 改进到 100%，F1 从 16.5% 到 88.9%

• 工程实践：使用 CrewAI Flows 而非 ReAct 确保安全性和可控性；
  实现了 PathResolver 和基于语义信号的去重；
  验证每个改进的具体贡献（ablation test on frozen dataset）
```

---

## 版本 5：强调技术深度的版本

**适用于**：想展示技术细节和系统思考的简历

```
PRGuard - 多 Agent 智能代码审查系统 | CrewAI, Python, FastAPI, Vue

系统设计与 Agent 协调：
  • 设计了四个专家 Agent 的架构，每个 Agent 有明确的 issue_type 闭合词表和职责规则
  • 实现了两阶段的确定性路由：(1) PR 风险分类（代码量/关键词/diff 模式）→ (2) 动态 Agent 选择
  • 实现了并行执行（asyncio + semaphore），支持超时和重试隔离

质量保证与验证系统：
  • 三层验证架构：Layer 1 (Schema/Path/Evidence) → Layer 2 (AST 级别的缺陷特定验证) → Layer 3 (人工评审)
  • Layer 2 使用 AST 解析而非文本匹配验证：SQL 注入 (参数化查询检查)、资源泄漏 (with/finally 检查) 等
  • 实现了 PathResolver，从改动文件列表和证据唯一性反推正确的文件路径

Prompt 工程与演进：
  • v1 → v2 → v3 的迭代，精度从 9% → 80% → 100%（在同一冻结数据集上）
  • 分析了 68 个假阳性的根本原因（45% 测量问题 + 42% 越界 + 13% 真实缺陷）
  • 改进：添加 trigger_condition/expected_behavior 要求、职责边界的闭合词表、路径合同

实验与验证：
  • 使用冻结数据集进行 ablation 测试，定量证明每层验证的贡献
  • 建立了 15 个离线 fixture 的确定性测试集和 36 个候选用例的标注流程
```

---

## 按岗位定制的版本

### 如果岗位重视「Agent 系统设计」

```
PRGuard - 多 Agent 代码审查系统

• 实现了四个专家 Agent（CodeQuality, Security, TestImpact, Context）的协调系统，
  通过确定性风险分类和动态路由，实现智能的 Agent 选择和成本优化（2.73x 平均调用）

• 建立了完整的 Agent 生命周期：职责定义 → prompt 版本管理 → 三层验证 → 结果追踪，
  所有决策基于量化指标（从 9% 精度改进到 100%）
```

### 如果岗位重视「Prompt 工程」

```
PRGuard - 多 Agent 代码审查系统

• 通过三个版本的 Prompt 迭代（v1 → v2 → v3），将精度从 9% 改进到 100%；
  通过系统分析第一次运行的 68 个假阳性，确定根本原因并设计针对性的 Prompt 改进

• 核心改进：(1) 明确要求 trigger_condition + expected_behavior，
  (2) 为每个 Agent 定义闭合词表和职责边界，
  (3) 添加了路径合同确保模型选择正确的文件
```

### 如果岗位重视「质量保证」

```
PRGuard - 多 Agent 代码审查系统

• 建立了三层验证体系：Schema 检查 → AST 级别的缺陷特定验证 → 人工评审，
  通过这个体系将假阳性从 68 个（93% 假阳性率）降到 0 个（100% 精度）

• 实现了确定性的验证规则（不依赖 LLM），每个规则都有明确的数学定义和代码实现
```

---

## 使用技巧

### 选择哪个版本？

| 简历长度 | 推荐版本 | 理由 |
|---|---|---|
| 1 页（紧凑） | 版本 1 或 2 | 快速突出亮点 |
| 1-2 页（标准） | 版本 2 或 3 | 平衡深度和简洁性 |
| 2+ 页（详细） | 版本 3 或 5 | 有空间讲故事和技术细节 |
| 特定岗位 | 按岗位定制版 | 针对性最强 |

### 优化建议

✅ **保留具体数字**
- ❌ 「提升了精度」
- ✅ 「精度从 9% 改进到 100%」

✅ **用动词开头**
- ❌ 「系统支持多 Agent 并行执行」
- ✅ 「实现了多 Agent 的并行执行和错误隔离」

✅ **突出系统性思维**
- ❌ 「改进了 prompt」
- ✅ 「通过根本原因分析（68 个假阳性中 45% 是测量问题），设计了针对性的三层验证体系」

✅ **展示 Agent 岗位的关键技能**
- 多 Agent 系统设计 ✓
- Agent 职责隔离和协调 ✓
- Prompt 工程和版本管理 ✓
- 量化验证和 ablation 测试 ✓

---

## 配套的「项目链接」说明

如果简历中有项目链接，可以这样写：

```
GitHub: github.com/[your-name]/prguard
📍 重点阅读：
  • docs/agent-design.md - 系统架构设计
  • docs/prompt-evolution.md - v1→v2→v3 的演进过程
  • docs/interview-pitch.md - 3 分钟技术演讲
```

---

## 搭配 LinkedIn/GitHub 的描述

### LinkedIn 项目描述

```
PRGuard: Multi-Agent Code Review System

A reproducible GitHub PR review system using CrewAI with 4 specialist agents.
• Improved precision from 9% to 100% through three-layer verification
• Optimized agent routing: 4x → 2.73x calls per PR
• Architecture: dynamic risk classification, concurrent execution, AST-level defect verification

Key innovation: Transformed 68 false positives into 0 through root cause analysis 
and systematic prompt evolution (v1 → v2 → v3).
```

### GitHub README 的项目亮点

已经在 README 中添加的部分保持不变。简历和 GitHub 的一致性很重要。

---

## 最终建议

### 标准推荐方案

在你的简历中使用**版本 2（标准版）**，因为它：
- ✅ 长度适中（3-4 行，不会显得冗长）
- ✅ 信息完整（架构、优化、技术实现三个维度）
- ✅ 数字有力（9% → 100%, 2.73x 优化）
- ✅ 突出 Agent 岗位的关键技能

### 面试前的调整

如果确认了面试的具体岗位和侧重，可以在简历基础上做 1-2 个关键词的调整（参考「按岗位定制的版本」）。

### 与作品集的配合

简历上的简洁描述 + GitHub 上的详细文档（agent-design.md, prompt-evolution.md 等）形成完整的组合。
面试官会从简历上看到数字 → 被吸引查看 GitHub → 找到详细的技术文档。

