import { BACKEND_URL } from './config'
import { refreshAccessToken } from './tokenBroker'
import { getAuthMode } from './viewerAuth'

export interface DriveFile {
  id: string
  name: string
}

export interface SidecarMeta {
  id: string
  modifiedTime: string
}

export const FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'
const EBOOK_EXTENSIONS = ['.epub', '.kpub', '.cbz', '.cbr']
const WALK_CONCURRENCY = 8

// The Drive helpers throw a plain Error with one of these messages on an
// expired/invalid token — used to switch the UI into "reconnect" mode.
export function isAuthError(err: unknown): boolean {
  return (
    err instanceof Error &&
    /sign-in expired|invalid credentials|invalid authentication|\b401\b/i.test(err.message)
  )
}

export function isSupportedEbook(name: string): boolean {
  const lower = name.toLowerCase()
  return EBOOK_EXTENSIONS.some((ext) => lower.endsWith(ext))
}

export class DriveApiError extends Error {
  status: number
  reason?: string
  constructor(message: string, status: number, reason?: string) {
    super(message)
    this.status = status
    this.reason = reason
  }
}

// The changes sync token expired (Drive only keeps change history for a
// limited window). This — and only this — is legitimately fixed by a full
// tree rebuild; every other sync error should surface, not silently rebuild.
export class StalePageTokenError extends Error {}

async function driveFetch(token: string, path: string): Promise<unknown> {
  const response = await fetch(`https://www.googleapis.com/drive/v3/${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) {
    if (response.status === 401) throw new Error('Sign-in expired — sign in again.')
    let reason: string | undefined
    try {
      const body = (await response.json()) as {
        error?: { errors?: { reason?: string }[]; status?: string }
      }
      reason = body?.error?.errors?.[0]?.reason ?? body?.error?.status
    } catch {
      // no / non-JSON body
    }
    throw new DriveApiError(`Drive API error (${response.status})`, response.status, reason)
  }
  return response.json()
}

// The passcode-mode counterpart to driveFetch — same "throw a plain Error
// isAuthError() can recognise on a 401" contract, but against our own
// backend's /api/viewer/* proxy instead of Google. `path` is relative to
// that prefix, e.g. `/drive/tree`.
async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${BACKEND_URL}/api/viewer${path}`, {
    ...init,
    credentials: 'include',
  })
  if (response.status === 401) throw new Error('Sign-in expired — sign in again.')
  return response
}

// id + modifiedTime for a named file directly in a folder — the "is my
// cache stale" half of every bookbrain-*.json/.bin sidecar's read, shared
// by readJsonFile below and the several lib/*.ts files (dashboard.ts,
// news.ts, reading.ts, …) that do their own modifiedTime-gated caching
// around it. Branches on auth mode so every one of those callers gets the
// passcode-mode proxy for free.
export async function findSidecarMeta(
  token: string,
  folderId: string,
  name: string,
): Promise<SidecarMeta | null> {
  if (getAuthMode() === 'passcode') {
    const resp = await backendFetch(`/drive/sidecar-meta?name=${encodeURIComponent(name)}`)
    if (!resp.ok) throw new Error(`Backend error (${resp.status})`)
    return (await resp.json()) as SidecarMeta | null
  }
  const q = encodeURIComponent(`'${folderId}' in parents and name = '${name}' and trashed = false`)
  const data = (await driveFetch(
    token,
    `files?q=${q}&fields=files(id,modifiedTime)&pageSize=1`,
  )) as { files: SidecarMeta[] }
  return data.files[0] ?? null
}

// Raw bytes for a file id — the binary counterpart to fetchDriveBlob, for
// callers (embeddings.ts's .bin sidecar) that want an ArrayBuffer to decode
// rather than a Blob to hand to an <img>/foliate.
export async function fetchDriveBytes(token: string, fileId: string): Promise<ArrayBuffer> {
  return (await fetchDriveBlob(token, fileId)).arrayBuffer()
}

async function mapConcurrent<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length)
  let next = 0
  async function worker() {
    while (next < items.length) {
      const i = next++
      results[i] = await fn(items[i])
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) || 1 }, worker))
  return results
}

async function listChildren(token: string, folderId: string): Promise<{ files: DriveFile[]; folders: DriveFile[] }> {
  const files: DriveFile[] = []
  const folders: DriveFile[] = []
  let pageToken: string | undefined

  do {
    const query = encodeURIComponent(`'${folderId}' in parents and trashed=false`)
    const pageParam = pageToken ? `&pageToken=${pageToken}` : ''
    const data = (await driveFetch(
      token,
      `files?q=${query}&fields=nextPageToken,files(id,name,mimeType)&pageSize=1000${pageParam}`,
    )) as { files: { id: string; name: string; mimeType: string }[]; nextPageToken?: string }

    for (const f of data.files) {
      if (f.mimeType === FOLDER_MIME_TYPE) folders.push({ id: f.id, name: f.name })
      else if (isSupportedEbook(f.name)) files.push({ id: f.id, name: f.name })
    }
    pageToken = data.nextPageToken
  } while (pageToken)

  return { files, folders }
}

// Breadth-first, with every folder at the current depth fetched concurrently
// (capped at WALK_CONCURRENCY) instead of one Drive API round-trip at a
// time — the sequential version turns "a few hundred author/series
// subfolders" into minutes of pure network latency.
export async function listLibraryTree(
  token: string,
  rootFolderId: string,
): Promise<{ files: DriveFile[]; folderIds: string[] }> {
  if (getAuthMode() === 'passcode') {
    // The backend already knows its own library folder — one round trip,
    // no BFS. folderIds is only ever consumed by the Drive-changes
    // incremental sync (librarySync.ts), which passcode mode never runs.
    const resp = await backendFetch('/drive/tree')
    if (!resp.ok) throw new Error(`Backend error (${resp.status})`)
    return { files: (await resp.json()) as DriveFile[], folderIds: [] }
  }
  const allFiles: DriveFile[] = []
  const folderIds = new Set<string>([rootFolderId])
  let frontier = [rootFolderId]

  while (frontier.length > 0) {
    const results = await mapConcurrent(frontier, WALK_CONCURRENCY, (folderId) => listChildren(token, folderId))
    const nextFrontier: string[] = []
    for (const { files, folders } of results) {
      allFiles.push(...files)
      for (const folder of folders) {
        folderIds.add(folder.id)
        nextFrontier.push(folder.id)
      }
    }
    frontier = nextFrontier
  }

  return { files: allFiles, folderIds: Array.from(folderIds) }
}

export async function getStartPageToken(token: string): Promise<string> {
  const data = (await driveFetch(token, 'changes/startPageToken')) as { startPageToken: string }
  return data.startPageToken
}

export interface DriveChange {
  fileId: string
  removed: boolean
  file?: {
    id: string
    name: string
    mimeType: string
    parents?: string[]
    trashed?: boolean
  }
}

const CHANGE_FIELDS = encodeURIComponent(
  'nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,parents,trashed))',
)

// Drains every page from startPageToken to "now" and returns the token to
// resume from next time. Changes are Drive-wide (not scoped to our library
// folder) — the caller filters by checking each change's parent against
// the known folder set.
export async function listAllChanges(
  token: string,
  startPageToken: string,
): Promise<{ changes: DriveChange[]; newStartPageToken: string }> {
  const changes: DriveChange[] = []
  let pageToken = startPageToken
  let newStartPageToken = startPageToken

  while (true) {
    let data: { changes: DriveChange[]; nextPageToken?: string; newStartPageToken?: string }
    try {
      data = (await driveFetch(
        token,
        `changes?pageToken=${encodeURIComponent(pageToken)}&pageSize=1000&fields=${CHANGE_FIELDS}`,
      )) as typeof data
    } catch (err) {
      // A stale/invalid page token comes back as 410 (sometimes 404) with
      // reason "pageTokenExpired". That — and only that — means "your token
      // is too old, do a full rebuild"; anything else is a real error.
      if (
        err instanceof DriveApiError &&
        (err.reason === 'pageTokenExpired' || err.status === 410)
      ) {
        throw new StalePageTokenError('Drive sync token expired')
      }
      throw err
    }

    changes.push(...data.changes)
    if (data.newStartPageToken) newStartPageToken = data.newStartPageToken
    if (!data.nextPageToken) break
    pageToken = data.nextPageToken
  }

  return { changes, newStartPageToken }
}

// Every non-folder file directly in `folderId` (not recursive) — used to
// show what's actually sitting in a device's Rakuten Kobo sync folder, and
// (via covers.ts) to build the covers/ manifest.
export async function listFolderContents(token: string, folderId: string): Promise<DriveFile[]> {
  if (getAuthMode() === 'passcode') {
    const resp = await backendFetch(`/drive/folder?folderId=${encodeURIComponent(folderId)}`)
    if (!resp.ok) throw new Error(`Backend error (${resp.status})`)
    return (await resp.json()) as DriveFile[]
  }
  const files: DriveFile[] = []
  let pageToken: string | undefined
  do {
    const query = encodeURIComponent(
      `'${folderId}' in parents and trashed=false and mimeType != '${FOLDER_MIME_TYPE}'`,
    )
    const pageParam = pageToken ? `&pageToken=${pageToken}` : ''
    const data = (await driveFetch(
      token,
      `files?q=${query}&fields=nextPageToken,files(id,name)&pageSize=1000${pageParam}`,
    )) as { files: DriveFile[]; nextPageToken?: string }
    files.push(...data.files)
    pageToken = data.nextPageToken
  } while (pageToken)
  return files
}

// Reads a single JSON file by name from a folder. Returns its parsed
// content and Drive id + modifiedTime (for change detection), or null.
// Built on findSidecarMeta + fetchDriveBytes so it (and every direct caller)
// picks up the passcode-mode backend proxy automatically.
export async function readJsonFile<T>(
  token: string,
  folderId: string,
  name: string,
): Promise<{ id: string; modifiedTime: string; content: T } | null> {
  const meta = await findSidecarMeta(token, folderId, name)
  if (!meta) return null
  const buf = await fetchDriveBytes(token, meta.id)
  const content = JSON.parse(new TextDecoder().decode(buf)) as T
  return { id: meta.id, modifiedTime: meta.modifiedTime, content }
}

// Creates or overwrites a JSON file in a folder. Returns the file's id.
export async function writeJsonFile(
  token: string,
  folderId: string,
  name: string,
  content: unknown,
  existingId: string | null,
  retried = false,
): Promise<string> {
  if (getAuthMode() === 'passcode') {
    // The backend looks the file up by name itself rather than trusting
    // existingId — it's the only thing here with a consistent view of
    // what's actually current, since (unlike the Google path) several
    // siblings' browsers all funnel through the same credential.
    const resp = await backendFetch(`/drive/sidecar?name=${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(content),
    })
    if (!resp.ok) throw new Error(`Failed to save (${resp.status})`)
    return ((await resp.json()) as { id: string }).id
  }
  const body = JSON.stringify(content)
  const auth = (t: string) => ({ Authorization: `Bearer ${t}` })

  let resp: Response
  if (existingId) {
    resp = await fetch(
      `https://www.googleapis.com/upload/drive/v3/files/${existingId}?uploadType=media&fields=id`,
      { method: 'PATCH', headers: { ...auth(token), 'Content-Type': 'application/json' }, body },
    )
  } else {
    const boundary = 'bookbrain' + Math.random().toString(36).slice(2)
    const multipart =
      `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n` +
      `${JSON.stringify({ name, parents: [folderId] })}\r\n` +
      `--${boundary}\r\nContent-Type: application/json\r\n\r\n${body}\r\n--${boundary}--`
    resp = await fetch(
      'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id',
      {
        method: 'POST',
        headers: { ...auth(token), 'Content-Type': `multipart/related; boundary=${boundary}` },
        body: multipart,
      },
    )
  }

  // A lapsed access token (tab open a long time, machine slept through the
  // silent renewal) — get a fresh one and try the write once more so a
  // queued change isn't dropped. isAuthError() also matches the "(401)"
  // message for callers that don't retry.
  if (resp.status === 401 && !retried) {
    const fresh = await refreshAccessToken() // throws "Sign-in expired…" if it can't
    return writeJsonFile(fresh, folderId, name, content, existingId, true)
  }
  if (!resp.ok) {
    if (resp.status === 401) throw new Error('Sign-in expired — sign in again.')
    throw new Error(`Failed to save (${resp.status})`)
  }
  return existingId ?? ((await resp.json()) as { id: string }).id
}

export async function trashFile(token: string, fileId: string): Promise<void> {
  if (getAuthMode() === 'passcode') {
    const resp = await backendFetch(`/drive/trash/${encodeURIComponent(fileId)}`, { method: 'POST' })
    if (!resp.ok) throw new Error(`Failed to remove file (${resp.status})`)
    return
  }
  const response = await fetch(`https://www.googleapis.com/drive/v3/files/${fileId}`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ trashed: true }),
  })
  if (!response.ok) {
    if (response.status === 401) throw new Error('Sign-in expired — sign in again.')
    throw new Error(`Failed to remove file (${response.status})`)
  }
}

export async function copyFileToFolder(token: string, file: DriveFile, destinationFolderId: string): Promise<void> {
  if (getAuthMode() === 'passcode') {
    const resp = await backendFetch(`/drive/copy/${encodeURIComponent(file.id)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destinationFolderId }),
    })
    if (!resp.ok) throw new Error(`Failed to send ${file.name} to Kobo folder (${resp.status})`)
    return
  }
  const response = await fetch(`https://www.googleapis.com/drive/v3/files/${file.id}/copy`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ parents: [destinationFolderId] }),
  })
  if (!response.ok) {
    if (response.status === 401) throw new Error('Sign-in expired — sign in again.')
    throw new Error(`Failed to send ${file.name} to Kobo folder (${response.status})`)
  }
}

// Raw file bytes from Drive. Shared by downloadFile (save-to-disk) and the
// EPUB reader (which caches the blob in IndexedDB, see lib/bookCache.ts) —
// also, via fetchDriveBytes/readJsonFile above, every JSON/binary sidecar
// and cover fetch.
export async function fetchDriveBlob(token: string, fileId: string): Promise<Blob> {
  if (getAuthMode() === 'passcode') {
    let response: Response
    try {
      response = await backendFetch(`/drive/blob/${encodeURIComponent(fileId)}`)
    } catch (err) {
      if (err instanceof Error && /sign-in expired/i.test(err.message)) throw err
      throw new Error("Couldn't reach the library server — check your connection.")
    }
    if (!response.ok) {
      if (response.status === 404) throw new Error('This file is no longer in your library.')
      throw new Error(`The library server refused the download (error ${response.status}).`)
    }
    return response.blob()
  }
  const base = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`
  const auth = { Authorization: `Bearer ${token}` }
  let response: Response
  try {
    response = await fetch(base, { headers: auth })
  } catch {
    // network down / CORS / offline — fetch() rejects with a bare TypeError
    throw new Error("Couldn't reach Google Drive — check your connection.")
  }
  // Google flags some files as "abusive" and refuses a plain download; a
  // second request with acknowledgeAbuse=true goes through.
  if (response.status === 403) {
    const body = await response.clone().text().catch(() => '')
    if (/abuse/i.test(body)) {
      response = await fetch(`${base}&acknowledgeAbuse=true`, { headers: auth }).catch(() => response)
    }
  }
  if (!response.ok) {
    if (response.status === 401) throw new Error('Sign-in expired — sign in again.')
    if (response.status === 404) throw new Error('This file is no longer in your Drive library.')
    throw new Error(`Google Drive refused the download (error ${response.status}).`)
  }
  return response.blob()
}

// A Blob is a ZIP (EPUBs are ZIP containers) — cheap guard against handing a
// Drive error page / truncated download to the EPUB renderer.
export async function looksLikeZip(blob: Blob): Promise<boolean> {
  if (blob.size < 4) return false
  const sig = new Uint8Array(await blob.slice(0, 4).arrayBuffer())
  return sig[0] === 0x50 && sig[1] === 0x4b // "PK"
}

export async function downloadFile(token: string, file: DriveFile): Promise<void> {
  const blob = await fetchDriveBlob(token, file.id)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
