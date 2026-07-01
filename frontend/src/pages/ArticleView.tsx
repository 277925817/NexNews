import { useCallback, useEffect, useState } from 'react'

import type { NewsAPIClient } from '../api/news'
import { APIResponseError } from '../api/http'
import type { NewsDetailItem } from '../types/news'
import { AppShell } from '../components/AppShell'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { ScoreBadge } from '../components/ScoreBadge'
import { SourceMarker } from '../components/SourceMarker'
import { StatusBadge } from '../components/StatusBadge'

type ArticleLoadState = 'loading' | 'ready' | 'not_found' | 'error'

type ArticleViewProps = {
  client: NewsAPIClient
  newsId: string
}

function renderContentParagraphs(content: string) {
  return content
    .split(/\n{2,}/)
    .filter(Boolean)
    .map((paragraph, index) => <p key={index}>{paragraph}</p>)
}

const UNREADABLE_DETAIL_TITLE = '摘要和正文暂不可用'
const READY_UNREADABLE_COPY = '翻译完成后将自动显示中文摘要和正文。'
const FAILED_UNREADABLE_COPY = '翻译失败，当前无法显示中文摘要和正文。'

export function ArticleView({ client, newsId }: ArticleViewProps) {
  const [detail, setDetail] = useState<NewsDetailItem | null>(null)
  const [loadState, setLoadState] = useState<ArticleLoadState>('loading')
  const [isRefreshing, setIsRefreshing] = useState(false)

  const loadDetail = useCallback(async () => {
    try {
      const nextDetail = await client.fetchNewsDetail(newsId)
      setDetail(nextDetail)
      setLoadState('ready')
    } catch (error) {
      setLoadState(error instanceof APIResponseError && error.status === 404 ? 'not_found' : 'error')
    }
  }, [client, newsId])

  useEffect(() => {
    void loadDetail()
  }, [loadDetail])

  useEffect(() => {
    if (detail?.status !== 'ready') {
      return
    }
    const pollTimer = window.setInterval(() => void loadDetail(), 3000)
    return () => window.clearInterval(pollTimer)
  }, [detail?.status, loadDetail])

  async function handleRefresh() {
    setIsRefreshing(true)
    try {
      await client.refreshHome()
      await loadDetail()
    } finally {
      setIsRefreshing(false)
    }
  }

  return (
    <AppShell isRefreshing={isRefreshing} onRefresh={handleRefresh}>
      <article className="article-view" data-news-id={newsId}>
        <a className="article-view__back" href="/" aria-label="返回新闻列表">
          <svg
            className="article-view__back-icon"
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
          >
            <path
              d="M15 18l-6-6 6-6"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </a>
        {loadState === 'loading' ? <LoadingState count={1} /> : null}
        {loadState === 'error' ? <ErrorState /> : null}
        {loadState === 'not_found' ? (
          <div className="article-view__not-found" role="alert">
            新闻不存在或不可展示
          </div>
        ) : null}
        {loadState === 'ready' && detail ? renderArticleContent(detail) : null}
      </article>
    </AppShell>
  )
}

function renderArticleContent(detail: NewsDetailItem) {
  const title = detail.status === 'translated' ? detail.title : detail.original_title

  return (
    <div className="article-view__content" data-status={detail.status}>
      <div className="article-view__meta">
        <SourceMarker sourceName={detail.source_name} />
        <time dateTime={detail.published_at}>{detail.published_at}</time>
        <ScoreBadge score={detail.score} />
        <StatusBadge status={detail.status} />
      </div>
      <h1>{title}</h1>
      <p className="article-view__original-title">{detail.original_title}</p>
      {detail.status === 'translated' && detail.summary_zh ? (
        <p className="article-view__summary">{detail.summary_zh}</p>
      ) : null}
      {detail.status === 'translated' && detail.content_zh ? (
        <div className="article-view__body">{renderContentParagraphs(detail.content_zh)}</div>
      ) : null}
      {detail.status === 'ready' ? (
        <div className="article-view__waiting" role="status">
          <strong className="article-view__state-title">{UNREADABLE_DETAIL_TITLE}</strong>
          <span className="article-view__state-label">翻译中</span>
          <p className="article-view__state-copy">{READY_UNREADABLE_COPY}</p>
        </div>
      ) : null}
      {detail.status === 'translation_failed' ? (
        <div className="article-view__failed" role="status">
          <strong className="article-view__state-title">{UNREADABLE_DETAIL_TITLE}</strong>
          <span className="article-view__state-label">翻译失败</span>
          <p className="article-view__state-copy">{FAILED_UNREADABLE_COPY}</p>
        </div>
      ) : null}
      {detail.status !== 'ready' ? (
        <a className="article-view__original-link" href={detail.original_url} target="_blank" rel="noreferrer">
          查看原文
        </a>
      ) : null}
    </div>
  )
}
