export type FetchFrequency = 'manual' | 'hourly' | 'twice_daily' | 'daily'

export type SourceItem = {
  id: string
  name: string
  rss_url: string
  is_enabled: boolean
  fetch_frequency: FetchFrequency
  created_at: string
}

export type CreateSourceRequest = {
  name: string
  rss_url: string
}

export type UpdateSourceRequest = {
  is_enabled: boolean
}
