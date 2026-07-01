import type { NewsListItem } from '../types/news'
import { ScoreBadge } from './ScoreBadge'
import { SourceMarker } from './SourceMarker'
import { StatusBadge } from './StatusBadge'

type HighScoreListProps = {
  items: NewsListItem[]
}

function buildArticleLinkLabel(item: NewsListItem) {
  const title = item.status === 'translated' ? item.title : item.original_title
  if (item.status === 'ready') {
    return `${title}，翻译中，摘要和正文暂不可用`
  }
  if (item.status === 'translation_failed') {
    return `${title}，翻译失败，摘要和正文暂不可用`
  }
  return `${title}，打开中文摘要和正文`
}

export function HighScoreList({ items }: HighScoreListProps) {
  return (
    <aside className="high-score-list" aria-labelledby="high-score-title">
      <h2 id="high-score-title">Top 30 Days</h2>
      <ol className="high-score-list__items">
        {items.slice(0, 10).map((item, index) => {
          const title = item.status === 'translated' ? item.title : item.original_title
          const linkLabel = buildArticleLinkLabel(item)

          return (
            <li className="high-score-list__item" key={item.id}>
              <a href={`/news/${item.id}`} aria-label={linkLabel} data-rank-item data-status={item.status}>
                <span className="high-score-list__rank">{index + 1}</span>
                <span className="high-score-list__body">
                  <span className="high-score-list__title">{title}</span>
                  <span className="high-score-list__meta">
                    <SourceMarker sourceName={item.source_name} />
                    {item.status === 'translated' ? null : <StatusBadge status={item.status} />}
                  </span>
                </span>
                <ScoreBadge score={item.score} />
              </a>
            </li>
          )
        })}
      </ol>
    </aside>
  )
}
