import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Company, PageResult, PositionOption, Prompt } from '../types'
import { formatPositionLabel } from '../utils/positionLabel'
import { notifyDataUpdated, onDataUpdated } from '../utils/dataEvents'
import { canManageBusiness, canManagePrompts } from '../utils/authz'
import { readPageCache, writePageCache } from '../utils/pageCache'
import { auth } from '../api'

type Layer = 'general' | 'company' | 'position'
const PROMPTS_DRAFT_KEY = 'hh-match:prompts-page-draft'
const PROMPTS_POSITIONS_CACHE_KEY = 'prompts:positions'
const PROMPTS_COMPANIES_CACHE_KEY = 'prompts:companies'

interface PromptsDraft {
  selectedCompany?: string
  selectedCompanyId?: number | ''
  selectedPositionId?: number | ''
}

interface PromptPreview {
  positionId: number
  system: string
  user: string
}

function readPromptsDraft(): PromptsDraft {
  try {
    return JSON.parse(window.localStorage.getItem(PROMPTS_DRAFT_KEY) || '{}') as PromptsDraft
  } catch {
    return {}
  }
}

export default function PromptsPage() {
  const canEdit = canManagePrompts()
  const currentUser = auth.user()
  const canEditCompanyGeneral = canManageBusiness(currentUser)
  const draft = readPromptsDraft()
  const legacySelectedCompany = useRef(draft.selectedCompany || '')
  const [prompts, setPrompts] = useState<Prompt[]>(() => readPageCache<Prompt[]>('prompts:all', []))
  const [positions, setPositions] = useState<PositionOption[]>(() => readPageCache<PositionOption[]>(PROMPTS_POSITIONS_CACHE_KEY, []))
  const [companies, setCompanies] = useState<Company[]>(() => readPageCache<Company[]>(PROMPTS_COMPANIES_CACHE_KEY, []))
  const [generalContent, setGeneralContent] = useState('')
  const [personalGeneralContent, setPersonalGeneralContent] = useState('')
  const [companyContent, setCompanyContent] = useState('')
  const [positionContent, setPositionContent] = useState('')
  const [selectedCompanyId, setSelectedCompanyId] = useState<number | ''>(draft.selectedCompanyId || '')
  const [selectedPositionId, setSelectedPositionId] = useState<number | ''>(draft.selectedPositionId || '')
  const [preview, setPreview] = useState<PromptPreview | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [editingPromptId, setEditingPromptId] = useState<number | null>(null)
  const [editPromptDraft, setEditPromptDraft] = useState({ version: 1, content: '' })
  const [showGeneralCreate, setShowGeneralCreate] = useState(false)
  const [showPersonalGeneralCreate, setShowPersonalGeneralCreate] = useState(false)
  const [showCompanyCreate, setShowCompanyCreate] = useState(false)
  const [showPositionCreate, setShowPositionCreate] = useState(false)
  const [showGeneralHistory, setShowGeneralHistory] = useState(false)
  const [showPersonalGeneral, setShowPersonalGeneral] = useState(false)
  const [showCompanyHistory, setShowCompanyHistory] = useState(false)
  const [showPositionHistory, setShowPositionHistory] = useState(false)

  const load = useCallback(() => {
    return api.get<Prompt[]>('/api/prompts?include_history=true').then((rows) => {
      writePageCache('prompts:all', rows)
      setPrompts(rows)
    })
  }, [])

  useEffect(() => {
    const loadPositions = () => api.get<PositionOption[]>('/api/positions/options').then((rows) => {
      writePageCache(PROMPTS_POSITIONS_CACHE_KEY, rows)
      setPositions(rows)
    })
    const loadCompanies = () => api.get<PageResult<Company>>('/api/companies?limit=1000').then((page) => {
      writePageCache(PROMPTS_COMPANIES_CACHE_KEY, page.items)
      setCompanies(page.items)
    })
    const refresh = () => {
      loadCompanies().catch((e) => setError(String((e as Error).message)))
      loadPositions().catch((e) => setError(String((e as Error).message)))
      load().catch((e) => setError(String((e as Error).message)))
    }
    refresh()
    window.addEventListener('focus', refresh)
    const unsubscribe = onDataUpdated(['companies', 'positions', 'prompts'], refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      unsubscribe()
    }
  }, [load])

  useEffect(() => {
    if (!selectedCompanyId && legacySelectedCompany.current) return
    window.localStorage.setItem(
      PROMPTS_DRAFT_KEY,
      JSON.stringify({
        selectedCompanyId,
        selectedPositionId,
      }),
    )
  }, [selectedCompanyId, selectedPositionId])

  useEffect(() => {
    if (selectedCompanyId || !legacySelectedCompany.current || !companies.length) return
    const legacy = companies.find((company) => (
      company.name === legacySelectedCompany.current
      || company.legal_name === legacySelectedCompany.current
      || company.short_name === legacySelectedCompany.current
    ))
    legacySelectedCompany.current = ''
    if (legacy) {
      setSelectedCompanyId(legacy.id)
    }
  }, [companies, selectedCompanyId])

  const companyOptions = companies.map((company) => ({
    id: company.id,
    label: [
      company.short_name || company.name,
      company.legal_name && company.legal_name !== (company.short_name || company.name) ? company.legal_name : '',
      company.city,
      company.industry,
    ].filter(Boolean).join(' / '),
  }))
  const positionOptions = positions.map((p) => ({
    id: p.id,
    label: formatPositionLabel(p),
  }))
  const generalPrompts = prompts.filter((p) => p.layer === 'general' && p.scope !== 'personal')
  const personalGeneralPrompts = prompts.filter((p) => p.layer === 'general' && p.scope === 'personal')
  const companyPrompts = prompts.filter((p) => p.layer === 'company' && !!selectedCompanyId && p.company_id === selectedCompanyId)
  const positionPrompts = prompts.filter((p) => p.layer === 'position' && !!selectedPositionId && p.position_id === selectedPositionId)
  const companyDefaultGeneralPrompt = generalPrompts.find((p) => p.is_active) || generalPrompts[0] || null
  const effectiveGeneralPrompt = [...generalPrompts, ...personalGeneralPrompts].find((p) => p.is_effective) || companyDefaultGeneralPrompt
  const currentCompanyPrompt = companyPrompts.find((p) => p.is_active) || companyPrompts[0] || null
  const currentPositionPrompt = positionPrompts.find((p) => p.is_active) || positionPrompts[0] || null
  const generalHistoryPrompts = companyDefaultGeneralPrompt ? generalPrompts.filter((p) => p.id !== companyDefaultGeneralPrompt.id) : []
  const companyHistoryPrompts = currentCompanyPrompt ? companyPrompts.filter((p) => p.id !== currentCompanyPrompt.id) : []
  const positionHistoryPrompts = currentPositionPrompt ? positionPrompts.filter((p) => p.id !== currentPositionPrompt.id) : []

  async function savePrompt(layer: Layer, scope = 'company_default') {
    setError('')
    setMessage('')
    try {
      const body: Record<string, unknown> = { layer }
      if (layer === 'general') {
        body.scope = scope
        body.content = scope === 'personal' ? personalGeneralContent : generalContent
      }
      if (layer === 'company') {
        if (!selectedCompanyId) throw new Error('请选择客户')
        body.company_id = selectedCompanyId
        body.content = companyContent
      }
      if (layer === 'position') {
        body.position_id = selectedPositionId
        body.content = positionContent
      }
      const result = await api.post<{ version: number }>('/api/prompts', body)
      setMessage(`已保存为 v${result.version}（旧版本自动失活，历史保留）`)
      await load()
      if (layer === 'general') {
        if (scope === 'personal') {
          setPersonalGeneralContent('')
          setShowPersonalGeneralCreate(false)
          setShowPersonalGeneral(true)
        } else {
          setGeneralContent('')
          setShowGeneralCreate(false)
        }
      }
      if (layer === 'company') {
        setCompanyContent('')
        setShowCompanyCreate(false)
      }
      if (layer === 'position') {
        setPositionContent('')
        setShowPositionCreate(false)
      }
      setPreview(null)
      notifyDataUpdated('prompts')
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  function startEditPrompt(p: Prompt) {
    setMessage('')
    setError('')
    setEditingPromptId(p.id)
    setEditPromptDraft({ version: p.version, content: p.content })
  }

  async function activatePrompt(p: Prompt) {
    setError('')
    setMessage('')
    try {
      await api.post(`/api/prompts/${p.id}/activate`)
      setMessage(`已应用 v${p.version}`)
      await load()
      setPreview(null)
      notifyDataUpdated('prompts')
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  async function savePromptEdit() {
    if (!editingPromptId) return
    setError('')
    setMessage('')
    try {
      await api.put(`/api/prompts/${editingPromptId}`, editPromptDraft)
      setMessage('提示词已更新')
      setEditingPromptId(null)
      await load()
      setPreview(null)
      notifyDataUpdated('prompts')
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  function canModifyPrompt(p: Prompt) {
    if (!canEdit) return false
    if (p.layer === 'general' && p.scope !== 'personal') return canEditCompanyGeneral
    return true
  }

  async function loadPreview(positionId?: number | '') {
    const pid = positionId || selectedPositionId || positions[0]?.id
    if (!pid) {
      setError('岗位库为空，无法预览')
      return
    }
    setError('')
    try {
      const data = await api.post<{ system: string; user: string }>(`/api/prompts/compose-preview?position_id=${pid}`)
      setPreview({ ...data, positionId: Number(pid) })
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  function togglePreview(positionId?: number | '') {
    const pid = Number(positionId || selectedPositionId || positions[0]?.id || 0)
    if (preview && preview.positionId === pid) {
      setPreview(null)
      return
    }
    void loadPreview(pid)
  }

  const renderPromptTable = (rows: Prompt[], emptyText: string, showActions = false) => (
    <table>
      <thead>
        <tr>
          <th style={{ width: 80 }}>序号</th>
          <th style={{ width: 100 }}>版本</th>
          <th>提示词内容</th>
          {showActions && <th style={{ width: 160 }}>操作</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((p, index) => (
          <tr key={p.id}>
            <td>{index + 1}</td>
            <td>
              {editingPromptId === p.id ? (
                <input
                  type="number"
                  min={1}
                  className="prompt-version-input"
                  value={editPromptDraft.version}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setEditPromptDraft({ ...editPromptDraft, version: Number(e.target.value) || 1 })}
                />
              ) : (
                <span className="prompt-version">{p.scope === 'personal' ? `${p.owner_name || currentUser?.display_name || '我的'} v${p.version}` : `v${p.version}`}</span>
              )}
            </td>
            <td>
              {p.layer !== 'general' && (
                <div><strong>{p.company || p.position_title || '未指定作用域'}</strong></div>
              )}
              {editingPromptId === p.id ? (
                <div className="prompt-inline-editor" onClick={(e) => e.stopPropagation()}>
                  <textarea
                    value={editPromptDraft.content}
                    onChange={(e) => setEditPromptDraft({ ...editPromptDraft, content: e.target.value })}
                  />
                  <div className="row">
                    <button onClick={savePromptEdit}>保存修改</button>
                    <button className="secondary" onClick={() => setEditingPromptId(null)}>取消</button>
                  </div>
                </div>
              ) : (
                <div className="prompt-content-text">{p.content}</div>
              )}
              <div className="prompt-meta">
                创建时间：{p.created_at}
                {p.layer === 'general'
                  ? p.is_effective
                    ? ' · 当前应用'
                    : p.is_active && p.scope !== 'personal'
                      ? ' · 公司默认'
                      : ' · 历史版本'
                  : p.is_active
                    ? ' · 当前生效'
                    : ' · 历史版本'}
              </div>
            </td>
            {showActions && (
              <td>
                <div className="prompt-action-buttons">
                  <button
                    className="secondary"
                    disabled={!canModifyPrompt(p)}
                    onClick={(e) => {
                      e.stopPropagation()
                      startEditPrompt(p)
                    }}
                  >
                    编辑
                  </button>
                  {p.layer === 'general' ? (
                    p.is_effective ? (
                      <span className="prompt-current-label">当前应用</span>
                    ) : (
                      <button
                        disabled={!canEdit}
                        onClick={(e) => {
                          e.stopPropagation()
                          activatePrompt(p)
                        }}
                      >
                        应用
                      </button>
                    )
                  ) : p.is_active ? (
                    <span className="prompt-current-label">当前应用</span>
                  ) : (
                    <button
                      disabled={!canEdit}
                      onClick={(e) => {
                        e.stopPropagation()
                        activatePrompt(p)
                      }}
                    >
                      应用
                    </button>
                  )}
                </div>
              </td>
            )}
          </tr>
        ))}
        {rows.length === 0 && (
          <tr><td colSpan={showActions ? 4 : 3} className="muted">{emptyText}</td></tr>
        )}
      </tbody>
    </table>
  )

  const renderHistoryBlock = (
    rows: Prompt[],
    expanded: boolean,
    onToggle: () => void,
    emptyText: string,
  ) => (
    <div className="prompt-history-fold">
      <button className="secondary" onClick={onToggle}>
        历史版本{rows.length ? `（${rows.length}）` : ''}
      </button>
      {expanded && (
        rows.length ? renderPromptTable(rows, emptyText, true) : <p className="muted">{emptyText}</p>
      )}
    </div>
  )

  return (
    <div>
      <div className="folder-page-title">
        <h2>提示词管理</h2>
      </div>

      {message && <p className="muted">{message}</p>}
      {error && <p className="error">{error}</p>}

      <div className="card prompt-management-section">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h3 style={{ margin: 0 }}>一、通用提示词</h3>
          {canEditCompanyGeneral && (
            <button
              onClick={() => {
                setShowGeneralCreate(true)
                setGeneralContent('')
              }}
            >
              新建
            </button>
          )}
        </div>
        <div className="prompt-active-version-row">
          当前应用版本：{effectiveGeneralPrompt
            ? `${effectiveGeneralPrompt.scope === 'personal' ? `${effectiveGeneralPrompt.owner_name || currentUser?.display_name || '我的'} · 个人` : '公司通用'} V${effectiveGeneralPrompt.version}`
            : '未配置'}
        </div>
        {renderPromptTable(companyDefaultGeneralPrompt ? [companyDefaultGeneralPrompt] : [], '暂无通用提示词', true)}
        {renderHistoryBlock(
          generalHistoryPrompts,
          showGeneralHistory,
          () => setShowGeneralHistory((open) => !open),
          '暂无通用提示词历史版本',
        )}
        <div className="prompt-history-fold">
          <button className="secondary" onClick={() => setShowPersonalGeneral((open) => !open)}>
            我的通用提示词{personalGeneralPrompts.length ? `（${personalGeneralPrompts.length}）` : ''}
          </button>
          {showPersonalGeneral && (
            <div className="prompt-personal-panel">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="muted">只对当前账号可见；应用后与公司通用提示词互斥。</span>
                {canEdit && (
                  <button
                    onClick={() => {
                      setShowPersonalGeneralCreate(true)
                      setPersonalGeneralContent('')
                    }}
                  >
                    新建
                  </button>
                )}
              </div>
              {renderPromptTable(personalGeneralPrompts, '暂无我的通用提示词', true)}
              {canEdit && showPersonalGeneralCreate && (
                <div className="prompt-new-version-box">
                  <textarea
                    placeholder="填写我的通用提示词。保存后将作为当前账号的应用版本。"
                    value={personalGeneralContent}
                    onChange={(e) => setPersonalGeneralContent(e.target.value)}
                  />
                  <div className="row">
                    <button onClick={() => savePrompt('general', 'personal')} disabled={!personalGeneralContent.trim()}>
                      保存个人提示词新版本
                    </button>
                    <button className="secondary" onClick={() => setShowPersonalGeneralCreate(false)}>取消</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        {canEditCompanyGeneral && showGeneralCreate && (
          <div className="prompt-new-version-box">
            <textarea
              placeholder="填写新的通用提示词版本。保存后旧版本自动进入历史，新版本立即生效。"
              value={generalContent}
              onChange={(e) => setGeneralContent(e.target.value)}
            />
            <div className="row">
              <button onClick={() => savePrompt('general', 'company_default')} disabled={!generalContent.trim()}>保存为新版本</button>
              <button className="secondary" onClick={() => setShowGeneralCreate(false)}>取消</button>
            </div>
          </div>
        )}
      </div>

      <div className="card prompt-management-section">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h3 style={{ margin: 0 }}>二、客户提示词</h3>
          <div className="row" style={{ marginBottom: 0 }}>
            <span className="muted">以公司为单位维护，和客户详情实时同步</span>
            {canEdit && selectedCompanyId && (
              <button
                onClick={() => {
                  setShowCompanyCreate(true)
                  setCompanyContent('')
                }}
              >
                新建
              </button>
            )}
          </div>
        </div>
        <div className="row">
          <select
            value={selectedCompanyId}
            onChange={(e) => {
              setSelectedCompanyId(Number(e.target.value) || '')
              setCompanyContent('')
              setShowCompanyCreate(false)
              setShowCompanyHistory(false)
            }}
          >
            <option value="" hidden>选择客户</option>
            {companyOptions.map((company) => (
              <option key={company.id} value={company.id}>{company.label}</option>
            ))}
          </select>
          <button
            className="secondary"
            onClick={() => {
              setSelectedCompanyId('')
              setShowCompanyCreate(false)
              setShowCompanyHistory(false)
            }}
          >
            清除筛选
          </button>
        </div>
        {renderPromptTable(currentCompanyPrompt ? [currentCompanyPrompt] : [], selectedCompanyId ? '该客户暂无公司提示词版本' : '请选择客户后查看公司提示词历史版本', true)}
        {selectedCompanyId && renderHistoryBlock(
          companyHistoryPrompts,
          showCompanyHistory,
          () => setShowCompanyHistory((open) => !open),
          '暂无公司提示词历史版本',
        )}
        {canEdit && selectedCompanyId && showCompanyCreate && (
          <div className="prompt-new-version-box">
            <textarea
              placeholder="填写该客户的新公司提示词版本。保存后会同步到客户详情。"
              value={companyContent}
              onChange={(e) => setCompanyContent(e.target.value)}
            />
            <div className="row">
              <button onClick={() => savePrompt('company')} disabled={!companyContent.trim()}>保存为新版本</button>
              <button className="secondary" onClick={() => setShowCompanyCreate(false)}>取消</button>
            </div>
          </div>
        )}
      </div>

      <div className="card prompt-management-section">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h3 style={{ margin: 0 }}>三、岗位提示词</h3>
          <div className="row" style={{ marginBottom: 0 }}>
            <span className="muted">以岗位为单位维护，和岗位详情实时同步</span>
            {canEdit && selectedPositionId && (
              <button
                onClick={() => {
                  setShowPositionCreate(true)
                  setPositionContent('')
                }}
              >
                新建
              </button>
            )}
          </div>
        </div>
        <div className="row">
          <select
            value={selectedPositionId}
            onChange={(e) => {
              setSelectedPositionId(Number(e.target.value) || '')
              setPositionContent('')
              setShowPositionCreate(false)
              setShowPositionHistory(false)
              setPreview(null)
            }}
          >
            <option value="" hidden>选择岗位</option>
            {positionOptions.map((position) => (
              <option key={position.id} value={position.id}>{position.label}</option>
            ))}
          </select>
          <button
            className="secondary"
            onClick={() => {
              setSelectedPositionId('')
              setShowPositionCreate(false)
              setShowPositionHistory(false)
              setPreview(null)
            }}
          >
            清除筛选
          </button>
        </div>
        {renderPromptTable(currentPositionPrompt ? [currentPositionPrompt] : [], selectedPositionId ? '该岗位暂无岗位提示词版本' : '请选择岗位后查看岗位提示词历史版本', true)}
        {selectedPositionId && renderHistoryBlock(
          positionHistoryPrompts,
          showPositionHistory,
          () => setShowPositionHistory((open) => !open),
          '暂无岗位提示词历史版本',
        )}
        {canEdit && selectedPositionId && showPositionCreate && (
          <div className="prompt-new-version-box">
            <textarea
              placeholder="填写该岗位的新岗位提示词版本。保存后会同步到岗位详情。"
              value={positionContent}
              onChange={(e) => setPositionContent(e.target.value)}
            />
            <div className="row">
              <button onClick={() => savePrompt('position')} disabled={!positionContent.trim()}>保存为新版本</button>
              <button className="secondary" onClick={() => setShowPositionCreate(false)}>取消</button>
            </div>
          </div>
        )}
      </div>

      <button onClick={() => togglePreview(selectedPositionId)}>
        {preview ? '收起组装后的完整 Prompt' : '预览组装后的完整 Prompt'}
      </button>

      {preview && (
        <div className="card">
          <h3>组装预览（模型实际收到的内容）</h3>
          <h3 className="muted">system</h3>
          <pre className="markdown-box">{preview.system}</pre>
          <h3 className="muted">user</h3>
          <pre className="markdown-box">{preview.user}</pre>
        </div>
      )}
    </div>
  )
}
