import { Suggestion } from '../types'

const TYPE_LABELS: Record<string, string> = {
  resume_add: '简历补充',
  resume_emphasize: '简历强调',
  clarify_with_candidate: '沟通确认',
}

export default function SuggestionList({ suggestions }: { suggestions: Suggestion[] }) {
  if (!suggestions.length) return null
  return (
    <div>
      <h3>专业简历优化沟通建议</h3>
      <ol style={{ paddingLeft: 20, fontSize: 14, lineHeight: 1.8 }}>
        {suggestions.map((s, i) => (
          <li key={i}>
            {TYPE_LABELS[s.type] && <strong>【{TYPE_LABELS[s.type]}】</strong>}
            {s.text}
          </li>
        ))}
      </ol>
    </div>
  )
}
