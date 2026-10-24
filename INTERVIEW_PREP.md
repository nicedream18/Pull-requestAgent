# Interview Preparation Checklist

> 你的 PRGuard 项目已经准备好作为 Agent Developer Engineer 岗位的作品集。

## 📚 新增文档

已为你创建了四份关键文档，适合面试准备和展示：

### 1. **[Agent Design (docs/agent-design.md)](docs/agent-design.md)** - 核心设计文档
   - 系统架构和设计原则
   - 四个 Agent 的详细职责和规则
   - 动态路由算法和并行协调
   - 三层验证体系的完整说明
   - 为什么这个设计有效的案例分析
   
   **面试用途**：当被问到「系统怎么设计的？」这是最完整的回答

### 2. **[Prompt Evolution (docs/prompt-evolution.md)](docs/prompt-evolution.md)** - 版本演进的故事
   - v1 的问题诊断：68 个假阳性的根本原因
   - v2 的解决方案：如何一步步改进
   - v3 的优化：路径恢复和去重
   - 量化结果和 ablation 分析
   
   **面试用途**：展示问题诊断、系统化解决问题的能力

### 3. **[Interview Pitch (docs/interview-pitch.md)](docs/interview-pitch.md)** - 3-5 分钟演讲稿
   - 核心问题和解决方案的简洁表述
   - 关键创新点的解释
   - 常见问题的预准备答案
   - Demo 演示的步骤
   - 最后的总结陈述
   
   **面试用途**：在技术讨论或 Q&A 环节使用

### 4. **[Agent Cheatsheet (docs/agent-cheatsheet.md)](docs/agent-cheatsheet.md)** - 快速参考卡
   - 系统架构一览
   - 四个 Agent 对比表
   - 关键算法和规则
   - 常见问题速答
   
   **面试用途**：自己复习或给面试官快速查阅

---

## 🎯 面试核心故事

你的项目展现了以下技能：

### 1. **多 Agent 系统设计**
- 职责隔离（4 个专家 Agent 而不是 1 个大 Agent）
- 动态路由（根据 PR 特征智能选择 Agent）
- 并行协调（concurrent execution with error isolation）

### 2. **问题诊断和解决**
- 从 68 个假阳性中系统地分类根本原因
- 针对每个根本原因设计独立的解决方案
- 用量化指标验证每个改进

### 3. **Prompt 工程**
- 理解为什么 v1 失败（45% 测量问题 + 42% 越界 + 13% 真实缺陷）
- v2 通过明确的分类要求、职责边界和路径合同来改进
- 从 9% 精度到 100% 精度（在相同的数据集上）

### 4. **质量保证**
- 三层验证体系（schema → AST → 人工）
- 从文本匹配到语义验证
- Ablation 测试证明每一层的价值

---

## 🚀 面试前准备清单

### 必做项

- [ ] 阅读 `docs/agent-design.md` 全文（理解系统）
- [ ] 阅读 `docs/prompt-evolution.md` 全文（理解演进）
- [ ] 背诵 `docs/agent-cheatsheet.md` 中的「高频问题速答」
- [ ] 在本地运行离线 demo（能展示）
  ```bash
  uv run python -m evals.run --mode offline --output /tmp/demo
  ```
- [ ] 准备 2-3 个项目细节的具体例子（某个 Agent 的规则、某个验证层的算法等）

### 可选加强

- [ ] 在本地启动前后端，试试完整的 UI（需要 .env 配置）
- [ ] 阅读 `docs/false-positive-analysis.md` 的前 2/3（深入理解 v1 的问题）
- [ ] 跑一遍 `uv run pytest -q` 看测试覆盖

### 演讲稿准备

- [ ] 对 `docs/interview-pitch.md` 中的「核心故事（5 分钟版）」做自己的总结
- [ ] 练习用 3 分钟说清楚：问题 → 解决方案 → 结果
- [ ] 准备 2-3 个有意思的细节问题的答案（从 `interview-pitch.md` 的 Q&A 部分选）

---

## 💡 面试时的表现要点

### 强调的方向

✅ **系统化思维**：不是随意改 prompt，而是诊断 → 设计 → 验证  
✅ **权衡意识**：理解 precision vs recall，成本 vs 覆盖率  
✅ **量化验证**：所有改进都有数字支持（从 9% 到 100%）  
✅ **可靠性思维**：验证层是设计的核心，不是事后修补  

### 避免的问题

❌ 「我试了这个 prompt 效果更好」（没有证据）  
❌ 「我用了 4 个 Agent 因为这样更好」（应该解释为什么 1 个不行）  
❌ 「系统很复杂」（应该强调设计很清晰）  

---

## 📋 README 更新

已经更新了项目 README，在顶部添加了「🎯 Quick Navigation for Agent Engineering」部分，直接指向三份关键文档。这样审视你项目的人可以快速找到 Agent 相关的内容。

---

## 🎬 Demo 展示顺序

如果有机会演示项目，建议这个顺序：

1. **5 分钟**：用 interview-pitch.md 讲系统故事
2. **3 分钟**：本地跑离线 fixture
   ```bash
   uv run python -m evals.run --mode offline --output /tmp/demo
   ```
3. **5 分钟**：展示前后端（如果面试官感兴趣）
   ```bash
   OTEL_SDK_DISABLED=true CREWAI_STORAGE_DIR=prguard \
   uv run uvicorn prguard.api:app --reload
   ```
4. **剩余时间**：讨论设计细节或答疑

---

## 🔑 最容易被问到的问题

根据经验，这些是 Agent Developer 岗位面试官最爱问的：

1. **「为什么选择这个架构？」**
   → 回答：参考 `agent-design.md` 的「核心设计原则」

2. **「假阳性这么多怎么办？」**
   → 回答：讲 v1→v2→v3 的故事，参考 `prompt-evolution.md`

3. **「怎么确保系统的可靠性？」**
   → 回答：三层验证体系，参考 `agent-design.md` 的第 5 章

4. **「多 Agent 系统的并发问题怎么处理？」**
   → 回答：semaphore + timeout + error isolation，参考 `agent-design.md` 的第 4 章

5. **「这个系统能扩展到其他用途吗？」**
   → 回答：Agent 职责隔离的设计使得很容易添加新 Agent，举例说明

---

## 📊 关键指标速记

面试时可能会被问到这些数字，记住它们：

- **精度改进**：9% → 100%
- **假阳性**：68 → 0
- **F1 改进**：16.5% → 88.9%
- **平均 Agent 调用**：2.73 per PR
- **文档/验证层**：3 layers
- **Agents**：4（CodeQuality, Security, TestImpact, Context）
- **Prompt 版本**：v1, v2, v3

---

## ✅ 最后的检查表

出门面试前：

- [ ] 对「我的项目做了什么」能用 3 分钟说清楚
- [ ] 对「为什么这样设计」能举 2-3 个具体例子
- [ ] 对「系统的局限」有诚实的理解（比如候选集还没标注）
- [ ] 准备好说「我还没做到的下一步是什么」
- [ ] 对 Agent 开发的观点有自己的看法（不是只重复文档）

---

## 💬 一句话总结给朋友

「我做了一个多 Agent 代码审查系统。核心是四个专家 Agent，每个只负责一个领域。通过三层验证和动态路由，我把精度从 9% 提升到 100%。」

---

**祝面试顺利！** 🎯

有任何问题或需要调整这些文档，欢迎告诉我。

