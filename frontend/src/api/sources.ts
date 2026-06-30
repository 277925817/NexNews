import type { CreateSourceRequest, SourceItem, UpdateSourceRequest } from '../types/source'
import { readDataEnvelope } from './http'

export type SourceAPIClient = {
  fetchSources: () => Promise<SourceItem[]>
  createSource: (request: CreateSourceRequest) => Promise<SourceItem>
  updateSource: (id: string, request: UpdateSourceRequest) => Promise<SourceItem>
  deleteSource: (id: string) => Promise<void>
}

export const sourceAPIClient: SourceAPIClient = {
  fetchSources() {
    return fetch('/api/sources').then((response) => readDataEnvelope<SourceItem[]>(response))
  },
  createSource(request: CreateSourceRequest) {
    return fetch('/api/sources', {
      body: JSON.stringify(request),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    }).then((response) => readDataEnvelope<SourceItem>(response))
  },
  updateSource(id: string, request: UpdateSourceRequest) {
    const encodedId = encodeURIComponent(id)
    return fetch(`/api/sources/${encodedId}`, {
      body: JSON.stringify(request),
      headers: { 'Content-Type': 'application/json' },
      method: 'PATCH',
    }).then((response) => readDataEnvelope<SourceItem>(response))
  },
  async deleteSource(id: string) {
    const encodedId = encodeURIComponent(id)
    const response = await fetch(`/api/sources/${encodedId}`, { method: 'DELETE' })
    if (!response.ok) {
      await readDataEnvelope<never>(response)
    }
  },
}
