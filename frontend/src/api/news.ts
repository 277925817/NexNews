import type { HomeData, NewsDetailItem, RefreshResponse } from '../types/news'
import { readDataEnvelope } from './http'

export type FetchHomeOptions = {
  cursor?: string
  limit?: number
}

export type NewsAPIClient = {
  fetchHome: (options?: FetchHomeOptions) => Promise<HomeData>
  fetchNewsDetail: (id: string) => Promise<NewsDetailItem>
  refreshHome: () => Promise<RefreshResponse>
}

function homePath(options?: FetchHomeOptions) {
  const params = new URLSearchParams()
  if (options?.cursor) {
    params.set('cursor', options.cursor)
  }
  if (typeof options?.limit === 'number') {
    params.set('limit', String(options.limit))
  }
  const query = params.toString()
  return query ? `/api/home?${query}` : '/api/home'
}

export const newsAPIClient: NewsAPIClient = {
  fetchHome(options) {
    return fetch(homePath(options)).then((response) => readDataEnvelope<HomeData>(response))
  },
  fetchNewsDetail(id: string) {
    const encodedId = encodeURIComponent(id)
    return fetch(`/api/news/${encodedId}`).then((response) =>
      readDataEnvelope<NewsDetailItem>(response),
    )
  },
  refreshHome() {
    return fetch('/api/refresh', { method: 'POST' }).then((response) =>
      readDataEnvelope<RefreshResponse>(response),
    )
  },
}
