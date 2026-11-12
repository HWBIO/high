import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { MatchHistoryItem, PageResult } from '../types'
import { formatPositionLabel } from '../utils/positionLabel'
import { useMatchConfig } from '../utils/matchConfig'
import { readPageCache, writePageCache } from '../utils/pageCache'
import { notifyDataUpdated } from '../utils/dataEvents'
import { canStartMatch } from '../utils/authz'

const STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  detailing: '明细分析中',
  done: '已完成',
  done_with_errors: '部分完成',
  failed: '失败',
}

const HISTORY_ITEMS_CACHE_KEY = 'match-history:items'
const HISTORY_OPTIONS_CACHE_KEY = 'match-history:options'

interface HistoryOptions {
  positions: Array<{ id: number; seq_no: string | null; title: string; company: string | null }>
  candidates: string[]
}

function parseRunIds(value: string | null) {
  return new Set((value || '').split(',').map((id) => Number(id)).filter(Boolean))
}

export default function MatchHistoryPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const optionsCache = readPageCache<HistoryOptions>(HISTORY_OPTIONS_CACHE_KEY, { positions: [], candidates: [] })
  const [items, setItems] = useState<MatchHistoryItem[]>(() => readPageCache<MatchHistoryItem[]>(HISTORY_ITEMS_CACHE_KEY, []))
  const [optionPositions, setOptionPositions] = useState<HistoryOptions['positions']>(() => optionsCache.positions)
  const [optionCandidates, setOptionCandidates] = useState<string[]>(() => optionsCache.candidates)
  const [positionFilter, setPositionFilter] = useState(searchParams.get('position') || '')
  const [candidateFilter, setCandidateFilter] = useState(searchParams.get('candidate') || '')
  const [scoreFilter, setScoreFilter] = useState(searchParams.get('score') || '')
  const initialRecentRuns = searchParams.get('recent_runs') || ''
  const [recentRunsValue, setRecentRunsValue] = useState(initialRecentRuns)
  const [showHistorical, setShowHistorical] = useState(false)
  const [expandedVersions, setExpandedVersions] = useState<Record<number, MatchHistoryItem[]>>({})
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const limit = 50
  const matchConfig = useMatchConfig()
  const canDeleteHistory = canStartMatch()

  useEffect(() => {
    const recent = searchParams.get('recent_runs') || ''
    setRecentRunsValue(recent)
    if (recent) setShowHistorical(false)
  }, [searchParams])

  useEffect(() => {
    const next = new URLSearchParams()
    if (recentRunsValue) next.set('recent_runs', recentRunsValue)
    if (positionFilter) next.set('position', positionFilter)
    if (candidateFilter) next.set('candidate', candidateFilter)
    if (scoreFilter) next.set('score', scoreFilter)
    setSearchParams(next, { replace: true })
  }, [candidateFilter, positionFilter, recentRunsValue, scoreFilter, setSearchParams])

  function historyQuery(nextOffset = 0) {
    const params = new URLSearchParams()
    params.set('limit', String(limit))
    params.set('offset', String(nextOffset))
    if (positionFilter) params.set('position_id', positionFilter)
    if (candidateFilter) params.set('candidate_name', candidateFilter)
    if (scoreFilter) params.set('score_band', scoreFilter)
    return `/api/match/history-results?${params.toString()}`
  }

  const load = (nextOffset = 0, append = false) =>
    api
      .get<PageResult<MatchHistoryItem>>(historyQuery(nextOffset))
      .then((page) => {
        setTotal(page.total)
        setOffset(page.offset)
        setItems((prev) => {
          const next = append ? [...prev, ...page.items] : page.items
          writePageCache(HISTORY_ITEMS_CACHE_KEY, next)
          return next
        })
      })
      .catch((e) => setError(String(e.message)))

  useEffect(() => {
    load(0)
    const refresh = () => load(0)
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [positionFilter, candidateFilter, scoreFilter])

  useEffect(() => {
    const loadOptions = () => api
      .get<HistoryOptions>('/api/match/history-options')
      .then((data) => {
        writePageCache(HISTORY_OPTIONS_CACHE_KEY, data)
        setOptionPositions(data.positions)
        setOptionCandidates(data.candidates)
      })
      .catch((e) => setError(String(e.message)))
    loadOptions()
    window.addEventListener('focus', loadOptions)
    return () => window.removeEventListener('focus', loadOptions)
  }, [])

  const positions = useMemo(() => optionPositions.map((position) => ({
    id: position.id,
    label: formatPositionLabel(position),
  })), [optionPositions])

  const candidates = optionCandidates

  const filteredItems = items

  const scoreText = (item: MatchHistoryItem) => {
    if (item.final_score != null) return `${item.final_score}分`
    return ['failed', 'done_with_errors'].includes(item.status) ? '明细失败' : '未完成'
  }

  const reportTitle = (item: MatchHistoryItem) =>
    `${item.candidate_name}+${item.position_title}，综合参考得分为：${scoreText(item)}`
  const currentQuery = searchParams.toString()
  const historyPath = currentQuery ? `/match/history?${currentQuery}` : '/match/history'
  const recentRunIds = useMemo(() => parseRunIds(recentRunsValue), [recentRunsValue])
  const hasRecentRuns = recentRunIds.size > 0
  const recentItems = hasRecentRuns ? filteredItems.filter((item) => recentRunIds.has(item.run_id)) : []
  const historicalItems = hasRecentRuns ? filteredItems.filter((item) => !recentRunIds.has(item.run_id)) : filteredItems
  const canOpenReport = (item: MatchHistoryItem) => Boolean(item.result_id && item.final_score != null)

  function removeResultFromPage(resultId: number) {
    setItems((prev) => {
      const next = prev.filter((item) => item.result_id !== resultId)
      writePageCache(HISTORY_ITEMS_CACHE_KEY, next)
      return next
    })
    setExpandedVersions((prev) => {
      const next: Record<number, MatchHistoryItem[]> = {}
      Object.entries(prev).forEach(([key, rows]) => {
        const numericKey = Number(key)
        if (numericKey === resultId) return
        const kept = rows.filter((item) => item.result_id !== resultId)
        if (kept.length) next[numericKey] = kept
      })
      return next
    })
  }

  async function deleteHistoryResult(item: MatchHistoryItem) {
    const title = `${item.candidate_name}+${item.position_title}`
    const confirmed = window.confirm(`确认删除匹配报告「${title}」吗？删除后将从匹配历史、AI 工作台和飞书记录中移除。`)
    if (!confirmed) return
    try {
      await api.delete<{ ok: boolean }>(`/api/match/history-results/${item.result_id}`)
      const wasInMainList = items.some((row) => row.result_id === item.result_id)
      removeResultFromPage(item.result_id)
      if (wasInMainList) setTotal((prev) => Math.max(0, prev - 1))
      notifyDataUpdated('runs')
      load(0)
    } catch (e) {
      setError(String((e as Error).message || e))
    }
  }

  const renderTable = (rows: MatchHistoryItem[], emptyText: string) => (
    <table>
      <thead>
        <tr>
          <th>匹配报告</th>
          <th style={{ width: 120 }}>人工审核</th>
          <th style={{ width: 90 }}>状态</th>
          <th style={{ width: 90 }}>Tokens</th>
          <th style={{ width: 150 }}>时间</th>
          <th style={{ width: 132 }}>操作</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((item) => (
          <Fragment key={item.result_id}>
            <tr key={item.result_id}>
              <td>
                <strong>{reportTitle(item)}</strong>
                <div className="muted">
                  {item.company || '未指定公司'}{item.seq_no ? ` · 岗位${item.seq_no}` : ''}
                  {item.exclude_reason ? ` · ${item.exclude_reason}` : ''}
                </div>
                {(item.version_count || 0) > 1 && (
                  <button
                    className="text-link-button"
                    onClick={async () => {
                      if (expandedVersions[item.result_id]) {
                        setExpandedVersions((prev) => {
                          const next = { ...prev }
                          delete next[item.result_id]
                          return next
                        })
                        return
                      }
                      const page = await api.get<PageResult<MatchHistoryItem>>(`/api/match/history-results/${item.result_id}/versions`)
                      setExpandedVersions((prev) => ({ ...prev, [item.result_id]: page.items.slice(1) }))
                    }}
                  >
                    {expandedVersions[item.result_id] ? '收起历史版本' : `历史版本 ${Number(item.version_count) - 1} 条`}
                  </button>
                )}
              </td>
              <td>
                {item.final_score != null && item.final_score >= matchConfig.review_threshold ? (
                  <span className="label-chip label-match">进入人工审核</span>
                ) : ['done', 'done_with_errors'].includes(item.status) ? (
                  <span className="label-chip label-mismatch">不进入</span>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td>{STATUS_LABELS[item.status] || item.status}</td>
              <td>{item.total_tokens}</td>
              <td className="muted">{item.created_at}</td>
              <td>
                <div className="history-action-group">
                  {canOpenReport(item) ? (
                    <Link className="report-action-button" to={`/results/${item.result_id}`} state={{ from: historyPath }}>
                      查看
                    </Link>
                  ) : (
                    <button type="button" className="report-action-button disabled" disabled>
                      查看
                    </button>
                  )}
                  {canDeleteHistory && (
                    <button type="button" className="report-action-button" onClick={() => deleteHistoryResult(item)}>
                      删除
                    </button>
                  )}
                </div>
              </td>
            </tr>
            {expandedVersions[item.result_id]?.map((version) => (
              <tr key={version.result_id} className="history-version-row">
                <td>
                  <span className="muted">历史版本：</span>
                  <strong>{reportTitle(version)}</strong>
                </td>
                <td>{version.final_score != null && version.final_score >= matchConfig.review_threshold ? '进入人工审核' : '不进入'}</td>
                <td>{STATUS_LABELS[version.status] || version.status}</td>
                <td>{version.total_tokens}</td>
                <td className="muted">{version.created_at}</td>
                <td>
                  <div className="history-action-group">
                    {canOpenReport(version) ? (
                      <Link className="report-action-button" to={`/results/${version.result_id}`} state={{ from: historyPath }}>
                        查看
                      </Link>
                    ) : (
                      <button type="button" className="report-action-button disabled" disabled>
                        查看
                      </button>
                    )}
                    {canDeleteHistory && (
                      <button type="button" className="report-action-button" onClick={() => deleteHistoryResult(version)}>
                        删除
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </Fragment>
        ))}
        {rows.length === 0 && (
          <tr><td colSpan={6} className="muted">{emptyText}</td></tr>
        )}
      </tbody>
    </table>
  )

  return (
    <div>
      <div className="page-heading-row">
        <div className="page-heading-left">
          <button className="back-icon-button" onClick={() => navigate('/match')} aria-label="返回" title="返回">
            ←
          </button>
          <div className="folder-page-title">
            <h2>匹配历史</h2>
          </div>
        </div>
        <span className="muted">按候选人 × 岗位展开，每一行是一份匹配报告。</span>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="history-table-layout">
        <section className="card history-results-panel">
        <div className="row">
          <select value={positionFilter} onChange={(e) => setPositionFilter(e.target.value)}>
            <option value="" hidden>按岗位筛选</option>
            {positions.map((position) => (
              <option key={position.id} value={position.id}>{position.label}</option>
            ))}
          </select>
          <select value={candidateFilter} onChange={(e) => setCandidateFilter(e.target.value)}>
            <option value="" hidden>按人才筛选</option>
            {candidates.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <select value={scoreFilter} onChange={(e) => setScoreFilter(e.target.value)}>
            <option value="" hidden>按分数筛选</option>
            <option value="review">进入人工审核</option>
            <option value="no_review">不进入人工审核</option>
          </select>
          <button
            className="secondary"
            onClick={() => {
              setPositionFilter('')
              setCandidateFilter('')
              setScoreFilter('')
              setRecentRunsValue('')
              setSearchParams({}, { replace: true })
            }}
          >
            清空筛选
          </button>
          <span className="muted">当前已加载 {items.length} 条 / 共 {total} 条</span>
        </div>
        <p className="muted" style={{ marginTop: -4 }}>
          分数规则：&lt;{matchConfig.review_threshold}分不进入人工审核；{matchConfig.review_threshold}-{matchConfig.top_threshold - 1}分进入人工审核；≥{matchConfig.top_threshold}分建议推荐客户。
        </p>

        {hasRecentRuns ? (
          <>
            <div className="history-section-head">
              <h3>本次匹配结果</h3>
              <span className="muted">{recentItems.length} 条</span>
            </div>
            {renderTable(recentItems, '本次匹配暂无符合筛选条件的报告')}
            <div className="history-divider">
              <button className="secondary" onClick={() => setShowHistorical((value) => !value)}>
                {showHistorical ? '收起历史匹配报告' : '历史匹配报告'}
              </button>
              <span className="muted">共 {historicalItems.length} 条历史报告</span>
            </div>
            {showHistorical && renderTable(historicalItems, '暂无符合筛选条件的历史匹配报告')}
          </>
        ) : (
          renderTable(historicalItems, '暂无符合筛选条件的匹配报告')
        )}
        {items.length < total && (
          <button className="secondary" style={{ marginTop: 12 }} onClick={() => load(offset + limit, true)}>
            加载更多
          </button>
        )}
        </section>
      </div>
    </div>
  )
}
