import { useState } from 'react'
import { BACKEND_URL } from '../lib/config'

interface Props {
  busy: boolean
  onRefresh: () => void
  onRebuild: () => void
  offlineCount: number
  onClearDownloads: () => void
  shareStatus: string | null
  onShare: () => void
  onCopyLink: () => void
  onEditAccount: () => void
  onShowSetup: () => void
  onForget: () => void
  onBack: () => void
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
        {title}
      </h2>
      <div className="mt-3 space-y-2">{children}</div>
    </section>
  )
}

// The former "⋯" dropdown's account/admin/misc actions, now their own
// screen reached from the sidebar's single "Settings" row. Display toggles
// (what shows on the shelf) live in the sidebar itself, not here.
export function SettingsScreen({
  busy,
  onRefresh,
  onRebuild,
  offlineCount,
  onClearDownloads,
  shareStatus,
  onShare,
  onCopyLink,
  onEditAccount,
  onShowSetup,
  onForget,
  onBack,
}: Props) {
  const [backendCopyStatus, setBackendCopyStatus] = useState<string | null>(null)

  async function copyBackendUrl() {
    if (!BACKEND_URL) return
    try {
      await navigator.clipboard.writeText(BACKEND_URL)
      setBackendCopyStatus('Copied!')
    } catch {
      setBackendCopyStatus(`Copy failed — here's the link: ${BACKEND_URL}`)
    }
    setTimeout(() => setBackendCopyStatus(null), 5000)
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">Settings</h1>

      <div className="mt-5 space-y-4">
        <Section title="Account & devices">
          <p className="text-sm text-neutral-500">
            Google account, library folder, and Kobo devices.
          </p>
          <button className="btn btn-neutral" onClick={onEditAccount}>
            Edit settings
          </button>
        </Section>

        <Section title="Library">
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-neutral" disabled={busy} onClick={onRefresh}>
              {busy ? 'Syncing…' : 'Refresh'}
            </button>
            <button className="btn btn-neutral" disabled={busy} onClick={onRebuild}>
              Rebuild library
            </button>
            {offlineCount > 0 && (
              <button className="btn btn-neutral" onClick={onClearDownloads}>
                Clear downloaded books ({offlineCount})
              </button>
            )}
          </div>
        </Section>

        <Section title="Sharing">
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-neutral" onClick={onShare}>
              Share…
            </button>
            <button className="btn btn-neutral" onClick={onCopyLink}>
              Copy link
            </button>
          </div>
          {shareStatus && <p className="text-xs text-neutral-400">{shareStatus}</p>}
        </Section>

        <Section title="Backend">
          {BACKEND_URL ? (
            <>
              <p className="text-sm text-neutral-500">
                What the household passcode sign-in talks to. Siblings never need to see or type
                this themselves — it's already baked into the app — this is just so it's on
                record somewhere.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <code className="rounded bg-neutral-100 px-2 py-1 text-xs break-all dark:bg-neutral-800">
                  {BACKEND_URL}
                </code>
                <button className="btn btn-neutral" onClick={copyBackendUrl}>
                  Copy
                </button>
              </div>
              {backendCopyStatus && <p className="text-xs text-neutral-400">{backendCopyStatus}</p>}
            </>
          ) : (
            <p className="text-sm text-neutral-500">
              Passcode sign-in isn't set up yet — no backend URL is configured.
            </p>
          )}
        </Section>

        <Section title="Admin">
          <button
            className="btn btn-neutral"
            onClick={() => window.open('http://homeserver:8000/', '_blank', 'noopener,noreferrer')}
          >
            Admin panel
          </button>
        </Section>

        <Section title="Help">
          <button
            className="text-sm text-neutral-500 underline underline-offset-2 hover:text-neutral-700 dark:hover:text-neutral-300"
            onClick={onShowSetup}
          >
            Lost your hard drive? Recovery checklist
          </button>
        </Section>

        <Section title="Danger zone">
          <button className="btn btn-danger" onClick={onForget}>
            Forget this device
          </button>
        </Section>
      </div>
    </div>
  )
}
