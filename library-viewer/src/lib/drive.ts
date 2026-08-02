export interface DriveFile {
  id: string
  name: string
}

const FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'
const EBOOK_EXTENSIONS = ['.epub', '.kpub']

function isSupportedEbook(name: string): boolean {
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

async function listChildren(token: string, folderId: string): Promise<{ files: DriveFile[]; folders: DriveFile[] }> {
  const files: DriveFile[] = []
  const folders: DriveFile[] = []
  let pageToken: string | undefined

  do {
    const query = encodeURIComponent(`'${folderId}' in parents and trashed=false`)
    const pageParam = pageToken ? `&pageToken=${pageToken}` : ''
    const data = (await driveFetch(
      token,
      `files?q=${query}&fields=nextPageToken,files(id,name,mimeType)&pageSize=200${pageParam}`,
    )) as { files: { id: string; name: string; mimeType: string }[]; nextPageToken?: string }

    for (const f of data.files) {
      if (f.mimeType === FOLDER_MIME_TYPE) folders.push({ id: f.id, name: f.name })
      else if (isSupportedEbook(f.name)) files.push({ id: f.id, name: f.name })
    }
    pageToken = data.nextPageToken
  } while (pageToken)

  return { files, folders }
}

export async function listLibraryRecursive(token: string, rootFolderId: string): Promise<DriveFile[]> {
  const all: DriveFile[] = []
  const stack = [rootFolderId]
  while (stack.length > 0) {
    const current = stack.pop()!
    const { files, folders } = await listChildren(token, current)
    all.push(...files)
    stack.push(...folders.map((f) => f.id))
  }
  return all
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
