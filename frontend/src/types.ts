export type Strategy = 'single' | 'static' | 'dynamic'
export type Page = 'review' | 'workflow' | 'code' | 'benchmark'

export interface DemoCase {
  case_id: string
  title: string
  defect_type: string
  file_path: string
  is_real_defect: boolean
}

export interface Finding {
  category: string
  severity: string
  file_path: string
  start_line?: number | null
  end_line?: number | null
  title: string
  description: string
  evidence: string
  suggestion: string
  confidence: number
  source_agent: string
  needs_human_review: boolean
}

export interface ReviewFile {
  path: string
  status: string
  patch?: string | null
  additions: number
  deletions: number
  changes: number
}

export interface ReviewEvent {
  run_id: string
  sequence: number
  timestamp: string
  node_id: string
  event_type: string
  status: string
  summary: string
  metrics: Record<string, number | string>
  error?: string
}

export interface AgentRun {
  agent: string
  status: string
  started_at: string
  ended_at: string
  duration_seconds: number
  calls: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  error_type?: string | null
  error?: string | null
  findings: Finding[]
}

export interface ReviewJob {
  run_id: string
  status: string
  mode: 'online' | 'demo'
  strategy: Strategy
  demo_case_id?: string | null
  repo?: string
  pr_number?: number
  title?: string
  head_sha?: string
  risk_level?: string
  selected_agents: string[]
  findings: Finding[]
  errors: { type: string; message: string }[]
  metrics: Record<string, number | string | null>
  token_usage?: Record<string, number>
  changed_files: ReviewFile[]
  diff: string
  trace: Record<string, unknown>[]
  agent_runs?: AgentRun[]
  output_dir?: string
  model?: string
}

export interface BenchmarkSummary {
  benchmark_id: string
  mode: string
  dataset_version: string
  case_count: number
  evaluated_at: string
  strategies: Record<string, Record<string, number | null | Record<string, number>>>
}
