import { useState, type FormEvent } from 'react'

type SourceFormProps = {
  errorMessage: string | null
  isSubmitting: boolean
  onCreate: (name: string, rssUrl: string) => Promise<boolean>
}

function isPublicRSSURL(value: string) {
  return /^https?:\/\/[^/\s.]+\.[^\s]+/i.test(value)
}

export function SourceForm({ errorMessage, isSubmitting, onCreate }: SourceFormProps) {
  const [name, setName] = useState('')
  const [rssUrl, setRSSUrl] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const canSubmit = name.trim() !== '' && rssUrl.trim() !== '' && !isSubmitting

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!isPublicRSSURL(rssUrl)) {
      setLocalError('请输入合法的公开 RSS URL')
      return
    }
    setLocalError(null)
    const created = await onCreate(name.trim(), rssUrl.trim())
    if (created) {
      setName('')
      setRSSUrl('')
    }
  }

  return (
    <form className="source-form" onSubmit={handleSubmit}>
      <label>
        名称
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label>
        RSS URL
        <input value={rssUrl} onChange={(event) => setRSSUrl(event.target.value)} />
        {localError ? <span className="source-form__error">{localError}</span> : null}
        {errorMessage ? <span className="source-form__error">{errorMessage}</span> : null}
      </label>
      <button type="submit" disabled={!canSubmit}>
        {isSubmitting ? '新增中' : '新增'}
      </button>
    </form>
  )
}
