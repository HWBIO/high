import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { MatchHistoryItem, PageResult, PositionOption, Tracking } from '../types'
import { notifyDataUpdated, onDataUpdated } from '../utils/dataEvents'
import { canManageProcess } from '../utils/authz'
import { readPageCache, writePageCache } from '../utils/pageCache'

interface UserOption {
  id: number
  display_name: string
}

interface TrackingMeta {
  stages: string[]
  reject_reasons: string[]
  client_feedbacks: string[]
}

interface TrackingInit {
  meta: Partial<TrackingMeta>
  users: UserOption[]
  positions: PositionOption[]
}

const DEFAULT_TRACKING_META: TrackingMeta = {
  stages: ['待推荐', '初筛（已推荐客户）', '一面', '二面', '三面', '四面', '谈薪', 'offer', '入职', 'pass'],
  reject_reasons: ['硬性条件不符', '经验深度不够', '行业背景不符', '薪资不符', '城市/到岗不符', '稳定性顾虑', '简历表达不清', '客户HC变化', '候选人拒绝', '顾问放弃', '其他'],
  client_feedbacks: ['待反馈', '感兴趣', '不感兴趣', '需要补充信息', '进入面试', '暂缓', '已拒绝'],
}
const TRACKING_ROWS_CACHE_KEY = 'process:rows'
const TRACKING_INIT_CACHE_KEY = 'process:init'

const stageStyle = (stage: string): React.CSSProperties => {
  if (stage === '待推荐') return { background: '#f5f6f7', color: '#646a73' }
  if (stage === 'pass') return { background: '#fde9e9', color: '#c0392b' }
  if (stage === 'offer' || stage === '入职') return { background: '#e8f7ee', color: '#1a7f37' }
  return { background: '#e8f1fd', color: '#1a56a7' }
}

const stageValue = (stage: string, stages: string[]) => {
  const aliases: Record<string, string> = {
    已推荐客户: '初筛（已推荐客户）',
    客户初筛通过: '初筛（已推荐客户）',
    客户初筛未通过: 'pass',
    需补充信息: '初筛（已推荐客户）',
    终面: '四面',
    Offer: 'offer',
    淘汰: 'pass',
  }
  const normalized = aliases[stage] || stage
  return stages.includes(normalized) ? normalized : '初筛（已推荐客户）'
}

const stageOptions = (stages?: string[]) => {
  if (!stages?.length) return DEFAULT_TRACKING_META.stages
  return stages.some((stage) => ['已推荐客户', '客户初筛通过', '终面', 'Offer', '淘汰'].includes(stage))
    ? DEFAULT_TRACKING_META.stages
    : stages
}

export default function ProcessPage() {
  const canEdit = canManageProcess()
  const initCache = readPageCache<TrackingInit | null>(TRACKING_INIT_CACHE_KEY, null)
  const [rows, setRows] = useState<Tracking[]>(() => readPageCache<Tracking[]>(TRACKING_ROWS_CACHE_KEY, []))
  const [positions, setPositions] = useState<PositionOption[]>(() => initCache?.positions || [])
  const [users, setUsers] = useState<UserOption[]>(() => initCache?.users || [])
  const [meta, setMeta] = useState<TrackingMeta>(() => ({
    stages: stageOptions(initCache?.meta.stages),
    reject_reasons: initCache?.meta.reject_reasons?.length ? initCache.meta.reject_reasons : DEFAULT_TRACKING_META.reject_reasons,
    client_feedbacks: initCache?.meta.client_feedbacks?.length ? initCache.meta.client_feedbacks : DEFAULT_TRACKING_META.client_feedbacks,
  }))
  const [error, setError] = useState('')
  const [stageFilter, setStageFilter] = useState('全部')
  const [ownerFilter, setOwnerFilter] = useState('全部')
  const [openFilter, setOpenFilter] = useState<'stage' | 'owner' | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [addCandidateFilter, setAddCandidateFilter] = useState('')
  const [addPositionFilter, setAddPositionFilter] = useState('')
  const [addScoreFilter, setAddScoreFilter] = useState('')
  const [addResults, setAddResults] = useState<MatchHistoryItem[]>([])
  const [addSearching, setAddSearching] = useState(false)
  const [addMessage, setAddMessage] = useState('')

  const load = () => api.get<Tracking[]>('/api/tracking').then((items) => {
    writePageCache(TRACKING_ROWS_CACHE_KEY, items)
    setRows(items)
  })
  const loadInit = () => api.get<TrackingInit>('/api/tracking/init').then((data) => {
    writePageCache(TRACKING_INIT_CACHE_KEY, data)
    setPositions(data.positions)
    setUsers(data.users)
    setMeta({
      stages: stageOptions(data.meta.stages),
      reject_reasons: data.meta.reject_reasons?.length ? data.meta.reject_reasons : DEFAULT_TRACKING_META.reject_reasons,
      client_feedbacks: data.meta.client_feedbacks?.length ? data.meta.client_feedbacks : DEFAULT_TRACKING_META.client_feedbacks,
    })
  })

  useEffect(() => {
    load().catch((e) => setError(String((e as Error).message)))
    loadInit().catch((e) => setError(String((e as Error).message)))
    const refresh = () => {
      load().catch((e) => setError(String((e as Error).message)))
      loadInit().catch((e) => setError(String((e as Error).message)))
    }
    window.addEventListener('focus', refresh)
    const unsubscribe = onDataUpdated(['positions', 'candidates', 'tracking'], refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      unsubscribe()
    }
  }, [])

  async function updateCell(row: Tracking, patch: Record<string, string | number | null>) {
    setError('')
    setRows(rows.map((item) => (item.id === row.id ? { ...item, ...patch } : item)))
    try {
      await api.put(`/api/tracking/${row.id}`, patch)
      notifyDataUpdated('tracking')
    } catch (e) {
      setError(String((e as Error).message))
      await load()
    }
  }

  async function updatePosition(row: Tracking, nextPositionId: number | null) {
    if (nextPositionId === row.position_id) return
    const confirmed = window.confirm('当前人才与前期匹配岗位不符，是否要更换岗位')
    if (!confirmed) return
    await updateCell(row, {
      position_id: nextPositionId,
      match_result_id: null,
      final_score_snapshot: 0,
      recommend_level_snapshot: '',
    })
  }

  async function searchReports() {
    setAddMessage('')
    setAddSearching(true)
    try {
      const params = new URLSearchParams()
      params.set('limit', '30')
      if (addCandidateFilter.trim()) params.set('candidate_name', addCandidateFilter.trim())
      if (addPositionFilter) params.set('position_id', addPositionFilter)
      if (addScoreFilter) params.set('score_band', addScoreFilter)
      const page = await api.get<PageResult<MatchHistoryItem>>(`/api/match/history-results?${params.toString()}`)
      setAddResults(page.items)
    } catch (e) {
      setAddMessage(String((e as Error).message || e))
    } finally {
      setAddSearching(false)
    }
  }

  async function addTrackingFromReport(item: MatchHistoryItem) {
    if (item.final_score == null) return
    setAddMessage('')
    try {
      const result = await api.post<{ id: number; existed?: boolean }>('/api/tracking', {
        candidate_id: item.candidate_id,
        position_id: item.position_id,
        match_result_id: item.result_id,
        final_score_snapshot: item.final_score,
        recommend_level_snapshot: item.recommend_level || '',
        source: '进程管理',
        stage: '初筛（已推荐客户）',
        client_feedback: '待反馈',
        next_action: '等待客户反馈',
      })
      await load()
      notifyDataUpdated('tracking')
      setAddMessage(result.existed ? '该人才在该项目已有进程，已更新匹配报告快照。' : '已加入进程管理。')
    } catch (e) {
      setAddMessage(String((e as Error).message || e))
    }
  }

  const visibleRows = rows.filter((row) => {
    const stageOk = stageFilter === '全部' || row.stage === stageFilter
    const ownerOk = ownerFilter === '全部' || (row.owner || '未指定') === ownerFilter
    return stageOk && ownerOk
  })

  function candidateTarget(row: Tracking) {
    if (row.match_result_id) return `/results/${row.match_result_id}`
    return `/match/history?candidate=${encodeURIComponent(row.candidate_name)}`
  }

  return (
    <div>
      <div className="page-title-row">
        <div className="folder-page-title">
          <h2>进程管理</h2>
        </div>
        {canEdit && (
          <button onClick={() => setShowAddModal(true)}>
            添加进程
          </button>
        )}
      </div>
      <div className="plain-panel">
        <div className="filter-block">
          <div className="filter-dropdown-bar">
            <div className="filter-dropdown">
              <button
                className={stageFilter === '全部' ? 'filter-chip' : 'filter-chip active'}
                onClick={() => setOpenFilter(openFilter === 'stage' ? null : 'stage')}
              >
                当前阶段：{stageFilter} ▾
              </button>
              {openFilter === 'stage' && (
                <div className="filter-dropdown-menu">
                  {['全部', ...meta.stages].map((stage) => (
                    <button
                      key={stage}
                      className={stageFilter === stage ? 'active' : ''}
                      onClick={() => {
                        setStageFilter(stage)
                        setOpenFilter(null)
                      }}
                    >
                      {stage}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="filter-dropdown">
              <button
                className={ownerFilter === '全部' ? 'filter-chip' : 'filter-chip active'}
                onClick={() => setOpenFilter(openFilter === 'owner' ? null : 'owner')}
              >
                顾问：{ownerFilter} ▾
              </button>
              {openFilter === 'owner' && (
                <div className="filter-dropdown-menu">
                  {['全部', ...users.map((user) => user.display_name)].map((owner) => (
                    <button
                      key={owner}
                      className={ownerFilter === owner ? 'active' : ''}
                      onClick={() => {
                        setOwnerFilter(owner)
                        setOpenFilter(null)
                      }}
                    >
                      {owner}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
        {error && <p className="error">{error}</p>}
        <table className="clean-table process-table">
          <thead>
            <tr>
              <th style={{ width: '12%' }}>姓名</th>
              <th style={{ width: '15%' }}>项目</th>
              <th style={{ width: '18%' }}>客户</th>
              <th style={{ width: '14%' }}>匹配得分</th>
              <th style={{ width: '11%' }}>当前阶段</th>
              <th style={{ width: '15%' }}>客户反馈</th>
              <th style={{ width: '15%' }}>顾问</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={row.id}>
                <td>
                  <Link className="text-link-button" to={candidateTarget(row)}>
                    {row.candidate_name}
                  </Link>
                </td>
                <td>
                  <select
                    value={row.position_id ?? ''}
                    style={{ width: '100%', padding: '4px 6px' }}
                    onChange={(e) => updatePosition(row, e.target.value ? Number(e.target.value) : null)}
                    disabled={!canEdit}
                  >
                    <option value="">未选岗位</option>
                    {positions.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.seq_no ? `岗位${p.seq_no}：` : ''}{p.title}
                      </option>
                    ))}
                  </select>
                </td>
                <td>{row.company || '—'}</td>
                <td>{row.final_score_snapshot != null ? `${row.final_score_snapshot}分` : '—'}</td>
                <td>
                  <select
                    value={stageValue(row.stage, meta.stages)}
                    style={{ ...stageStyle(stageValue(row.stage, meta.stages)), padding: '4px 8px', width: '100%' }}
                    onChange={(e) => updateCell(row, { stage: e.target.value })}
                    disabled={!canEdit}
                  >
                    {meta.stages.map((stage) => (
                      <option key={stage} value={stage}>{stage}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    value={row.client_feedback || '待反馈'}
                    style={{ width: '100%', padding: '4px 6px' }}
                    onChange={(e) => updateCell(row, { client_feedback: e.target.value })}
                    disabled={!canEdit}
                  >
                    {meta.client_feedbacks.map((feedback) => (
                      <option key={feedback} value={feedback}>{feedback}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    value={row.owner || ''}
                    style={{ width: '100%', padding: '4px 6px' }}
                    onChange={(e) => updateCell(row, { owner: e.target.value })}
                    disabled={!canEdit}
                  >
                    <option value="">未指定</option>
                    {row.owner && !users.some((user) => user.display_name === row.owner) && (
                      <option value={row.owner}>{row.owner}</option>
                    )}
                    {users.map((user) => (
                      <option key={user.id} value={user.display_name}>{user.display_name}</option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
            {visibleRows.length === 0 && (
              <tr><td colSpan={7} className="muted">暂无进程记录，请先从匹配报告的人工审核队列加入。</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {showAddModal && (
        <div className="modal-backdrop" role="presentation" onClick={() => setShowAddModal(false)}>
          <div className="modal-panel process-add-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title-row">
              <h3>添加进程</h3>
              <button className="secondary small" onClick={() => setShowAddModal(false)}>关闭</button>
            </div>
            <div className="process-add-filters">
              <input
                type="text"
                placeholder="搜索候选人"
                value={addCandidateFilter}
                onChange={(e) => setAddCandidateFilter(e.target.value)}
              />
              <select value={addPositionFilter} onChange={(e) => setAddPositionFilter(e.target.value)}>
                <option value="">全部项目</option>
                {positions.map((position) => (
                  <option key={position.id} value={position.id}>
                    {position.seq_no ? `岗位${position.seq_no}：` : ''}{position.title}{position.company ? `（${position.company}）` : ''}
                  </option>
                ))}
              </select>
              <select value={addScoreFilter} onChange={(e) => setAddScoreFilter(e.target.value)}>
                <option value="">全部分数</option>
                <option value="review">80分及以上</option>
                <option value="no_review">低于80分/无分数</option>
              </select>
              <button onClick={searchReports} disabled={addSearching}>
                {addSearching ? '搜索中...' : '搜索'}
              </button>
            </div>
            {addMessage && <p className="muted">{addMessage}</p>}
            <div className="process-add-results">
              {addResults.map((item) => (
                <div className="process-add-result" key={item.result_id}>
                  <span>
                    <strong>{item.candidate_name} × {item.position_title}</strong>
                    <em>{item.company || '未指定客户'} · {item.final_score != null ? `${item.final_score}分` : '无可用分数'}</em>
                  </span>
                  <button
                    className="secondary small"
                    disabled={item.final_score == null}
                    onClick={() => addTrackingFromReport(item)}
                  >
                    加入进程
                  </button>
                </div>
              ))}
              {addResults.length === 0 && <p className="muted">请先搜索已有匹配报告，再从结果中加入进程。</p>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
