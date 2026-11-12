import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { BatchRunStatus, ProjectCard, RunStatus } from '../types'
import ProgressPanel from '../components/ProgressPanel'
import { notifyDataUpdated, onDataUpdated } from '../utils/dataEvents'
import { formatPositionLabel } from '../utils/positionLabel'
import { canStartMatch, canUseAI, canViewReports } from '../utils/authz'
import { readPageCache, writePageCache } from '../utils/pageCache'

const MATCH_DRAFT_KEY = 'hh-match:match-run-draft'
const ACTIVE_MATCH_KEY = 'hh-match:active-run-ids'
const VIEWED_MATCH_RUNS_KEY = 'hh-match:viewed-run-ids'
const RESUME_DB_NAME = 'hh-match-resume-files'
const RESUME_STORE_NAME = 'files'
const MAX_RESUME_FILES = 10
const MAX_PROJECTS = 10
const MAX_MATCH_COUNT = 100
const MIN_TEXT_RESUME_LENGTH = 20
const ALLOWED_RESUME_EXTENSIONS = new Set(['pdf', 'docx', 'txt'])
const PROJECT_CACHE_KEY = 'match-run:projects'
const TERMINAL_RUN_STATUSES = new Set(['done', 'done_with_errors', 'failed'])
const SUCCESS_RUN_STATUSES = new Set(['done', 'done_with_errors'])

interface StoredResumeFile {
  id: string
  name: string
  type: string
  lastModified: number
  file: File
}

interface MatchDraft {
  selectedPositionIds?: number[]
}

interface ActiveMatch {
  run_ids: number[]
  result_ids: number[]
}

function readMatchDraft(): MatchDraft {
  try {
    return JSON.parse(window.localStorage.getItem(MATCH_DRAFT_KEY) || '{}') as MatchDraft
  } catch {
    return {}
  }
}

function readActiveMatch(): ActiveMatch {
  try {
    const value = JSON.parse(window.localStorage.getItem(ACTIVE_MATCH_KEY) || '[]') as number[] | Partial<ActiveMatch>
    if (Array.isArray(value)) return { run_ids: value, result_ids: [] }
    return {
      run_ids: Array.isArray(value.run_ids) ? value.run_ids.map(Number).filter(Boolean) : [],
      result_ids: Array.isArray(value.result_ids) ? value.result_ids.map(Number).filter(Boolean) : [],
    }
  } catch {
    return { run_ids: [], result_ids: [] }
  }
}

function readActiveRunIds() {
  return readActiveMatch().run_ids
}

function writeActiveMatch(runIds: number[], resultIds: number[]) {
  window.localStorage.setItem(ACTIVE_MATCH_KEY, JSON.stringify({ run_ids: runIds, result_ids: resultIds }))
}

function workspacePath(runIds: number[], resultIds: number[] = []) {
  const params = new URLSearchParams()
  if (runIds.length) params.set('run_ids', runIds.join(','))
  if (resultIds.length) {
    params.set('result_ids', resultIds.join(','))
    params.set('reused', '1')
  }
  return params.toString() ? `/match/runs?${params.toString()}` : '/match/runs'
}

function fileKey(file: File) {
  return `${file.name}-${file.lastModified}-${file.size}`
}

function fileExtension(file: File) {
  const name = file.name || ''
  const ext = name.includes('.') ? name.split('.').pop() || '' : ''
  return ext.toLowerCase()
}

function isAllowedResumeFile(file: File) {
  return ALLOWED_RESUME_EXTENSIONS.has(fileExtension(file))
}

function formatFileSize(size: number) {
  if (size < 1024) return `${size}B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 100 * 1024 ? 1 : 0)}KB`
  return `${(size / 1024 / 1024).toFixed(1)}MB`
}

function resumeFileMeta(file: File) {
  const ext = fileExtension(file)
  if (ext === 'pdf') return { label: 'PDF', kind: 'pdf' }
  if (ext === 'docx') return { label: 'DOCX', kind: 'docx' }
  return { label: 'TXT', kind: 'txt' }
}

function projectCity(position: ProjectCard) {
  return position.city || ''
}

function openResumeDb() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = window.indexedDB.open(RESUME_DB_NAME, 1)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(RESUME_STORE_NAME)) {
        db.createObjectStore(RESUME_STORE_NAME, { keyPath: 'id' })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function loadStoredResumeFiles() {
  const db = await openResumeDb()
  return new Promise<File[]>((resolve, reject) => {
    const tx = db.transaction(RESUME_STORE_NAME, 'readonly')
    const request = tx.objectStore(RESUME_STORE_NAME).getAll()
    request.onsuccess = () => {
      const rows = request.result as StoredResumeFile[]
      resolve(rows.map((row) => row.file instanceof File ? row.file : new File([row.file], row.name, {
        type: row.type,
        lastModified: row.lastModified,
      })))
    }
    request.onerror = () => reject(request.error)
    tx.oncomplete = () => db.close()
  })
}

async function saveStoredResumeFiles(files: File[]) {
  const db = await openResumeDb()
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction(RESUME_STORE_NAME, 'readwrite')
    const store = tx.objectStore(RESUME_STORE_NAME)
    store.clear()
    files.forEach((file) => {
      store.put({
        id: fileKey(file),
        name: file.name,
        type: file.type,
        lastModified: file.lastModified,
        file,
      })
    })
    tx.oncomplete = () => {
      db.close()
      resolve()
    }
    tx.onerror = () => {
      db.close()
      reject(tx.error)
    }
  })
}

async function clearStoredResumeFiles() {
  await saveStoredResumeFiles([])
}

export default function MatchRunPage() {
  const canRunMatch = canStartMatch()
  const canOpenAIWorkspace = canUseAI()
  const canOpenHistory = canViewReports()
  const navigate = useNavigate()
  const draft = readMatchDraft()
  const [positions, setPositions] = useState<ProjectCard[]>(() => readPageCache<ProjectCard[]>(PROJECT_CACHE_KEY, []))
  const [positionsLoaded, setPositionsLoaded] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set(draft.selectedPositionIds || []))
  const [resumeFiles, setResumeFiles] = useState<File[]>([])
  const [resumeFilesLoaded, setResumeFilesLoaded] = useState(false)
  const [resumeText, setResumeText] = useState('')
  const [projectKeyword, setProjectKeyword] = useState('')
  const [uploading, setUploading] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null)
  const [batchRunIds, setBatchRunIds] = useState<number[]>(readActiveRunIds())
  const [reusedResultIds, setReusedResultIds] = useState<number[]>(readActiveMatch().result_ids)
  const [lastRunsDone, setLastRunsDone] = useState(false)
  const [pollSummary, setPollSummary] = useState<BatchRunStatus | null>(null)
  const [autoOpenWorkspace, setAutoOpenWorkspace] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const isCheckingRef = useRef(false)

  const loadProjects = () => api.get<ProjectCard[]>('/api/projects').then((rows) => {
    writePageCache(PROJECT_CACHE_KEY, rows)
    setPositions(rows)
  })

  useEffect(() => {
    loadProjects().finally(() => setPositionsLoaded(true))
    const onFocus = () => {
      loadProjects().catch((e) => setError(String((e as Error).message)))
    }
    window.addEventListener('focus', onFocus)
    const unsubscribe = onDataUpdated(['positions', 'projects'], () => {
      loadProjects().catch((e) => setError(String((e as Error).message)))
    })
    return () => {
      window.removeEventListener('focus', onFocus)
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    const active = readActiveMatch()
    setReusedResultIds(active.result_ids)
    if (active.run_ids.length) {
      setBatchRunIds(active.run_ids)
      checkRuns(active.run_ids, { autoNavigate: false, resultIds: active.result_ids })
    }
  }, [])

  useEffect(() => {
    const refreshRuns = () => {
      const active = readActiveMatch()
      setReusedResultIds(active.result_ids)
      if (active.run_ids.length) {
        setBatchRunIds(active.run_ids)
        checkRuns(active.run_ids, { autoNavigate: false, resultIds: active.result_ids })
      }
    }
    window.addEventListener('focus', refreshRuns)
    const unsubscribe = onDataUpdated(['runs'], refreshRuns)
    return () => {
      window.removeEventListener('focus', refreshRuns)
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    loadStoredResumeFiles()
      .then((files) => setResumeFiles(files.slice(0, 10)))
      .catch((err) => setError(String((err as Error).message)))
      .finally(() => setResumeFilesLoaded(true))
  }, [])

  useEffect(() => {
    if (!resumeFilesLoaded) return
    saveStoredResumeFiles(resumeFiles).catch((err) => setError(String((err as Error).message)))
  }, [resumeFiles, resumeFilesLoaded])

  useEffect(() => {
    window.localStorage.setItem(
      MATCH_DRAFT_KEY,
      JSON.stringify({
        selectedPositionIds: Array.from(selectedIds),
      }),
    )
  }, [selectedIds])

  useEffect(() => {
    if (!positionsLoaded) return
    const valid = new Set(positions.map((p) => p.id))
    setSelectedIds((prev) => new Set(Array.from(prev).filter((id) => valid.has(id))))
  }, [positions, positionsLoaded])

  function addResumeFiles(files: File[]) {
    if (!files.length) return
    setUploading(true)
    setError('')
    try {
      const validFiles = files.filter(isAllowedResumeFile)
      if (validFiles.length < files.length) {
        setNotice('已忽略不支持的文件类型，请上传 PDF、DOCX 或 TXT。')
      }
      if (!validFiles.length) return
      setResumeFiles((prev) => {
        const next = new Map(prev.map((file) => [fileKey(file), file]))
        const before = next.size
        validFiles.forEach((file) => {
          if (next.size < MAX_RESUME_FILES) next.set(fileKey(file), file)
        })
        if (validFiles.length + before > MAX_RESUME_FILES) {
          setNotice(`一次最多上传 ${MAX_RESUME_FILES} 份简历，已自动保留前 ${MAX_RESUME_FILES} 份。`)
        }
        return Array.from(next.values())
      })
    } catch (err) {
      setError(String((err as Error).message))
    } finally {
      setUploading(false)
    }
  }

  function addTextResume() {
    const text = resumeText.trim()
    if (text.length < MIN_TEXT_RESUME_LENGTH) {
      setError(`文字简历至少需要 ${MIN_TEXT_RESUME_LENGTH} 字，请补充后再添加。`)
      return
    }
    if (resumeFiles.length >= MAX_RESUME_FILES) {
      setError(`一次最多上传 ${MAX_RESUME_FILES} 份简历，请先删除一份后再添加。`)
      return
    }
    const index = resumeFiles.filter((file) => fileExtension(file) === 'txt' && file.name.startsWith('文字简历_')).length + 1
    const file = new File([text], `文字简历_${index}_${Date.now()}.txt`, {
      type: 'text/plain',
      lastModified: Date.now(),
    })
    addResumeFiles([file])
    setResumeText('')
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    addResumeFiles(Array.from(e.target.files || []))
    e.target.value = ''
  }

  function handleDrop(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
    addResumeFiles(Array.from(e.dataTransfer.files || []))
  }

  function handleDragOver(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault()
    e.stopPropagation()
    if (!isDragOver) setIsDragOver(true)
  }

  function handleDragLeave(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
  }

  function finishRunCheck(
    summary: BatchRunStatus | null,
    options: { autoNavigate: boolean; resultIds?: number[] },
    message = '',
  ) {
    setPollSummary(summary)
    setLastRunsDone(true)
    setAutoOpenWorkspace(false)
    // 本轮已结束：清掉 active 标记，避免下次进入页面时被旧 run 状态占用
    window.localStorage.removeItem(ACTIVE_MATCH_KEY)
    if (message) setNotice(message)
    const doneIds = summary?.items
      .filter((item) => SUCCESS_RUN_STATUSES.has(item.status) && item.progress.valid_results > 0)
      .map((item) => item.run_id) || []
    const resultIds = options.resultIds ?? reusedResultIds
    if (options.autoNavigate && (doneIds.length || resultIds.length)) {
      navigate(workspacePath(doneIds, resultIds))
    }
  }

  async function fetchBatchStatus(runIds: number[]) {
    return api.post<BatchRunStatus>('/api/match/runs/status', { run_ids: runIds })
  }

  async function checkRuns(runIds: number[], options: { autoNavigate: boolean; resultIds?: number[] }) {
    if (isCheckingRef.current || !runIds.length) return
    isCheckingRef.current = true
    try {
      const summary = await fetchBatchStatus(runIds)
      setError('')
      setPollSummary(summary)
      const active = summary.items.find((item) => !TERMINAL_RUN_STATUSES.has(item.status)) || summary.items[summary.items.length - 1]
      setRunStatus(active || null)
      if (summary.running === 0) {
        const validDone = summary.items.filter((item) => SUCCESS_RUN_STATUSES.has(item.status) && item.progress.valid_results > 0).length
        const message = summary.failed || summary.partial_failed
          ? `本次匹配已结束，其中 ${validDone} 份有有效报告、${summary.partial_failed} 份部分完成、${summary.failed} 份失败。${validDone ? '已为你展示可用的匹配报告。' : '没有生成可用报告，请查看失败原因。'}`
          : ''
        finishRunCheck(summary, options, message)
      } else {
        setLastRunsDone(false)
      }
    } catch (err) {
      setError(`匹配状态刷新失败：${String((err as Error).message)}。可稍后重新进入页面或打开匹配历史查看已完成结果。`)
    } finally {
      isCheckingRef.current = false
    }
  }

  async function startRun() {
    if (resumeFiles.length === 0 || selectedIds.size === 0) return
    if (resumeFiles.length > MAX_RESUME_FILES || selectedIds.size > MAX_PROJECTS || resumeFiles.length * selectedIds.size > MAX_MATCH_COUNT) {
      setError(`一次最多执行 ${MAX_RESUME_FILES} 份简历 × ${MAX_PROJECTS} 个项目，请减少选择后再发起匹配。`)
      return
    }
    setError('')
    setNotice('')
    setBatchRunIds([])
    setPollSummary(null)
    setAutoOpenWorkspace(false)
    try {
      const form = new FormData()
      form.append('position_ids_json', JSON.stringify(Array.from(selectedIds)))
      for (const file of resumeFiles) {
        form.append('files', file)
      }
      const result = await api.postForm<{
        run_ids: number[]
        reused_result_ids: number[]
        reused_count: number
        warnings: string[]
        match_count: number
      }>('/api/match/batch-runs', form)
      const created = result.run_ids
      const reused = result.reused_result_ids || []
      if (result.warnings?.length) setNotice(result.warnings.join('；'))
      if (result.reused_count) {
        setNotice((current) => {
          const text = `已复用 ${result.reused_count} 份已有匹配报告，未重复消耗 token。`
          return current ? `${current}；${text}` : text
        })
      }
      setBatchRunIds(created)
      setReusedResultIds(reused)
      setAutoOpenWorkspace(true)
      setResumeFiles([])
      await clearStoredResumeFiles()
      setLastRunsDone(false)
      writeActiveMatch(created, reused)
      notifyDataUpdated('runs')
      if (created.length) {
        checkRuns(created, { autoNavigate: true, resultIds: reused })
      } else if (reused.length) {
        setLastRunsDone(true)
        window.localStorage.removeItem(ACTIVE_MATCH_KEY)
        navigate(workspacePath([], reused))
      }
    } catch (err) {
      setError(String((err as Error).message))
    }
  }

  function startNewMatch() {
    window.localStorage.removeItem(ACTIVE_MATCH_KEY)
    setBatchRunIds([])
    setReusedResultIds([])
    setPollSummary(null)
    setRunStatus(null)
    setAutoOpenWorkspace(false)
    setLastRunsDone(false)
    setError('')
    setNotice('')
  }

  function toggleAllProjects(checked: boolean) {
    if (!checked) {
      const visibleIds = new Set(filteredProjects.map((p) => p.id))
      setSelectedIds((current) => new Set(Array.from(current).filter((id) => !visibleIds.has(id))))
      return
    }
    const next = filteredProjects.slice(0, MAX_PROJECTS).map((p) => p.id)
    setSelectedIds(new Set(next))
    if (filteredProjects.length > MAX_PROJECTS) {
      setNotice(`一次最多选择 ${MAX_PROJECTS} 个项目，已自动选择前 ${MAX_PROJECTS} 个。`)
    }
  }

  async function dismissProject(projectId: number) {
    setError('')
    setSelectedIds((current) => {
      const next = new Set(current)
      next.delete(projectId)
      return next
    })
    setPositions((current) => current.filter((position) => position.id !== projectId))
    try {
      await api.put(`/api/positions/${projectId}`, { is_project: 0 })
      notifyDataUpdated('positions')
      notifyDataUpdated('projects')
      setNotice('已从重点项目移除，可在项目管理的全部岗位中重新加入。')
    } catch (e) {
      setError(String((e as Error).message || e))
      loadProjects().catch((err) => setError(String((err as Error).message || err)))
    }
  }

  const running = runStatus && !TERMINAL_RUN_STATUSES.has(runStatus.status)
  const hasStoredRuns = batchRunIds.length > 0
  const viewedRunIds = typeof window === 'undefined' ? '' : window.localStorage.getItem(VIEWED_MATCH_RUNS_KEY) || ''
  const currentRunIds = batchRunIds.join(',')
  const hasViewedCurrentRuns = !!currentRunIds && viewedRunIds === currentRunIds
  const shouldShowResultButton = hasStoredRuns && lastRunsDone
  const completedRunIds = pollSummary?.items
    .filter((item) => SUCCESS_RUN_STATUSES.has(item.status) && item.progress.valid_results > 0)
    .map((item) => item.run_id) || []
  const failedRuns = pollSummary?.items.filter((item) => item.status === 'failed') || []
  const partialRuns = pollSummary?.items.filter((item) => item.status === 'done_with_errors') || []
  const progressPct = pollSummary?.total ? Math.round(((pollSummary.done + pollSummary.failed) / pollSummary.total) * 100) : 0

  useEffect(() => {
    if (!batchRunIds.length || lastRunsDone || hasViewedCurrentRuns) return
    checkRuns(batchRunIds, { autoNavigate: autoOpenWorkspace, resultIds: reusedResultIds })
    const timer = window.setInterval(() => {
      checkRuns(batchRunIds, { autoNavigate: autoOpenWorkspace, resultIds: reusedResultIds })
    }, 3000)
    return () => window.clearInterval(timer)
  }, [batchRunIds, lastRunsDone, hasViewedCurrentRuns, autoOpenWorkspace, reusedResultIds])

  const matchCount = resumeFiles.length * selectedIds.size
  const filteredProjects = positions.filter((p) => {
    const q = projectKeyword.trim().toLowerCase()
    if (!q) return true
    return [p.title, p.company || '', p.seq_no || '', p.salary_range || '', p.city || '', p.industry || '']
      .join(' ')
      .toLowerCase()
      .includes(q)
  })
  const groupedProjects = filteredProjects.reduce<Record<string, ProjectCard[]>>((acc, position) => {
    const company = position.company || '未指定客户'
    acc[company] = [...(acc[company] || []), position]
    return acc
  }, {})
  return (
    <div>
      <div className="folder-page-title">
        <h2>发起匹配</h2>
      </div>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}

      <div className="card match-run-section">
        <h3>上传简历</h3>
        <p className="muted">
          一份文件等于一个人才；当前最多上传 10 份简历。当前已选择 {resumeFiles.length}/10 份。
        </p>
        <div className="resume-input-grid">
          <div className="resume-input-card">
            <label
              className={isDragOver ? 'resume-upload-zone drag-over' : 'resume-upload-zone'}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
              <input
                type="file"
                multiple
                accept=".pdf,.docx,.txt"
                onChange={handleUpload}
                disabled={uploading || resumeFiles.length >= 10}
              />
              <strong>{uploading ? '处理中…' : '点击选择文件，或把简历拖到这里'}</strong>
              <span>PDF / DOCX / TXT</span>
            </label>
          </div>
          <div className="resume-input-card">
            <div className="resume-text-zone">
              <textarea
                className="resume-textarea"
                value={resumeText}
                onChange={(e) => setResumeText(e.target.value)}
                placeholder="也可以直接粘贴一份简历文字。"
                disabled={resumeFiles.length >= MAX_RESUME_FILES}
              />
              <div className="resume-text-actions">
                <span className="muted">{resumeText.trim().length} 字</span>
                <button
                  type="button"
                  onClick={addTextResume}
                  disabled={resumeFiles.length >= MAX_RESUME_FILES}
                >
                  添加为一份简历
                </button>
              </div>
            </div>
          </div>
        </div>
        {resumeFiles.length > 0 && (
          <div className="uploaded-resume-list">
            {resumeFiles.map((file, index) => {
              const meta = resumeFileMeta(file)
              return (
                <div className="uploaded-resume-item" key={fileKey(file)}>
                  <span className={`resume-file-icon ${meta.kind}`}>{meta.label}</span>
                  <div className="resume-file-info">
                    <strong title={file.name}>{file.name}</strong>
                    <em>{meta.label} {formatFileSize(file.size)}</em>
                  </div>
                  <button
                    className="resume-remove-button"
                    aria-label={`删除 ${file.name}`}
                    onClick={() => {
                      setResumeFiles((rows) => rows.filter((_, fileIndex) => fileIndex !== index))
                    }}
                  >
                    ×
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="card match-run-section">
        <h3>选择项目</h3>
        <p className="muted">
          当前会按笛卡尔积执行匹配：已选 {selectedIds.size} 个项目 × {resumeFiles.length} 份简历 = {matchCount} 次匹配，最多 10 × 10。
        </p>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <input
            className="project-search-input"
            type="text"
            placeholder="关键词搜索项目/客户/城市/薪资"
            value={projectKeyword}
            onChange={(e) => setProjectKeyword(e.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={filteredProjects.length > 0 && filteredProjects.every((p) => selectedIds.has(p.id))}
              onChange={(e) => toggleAllProjects(e.target.checked)}
            />{' '}
            {filteredProjects.length > MAX_PROJECTS ? `选择当前前${MAX_PROJECTS}个项目` : '选择当前项目'}（{selectedIds.size}/10）
          </label>
        </div>
        <div className="project-group-list">
          {Object.entries(groupedProjects).map(([company, rows]) => (
            <section className="project-group" key={company}>
              <div className="project-group-head">
                <strong>{company}</strong>
                <span className="muted">{rows.length} 个项目</span>
              </div>
              <div className="project-check-grid">
                {rows.map((p) => (
                  <div className="project-check-card" key={p.id}>
                    <button
                      type="button"
                      className="project-remove-button"
                      aria-label={`从本页移除 ${formatPositionLabel(p)}`}
                      title={`从本页移除 ${formatPositionLabel(p)}`}
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        dismissProject(p.id)
                      }}
                    >
                      ×
                    </button>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(p.id)}
                      disabled={!selectedIds.has(p.id) && selectedIds.size >= 10}
                      onChange={(e) => {
                        e.stopPropagation()
                        const next = new Set(selectedIds)
                        e.target.checked ? next.add(p.id) : next.delete(p.id)
                        setSelectedIds(next)
                      }}
                    />
                    <span>
                      <strong>{formatPositionLabel(p)}</strong>
                      <em>{projectCity(p) || '城市待补充'}</em>
                      <em>{p.salary_range || '薪资待补充'}</em>
                    </span>
                  </div>
                ))}
              </div>
            </section>
          ))}
          {filteredProjects.length === 0 && (
            <p className="muted">{projectKeyword.trim() ? '没有符合关键词的重点项目，请清空关键词后重试。' : '暂无重点项目，请先在「项目管理」中加入项目。'}</p>
          )}
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          {(canRunMatch || shouldShowResultButton) && (
            <button
              onClick={() => {
                if (shouldShowResultButton) {
                  if (completedRunIds.length || reusedResultIds.length) {
                    navigate(workspacePath(completedRunIds, reusedResultIds))
                  } else {
                    setNotice('本次匹配没有生成可用报告，请查看下方失败原因。')
                  }
                } else {
                  startRun()
                }
              }}
              disabled={!shouldShowResultButton && (!canRunMatch || resumeFiles.length === 0 || selectedIds.size === 0 || !!running || matchCount > MAX_MATCH_COUNT)}
            >
              {shouldShowResultButton ? '查看结果' : running ? '匹配中…' : '发起匹配'}
            </button>
          )}
          {canRunMatch && shouldShowResultButton && (
            <button className="secondary" onClick={startNewMatch}>
              发起新匹配
            </button>
          )}
          {canOpenHistory && (
            <button
              className="secondary"
              onClick={() => navigate('/match/history')}
            >
              匹配历史
            </button>
          )}
          {canOpenAIWorkspace && (
            <button
              className="secondary"
              onClick={() => navigate('/match/runs')}
            >
              AI工作台
            </button>
          )}
        </div>
      </div>

      {matchCount > MAX_MATCH_COUNT && <p className="error">一次最多执行 {MAX_MATCH_COUNT} 次匹配，请减少简历或项目数量。</p>}
      {pollSummary && pollSummary.total > 0 && (
        <div className="card match-progress-summary">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <strong>本次匹配进度</strong>
            <span className="muted">{progressPct}%</span>
          </div>
          <div className="progress-bar"><div style={{ width: `${progressPct}%` }} /></div>
          <p className="muted">
            共 {pollSummary.total} 份，可用 {pollSummary.done} 份，其中部分完成 {pollSummary.partial_failed} 份，失败 {pollSummary.failed} 份，处理中 {pollSummary.running} 份。
            {pollSummary.running > 0 ? ' 系统每 3 秒自动刷新一次。' : ''}
          </p>
          {partialRuns.length > 0 && (
            <div className="failed-run-list">
              <strong>部分完成</strong>
              {partialRuns.map((item) => (
                <p className="notice" key={item.run_id}>
                  Run #{item.run_id}：有效报告 {item.progress.valid_results} 份，失败明细 {item.progress.failed_results} 份。{item.error || ''}
                </p>
              ))}
            </div>
          )}
          {failedRuns.length > 0 && (
            <div className="failed-run-list">
              <strong>失败原因</strong>
              {failedRuns.map((item) => (
                <p className="error" key={item.run_id}>
                  Run #{item.run_id}：{item.error || '未返回具体失败原因'}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
      {runStatus && !hasViewedCurrentRuns && <ProgressPanel status={runStatus} />}
    </div>
  )
}
