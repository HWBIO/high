import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Company, Position, Prompt } from '../types'
import { notifyDataUpdated, onDataUpdated } from '../utils/dataEvents'
import { canManageCustomers } from '../utils/authz'
import { readPageCache, writePageCache } from '../utils/pageCache'
import { ImeTextInput } from '../components/ImeTextInput'

interface UserOption {
  id: number
  username: string
  display_name: string
  role: string
  status: string
}

interface CompanyMeta {
  industries: string[]
  statuses: string[]
  cities: string[]
  users: UserOption[]
}

interface PageResult<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

function filterButtonLabel(label: string, value: string) {
  return `▾ ${label}${value !== '全部' ? `：${value}` : ''}`
}

function contactName(company: Company) {
  return company.contact_display_name || company.contact_name || '未指定'
}

function companyDisplayName(company: Company) {
  return company.legal_name || company.short_name || company.name
}

function companyNameAliases(company: Company) {
  return new Set([company.id, company.name, company.legal_name, company.short_name].filter(Boolean))
}

const EMPTY_COMPANY_FORM = {
  legal_name: '',
  short_name: '',
  city: '',
  industry: '',
  contact_user_id: '',
  contact_name: '',
  status: '开发中',
  notes: '',
}

const INDUSTRY_OPTIONS = [
  'AI', '互联网/IT', '电子/通信/半导体', '金融', '汽车', '能源/化工/环保',
  '医疗/生物医药', '机械/装备制造', '消费品制造', '房地产/建筑', '广告/传媒/文化',
  '专业服务', '教育/培训/科研', '贸易/零售', '交通/物流/仓储', '生活服务',
  '政府/公共事业', '农林牧渔', '其他行业',
]
const COMPANIES_CACHE_KEY = 'customers:companies'
const META_CACHE_KEY = 'customers:meta'
const PROMPTS_CACHE_KEY = 'customers:prompts'
const EMPTY_META: CompanyMeta = { industries: [], statuses: [], cities: [], users: [] }

export default function CustomersPage() {
  const canEdit = canManageCustomers()
  const [companies, setCompanies] = useState<Company[]>(() => readPageCache<Company[]>(COMPANIES_CACHE_KEY, []))
  const [meta, setMeta] = useState<CompanyMeta>(() => readPageCache<CompanyMeta>(META_CACHE_KEY, EMPTY_META))
  const [prompts, setPrompts] = useState<Prompt[]>(() => readPageCache<Prompt[]>(PROMPTS_CACHE_KEY, []))
  const [selectedCompanyId, setSelectedCompanyId] = useState<number | null>(null)
  const [selectedPositions, setSelectedPositions] = useState<Position[]>([])
  const [industryFilter, setIndustryFilter] = useState('全部')
  const [contactFilter, setContactFilter] = useState('全部')
  const [statusFilter, setStatusFilter] = useState('全部')
  const [openFilter, setOpenFilter] = useState<'industry' | 'contact' | 'status' | null>(null)
  const [promptDraft, setPromptDraft] = useState('')
  const [showPromptHistory, setShowPromptHistory] = useState(false)
  const [createPromptDraft, setCreatePromptDraft] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [form, setForm] = useState({ ...EMPTY_COMPANY_FORM })
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const companySaveTimers = useRef<Record<number, number>>({})
  const pendingCompanyPatches = useRef<Record<number, Record<string, unknown>>>({})

  const load = async () => {
    const [companyRows, promptRows, metaRows] = await Promise.all([
      api.get<PageResult<Company>>('/api/companies?limit=1000'),
      api.get<Prompt[]>('/api/prompts?layer=company&include_history=true'),
      api.get<CompanyMeta>('/api/companies/meta'),
    ])
    writePageCache(COMPANIES_CACHE_KEY, companyRows.items)
    writePageCache(PROMPTS_CACHE_KEY, promptRows)
    writePageCache(META_CACHE_KEY, metaRows)
    setCompanies(companyRows.items)
    setPrompts(promptRows)
    setMeta(metaRows)
  }

  useEffect(() => {
    load().catch((e) => setError(String((e as Error).message)))
    const refresh = () => {
      load().catch((e) => setError(String((e as Error).message)))
    }
    window.addEventListener('focus', refresh)
    const unsubscribe = onDataUpdated(['companies', 'positions', 'prompts'], refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      unsubscribe()
    }
  }, [])

  useEffect(() => () => {
    Object.values(companySaveTimers.current).forEach((timer) => window.clearTimeout(timer))
  }, [])

  const activePrompts = useMemo(() => prompts.filter((prompt) => prompt.is_active), [prompts])
  const selected = companies.find((company) => company.id === selectedCompanyId) || null
  const selectedAliases = selected ? companyNameAliases(selected) : new Set()
  const selectedPrompt = selected ? activePrompts.find((prompt) => prompt.company_id === selected.id || selectedAliases.has(prompt.company || '')) : null
  const selectedPromptHistory = selected
    ? prompts.filter((prompt) => prompt.company_id === selected.id || selectedAliases.has(prompt.company || ''))
    : []
  const contactOptions = meta.users.map((user) => user.display_name).filter(Boolean)
  const statusOptions = meta.statuses.length ? meta.statuses : ['开发中', '已签约', '历史客户']
  const industryOptions = meta.industries.length ? meta.industries : INDUSTRY_OPTIONS

  const visibleRows = companies.filter((company) => {
    if (industryFilter !== '全部' && company.industry !== industryFilter) return false
    if (contactFilter !== '全部' && contactName(company) !== contactFilter) return false
    if (statusFilter !== '全部' && company.status !== statusFilter) return false
    return true
  })

  async function openCustomer(company: Company) {
    setSelectedCompanyId(company.id)
    const aliases = companyNameAliases(company)
    setPromptDraft(activePrompts.find((prompt) => prompt.company_id === company.id || aliases.has(prompt.company || ''))?.content || '')
    setShowPromptHistory(false)
    setMessage('')
    setError('')
    try {
      await loadSelectedPositions(company.id)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  async function loadSelectedPositions(companyId: number) {
    setSelectedPositions(await api.get<Position[]>(`/api/companies/${companyId}/positions`))
  }

  useEffect(() => {
    if (!selected) return
    setPromptDraft(selectedPrompt?.content || '')
  }, [selected?.id, selectedPrompt?.id])

  async function saveCompanyPrompt() {
    if (!selected) return
    setMessage('')
    setError('')
    try {
      await api.post('/api/prompts', {
        layer: 'company',
        company_id: selected.id,
        content: promptDraft,
      })
      setMessage(`客户「${companyDisplayName(selected)}」公司提示词已保存`)
      await load()
      notifyDataUpdated('prompts')
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  async function createCompany() {
    if (!form.legal_name.trim()) {
      setError('营业执照名称不能为空')
      return
    }
    setError('')
    setMessage('')
    try {
      const created = await api.post<{ id: number }>('/api/companies', {
        legal_name: form.legal_name.trim(),
        short_name: form.short_name.trim(),
        city: form.city.trim(),
        industry: form.industry,
        contact_user_id: form.contact_user_id ? Number(form.contact_user_id) : null,
        contact_name: form.contact_name.trim(),
        status: form.status,
        notes: form.notes.trim(),
      })
      if (createPromptDraft.trim()) {
        await api.post('/api/prompts', {
          layer: 'company',
          company_id: created.id,
          content: createPromptDraft.trim(),
        })
      }
      setMessage(`客户「${form.legal_name.trim()}」已新增`)
      setForm({ ...EMPTY_COMPANY_FORM })
      setCreatePromptDraft('')
      setShowForm(false)
      await load()
      notifyDataUpdated('companies')
      if (createPromptDraft.trim()) notifyDataUpdated('prompts')
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  async function handleCustomerUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setError('')
    setMessage('')
    try {
      const result = await api.upload<{ count: number; created?: Array<{ name: string }>; errors: string[] }>('/api/companies/upload', file)
      const imported = result.created?.map((item) => item.name).filter(Boolean).join('、')
      const suffix = result.errors?.length ? `；需检查：${result.errors.join('；')}` : ''
      setMessage(`已导入 ${result.count} 个客户${imported ? `：${imported}` : ''}${suffix}`)
      await load()
      notifyDataUpdated('companies')
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  async function flushCompanyUpdate(companyId: number) {
    const timer = companySaveTimers.current[companyId]
    if (timer) {
      window.clearTimeout(timer)
      delete companySaveTimers.current[companyId]
    }
    const patch = pendingCompanyPatches.current[companyId]
    if (!patch) return
    delete pendingCompanyPatches.current[companyId]
    try {
      await api.put(`/api/companies/${companyId}`, patch)
    } catch (e) {
      setError(String((e as Error).message))
      await load()
    }
  }

  function queueCompanyUpdate(companyId: number, patch: Record<string, unknown>) {
    pendingCompanyPatches.current[companyId] = {
      ...(pendingCompanyPatches.current[companyId] || {}),
      ...patch,
    }
    const timer = companySaveTimers.current[companyId]
    if (timer) window.clearTimeout(timer)
    companySaveTimers.current[companyId] = window.setTimeout(() => {
      flushCompanyUpdate(companyId)
    }, 600)
  }

  async function updateCompany(company: Company, patch: Record<string, unknown>, options: { debounce?: boolean; reload?: boolean } = {}) {
    setError('')
    setCompanies((current) => current.map((item) => (item.id === company.id ? { ...item, ...patch } : item)))
    if (options.debounce) {
      queueCompanyUpdate(company.id, patch)
      return
    }
    try {
      await api.put(`/api/companies/${company.id}`, patch)
      if (options.reload !== false) await load()
    } catch (e) {
      setError(String((e as Error).message))
      await load()
    }
  }

  async function deleteCompany(row: Company) {
    const displayName = companyDisplayName(row)
    if (!window.confirm(`确认删除客户「${displayName}」吗？该客户下的岗位、匹配报告、客户推荐报告、AI 会话和进程记录都会一起删除，并记录操作日志。`)) return
    setError('')
    setMessage('')
    try {
      await api.delete(`/api/companies/${row.id}`)
      setMessage(`客户「${displayName}」已删除`)
      await load()
      notifyDataUpdated('companies')
      notifyDataUpdated('positions')
      notifyDataUpdated('projects')
      notifyDataUpdated('prompts')
      notifyDataUpdated('tracking')
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  if (selected) {
    return (
      <div>
        <div className="page-heading-row">
          <div className="page-heading-left">
            <button className="back-icon-button" onClick={() => setSelectedCompanyId(null)} aria-label="返回" title="返回">
              ←
            </button>
            <div className="folder-page-title">
              <h2>客户详情</h2>
            </div>
          </div>
        </div>
        {message && <p className="muted">{message}</p>}
        {error && <p className="error">{error}</p>}
        <div className="card">
          <h3 className="customer-detail-name">{selected.short_name || selected.legal_name || selected.name}</h3>
          <div className="customer-summary-grid">
            <label className="form-field">
              <span className="muted">营业执照名称</span>
              <ImeTextInput
                className="customer-detail-field"
                value={selected.legal_name || ''}
                placeholder="待补充营业执照名称"
                onValueChange={(value) => updateCompany(selected, { legal_name: value }, { debounce: true })}
                onBlur={() => flushCompanyUpdate(selected.id)}
                disabled={!canEdit}
              />
            </label>
            <label className="form-field">
              <span className="muted">公司简称</span>
              <ImeTextInput
                className="customer-detail-field"
                value={selected.short_name || ''}
                placeholder="如品牌名、常用简称"
                onValueChange={(value) => updateCompany(selected, { short_name: value }, { debounce: true })}
                onBlur={() => flushCompanyUpdate(selected.id)}
                disabled={!canEdit}
              />
            </label>
            <label className="form-field">
              <span className="muted">城市</span>
              <ImeTextInput
                className="customer-detail-field"
                value={selected.city || ''}
                placeholder="填写城市"
                onValueChange={(value) => updateCompany(selected, { city: value }, { debounce: true })}
                onBlur={() => flushCompanyUpdate(selected.id)}
                disabled={!canEdit}
              />
            </label>
            <label className="form-field">
              <span className="muted">行业</span>
              <select
                className="customer-detail-field"
                value={selected.industry || ''}
                onChange={(e) => updateCompany(selected, { industry: e.target.value })}
                disabled={!canEdit}
              >
                <option value="">未填写</option>
                {industryOptions.map((industry) => (
                  <option key={industry} value={industry}>{industry}</option>
                ))}
              </select>
            </label>
            <label className="form-field">
              <span className="muted">联系人</span>
              <select
                className="customer-detail-field"
                value={selected.contact_user_id || ''}
                onChange={(e) => updateCompany(selected, { contact_user_id: e.target.value ? Number(e.target.value) : null })}
                disabled={!canEdit}
              >
                <option value="">未指定</option>
                {meta.users.map((user) => (
                  <option key={user.id} value={user.id}>{user.display_name}</option>
                ))}
              </select>
            </label>
            <label className="form-field">
              <span className="muted">项目/岗位</span>
              <span className="customer-detail-field inline-number-field customer-count-field">
                <strong>{selected.project_count}</strong>
                <em className="muted">/{selected.position_count}</em>
              </span>
            </label>
            <label className="form-field">
              <span className="muted">状态</span>
              <select
                className="customer-detail-field"
                value={selected.status || '开发中'}
                onChange={(e) => updateCompany(selected, { status: e.target.value })}
                disabled={!canEdit}
              >
                {statusOptions.map((status) => (
                  <option key={status} value={status}>{status}</option>
                ))}
              </select>
            </label>
            <label className="form-field">
              <span className="muted">公司提示词</span>
              <span className="customer-detail-field customer-prompt-status-field">
                <strong>{selectedPrompt ? `v${selectedPrompt.version}` : '未配置'}</strong>
              </span>
            </label>
          </div>
          <h3>客户备注</h3>
          <textarea
            className="customer-note-input"
            placeholder="记录客户背景、用人偏好、合作状态、风险点等。"
            value={selected.notes || ''}
            onChange={(e) => updateCompany(selected, { notes: e.target.value }, { debounce: true })}
            onBlur={() => flushCompanyUpdate(selected.id)}
            disabled={!canEdit}
          />
          <h3>公司提示词</h3>
          <textarea
            style={{ minHeight: 150 }}
            placeholder="填写该客户通用用人标准、避雷项、不接收背景、薪酬/流程偏好等。该提示词会自动作用到该客户下所有项目。"
            value={promptDraft}
            onChange={(e) => setPromptDraft(e.target.value)}
            disabled={!canEdit}
          />
          {canEdit && (
            <div className="row" style={{ marginTop: 10 }}>
              <button onClick={saveCompanyPrompt} disabled={!promptDraft.trim()}>保存公司提示词</button>
              <button className="secondary" onClick={() => setShowPromptHistory((value) => !value)}>
                历史版本
              </button>
            </div>
          )}
          {!canEdit && (
            <div className="row" style={{ marginTop: 10 }}>
              <button className="secondary" onClick={() => setShowPromptHistory((value) => !value)}>
                历史版本
              </button>
            </div>
          )}
          {showPromptHistory && (
            <div className="prompt-history-panel">
              <h4>公司提示词历史版本</h4>
              {selectedPromptHistory.map((prompt) => (
                <div key={prompt.id} className="prompt-history-item">
                  <strong>v{prompt.version}{prompt.is_active ? ' · 当前生效' : ''}</strong>
                  <span className="muted">{prompt.created_at}</span>
                  <p>{prompt.content}</p>
                </div>
              ))}
              {selectedPromptHistory.length === 0 && <p className="muted">暂无历史版本。</p>}
            </div>
          )}
        </div>
        <div className="plain-panel">
          <div className="panel-title-row">
            <h3>该客户下的岗位</h3>
            {canEdit && (
              <Link className="button-link" to={`/positions?company_id=${selected.id}`}>
                ＋ 新增岗位
              </Link>
            )}
          </div>
          <table className="clean-table">
            <thead>
              <tr><th>序号</th><th>项目</th><th>城市</th><th>行业</th><th>联系人</th><th>状态</th></tr>
            </thead>
            <tbody>
              {selectedPositions.map((position) => {
                let project: Record<string, string> = {}
                try {
                  project = JSON.parse(position.project_json || '{}')
                } catch {
                  project = {}
                }
                return (
                  <tr key={position.id}>
                    <td>{position.seq_no || '—'}</td>
                    <td>
                      <Link className="text-link-button" to={`/positions/${position.id}`}>
                        {position.title}{position.is_project ? '（重点）' : ''}
                      </Link>
                    </td>
                    <td>{project.city || selected.city || '—'}</td>
                    <td>{project.industry || selected.industry || '—'}</td>
                    <td>{project.contact || position.consultant || contactName(selected)}</td>
                    <td>{position.project_status || '—'}</td>
                  </tr>
                )
              })}
              {selectedPositions.length === 0 && <tr><td colSpan={6} className="muted">该客户下暂无岗位。</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="folder-page-title">
        <h2>客户管理</h2>
      </div>
      <div className="card">
        <p className="muted" style={{ marginBottom: 12 }}>
          客户可手动新增，也可上传 PDF/DOCX/XLSX/TXT 文件由 DeepSeek 解析并自动填入；新增后会进入客户管理大表，并在 <Link to="/match">发起匹配</Link>、提示词管理和项目筛选中作为客户维度使用。
        </p>
        {canEdit && (
          <div className="row">
            <button onClick={() => setShowForm(!showForm)}>
              {showForm ? '收起表单' : '＋ 新增客户'}
            </button>
            <label className="row" style={{ marginBottom: 0 }}>
              <span className="muted">上传客户文件（PDF/DOCX/XLSX/TXT）：</span>
              <input type="file" accept=".pdf,.docx,.xlsx,.txt,.md" onChange={handleCustomerUpload} disabled={uploading} />
            </label>
          </div>
        )}
        {uploading && <p className="muted">DeepSeek 解析导入中，可能需要几十秒…</p>}
        {message && <p className="muted">{message}</p>}
        {error && <p className="error">{error}</p>}
        {canEdit && showForm && (
          <div style={{ marginTop: 12 }}>
            <section className="position-form-section customer-prompt-section">
              <h3>公司提示词</h3>
              <textarea
                className="customer-note-input"
                placeholder="填写该公司的用人标准、偏好、避雷项、特殊要求等。保存后会作为三层提示词中的公司提示词，自动作用到该公司下的项目匹配。"
                value={createPromptDraft}
                onChange={(e) => setCreatePromptDraft(e.target.value)}
              />
            </section>
            <div className="position-form-grid customer-create-form">
              <section className="position-form-section">
                <h3>客户信息</h3>
                <div className="form-field-grid">
                  <label className="form-field">
                    <span className="muted">营业执照名称 *</span>
                    <ImeTextInput value={form.legal_name} placeholder="请与营业执照完全一致" onValueChange={(value) => setForm({ ...form, legal_name: value })} />
                  </label>
                  <label className="form-field">
                    <span className="muted">公司简称</span>
                    <ImeTextInput value={form.short_name} placeholder="如品牌名、常用简称" onValueChange={(value) => setForm({ ...form, short_name: value })} />
                  </label>
                  <label className="form-field">
                    <span className="muted">城市</span>
                    <ImeTextInput value={form.city} placeholder="如 北京/上海" onValueChange={(value) => setForm({ ...form, city: value })} />
                  </label>
                  <label className="form-field">
                    <span className="muted">行业</span>
                    <select value={form.industry} onChange={(e) => setForm({ ...form, industry: e.target.value })}>
                      <option value="">请选择</option>
                      {industryOptions.map((industry) => (
                        <option key={industry} value={industry}>{industry}</option>
                      ))}
                    </select>
                  </label>
                  <label className="form-field">
                    <span className="muted">联系人</span>
                    <select value={form.contact_user_id} onChange={(e) => setForm({ ...form, contact_user_id: e.target.value })}>
                      <option value="">未指定</option>
                      {meta.users.map((user) => (
                        <option key={user.id} value={user.id}>{user.display_name}</option>
                      ))}
                    </select>
                  </label>
                  <label className="form-field">
                    <span className="muted">外部联系人</span>
                    <ImeTextInput value={form.contact_name} placeholder="客户侧联系人姓名" onValueChange={(value) => setForm({ ...form, contact_name: value })} />
                  </label>
                  <label className="form-field">
                    <span className="muted">状态</span>
                    <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                      {statusOptions.map((status) => (
                        <option key={status} value={status}>{status}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </section>
              <section className="position-form-section">
                <h3>客户备注</h3>
                <textarea
                  className="customer-note-input"
                  placeholder="记录客户背景、用人偏好、合作状态、风险点等。"
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                />
              </section>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button onClick={createCompany}>保存客户</button>
              <button className="secondary" onClick={() => { setShowForm(false); setForm({ ...EMPTY_COMPANY_FORM }); setCreatePromptDraft('') }}>取消</button>
            </div>
          </div>
        )}
      </div>
      <div className="plain-panel">
        <div className="filter-block">
          <div className="filter-dropdown-bar">
            {[
              { key: 'industry' as const, label: '行业', value: industryFilter, setValue: setIndustryFilter, options: industryOptions },
              { key: 'contact' as const, label: '联系人', value: contactFilter, setValue: setContactFilter, options: contactOptions },
              { key: 'status' as const, label: '状态', value: statusFilter, setValue: setStatusFilter, options: statusOptions },
            ].map((filter) => (
              <div className="filter-dropdown" key={filter.key}>
                <button
                  className={filter.value === '全部' ? 'filter-chip' : 'filter-chip active'}
                  onClick={() => setOpenFilter(openFilter === filter.key ? null : filter.key)}
                >
                  {filterButtonLabel(filter.label, filter.value)}
                </button>
                {openFilter === filter.key && (
                  <div className="filter-dropdown-menu">
                    {['全部', ...filter.options].map((option) => (
                      <button
                        key={option}
                        className={filter.value === option ? 'active' : ''}
                        onClick={() => {
                          filter.setValue(option)
                          setOpenFilter(null)
                        }}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
        <table className="customer-table clean-table">
          <colgroup>
            <col className="customer-col-name" />
            <col className="customer-col-short-name" />
            <col className="customer-col-even" />
            <col className="customer-col-even" />
            <col className="customer-col-even" />
            <col className="customer-col-project" />
            <col className="customer-col-even" />
            <col className="customer-col-action" />
          </colgroup>
          <thead>
            <tr><th>营业执照名称</th><th>公司简称</th><th>城市</th><th>行业</th><th>联系人</th><th>项目/岗位</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={row.id}>
                <td>
                  <button className="link-button" onClick={() => openCustomer(row)}>{row.legal_name || '待补充'}</button>
                </td>
                <td>{row.short_name || '—'}</td>
                <td>
                  <ImeTextInput
                    className="table-inline-control"
                    type="text"
                    value={row.city || ''}
                    placeholder="填写城市"
                    onValueChange={(value) => updateCompany(row, { city: value }, { debounce: true })}
                    onBlur={() => flushCompanyUpdate(row.id)}
                    disabled={!canEdit}
                  />
                </td>
                <td>
                  <select
                    className="table-inline-control"
                    value={row.industry || ''}
                    onChange={(e) => updateCompany(row, { industry: e.target.value })}
                    disabled={!canEdit}
                  >
                    <option value="">未填写</option>
                    {industryOptions.map((industry) => (
                      <option key={industry} value={industry}>{industry}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    className="table-inline-control"
                    value={row.contact_user_id || ''}
                    onChange={(e) => updateCompany(row, { contact_user_id: e.target.value ? Number(e.target.value) : null })}
                    disabled={!canEdit}
                  >
                    <option value="">未指定</option>
                    {meta.users.map((user) => (
                      <option key={user.id} value={user.id}>{user.display_name}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <strong>{row.project_count}</strong>
                  <span className="muted">/{row.position_count}</span>
                </td>
                <td>
                  <select
                    className="table-inline-control"
                    value={row.status || '开发中'}
                    onChange={(e) => updateCompany(row, { status: e.target.value })}
                    disabled={!canEdit}
                  >
                    {statusOptions.map((status) => (
                      <option key={status} value={status}>{status}</option>
                    ))}
                  </select>
                </td>
                <td className="delete-action-cell">
                  {canEdit && (
                    <div className="table-action-group">
                      <Link className="table-action-link" to={`/positions?company_id=${row.id}`}>
                        新增岗位
                      </Link>
                      <button className="row-delete-button" onClick={() => deleteCompany(row)} aria-label={`删除客户 ${companyDisplayName(row)}`}>×</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {visibleRows.length === 0 && <tr><td colSpan={8} className="muted">暂无客户，请先在岗位管理中新增岗位，或清除筛选条件。</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
