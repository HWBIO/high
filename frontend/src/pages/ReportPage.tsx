import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { DetailItem, Report } from '../types'
import DetailMatchTable from '../components/DetailMatchTable'
import SuggestionList from '../components/SuggestionList'
import { useMatchConfig } from '../utils/matchConfig'
import { canGenerateClientReports, canManageProcess } from '../utils/authz'
import { notifyDataUpdated } from '../utils/dataEvents'

function safeExportFilename(title: string, suffix: string) {
  const clean = title.replace(/[\\/:*?"<>|\s]+/g, '_').replace(/^_+|_+$/g, '')
  return `${clean || 'match_report'}${suffix}`
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
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

function detailHeading(candidateName: string, detail: DetailItem) {
  return `候选人：${detail.candidate_name || candidateName}｜${detail.seq_no ? `岗位${detail.seq_no}：` : ''}${detail.title}`
}

export default function ReportPage() {
  const canEditProcess = canManageProcess()
  const canGenerateClientReport = canGenerateClientReports()
  const { resultId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  const [actionMessage, setActionMessage] = useState('')
  const [exportMessage, setExportMessage] = useState('')
  const matchConfig = useMatchConfig()

  useEffect(() => {
    const loadReport = () => {
      api
        .get<Report>(`/api/match/results/${resultId}/report`)
        .then(setReport)
        .catch((e) => setError(String(e.message)))
    }
    loadReport()
    window.addEventListener('focus', loadReport)
    return () => window.removeEventListener('focus', loadReport)
  }, [resultId])

  if (error) return <p className="error">{error}</p>
  if (!report) return <p className="muted">加载中…</p>

  const reviewItems = report.details.filter((item) => item.final_score >= matchConfig.review_threshold)
  const clientReadyItems = report.details.filter((item) => item.final_score >= matchConfig.top_threshold)
  const singleDetail = resultId ? report.details[0] : null
  const reportTitle = singleDetail
    ? `${report.candidate_name}+${singleDetail.title}匹配报告`
    : `候选人「${report.candidate_name}」匹配报告`
  const backTarget = (location.state as { from?: string } | null)?.from || '/match/history'

  async function exportWord() {
    try {
      const { blob, filename } = await api.blob(`/api/match/results/${resultId}/export.docx`)
      downloadBlob(blob, filename || safeExportFilename(reportTitle, '.docx'))
      setExportMessage('已导出 Word 文件。')
    } catch (e) {
      setExportMessage(`导出 Word 失败：${String((e as Error).message || e)}`)
    }
  }

  async function exportPdf() {
    try {
      const { blob, filename } = await api.blob(`/api/match/results/${resultId}/export.pdf`)
      downloadBlob(blob, filename || safeExportFilename(reportTitle, '.pdf'))
      setExportMessage('已导出 PDF 文件。')
    } catch (e) {
      setExportMessage(`导出 PDF 失败：${String((e as Error).message || e)}`)
    }
  }

  async function createTracking(item: DetailItem, mode: 'recommend' | 'abandon') {
    setActionMessage('')
    try {
      const created = await api.post<{ id: number; existed?: boolean }>('/api/tracking', {
        candidate_id: report!.candidate_id,
        position_id: item.position_id,
        match_result_id: resultId ? Number(resultId) : null,
        final_score_snapshot: item.final_score,
        recommend_level_snapshot: item.recommend_level || '',
        source: '匹配报告',
        stage: mode === 'recommend' ? '初筛（已推荐客户）' : 'pass',
        client_feedback: mode === 'recommend' ? '待反馈' : '已拒绝',
        next_action: mode === 'recommend' ? '等待客户反馈' : '已放弃推荐',
      })
      if (mode === 'abandon') {
        await api.put(`/api/tracking/${created.id}`, {
          stage: 'pass',
          reject_reason: '顾问放弃',
          client_feedback: '已拒绝',
          next_action: '不再推进',
        })
      }
      notifyDataUpdated('tracking')
      setActionMessage(mode === 'recommend' ? '已加入进程管理' : '已标记为放弃推荐')
    } catch (e) {
      setActionMessage(String((e as Error).message))
    }
  }

  async function openResumeAttachment() {
    if (!report?.candidate_id) return
    setExportMessage('')
    try {
      const { blob } = await api.blob(`/api/candidates/${report.candidate_id}/resume`)
      openBlobInNewTab(blob)
    } catch (e) {
      setExportMessage(`打开原简历附件失败：${String((e as Error).message || e)}`)
    }
  }

  return (
    <div className="report-page">
      <div className="row report-page-toolbar" style={{ justifyContent: 'space-between' }}>
        <div className="row" style={{ marginBottom: 0 }}>
          <button className="back-icon-button" onClick={() => navigate(backTarget)} aria-label="返回" title="返回">
            ←
          </button>
          <h2 style={{ marginBottom: 0 }}>{reportTitle}</h2>
        </div>
        <div className="row" style={{ marginBottom: 0 }}>
          <button className="secondary" onClick={exportWord}>
            导出 Word
          </button>
          <button className="secondary" onClick={exportPdf}>
            导出 PDF
          </button>
        </div>
      </div>
      {exportMessage && <p className="muted report-export-message">{exportMessage}</p>}
      <div className="report-export-area">
        <h2 className="report-print-title">{reportTitle}</h2>
        <p className="muted">
          模型 {report.model} · {report.total_tokens} tokens · {report.finished_at}
        </p>

      <div className="report-hero card">
        <div>
          <p className="report-eyebrow">匹配报告 · 基于本次运行 #{report.run_id}</p>
          <h2>{singleDetail ? `${report.candidate_name} × ${singleDetail.title}` : '把顾问自然语言要求转成可审核的匹配规则'}</h2>
        </div>
        <div className="metric-grid">
          <div className="metric-card">
            <strong>{singleDetail ? 1 : report.details.length}</strong>
            <span>{singleDetail ? '本份报告岗位' : '匹配岗位'}</span>
          </div>
          <div className="metric-card">
            <strong>{reviewItems.length}</strong>
            <span>进入人工审核</span>
          </div>
          <div className="metric-card">
            <strong>{clientReadyItems.length}</strong>
            <span>可推荐客户</span>
          </div>
        </div>
      </div>

      {report.details.length > 0 && (
        <div className="card">
          <h3>一、岗位逐项匹配明细</h3>
          {report.details.map((d) => (
            <div key={d.position_id} style={{ marginBottom: 32 }}>
              <h3>
                {detailHeading(report.candidate_name, d)}
                {d.company && <span className="muted">（{d.company}）</span>}
              </h3>
              {d.score_summary && (
                <div className="score-summary-card">
                  {d.score_summary.dimensions ? (
                    <>
                      {(d.score_summary.blocker?.total || 0) > 0 && (
                        <p>
                          <strong>关键门槛：{d.score_summary.blocker?.score ?? 0}分</strong>
                          <span className="muted">
                            （满足 {d.score_summary.blocker?.satisfied || 0}/{d.score_summary.blocker?.total || 0} 条，
                            不满足 {d.score_summary.blocker?.failed || 0} 条，待确认 {d.score_summary.blocker?.pending || 0} 条）
                          </span>
                        </p>
                      )}
                      {d.score_summary.dimensions.map((dim) => (
                        <p key={dim.label}>
                          <strong>{dim.label || '评分维度'}：{dim.score ?? 0}分</strong>
                          <span className="muted">
                            （满足 {dim.satisfied || 0}/{dim.total || 0} 条，
                            不满足 {dim.failed || 0} 条，待确认 {dim.pending || 0} 条）
                          </span>
                        </p>
                      ))}
                      <p><strong>置信度：{d.score_summary.confidence ?? 0}分</strong></p>
                    </>
                  ) : (
                    <>
                      <p>
                        <strong>硬性条件匹配得分：{d.score_summary.hard?.score ?? 0}分</strong>
                        <span className="muted">
                          （满足 {d.score_summary.hard?.satisfied || 0}/{d.score_summary.hard?.total || 0} 条，
                          不满足 {d.score_summary.hard?.failed || 0} 条，待确认 {d.score_summary.hard?.pending || 0} 条）
                        </span>
                      </p>
                      <p>
                        <strong>优先条件匹配得分：{d.score_summary.priority?.score ?? d.score_summary.soft?.score ?? 0}分</strong>
                        <span className="muted">
                          （满足 {d.score_summary.priority?.satisfied ?? d.score_summary.soft?.satisfied ?? 0}/{d.score_summary.priority?.total ?? d.score_summary.soft?.total ?? 0} 条，
                          不满足 {d.score_summary.priority?.failed ?? d.score_summary.soft?.failed ?? 0} 条，待确认 {d.score_summary.priority?.pending ?? d.score_summary.soft?.pending ?? 0} 条）
                        </span>
                      </p>
                    </>
                  )}
                  <p>结论：{d.score_summary.judgement || '需结合客户接受度判断'}</p>
                </div>
              )}
              <DetailMatchTable rows={d.rows} />
              <p style={{ margin: '12px 0' }}>
                <span className="score">综合参考得分：约{d.final_score}分</span>
                {d.recommend_label && (
                  <span className={`badge badge-${d.recommend_level}`}>{d.recommend_label}</span>
                )}
              </p>
              <SuggestionList suggestions={d.suggestions} />
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h3>二、人工审核队列</h3>
        <p className="muted">综合参考得分达到 {matchConfig.review_threshold} 分进入人工审核，达到 {matchConfig.top_threshold} 分建议推荐客户；真正推进前仍由有业务权限的人确认。</p>
        {actionMessage && <p className="muted">{actionMessage}</p>}
        {reviewItems.length > 0 ? (
          <table>
            <thead>
              <tr><th>候选人</th><th>岗位</th><th>公司</th><th>得分</th><th>建议动作</th></tr>
            </thead>
            <tbody>
              {reviewItems.map((item) => (
                <tr key={item.position_id}>
                  <td>{item.candidate_name || report.candidate_name}</td>
                  <td>{item.seq_no ? `岗位${item.seq_no}：` : ''}{item.title}</td>
                  <td>{item.company || '—'}</td>
                  <td><strong>{item.final_score}分</strong></td>
                  <td>
                    <span className={item.final_score >= matchConfig.top_threshold ? 'recommendation-pill recommendation-pill-green' : 'recommendation-pill'}>
                      {item.final_score >= matchConfig.top_threshold ? '建议推荐客户' : '进入人工审核'}
                    </span>
                    {item.final_score >= matchConfig.top_threshold && canGenerateClientReport && (
                      <button
                        className="text-link-button"
                        onClick={() => resultId ? navigate(`/client-reports/${resultId}`) : alert('请从匹配历史进入单份报告后生成客户推荐报告。')}
                      >
                        生成客户推荐报告
                      </button>
                    )}
                    {canEditProcess && (
                      <>
                        <button className="secondary small" onClick={() => createTracking(item, 'recommend')}>
                          加入进程管理
                        </button>{' '}
                        <button className="secondary small" onClick={() => createTracking(item, 'abandon')}>
                          放弃推荐
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">本次没有达到 80 分的岗位，暂不进入人工审核。</p>
        )}
      </div>

      {report.candidate.has_resume && (
        <div className="card">
          <h3>
            <button className="text-link-button" onClick={openResumeAttachment}>
              原简历附件
            </button>
          </h3>
        </div>
      )}
      </div>
    </div>
  )
}
