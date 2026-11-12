import { useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { Company, PageResult, Position, Prompt } from '../types'
import { notifyDataUpdated, onDataUpdated } from '../utils/dataEvents'
import { canManageBusiness, canManagePositions } from '../utils/authz'
import { readPageCache, writePageCache } from '../utils/pageCache'
import { ImeTextInput } from '../components/ImeTextInput'
import {
  EMPTY_HARD_STRUCT,
  HardStructDraft,
  HardStructInputs,
  ParsedPositionPayload,
  PROJECT_STATUS,
  hardStructToDraft,
  hardStructToPayload,
  nextDateString,
  parsedHardStructToDraft,
} from '../components/PositionHardConditions'

interface UserOption {
  id: number
  username: string
  display_name: string
  role: string
  status: string
}

type PositionPatch = Record<string, string | number | null | Record<string, string | string[]>>

const COMPANY_TYPES = ['开发中', '已签约', '历史客户']
const STANDARD_COMPANY_STATUSES = ['开发中', '已签约', '历史客户']
const POSITIONS_CACHE_KEY = 'positions:rows'
const COMPANIES_CACHE_KEY = 'positions:companies'
const USERS_CACHE_KEY = 'positions:users'
const COMPANY_STATUSES = ['刚录入', '对接KP', '有猎头需求客户', '费率价格条款谈判', '试单推荐简历', '签约完成（交付中）']

const EMPTY_COMPANY_DETAIL = {
  contactName: '',
  contactTitle: '',
  mobile: '',
  email: '',
  wechat: '',
  phone: '',
  companyType: '',
  companyStatus: '',
  website: '',
  address: '',
  benefits: '',
  products: '',
  scale: '',
  competitors: '',
  strengths: '',
  challenges: '',
  financing: '',
  foundedAt: '',
  intro: '',
  invoiceTitle: '',
  shortName: '',
}

const statusStyle = (s: string): React.CSSProperties => {
  if (s === '进行中') return { background: '#e8f1fd', color: '#1a56a7' }
  if (s === '已结束') return { background: '#f0f0f0', color: '#666' }
  return {}
}

function projectStatus(value: string) {
  const text = value || '未开始'
  return ({ 待开始: '未开始', 待启动: '未开始', 进展中: '进行中', Live: '进行中', live: '进行中' } as Record<string, string>)[text] || text
}

const EMPTY_FORM = {
  company_id: '', company: '', contact: '', title: '', headcount: 1, fee: '', priority: '',
  industry: '', func: '', city: '', address: '', guarantee_months: '',
  project_status: '未开始', start_date: '', end_date: '',
  salary_range: '', jd_raw: '',
}

const EMPTY_PROJECT_DETAIL = {
  contact: '',
  fee: '',
  priority: '',
  industry: '',
  function: '',
  city: '',
  address: '',
  guarantee_months: '',
  start_date: '',
  end_date: '',
}

function extraText(extra: Record<string, unknown>, key: string) {
  const value = extra[key]
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

function parseProjectDetail(raw: string) {
  let data: Record<string, unknown> = {}
  try {
    data = raw ? JSON.parse(raw) : {}
  } catch {
    data = {}
  }
  return Object.fromEntries(
    Object.keys(EMPTY_PROJECT_DETAIL).map((key) => {
      const value = data[key]
      return [key, typeof value === 'string' || typeof value === 'number' ? String(value) : '']
    }),
  ) as typeof EMPTY_PROJECT_DETAIL
}

export default function PositionsPage() {
  const canEdit = canManagePositions()
  const canEditCompany = canManageBusiness()
  const { positionId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const companyIdParam = searchParams.get('company_id') || ''
  const companyNameParam = searchParams.get('company_name') || ''
  const [positions, setPositions] = useState<Position[]>(() => readPageCache<Position[]>(POSITIONS_CACHE_KEY, []))
  const [companies, setCompanies] = useState<Company[]>(() => readPageCache<Company[]>(COMPANIES_CACHE_KEY, []))
  const [users, setUsers] = useState<UserOption[]>(() => readPageCache<UserOption[]>(USERS_CACHE_KEY, []))
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [parsingJd, setParsingJd] = useState(false)
  const [creatingPosition, setCreatingPosition] = useState(false)
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [createPromptDraft, setCreatePromptDraft] = useState('')
  const [formHardStruct, setFormHardStruct] = useState<HardStructDraft>({ ...EMPTY_HARD_STRUCT })
  // 详情编辑：公司详情 / 岗位详情共用当前岗位 id 定位
  const [editingId, setEditingId] = useState<number | null>(null)
  const [detailMode, setDetailMode] = useState<'company' | 'position' | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editCompanyId, setEditCompanyId] = useState('')
  const [editCompany, setEditCompany] = useState('')
  const [companyDetail, setCompanyDetail] = useState({ ...EMPTY_COMPANY_DETAIL })
  const [editSource, setEditSource] = useState('manual')
  const [editHeadcount, setEditHeadcount] = useState(1)
  const [editSalary, setEditSalary] = useState('')
  const [editProjectStatus, setEditProjectStatus] = useState('未开始')
  const [editConsultant, setEditConsultant] = useState('')
  const [editProject, setEditProject] = useState({ ...EMPTY_PROJECT_DETAIL })
  const [editHard, setEditHard] = useState('')
  const [editHardStruct, setEditHardStruct] = useState<HardStructDraft>({ ...EMPTY_HARD_STRUCT })
  const [editNice, setEditNice] = useState('')
  const [editNotes, setEditNotes] = useState('')
  const [editPrompt, setEditPrompt] = useState('')
  const [origPrompt, setOrigPrompt] = useState('')
  const [positionPromptHistory, setPositionPromptHistory] = useState<Prompt[]>([])
  const [showPositionPromptHistory, setShowPositionPromptHistory] = useState(false)
  const [savingRow, setSavingRow] = useState(false)
  // 数字列草稿（人才数量/推荐/面试/offer），失焦保存
  const [numDraft, setNumDraft] = useState<Record<string, number>>({})
  const positionSaveTimers = useRef<Record<number, number>>({})
  const pendingPositionPatches = useRef<Record<number, PositionPatch>>({})
  const [companyFilter, setCompanyFilter] = useState('全部')
  const [statusFilter, setStatusFilter] = useState('全部')
  const [openFilter, setOpenFilter] = useState<'company' | 'status' | null>(null)
  const [appliedCompanyParam, setAppliedCompanyParam] = useState('')

  const load = () => api.get<Position[]>('/api/positions').then((rows) => {
    writePageCache(POSITIONS_CACHE_KEY, rows)
    setPositions(rows)
  })
  const loadCompanies = () => api.get<PageResult<Company>>('/api/companies?limit=1000').then((rows) => {
    writePageCache(COMPANIES_CACHE_KEY, rows.items)
    setCompanies(rows.items)
  })
  const loadUsers = () => api.get<UserOption[]>('/api/auth/users').then((rows) => {
    writePageCache(USERS_CACHE_KEY, rows)
    setUsers(rows)
  })

  useEffect(() => {
    load()
    loadCompanies()
    loadUsers()
    const onFocus = () => loadUsers()
    window.addEventListener('focus', onFocus)
    window.addEventListener('hh-match-users-updated', onFocus)
    const unsubscribe = onDataUpdated(['positions', 'prompts'], load)
    const unsubscribeCompanies = onDataUpdated(['companies'], loadCompanies)
    return () => {
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('hh-match-users-updated', onFocus)
      unsubscribe()
      unsubscribeCompanies()
    }
  }, [])

  useEffect(() => () => {
    Object.values(positionSaveTimers.current).forEach((timer) => window.clearTimeout(timer))
  }, [])

  useEffect(() => {
    if (!editingId || detailMode !== 'position') return
    const unsubscribe = onDataUpdated(['prompts'], () => {
      loadPositionPrompt(editingId).catch((e) => setError(String((e as Error).message)))
    })
    return unsubscribe
  }, [editingId, detailMode])

  async function loadPositionPrompt(positionId: number) {
    const list = await api.get<Prompt[]>('/api/prompts?layer=position&include_history=true')
    const history = list.filter((pr) => pr.position_id === positionId)
    const active = history.find((pr) => pr.is_active) || null
    setPositionPromptHistory(history)
    setEditPrompt(active?.content || '')
    setOrigPrompt(active?.content || '')
  }

  useEffect(() => {
    if (!positionId || positions.length === 0 || editingId === Number(positionId)) return
    const target = positions.find((position) => position.id === Number(positionId))
    if (target) openDetail(target, 'position')
  }, [positionId, positions, editingId])

  function companyDisplayName(company: Company) {
    return company.legal_name || company.short_name || company.name
  }

  function companyOptionLabel(company: Company) {
    return [
      company.short_name || company.name,
      company.legal_name && company.legal_name !== (company.short_name || company.name) ? company.legal_name : '',
      company.city,
      company.industry,
      company.contact_display_name || company.contact_name,
    ].filter(Boolean).join(' / ')
  }

  function applyCompanyToForm(company: Company) {
    setForm((prev) => ({
      ...prev,
      company_id: String(company.id),
      company: companyDisplayName(company),
      contact: prev.contact || company.contact_display_name || company.contact_name || '',
      industry: prev.industry || company.industry || '',
      city: prev.city || company.city || '',
    }))
  }

  useEffect(() => {
    if (positionId || !canEdit) return
    const routeKey = `${companyIdParam}|${companyNameParam}`
    if (!routeKey || routeKey === '|' || appliedCompanyParam === routeKey) return
    let cancelled = false
    const applyRouteCompany = (target?: Company) => {
      if (cancelled) return
      setEditingId(null)
      setDetailMode(null)
      setShowForm(true)
      setForm((prev) => ({
        ...EMPTY_FORM,
        jd_raw: prev.jd_raw,
        company: companyNameParam || prev.company,
      }))
      setCreatePromptDraft('')
      setFormHardStruct({ ...EMPTY_HARD_STRUCT })
      if (target) applyCompanyToForm(target)
      setAppliedCompanyParam(routeKey)
    }
    const target = companyIdParam
      ? companies.find((company) => company.id === Number(companyIdParam))
      : companies.find((company) => companyDisplayName(company) === companyNameParam || company.short_name === companyNameParam)
    if (target) {
      applyRouteCompany(target)
      return () => {
        cancelled = true
      }
    }
    if (companyIdParam) {
      api
        .get<Company>(`/api/companies/${companyIdParam}`)
        .then(applyRouteCompany)
        .catch((e) => {
          if (cancelled) return
          setError(String((e as Error).message || `客户 ID ${companyIdParam} 不存在或当前账号不可见`))
          setAppliedCompanyParam(routeKey)
        })
      return () => {
        cancelled = true
      }
    }
    applyRouteCompany()
    return () => {
      cancelled = true
    }
  }, [positionId, canEdit, companyIdParam, companyNameParam, companies, appliedCompanyParam])

  const REQUIRED: Array<[keyof typeof EMPTY_FORM, string]> = [
    ['title', '项目名称'], ['priority', '项目优先级'],
    ['industry', '行业'], ['func', '职能'], ['city', '城市'], ['start_date', '开始日期'],
  ]

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setError('')
    setMessage('')
    try {
      const result = await api.upload<{ count: number; created?: Array<{ title: string }>; skipped?: Array<{ title: string; reason: string }>; errors: string[] }>('/api/positions/upload', file)
      const imported = result.created?.map((item) => item.title).filter(Boolean).join('、')
      const skippedGroups = (result.skipped || []).reduce<Record<string, string[]>>((groups, item) => {
        const reason = item.reason || '岗位已存在，无需重复导入'
        if (!groups[reason]) groups[reason] = []
        if (item.title) groups[reason].push(item.title)
        return groups
      }, {})
      const skipped = Object.entries(skippedGroups)
        .map(([reason, titles]) => `${titles.join('、')}${reason}`)
        .filter(Boolean)
        .join('；')
      const suffix = result.errors?.length ? `；需检查：${result.errors.join('；')}` : ''
      const skippedSuffix = skipped ? `；已跳过：${skipped}` : ''
      setMessage(`已导入 ${result.count} 个岗位${imported ? `：${imported}` : ''}${skippedSuffix}${suffix}`)
      await load()
      notifyDataUpdated('positions')
      notifyDataUpdated('companies')
    } catch (err) {
      setError(String((err as Error).message))
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  async function createPosition() {
    if (creatingPosition) return
    const missing = REQUIRED.filter(([k]) => !String(form[k]).trim()).map(([, label]) => label)
    if (!form.company_id && !form.company.trim()) missing.unshift('客户公司')
    if (missing.length) {
      setError(`必填字段未填：${missing.join('、')}`)
      return
    }
    if (form.end_date && form.end_date <= form.start_date) {
      setError('结束日期必须晚于开始日期')
      return
    }
    setError('')
    setMessage('')
    setCreatingPosition(true)
    try {
      const created = await api.post<{ id: number; company_id: number | null }>('/api/positions', {
        title: form.title.trim(),
        company_id: form.company_id ? Number(form.company_id) : null,
        company: form.company.trim(),
        jd_raw: form.jd_raw,
        headcount: form.headcount,
        salary_range: form.salary_range,
        project_status: form.project_status,
        project: {
          contact: form.contact, fee: form.fee, priority: form.priority,
          industry: form.industry, function: form.func, city: form.city,
          address: form.address, guarantee_months: form.guarantee_months,
          start_date: form.start_date, end_date: form.end_date,
        },
        hard_struct: hardStructToPayload(formHardStruct),
      })
      if (createPromptDraft.trim()) {
        await api.post('/api/prompts', {
          layer: 'position',
          position_id: created.id,
          content: createPromptDraft.trim(),
        })
      }
      setForm({ ...EMPTY_FORM })
      setCreatePromptDraft('')
      setFormHardStruct({ ...EMPTY_HARD_STRUCT })
      setShowForm(false)
      setMessage(`岗位「${form.title.trim()}」已创建`)
      setSearchParams({})
      setAppliedCompanyParam('')
      await Promise.all([load(), loadCompanies()])
      notifyDataUpdated('positions')
      notifyDataUpdated('companies')
      if (createPromptDraft.trim()) notifyDataUpdated('prompts')
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setCreatingPosition(false)
    }
  }

  async function parseJdIntoForm() {
    const text = form.jd_raw.trim()
    if (!text) {
      setError('请先填写职位JD原文')
      return
    }
    setParsingJd(true)
    setError('')
    setMessage('')
    try {
      const result = await api.post<{ position: ParsedPositionPayload; count: number; unstructured_summary?: string; warnings?: string[] }>('/api/positions/parse', { text })
      const parsed = result.position || {}
      const project = parsed.project || {}
      const hardStruct = parsed.hard_struct || {}
      setForm((prev) => {
        const startDate = project.start_date || prev.start_date
        const endDate = project.end_date || prev.end_date
        return {
          ...prev,
          company: prev.company_id ? prev.company : parsed.company || prev.company,
          title: parsed.title || prev.title,
          headcount: parsed.headcount || prev.headcount,
          salary_range: parsed.salary_range || prev.salary_range,
          project_status: parsed.project_status || prev.project_status,
          contact: project.contact || prev.contact,
          fee: project.fee || prev.fee,
          priority: project.priority || prev.priority,
          industry: project.industry || prev.industry,
          func: project.function || prev.func,
          city: project.city || prev.city,
          address: project.address || prev.address,
          guarantee_months: project.guarantee_months || prev.guarantee_months,
          start_date: startDate,
          end_date: startDate && endDate && endDate <= startDate ? '' : endDate,
          jd_raw: prev.jd_raw,
        }
      })
      setFormHardStruct((prev) => parsedHardStructToDraft(hardStruct, prev))
      const reminders = [result.unstructured_summary || parsed.unstructured_summary, ...(result.warnings || [])].filter(Boolean)
      const reminderText = reminders.length ? `；需人工关注：${reminders.join('；')}` : ''
      setMessage(`已解析并填入表单${result.count > 1 ? `，检测到 ${result.count} 个岗位，已使用第 1 个` : ''}${reminderText}`)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setParsingJd(false)
    }
  }

  function mergePositionPatch(base: PositionPatch = {}, patch: PositionPatch) {
    const next = { ...base, ...patch }
    if (base.project && patch.project && typeof base.project === 'object' && typeof patch.project === 'object') {
      next.project = { ...base.project, ...patch.project }
    }
    return next
  }

  function applyPositionPatch(positionId: number, patch: PositionPatch) {
    setPositions((current) => current.map((x) => {
      if (x.id !== positionId) return x
      const next = { ...x, ...patch }
      if (patch.project && typeof patch.project === 'object') {
        const mergedProject = { ...parseProjectDetail(x.project_json || ''), ...patch.project }
        next.project_json = JSON.stringify(mergedProject)
      }
      return next
    }))
  }

  function queuePositionUpdate(positionId: number, patch: PositionPatch) {
    pendingPositionPatches.current[positionId] = mergePositionPatch(
      pendingPositionPatches.current[positionId],
      patch,
    )
    const timer = positionSaveTimers.current[positionId]
    if (timer) window.clearTimeout(timer)
    positionSaveTimers.current[positionId] = window.setTimeout(() => {
      const position = positions.find((item) => item.id === positionId)
      if (position) flushPositionUpdate(position)
    }, 600)
  }

  async function flushPositionUpdate(p: Position) {
    const timer = positionSaveTimers.current[p.id]
    if (timer) {
      window.clearTimeout(timer)
      delete positionSaveTimers.current[p.id]
    }
    const patch = pendingPositionPatches.current[p.id]
    if (!patch) return
    delete pendingPositionPatches.current[p.id]
    try {
      await api.put(`/api/positions/${p.id}`, patch)
      notifyDataUpdated('positions')
      if (
        Object.prototype.hasOwnProperty.call(patch, 'company')
        || Object.prototype.hasOwnProperty.call(patch, 'company_id')
      ) {
        notifyDataUpdated('companies')
      }
    } catch (e) {
      setError(String((e as Error).message))
      await load()
    }
  }

  async function patchPosition(p: Position, patch: PositionPatch, options: { debounce?: boolean } = {}) {
    setError('')
    applyPositionPatch(p.id, patch)
    if (options.debounce) {
      queuePositionUpdate(p.id, patch)
      return
    }
    try {
      await api.put(`/api/positions/${p.id}`, patch)
      notifyDataUpdated('positions')
      if (
        Object.prototype.hasOwnProperty.call(patch, 'company')
        || Object.prototype.hasOwnProperty.call(patch, 'company_id')
      ) {
        notifyDataUpdated('companies')
      }
    } catch (e) {
      setError(String((e as Error).message))
      await load()
    }
  }

  async function deletePosition(p: Position) {
    if (!window.confirm(`确认删除岗位「${p.title}」吗？该岗位相关匹配报告、客户推荐报告、AI 会话和进程记录都会一起删除，并记录操作日志。`)) return
    setError('')
    setMessage('')
    try {
      await api.delete(`/api/positions/${p.id}`)
      setMessage(`岗位「${p.title}」已删除`)
      await load()
      notifyDataUpdated('positions')
      notifyDataUpdated('companies')
      notifyDataUpdated('projects')
      notifyDataUpdated('prompts')
      notifyDataUpdated('tracking')
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  async function openDetail(p: Position, mode: 'company' | 'position') {
    if (editingId === p.id && detailMode === mode) {
      setEditingId(null)
      setDetailMode(null)
      return
    }
    setEditingId(p.id)
    setDetailMode(mode)
    setEditTitle(p.title)
    setEditCompanyId(p.company_id ? String(p.company_id) : '')
    setEditCompany(p.company || '')
    setCompanyDetail({
      ...EMPTY_COMPANY_DETAIL,
      shortName: '',
      intro: p.consultant_notes || '',
    })
    if (mode === 'company' && p.company_id) {
      try {
        const company = await api.get<Company>(`/api/companies/${p.company_id}`)
        setEditCompany(company.legal_name || '')
        setCompanyDetail({
          ...EMPTY_COMPANY_DETAIL,
          contactName: company.contact_name || '',
          companyType: company.status || '',
          companyStatus: extraText(company.extra || {}, 'companyStatus'),
          website: extraText(company.extra || {}, 'website'),
          address: extraText(company.extra || {}, 'address'),
          benefits: extraText(company.extra || {}, 'benefits'),
          products: extraText(company.extra || {}, 'products'),
          scale: extraText(company.extra || {}, 'scale'),
          competitors: extraText(company.extra || {}, 'competitors'),
          strengths: extraText(company.extra || {}, 'strengths'),
          challenges: extraText(company.extra || {}, 'challenges'),
          financing: extraText(company.extra || {}, 'financing'),
          foundedAt: extraText(company.extra || {}, 'foundedAt'),
          intro: company.notes || extraText(company.extra || {}, 'intro'),
          invoiceTitle: extraText(company.extra || {}, 'invoiceTitle'),
          shortName: company.short_name || '',
          contactTitle: extraText(company.extra || {}, 'contactTitle'),
          mobile: extraText(company.extra || {}, 'mobile'),
          email: extraText(company.extra || {}, 'email'),
          wechat: extraText(company.extra || {}, 'wechat'),
          phone: extraText(company.extra || {}, 'phone'),
        })
      } catch (e) {
        setError(String((e as Error).message))
      }
    }
    setEditSource('manual')
    setEditHeadcount(p.headcount || 1)
    setEditSalary(p.salary_range || '')
    setEditProjectStatus(projectStatus(p.project_status))
    setEditConsultant(p.consultant || '')
    setEditProject(parseProjectDetail(p.project_json || ''))
    setEditHard(p.hard_requirements || '')
    setEditHardStruct(hardStructToDraft(p.hard_struct_json || ''))
    setEditNice(p.nice_to_have || '')
    setEditNotes(p.consultant_notes || '')
    setEditPrompt('')
    setOrigPrompt('')
    setPositionPromptHistory([])
    setShowPositionPromptHistory(false)
    if (mode === 'position') await loadPositionPrompt(p.id)
  }

  async function saveCompany(p: Position) {
    setSavingRow(true)
    setError('')
    setMessage('')
    try {
      if (p.company_id) {
        const payload: Record<string, unknown> = {
          legal_name: editCompany.trim(),
          short_name: companyDetail.shortName.trim(),
          contact_name: companyDetail.contactName.trim(),
          notes: companyDetail.intro.trim(),
          extra: {
            companyStatus: companyDetail.companyStatus,
            contactTitle: companyDetail.contactTitle.trim(),
            mobile: companyDetail.mobile.trim(),
            email: companyDetail.email.trim(),
            wechat: companyDetail.wechat.trim(),
            phone: companyDetail.phone.trim(),
            website: companyDetail.website.trim(),
            address: companyDetail.address.trim(),
            benefits: companyDetail.benefits.trim(),
            products: companyDetail.products.trim(),
            scale: companyDetail.scale.trim(),
            competitors: companyDetail.competitors.trim(),
            strengths: companyDetail.strengths.trim(),
            challenges: companyDetail.challenges.trim(),
            financing: companyDetail.financing.trim(),
            foundedAt: companyDetail.foundedAt.trim(),
            intro: companyDetail.intro.trim(),
            invoiceTitle: companyDetail.invoiceTitle.trim(),
          },
        }
        if (STANDARD_COMPANY_STATUSES.includes(companyDetail.companyType)) {
          payload.status = companyDetail.companyType
        }
        await api.put(`/api/companies/${p.company_id}`, payload)
      } else if (editCompany !== (p.company || '')) {
        await api.put(`/api/positions/${p.id}`, { company: editCompany.trim() })
      }
      if (p.company_id || editCompany !== (p.company || '')) {
        await load()
        notifyDataUpdated('positions')
        notifyDataUpdated('companies')
      }
      setMessage(`公司「${editCompany || p.company || '未命名公司'}」信息已保存`)
      setEditingId(null)
      setDetailMode(null)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setSavingRow(false)
    }
  }

  async function saveRow(p: Position) {
    setSavingRow(true)
    setError('')
    setMessage('')
    try {
      const jobs: Promise<unknown>[] = []
      const patch: PositionPatch = {}
      if (editTitle.trim() && editTitle !== p.title) patch.title = editTitle.trim()
      if (editCompanyId !== (p.company_id ? String(p.company_id) : '')) {
        patch.company_id = editCompanyId ? Number(editCompanyId) : null
      } else if (editCompany !== (p.company || '')) {
        patch.company = editCompany.trim()
      }
      if (editSource !== p.source) patch.source = editSource
      if (editHeadcount !== (p.headcount || 1)) patch.headcount = editHeadcount
      if (editSalary !== (p.salary_range || '')) patch.salary_range = editSalary.trim()
      if (editProjectStatus !== projectStatus(p.project_status)) patch.project_status = editProjectStatus
      if (editConsultant !== (p.consultant || '')) patch.consultant = editConsultant
      const currentProject = parseProjectDetail(p.project_json || '')
      const projectPayload = Object.fromEntries(
        Object.entries(editProject).map(([key, value]) => [key, value.trim()]),
      ) as typeof EMPTY_PROJECT_DETAIL
      if (JSON.stringify(projectPayload) !== JSON.stringify(currentProject)) patch.project = projectPayload
      if (editHard !== (p.hard_requirements || '')) patch.hard_requirements = editHard
      const hardStruct = hardStructToPayload(editHardStruct)
      if (JSON.stringify(hardStruct) !== JSON.stringify(hardStructToPayload(hardStructToDraft(p.hard_struct_json || '')))) {
        patch.hard_struct = hardStruct
      }
      if (editNice !== (p.nice_to_have || '')) patch.nice_to_have = editNice
      if (editNotes !== (p.consultant_notes || '')) patch.consultant_notes = editNotes
      if (Object.keys(patch).length > 0) {
        jobs.push(api.put(`/api/positions/${p.id}`, patch))
      }
      // 岗位提示词有变化且非空才保存（保存即新版本）
      if (editPrompt.trim() && editPrompt !== origPrompt) {
        jobs.push(
          api.post('/api/prompts', { layer: 'position', position_id: p.id, content: editPrompt }),
        )
      }
      if (jobs.length === 0) {
        setMessage('没有改动')
      } else {
        await Promise.all(jobs)
        setMessage(`岗位「${p.title}」已保存`)
        await load()
        if (editPrompt.trim() && editPrompt !== origPrompt) await loadPositionPrompt(p.id)
        if (Object.keys(patch).length > 0) notifyDataUpdated('positions')
        if (
          Object.prototype.hasOwnProperty.call(patch, 'company')
          || Object.prototype.hasOwnProperty.call(patch, 'company_id')
        ) {
          notifyDataUpdated('companies')
        }
        if (editPrompt.trim() && editPrompt !== origPrompt) notifyDataUpdated('prompts')
      }
      setEditingId(null)
      setDetailMode(null)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setSavingRow(false)
    }
  }

  // 数字列：本地草稿 + 失焦保存
  const numCell = (p: Position, field: 'n_talents' | 'n_recommend' | 'n_interview' | 'n_offer') => {
    const key = `${p.id}:${field}`
    return (
      <input
        type="number"
        min={0}
        style={{ width: 56, padding: '4px 6px', textAlign: 'center', border: 0, background: 'transparent' }}
        value={numDraft[key] ?? p[field] ?? 0}
        onChange={(e) => setNumDraft({ ...numDraft, [key]: Math.max(0, Number(e.target.value) || 0) })}
        onBlur={() => {
          const val = numDraft[key]
          if (val !== undefined && val !== p[field]) patchPosition(p, { [field]: val })
        }}
        disabled={!canEdit}
      />
    )
  }

  const projectCity = (p: Position) => {
    return parseProjectDetail(p.project_json || '').city
  }

  const formInput = (key: keyof typeof EMPTY_FORM, placeholder: string, width: number | string = '100%') => (
    <ImeTextInput
      type="text"
      placeholder={placeholder}
      style={{ width }}
      value={String(form[key])}
      onValueChange={(value) => setForm({ ...form, [key]: value })}
    />
  )

  const companyOptions = Array.from(new Set(positions.map((p) => p.company).filter(Boolean))) as string[]
  const statusOptions = PROJECT_STATUS
  const visiblePositions = positions.filter((p) => {
    const companyOk = companyFilter === '全部' || p.company === companyFilter
    const statusOk = statusFilter === '全部' || projectStatus(p.project_status) === statusFilter
    return companyOk && statusOk
  })
  const groupedPositions = Array.from(
    visiblePositions.reduce((groups, position) => {
      const company = position.company || '未指定公司'
      const items = groups.get(company) || []
      items.push(position)
      groups.set(company, items)
      return groups
    }, new Map<string, Position[]>()),
  )
  const editingPosition = positions.find((p) => p.id === editingId) || null

  return (
    <div>
      {editingPosition && (
        <div className="page-heading-row">
          <div className="page-heading-left">
            <button className="back-icon-button" onClick={() => { setEditingId(null); setDetailMode(null) }} aria-label="返回" title="返回">
              ←
            </button>
            <div className="folder-page-title">
              <h2>{detailMode === 'company' ? '公司详情' : '岗位详情'}</h2>
            </div>
          </div>
        </div>
      )}
      {!editingPosition && (
        <>
          <div className="folder-page-title">
            <h2>岗位管理</h2>
          </div>
          <div className="card">
        <p className="muted" style={{ marginBottom: 12 }}>
          岗位可手动新增，也可上传 PDF/DOCX/XLSX/TXT 文件自动填入；标准 XLSX 会按表格列直接解析，其他文档会调用模型解析。保存后会进入岗位管理和项目管理，再到 <Link to="/match">发起匹配</Link> 中选择项目并进入匹配流程。
        </p>
        {canEdit && (
          <div className="row">
            <button
              onClick={() => {
                if (!showForm) {
                  setSearchParams({})
                  setForm({ ...EMPTY_FORM })
                  setCreatePromptDraft('')
                  setFormHardStruct({ ...EMPTY_HARD_STRUCT })
                }
                setShowForm(!showForm)
              }}
            >
              {showForm ? '收起表单' : '＋ 新增岗位'}
            </button>
            <label className="row" style={{ marginBottom: 0 }}>
              <span className="muted">上传岗位文件（PDF/DOCX/XLSX/TXT）：</span>
              <input type="file" accept=".pdf,.docx,.xlsx,.txt,.md" onChange={handleUpload} disabled={uploading} />
            </label>
          </div>
        )}
        {uploading && <p className="muted">岗位文件解析导入中，可能需要几十秒…</p>}
        {message && <p className="muted">{message}</p>}
        {error && <p className="error">{error}</p>}
        {canEdit && showForm && (
          <div style={{ marginTop: 12 }}>
            <section className="position-form-section position-prompt-section">
              <h3>岗位提示词</h3>
              <textarea
                className="customer-note-input"
                placeholder="填写该岗位的必须项、优先项、排除项、客户没明说但匹配时必须考虑的规则。保存后会作为三层提示词中的岗位提示词，仅作用到该岗位匹配。"
                value={createPromptDraft}
                onChange={(e) => setCreatePromptDraft(e.target.value)}
              />
            </section>
            <div className="position-form-grid">
              <section className="position-form-section">
                <h3>项目信息</h3>
                <div className="form-field-grid">
                  <label className="form-field company-picker-field">
                    <span className="muted">客户公司 *</span>
                    <select
                      value={form.company_id}
                      onChange={(e) => {
                        const selectedCompany = companies.find((company) => company.id === Number(e.target.value))
                        if (selectedCompany) {
                          applyCompanyToForm(selectedCompany)
                        } else {
                          setForm({ ...form, company_id: '', company: '' })
                        }
                      }}
                    >
                      <option value="">新建客户 / 手动输入</option>
                      {companies.map((company) => (
                        <option key={company.id} value={company.id}>
                          {companyOptionLabel(company)}
                        </option>
                      ))}
                    </select>
                    <ImeTextInput
                      type="text"
                      placeholder={form.company_id ? '已选择已有客户' : '请与营业执照完全一致'}
                      value={form.company}
                      onValueChange={(value) => setForm({ ...form, company: value, company_id: '' })}
                      disabled={Boolean(form.company_id)}
                    />
                  </label>
                  <label className="form-field"><span className="muted">主要联系人</span>{formInput('contact', '主要联系人')}</label>
                  <label className="form-field"><span className="muted">项目名称 *</span>{formInput('title', '项目名称 *')}</label>
                  <label className="form-field">
                    <span className="muted">招聘数量</span>
                    <input
                      type="number"
                      min={1}
                      value={form.headcount}
                      onChange={(e) => setForm({ ...form, headcount: Math.max(1, Number(e.target.value) || 1) })}
                    />
                  </label>
                  <label className="form-field"><span className="muted">项目费用</span>{formInput('fee', '项目费用')}</label>
                  <label className="form-field">
                    <span className="muted">项目优先级 *</span>
                    <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                      <option value="">请选择</option>
                      <option value="高">高</option>
                      <option value="中">中</option>
                      <option value="低">低</option>
                    </select>
                  </label>
                </div>
              </section>

              <section className="position-form-section">
                <h3>岗位与交付</h3>
                <div className="form-field-grid">
                  <label className="form-field"><span className="muted">行业 *</span>{formInput('industry', '行业 *')}</label>
                  <label className="form-field"><span className="muted">职能 *</span>{formInput('func', '如 后端研发')}</label>
                  <label className="form-field"><span className="muted">城市 *</span>{formInput('city', '城市 *')}</label>
                  <label className="form-field"><span className="muted">月薪</span>{formInput('salary_range', '如 50k-70k')}</label>
                  <label className="form-field">
                    <span className="muted">保证期</span>
                    <select value={form.guarantee_months} onChange={(e) => setForm({ ...form, guarantee_months: e.target.value })}>
                      <option value="">请选择</option>
                      {[1, 2, 3, 4, 5, 6, 9, 12].map((n) => (
                        <option key={n} value={`${n}个月`}>{n} 个月</option>
                      ))}
                    </select>
                  </label>
                  <label className="form-field">
                    <span className="muted">项目状态 *</span>
                    <select value={form.project_status} onChange={(e) => setForm({ ...form, project_status: e.target.value })}>
                      {PROJECT_STATUS.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                  </label>
                  <label className="form-field">
                    <span className="muted">开始日期 *</span>
                    <input
                      type="date"
                      value={form.start_date}
                      onChange={(e) => {
                        const nextStart = e.target.value
                        setForm({
                          ...form,
                          start_date: nextStart,
                          end_date: form.end_date && form.end_date <= nextStart ? '' : form.end_date,
                        })
                      }}
                    />
                  </label>
                  <label className="form-field">
                    <span className="muted">结束日期</span>
                    <input
                      type="date"
                      min={nextDateString(form.start_date)}
                      value={form.end_date}
                      onChange={(e) => setForm({ ...form, end_date: e.target.value })}
                    />
                  </label>
                </div>
              </section>

              <section className="position-form-section position-hard-condition-section">
                <HardStructInputs draft={formHardStruct} onChange={setFormHardStruct} />
              </section>
            </div>
            <div className="jd-parse-toolbar">
              <span className="muted">职位JD原文</span>
              <button onClick={parseJdIntoForm} disabled={parsingJd || !form.jd_raw.trim()}>
                {parsingJd ? '解析中…' : '解析并填入'}
              </button>
            </div>
            <textarea
              placeholder="职位JD原文（选填；不填则由上方项目要求字段自动生成岗位要求用于匹配）"
              style={{ minHeight: 80 }}
              value={form.jd_raw}
              onChange={(e) => setForm({ ...form, jd_raw: e.target.value })}
            />
            <div className="row" style={{ marginTop: 8 }}>
              <button onClick={createPosition} disabled={creatingPosition}>
                {creatingPosition ? '保存中…' : '保存岗位'}
              </button>
              <button className="secondary" onClick={() => { setShowForm(false); setForm({ ...EMPTY_FORM }); setCreatePromptDraft(''); setFormHardStruct({ ...EMPTY_HARD_STRUCT }); setParsingJd(false); setSearchParams({}); setAppliedCompanyParam('') }}>取消</button>
            </div>
          </div>
        )}
          </div>
        </>
      )}

      <div className="plain-panel">
        {!editingPosition && (
          <div className="filter-block">
            <div className="filter-dropdown-bar">
              <div className="filter-dropdown">
                <button
                  className={companyFilter === '全部' ? 'filter-chip' : 'filter-chip active'}
                  onClick={() => setOpenFilter(openFilter === 'company' ? null : 'company')}
                >
                  ▾ 客户{companyFilter !== '全部' ? `：${companyFilter}` : ''}
                </button>
                {openFilter === 'company' && (
                  <div className="filter-dropdown-menu">
                    {['全部', ...companyOptions].map((company) => (
                      <button
                        key={company}
                        className={companyFilter === company ? 'active' : ''}
                        onClick={() => {
                          setCompanyFilter(company)
                          setOpenFilter(null)
                        }}
                      >
                        {company}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="filter-dropdown">
                <button
                  className={statusFilter === '全部' ? 'filter-chip' : 'filter-chip active'}
                  onClick={() => setOpenFilter(openFilter === 'status' ? null : 'status')}
                >
                  ▾ 项目状态{statusFilter !== '全部' ? `：${statusFilter}` : ''}
                </button>
                {openFilter === 'status' && (
                  <div className="filter-dropdown-menu">
                    {['全部', ...statusOptions].map((status) => (
                      <button
                        key={status}
                        className={statusFilter === status ? 'active' : ''}
                        onClick={() => {
                          setStatusFilter(status)
                          setOpenFilter(null)
                        }}
                      >
                        {status}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {editingPosition ? (
          <div className="position-detail">
            {detailMode === 'company' ? (
              <>
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <div>
                    <h3 style={{ margin: 0 }}>{editCompany || '未填写公司'} · 公司详情</h3>
                    <p className="muted">维护共享客户资料，保存后会通过后端同步到统一数据源。</p>
                  </div>
                </div>
                <div className="position-detail-grid">
                  <label className="form-field"><span className="muted">营业执照名称</span><ImeTextInput type="text" value={editCompany} onValueChange={setEditCompany} disabled={editingPosition.company_id ? !canEditCompany : !canEdit} /></label>
                  <label className="form-field"><span className="muted">公司简称</span><ImeTextInput type="text" value={companyDetail.shortName} onValueChange={(value) => setCompanyDetail({ ...companyDetail, shortName: value })} disabled={!canEditCompany} /></label>
                  <label className="form-field">
                    <span className="muted">公司类型</span>
                    <select value={companyDetail.companyType} onChange={(e) => setCompanyDetail({ ...companyDetail, companyType: e.target.value })} disabled={!canEditCompany}>
                      <option value="">请选择</option>
                      {COMPANY_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
                    </select>
                  </label>
                  <label className="form-field">
                    <span className="muted">客户状态</span>
                    <select value={companyDetail.companyStatus} onChange={(e) => setCompanyDetail({ ...companyDetail, companyStatus: e.target.value })} disabled={!canEditCompany}>
                      <option value="">请选择</option>
                      {COMPANY_STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}
                    </select>
                  </label>
                  <label className="form-field"><span className="muted">联系人姓名</span><ImeTextInput type="text" value={companyDetail.contactName} onValueChange={(value) => setCompanyDetail({ ...companyDetail, contactName: value })} disabled={!canEditCompany} /></label>
                  <label className="form-field"><span className="muted">联系人职位</span><ImeTextInput type="text" value={companyDetail.contactTitle} onValueChange={(value) => setCompanyDetail({ ...companyDetail, contactTitle: value })} disabled={!canEditCompany} /></label>
                  <label className="form-field"><span className="muted">手机</span><ImeTextInput type="text" value={companyDetail.mobile} onValueChange={(value) => setCompanyDetail({ ...companyDetail, mobile: value })} disabled={!canEditCompany} /></label>
                  <label className="form-field"><span className="muted">邮箱</span><ImeTextInput type="text" value={companyDetail.email} onValueChange={(value) => setCompanyDetail({ ...companyDetail, email: value })} disabled={!canEditCompany} /></label>
                  <label className="form-field"><span className="muted">微信</span><ImeTextInput type="text" value={companyDetail.wechat} onValueChange={(value) => setCompanyDetail({ ...companyDetail, wechat: value })} disabled={!canEditCompany} /></label>
                  <label className="form-field"><span className="muted">电话</span><ImeTextInput type="text" value={companyDetail.phone} onValueChange={(value) => setCompanyDetail({ ...companyDetail, phone: value })} disabled={!canEditCompany} /></label>
                  <label className="form-field"><span className="muted">网址</span><ImeTextInput type="text" value={companyDetail.website} onValueChange={(value) => setCompanyDetail({ ...companyDetail, website: value })} disabled={!canEditCompany} /></label>
                  <label className="form-field"><span className="muted">地址</span><ImeTextInput type="text" value={companyDetail.address} onValueChange={(value) => setCompanyDetail({ ...companyDetail, address: value })} disabled={!canEditCompany} /></label>
                </div>
                <h3>公司业务与吸引力</h3>
                <textarea placeholder="主营业务、产品线、行业排名…" value={companyDetail.products} onChange={(e) => setCompanyDetail({ ...companyDetail, products: e.target.value })} disabled={!canEditCompany} />
                <textarea placeholder="薪资结构、福利、车补、餐补、交通、公积金社保等…" value={companyDetail.benefits} onChange={(e) => setCompanyDetail({ ...companyDetail, benefits: e.target.value })} disabled={!canEditCompany} />
                <textarea placeholder="人员规模、人员来源、学历结构…" value={companyDetail.scale} onChange={(e) => setCompanyDetail({ ...companyDetail, scale: e.target.value })} disabled={!canEditCompany} />
                <textarea placeholder="公司优势和吸引人才的亮点…" value={companyDetail.strengths} onChange={(e) => setCompanyDetail({ ...companyDetail, strengths: e.target.value })} disabled={!canEditCompany} />
                <textarea placeholder="公司或产品劣势、目前挑战…" value={companyDetail.challenges} onChange={(e) => setCompanyDetail({ ...companyDetail, challenges: e.target.value })} disabled={!canEditCompany} />
                <textarea placeholder="主要竞争对手…" value={companyDetail.competitors} onChange={(e) => setCompanyDetail({ ...companyDetail, competitors: e.target.value })} disabled={!canEditCompany} />
                <textarea placeholder="融资情况、上市市值、成立时间、客户简介…" value={companyDetail.intro} onChange={(e) => setCompanyDetail({ ...companyDetail, intro: e.target.value })} disabled={!canEditCompany} />
                {(canEditCompany || (!editingPosition.company_id && canEdit)) && (
                  <div className="row" style={{ marginTop: 12 }}>
                    <button onClick={() => saveCompany(editingPosition)} disabled={savingRow}>{savingRow ? '保存中…' : '保存公司详情'}</button>
                    <button className="secondary" onClick={() => { setEditingId(null); setDetailMode(null) }}>取消</button>
                  </div>
                )}
              </>
            ) : (
              <>
                <section className="position-form-section position-prompt-section">
                  <h3>岗位提示词</h3>
                  <textarea
                    className="customer-note-input"
                    placeholder="填写该岗位的必须项、优先项、排除项、客户没明说但匹配时必须考虑的规则。保存后会作为三层提示词中的岗位提示词，仅作用到该岗位匹配。"
                    value={editPrompt}
                    onChange={(e) => setEditPrompt(e.target.value)}
                    disabled={!canEdit}
                  />
                  <div className="row" style={{ marginTop: 10 }}>
                    <button
                      className="secondary"
                      onClick={() => setShowPositionPromptHistory((value) => !value)}
                    >
                      历史版本
                    </button>
                  </div>
                  {showPositionPromptHistory && (
                    <div className="prompt-history-panel">
                      <h4>岗位提示词历史版本</h4>
                      {positionPromptHistory.map((prompt) => (
                        <div key={prompt.id} className="prompt-history-item">
                          <strong>v{prompt.version}{prompt.is_active ? ' · 当前生效' : ''}</strong>
                          <span className="muted">{prompt.created_at}</span>
                          <p>{prompt.content}</p>
                        </div>
                      ))}
                      {positionPromptHistory.length === 0 && <p className="muted">暂无历史版本。</p>}
                    </div>
                  )}
                </section>
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <div>
                    <h3 style={{ margin: 0 }}>{editTitle || '未填写岗位'} · 岗位详情</h3>
                  </div>
                </div>
                <div className="position-detail-grid">
                  <label className="form-field"><span className="muted">岗位名称</span><ImeTextInput type="text" value={editTitle} onValueChange={setEditTitle} /></label>
                  <label className="form-field company-picker-field">
                    <span className="muted">客户公司</span>
                    <select
                      value={editCompanyId}
                      onChange={(e) => {
                        const selectedCompany = companies.find((company) => company.id === Number(e.target.value))
                        if (selectedCompany) {
                          setEditCompanyId(String(selectedCompany.id))
                          setEditCompany(companyDisplayName(selectedCompany))
                        } else {
                          setEditCompanyId('')
                          setEditCompany('')
                        }
                      }}
                    >
                      <option value="">新建客户 / 手动输入</option>
                      {companies.map((company) => (
                        <option key={company.id} value={company.id}>
                          {companyOptionLabel(company)}
                        </option>
                      ))}
                    </select>
                    <ImeTextInput
                      type="text"
                      placeholder={editCompanyId ? '已选择已有客户' : '请与营业执照完全一致'}
                      value={editCompany}
                      onValueChange={(value) => {
                        setEditCompany(value)
                        setEditCompanyId('')
                      }}
                      disabled={Boolean(editCompanyId)}
                    />
                  </label>
                  <label className="form-field">
                    <span className="muted">来源</span>
                    <select value={editSource} onChange={(e) => setEditSource(e.target.value)}>
                      <option value="manual">手动</option>
                    </select>
                  </label>
                  <label className="form-field">
                    <span className="muted">项目状态</span>
                    <select value={editProjectStatus} onChange={(e) => setEditProjectStatus(e.target.value)}>
                      {PROJECT_STATUS.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </label>
                  <label className="form-field"><span className="muted">招聘数量</span><input type="number" min={1} value={editHeadcount} onChange={(e) => setEditHeadcount(Math.max(1, Number(e.target.value) || 1))} /></label>
                  <label className="form-field"><span className="muted">月薪范围</span><ImeTextInput type="text" placeholder="如 50k-70k / 面议" value={editSalary} onValueChange={setEditSalary} /></label>
                  <label className="form-field">
                    <span className="muted">顾问</span>
                    <select value={editConsultant} onChange={(e) => setEditConsultant(e.target.value)}>
                      <option value="">未指定</option>
                      {editConsultant && !users.some((u) => u.display_name === editConsultant) && (
                        <option value={editConsultant}>{editConsultant}</option>
                      )}
                      {users.map((u) => <option key={u.id} value={u.display_name}>{u.display_name}</option>)}
                    </select>
                  </label>
                </div>
                <h3>项目信息</h3>
                <div className="position-detail-grid">
                  <label className="form-field"><span className="muted">主要联系人</span><ImeTextInput type="text" value={editProject.contact} onValueChange={(value) => setEditProject({ ...editProject, contact: value })} /></label>
                  <label className="form-field"><span className="muted">项目费用</span><ImeTextInput type="text" value={editProject.fee} onValueChange={(value) => setEditProject({ ...editProject, fee: value })} /></label>
                  <label className="form-field">
                    <span className="muted">项目优先级</span>
                    <select value={editProject.priority} onChange={(e) => setEditProject({ ...editProject, priority: e.target.value })}>
                      <option value="">请选择</option>
                      <option value="高">高</option>
                      <option value="中">中</option>
                      <option value="低">低</option>
                    </select>
                  </label>
                  <label className="form-field"><span className="muted">行业</span><ImeTextInput type="text" value={editProject.industry} onValueChange={(value) => setEditProject({ ...editProject, industry: value })} /></label>
                  <label className="form-field"><span className="muted">职能</span><ImeTextInput type="text" value={editProject.function} onValueChange={(value) => setEditProject({ ...editProject, function: value })} /></label>
                  <label className="form-field"><span className="muted">城市</span><ImeTextInput type="text" value={editProject.city} onValueChange={(value) => setEditProject({ ...editProject, city: value })} /></label>
                  <label className="form-field"><span className="muted">地址</span><ImeTextInput type="text" value={editProject.address} onValueChange={(value) => setEditProject({ ...editProject, address: value })} /></label>
                  <label className="form-field">
                    <span className="muted">保证期</span>
                    <select value={editProject.guarantee_months} onChange={(e) => setEditProject({ ...editProject, guarantee_months: e.target.value })}>
                      <option value="">请选择</option>
                      {[1, 2, 3, 4, 5, 6, 9, 12].map((n) => (
                        <option key={n} value={`${n}个月`}>{n} 个月</option>
                      ))}
                    </select>
                  </label>
                  <label className="form-field">
                    <span className="muted">开始日期</span>
                    <input
                      type="date"
                      value={editProject.start_date}
                      onChange={(e) => {
                        const nextStart = e.target.value
                        setEditProject({
                          ...editProject,
                          start_date: nextStart,
                          end_date: editProject.end_date && editProject.end_date <= nextStart ? '' : editProject.end_date,
                        })
                      }}
                    />
                  </label>
                  <label className="form-field">
                    <span className="muted">结束日期</span>
                    <input
                      type="date"
                      min={nextDateString(editProject.start_date)}
                      value={editProject.end_date}
                      onChange={(e) => setEditProject({ ...editProject, end_date: e.target.value })}
                    />
                  </label>
                </div>
                <HardStructInputs draft={editHardStruct} onChange={setEditHardStruct} title="岗位要求" />
                <textarea style={{ minHeight: 72, marginTop: 10 }} placeholder={'一行一条，如：\n1. 统招本科\n2. 必须有云原生生产经验'} value={editHard} onChange={(e) => setEditHard(e.target.value)} />
                <h3>优先条件</h3>
                <textarea style={{ minHeight: 72 }} placeholder={'一行一条，如：\n1. 研究生毕业\n2. 有大厂背景'} value={editNice} onChange={(e) => setEditNice(e.target.value)} />
                <h3>顾问备注</h3>
                <textarea style={{ minHeight: 72 }} placeholder="公司没明说但隐含的要求、历史交付经验…" value={editNotes} onChange={(e) => setEditNotes(e.target.value)} />
                {canEdit && (
                  <div className="row" style={{ marginTop: 12 }}>
                    <button onClick={() => saveRow(editingPosition)} disabled={savingRow}>{savingRow ? '保存中…' : '保存岗位详情'}</button>
                    <button className="secondary" onClick={() => { setEditingId(null); setDetailMode(null) }}>取消</button>
                  </div>
                )}
              </>
            )}
          </div>
        ) : (
          <table className="positions-table clean-table">
            <thead>
              <tr>
                <th style={{ width: 170 }}>客户</th>
                <th>岗位名称</th>
                <th className="position-compact-head" style={{ width: 110 }}>薪资</th>
                <th className="position-compact-head" style={{ width: 110 }}>城市</th>
                <th className="center-column-head" style={{ width: 96 }}>状态</th>
                <th style={{ width: 68 }}>人才数量</th>
                <th className="position-consultant-head" style={{ width: 100 }}>顾问</th>
                <th style={{ width: 56 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {groupedPositions.map(([company, items]) => (
                items.map((p, index) => (
                  <tr key={p.id} className={index === 0 ? 'company-group-start' : ''}>
                    {index === 0 && (
                      <td className="position-company-group-cell" rowSpan={items.length}>
                        {company === '未指定公司' ? (
                          <span>{company}</span>
                        ) : (
                          <button className="link-button table-link-wrap" onClick={() => openDetail(p, 'company')}>
                            {company}
                          </button>
                        )}
                      </td>
                    )}
                    <td className="position-title-cell">
                      <button className="link-button table-link-wrap" onClick={() => openDetail(p, 'position')}>
                        {p.title}
                      </button>
                    </td>
                    <td className="position-compact-cell">
                      <ImeTextInput
                        className="table-inline-control"
                        type="text"
                        value={p.salary_range || ''}
                        placeholder="填写薪资"
                        onValueChange={(value) => patchPosition(p, { salary_range: value }, { debounce: true })}
                        onBlur={() => flushPositionUpdate(p)}
                        disabled={!canEdit}
                      />
                    </td>
                    <td className="position-compact-cell">
                      <ImeTextInput
                        className="table-inline-control"
                        type="text"
                        value={projectCity(p)}
                        placeholder="填写城市"
                        onValueChange={(value) => patchPosition(p, { project: { city: value } }, { debounce: true })}
                        onBlur={() => flushPositionUpdate(p)}
                        disabled={!canEdit}
                      />
                    </td>
                    <td className="center-column-cell">
                      <select
                        value={projectStatus(p.project_status)}
                        style={{ ...statusStyle(projectStatus(p.project_status)), padding: '4px 6px' }}
                        onChange={(e) => patchPosition(p, { project_status: e.target.value })}
                        disabled={!canEdit}
                      >
                        {PROJECT_STATUS.map((s) => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </select>
                    </td>
                    <td>{numCell(p, 'n_talents')}</td>
                    <td>
                      <select
                        value={p.consultant || ''}
                        style={{ padding: '4px 6px', maxWidth: 100 }}
                        onChange={(e) => patchPosition(p, { consultant: e.target.value })}
                        disabled={!canEdit}
                      >
                        <option value="">未指定</option>
                        {p.consultant && !users.some((u) => u.display_name === p.consultant) && (
                          <option value={p.consultant}>{p.consultant}</option>
                        )}
                        {users.map((u) => (
                          <option key={u.id} value={u.display_name}>{u.display_name}</option>
                        ))}
                      </select>
                    </td>
                    <td className="delete-action-cell">
                      <button
                        className="row-delete-button"
                        onClick={() => deletePosition(p)}
                        aria-label={`删除岗位 ${p.title}`}
                        title={canEdit ? `删除岗位 ${p.title}` : '当前账号无权限删除岗位'}
                        disabled={!canEdit}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))
              ))}
              {visiblePositions.length === 0 && (
                <tr><td colSpan={8} className="muted">暂无岗位，请新增或导入文件</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
