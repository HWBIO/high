import { DetailRow } from '../types'

const LABEL_CLASS: Record<string, string> = {
  满足: 'label-chip label-match',
  待确认: 'label-chip label-unknown',
  不满足: 'label-chip label-mismatch',
  完全匹配: 'label-chip label-match',
  '未提及/待确认': 'label-chip label-unknown',
  不匹配: 'label-chip label-mismatch',
}

function displayLabel(label: string) {
  if (label === '完全匹配') return '满足'
  if (label === '未提及/待确认') return '待确认'
  if (label === '不匹配') return '不满足'
  return label
}

function ConditionTable({
  title,
  rows,
  tailHeader,
}: {
  title: string
  rows: DetailRow[]
  tailHeader: string
}) {
  return (
    <div className="condition-table-block">
      <h4>{title}</h4>
    <table>
      <thead>
        <tr><th style={{ width: 56 }}>序号</th><th style={{ width: '30%' }}>条件</th><th>判断</th><th>依据</th><th>{tailHeader}</th></tr>
      </thead>
      <tbody>
        {rows.length > 0 ? (
          rows.map((r, i) => {
            const label = displayLabel(r.label)
            return (
              <tr key={i}>
                <td>{i + 1}</td>
                <td>{r.requirement}</td>
                <td><span className={LABEL_CLASS[label] || 'label-chip'}>{label}</span></td>
                <td>{r.note}</td>
                <td>{label === '满足' ? '—' : r.note}</td>
              </tr>
            )
          })
        ) : (
          <tr>
            <td>—</td>
            <td>未拆出条件</td>
            <td><span className="label-chip label-unknown">待确认</span></td>
            <td>输入信息不足</td>
            <td>需人工确认</td>
          </tr>
        )}
      </tbody>
    </table>
    </div>
  )
}

const CONDITION_GROUPS = [
  { key: 'blocker', aliases: ['blocker'], title: '一、关键门槛逐项匹配', tailHeader: '风险/待确认点' },
  { key: 'core', aliases: ['core', 'hard'], title: '二、核心能力逐项匹配', tailHeader: '风险/待确认点' },
  { key: 'relevance', aliases: ['relevance'], title: '三、相关经验逐项匹配', tailHeader: '亮点/缺口' },
  { key: 'seniority', aliases: ['seniority'], title: '四、资历/复杂度逐项匹配', tailHeader: '亮点/缺口' },
  { key: 'preference', aliases: ['preference', 'priority', 'soft'], title: '五、客户偏好逐项匹配', tailHeader: '亮点/缺口' },
  { key: 'risk', aliases: ['risk'], title: '六、风险可控度逐项匹配', tailHeader: '风险/待确认点' },
]

export default function DetailMatchTable({ rows }: { rows: DetailRow[] }) {
  const hasNewGroups = rows.some((r) => ['blocker', 'core', 'relevance', 'seniority', 'preference', 'risk'].includes(r.condition_type || ''))
  if (hasNewGroups) {
    return (
      <>
        {CONDITION_GROUPS.map((group) => {
          const groupRows = rows.filter((r) => group.aliases.includes(r.condition_type || ''))
          if (groupRows.length === 0) return null
          return <ConditionTable key={group.key} title={group.title} rows={groupRows} tailHeader={group.tailHeader} />
        })}
      </>
    )
  }
  const hardRows = rows.filter((r) => r.condition_type === 'hard')
  const priorityRows = rows.filter((r) => r.condition_type === 'priority' || r.condition_type === 'soft')
  return (
    <>
      <ConditionTable title="一、硬性条件逐项匹配" rows={hardRows} tailHeader="风险/待确认点" />
      <ConditionTable title="二、优先条件逐项匹配" rows={priorityRows} tailHeader="亮点/缺口" />
    </>
  )
}
