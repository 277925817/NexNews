import { useCallback, useEffect, useState } from 'react'

import type { NewsAPIClient } from '../api/news'
import type { HomeData } from '../types/news'
import { AppShell } from '../components/AppShell'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { HighScoreList } from '../components/HighScoreList'
import { LoadingState } from '../components/LoadingState'
import { NewsCard } from '../components/NewsCard'

type LoadState = 'loading' | 'ready' | 'error'
type LoadingMoreState = 'idle' | 'loading' | 'error'

type HomePageProps = {
  client: NewsAPIClient
}

const LOAD_MORE_THRESHOLD_PX = 280

function isNearPageBottom() {
  const root = document.documentElement
  return root.scrollHeight - (window.scrollY + window.innerHeight) <= LOAD_MORE_THRESHOLD_PX
}

function mergeUniqueLatestNews(current: HomeData, nextPage: HomeData): HomeData {
  const seenIds = new Set(current.latest_news.map((item) => item.id))
  const appendedNews = nextPage.latest_news.filter((item) => !seenIds.has(item.id))
  return {
    latest_news: [...current.latest_news, ...appendedNews],
    top_ranked_news: current.top_ranked_news,
    next_cursor: nextPage.next_cursor,
  }
}

export function HomePage({ client }: HomePageProps) {
  const [homeData, setHomeData] = useState<HomeData | null>(null)
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [loadingMoreState, setLoadingMoreState] = useState<LoadingMoreState>('idle')
  const [nextCursor, setNextCursor] = useState<string | undefined>()
  const [isRefreshing, setIsRefreshing] = useState(false)

  const loadHome = useCallback(async () => {
    try {
      const nextHomeData = await client.fetchHome()
      setHomeData(nextHomeData)
      setNextCursor(nextHomeData.next_cursor)
      setLoadingMoreState('idle')
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [client])

  const loadMoreHome = useCallback(async () => {
    if (!nextCursor || loadState !== 'ready' || loadingMoreState === 'loading') {
      return
    }
    setLoadingMoreState('loading')
    try {
      const nextPage = await client.fetchHome({ cursor: nextCursor })
      setHomeData((current) => (current ? mergeUniqueLatestNews(current, nextPage) : nextPage))
      setNextCursor(nextPage.next_cursor)
      setLoadingMoreState('idle')
    } catch {
      setLoadingMoreState('error')
    }
  }, [client, loadState, loadingMoreState, nextCursor])

  useEffect(() => {
    void loadHome()
  }, [loadHome])

  useEffect(() => {
    function handleScroll() {
      if (isNearPageBottom()) {
        void loadMoreHome()
      }
    }
    window.addEventListener('scroll', handleScroll)
    handleScroll()
    return () => window.removeEventListener('scroll', handleScroll)
  }, [loadMoreHome])

  async function handleRefresh() {
    setIsRefreshing(true)
    try {
      await client.refreshHome()
      await loadHome()
    } catch {
      setLoadState('error')
    } finally {
      setIsRefreshing(false)
    }
  }

  return (
    <AppShell isRefreshing={isRefreshing} onRefresh={handleRefresh}>
      <section className="home-grid" aria-label="首页新闻">
        <div className="news-feed">
          <h1>News Feed</h1>
          {loadState === 'loading' ? <LoadingState /> : null}
          {loadState === 'error' ? <ErrorState /> : null}
          {loadState === 'ready' && homeData?.latest_news.length === 0 ? <EmptyState /> : null}
          {loadState === 'ready' && homeData ? (
            <div className="news-feed__list">
              {homeData.latest_news.map((item) => (
                <NewsCard item={item} key={item.id} />
              ))}
            </div>
          ) : null}
          {loadState === 'ready' && loadingMoreState === 'loading' ? (
            <div className="load-more" aria-live="polite">
              加载中
            </div>
          ) : null}
          {loadState === 'ready' && loadingMoreState === 'error' ? (
            <div className="load-more load-more--error" role="alert">
              新闻加载失败
            </div>
          ) : null}
        </div>
        <HighScoreList items={homeData?.top_ranked_news ?? []} />
      </section>
    </AppShell>
  )
}
