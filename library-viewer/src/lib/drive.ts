export interface DriveFile {
  id: string
  name: string
}

export const FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'
const EBOOK_EXTENSIONS = ['.epub', '.kpub']
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

async function driveFetch(token: string, path: string): Promise<unknown> {
  const response = await fetch(`https://www.googleapis.com/drive/v3/${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) {
    if (response.status === 401) throw new Error('Sign-in expired — sign in again.')
    throw new Error(`Drive API error (${response.status})`)
  }
  return response.json()
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
    const data = (await driveFetch(
      token,
      `changes?pageToken=${encodeURIComponent(pageToken)}&pageSize=1000&fields=${CHANGE_FIELDS}`,
    )) as { changes: DriveChange[]; nextPageToken?: string; newStartPageToken?: string }

    changes.push(...data.changes)
    if (data.newStartPageToken) newStartPageToken = data.newStartPageToken
    if (!data.nextPageToken) break
    pageToken = data.nextPageToken
  }

  return { changes, newStartPageToken }
}

// Every non-folder file directly in `folderId` (not recursive) — used to
// show what's actually sitting in a device's Rakuten Kobo sync folder.
export async function listFolderContents(token: string, folderId: string): Promise<DriveFile[]> {
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
export async function readJsonFile<T>(
  token: string,
  folderId: string,
  name: string,
): Promise<{ id: string; modifiedTime: string; content: T } | null> {
  const q = encodeURIComponent(`'${folderId}' in parents and name = '${name}' and trashed = false`)
  const listResp = await fetch(
    `https://www.googleapis.com/drive/v3/files?q=${q}&fields=files(id,modifiedTime)&pageSize=1`,
    { headers: { Authorization: `Bearer ${token}` } },
  )
  if (!listResp.ok) throw new Error(`Drive API error (${listResp.status})`)
  const { files } = (await listResp.json()) as { files: { id: string; modifiedTime: string }[] }
  if (files.length === 0) return null
  const dl = await fetch(`https://www.googleapis.com/drive/v3/files/${files[0].id}?alt=media`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!dl.ok) throw new Error(`Drive API error (${dl.status})`)
  return { id: files[0].id, modifiedTime: files[0].modifiedTime, content: (await dl.json()) as T }
}

// Creates or overwrites a JSON file in a folder. Returns the file's id.
export async function writeJsonFile(
  token: string,
  folderId: string,
  name: string,
  content: unknown,
  existingId: string | null,
): Promise<string> {
  const body = JSON.stringify(content)
  if (existingId) {
    const resp = await fetch(
      `https://www.googleapis.com/upload/drive/v3/files/${existingId}?uploadType=media&fields=id`,
      {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body,
      },
    )
    if (!resp.ok) throw new Error(`Failed to save (${resp.status})`)
    return existingId
  }
  const boundary = 'bookbrain' + Math.random().toString(36).slice(2)
  const multipart =
    `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n` +
    `${JSON.stringify({ name, parents: [folderId] })}\r\n` +
    `--${boundary}\r\nContent-Type: application/json\r\n\r\n${body}\r\n--${boundary}--`
  const resp = await fetch(
    'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': `multipart/related; boundary=${boundary}`,
      },
      body: multipart,
    },
  )
  if (!resp.ok) throw new Error(`Failed to save (${resp.status})`)
  return ((await resp.json()) as { id: string }).id
}

export async function trashFile(token: string, fileId: string): Promise<void> {
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

export async function downloadFile(token: string, file: DriveFile): Promise<void> {
  const response = await fetch(`https://www.googleapis.com/drive/v3/files/${file.id}?alt=media`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) {
    throw new Error(`Failed to download ${file.name} (${response.status})`)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
