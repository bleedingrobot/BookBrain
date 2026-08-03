import type { ReactNode } from 'react'

function Step({ title, children }: { title: string; children: ReactNode }) {
  return (
    <li className="border-t border-neutral-200 py-4 first:border-t-0 dark:border-neutral-800">
      <h2 className="text-sm font-medium">{title}</h2>
      <div className="mt-1.5 space-y-1.5 text-sm text-neutral-600 dark:text-neutral-400">{children}</div>
    </li>
  )
}

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="block overflow-x-auto rounded bg-neutral-100 px-2 py-1 font-mono text-xs dark:bg-neutral-800">
      {children}
    </code>
  )
}

export function SetupChecklist({ onBack }: { onBack: () => void }) {
  return (
    <div className="mx-auto max-w-2xl p-6">
      <button className="text-xs text-neutral-400 underline" onClick={onBack}>
        &larr; Back
      </button>

      <h1 className="mt-3 text-xl font-semibold">Recovering on a new computer</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Your actual books already live safely in Google Drive, and the code is safely in GitHub —
        losing a hard drive doesn't lose either of those. This page is reachable from any browser,
        on any machine, without anything installed, specifically so it survives that scenario too.
      </p>

      <div className="mt-4 rounded border border-amber-300 p-3 text-sm dark:border-amber-800">
        <p className="font-medium text-amber-700 dark:text-amber-400">Do this now, before disaster strikes</p>
        <p className="mt-1 text-neutral-600 dark:text-neutral-400">
          Save these four values in a password manager. They're technically all re-fetchable from
          their respective consoles later, but the Google client secret specifically can't be
          re-viewed once created — only regenerated — so saving it now avoids that trip entirely.
        </p>
        <ul className="mt-2 list-inside list-disc text-neutral-600 dark:text-neutral-400">
          <li>Google OAuth Client ID + Client Secret (Google Cloud Console)</li>
          <li>Anthropic API key (console.anthropic.com)</li>
          <li>Google Books API key (Google Cloud Console)</li>
        </ul>
      </div>

      <ol className="mt-6">
        <Step title="1. Install prerequisites">
          <p>Git, Node.js 20+, Python 3.11+, and (optional — for mobi/rtf conversion) Calibre.</p>
        </Step>

        <Step title="2. Clone the repo">
          <Code>git clone https://github.com/bleedingrobot/BookBrain.git</Code>
        </Step>

        <Step title="3. Set up the backend">
          <Code>cd backend</Code>
          <Code>python -m venv .venv</Code>
          <Code>.venv\Scripts\pip install -e ".[dev]"</Code>
          <p>
            Copy <code>.env.example</code> to <code>.env</code> and fill in the four saved values
            above, plus a fresh <code>TOKEN_ENCRYPTION_KEY</code> — the command to generate one is
            in a comment right above that line in the file.
          </p>
          <Code>.venv\Scripts\alembic upgrade head</Code>
          <p>
            Then run it — either <code>backend\start-dev.bat</code>, or directly:
          </p>
          <Code>.venv\Scripts\uvicorn app.main:app --reload --port 8000</Code>
        </Step>

        <Step title="4. Set up the frontend">
          <Code>cd frontend</Code>
          <Code>npm install</Code>
          <p>
            Then run it — either <code>frontend\start-dev.bat</code> (starts both backend and
            frontend), or directly:
          </p>
          <Code>npm run dev</Code>
        </Step>

        <Step title="5. Reconnect Google Drive">
          <p>
            Open the app, go to Settings, and connect to Google Drive. Pick the same Book Dump
            (inbox) folder and library folder you used before — nothing on Drive itself was ever
            touched by losing the hard drive, so they're both still exactly where you left them.
          </p>
        </Step>

        <Step title="6. Rebuild the library">
          <p>
            Click <strong>Rebuild library</strong> on the Library page. It walks your already-organized
            Author/Series folder tree on Drive and reconstructs every Book, Author, Series, and
            File record from what's actually there — this is what makes the local database
            disposable rather than something you need to back up.
          </p>
        </Step>

        <Step title="7. Run a scan">
          <p>
            Catches anything that was still sitting in Book Dump, mid-review, or mid-organize at
            the moment the drive was lost, and routes it back through the normal pipeline.
          </p>
        </Step>
      </ol>

      <p className="mt-6 text-xs text-neutral-400">
        What doesn't come back: review decisions, cleared-duplicate history, and the operations
        undo log — all just local activity logs, not your books or their organization. Anything
        still genuinely needing a decision reappears through the scan in step 7.
      </p>
    </div>
  )
}
