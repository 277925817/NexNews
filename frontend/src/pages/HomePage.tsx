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

type HomePageProps = {
  client: NewsAPIClient
}

export function HomePage({ client }: HomePageProps) {
  const [homeData, setHomeData] = useState<HomeData | null>(null)
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [isRefreshing, setIsRefreshing] = useState(false)

  const loadHome = useCallback(async () => {
    try {
      const nextHomeData = await client.fetchHome()
      setHomeData(nextHomeData)
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [client])

  useEffect(() => {
    void loadHome()
  }, [loadHome])

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
        </div>
        <HighScoreList items={homeData?.top_ranked_news ?? []} />
      </section>
    </AppShell>
  )
}
