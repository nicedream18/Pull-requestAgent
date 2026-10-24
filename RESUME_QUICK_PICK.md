# 快速选择：直接用这个版本

> 根据不同场景，选择最适合你的简历表述

---

## 🎯 最推荐：标准版本（复制即用）

这个版本在**大多数情况下效果最好**，因为它信息完整、数字有力、长度合适。

```
项目名称：PRGuard - 多 Agent 智能代码审查系统

描述（复制粘贴）：
设计并实现了多 Agent 代码审查系统，包含四个专家 Agent（代码质量、安全、测试影响、上下文）。
通过确定性风险分类和动态路由算法优化了 Agent 执行成本（4x → 2.73x），精度从 9% 改进到 100%（F1: 88.9%）。
建立了三层验证体系（Schema → AST 级别缺陷验证 → 人工评审），系统分析了 68 个假阳性的根本原因并设计了针对性解决方案。

技术栈：CrewAI, Python, FastAPI, Vue, AST parsing

GitHub：[你的仓库链接]
关键文档：docs/agent-design.md | docs/prompt-evolution.md | docs/interview-pitch.md
```

**为什么推荐这个：**
- ✅ 长度 3-4 行（简历标准）
- ✅ 包含三个维度：架构设计、性能优化、质量保证
- ✅ 数字有力（9% → 100%, 2.73x 优化）
- ✅ 突出 Agent 岗位的关键技能
- ✅ 技术栈清晰

---

## 其他场景的快速选择

### 如果简历很紧凑（1 页以内）

```
PRGuard - 多 Agent 代码审查系统

设计四个专家 Agent 的协调系统，通过三层验证体系和动态路由，
将精度从 9% 改进到 100%，成本优化到 2.73x 平均调用/PR。
```

---

### 如果想突出 Agent 系统设计

```
PRGuard - 多 Agent 智能代码审查系统

• 架构设计：实现四个职责隔离的 Agent 的协调系统，
  通过确定性风险分类和动态路由算法实现智能的 Agent 选择和成本优化

• 性能优化：Agent 调用成本从 static 策略的 4x 优化到 dynamic 的 2.73x

• 质量改进：通过系统分析 68 个假阳性、建立三层验证体系，
  精度从 9% 改进到 100%（F1: 16.5% → 88.9%）
```

---

### 如果想突出 Prompt 工程

```
PRGuard - 多 Agent 代码审查系统

通过三个版本的 Prompt 迭代，精度从 9% 改进到 100%。
对第一次运行的 68 个假阳性进行系统分析，确定根本原因（45% 测量问题 + 42% 越界 + 13% 真实缺陷），
并针对每个原因设计了对应的 Prompt 改进和验证规则。
```

---

### 如果是英文简历

```
PRGuard - Multi-Agent Code Review System

Designed and implemented a multi-agent code review system with 4 specialist agents 
(CodeQuality, Security, TestImpact, Context). Improved precision from 9% to 100% 
through three-layer verification architecture and systematic Prompt evolution (v1 → v2 → v3). 
Optimized agent routing cost from 4x to 2.73x average calls per PR.

Tech Stack: CrewAI, Python, FastAPI, Vue, AST Parsing
```

---

## ☑️ 使用前检查单

在把文字放进简历前：

- [ ] 把版本号改成你项目的实际版本（v1 → v2 → v3 是真的吗？）
- [ ] 如果你用的不是 DeepSeek 模型，改一下模型名字
- [ ] 如果候选测试集的用例数不是 36，改成实际的数字
- [ ] GitHub 链接改成你自己的
- [ ] 技术栈删除你没用的（比如 Vue 如果没有前端）

---

## 🎬 配套的面试演讲稿

如果面试官问「介绍一下这个项目」，用这个 3 分钟版本：

```
这个项目叫 PRGuard，是一个多 Agent 的代码审查系统。

核心问题是：代码审查需要多种专业知识（代码质量、安全、测试、跨模块影响）。
我的解决方案是用四个专家 Agent，每个只负责一个领域。

第一次运行的结果很差，有 68 个假阳性（精度只有 9%）。
我对这些假发现做了分析，发现问题来自三个方面：
  - 45% 是测量问题（模型没有提供关键字段）
  - 42% 是 Agent 越界（比如报告风格偏好作为缺陷）
  - 13% 是真实缺陷

针对每个问题，我设计了对应的解决方案：
  - 改进 Prompt 要求、定义职责边界、建立三层验证
  
最后的结果是精度达到 100%，假阳性从 68 个降到 0 个。

同时，我用动态路由算法优化了成本，平均每个 PR 的 Agent 调用从 4 次降到 2.73 次。

这个过程对我最大的收获是：
好的 Agent 系统不是靠一个更强大的模型，而是靠设计和验证。
```

---

## 🔗 简历中的项目链接

在简历中添加项目链接时，建议这样写：

```
GitHub: github.com/[username]/prguard

📍 For Agent Engineering roles, start here:
  • docs/agent-design.md — System architecture & multi-agent coordination
  • docs/prompt-evolution.md — Root cause analysis & v1→v2→v3 evolution
  • docs/interview-pitch.md — 3-min technical pitch
```

这样面试官知道从哪里找到最相关的内容。

---

## 最后提示

### ✅ 做这些事

- 在简历中加上**具体数字**（9% → 100%, 2.73x）
- 用**动词开头**（设计、实现、优化、改进）
- 强调**系统化思维**（分析 → 诊断 → 设计 → 验证）

### ❌ 避免这些错误

- 不要说「我用了多个 Agent」而不解释为什么
- 不要只说「改进了精度」，要说「从 X% 到 Y%」
- 不要夸大（「最先进的」「最优的」），用数字说话

---

## 确认无误后就可以用了 ✓

选好版本后，复制到你的简历中。如果遇到不确定的地方，参考这些文件：

- 项目的实际数字：README.md 和 docs/benchmark.md
- 面试时怎么讲：docs/interview-pitch.md
- 技术细节：docs/agent-design.md

**祝简历顺利！** 📋

