import type { SourceItem } from '../types/source'

type SourceRowProps = {
  source: SourceItem
  errorMessage: string | null
  isBusy: boolean
  onDelete: (source: SourceItem) => void
  onToggle: (source: SourceItem) => void
}

export function SourceRow({ source, errorMessage, isBusy, onDelete, onToggle }: SourceRowProps) {
  return (
    <li className="source-row">
      <span className="source-row__main">
        <strong>{source.name}</strong>
        <span>{source.rss_url}</span>
        {errorMessage ? <em>{errorMessage}</em> : null}
      </span>
      <span className={source.is_enabled ? 'source-row__status' : 'source-row__status source-row__status--off'}>
        {source.is_enabled ? '启用' : '停用'}
      </span>
      <span className="source-row__actions">
        <button type="button" disabled={isBusy} onClick={() => onToggle(source)}>
          {source.is_enabled ? '停用' : '启用'}
        </button>
        <button type="button" disabled={isBusy} onClick={() => onDelete(source)}>
          删除
        </button>
      </span>
    </li>
  )
}
