# PRGuard 面试演示脚本（约 4 分钟）

演示使用离线 Demo Fixture，不会调用 LLM 或 GitHub，也不代表独立 Benchmark 的模型效果。

## 开始前

在两个终端启动服务：

```bash
OTEL_SDK_DISABLED=true CREWAI_STORAGE_DIR=prguard uv run uvicorn prguard.api:app --reload
```

```bash
cd frontend && npm run dev
```

浏览器打开 `http://127.0.0.1:5173/`，选择“离线 Demo”。

## 演示流程

| 时间 | 操作与讲解 |
| --- | --- |
| 0:00-0:35 | 介绍输入模式：真实 PR 只读获取；本次选离线 Demo，固定 Fixture 由确定性 Agent Runner 回放，不产生模型费用。 |
| 0:35-1:25 | 选择 `sql-injection-05` 并启动。说明 Flow 先取 PR 快照和 Diff，再按文件/差异特征确定风险，路由由规则决定。 |
| 1:25-2:15 | 打开 Agent Workflow，观察实际执行状态。讲解高风险代码会选择安全、测试影响和上下文 Agent；显示为 skipped 的 Agent 就是真正未运行。 |
| 2:15-3:05 | 打开 Finding，点击问题定位到文件与行号。展示原始证据、影响、修复意见和 Verifier 的路径/行号/证据核验。 |
| 3:05-3:40 | 切换到 `docs-only-09`，指出纯文档变更会跳过不适用的专业 Agent，展示路由不是固定全员运行。 |
| 3:40-4:20 | 打开评测对比，解释当前图表来自 15 个开发回归 Fixture，只验证流程和策略实现，不是独立测试集上的真实 LLM 质量结论。 |
| 4:20-4:40 | 收尾：36 个候选案例正在等待人工审核；只有审核、修订并冻结后才会运行 v2/v3 正式对照，holdout 只做最终一次测量。 |

Dashboard 截图目标位置是 `docs/images/prguard-review.png`。当前仓库没有真实运行截图；补图时标明“离线 Demo Fixture”或“真实 GitHub PR”，不要把 Mock 数据标成模型实验结果。
