export type NewsStatus = 'ready' | 'translated' | 'untranslated' | 'translation_failed'

export type NewsItem = {
  id: string
  title: string
  original_title: string
  source_name: string
  original_url: string
  published_at: string
  score: number
  status: NewsStatus
}

export type NewsListItem = NewsItem & {
  summary_zh?: string
}

export type NewsDetailItem = NewsItem & {
  summary_zh?: string
  content_zh?: string
}

export type HomeData = {
  latest_news: NewsListItem[]
  top_ranked_news: NewsListItem[]
  next_cursor?: string
}

export type RefreshResponse = {
  refreshed_at: string | null
}
