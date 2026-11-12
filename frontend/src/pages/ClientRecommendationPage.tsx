import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { ClientRecommendation, DetailItem, Report } from '../types'
import { canGenerateClientReports } from '../utils/authz'

const valueOrPending = (value?: string | number | null) => {
  if (value === undefined || value === null || String(value).trim() === '') return '待确认'
  return String(value)
}

function inferGender(tags: string) {
  if (/女/.test(tags)) return '女'
  if (/男/.test(tags)) return '男'
  return '待确认'
}

function inferEmploymentType(tags: string) {
  if (/非外包/.test(tags)) return '非外包'
  if (/外包/.test(tags)) return '外包'
  return '待确认'
}

function buildReasons(detail: DetailItem) {
  const rows = detail.rows || []
  const matchedRows = rows.filter((row) => row.label === '满足' || row.label === '完全匹配')
  const matchedHard = matchedRows.filter((row) => ['blocker', 'core', 'hard'].includes(row.condition_type || ''))
  const matchedPriority = matchedRows.filter((row) => !['blocker', 'core', 'hard'].includes(row.condition_type || ''))
  const suggestions = detail.suggestions || []

  const reasons = [
    {
      title: '岗位核心要求匹配',
      text: matchedRows.length
        ? (matchedHard.length ? matchedHard : matchedRows).slice(0, 2).map((row) => row.note || row.requirement).join('；')
        : '候选人与目标岗位存在一定匹配基础，具体硬性条件建议顾问进一步确认。',
    },
    {
      title: '相关经验与能力基础',
      text: matchedPriority.slice(0, 2).map((row) => row.note || row.requirement).join('；')
        || matchedRows.slice(2, 4).map((row) => row.note || row.requirement).join('；')
        || '候选人具备支持目标岗位工作的相关经验，建议结合简历附件进一步核验。',
    },
    {
      title: '推荐前可补充确认点',
      text: suggestions.find((item) => item.type === 'resume_emphasize')?.text
        || suggestions.find((item) => item.type === 'clarify_with_candidate')?.text
        || '建议顾问补充确认候选人近期状态、薪资预期和到岗意愿。',
    },
  ]

  return reasons.filter((item) => item.text.trim()).slice(0, 4)
}

function openBlobInNewTab(blob: Blob) {
  const url = URL.createObjectURL(blob)
  const win = window.open(url, '_blank')
  if (!win) {
    URL.revokeObjectURL(url)
    throw new Error('浏览器阻止了简历预览窗口，请允许弹窗后重试')
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export default function ClientRecommendationPage() {
  const { resultId } = useParams()
  const navigate = useNavigate()
  const [report, setReport] = useState<Report | null>(null)
  const [recommendation, setRecommendation] = useState<ClientRecommendation | null>(null)
  const [draft, setDraft] = useState<ClientRecommendation | null>(null)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const canGenerate = canGenerateClientReports()

  async function openResumeAttachment(candidateId: number) {
    setMessage('')
    try {
      const { blob } = await api.blob(`/api/candidates/${candidateId}/resume`)
      openBlobInNewTab(blob)
    } catch (e) {
      setMessage(`打开原简历附件失败：${String((e as Error).message || e)}`)
    }
  }

  useEffect(() => {
    const loadReport = () => {
      api
        .get<Report>(`/api/match/results/${resultId}/report`)
        .then(async (reportData) => {
          setReport(reportData)
          try {
            const recommendationData = await api.get<ClientRecommendation>(`/api/match/results/${resultId}/client-recommendation`)
            setRecommendation(recommendationData)
            setDraft(recommendationData)
          } catch (e) {
            const message = String((e as Error).message || e)
            if (message.includes('客户推荐报告尚未生成')) {
              setRecommendation(null)
              setDraft(null)
              return
            }
            throw e
          }
        })
        .catch((e) => setError(String(e.message)))
    }
    loadReport()
    window.addEventListener('focus', loadReport)
    return () => window.removeEventListener('focus', loadReport)
  }, [resultId])

  const detail = report?.details[0]
  const reasons = useMemo(() => (detail ? buildReasons(detail) : []), [detail])

  if (error) return <p className="error">{error}</p>
  if (!report || !detail) return <p className="muted">加载中…</p>

  const candidate = report.candidate
  const fallbackFields: Record<string, string | number | null | undefined> = {
    推荐人选: report.candidate_name,
    推荐岗位: detail.title,
    性别: inferGender(candidate.tags),
    年龄: candidate.age,
    学历: '待确认',
    期望地点: candidate.city,
    目前状态: candidate.talent_status,
    目前薪酬: candidate.current_salary,
    期望薪酬: candidate.expected_salary,
    用工形式: inferEmploymentType(candidate.tags),
  }
  const fieldOrder = ['推荐人选', '推荐岗位', '性别', '年龄', '学历', '期望地点', '目前状态', '目前薪酬', '期望薪酬', '用工形式']
  const fields: Array<[string, string | number | null | undefined]> = fieldOrder.map((label) => [
    label,
    recommendation?.fields?.[label] || fallbackFields[label],
  ])
  const activeRecommendation = editing && draft ? draft : recommendation
  const displayReasons = activeRecommendation?.reasons?.length ? activeRecommendation.reasons : reasons
  const leaveReason = recommendation?.leave_reason || (candidate.tags.includes('离职原因') ? candidate.tags : '待顾问确认补充')
  const ps = recommendation?.ps || '如候选人有简历暂不便更新、JD 条款已线下确认等信息，请顾问在发送前补充。'

  function fallbackRecommendation(): ClientRecommendation {
    return {
      fields: Object.fromEntries(fieldOrder.map((label) => [label, valueOrPending(fallbackFields[label])])),
      reasons,
      leave_reason: leaveReason,
      ps,
      model: '',
      tokens: 0,
      is_ai: false,
      status: '草稿',
    }
  }

  async function regenerate() {
    setMessage('')
    setSaving(true)
    try {
      const path = recommendation
        ? `/api/match/results/${resultId}/client-recommendation/regenerate`
        : `/api/match/results/${resultId}/client-recommendation/generate`
      const data = await api.post<ClientRecommendation>(path)
      setRecommendation(data)
      setDraft(data)
      setEditing(false)
      setMessage(`已${recommendation ? '重新' : ''}生成并保存为 v${data.version || ''}`)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setSaving(false)
    }
  }

  async function saveEdit() {
    if (!draft) return
    setMessage('')
    setSaving(true)
    try {
      const data = await api.put<ClientRecommendation>(`/api/match/results/${resultId}/client-recommendation`, {
        fields: draft.fields,
        reasons: draft.reasons,
        leave_reason: draft.leave_reason,
        ps: draft.ps,
        status: draft.status || '草稿',
      })
      setRecommendation(data)
      setDraft(data)
      setEditing(false)
      setMessage(`已保存为 v${data.version || ''}`)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setSaving(false)
    }
  }

  function updateDraftField(label: string, value: string) {
    if (!draft) return
    setDraft({ ...draft, fields: { ...draft.fields, [label]: value } })
  }

  function updateDraftReason(index: number, key: 'title' | 'text', value: string) {
    if (!draft) return
    const next = [...draft.reasons]
    next[index] = { ...next[index], [key]: value }
    setDraft({ ...draft, reasons: next })
  }

  return (
    <div>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div className="row" style={{ marginBottom: 0 }}>
          <button className="back-icon-button" onClick={() => navigate(`/results/${resultId}`)} aria-label="返回" title="返回">
            ←
          </button>
          <h2 style={{ marginBottom: 0 }}>客户推荐报告</h2>
        </div>
        <div className="row" style={{ marginBottom: 0 }}>
          {canGenerate && (
            <>
              <button className="secondary" onClick={regenerate} disabled={saving}>{recommendation ? '重新生成 AI 版' : '生成 AI 版'}</button>
              {editing ? (
                <>
                  <button onClick={saveEdit} disabled={saving}>保存编辑</button>
                  <button className="secondary" onClick={() => { setDraft(recommendation); setEditing(false) }}>取消</button>
                </>
              ) : (
                <button className="secondary" onClick={() => { setDraft(recommendation || fallbackRecommendation()); setEditing(true) }}>编辑</button>
              )}
            </>
          )}
        </div>
      </div>
      <p className="muted">客户版初稿，发送前请人工核对事实与附件。{message}</p>

      <div className="client-report-page">
        <section className="client-report-card">
          <h1>{report.candidate_name} 推荐报告</h1>
          <p className="muted">
            给客户看的推荐文案初稿，不包含内部模型分数、审核过程和匹配流水。
            {recommendation?.is_ai ? ` AI 已生成 · ${recommendation.model}` : ' 当前为规则兜底版本'}
          </p>

          <div className="client-field-grid">
            {fields.map(([label, value]) => (
              <div key={label}>
                <span>{label}：</span>
                {editing && draft ? (
                  <input value={draft.fields[label] || valueOrPending(value)} onChange={(e) => updateDraftField(label, e.target.value)} />
                ) : (
                  <strong>{valueOrPending(value)}</strong>
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="client-report-card">
          <h3>推荐理由：</h3>
          <ol className="client-reason-list">
            {displayReasons.map((reason, index) => (
              <li key={`${reason.title}-${index}`}>
                {editing && draft ? (
                  <div className="client-reason-editor">
                    <input value={draft.reasons[index]?.title || ''} onChange={(e) => updateDraftReason(index, 'title', e.target.value)} />
                    <textarea value={draft.reasons[index]?.text || ''} onChange={(e) => updateDraftReason(index, 'text', e.target.value)} />
                  </div>
                ) : (
                  <>
                    <strong>{reason.title}：</strong>
                    {reason.text}
                  </>
                )}
              </li>
            ))}
          </ol>
          {recommendation?.error && <p className="muted">AI 生成失败，已使用规则兜底：{recommendation.error}</p>}
        </section>

        <section className="client-report-card">
          <h3>离职原因：</h3>
          {editing && draft ? (
            <textarea value={draft.leave_reason} onChange={(e) => setDraft({ ...draft, leave_reason: e.target.value })} />
          ) : (
            <p>{leaveReason}</p>
          )}
        </section>

        <section className="client-report-card">
          <h3>PS：</h3>
          {editing && draft ? (
            <textarea value={draft.ps} onChange={(e) => setDraft({ ...draft, ps: e.target.value })} />
          ) : (
            <p>{ps}</p>
          )}
        </section>

        <section className="client-report-card">
          <h3>附件简历</h3>
          {candidate.has_resume ? (
            <button className="resume-attachment-card" onClick={() => openResumeAttachment(candidate.id)}>
              <span className="resume-file-icon">PDF</span>
              <span>
                <strong>{report.candidate_name} 简历附件</strong>
                <em>点击预览或下载原始简历</em>
              </span>
              <b>查看</b>
            </button>
          ) : (
            <div className="resume-attachment-card disabled">
              <span className="resume-file-icon">PDF</span>
              <span>
                <strong>暂无简历附件</strong>
                <em>本次匹配未保留可打开的原简历附件</em>
              </span>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
