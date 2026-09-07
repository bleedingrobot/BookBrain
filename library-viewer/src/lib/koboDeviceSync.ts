// Kobo device folder IDs (the fiddly bit of Settings — copy-pasting a
// Drive folder id per eReader) live as bookbrain-viewer-settings.json in
// the Drive library folder, same pattern as the wishlist sidecar. Once one
// signed-in device has saved them, every other device signed in with
// access to that same library folder picks them up automatically — no
// more retyping the same folder ids into a second browser.
//
// Deliberately does NOT hold googleClientId/libraryFolderId: those two are
// needed *before* the first successful sign-in (there's nothing to read
// from Drive without them yet), so they stay as build-time defaults
// (see settings.ts) rather than round-tripping through Drive.

import type { KoboDevice } from './settings'
import { readJsonFile, writeJsonFile } from './drive'

const FILENAME = 'bookbrain-viewer-settings.json'

interface RawFile {
  version?: number
  koboDevices?: Partial<KoboDevice>[]
}

export interface RemoteKoboDevices {
  fileId: string | null
  // null = no settings file exists yet in this library (vs. an existing
  // file that's just empty) — callers use this to decide whether to seed
  // Drive from whatever's configured locally.
  devices: KoboDevice[] | null
}

export async function loadRemoteKoboDevices(
  token: string,
  libraryFolderId: string,
): Promise<RemoteKoboDevices> {
  try {
    const found = await readJsonFile<RawFile>(token, libraryFolderId, FILENAME)
    if (!found) return { fileId: null, devices: null }
    const devices = (found.content.koboDevices ?? []).filter(
      (d): d is KoboDevice =>
        !!d && typeof d.label === 'string' && typeof d.folderId === 'string' && !!d.folderId,
    )
    return { fileId: found.id, devices }
  } catch {
    return { fileId: null, devices: null }
  }
}

export async function saveRemoteKoboDevices(
  token: string,
  libraryFolderId: string,
  devices: KoboDevice[],
  existingFileId: string | null,
): Promise<string> {
  return writeJsonFile(token, libraryFolderId, FILENAME, { version: 1, koboDevices: devices }, existingFileId)
}
