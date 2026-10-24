from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prguard.i18n import localize_findings
from prguard.models import FlowState, PRSnapshot


def _markdown(state: FlowState, snapshot: PRSnapshot) -> str:
    findings = localize_findings(state.verified_findings)
    lines = [
        f"# PRGuard 代码审查报告：{snapshot.repo}#{snapshot.pr_number}",
        "",
        f"**{snapshot.title}**",
        "",
        f"- 状态：`{state.review_status}`",
        f"- 运行 ID：`{state.run_id}`",
        f"- Head SHA：`{snapshot.head_sha}`",
        f"- 风险等级：`{state.risk_level.value}`",
        f"- Agent：{', '.join(state.selected_agents) or '无'}",
        f"- 修改文件：{len(snapshot.files)}",
        f"- 执行耗时：{state.execution_time:.2f}s",
        "",
    ]
    if snapshot.diff_omitted_files:
        lines.extend(["> Diff 不完整。GitHub 未返回以下文件的补丁数据：" + ", ".join(snapshot.diff_omitted_files), ""])
    lines.append("## 审查问题")
    lines.append("")
    if not findings:
        lines.append("没有经过核验的问题。")
    for index, finding in enumerate(findings, 1):
        location = finding.file_path
        if finding.start_line:
            location += f":{finding.start_line}"
        url = f"https://github.com/{snapshot.repo}/blob/{snapshot.head_sha}/{finding.file_path}"
        if finding.start_line:
            url += f"#L{finding.start_line}"
        lines.extend([
            f"### {index}. [{finding.severity.value.upper()}] {finding.title}",
            "",
            f"`{location}` | `{finding.category}` | 置信度 `{finding.confidence:.2f}` | Agent `{finding.source_agent}`",
            "",
            finding.description,
            "",
            f"代码证据：`{finding.evidence}`",
            "",
            f"修复建议：{finding.suggestion}",
        ])
        if finding.needs_human_review:
            lines.extend(["", "由于置信度较低，需要人工复核。"])
        lines.extend(["", f"[Open code]({url})", ""])
    if state.errors:
        lines.extend(["## 部分失败", ""])
        for error in state.errors:
            lines.append(f"- `{error.get('type', 'error')}`：{error.get('message', '审查阶段执行失败')}")
        lines.append("")
    lines.extend([
        "## 执行指标",
        "",
        f"- 调用次数：{state.token_usage['calls']}",
        f"- 输入 Token：{state.token_usage['input']}",
        f"- 输出 Token：{state.token_usage['output']}",
        f"- 总 Token：{state.token_usage['total']}",
    ])
    return "\n".join(lines).rstrip() + "\n"


def write_reports(
    state: FlowState,
    snapshot: PRSnapshot,
    output_dir: str,
    source_map: dict[str, str],
    prompt_version: str,
    *,
    model: str | None = None,
    execution_mode: str = "online",
) -> dict[str, str]:
    del source_map  # Repository contents belong in the review report only through verified evidence.
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    findings = localize_findings(state.verified_findings)
    common = {
        "schema_version": "1.0",
        "project": "PRGuard",
        "run_id": state.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": execution_mode,
        "model": model,
        "prompt_version": prompt_version,
        "repo": snapshot.repo,
        "pr_number": snapshot.pr_number,
        "title": snapshot.title,
        "base_sha": snapshot.base_sha,
        "head_sha": snapshot.head_sha,
        "changed_files": [file.model_dump() for file in snapshot.files],
        "risk_level": state.risk_level.value,
        "selected_agents": state.selected_agents,
        "historical_prs": state.historical_prs,
        "review_status": state.review_status,
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "errors": state.errors,
        "token_usage": state.token_usage,
        "metrics": state.metrics,
    }
    (output / "review.json").write_text(json.dumps(common, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "review.md").write_text(_markdown(state, snapshot), encoding="utf-8")
    trace = {
        "run_id": state.run_id,
        "repo": snapshot.repo,
        "pr_number": snapshot.pr_number,
        "head_sha": snapshot.head_sha,
        "prompt_version": prompt_version,
        "model": model,
        "events": state.trace_events,
        "agents": [
            {
                "agent": run.agent,
                "status": run.status,
                "started_at": run.started_at,
                "ended_at": run.ended_at,
                "duration_seconds": run.duration_seconds,
                "calls": run.calls,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "total_tokens": run.total_tokens,
                "error_type": run.error_type,
                "finding_count": len(run.findings),
            }
            for run in state.agent_runs
        ],
        "errors": state.errors,
    }
    (output / "trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {name: str(output / name) for name in ("review.json", "review.md", "trace.json")}
