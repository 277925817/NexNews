import type { HomeData, NewsDetailItem, RefreshResponse } from '../types/news'
import { readDataEnvelope } from './http'

export type NewsAPIClient = {
  fetchHome: () => Promise<HomeData>
  fetchNewsDetail: (id: string) => Promise<NewsDetailItem>
  refreshHome: () => Promise<RefreshResponse>
}

export const newsAPIClient: NewsAPIClient = {
  fetchHome() {
    return fetch('/api/home').then((response) => readDataEnvelope<HomeData>(response))
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
