/**
 * PDFs on their way to the server. Each gets a Library row straight away
 * (the filename as a placeholder title and byte progress) until the server's
 * PaperSummary replaces it. Kept outside React so an upload survives the
 * reader leaving the Library.
 */
import { create } from 'zustand'
import { isAbort, NetworkError, uploadPdf, type UploadResult } from '../../api/client'
import { qk } from '../../api/keys'
import { upsertPaperInCache } from '../../api/queries'
import { queryClient } from '../../api/queryClient'

export interface Upload {
  id: string
  name: string
  size: number
  /** 0..1 of the bytes sent. */
  fraction: number
  status: 'uploading' | 'failed'
  error?: string
  addedAt: string
  file: File
  controller: AbortController
}

interface UploadsState {
  uploads: Upload[]
}

export const useUploads = create<UploadsState>()(() => ({ uploads: [] }))

let seq = 0

const patch = (id: string, changes: Partial<Upload>) =>
  useUploads.setState((s) => ({ uploads: s.uploads.map((u) => (u.id === id ? { ...u, ...changes } : u)) }))

const drop = (id: string) => useUploads.setState((s) => ({ uploads: s.uploads.filter((u) => u.id !== id) }))

export interface UploadOutcome {
  /** The server's answer, when it took the file. status 200 = it was already in the library. */
  result?: UploadResult
  /** The server refused the file (413, 415): the row goes, the reason belongs in the hint line. */
  refused?: { message: string }
}

/** Start uploading a PDF; resolves once the server answered (or the reader cancelled). */
export async function startUpload(file: File): Promise<UploadOutcome> {
  const id = `upload-${++seq}`
  const controller = new AbortController()
  useUploads.setState((s) => ({
    uploads: [
      { id, name: file.name, size: file.size, fraction: 0, status: 'uploading', addedAt: new Date().toISOString(), file, controller },
      ...s.uploads,
    ],
  }))
  return run(id, file, controller)
}

async function run(id: string, file: File, controller: AbortController): Promise<UploadOutcome> {
  try {
    const result = await uploadPdf(file, (fraction) => patch(id, { fraction }), controller.signal)
    upsertPaperInCache(queryClient, result.summary)
    drop(id)
    void queryClient.invalidateQueries({ queryKey: qk.papers, exact: true })
    return { result }
  } catch (e) {
    if (isAbort(e)) {
      drop(id)
      return {}
    }
    if (e instanceof NetworkError) {
      // Keep the row so "Try again" can resend the same file.
      patch(id, { status: 'failed', error: 'The upload stopped partway: the server didn’t respond.' })
      return {}
    }
    drop(id)
    return { refused: { message: e instanceof Error && e.message ? e.message : 'The server refused that file.' } }
  }
}

export function cancelUpload(id: string): void {
  useUploads.getState().uploads.find((u) => u.id === id)?.controller.abort()
  drop(id)
}

export function retryUpload(id: string): Promise<UploadOutcome> | undefined {
  const upload = useUploads.getState().uploads.find((u) => u.id === id)
  if (!upload) return undefined
  const controller = new AbortController()
  patch(id, { status: 'uploading', fraction: 0, error: undefined, controller })
  return run(id, upload.file, controller)
}

export function removeFailedUpload(id: string): void {
  drop(id)
}
