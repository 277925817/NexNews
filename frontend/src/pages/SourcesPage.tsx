import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'

import { APIResponseError } from '../api/http'
import type { SourceAPIClient } from '../api/sources'
import type { SourceItem } from '../types/source'
import { AppShell } from '../components/AppShell'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { SourceForm } from '../components/SourceForm'
import { SourceRow } from '../components/SourceRow'

type SourceLoadState = 'loading' | 'ready' | 'error'

type SourcesPageProps = {
  client: SourceAPIClient
  onRefresh: () => Promise<unknown>
}

function getAPIMessage(error: unknown, fallback: string) {
  return error instanceof APIResponseError ? error.message : fallback
}

function useSourceList(client: SourceAPIClient) {
  const [sources, setSources] = useState<SourceItem[]>([])
  const [loadState, setLoadState] = useState<SourceLoadState>('loading')
  const loadSources = useCallback(async () => {
    try {
      setSources(await client.fetchSources())
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [client])

  useEffect(() => {
    void loadSources()
  }, [loadSources])

  return { loadSources, loadState, setSources, sources }
}

function useSourceCreate(client: SourceAPIClient, loadSources: () => Promise<void>) {
  const [formBusy, setFormBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  async function createSource(name: string, rssUrl: string) {
    setFormBusy(true)
    setFormError(null)
    try {
      await client.createSource({ name, rss_url: rssUrl })
      await loadSources()
      return true
    } catch (error) {
      setFormError(getAPIMessage(error, '新增失败'))
      return false
    } finally {
      setFormBusy(false)
    }
  }
  return { createSource, formBusy, formError }
}

function useSourceRows(client: SourceAPIClient, setSources: Dispatch<SetStateAction<SourceItem[]>>) {
  const [busyId, setBusyId] = useState<string | null>(null)
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})
  async function toggleSource(source: SourceItem) {
    setBusyId(source.id)
    setRowErrors({})
    try {
      const updated = await client.updateSource(source.id, { is_enabled: !source.is_enabled })
      setSources((current) => current.map((item) => (item.id === updated.id ? updated : item)))
    } catch (error) {
      setRowErrors({ [source.id]: getAPIMessage(error, '更新失败') })
    } finally {
      setBusyId(null)
    }
  }
  async function deleteSource(source: SourceItem) {
    setBusyId(source.id)
    setRowErrors({})
    try {
      await client.deleteSource(source.id)
      setSources((current) => current.filter((item) => item.id !== source.id))
    } catch (error) {
      setRowErrors({ [source.id]: getAPIMessage(error, '删除失败') })
    } finally {
      setBusyId(null)
    }
  }
  return { busyId, deleteSource, rowErrors, toggleSource }
}

function useSourceRefresh(onRefresh: () => Promise<unknown>, loadSources: () => Promise<void>) {
  const [isRefreshing, setIsRefreshing] = useState(false)
  async function handleRefresh() {
    setIsRefreshing(true)
    try {
      await onRefresh()
      await loadSources()
    } finally {
      setIsRefreshing(false)
    }
  }
  return { handleRefresh, isRefreshing }
}

export function SourcesPage({ client, onRefresh }: SourcesPageProps) {
  const sourceList = useSourceList(client)
  const sourceCreate = useSourceCreate(client, sourceList.loadSources)
  const sourceRows = useSourceRows(client, sourceList.setSources)
  const refresh = useSourceRefresh(onRefresh, sourceList.loadSources)

  return (
    <AppShell isRefreshing={refresh.isRefreshing} onRefresh={refresh.handleRefresh}>
      <section className="sources-page" aria-label="RSS 信源配置">
        <h1>RSS 信源</h1>
        <SourceForm
          errorMessage={sourceCreate.formError}
          isSubmitting={sourceCreate.formBusy}
          onCreate={sourceCreate.createSource}
        />
        {sourceList.loadState === 'loading' ? <LoadingState count={2} /> : null}
        {sourceList.loadState === 'error' ? <ErrorState /> : null}
        {sourceList.loadState === 'ready' ? (
          <ul className="source-list">
            {sourceList.sources.map((source) => (
              <SourceRow
                errorMessage={sourceRows.rowErrors[source.id] ?? null}
                isBusy={sourceRows.busyId === source.id}
                key={source.id}
                onDelete={sourceRows.deleteSource}
                onToggle={sourceRows.toggleSource}
                source={source}
              />
            ))}
          </ul>
        ) : null}
      </section>
    </AppShell>
  )
}
