import type { NewsListItem } from '../types/news'
import { ScoreBadge } from './ScoreBadge'
import { SourceMarker } from './SourceMarker'
import { StatusBadge } from './StatusBadge'

type NewsCardProps = {
  item: NewsListItem
}

function buildArticleLinkLabel(item: NewsListItem) {
  const title = item.status === 'translated' ? item.title : item.original_title
  if (item.status === 'ready') {
    return `${title}，翻译中，摘要和正文暂不可用`
  }
  if (item.status === 'translation_failed') {
    return `${title}，翻译失败，摘要和正文暂不可用`
  }
  if (item.status === 'untranslated') {
    return `${title}，未翻译，中文摘要和正文暂不可用`
  }
  return `${title}，打开中文摘要和正文`
}

export function NewsCard({ item }: NewsCardProps) {
  const title = item.status === 'translated' ? item.title : item.original_title
  const hasSummary = item.status === 'translated' && Boolean(item.summary_zh)
  const linkLabel = buildArticleLinkLabel(item)

  return (
    <a className="news-card" href={`/news/${item.id}`} aria-label={linkLabel} data-news-card data-status={item.status}>
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
