import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'

interface Crumb {
  id: string | null
  name: string
}

export function FolderPicker({ onSelect }: { onSelect: (folderId: string, name: string) => void }) {
  const [stack, setStack] = useState<Crumb[]>([{ id: null, name: 'My Drive' }])
  const [newFolderName, setNewFolderName] = useState('')
  const current = stack[stack.length - 1]
  const queryClient = useQueryClient()

  const folders = useQuery({
    queryKey: ['drive-folders', current.id],
    queryFn: () => api.driveFolders(current.id ?? undefined),
  })

  return (
    <div className="rounded border border-neutral-200 p-3 text-sm dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-1 text-neutral-500">
        {stack.map((crumb, i) => (
          <span key={crumb.id ?? 'root'}>
            <button
              className="hover:underline"
              onClick={() => setStack(stack.slice(0, i + 1))}
            >
              {crumb.name}
            </button>
            {i < stack.length - 1 && <span className="mx-1">/</span>}
          </span>
        ))}
      </div>

      <ul className="mt-2 max-h-64 divide-y divide-neutral-100 overflow-y-auto dark:divide-neutral-800">
        {folders.isLoading && <li className="py-2 text-neutral-500">Loading...</li>}
        {folders.isError && <li className="py-2 text-red-600">Failed to load folders.</li>}
        {folders.data?.map((folder) => (
          <li key={folder.id}>
            <button
              className="w-full py-1.5 text-left hover:text-neutral-900 dark:hover:text-neutral-100"
              onClick={() => setStack([...stack, { id: folder.id, name: folder.name }])}
            >
              {folder.name}
            </button>
          </li>
        ))}
        {folders.data?.length === 0 && (
          <li className="py-2 text-neutral-400">No subfolders here.</li>
        )}
      </ul>

      <div className="mt-3 flex gap-2">
        <input
          className="flex-1 rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900"
          placeholder="New folder name"
          value={newFolderName}
          onChange={(e) => setNewFolderName(e.target.value)}
        />
        <button
          className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-50 dark:border-neutral-700"
          disabled={newFolderName.trim() === ''}
          onClick={async () => {
            await api.driveCreateFolder(newFolderName.trim(), current.id ?? undefined)
            setNewFolderName('')
            await queryClient.invalidateQueries({ queryKey: ['drive-folders', current.id] })
          }}
        >
          + New
        </button>
      </div>

      <button
        className="mt-3 w-full rounded bg-neutral-900 py-1.5 text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
        disabled={current.id === null}
        onClick={() => current.id && onSelect(current.id, current.name)}
      >
        Use "{current.name}" as inbox folder
      </button>
    </div>
  )
}
