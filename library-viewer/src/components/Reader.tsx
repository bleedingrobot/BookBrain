import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import '../vendor/foliate/view.js'
import type { FoliateTOCItem, FoliateView } from '../vendor/foliate/view.js'
import type { BookRow } from '../lib/books'
import { getCachedBook, putCachedBook } from '../lib/bookCache'
import { downloadFile, fetchDriveBlob, isAuthError, looksLikeZip } from '../lib/drive'
import { getProgress, setProgress } from '../lib/readingProgress'
import {
  DEFAULT_PREFS,
  loadReaderPrefs,
  readerCss,
  saveReaderPrefs,
  themeColors,
  type ReaderFont,
  type ReaderPrefs,
  type ReaderTheme,
} from '../lib/readerPrefs'

interface Props {
  token: string
  book: BookRow
  onClose: () => void
  onAuthError: (err: unknown) => void
}

const SAVE_DEBOUNCE_MS = 1000
const CHROME_HIDE_MS = 2800
const EDGE_GUARD_PX = 24

function tocFlat(items: FoliateTOCItem[], depth = 0): { item: FoliateTOCItem; depth: number }[] {
  const out: { item: FoliateTOCItem; depth: number }[] = []
  for (const item of items) {
    out.push({ item, depth })
    if (item.subitems?.length) out.push(...tocFlat(item.subitems, depth + 1))
  }
  return out
}

export function Reader({ token, book, onClose, onAuthError }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<FoliateView | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pending = useRef<{ cfi: string; fraction: number } | null>(null)

  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMsg, setErrorMsg] = useState('')
  const [retryNonce, setRetryNonce] = useState(0)
  const [downloadState, setDownloadState] = useState<'idle' | 'saving' | 'done' | 'failed'>('idle')
  const [prefs, setPrefs] = useState<ReaderPrefs>(() => loadReaderPrefs())
  // The load effect reads prefs once for the first paint; the effect below
  // keeps them live. A ref keeps prefs out of the load effect's deps (it must
  // not re-download the book when a setting changes).
  const prefsRef = useRef(prefs)
  prefsRef.current = prefs
  const [toc, setToc] = useState<FoliateTOCItem[]>([])
  const [panel, setPanel] = useState<'none' | 'toc' | 'prefs'>('none')
  const [chrome, setChrome] = useState(true)
  const [pos, setPos] = useState<{ fraction: number; label: string }>({ fraction: 0, label: '' })

  const flushSave = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = null
    if (pending.current) {
      setProgress(book.id, pending.current.cfi, pending.current.fraction)
      pending.current = null
    }
  }, [book.id])

  // --- load + mount the foliate view -------------------------------------
  useEffect(() => {
    let cancelled = false
    const host = hostRef.current
    ;(async () => {
      setStatus('loading')
      try {
        // "Try again" (retryNonce > 0) bypasses the cache — the cached copy
        // may be the thing that's broken.
        let blob = retryNonce > 0 ? null : await getCachedBook(book.id)
        if (!blob) {
          blob = await fetchDriveBlob(token, book.id)
          if (cancelled) return
          if (!(await looksLikeZip(blob))) {
            throw new Error(
              "The download didn't come back as a book file — Drive may be rate-limiting you. Try again in a minute, or download it and open it in another reader.",
            )
          }
          void putCachedBook(book.id, blob)
        }
        if (cancelled || !host) return

        const view = document.createElement('foliate-view') as FoliateView
        // <foliate-view> sets no :host style — it defaults to display:inline,
        // which gives the paginator a zero-size container. Size it explicitly.
        view.style.display = 'block'
        view.style.width = '100%'
        view.style.height = '100%'
        viewRef.current = view
        host.append(view)

        view.addEventListener('relocate', (e) => {
          const d = e.detail
          const fraction = Number.isFinite(d.fraction) ? d.fraction : 0
          const label = d.pageItem?.label
            ? `Page ${d.pageItem.label}`
            : d.location
              ? `${d.location.current} / ${d.location.total}`
              : ''
          setPos({ fraction, label })
          if (d.cfi) {
            pending.current = { cfi: d.cfi, fraction }
            if (saveTimer.current) clearTimeout(saveTimer.current)
            saveTimer.current = setTimeout(flushSave, SAVE_DEBOUNCE_MS)
          }
        })

        const file = new File([blob], `${book.id}.epub`, { type: 'application/epub+zip' })
        await view.open(file)
        if (cancelled) return

        const p = prefsRef.current
        view.renderer.setAttribute('flow', 'paginated')
        view.renderer.setAttribute('margin', String(p.margin))
        view.renderer.setAttribute('max-inline-size', '720')
        view.renderer.setAttribute('max-column-count', '1')
        view.renderer.setStyles?.(readerCss(p))
        setToc(view.book.toc ?? [])

        const saved = getProgress(book.id)
        if (saved?.cfi) {
          try {
            await view.goTo(saved.cfi)
          } catch {
            await view.renderer.next()
          }
        } else {
          await view.renderer.next()
        }
        if (!cancelled) setStatus('ready')
      } catch (err) {
        if (cancelled) return
        if (isAuthError(err)) onAuthError(err)
        setErrorMsg(
          err instanceof Error && err.message
            ? err.message
            : 'Could not open this book — the file may be missing or not a valid EPUB.',
        )
        setStatus('error')
      }
    })()

    return () => {
      cancelled = true
      flushSave()
      try {
        viewRef.current?.close()
        viewRef.current?.remove()
      } catch {
        // already gone
      }
      viewRef.current = null
    }
  }, [book.id, token, flushSave, retryNonce, onAuthError])

  // --- re-apply typography/theme on change ------------------------------
  useEffect(() => {
    if (status !== 'ready') return
    const view = viewRef.current
    if (!view) return
    view.renderer.setStyles?.(readerCss(prefs))
    view.renderer.setAttribute('margin', String(prefs.margin))
    saveReaderPrefs(prefs)
  }, [prefs, status])

  // --- body scroll lock while the reader is mounted --------------------
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [])

  // --- keyboard ------------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (panel !== 'none') setPanel('none')
        else onClose()
        return
      }
      const view = viewRef.current
      if (!view) return
      if (e.key === 'ArrowLeft' || e.key === 'PageUp') void view.goLeft()
      else if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') void view.goRight()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, panel])

  // --- auto-hide chrome ------------------------------------------------
  useEffect(() => {
    if (!chrome || panel !== 'none') return
    const t = setTimeout(() => setChrome(false), CHROME_HIDE_MS)
    return () => clearTimeout(t)
  }, [chrome, panel, pos.fraction])

  // --- tap / swipe zones --------------------------------------------------
  const touch = useRef<{ x: number; y: number; t: number } | null>(null)
  const onTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0]
    if (t.clientX < EDGE_GUARD_PX || t.clientX > window.innerWidth - EDGE_GUARD_PX) {
      touch.current = null
      return
    }
    touch.current = { x: t.clientX, y: t.clientY, t: Date.now() }
  }
  const onTouchEnd = (e: React.TouchEvent) => {
    const start = touch.current
    touch.current = null
    if (!start) return
    const end = e.changedTouches[0]
    const dx = end.clientX - start.x
    const dy = end.clientY - start.y
    if (Math.abs(dx) > 45 && Math.abs(dy) < 60 && Date.now() - start.t < 500) {
      if (dx > 0) void viewRef.current?.goRight()
      else void viewRef.current?.goLeft()
    }
  }

  const onZoneClick = (e: React.MouseEvent) => {
    const x = e.clientX / window.innerWidth
    if (x < 0.32) void viewRef.current?.goLeft()
    else if (x > 0.68) void viewRef.current?.goRight()
    else setChrome((c) => !c)
  }

  const theme = themeColors(prefs.theme)
  const flatToc = useMemo(() => tocFlat(toc), [toc])
  const pct = Math.round(pos.fraction * 100)

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col"
      style={{ background: theme.bg, color: theme.fg }}
    >
      {/* top bar */}
      <div
        className={`absolute inset-x-0 top-0 z-20 flex items-center gap-2 border-b px-3 py-2 backdrop-blur transition-transform ${
          chrome ? 'translate-y-0' : '-translate-y-full'
        }`}
        style={{ background: `${theme.bg}ee`, borderColor: `${theme.fg}22` }}
      >
        <button className="btn btn-ghost btn-xs" onClick={onClose}>
          ‹ Library
        </button>
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{book.title}</span>
        <button
          className="btn btn-ghost btn-xs"
          onClick={() => setPanel(panel === 'toc' ? 'none' : 'toc')}
        >
          Contents
        </button>
        <button
          className="btn btn-ghost btn-xs"
          onClick={() => setPanel(panel === 'prefs' ? 'none' : 'prefs')}
        >
          Aa
        </button>
      </div>

      {/* reader surface */}
      <div className="relative flex-1 overflow-hidden">
        <div ref={hostRef} className="absolute inset-0" />
        {status === 'ready' && (
          <div
            className="absolute inset-0 z-10"
            onClick={onZoneClick}
            onTouchStart={onTouchStart}
            onTouchEnd={onTouchEnd}
            style={{ touchAction: 'pan-y' }}
          />
        )}
        {status === 'loading' && (
          <div className="absolute inset-0 flex items-center justify-center text-sm opacity-70">
            Opening “{book.title}”…
          </div>
        )}
        {status === 'error' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center">
            <p className="max-w-sm text-sm">{errorMsg}</p>
            <div className="flex flex-wrap items-center justify-center gap-2">
              <button
                className="btn btn-primary btn-xs"
                onClick={() => {
                  setDownloadState('idle')
                  setRetryNonce((n) => n + 1)
                }}
              >
                Try again
              </button>
              <button
                className="btn btn-neutral btn-xs"
                disabled={downloadState === 'saving'}
                onClick={async () => {
                  setDownloadState('saving')
                  try {
                    await downloadFile(token, book.file)
                    setDownloadState('done')
                  } catch {
                    setDownloadState('failed')
                  }
                }}
              >
                {downloadState === 'saving'
                  ? 'Downloading…'
                  : downloadState === 'done'
                    ? 'Downloaded ✓'
                    : downloadState === 'failed'
                      ? 'Download failed — retry'
                      : 'Download the file'}
              </button>
              <button className="btn btn-ghost btn-xs" onClick={onClose}>
                Back to library
              </button>
            </div>
            {downloadState === 'done' && (
              <p className="max-w-sm text-xs opacity-60">
                Saved to your device — open it in any EPUB reader.
              </p>
            )}
          </div>
        )}
      </div>

      {/* bottom bar */}
      <div
        className={`absolute inset-x-0 bottom-0 z-20 flex items-center gap-3 border-t px-4 py-2 backdrop-blur transition-transform ${
          chrome ? 'translate-y-0' : 'translate-y-full'
        }`}
        style={{ background: `${theme.bg}ee`, borderColor: `${theme.fg}22` }}
      >
        <input
          type="range"
          min={0}
          max={1}
          step={0.001}
          value={pos.fraction}
          onChange={(e) => void viewRef.current?.goToFraction(parseFloat(e.target.value))}
          className="flex-1 accent-brand-600"
          aria-label="Reading position"
        />
        <span className="shrink-0 text-xs tabular-nums opacity-70">
          {pct}%{pos.label ? ` · ${pos.label}` : ''}
        </span>
      </div>

      {/* TOC panel */}
      {panel === 'toc' && (
        <ReaderPanel title="Contents" onClose={() => setPanel('none')} theme={prefs.theme}>
          {flatToc.length === 0 ? (
            <p className="px-4 py-3 text-sm opacity-60">No table of contents.</p>
          ) : (
            <ul className="py-1">
              {flatToc.map(({ item, depth }, i) => (
                <li key={i}>
                  <button
                    className="block w-full truncate px-4 py-2 text-left text-sm hover:bg-black/5 dark:hover:bg-white/10"
                    style={{ paddingLeft: `${16 + depth * 14}px` }}
                    onClick={() => {
                      void viewRef.current?.goTo(item.href)
                      setPanel('none')
                    }}
                  >
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </ReaderPanel>
      )}

      {/* prefs panel */}
      {panel === 'prefs' && (
        <ReaderPanel title="Display" onClose={() => setPanel('none')} theme={prefs.theme}>
          <div className="space-y-4 p-4 text-sm">
            <Stepper
              label="Text size"
              value={`${prefs.fontSize}%`}
              onDec={() => setPrefs((p) => ({ ...p, fontSize: Math.max(80, p.fontSize - 10) }))}
              onInc={() => setPrefs((p) => ({ ...p, fontSize: Math.min(180, p.fontSize + 10) }))}
            />
            <label className="block">
              <span className="mb-1 block opacity-70">Typeface</span>
              <select
                className="field w-full"
                value={prefs.font}
                onChange={(e) => setPrefs((p) => ({ ...p, font: e.target.value as ReaderFont }))}
              >
                <option value="publisher">Publisher default</option>
                <option value="serif">Serif</option>
                <option value="sans">Sans-serif</option>
              </select>
            </label>
            <Slider
              label="Line spacing"
              min={1.2}
              max={2}
              step={0.1}
              value={prefs.lineHeight}
              onChange={(v) => setPrefs((p) => ({ ...p, lineHeight: v }))}
            />
            <Slider
              label="Margin"
              min={20}
              max={100}
              step={4}
              value={prefs.margin}
              onChange={(v) => setPrefs((p) => ({ ...p, margin: v }))}
            />
            <div>
              <span className="mb-1 block opacity-70">Theme</span>
              <div className="flex gap-2">
                {(['light', 'sepia', 'dark'] as ReaderTheme[]).map((t) => (
                  <button
                    key={t}
                    className={`flex-1 rounded-md border px-2 py-2 text-xs capitalize ${
                      prefs.theme === t ? 'ring-2 ring-brand-500' : ''
                    }`}
                    style={{ background: themeColors(t).bg, color: themeColors(t).fg }}
                    onClick={() => setPrefs((p) => ({ ...p, theme: t }))}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
            <button
              className="btn btn-ghost btn-xs"
              onClick={() => setPrefs({ ...DEFAULT_PREFS })}
            >
              Reset to defaults
            </button>
          </div>
        </ReaderPanel>
      )}
    </div>
  )
}

function ReaderPanel({
  title,
  onClose,
  theme,
  children,
}: {
  title: string
  onClose: () => void
  theme: ReaderTheme
  children: React.ReactNode
}) {
  const c = themeColors(theme)
  return (
    <div className="absolute inset-0 z-30 flex">
      <button className="flex-1 bg-black/30" aria-label="Close panel" onClick={onClose} />
      <div
        className="flex w-80 max-w-[85vw] flex-col overflow-y-auto border-l shadow-xl"
        style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}22` }}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-3"
          style={{ borderColor: `${c.fg}22` }}
        >
          <span className="text-sm font-semibold">{title}</span>
          <button className="btn btn-ghost btn-xs" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function Stepper({
  label,
  value,
  onDec,
  onInc,
}: {
  label: string
  value: string
  onDec: () => void
  onInc: () => void
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="opacity-70">{label}</span>
      <div className="flex items-center gap-2">
        <button className="btn btn-neutral btn-xs" onClick={onDec}>
          −
        </button>
        <span className="w-12 text-center tabular-nums">{value}</span>
        <button className="btn btn-neutral btn-xs" onClick={onInc}>
          +
        </button>
      </div>
    </div>
  )
}

function Slider({
  label,
  min,
  max,
  step,
  value,
  onChange,
}: {
  label: string
  min: number
  max: number
  step: number
  value: number
  onChange: (v: number) => void
}) {
  return (
    <label className="block">
      <span className="mb-1 flex justify-between opacity-70">
        <span>{label}</span>
        <span className="tabular-nums">{value}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full accent-brand-600"
      />
    </label>
  )
}
