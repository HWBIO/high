import { RunStatus } from '../types'

const STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  detailing: '逐项明细分析中…',
  done: '已完成',
  done_with_errors: '部分完成',
  failed: '失败',
}

export default function ProgressPanel({ status }: { status: RunStatus }) {
  const { progress } = status
  const pct =
    ['done', 'done_with_errors', 'failed'].includes(status.status)
      ? 100
      : status.status === 'detailing'
        ? 60
        : 5
  return (
    <div className="card">
      <div className="row">
        <strong>{STATUS_LABELS[status.status] || status.status}</strong>
        <span className="muted">
          {progress.total_positions} 个岗位 · 有效 {progress.valid_results} · 失败 {progress.failed_results} · 已消耗 {status.total_tokens} tokens
        </span>
      </div>
      <div className="progress-bar"><div style={{ width: `${pct}%` }} /></div>
      {status.error && <p className="error">{status.error}</p>}
    </div>
  )
}
