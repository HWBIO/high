export interface Candidate {
  id: number
  name: string
  phone: string
  city: string
  current_salary: string
  expected_salary: string
  tags: string
  target_position: string
  current_company: string
  current_title: string
  age: string
  talent_status: string
  owner: string
  has_resume: number
  created_at: string
}

export interface Position {
  id: number
  company_id: number | null
  seq_no: string
  title: string
  source: string
  status: string
  company: string | null
  consultant_notes: string
  headcount: number
  salary_range: string
  hard_requirements: string
  nice_to_have: string
  project_status: string
  n_talents: number
  n_recommend: number
  n_interview: number
  n_offer: number
  consultant: string
  is_project: number
  hard_struct_json: string
  project_json: string
  has_struct: number
  has_position_prompt: number
  updated_at: string
}

export interface PositionOption {
  id: number
  seq_no: string | null
  title: string
  company: string | null
}

export interface ProjectCard {
  id: number
  seq_no: string | null
  title: string
  salary_range: string
  project_status: string
  company_id: number | null
  company: string | null
  has_position_prompt: number
  city: string
  industry: string
}

export interface Company {
  id: number
  name: string
  legal_name: string
  short_name: string
  city: string
  industry: string
  contact_user_id: number | null
  contact_name: string
  contact_display_name: string | null
  status: string
  notes: string
  extra: Record<string, unknown>
  position_count: number
  project_count: number
  created_at: string
  updated_at: string
}

export interface Prompt {
  id: number
  layer: 'general' | 'company' | 'position'
  company_id: number | null
  position_id: number | null
  content: string
  is_active: number
  is_effective?: number
  version: number
  updated_by: string
  scope?: string
  owner_user_id?: number | null
  owner_name?: string
  created_at: string
  company: string | null
  position_title: string | null
}

export interface PageResult<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface RunStatus {
  run_id: number
  status: string
  error: string
  total_tokens: number
  progress: {
    total_positions: number
    results_saved: number
    valid_results: number
    failed_results: number
    status: string
  }
}

export interface BatchRunStatus {
  items: RunStatus[]
  done: number
  partial_failed: number
  failed: number
  running: number
  total: number
}

export interface MatchHistoryItem {
  result_id: number
  run_id: number
  candidate_id: number
  position_id: number
  final_score: number | null
  recommend_level: string | null
  exclude_reason: string
  review_status: string
  status: string
  total_tokens: number
  created_at: string
  finished_at: string | null
  error: string
  candidate_name: string
  position_title: string
  seq_no: string | null
  company: string | null
  version_count?: number
}

export interface DetailRow {
  requirement: string
  condition_type?: 'hard' | 'priority' | 'soft' | string
  label: string
  note: string
}

export interface Suggestion {
  type: string
  text: string
}

export interface DetailItem {
  position_id: number
  candidate_name?: string
  seq_no: string
  title: string
  company: string
  final_score: number
  recommend_level: string | null
  recommend_label: string
  rows: DetailRow[]
  score_summary?: {
    hard?: { total: number; satisfied: number; failed: number; pending: number; passed: boolean; score?: number }
    priority?: { total: number; satisfied: number; failed: number; pending: number; score?: number }
    soft?: { total: number; satisfied: number; failed: number; pending: number; score?: number }
    blocker?: { total: number; satisfied: number; failed: number; pending: number; passed: boolean; score?: number; label?: string }
    dimensions?: Array<{ total: number; satisfied: number; failed: number; pending: number; score?: number; label?: string; weight?: number }>
    all?: { total: number; satisfied: number; failed: number; pending: number }
    confidence?: number
    scoring_rule?: string
    judgement?: string
  }
  suggestions: Suggestion[]
}

export interface Report {
  run_id: number
  candidate_id: number
  candidate_name: string
  candidate: Candidate
  status: string
  model: string
  total_tokens: number
  created_at: string
  finished_at: string | null
  details: DetailItem[]
}

export interface ClientRecommendation {
  id?: number
  version?: number
  fields: Record<string, string>
  reasons: Array<{ title: string; text: string }>
  leave_reason: string
  ps: string
  model: string
  tokens: number
  is_ai: boolean
  status?: string
  error?: string
}

export interface Tracking {
  id: number
  candidate_id: number
  position_id: number | null
  match_result_id: number | null
  final_score_snapshot: number | null
  recommend_level_snapshot: string
  source: string
  recommended_at: string
  interview_at: string
  stage: string
  reject_reason: string
  client_feedback: string
  next_action: string
  status: string
  owner: string
  created_at: string
  updated_at: string
  candidate_name: string
  position_title: string | null
  seq_no: string | null
  company: string | null
}
