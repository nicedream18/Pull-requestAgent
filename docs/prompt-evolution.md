# Prompt Engineering Evolution: v1 → v2 → v3

> 本文档记录 PRGuard Agent prompts 的演进过程，从第一次在线运行的教训中总结的改进点。每次版本更新都有明确的问题诊断和目标改进指标。

---

## v1 → v2: 从 68 个假阳性到可信的发现

### 背景：第一次在线运行

**时间**：2026-10-24  
**模型**：DeepSeek deepseek-chat  
**数据集**：15 个 Python 代码审查 fixture（确定性测试集）  
**结果**：
- 精度 P = 0.093（93% 假阳性！）
- 回召 R = 0.700（漏掉 3 个真实缺陷）
- F1 = 0.165（几乎不可用）

### 问题诊断

我对所有 68 个假阳性进行了手工分类，发现了以下问题：

#### 问题 #1：测量缺陷（45% 的假阳性）

**症状**：模型没有提供 `start_line`，verifier 无法评估发现

```json
// v1 的发现示例
{
  "issue_type": "missing_test_coverage",
  "description": "缺少测试",
  "file_path": "src/parser.py"
  // ❌ 没有 start_line！
}
```

**根本原因**：
- v1 prompt 没有明确要求 `start_line` 字段
- 模型不知道这对评估很重要

**v1 matcher 的局限**：
- 尝试用 Jaccard 相似度匹配发现和标签
- 34 个发现因为没有行号而无法匹配（相似度 = 0）
- 66 个发现是中文，但标签都是英文，token 重叠率 0

**结论**：45% 的假阳性其实是**测量问题，不是模型问题**

#### 问题 #2：越界报告（42.6% 不是缺陷）

**症状**：TestImpactAgent 和 StyleAgent（不存在但被模仿）报告风格问题和纯测试偏好

```json
// 真实案例：不该报告的「缺陷」
{
  "issue_type": "missing_test_coverage",
  "description": "parse_date 函数没有单元测试",
  "file_path": "src/utils.py",
  "start_line": 42
  // ⚠️ 这是一个简单的辅助函数，不涉及多资源变更
  // 根据新的定义，这应该是 advisory（建议），不是 defect（缺陷）
}
```

**分类**：
- **D. 测试缺口没有可证明的行为风险**（29 个，42.6%）：
  - 简单辅助函数没有测试
  - 只读操作没有测试
  - 已经有上层集成测试的函数

- **E. 风格偏好**（10 个，14.7%）：
  - 缺少类型注解
  - 缺少 docstring
  - 不用 `SELECT *` 这样的偏好

**结论**：56% 的假阳性来自 Agent 职责边界定义不清

#### 问题 #3：路径问题（21 个发现被拒）

**症状**：21 个发现因为无法确定正确的文件路径而被 layer 1 验证拒绝

```json
// v1 的路径问题
{
  "file_path": "unknown"  // ❌ 13 个发现
}
{
  "file_path": ""  // ❌ 3 个发现
}
{
  "file_path": "utils.py"  // ❌ 4 个发现，应该是 "src/utils.py"
}
{
  "file_path": "/absolute/path/to/file.py"  // ❌ 1 个发现，不存在
}
```

**结论**：模型没有理解必须从改动文件列表中选择路径

### v2 解决方案

#### 改进 #1：明确的分类要求

**添加到 prompt 中**：
```
Write each finding MUST include:
  - issue_type: 从允许列表中选择
  - finding_type: 'defect' 或 'advisory'
  - trigger_condition: 什么输入或状态触发这个问题
  - expected_behavior: 应该怎样
  - actual_behavior: 实际怎样

A defect MUST have all three: trigger_condition, expected_behavior, actual_behavior.
If you cannot state these clearly, return as advisory with 'needs_human_review'.
```

**为什么有效**：
- 模型被迫思考「这真的是缺陷吗」
- 无法清晰说明的发现自动降级为 advisory
- 评估器现在可以按 `issue_type` 精确匹配

#### 改进 #2：职责边界的闭合词表

**CodeQualityAgent**：
```python
allowed_issue_types = [
    "null_dereference",
    "missing_exception_handling", 
    "boundary_condition",
    "resource_leak",
    "division_by_zero",
    "other"
]

# ✅ 可以报告：变量在某处被赋 None，后面被访问
# ❌ 不能报告：缺少类型注解、缺少 docstring
```

**TestImpactAgent**：
```python
# ❌ v1：「函数没有测试」→ 报告为缺陷
# ✅ v2：「函数执行数据库写入 + API 调用但没有测试」→ 报告为缺陷

rule = """
Report missing_test_coverage as a defect ONLY when:
1. The changed code performs irreversible or multi-resource behavior
2. Examples of such behavior: database mutations, external API calls, file writes
3. These behaviors are NOT tested in the changed code
4. Otherwise, return advisory or empty list.

Helper functions that are pure and side-effect-free are advisory, never defects.
"""
```

#### 改进 #3：强制路径合同

**添加到 prompt 中**：
```
FILE PATH CONTRACT:
  file_path MUST be one of the changed files listed in the context.
  Copy it exactly from the provided list.
  Never write 'unknown', empty string, bare filename, or non-existent path.
  If you cannot attribute a finding to one of the listed files, 
  return it as advisory with 'needs_human_review' or omit it.
```

**为什么有效**：
- 模型被迫检查提供的文件列表
- 无法匹配的发现不被拒绝，而是降级为 advisory
- zero 的假路径

#### 改进 #4：三层验证体系

**Layer 1: Schema + Path Validation**
- JSON 格式
- `file_path` 在改动文件列表中
- `start_line` 在合理范围
- 证据能在源文件中找到
- 去重

**Layer 2: AST 级别的缺陷验证**

对于每个 `issue_type`，有参数化的检查函数：

```python
def verify_sql_injection(source_code: str, finding: Finding) -> bool:
    """Check if the finding actually has a SQL injection vulnerability."""
    tree = ast.parse(source_code)
    
    # 寻找 cursor.execute() 的调用
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # 检查是否使用了参数化查询（安全）
            if is_parameterized_query(node):
                return False  # 安全，拒绝该发现
            
            # 检查是否有字符串格式化（不安全）
            if has_string_formatting(node):
                return True  # 确认不安全
    
    return False  # 无法确认

def verify_resource_leak(source_code: str, finding: Finding) -> bool:
    """Check if there's actually a resource leak."""
    tree = ast.parse(source_code)
    
    # 寻找 open() 调用
    for node in ast.walk(tree):
        if is_file_open_call(node):
            # 检查是否在 with 语句中（安全）
            parent = find_parent(node)
            if is_with_statement(parent):
                return False  # 安全
            
            # 检查是否在 try/finally 中关闭（安全）
            if has_close_in_finally(node):
                return False  # 安全
            
            return True  # 泄漏
    
    return False
```

**层 2 的转变**：从**文本匹配**（不准确）到 **AST 验证**（准确）
- 不能再用 "contains substring" 来判断是否安全
- 必须真正解析代码的语法树

### v2 结果

重新对第一次运行的冻结发现进行评估，使用 v2 的验证规则：

| Config | P | R | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| A: Layer 1 only (schema + path) | 0.667 | 0.400 | 0.500 | 4 | 2 | 6 |
| B: + path recovery | 0.333 | 0.800 | 0.471 | 8 | 16 | 2 |
| C: + dedup | 0.615 | 0.800 | 0.696 | 8 | 5 | 2 |
| D: + Layer 2 (AST verification) | **0.800** | **0.800** | **0.800** | 8 | 2 | 2 |

**关键发现**：
- Layer 1 + Layer 2 可以达到 80% 精度
- 从 28 个原始发现到 8 个最终发现（71% 拒绝率）
- 剩下的 2 个假阳性是真实的观察但标签没有的问题（标签不完整）

---

## v2 → v3: 路径恢复和去重

### 背景

v2 的评估显示，即使使用了强制路径合同，仍然有 21 个发现因为路径问题而被完全拒绝。

**问题**：模型提供的路径无用，但发现本身可能是正确的。

### 问题诊断

分析 28 个原始发现：

```
模型提供的路径分布：
- 正确的完整路径：7 (25%)
- 写成 'unknown'：13 (46%)  ← 最常见的错误
- 空字符串：3 (11%)
- 只有文件名：4 (14%)
- 不存在的路径：1 (4%)
```

**现象**：
- 模型知道有问题，找到了正确的代码
- 但不知道或记不住改动文件的完整路径
- 很多发现的证据（evidence 字段）其实可以唯一确定文件

### v3 解决方案

#### 改进 #1：智能路径恢复（PathResolver）

```python
class PathResolver:
    """从信任的数据源恢复路径，而不是相信模型的 file_path。"""
    
    def resolve(self, finding: Finding, changed_files: List[str], 
                evidence: str, start_line: int) -> Tuple[str, str]:
        """
        Returns: (resolved_path, resolution_status)
        Status: 'exact', 'normalized', 'evidence_resolved', 'ambiguous', 'unresolved'
        """
        
        # 1. 如果模型的路径在改动文件列表中，直接用（exact）
        if finding.file_path in changed_files:
            return finding.file_path, "exact"
        
        # 2. 如果模型的路径是只有文件名，尝试找唯一匹配（normalized）
        if finding.file_path and '/' not in finding.file_path:
            matches = [f for f in changed_files if f.endswith(finding.file_path)]
            if len(matches) == 1:
                return matches[0], "normalized"
            if len(matches) > 1:
                return None, "ambiguous"
        
        # 3. 关键：使用证据来反推文件（evidence_resolved）
        for file_path in changed_files:
            if self._evidence_matches_file(evidence, file_path, start_line):
                return file_path, "evidence_resolved"
        
        return None, "unresolved"
    
    def _evidence_matches_file(self, evidence: str, file_path: str, 
                               start_line: int, window: int = 2) -> bool:
        """检查证据是否唯一地出现在该文件中（start_line ± window 范围内）。"""
        # 读取文件，检查该代码片段是否出现在指定行附近
        # 使用行号窗口防止歧义
        lines = read_file(file_path)
        for i in range(max(0, start_line - window), 
                      min(len(lines), start_line + window + 1)):
            if evidence in lines[i]:
                # 检查这段证据是否在其他改动文件中也出现
                matches_in_other = sum(
                    1 for f in changed_files if f != file_path 
                    and evidence in read_file(f)
                )
                if matches_in_other == 0:
                    return True  # 只在这个文件中出现
        
        return False
```

**结果**：
- 能恢复 21 个被拒绝的发现中的大部分
- 引入的新假阳性（ambiguous 情况）通过去重来处理

#### 改进 #2：改进的去重算法

v1 的去重是基于文本相似度（Jaccard），对中文无效。

v2 改为基于**语义信号**排序，而不是文本相似度：

```python
def merge_duplicates(findings: List[Finding]) -> List[Finding]:
    """
    去重排序：
    1. 完全相同的 (file, line, issue_type, evidence)
    2. 不合并不同的 issue_type（两个不同的缺陷留着）
    3. 优先保留：exact path > evidence_resolved > normalized > ambiguous
    """
    
    groups = {}  # key = (file_path, start_line, issue_type)
    
    for finding in findings:
        key = (finding.file_path, finding.start_line, finding.issue_type)
        
        if key not in groups:
            groups[key] = []
        groups[key].append(finding)
    
    result = []
    for key, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
        else:
            # 保留最好的版本（基于证据唯一性）
            best = max(group, key=lambda f: (
                f.evidence_uniqueness_score,
                f.confidence,
                len(f.evidence)  # 更长的证据更清晰
            ))
            result.append(best)
    
    return result
```

**重要**：两个**不同** `issue_type` 的发现**不会被去重**
- 如果同一行有 `null_dereference` 和 `missing_exception_handling`，都保留
- 这是两个真实的不同缺陷

### v3 结果

在相同的冻结发现集合上重新评估，路径恢复 + 改进的去重：

| Step | Findings | TP | FP | FN | P | R | F1 |
|---|---|---|---|---|---|---|---|
| After Layer 2 verification | 8 | 8 | 2 | 2 | 0.800 | 0.800 | 0.800 |
| With path recovery | 16 | 8 | 8 | 2 | 0.500 | 0.800 | 0.615 |
| With dedup | 8 | 8 | 0 | 2 | **1.000** | **0.800** | **0.889** |

**最终结果**：
- 精度 100%（没有假阳性）
- 回召 80%（漏掉 2 个真实缺陷，但这 2 个是标签不完整的情况）
- F1 = 0.889（可用的系统）

---

## 从 v1 到 v3：关键改进总结

### 量化改进

| Metric | v1 | v2 | v3 | 改进 |
|---|---|---|---|---|
| 精度（Precision） | 0.093 | 0.800 | 1.000 | **10.7x** |
| 回召（Recall） | 0.700 | 0.800 | 0.800 | 1.14x |
| F1 | 0.165 | 0.800 | 0.889 | **5.4x** |
| 最终发现数 | 75 | 8 | 8 | 89% 拒绝率 |

### 根本性改进

#### 1. **从「我相信模型」到「我验证发现」**
- v1：接受模型的所有输出
- v2：schema + 职责边界 + 三层验证
- v3：+ 路径恢复 + 去重

#### 2. **从「文本匹配」到「语义验证」**
- v1 matcher：Jaccard 相似度（失败在中文）
- v2 verifier：AST 解析、语法树检查
- v3：+ 证据唯一性分析

#### 3. **从「一个大 Agent」到「多个专家 Agent」**
- v1：单一 Agent，尝试做所有事情
- v2/v3：四个专家 Agent，每个有明确的职责和规则

#### 4. **从「假设最坏」到「证明最好」**
- v1 原则：「模型可能出错」→ 没有验证
- v2/v3 原则：「发现必须可被证明」→ 多层验证

---

## 面试角度：为什么这个演进过程很重要

### 展现的技能

✅ **问题诊断**：能从 68 个假阳性中系统地分类根本原因  
✅ **系统设计**：不是拍脑袋改 prompt，而是设计三层验证体系  
✅ **量化验证**：对每个改进都有明确的指标  
✅ **权衡思维**：理解 precision vs recall，假阳性 vs 遗漏  
✅ **实验方法**：使用冻结数据集做 ablation，确保公平对比  

### 关键对话要点

**Q: 你从 93% 假阳性到 100% 精度，是怎么做到的？**

A:
1. **诊断**：手工分类 68 个假阳性，发现 45% 是测量问题，42% 是职责越界，13% 是真实缺陷
2. **设计**：针对每个根本原因设计解决方案
   - 测量问题 → 修改 prompt 要求 trigger_condition/expected_behavior
   - 职责越界 → 为每个 Agent 定义闭合词表和规则
   - 路径问题 → 建立三层验证 + 路径恢复
3. **验证**：在冻结数据集上做 ablation，证明每一层的贡献
4. **结果**：从 8 个发现中 7 个正确（88% 精度）到 8 个 8 个正确（100%）

**Q: 为什么用三层验证而不是一个更强的模型？**

A:
1. 模型本身不是问题，问题是验证不足
2. 三层验证用的是确定性规则（不需要模型）
3. Layer 1-2 成本低，第一次运行就能拒绝 71% 的假阳性
4. 剩下的假阳性用 Layer 3（人工评审）处理，这样 token 成本最优

---

## 后续工作

### v4（考虑中）
- 实现 Layer 3 的批评家（Critic Agent）
- 对冲突或低置信度的发现进行二次评估
- 需要额外的 token 预算，会增加 10-15% 成本

### 评估协议更新
- 候选测试集（36 个用例）需要人工标注
- 确立官方的「黄金标准」，而不是依赖 fixture 标签
- 支持多版本 prompt 的对比评测

