type DataEnvelope<T> = {
  data: T
}

type ErrorEnvelope = {
  error?: {
    code?: string
    message?: string
  }
}

export class APIResponseError extends Error {
  status: number
  code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export async function readDataEnvelope<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let errorPayload: ErrorEnvelope = {}
    try {
      errorPayload = (await response.json()) as ErrorEnvelope
    } catch {
      errorPayload = {}
    }
    throw new APIResponseError(
      response.status,
      errorPayload.error?.message ?? `API request failed with ${response.status}`,
      errorPayload.error?.code,
    )
  }
  const payload = (await response.json()) as DataEnvelope<T>
  return payload.data
}
