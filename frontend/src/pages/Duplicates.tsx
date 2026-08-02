import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function Duplicates() {
  const duplicates = useQuery({ queryKey: ['duplicates'], queryFn: api.listDuplicates })

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">Duplicates</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Files whose content exactly matches another file already in your Drive — detected
        by content hash, not filename.
      </p>

      <ul className="mt-4 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
        {duplicates.data?.map((group) => (
          <li key={group.duplicate_file_id} className="py-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{group.duplicate_filename}</span>
              {group.quality_score !== null && (
                <span className="text-xs text-neutral-400">quality {group.quality_score}</span>
              )}
            </div>
            <p className="text-xs text-neutral-500">
              Duplicate of{' '}
              {group.primary_filename ? (
                <span className="font-medium">{group.primary_filename}</span>
              ) : (
                <span className="italic">an unknown file (primary not found)</span>
              )}
            </p>
          </li>
        ))}
        {duplicates.data?.length === 0 && (
          <li className="py-4 text-neutral-400">No duplicates found.</li>
        )}
      </ul>
    </div>
  )
}
