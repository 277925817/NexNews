import type { NewsListItem } from '../types/news'
import { ScoreBadge } from './ScoreBadge'
import { SourceMarker } from './SourceMarker'
import { StatusBadge } from './StatusBadge'

type NewsCardProps = {
  item: NewsListItem
}

export function NewsCard({ item }: NewsCardProps) {
  const title = item.status === 'translated' ? item.title : item.original_title
  const hasSummary = item.status === 'translated' && Boolean(item.summary_zh)

  return (
    <a className="news-card" href={`/news/${item.id}`} data-news-card data-status={item.status}>
      <span className="news-card__meta">
        <SourceMarker sourceName={item.source_name} />
        <time dateTime={item.published_at}>{item.published_at}</time>
      </span>
      <span className="news-card__title-row">
        <span className="news-card__title">{title}</span>
        <ScoreBadge score={item.score} />
      </span>
      {hasSummary ? (
        <span className="news-card__summary" data-summary-text>
          {item.summary_zh}
        </span>
      ) : (
        <span className="news-card__state">
          <StatusBadge status={item.status} />
        </span>
      )}
    </a>
  )
}
