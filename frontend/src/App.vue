<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { VueFlow, type Edge, type Node } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import * as echarts from 'echarts'
import * as monaco from 'monaco-editor'
import {
  ArrowRight,
  Check,
  CircleCheck,
  Clock,
  Cpu,
  DataAnalysis,
  Document,
  Download,
  Link,
  Monitor,
  Moon,
  Promotion,
  Refresh,
  Search,
  Share,
  Sunny,
  Timer,
  Warning,
} from '@element-plus/icons-vue'
import { createReview, getBenchmarkList, getDemos, getReview, reportUrl, subscribeToReview } from './api'
import type { BenchmarkSummary, DemoCase, Finding, Page, ReviewEvent, ReviewJob, Strategy } from './types'

const page = ref<Page>('review')
const dark = ref(true)
const loading = ref(false)
const error = ref('')
const runId = ref(localStorage.getItem('prguard:last-run') || '')
const currentJob = ref<ReviewJob | null>(null)
const events = ref<ReviewEvent[]>([])
const demos = ref<DemoCase[]>([])
const demoDescription = ref('')
const benchmarks = ref<BenchmarkSummary[]>([])
const benchmarkMessage = ref('')
const selectedNode = ref('dashboard')
const selectedFinding = ref<Finding | null>(null)
const activeFile = ref('')
const editorHost = ref<HTMLElement | null>(null)
const chartHost = ref<HTMLElement | null>(null)
let editor: monaco.editor.IStandaloneCodeEditor | undefined
let chart: echarts.ECharts | undefined
let unsubscribe: (() => void) | undefined
let pollTimer: number | undefined

const form = reactive({
  mode: 'demo' as 'online' | 'demo',
  prUrl: 'https://github.com/crewAIInc/crewAI/pull/8426',
  demoCase: 'sql-injection-05',
  strategy: 'dynamic' as Strategy,
})

const navItems: { id: Page; label: string; icon: typeof Monitor }[] = [
  { id: 'review', label: 'PR 审查', icon: Monitor },
  { id: 'workflow', label: 'Agent 工作流', icon: Share },
  { id: 'code', label: '问题定位', icon: Document },
  { id: 'benchmark', label: '评测对比', icon: DataAnalysis },
]

const workflowDefinitions = [
  { id: 'fetch_and_prepare', label: 'PR 获取', x: 32, y: 80 },
  { id: 'route_review', label: '风险路由', x: 272, y: 80 },
  { id: 'context', label: '上下文检索', x: 512, y: 80 },
  { id: 'CodeQualityAgent', label: '代码质量 Agent', x: 192, y: 245 },
  { id: 'SecurityAgent', label: '安全审查 Agent', x: 432, y: 245 },
  { id: 'TestImpactAgent', label: '测试影响 Agent', x: 672, y: 245 },
  { id: 'ContextAgent', label: '上下文 Agent', x: 912, y: 245 },
  { id: 'verify_and_report', label: 'Verifier 核验器', x: 432, y: 410 },
  { id: 'report', label: '报告生成', x: 672, y: 410 },
]

const workflowEdges: Edge[] = [
  { id: 'e1', source: 'fetch_and_prepare', target: 'route_review', animated: true },
  { id: 'e2', source: 'route_review', target: 'context' },
  { id: 'e3', source: 'context', target: 'CodeQualityAgent' },
  { id: 'e4', source: 'context', target: 'SecurityAgent' },
  { id: 'e5', source: 'context', target: 'TestImpactAgent' },
  { id: 'e6', source: 'context', target: 'ContextAgent' },
  { id: 'e7', source: 'CodeQualityAgent', target: 'verify_and_report' },
  { id: 'e8', source: 'SecurityAgent', target: 'verify_and_report' },
  { id: 'e9', source: 'TestImpactAgent', target: 'verify_and_report' },
  { id: 'e10', source: 'ContextAgent', target: 'verify_and_report' },
  { id: 'e11', source: 'verify_and_report', target: 'report' },
]

const terminal = computed(() => ['completed', 'partial', 'failed'].includes(currentJob.value?.status || ''))
const latestBenchmark = computed(() => benchmarks.value[0])
const activeFileData = computed(() => currentJob.value?.changed_files.find((file) => file.path === activeFile.value) || currentJob.value?.changed_files[0])
const activePatch = computed(() => activeFileData.value?.patch || currentJob.value?.diff || '暂无补丁数据。')
const fileFindings = computed(() => currentJob.value?.findings.filter((finding) => finding.file_path === activeFile.value) || [])
const selectedEvent = computed(() => [...events.value].reverse().find((event) => event.node_id === selectedNode.value))
const workflowNodes = computed<Node[]>(() => workflowDefinitions.map((definition) => {
  const status = nodeStatus(definition.id)
  return {
    id: definition.id,
    position: { x: definition.x, y: definition.y },
    data: { label: `${definition.label}\n${statusLabel(status)}` },
    class: `workflow-node status-${status}`,
  }
}))

function nodeStatus(nodeId: string): string {
  if (!currentJob.value && !events.value.length) return 'waiting'
  if (nodeId === 'context') {
    const event = events.value.find((item) => item.node_id === 'fetch_and_prepare')
    return event ? 'completed' : 'waiting'
  }
  if (nodeId === 'report') return terminal.value ? 'completed' : 'waiting'
  const records = events.value.filter((item) => item.node_id === nodeId)
  const latest = records[records.length - 1]
  if (latest) return latest.status
  if (currentJob.value?.selected_agents.includes(nodeId)) return 'waiting'
  if (['SecurityAgent', 'ContextAgent'].includes(nodeId) && currentJob.value) return 'skipped'
  return 'waiting'
}

function statusLabel(status: string) {
  return statusText(status)
}

function statusText(status: string) {
  return ({
    queued: '排队中',
    waiting: '等待中',
    running: '运行中',
    success: '成功',
    completed: '已完成',
    partial: '部分完成',
    failed: '失败',
    timeout: '超时',
    skipped: '已跳过',
  } as Record<string, string>)[status] || status
}

function nodeLabel(nodeId: string) {
  return workflowDefinitions.find((definition) => definition.id === nodeId)?.label || nodeId
}

function eventSummaryText(summary: string) {
  const direct = ({
    'Review queued': '审查任务已排队',
    'Review started': '审查任务已启动',
    'Review completed': '审查任务已完成',
    'Review partial': '审查任务部分完成',
    'Review failed': '审查任务失败',
  } as Record<string, string>)[summary]
  if (direct) return direct
  const started = summary.match(/^(.+) started$/)
  if (started) return `${nodeLabel(started[1])} 已开始`
  const finished = summary.match(/^(.+) (completed|success|failed|timeout|skipped)$/)
  if (finished) return `${nodeLabel(finished[1])}：${statusText(finished[2])}`
  return summary
}

function strategyLabel(strategy: string) {
  return ({ single: '单 Agent', static: '静态多 Agent', dynamic: '动态多 Agent' } as Record<string, string>)[strategy] || strategy
}

function agentLabel(agent: string) {
  return ({
    CodeQualityAgent: '代码质量 Agent',
    SecurityAgent: '安全审查 Agent',
    TestImpactAgent: '测试影响 Agent',
    ContextAgent: '上下文 Agent',
    VerifierAgent: 'Verifier 核验器',
  } as Record<string, string>)[agent] || agent
}

function modeLabel(mode: string) {
  return mode === 'demo' ? '离线 Demo' : mode === 'online' ? '在线 GitHub' : mode
}

function severityText(severity: string) {
  return ({ critical: '严重', high: '高', medium: '中', low: '低', info: '提示' } as Record<string, string>)[severity] || severity
}

function riskText(risk: string) {
  return ({ low: '低风险', medium: '中风险', high: '高风险', critical: '严重风险' } as Record<string, string>)[risk] || risk
}

function fileStatusText(status: string) {
  return ({ added: '新增', modified: '修改', removed: '删除', renamed: '重命名' } as Record<string, string>)[status] || status
}

function defectTypeText(defectType: string) {
  return ({
    null_dereference: '空值引用',
    sql_injection: 'SQL 注入',
    documentation_only: '仅文档修改',
    missing_exception_handling: '缺少异常处理',
    boundary_condition: '边界条件错误',
    resource_leak: '资源泄露',
    unsafe_command: '不安全命令调用',
  } as Record<string, string>)[defectType] || defectType
}

function benchmarkMessageText(message: string) {
  if (message.includes('Offline results are deterministic fixture pipeline outputs')) {
    return '结果来自确定性的离线 Fixture 流程，不代表真实 LLM 质量。'
  }
  return message
}

function statusType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'completed' || status === 'success') return 'success'
  if (status === 'failed' || status === 'timeout') return 'danger'
  if (status === 'skipped') return 'info'
  return 'warning'
}

function metricValue(key: string) {
  return currentJob.value?.metrics?.[key] ?? currentJob.value?.token_usage?.[key] ?? 0
}

function formatSeconds(value: unknown) {
  const number = Number(value || 0)
  return `${number.toFixed(number > 10 ? 1 : 3)}s`
}

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

async function loadReferenceData() {
  try {
    const [demoData, benchmarkData] = await Promise.all([getDemos(), getBenchmarkList()])
    demos.value = demoData.cases
    demoDescription.value = demoData.description.includes('Offline deterministic')
      ? '使用确定性的离线 Fixture 回放，不代表真实 LLM 质量，也不会产生外部 API 费用。'
      : demoData.description
    benchmarks.value = benchmarkData.benchmarks
    benchmarkMessage.value = benchmarkMessageText(benchmarkData.message)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '加载 Dashboard 参考数据失败。'
  }
}

function ingestEvent(event: ReviewEvent) {
  if (!events.value.some((item) => item.sequence === event.sequence)) events.value.push(event)
  selectedNode.value = event.node_id
}

async function refreshJob() {
  if (!runId.value) return
  try {
    currentJob.value = await getReview(runId.value)
    if (currentJob.value.changed_files?.length && !activeFile.value) activeFile.value = currentJob.value.changed_files[0].path
    if (terminal.value) stopTracking()
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : '刷新审查任务失败。'
    if (message.toLowerCase().includes('review run not found')) {
      clearStaleRun()
      error.value = ''
      return
    }
    error.value = message
  }
}

function stopTracking() {
  if (unsubscribe) unsubscribe()
  unsubscribe = undefined
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = undefined
}

function clearStaleRun() {
  stopTracking()
  runId.value = ''
  currentJob.value = null
  activeFile.value = ''
  selectedFinding.value = null
  localStorage.removeItem('prguard:last-run')
}

function trackReview(id: string) {
  stopTracking()
  unsubscribe = subscribeToReview(id, ingestEvent, () => refreshJob())
  pollTimer = window.setInterval(refreshJob, 1200)
}

async function startReview() {
  loading.value = true
  error.value = ''
  selectedFinding.value = null
  events.value = []
  try {
    const payload = form.mode === 'demo'
      ? { mode: 'demo' as const, demo_case_id: form.demoCase, strategy: form.strategy }
      : { mode: 'online' as const, pr_url: form.prUrl.trim(), strategy: form.strategy }
    const created = await createReview(payload)
    runId.value = created.run_id
    localStorage.setItem('prguard:last-run', created.run_id)
    page.value = 'workflow'
    await refreshJob()
    trackReview(created.run_id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '创建审查任务失败。'
  } finally {
    loading.value = false
  }
}

async function restoreRun() {
  if (!runId.value) return
  await refreshJob()
  if (currentJob.value && !terminal.value) trackReview(runId.value)
}

function selectFinding(finding: Finding) {
  selectedFinding.value = finding
  activeFile.value = finding.file_path
  page.value = 'code'
  nextTick(() => {
    if (editor && finding.start_line) editor.revealLineInCenter(finding.start_line)
  })
}

function selectFile(path: string) {
  activeFile.value = path
  nextTick(renderEditor)
}

function renderEditor() {
  if (!editorHost.value) return
  editor?.dispose()
  editor = monaco.editor.create(editorHost.value, {
    value: activePatch.value,
    language: 'diff',
    theme: dark.value ? 'vs-dark' : 'vs',
    readOnly: true,
    minimap: { enabled: false },
    lineNumbers: 'on',
    wordWrap: 'on',
    padding: { top: 16, bottom: 16 },
    scrollbar: { verticalScrollbarSize: 8 },
  })
  if (selectedFinding.value?.start_line) editor.revealLineInCenter(selectedFinding.value.start_line)
}

function renderChart() {
  if (!chartHost.value || !latestBenchmark.value) return
  chart?.dispose()
  chart = echarts.init(chartHost.value, dark.value ? 'dark' : undefined)
  const strategies = ['single', 'static', 'dynamic']
  const labels = ['单 Agent', '静态多 Agent', '动态多 Agent']
  const metric = (name: string) => strategies.map((strategy) => Number(latestBenchmark.value?.strategies[strategy]?.[name] || 0))
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 42, right: 20, top: 28, bottom: 32 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['精确率', '召回率', 'F1'], textStyle: { color: '#9aa7ba' } },
    xAxis: { type: 'category', data: labels, axisLabel: { color: '#8d9ab0', interval: 0 } },
    yAxis: { type: 'value', min: 0, max: 1, splitLine: { lineStyle: { color: '#273244' } }, axisLabel: { color: '#8d9ab0' } },
    series: [
      { name: '精确率', type: 'bar', data: metric('defect_precision'), itemStyle: { color: '#49b6a5' }, barGap: '10%' },
      { name: '召回率', type: 'bar', data: metric('defect_recall'), itemStyle: { color: '#6c8cff' } },
      { name: 'F1', type: 'bar', data: metric('f1_score'), itemStyle: { color: '#f0a35b' } },
    ],
  })
}

watch([page, latestBenchmark, dark], () => {
  if (page.value === 'benchmark') nextTick(renderChart)
  if (page.value === 'code') nextTick(renderEditor)
})

onMounted(async () => {
  document.documentElement.dataset.theme = dark.value ? 'dark' : 'light'
  await loadReferenceData()
  await restoreRun()
})

onUnmounted(() => {
  stopTracking()
  editor?.dispose()
  chart?.dispose()
})

function toggleTheme() {
  dark.value = !dark.value
  document.documentElement.dataset.theme = dark.value ? 'dark' : 'light'
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand-mark"><span class="brand-glyph">P</span><div><strong>PRGuard</strong><small>Agent 可观测性</small></div></div>
      <div class="sidebar-label">工作区</div>
      <nav class="nav-list">
        <button v-for="item in navItems" :key="item.id" class="nav-item" :class="{ active: page === item.id }" @click="page = item.id">
          <component :is="item.icon" /><span>{{ item.label }}</span>
          <span v-if="item.id === 'workflow' && currentJob" class="nav-pulse" :class="`pulse-${currentJob.status}`"></span>
        </button>
      </nav>
      <div class="sidebar-spacer"></div>
      <div class="runtime-card">
        <div class="runtime-heading"><span class="live-dot"></span> 运行环境</div>
        <div class="runtime-row"><span>后端</span><b>FastAPI</b></div>
        <div class="runtime-row"><span>流程</span><b>CrewAI 0.98</b></div>
        <div class="runtime-row"><span>事件</span><b>SSE</b></div>
      </div>
      <button class="theme-button" @click="toggleTheme"><component :is="dark ? Sunny : Moon" /><span>{{ dark ? '浅色主题' : '深色主题' }}</span></button>
    </aside>

    <main class="main-panel">
      <header class="topbar">
        <div><span class="eyebrow">多 Agent 智能代码审查</span><h1>{{ page === 'review' ? '审查工作区' : page === 'workflow' ? 'Agent 工作流' : page === 'code' ? '问题定位' : '评测对比' }}</h1></div>
        <div class="topbar-meta">
          <span v-if="form.mode === 'demo'" class="demo-badge"><span class="demo-dot"></span> 离线 Demo</span>
          <span v-if="currentJob" class="run-chip"><span class="run-id-label">运行 ID</span>{{ currentJob.run_id.slice(0, 8) }}</span>
          <el-tag v-if="currentJob" :type="statusType(currentJob.status)" effect="dark">{{ statusText(currentJob.status) }}</el-tag>
        </div>
      </header>

      <div v-if="error" class="error-banner"><Warning /><span>{{ error }}</span><button @click="error = ''">关闭</button></div>

      <section v-if="page === 'review'" class="page-content review-page">
        <div class="review-grid">
          <div class="panel intake-panel">
            <div class="panel-kicker"><span class="kicker-line"></span> 启动审查任务</div>
            <h2>将 Pull Request<br /><em>接入审查流程</em></h2>
            <p class="muted-copy">按风险选择必要的专业 Agent，基于源代码证据核验每条问题，并保留完整执行轨迹。</p>
            <div class="mode-switch">
              <button :class="{ selected: form.mode === 'demo' }" @click="form.mode = 'demo'"><Promotion /> 离线 Demo</button>
              <button :class="{ selected: form.mode === 'online' }" @click="form.mode = 'online'"><Link /> 在线 GitHub</button>
            </div>
            <div v-if="form.mode === 'demo'" class="demo-selector">
              <label>演示案例</label>
              <select v-model="form.demoCase">
                <option v-for="demo in demos" :key="demo.case_id" :value="demo.case_id">{{ demo.case_id }} · {{ defectTypeText(demo.defect_type) }}</option>
              </select>
              <small>{{ demoDescription || '使用固定 Fixture 离线回放，不产生真实 LLM 费用。' }}</small>
            </div>
            <div v-else class="url-field">
              <label>GitHub Pull Request 地址</label>
              <div class="input-with-icon"><Link /><input v-model="form.prUrl" placeholder="https://github.com/owner/repo/pull/123" /></div>
            </div>
            <div class="field-block"><label>审查策略</label><div class="strategy-tabs"><button v-for="strategy in ['single', 'static', 'dynamic'] as Strategy[]" :key="strategy" :class="{ selected: form.strategy === strategy }" @click="form.strategy = strategy"><span class="strategy-indicator" :class="strategy"></span>{{ strategyLabel(strategy) }}</button></div></div>
            <button class="primary-action" :disabled="loading" @click="startReview"><span v-if="loading" class="button-spinner"></span><ArrowRight v-else /> {{ loading ? '正在启动…' : '开始审查' }}</button>
          </div>

          <div class="panel architecture-panel">
            <div class="panel-header"><div><div class="panel-kicker"><span class="kicker-line blue"></span> 执行模型</div><h3>证据优先，按风险路由</h3></div><el-tag type="success" effect="plain">只读</el-tag></div>
            <div class="architecture-stack">
              <div class="arch-step"><span class="arch-number">01</span><div><b>获取并分类</b><small>读取 GitHub 元数据、补丁、修改文件，并计算确定性风险等级</small></div><Check /></div>
              <div class="arch-connector"></div>
              <div class="arch-step"><span class="arch-number">02</span><div><b>调度专业 Agent</b><small>动态 Flow 选择代码质量、安全、测试影响和上下文 Agent</small></div><Cpu /></div>
              <div class="arch-connector"></div>
              <div class="arch-step"><span class="arch-number">03</span><div><b>核验并生成报告</b><small>去重、核对代码证据，并输出 JSON、Markdown 和执行轨迹</small></div><CircleCheck /></div>
            </div>
            <div class="stat-strip"><div><strong>{{ currentJob?.selected_agents?.length || 0 }}</strong><span>已选 Agent</span></div><div><strong>{{ currentJob?.findings?.length || 0 }}</strong><span>已核验问题</span></div><div><strong>{{ formatSeconds(metricValue('execution_seconds')) }}</strong><span>最近耗时</span></div></div>
          </div>
        </div>
        <div class="section-heading"><div><span class="eyebrow">当前任务</span><h3>实时查看执行过程</h3></div><button v-if="currentJob" class="quiet-button" @click="page = 'workflow'">查看工作流 <ArrowRight /></button></div>
        <div v-if="currentJob" class="run-overview panel">
          <div class="run-overview-main"><div class="run-title"><span class="status-ring" :class="`ring-${currentJob.status}`"></span><div><strong>{{ currentJob.title || currentJob.demo_case_id || '审查任务' }}</strong><small>{{ currentJob.repo }}{{ currentJob.pr_number ? ` #${currentJob.pr_number}` : '' }}</small></div></div><el-tag :type="statusType(currentJob.status)">{{ statusText(currentJob.status) }}</el-tag></div>
          <div class="run-overview-stats"><div><span>风险等级</span><b>{{ riskText(currentJob.risk_level || '—') }}</b></div><div><span>Agent 数量</span><b>{{ currentJob.selected_agents.length }}</b></div><div><span>问题数量</span><b>{{ currentJob.findings.length }}</b></div><div><span>Token 数</span><b>{{ currentJob.token_usage?.total || 0 }}</b></div><div><span>运行模式</span><b>{{ modeLabel(currentJob.mode) }}</b></div></div>
          <div class="run-actions"><button class="quiet-button" @click="page = 'code'">查看问题 <Search /></button><a v-if="terminal" class="download-link" :href="reportUrl(currentJob.run_id, 'md')" target="_blank"><Download /> Markdown 报告</a><a v-if="terminal" class="download-link" :href="reportUrl(currentJob.run_id, 'json')" target="_blank"><Download /> JSON 报告</a></div>
        </div>
        <div v-else class="empty-panel panel"><Clock /><div><strong>暂无审查任务</strong><span>启动一个离线 Demo，即可查看 Flow、事件和证据定位。</span></div></div>
      </section>

      <section v-else-if="page === 'workflow'" class="page-content workflow-page">
        <div class="page-toolbar"><div><span class="eyebrow">实时事件流</span><h2>Flow 执行图</h2></div><div class="toolbar-actions"><span v-if="currentJob?.mode === 'demo'" class="small-demo-label">离线 Fixture 回放</span><button class="quiet-button" @click="refreshJob"><Refresh /> 刷新</button></div></div>
        <div class="workflow-layout">
          <div class="panel flow-panel"><VueFlow :nodes="workflowNodes" :edges="workflowEdges" :fit-view-on-init="true" :nodes-draggable="false" :nodes-connectable="false" @node-click="({ node }) => selectedNode = node.id"><Background pattern-color="#263247" :gap="24" /><Controls /><MiniMap pannable zoomable /></VueFlow></div>
          <div class="panel event-panel"><div class="panel-header"><div><div class="panel-kicker"><span class="kicker-line"></span> 事件日志</div><h3>已接收 {{ events.length }} 个事件</h3></div><span class="stream-status"><span class="live-dot"></span> SSE</span></div><div class="event-list"><div v-for="event in [...events].reverse()" :key="event.sequence" class="event-row" :class="{ selected: selectedNode === event.node_id }" @click="selectedNode = event.node_id"><span class="event-index">{{ String(event.sequence).padStart(2, '0') }}</span><span class="event-state" :class="`event-${event.status}`"></span><div><b>{{ nodeLabel(event.node_id) }}</b><small>{{ eventSummaryText(event.summary) }}</small></div><time>{{ formatTime(event.timestamp) }}</time></div><div v-if="!events.length" class="empty-inline">任务开始后，事件会显示在这里。</div></div></div>
        </div>
        <div class="panel node-detail"><div class="panel-header"><div><div class="panel-kicker"><span class="kicker-line blue"></span> 当前节点</div><h3>{{ nodeLabel(selectedNode) }}</h3></div><el-tag :type="statusType(nodeStatus(selectedNode))">{{ statusText(nodeStatus(selectedNode)) }}</el-tag></div><div v-if="selectedEvent" class="detail-grid"><div><span>状态</span><b>{{ statusText(selectedEvent.status) }}</b></div><div><span>摘要</span><b>{{ eventSummaryText(selectedEvent.summary) }}</b></div><div><span>耗时</span><b>{{ formatSeconds(selectedEvent.metrics.duration_seconds) }}</b></div><div><span>调用次数</span><b>{{ selectedEvent.metrics.calls || 0 }}</b></div><div><span>输入 Token</span><b>{{ selectedEvent.metrics.input_tokens || 0 }}</b></div><div><span>问题数量</span><b>{{ selectedEvent.metrics.finding_count || 0 }}</b></div></div><div v-else class="empty-inline">选择一个节点，或等待第一个事件到达。</div></div>
      </section>

      <section v-else-if="page === 'code'" class="page-content code-page">
        <div class="page-toolbar"><div><span class="eyebrow">源代码证据</span><h2>对照补丁查看审查问题</h2></div><div class="toolbar-actions"><span class="small-meta">{{ currentJob?.findings.length || 0 }} 条已核验问题</span></div></div>
        <div v-if="currentJob" class="code-layout">
          <div class="panel file-panel"><div class="panel-header"><div><div class="panel-kicker"><span class="kicker-line"></span> 修改文件</div><h3>{{ currentJob.changed_files.length }} 个文件</h3></div></div><button v-for="file in currentJob.changed_files" :key="file.path" class="file-row" :class="{ active: activeFile === file.path }" @click="selectFile(file.path)"><Document /><div><b>{{ file.path.split('/')[file.path.split('/').length - 1] }}</b><small>{{ file.path }}</small></div><span class="file-count">{{ file.changes }}</span></button></div>
          <div class="panel diff-panel"><div class="diff-toolbar"><div><b>{{ activeFileData?.path || '补丁' }}</b><span>{{ fileStatusText(activeFileData?.status || '—') }} · +{{ activeFileData?.additions || 0 }} -{{ activeFileData?.deletions || 0 }}</span></div><span class="evidence-label"><span class="evidence-dot"></span> 已完成证据核验</span></div><div ref="editorHost" class="editor-host"></div><div v-if="!currentJob.changed_files.length" class="empty-inline">暂未返回 Diff。</div></div>
          <div class="panel findings-panel"><div class="panel-header"><div><div class="panel-kicker"><span class="kicker-line blue"></span> 审查问题</div><h3>{{ fileFindings.length }} 条</h3></div></div><button v-for="finding in fileFindings" :key="`${finding.file_path}-${finding.start_line}-${finding.title}`" class="finding-row" :class="{ selected: selectedFinding === finding }" @click="selectFinding(finding)"><div class="finding-row-top"><el-tag :type="statusType(finding.severity === 'critical' || finding.severity === 'high' ? 'failed' : finding.severity === 'medium' ? 'running' : 'info')" size="small">{{ severityText(finding.severity) }}</el-tag><span>第 {{ finding.start_line || '—' }} 行</span></div><b>{{ finding.title }}</b><small>{{ agentLabel(finding.source_agent) }} · 置信度 {{ Math.round(finding.confidence * 100) }}%</small></button><div v-if="!fileFindings.length" class="empty-inline">此文件没有已核验问题。</div></div>
        </div>
        <div v-else class="empty-panel panel"><Document /><div><strong>暂无审查结果</strong><span>请先启动任务，再查看修改文件和审查问题。</span></div></div>
        <div v-if="selectedFinding" class="finding-detail panel"><div class="finding-detail-header"><div><span class="eyebrow">已核验问题</span><h3>{{ selectedFinding.title }}</h3></div><el-tag :type="statusType(selectedFinding.severity === 'critical' || selectedFinding.severity === 'high' ? 'failed' : 'running')">{{ severityText(selectedFinding.severity) }}</el-tag></div><div class="finding-columns"><div><span>代码证据</span><code>{{ selectedFinding.evidence }}</code></div><div><span>问题影响</span><p>{{ selectedFinding.description }}</p></div><div><span>修复建议</span><p>{{ selectedFinding.suggestion }}</p></div></div><div v-if="selectedFinding.needs_human_review" class="review-note"><Warning /> 置信度较低：请将此问题保留在人工作业队列中。</div></div>
      </section>

      <section v-else class="page-content benchmark-page"><div class="page-toolbar"><div><span class="eyebrow">评测实验室</span><h2>审查策略对比</h2></div><span v-if="latestBenchmark" class="small-meta">{{ latestBenchmark.case_count }} 个 Fixture · {{ new Date(latestBenchmark.evaluated_at).toLocaleString('zh-CN') }}</span></div><div v-if="latestBenchmark" class="benchmark-layout"><div class="panel chart-panel"><div class="panel-header"><div><div class="panel-kicker"><span class="kicker-line blue"></span> 缺陷检测</div><h3>精确率、召回率与 F1</h3></div><span class="offline-label">离线 Fixture</span></div><div ref="chartHost" class="chart-host"></div><p class="chart-note">{{ benchmarkMessage || '指标来自已生成的离线评测结果。' }}</p></div><div class="panel benchmark-table-panel"><div class="panel-header"><div><div class="panel-kicker"><span class="kicker-line"></span> 运行指标</div><h3>架构对比</h3></div></div><table><thead><tr><th>策略</th><th>精确率</th><th>召回率</th><th>F1</th><th>严格精度</th><th>建议率</th><th>拒绝率</th><th>输入 Token</th><th>调用次数 / PR</th><th>平均耗时</th><th>P95</th></tr></thead><tbody><tr v-for="strategy in ['single', 'static', 'dynamic']" :key="strategy"><td><b>{{ strategyLabel(strategy) }}</b></td><td>{{ Number(latestBenchmark.strategies[strategy]?.defect_precision || 0).toFixed(3) }}</td><td>{{ Number(latestBenchmark.strategies[strategy]?.defect_recall || 0).toFixed(3) }}</td><td><strong class="metric-highlight">{{ Number(latestBenchmark.strategies[strategy]?.f1_score || 0).toFixed(3) }}</strong></td><td>{{ Number(latestBenchmark.strategies[strategy]?.strict_precision ?? latestBenchmark.strategies[strategy]?.defect_precision ?? 0).toFixed(3) }}</td><td>{{ Number(latestBenchmark.strategies[strategy]?.advisory_rate || 0).toFixed(3) }}</td><td>{{ Number(latestBenchmark.strategies[strategy]?.verifier_rejection_rate || 0).toFixed(3) }}</td><td>{{ Number(latestBenchmark.strategies[strategy]?.mean_input_tokens || 0).toFixed(1) }}</td><td>{{ Number(latestBenchmark.strategies[strategy]?.mean_llm_calls || 0).toFixed(2) }}</td><td>{{ formatSeconds(latestBenchmark.strategies[strategy]?.mean_latency_seconds) }}</td><td>{{ formatSeconds(latestBenchmark.strategies[strategy]?.p95_latency_seconds) }}</td></tr></tbody></table><div class="benchmark-disclaimer"><Warning /> 此面板读取生成的 `results.json`。精确率/召回率只统计已确认缺陷（defect），严格精度把改进建议（advisory）计为误报，建议率与拒绝率用于识别“全部改成建议”的退化；离线数值描述确定性的 Fixture 流程，不代表真实 LLM 效果。</div></div></div><div v-else class="empty-panel panel"><DataAnalysis /><div><strong>未找到评测结果</strong><span>请先运行 <code>uv run python -m evals.run --mode offline</code>。</span></div></div></section>
    </main>
  </div>
</template>
