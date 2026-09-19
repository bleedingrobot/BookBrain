import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '../services/api'
import type { RulePreviewResult, SmartCollection } from '../types/smartCollections'

function RuleForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: { name: string; description: string; rule: string }
  submitLabel: string
  onSubmit: (values: { name: string; description: string; rule: string }) => void
  onCancel?: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [rule, setRule] = useState(initial?.rule ?? '')
  const [preview, setPreview] = useState<RulePreviewResult | null>(null)

  const previewMutation = useMutation({
    mutationFn: () => api.previewSmartCollectionRule(rule),
    onSuccess: setPreview,
  })

  return (
    <div className="space-y-2">
      <input
        className="field w-full py-1.5 text-sm"
        placeholder="Name (e.g. Fantasy Favorites)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <input
        className="field w-full py-1.5 text-sm"
        placeholder="Description (optional)"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      <textarea
        className="field w-full py-1.5 font-mono text-sm"
        rows={2}
        placeholder='genre:Fantasy AND rating>=4'
        value={rule}
        onChange={(e) => {
          setRule(e.target.value)
          setPreview(null)
        }}
      />
      <div className="flex items-center gap-2">
        <button
          className="btn btn-neutral py-1.5 text-sm"
          disabled={!rule.trim() || previewMutation.isPending}
          onClick={() => previewMutation.mutate()}
        >
          {previewMutation.isPending ? 'Checking…' : 'Preview'}
        </button>
        <button
          className="btn btn-primary py-1.5 text-sm"
          disabled={!name.trim() || !rule.trim()}
          onClick={() => onSubmit({ name: name.trim(), description: description.trim(), rule: rule.trim() })}
        >
          {submitLabel}
        </button>
        {onCancel && (
          <button className="text-sm text-neutral-400 underline" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>

      {previewMutation.isError && (
        <p className="text-xs text-red-600">
          {previewMutation.error instanceof ApiError ? previewMutation.error.message : 'Invalid rule.'}
        </p>
      )}
      {preview && (
        <div className="card bg-neutral-50 px-3 py-2 text-xs text-neutral-600 dark:bg-neutral-900/60 dark:text-neutral-400">
          <p className="font-medium">{preview.count} matching book{preview.count === 1 ? '' : 's'}</p>
          {preview.sample.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {preview.sample.map((title) => (
                <li key={title}>{title}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function CollectionRow({ collection }: { collection: SmartCollection }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)

  const update = useMutation({
    mutationFn: (values: { name: string; description: string; rule: string }) =>
      api.updateSmartCollection(collection.id, {
        name: values.name,
        description: values.description || null,
        rule: values.rule,
      }),
    onSuccess: () => {
      setEditing(false)
      queryClient.invalidateQueries({ queryKey: ['smartCollections'] })
    },
  })

  const remove = useMutation({
    mutationFn: () => api.deleteSmartCollection(collection.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['smartCollections'] }),
  })

  if (editing) {
    return (
      <li className="py-3">
        <RuleForm
          initial={{
            name: collection.name,
            description: collection.description ?? '',
            rule: collection.rule,
          }}
          submitLabel={update.isPending ? 'Saving…' : 'Save'}
          onSubmit={(values) => update.mutate(values)}
          onCancel={() => setEditing(false)}
        />
        {update.isError && (
          <p className="mt-1 text-xs text-red-600">
            {update.error instanceof ApiError ? update.error.message : 'Failed to save.'}
          </p>
        )}
      </li>
    )
  }

  return (
    <li className="flex items-start justify-between gap-3 py-3">
      <div className="min-w-0 flex-1">
        <div className="font-medium">{collection.name}</div>
        {collection.description && (
          <div className="text-sm text-neutral-500">{collection.description}</div>
        )}
        <div className="mt-0.5 truncate font-mono text-xs text-neutral-400">{collection.rule}</div>
      </div>
      <div className="flex shrink-0 gap-2 text-xs">
        <button className="text-neutral-400 underline hover:text-neutral-700" onClick={() => setEditing(true)}>
          Edit
        </button>
        <button
          className="text-neutral-400 underline hover:text-red-600"
          onClick={() => remove.mutate()}
        >
          Remove
        </button>
      </div>
    </li>
  )
}

export function SmartCollections() {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const collections = useQuery({ queryKey: ['smartCollections'], queryFn: api.listSmartCollections })

  const create = useMutation({
    mutationFn: (values: { name: string; description: string; rule: string }) =>
      api.createSmartCollection({
        name: values.name,
        description: values.description || null,
        rule: values.rule,
      }),
    onSuccess: () => {
      setCreating(false)
      queryClient.invalidateQueries({ queryKey: ['smartCollections'] })
    },
  })

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-xl font-semibold">Smart Collections</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Named, rule-based shelves — membership is computed from a query and stays current as the
        library grows. These show up as browsable shelves in the family viewer.
      </p>

      <div className="mt-4">
        {creating ? (
          <div className="card p-4">
            <RuleForm
              submitLabel={create.isPending ? 'Creating…' : 'Create'}
              onSubmit={(values) => create.mutate(values)}
              onCancel={() => setCreating(false)}
            />
            {create.isError && (
              <p className="mt-1 text-xs text-red-600">
                {create.error instanceof ApiError ? create.error.message : 'Failed to create.'}
              </p>
            )}
          </div>
        ) : (
          <button
            className="btn btn-primary py-1.5 text-sm"
            onClick={() => setCreating(true)}
          >
            New collection
          </button>
        )}
      </div>

      {collections.isLoading && <p className="mt-4 text-sm text-neutral-500">Loading…</p>}

      <ul className="mt-4 divide-y divide-neutral-100 dark:divide-neutral-800">
        {(collections.data ?? []).map((collection) => (
          <CollectionRow key={collection.id} collection={collection} />
        ))}
        {collections.data && collections.data.length === 0 && (
          <li className="py-4 text-sm text-neutral-400">No smart collections yet.</li>
        )}
      </ul>
    </div>
  )
}
