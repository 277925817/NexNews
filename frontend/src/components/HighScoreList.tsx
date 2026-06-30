import type { NewsListItem } from '../types/news'
import { ScoreBadge } from './ScoreBadge'
import { SourceMarker } from './SourceMarker'
import { StatusBadge } from './StatusBadge'

type HighScoreListProps = {
  items: NewsListItem[]
}

export function HighScoreList({ items }: HighScoreListProps) {
  return (
    <aside className="high-score-list" aria-labelledby="high-score-title">
      <h2 id="high-score-title">Top 30 Days</h2>
      <ol className="high-score-list__items">
        {items.slice(0, 10).map((item, index) => {
          const title = item.status === 'translated' ? item.title : item.original_title

          return (
            <li className="high-score-list__item" key={item.id}>
              <a href={`/news/${item.id}`} data-rank-item data-status={item.status}>
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
