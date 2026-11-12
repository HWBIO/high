export function formatPositionLabel(position: {
  seq_no?: string | null
  title: string
  company?: string | null
}) {
  const prefix = position.seq_no ? `岗位${position.seq_no}：` : ''
  const companySuffix = position.company ? `（${position.company}）` : ''
  const title =
    position.company && position.title.endsWith(companySuffix)
      ? position.title
      : `${position.title}${companySuffix}`
  return `${prefix}${title}`
}
