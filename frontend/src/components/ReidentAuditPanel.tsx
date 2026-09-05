import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../services/api'
import { useReidentRebuildStatus } from '../hooks/useReidentRebuildStatus'
import { CorrectFileForm } from './CorrectFileForm'
import type { FileSummary } from '../types/files'
import type { CorrectReviewRequest } from '../types/reviews'
import {
  SIGNAL_LABEL,
  type DeepCheckEstimate,
  type ReidentDivergence,
} from '../types/reidentAudit'

function asFileSummary(d: ReidentDivergence): FileSummary {
  return {
    id: d.file_id,
    filename: d.filename,
    status: 'organised',
    status_reason: null,
    book_title: d.stored_title,
    book_author: d.stored_author,
    book_series: d.stored_series,
    book_series_number: d.stored_series_number,
    computed_confidence: d.stored_confidence,
    ai_reasoning: null,
    quality_score: null,
    discovered_at: '',
  }
}

function DivergenceRow({ d }: { d: ReidentDivergence }) {
  const queryClient = useQueryClient()
  const [correcting, setCorrecting] = useState(false)
  const [correctError, setCorrectError] = useState<string | null>(null)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['reident-report'] })
    queryClient.invalidateQueries({ queryKey: ['files'] })
  }

  const correct = useMutation({
    mutationFn: (body: CorrectReviewRequest) => api.correctFile(d.file_id, body),
    onSuccess: () => {
      setCorrecting(false)
      setCorrectError(null)
      invalidate()
    },
    onError: (err: unknown) =>
      setCorrectError(err instanceof ApiError ? err.message : 'Failed to save correction.'),
  })

  const dismiss = useMutation({
    mutationFn: () => api.dismissReidentFlag(d.book_id),
    onSuccess: invalidate,
  })

  const dc = d.deep_check_verdict

  return (
    <li className="rounded border border-amber-300 p-3 text-sm dark:border-amber-800">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">
          {d.stored_title}
          {d.stored_author && (
            <span className="font-normal text-neutral-500"> by {d.stored_author}</span>
          )}
        </span>
        <span className="shrink-0 text-xs text-neutral-500">
          {d.stored_series
            ? `${d.stored_series}${d.stored_series_number !== null ? ` #${d.stored_series_number}` : ''} · `
            : ''}
          {d.stored_confidence !== null ? `confidence ${d.stored_confidence}` : ''}
        </span>
      </div>

      <div className="mt-1.5 flex flex-wrap gap-1">
        {d.signals.map((s) => (
          <span
            key={s}
            className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
          >
            {SIGNAL_LABEL[s]}
          </span>
        ))}
        {d.stored_from_human && (
          <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-500 dark:bg-neutral-800">
            human-corrected
          </span>
        )}
      </div>

      <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-xs text-neutral-600 dark:text-neutral-400">
        {d.evidence.map((e, i) => (
          <li key={i}>{e}</li>
        ))}
        <li className="text-neutral-400">File: {d.filename}</li>
      </ul>

      {dc && (
        <div
          className={`mt-2 rounded border p-2 text-xs ${
            dc === 'stored_is_wrong'
              ? 'border-red-300 text-red-700 dark:border-red-800 dark:text-red-400'
              : dc === 'stored_is_correct'
                ? 'border-emerald-300 text-emerald-700 dark:border-emerald-800 dark:text-emerald-400'
                : 'border-neutral-300 text-neutral-600 dark:border-neutral-700 dark:text-neutral-400'
          }`}
        >
          <span className="font-medium">Deep re-check: {dc.replace(/_/g, ' ')}</span>
          {d.deep_check_explanation && <> — {d.deep_check_explanation}</>}
          {dc === 'stored_is_wrong' && (
            <div className="mt-1 text-neutral-600 dark:text-neutral-400">
              Suggested: {d.deep_check_suggested_title ?? d.stored_title}
              {d.deep_check_suggested_author ? ` · ${d.deep_check_suggested_author}` : ''}
              {' · '}
              {d.deep_check_suggested_series
                ? `${d.deep_check_suggested_series}${
                    d.deep_check_suggested_series_number !== null
                      ? ` #${d.deep_check_suggested_series_number}`
                      : ''
                  }`
                : 'standalone'}
            </div>
          )}
        </div>
      )}

      <div className="mt-2 flex items-center gap-2">
        <button
          className="rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
          onClick={() => {
            setCorrectError(null)
            setCorrecting((v) => !v)
          }}
        >
          {correcting ? 'Cancel' : 'Correct…'}
        </button>
        <button
          className="rounded border border-neutral-300 px-2 py-1 text-xs text-neutral-500 disabled:opacity-50 dark:border-neutral-700"
          disabled={dismiss.isPending}
          onClick={() => dismiss.mutate()}
          title="Already reviewed this one — stop flagging it"
        >
          Dismiss
        </button>
      </div>

      {correcting && (
        <CorrectFileForm
          file={asFileSummary(d)}
          busy={correct.isPending}
          error={correctError}
          onSubmit={(body) => correct.mutate(body)}
          onCancel={() => setCorrecting(false)}
        />
      )}
    </li>
  )
}

function DismissedList() {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const dismissed = useQuery({
    queryKey: ['reident-dismissed'],
    queryFn: api.listReidentDismissed,
    enabled: expanded,
  })
  const restore = useMutation({
    mutationFn: (bookId: number) => api.restoreReidentFlag(bookId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reident-report'] })
      queryClient.invalidateQueries({ queryKey: ['reident-dismissed'] })
    },
  })

  return (
    <div className="mt-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? 'Hide dismissed' : 'Show dismissed books'}
      </button>
      {expanded && (
        <ul className="mt-2 space-y-1">
          {dismissed.data?.length === 0 && (
            <li className="text-xs text-neutral-400">Nothing dismissed yet.</li>
          )}
          {dismissed.data?.map((row) => (
            <li key={row.book_id} className="flex items-center gap-3 text-xs text-neutral-500">
              <span>book #{row.book_id}</span>
              <button
                className="text-neutral-400 underline hover:text-neutral-700 dark:hover:text-neutral-300"
                disabled={restore.isPending}
                onClick={() => restore.mutate(row.book_id)}
              >
                Restore
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function ReidentAuditPanel() {
  const queryClient = useQueryClient()
  const report = useQuery({ queryKey: ['reident-report'], queryFn: api.getReidentReport })

  const [jobId, setJobId] = useState<string | null>(null)
  const [rebuildError, setRebuildError] = useState<string | null>(null)
  const rebuild = useReidentRebuildStatus(jobId)

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [estimate, setEstimate] = useState<DeepCheckEstimate | null>(null)
  const [deepError, setDeepError] = useState<string | null>(null)

  useEffect(() => {
    if (rebuild.data?.status === 'done') {
      queryClient.invalidateQueries({ queryKey: ['reident-report'] })
      setJobId(null)
    }
    if (rebuild.data?.status === 'failed') {
      setRebuildError(rebuild.data.detail ?? 'Rebuild failed.')
      setJobId(null)
    }
    if (rebuild.isError) {
      setRebuildError('Lost track of the rebuild job — the server may have restarted. Try again.')
      setJobId(null)
    }
  }, [rebuild.data?.status, rebuild.data?.detail, rebuild.isError, queryClient])

  const deepCheck = useMutation({
    mutationFn: (bookIds: number[]) => api.runReidentDeepCheck(bookIds),
    onSuccess: () => {
      setEstimate(null)
      setSelected(new Set())
      queryClient.invalidateQueries({ queryKey: ['reident-report'] })
    },
    onError: (err: unknown) =>
      setDeepError(err instanceof ApiError ? err.message : 'Deep re-check failed.'),
  })

  const running = rebuild.data?.status === 'running'
  const divergences = report.data?.divergences ?? []

  const startRebuild = async () => {
    setRebuildError(null)
    try {
      const job = await api.rebuildReidentReport()
      setJobId(job.job_id)
    } catch (err) {
      setRebuildError(err instanceof ApiError ? err.message : 'Failed to start the rebuild.')
    }
  }

  const askEstimate = async () => {
    setDeepError(null)
    try {
      const est = await api.estimateReidentDeepCheck([...selected])
      setEstimate(est)
    } catch (err) {
      setDeepError(err instanceof ApiError ? err.message : 'Failed to get an estimate.')
    }
  }

  const toggle = (bookId: number) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(bookId)) next.delete(bookId)
      else next.add(bookId)
      return next
    })

  return (
    <div>
      <p className="max-w-2xl text-sm text-neutral-500">
        Re-checks every organised book's stored identification against current data and lists the
        ones where it now disagrees. The default pass is free — it reuses cached AI decisions and
        free provider lookups, no API credits. It mainly hunts the recurring case where a book was
        filed into a series that doesn't actually exist.
      </p>

      <div className="mt-3 flex items-center gap-3">
        <button
          className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
          disabled={running}
          onClick={startRebuild}
        >
          {running
            ? `Re-checking… ${rebuild.data?.checked ?? 0}/${rebuild.data?.total ?? '?'}`
            : report.data?.generated_at
              ? 'Re-run check'
              : 'Run check'}
        </button>
        <span className="text-xs text-neutral-400">
          {report.data?.generated_at
            ? `Last run ${new Date(report.data.generated_at).toLocaleString()} — ${
                report.data.checked
              } book(s) checked${
                report.data.providers_unavailable
                  ? `, ${report.data.providers_unavailable} with no provider data`
                  : ''
              }`
            : 'Never run yet.'}
        </span>
      </div>
      {rebuildError && <p className="mt-1 text-xs text-red-600">{rebuildError}</p>}

      {report.isLoading && <p className="mt-6 text-sm text-neutral-500">Loading…</p>}

      {report.data && report.data.generated_at && divergences.length === 0 && (
        <p className="mt-6 text-sm text-neutral-400">
          No divergences — every organised book still matches its stored identification.
        </p>
      )}

      {divergences.length > 0 && (
        <>
          <div className="mt-5 rounded border border-neutral-200 p-3 text-xs dark:border-neutral-800">
            <p className="text-neutral-600 dark:text-neutral-400">
              <span className="font-medium">Deep re-check</span> — ask Claude to look again at the
              rows you tick. Capped at 50 per run, costs API credits, only touches already-flagged
              rows. {selected.size} selected.
            </p>
            {deepError && <p className="mt-1 text-red-600">{deepError}</p>}
            {!estimate ? (
              <button
                className="mt-2 rounded border border-neutral-300 px-2 py-1 disabled:opacity-50 dark:border-neutral-700"
                disabled={selected.size === 0}
                onClick={askEstimate}
              >
                Estimate cost for selected
              </button>
            ) : (
              <div className="mt-2 space-y-2 rounded border border-amber-300 p-2 dark:border-amber-800">
                <p className="text-amber-700 dark:text-amber-400">
                  Will deep-check {estimate.will_check} of {estimate.eligible} eligible row(s) —
                  estimated ~${estimate.estimated_cost_usd.toFixed(2)} in API credits. Proceed?
                </p>
                <div className="flex gap-2">
                  <button
                    className="rounded bg-amber-600 px-2 py-1 text-white disabled:opacity-50"
                    disabled={deepCheck.isPending || estimate.will_check === 0}
                    onClick={() => deepCheck.mutate([...selected])}
                  >
                    {deepCheck.isPending ? 'Re-checking…' : 'Yes, run deep re-check'}
                  </button>
                  <button
                    className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700"
                    onClick={() => setEstimate(null)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
            {deepCheck.data && (
              <p className="mt-2 text-neutral-500">
                Last deep re-check: {deepCheck.data.rechecked} done — {deepCheck.data.stored_is_wrong}{' '}
                wrong, {deepCheck.data.stored_is_correct} confirmed, {deepCheck.data.uncertain}{' '}
                uncertain
                {deepCheck.data.failed > 0 ? `, ${deepCheck.data.failed} failed` : ''}.
              </p>
            )}
          </div>

          <ul className="mt-3 space-y-3">
            {divergences.map((d) => (
              <li key={d.book_id} className="flex gap-2">
                <input
                  type="checkbox"
                  className="mt-3.5"
                  checked={selected.has(d.book_id)}
                  onChange={() => toggle(d.book_id)}
                  title="Select for deep re-check"
                />
                <div className="min-w-0 flex-1">
                  <DivergenceRow d={d} />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      <DismissedList />
    </div>
  )
}
