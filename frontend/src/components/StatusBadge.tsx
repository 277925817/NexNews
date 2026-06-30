import type { NewsStatus } from '../types/news'

const STATUS_LABELS: Record<NewsStatus, string> = {
  ready: '翻译中',
  translated: '已翻译',
  translation_failed: '翻译失败',
}

type StatusBadgeProps = {
  status: NewsStatus
}

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${status}`}>
      {STATUS_LABELS[status]}
    </span>
  )
}
