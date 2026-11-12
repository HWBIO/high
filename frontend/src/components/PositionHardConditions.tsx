import { ImeTextInput } from './ImeTextInput'

export interface HardStructDraft {
  language: string
  company_background: string
  gender: string
  arrival_time: string
  must_skills: string
  excluded_companies: string
  vetoes: string
}

export interface ParsedPositionPayload {
  company?: string
  title?: string
  headcount?: number
  salary_range?: string
  project_status?: string
  jd_raw?: string
  project?: Record<string, string>
  hard_struct?: Record<string, string | string[]>
  unstructured_summary?: string
}

export const PROJECT_STATUS = ['未开始', '进行中', '已结束']

export const EMPTY_HARD_STRUCT: HardStructDraft = {
  language: '',
  company_background: '',
  gender: '',
  arrival_time: '',
  must_skills: '',
  excluded_companies: '',
  vetoes: '',
}

const LIST_FIELDS = new Set(['must_skills', 'excluded_companies', 'vetoes'])

export function nextDateString(date: string) {
  if (!date) return undefined
  const current = new Date(`${date}T00:00:00`)
  if (Number.isNaN(current.getTime())) return undefined
  current.setDate(current.getDate() + 1)
  return current.toISOString().slice(0, 10)
}

export function splitList(value: string) {
  return value.split(/[、,，\n]/).map((s) => s.trim()).filter(Boolean)
}

export function hardStructToPayload(draft: HardStructDraft) {
  const payload: Record<string, string | string[]> = {}
  Object.entries(draft).forEach(([key, value]) => {
    if (!value.trim()) return
    payload[key] = LIST_FIELDS.has(key) ? splitList(value) : value.trim()
  })
  return payload
}

export function hardStructToDraft(raw: string): HardStructDraft {
  let data: Record<string, string | string[]> = {}
  try {
    data = raw ? JSON.parse(raw) : {}
  } catch {
    data = {}
  }
  const draft = { ...EMPTY_HARD_STRUCT }
  Object.keys(draft).forEach((key) => {
    const value = data[key]
    draft[key as keyof HardStructDraft] = Array.isArray(value) ? value.join('、') : String(value || '')
  })
  return draft
}

export function parsedHardStructToDraft(
  hardStruct: Record<string, string | string[]> = {},
  fallback: HardStructDraft = EMPTY_HARD_STRUCT,
) {
  return {
    ...fallback,
    language: String(hardStruct.language || fallback.language),
    company_background: String(hardStruct.company_background || fallback.company_background),
    gender: String(hardStruct.gender || fallback.gender),
    arrival_time: String(hardStruct.arrival_time || fallback.arrival_time),
    must_skills: Array.isArray(hardStruct.must_skills)
      ? hardStruct.must_skills.join('、')
      : String(hardStruct.must_skills || fallback.must_skills),
    excluded_companies: Array.isArray(hardStruct.excluded_companies)
      ? hardStruct.excluded_companies.join('、')
      : String(hardStruct.excluded_companies || fallback.excluded_companies),
    vetoes: Array.isArray(hardStruct.vetoes)
      ? hardStruct.vetoes.join('、')
      : String(hardStruct.vetoes || fallback.vetoes),
  }
}

const hardStructFields: Array<[keyof HardStructDraft, string, string]> = [
  ['language', '语言能力', '如 英语可作为工作语言'],
  ['company_background', '名企背景', '如 头部互联网/游戏公司'],
  ['gender', '性别', '不限'],
  ['arrival_time', '到岗时间', '如 1个月内'],
  ['must_skills', '必须技能', '如 Java、Spring、MySQL'],
  ['excluded_companies', '排除公司', '如 XX公司、YY公司'],
  ['vetoes', '一票否决', '如 薪资超预算、竞业限制'],
]

interface HardStructInputsProps {
  draft: HardStructDraft
  onChange: (next: HardStructDraft) => void
  title?: string
}

export function HardStructInputs({ draft, onChange, title = '匹配硬条件' }: HardStructInputsProps) {
  const primaryFields = hardStructFields.slice(0, 4)
  const ruleFields = hardStructFields.slice(4)
  const renderField = ([key, label, placeholder]: [keyof HardStructDraft, string, string]) => (
    <label key={key} className="form-field">
      <span className="muted">{label}：</span>
      {key === 'company_background' ? (
        <select value={draft[key]} onChange={(e) => onChange({ ...draft, [key]: e.target.value })}>
          <option value="">不限</option>
          <option value="是">是</option>
          <option value="否">否</option>
        </select>
      ) : key === 'gender' ? (
        <select value={draft[key]} onChange={(e) => onChange({ ...draft, [key]: e.target.value })}>
          <option value="">不限</option>
          <option value="男">男</option>
          <option value="女">女</option>
        </select>
      ) : (
        <ImeTextInput
          type="text"
          placeholder={placeholder}
          value={draft[key]}
          onValueChange={(value) => onChange({ ...draft, [key]: value })}
        />
      )}
    </label>
  )
  return (
    <div>
      <h3>{title}</h3>
      <div className="hard-condition-grid hard-condition-grid-primary">
        {primaryFields.map(renderField)}
      </div>
      <div className="hard-condition-grid hard-condition-grid-rules">
        {ruleFields.map(renderField)}
      </div>
    </div>
  )
}
