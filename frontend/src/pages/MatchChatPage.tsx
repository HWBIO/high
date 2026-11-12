import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, auth } from '../api'
import { MatchHistoryItem, PageResult } from '../types'
import { useMatchConfig } from '../utils/matchConfig'
import { notifyDataUpdated, onDataUpdated } from '../utils/dataEvents'
import { formatPositionLabel } from '../utils/positionLabel'
import { canManageProcess } from '../utils/authz'

const VIEWED_MATCH_RUNS_KEY = 'hh-match:viewed-run-ids'
const AUTO_SUMMARY_PREFIX = '本次匹配综述：'

const STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  detailing: '明细分析中',
  done: '已完成',
  done_with_errors: '部分完成',
  failed: '失败',
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

interface ChatSession {
  session_id: number
  title: string
  run_ids: number[]
  result_id?: number | null
  result_ids?: number[]
  added_result_ids?: number[]
  hidden_result_ids?: number[]
  messages: Array<{ id: number; role: 'user' | 'assistant'; content: string; model: string; tokens: number; created_at: string }>
}

interface HistoryOptions {
  positions: Array<{ id: number; seq_no: string | null; title: string; company: string | null }>
  candidates: string[]
}

function parseRunIds(value: string | null) {
  return (value || '').split(',').map((id) => Number(id)).filter(Boolean)
}

function workspaceScopeKey(runIds: number[], resultId: number, resultIds: number[]) {
  if (resultId) return `result:${resultId}`
  if (runIds.length || resultIds.length) return `runs:${runIds.join(',')}|results:${resultIds.join(',')}`
  return 'recent'
}

function workspaceQuery(runIds: number[], resultId: number, resultIds: number[] = []) {
  const params = new URLSearchParams()
  if (runIds.length) params.set('run_ids', runIds.join(','))
  if (resultId) params.set('result_id', String(resultId))
  if (resultIds.length) params.set('result_ids', resultIds.join(','))
  return params.toString() ? `?${params.toString()}` : ''
}

function scoreText(item: MatchHistoryItem) {
  if (item.final_score != null) return `${item.final_score}分`
  return ['failed', 'done_with_errors'].includes(item.status) ? '明细失败' : '未完成'
}

function isAutoSummary(content: string) {
  return content.startsWith(AUTO_SUMMARY_PREFIX)
}

export default function MatchChatPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const runIdsParam = searchParams.get('run_ids') || ''
  const [items, setItems] = useState<MatchHistoryItem[]>([])
  const [selectedResultIds, setSelectedResultIds] = useState<Set<number>>(new Set())
  const contextInitializedRef = useRef('')
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [showFailedReports, setShowFailedReports] = useState(false)
  const [showAddReports, setShowAddReports] = useState(false)
  const [extraItems, setExtraItems] = useState<MatchHistoryItem[]>([])
  const [historyOptions, setHistoryOptions] = useState<HistoryOptions>({ positions: [], candidates: [] })
  const [addCandidateFilter, setAddCandidateFilter] = useState('')
  const [addPositionFilter, setAddPositionFilter] = useState('')
  const [addScoreFilter, setAddScoreFilter] = useState('')
  const [addSearchResults, setAddSearchResults] = useState<MatchHistoryItem[]>([])
  const [addSearching, setAddSearching] = useState(false)
  const [addError, setAddError] = useState('')
  const [actionMessage, setActionMessage] = useState('')
  const [error, setError] = useState('')
  const matchConfig = useMatchConfig()
  const canEditProcess = canManageProcess()
  const runIds = useMemo(() => parseRunIds(runIdsParam), [runIdsParam])
  const resultId = Number(searchParams.get('result_id') || 0)
  const resultIdsParam = searchParams.get('result_ids') || ''
  const queryResultIds = useMemo(() => parseRunIds(resultIdsParam), [resultIdsParam])
  const hasReusedReports = queryResultIds.length > 0 || searchParams.get('reused') === '1'
  const backTarget = searchParams.get('return_to') || '/match'
  const scopeKey = useMemo(() => workspaceScopeKey(runIds, resultId, queryResultIds), [resultId, runIds, queryResultIds])
  const [hiddenReports, setHiddenReports] = useState(() => ({
    scopeKey,
    ids: new Set<number>(),
  }))
  const [addedReports, setAddedReports] = useState(() => ({
    scopeKey,
    ids: new Set<number>(),
  }))
  const hiddenResultIds = hiddenReports.scopeKey === scopeKey ? hiddenReports.ids : new Set<number>()
  const addedResultIds = addedReports.scopeKey === scopeKey ? addedReports.ids : new Set<number>()

  useEffect(() => {
    setAddedReports({ scopeKey, ids: new Set<number>() })
    setHiddenReports({ scopeKey, ids: new Set<number>() })
    api
      .get<{ added_result_ids: number[]; hidden_result_ids: number[] }>(`/api/match/chat/workspace-state${workspaceQuery(runIds, resultId, queryResultIds)}`)
      .then((state) => {
        setAddedReports({ scopeKey, ids: new Set(state.added_result_ids || []) })
        setHiddenReports({ scopeKey, ids: new Set(state.hidden_result_ids || []) })
      })
      .catch((e) => setError(String((e as Error).message || e)))
  }, [queryResultIds, resultId, runIds, scopeKey])

  useEffect(() => {
    if (runIds.length) {
      const value = runIds.join(',')
      window.localStorage.setItem(VIEWED_MATCH_RUNS_KEY, value)
    }
    const loadRunData = () => {
      const query = resultId
        ? `/api/match/history-results?result_id=${resultId}&limit=1`
        : runIds.length
        ? `/api/match/history-results?run_ids=${encodeURIComponent(runIds.join(','))}&limit=200`
        : queryResultIds.length
        ? `/api/match/history-results?result_ids=${encodeURIComponent(queryResultIds.join(','))}&limit=200`
        : '/api/match/history-results?limit=30'
      api
        .get<PageResult<MatchHistoryItem>>(query)
        .then((page) => {
          setItems(page.items)
          const selectableIds = page.items
            .filter((item) => item.result_id && item.final_score != null && !hiddenResultIds.has(item.result_id))
            .map((item) => item.result_id)
          const addedSelectableIds = Array.from(new Set([...queryResultIds, ...addedResultIds])).filter((id) => !hiddenResultIds.has(id))
          const availableIds = Array.from(new Set([...selectableIds, ...addedSelectableIds])).slice(0, 30)
          const contextKey = `${runIds.join(',')}|${resultId}|${queryResultIds.join(',')}|${availableIds.join(',')}`
          if (contextInitializedRef.current !== contextKey) {
            contextInitializedRef.current = contextKey
            setSelectedResultIds(new Set(availableIds))
          } else {
            const availableSet = new Set(availableIds)
            setSelectedResultIds((current) => new Set(Array.from(current).filter((id) => availableSet.has(id))))
          }
          if (!runIds.length && !resultId) {
            contextInitializedRef.current = ''
          }
        })
        .catch((e) => setError(String(e.message)))
    }
    loadRunData()
    window.addEventListener('focus', loadRunData)
    const unsubscribe = onDataUpdated(['runs'], loadRunData)
    return () => {
      window.removeEventListener('focus', loadRunData)
      unsubscribe()
    }
  }, [addedResultIds, hiddenResultIds, queryResultIds, resultId, runIds])

  useEffect(() => {
    const ids = Array.from(new Set([...queryResultIds, ...addedResultIds]))
    if (!ids.length) {
      setExtraItems([])
      return
    }
    Promise.all(
      ids.map((id) => api.get<PageResult<MatchHistoryItem>>(`/api/match/history-results?result_id=${id}&limit=1`)),
    )
      .then((pages) => {
        const rows = pages.flatMap((page) => page.items)
        setExtraItems(rows)
        setSelectedResultIds((current) => {
          const next = new Set(current)
          rows
            .filter((item) => item.final_score != null && !hiddenResultIds.has(item.result_id))
            .forEach((item) => {
              if (next.size < 30) next.add(item.result_id)
            })
          return next
        })
      })
      .catch((e) => setError(String((e as Error).message || e)))
  }, [addedResultIds, hiddenResultIds, queryResultIds])

  useEffect(() => {
    if (!showAddReports || historyOptions.positions.length || historyOptions.candidates.length) return
    api
      .get<HistoryOptions>('/api/match/history-options')
      .then((data) => setHistoryOptions(data))
      .catch((e) => setAddError(String((e as Error).message || e)))
  }, [historyOptions.candidates.length, historyOptions.positions.length, showAddReports])

  useEffect(() => {
    const params = new URLSearchParams()
    if (runIds.length) params.set('run_ids', runIds.join(','))
    if (resultId) params.set('result_id', String(resultId))
    if (queryResultIds.length) params.set('result_ids', queryResultIds.join(','))
    const query = params.toString() ? `?${params.toString()}` : ''
    api
      .get<ChatSession>(`/api/match/chat/sessions/current${query}`)
      .then((session) => {
        setSessionId(session.session_id)
        setChatMessages(
          session.messages
            .filter((item) => !(item.role === 'assistant' && isAutoSummary(item.content)))
            .map((item) => ({ role: item.role, content: item.content })),
        )
      })
      .catch((e) => {
        const message = String((e as Error).message || e)
        if (message.includes('对话会话不存在')) {
          setSessionId(null)
          setChatMessages([])
          return
        }
        setError(message)
      })
  }, [queryResultIds, resultId, runIds])

  const allItems = useMemo(() => {
    const byId = new Map<number, MatchHistoryItem>()
    items.forEach((item) => byId.set(item.result_id, item))
    extraItems.forEach((item) => byId.set(item.result_id, item))
    return Array.from(byId.values())
  }, [extraItems, items])
  const visibleItems = allItems.filter((item) => !hiddenResultIds.has(item.result_id))
  const validItems = visibleItems.filter((item) => item.final_score != null)
  const best = validItems
    .filter((item) => item.final_score != null)
    .sort((a, b) => (b.final_score ?? -1) - (a.final_score ?? -1))[0]
  const candidateCount = new Set(visibleItems.map((item) => item.candidate_name)).size
  const projectCount = new Set(visibleItems.map((item) => item.position_id)).size
  const reviewCount = validItems.filter((item) => (item.final_score ?? 0) >= matchConfig.review_threshold).length
  const cautiousCount = validItems.filter((item) => (item.final_score ?? 0) < matchConfig.review_threshold).length
  const unavailableCount = visibleItems.length - validItems.length
  const summary = best
    ? `本次共有 ${candidateCount} 位人才 × ${projectCount} 个项目，当前报告列表 ${visibleItems.length} 份；其中 ${reviewCount} 份达到 ${matchConfig.review_threshold} 分，建议进入人工筛选；${cautiousCount} 份低于 ${matchConfig.review_threshold} 分，建议慎重考虑。当前最高分为「${best.candidate_name}」匹配「${best.position_title}」${scoreText(best)}。${unavailableCount ? `另有 ${unavailableCount} 份明细失败或不可用。` : ''}`
    : `本次匹配正在整理结果，系统会按人才 × 项目生成全部匹配报告。`
  const summaryText = hasReusedReports && best
    ? `${summary}部分人才 + 岗位已有匹配报告，已放入左侧报告列，可直接查看。`
    : summary
  const displayItems = visibleItems.sort((a, b) => {
    const scoreDiff = (b.final_score ?? -1) - (a.final_score ?? -1)
    return scoreDiff || b.result_id - a.result_id
  })
  const selectableItems = displayItems.filter((item) => item.result_id && item.final_score != null)
  const selectedIds = Array.from(selectedResultIds)
  const canOpenReport = (item: MatchHistoryItem) => Boolean(item.result_id && item.final_score != null)
  const failedItems = displayItems.filter((item) => !canOpenReport(item))

  function toggleResult(resultId: number) {
    setSelectedResultIds((current) => {
      const next = new Set(current)
      if (next.has(resultId)) next.delete(resultId)
      else if (next.size < 30) next.add(resultId)
      return next
    })
  }

  function removeReportFromWorkspace(reportId: number) {
    const currentIds = hiddenReports.scopeKey === scopeKey ? hiddenReports.ids : new Set<number>()
    const currentAddedIds = addedReports.scopeKey === scopeKey ? addedReports.ids : new Set<number>()
    const nextIds = new Set(currentIds)
    const nextAddedIds = new Set(currentAddedIds)
    nextIds.add(reportId)
    nextAddedIds.delete(reportId)
    setHiddenReports({ scopeKey, ids: nextIds })
    setAddedReports({ scopeKey, ids: nextAddedIds })
    api
      .put<{ added_result_ids: number[]; hidden_result_ids: number[] }>('/api/match/chat/workspace-state', {
        run_ids: runIds,
        result_id: resultId || undefined,
        result_ids: queryResultIds,
        added_result_ids: Array.from(nextAddedIds),
        hidden_result_ids: Array.from(nextIds),
      })
      .then((state) => {
        setAddedReports({ scopeKey, ids: new Set(state.added_result_ids || []) })
        setHiddenReports({ scopeKey, ids: new Set(state.hidden_result_ids || []) })
      })
      .catch((e) => setError(String((e as Error).message || e)))
    setSelectedResultIds((current) => {
      const next = new Set(current)
      next.delete(reportId)
      return next
    })
  }

  function addReportToWorkspace(item: MatchHistoryItem) {
    const currentAddedIds = addedReports.scopeKey === scopeKey ? addedReports.ids : new Set<number>()
    const nextAddedIds = new Set(currentAddedIds)
    nextAddedIds.add(item.result_id)
    setAddedReports((current) => {
      const ids = current.scopeKey === scopeKey ? current.ids : new Set<number>()
      return { scopeKey, ids: new Set(ids).add(item.result_id) }
    })
    const currentHiddenIds = hiddenReports.scopeKey === scopeKey ? hiddenReports.ids : new Set<number>()
    const nextHiddenIds = new Set(currentHiddenIds)
    nextHiddenIds.delete(item.result_id)
    setHiddenReports({ scopeKey, ids: nextHiddenIds })
    api
      .put<{ added_result_ids: number[]; hidden_result_ids: number[] }>('/api/match/chat/workspace-state', {
        run_ids: runIds,
        result_id: resultId || undefined,
        result_ids: queryResultIds,
        added_result_ids: Array.from(nextAddedIds),
        hidden_result_ids: Array.from(nextHiddenIds),
      })
      .then((state) => {
        setAddedReports({ scopeKey, ids: new Set(state.added_result_ids || []) })
        setHiddenReports({ scopeKey, ids: new Set(state.hidden_result_ids || []) })
      })
      .catch((e) => setError(String((e as Error).message || e)))
    setExtraItems((current) => {
      if (current.some((row) => row.result_id === item.result_id)) return current
      return [...current, item]
    })
    if (item.final_score != null) {
      setSelectedResultIds((current) => {
        if (current.has(item.result_id) || current.size >= 30) return current
        return new Set(current).add(item.result_id)
      })
    }
  }

  function selectReviewItems() {
    setSelectedResultIds(new Set(
      selectableItems
        .filter((item) => (item.final_score ?? 0) >= matchConfig.review_threshold)
        .slice(0, 30)
        .map((item) => item.result_id),
    ))
  }

  function selectBelowReviewItems() {
    setSelectedResultIds(new Set(
      selectableItems
        .filter((item) => (item.final_score ?? 0) < matchConfig.review_threshold)
        .slice(0, 30)
        .map((item) => item.result_id),
    ))
  }

  async function searchReportsToAdd() {
    setAddError('')
    setAddSearching(true)
    try {
      const params = new URLSearchParams()
      params.set('limit', '20')
      if (addCandidateFilter.trim()) params.set('candidate_name', addCandidateFilter.trim())
      if (addPositionFilter) params.set('position_id', addPositionFilter)
      if (addScoreFilter) params.set('score_band', addScoreFilter)
      const page = await api.get<PageResult<MatchHistoryItem>>(`/api/match/history-results?${params.toString()}`)
      setAddSearchResults(page.items)
    } catch (e) {
      setAddError(String((e as Error).message || e))
    } finally {
      setAddSearching(false)
    }
  }

  async function sendMessage() {
    const text = message.trim()
    if (!text || sending) return
    if (!selectedIds.length) {
      setError('请至少勾选 1 份报告作为 AI 对话上下文。')
      return
    }
    setError('')
    const nextMessages: ChatMessage[] = [...chatMessages, { role: 'user', content: text }, { role: 'assistant', content: '' }]
    const assistantIndex = nextMessages.length - 1
    setChatMessages(nextMessages)
    setMessage('')
    setSending(true)
    let streamStarted = false
    try {
      const resp = await fetch('/api/match/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${auth.token()}`,
        },
        body: JSON.stringify({
          run_ids: runIds,
          result_id: resultId || undefined,
          result_ids: queryResultIds,
          selected_result_ids: selectedIds,
          message: text,
        }),
      })
      if (!resp.ok || !resp.body) throw new Error(resp.statusText || 'stream failed')
      streamStarted = true
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullText = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split('\n\n')
        buffer = events.pop() || ''
        for (const event of events) {
          const line = event.split('\n').find((part) => part.startsWith('data: '))
          if (!line) continue
          const payload = JSON.parse(line.slice(6)) as { type: string; text?: string; message?: string; session_id?: number }
          if (payload.type === 'delta' && payload.text) {
            fullText += payload.text
            setChatMessages((current) =>
              current.map((item, index) => (index === assistantIndex ? { ...item, content: fullText } : item)),
            )
          } else if (payload.type === 'done' && payload.session_id) {
            setSessionId(payload.session_id)
          } else if (payload.type === 'error') {
            throw new Error(payload.message || 'stream error')
          }
        }
      }
      if (!fullText.trim()) {
        setChatMessages((current) =>
          current.map((item, index) => (index === assistantIndex ? { ...item, content: '我暂时没有拿到可用回答。' } : item)),
        )
      }
    } catch (e) {
      if (streamStarted) {
        setChatMessages((current) =>
          current.map((item, index) =>
            index === assistantIndex
              ? { ...item, content: `这次没有回答成功：${String((e as Error).message)}` }
              : item,
          ),
        )
        setSending(false)
        return
      }
      try {
        const data = await api.post<{ reply: string; model: string; tokens: number }>('/api/match/chat', {
          run_ids: runIds,
          result_id: resultId || undefined,
          result_ids: queryResultIds,
          selected_result_ids: selectedIds,
          message: text,
        })
        setChatMessages((current) =>
          current.map((item, index) => (index === assistantIndex ? { ...item, content: data.reply || '我暂时没有拿到可用回答。' } : item)),
        )
      } catch (fallbackError) {
        setChatMessages((current) =>
          current.map((item, index) =>
            index === assistantIndex
              ? { ...item, content: `这次没有回答成功：${String((fallbackError as Error).message || (e as Error).message)}` }
              : item,
          ),
        )
      }
    } finally {
      setSending(false)
    }
  }

  async function createTrackingFromReport(item: MatchHistoryItem) {
    if (!item.candidate_id || !item.position_id) return
    setActionMessage('')
    try {
      const result = await api.post<{ id: number; existed?: boolean }>('/api/tracking', {
        candidate_id: item.candidate_id,
        position_id: item.position_id,
        match_result_id: item.result_id,
        final_score_snapshot: item.final_score,
        recommend_level_snapshot: item.recommend_level || '',
        source: 'AI工作台',
        stage: '初筛（已推荐客户）',
        client_feedback: '待反馈',
        next_action: '等待客户反馈',
      })
      notifyDataUpdated('tracking')
      setActionMessage(result.existed ? '该人才在该岗位已有进程记录' : '已加入进程管理')
    } catch (e) {
      setActionMessage(String((e as Error).message || e))
    }
  }

  return (
    <div className="match-chat-page">
      <div className="page-heading-row match-chat-top">
        <div className="page-heading-left">
          <button className="back-icon-button" onClick={() => navigate(backTarget)} aria-label="返回" title="返回">
            ←
          </button>
          <div className="folder-page-title">
            <h2>匹配运行</h2>
          </div>
        </div>
        {sessionId && <span className="muted">会话 #{sessionId}</span>}
      </div>
      {error && <p className="error">{error}</p>}

      <div className="match-chat-workspace">
        <aside className="report-file-section">
          {actionMessage && <p className="muted">{actionMessage}</p>}
          <div className="report-context-toolbar">
            <strong>报告列表</strong>
            <button className="secondary small" onClick={() => setShowAddReports((value) => !value)}>
              添加报告
            </button>
            <button className="secondary small" onClick={() => setSelectedResultIds(new Set(selectableItems.slice(0, 30).map((item) => item.result_id)))}>
              全选
            </button>
            <button className="secondary small" onClick={selectReviewItems}>
              只选80分+
            </button>
            <button className="secondary small" onClick={selectBelowReviewItems}>
              只选80分-
            </button>
            <button className="secondary small" onClick={() => setSelectedResultIds(new Set())}>
              清空
            </button>
          </div>
          {showAddReports && (
            <div className="add-report-panel">
              {addError && <p className="error">{addError}</p>}
              <input
                type="text"
                list="match-chat-candidates"
                placeholder="搜索人才"
                value={addCandidateFilter}
                onChange={(e) => setAddCandidateFilter(e.target.value)}
              />
              <datalist id="match-chat-candidates">
                {historyOptions.candidates.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
              <select value={addPositionFilter} onChange={(e) => setAddPositionFilter(e.target.value)}>
                <option value="">全部岗位</option>
                {historyOptions.positions.map((position) => (
                  <option key={position.id} value={position.id}>{formatPositionLabel(position)}</option>
                ))}
              </select>
              <select value={addScoreFilter} onChange={(e) => setAddScoreFilter(e.target.value)}>
                <option value="">全部分数</option>
                <option value="review">达到人工审核线</option>
                <option value="no_review">低于人工审核线</option>
              </select>
              <button className="secondary small" onClick={searchReportsToAdd} disabled={addSearching}>
                {addSearching ? '搜索中…' : '搜索'}
              </button>
              <div className="add-report-results">
                {addSearchResults.map((item) => {
                  const isInWorkspace = !hiddenResultIds.has(item.result_id) && allItems.some((row) => row.result_id === item.result_id)
                  return (
                    <div className="add-report-result" key={item.result_id}>
                      <span>
                        <strong>{item.candidate_name}+{item.position_title}</strong>
                        <em>{item.company || '未指定客户'} · {scoreText(item)}</em>
                      </span>
                      <button
                        className="secondary small"
                        onClick={() => addReportToWorkspace(item)}
                        disabled={isInWorkspace || item.final_score == null}
                      >
                        {item.final_score == null ? '不可加入' : isInWorkspace ? '已在列表' : '加入'}
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          <div className="report-card-grid">
            {displayItems.filter((item) => canOpenReport(item)).map((item) => (
              <div
                className={`report-file-card ${selectedResultIds.has(item.result_id) ? 'selected' : ''}`}
                key={item.result_id}
              >
                <button
                  type="button"
                  className="report-card-remove-button"
                  aria-label={`从 AI 工作台移除 ${item.candidate_name}+${item.position_title}`}
                  title="从当前 AI 工作台移除"
                  onClick={() => removeReportFromWorkspace(item.result_id)}
                >
                  ×
                </button>
                <label className="report-card-check">
                  <input
                    type="checkbox"
                    checked={selectedResultIds.has(item.result_id)}
                    disabled={item.final_score == null}
                    onChange={() => toggleResult(item.result_id)}
                  />
                  <span>
                    <strong>{item.candidate_name}+{item.position_title}</strong>
                    <em>{item.company || '未指定客户'} · {scoreText(item)} · {STATUS_LABELS[item.status] || item.status}</em>
                  </span>
                </label>
                {canOpenReport(item) ? (
                  <div className="report-card-actions">
                    <Link
                      className="text-link-button"
                      to={`/results/${item.result_id}`}
                      state={{ from: `/match/runs?${searchParams.toString()}` }}
                    >
                      查看报告
                    </Link>
                    {canEditProcess && (
                      <button className="text-link-button" onClick={() => createTrackingFromReport(item)}>
                        加入进程
                      </button>
                    )}
                  </div>
                ) : (
                  <span className="muted">报告尚不可查看</span>
                )}
              </div>
            ))}
            {failedItems.length > 0 && (
              <div className="failed-report-fold">
                <button
                  type="button"
                  className="text-link-button"
                  onClick={() => setShowFailedReports((value) => !value)}
                >
                  {showFailedReports ? '收起失败结果' : `失败结果 ${failedItems.length} 条`}
                </button>
                {showFailedReports && failedItems.map((item) => (
                  <div className="report-file-card unavailable" key={item.result_id}>
                    <button
                      type="button"
                      className="report-card-remove-button"
                      aria-label={`从 AI 工作台移除 ${item.candidate_name}+${item.position_title}`}
                      title="从当前 AI 工作台移除"
                      onClick={() => removeReportFromWorkspace(item.result_id)}
                    >
                      ×
                    </button>
                    <strong>{item.candidate_name}+{item.position_title}</strong>
                    <em>{item.company || '未指定客户'} · {scoreText(item)} · {STATUS_LABELS[item.status] || item.status}</em>
                  </div>
                ))}
              </div>
            )}
            {items.length === 0 && <p className="muted">暂无报告，请稍后刷新。</p>}
            {items.length > 0 && displayItems.length === 0 && <p className="muted">当前 AI 工作台的报告已移除。</p>}
          </div>
        </aside>

        <section className="match-chat-dialog">
          <main className="chat-stream">
            <div className="chat-message ai">
              <p><strong>综述：</strong>{summaryText}</p>
            </div>

            {chatMessages.map((item, index) => (
              <div key={`${item.role}-${index}`} className={`chat-message ${item.role === 'user' ? 'user' : 'ai'}`}>
                <span>{item.role === 'user' ? '你' : 'AI'}</span>
                <p>{item.content}</p>
              </div>
            ))}
          </main>

          <div className="match-chat-input">
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault()
                  sendMessage()
                }
              }}
              placeholder="可以继续补充客户要求或让 AI 解释某份报告。"
            />
            <button className="chat-send-button" onClick={sendMessage} disabled={sending || !message.trim() || selectedResultIds.size === 0}>
              {sending ? '思考中…' : '发送'}
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}
