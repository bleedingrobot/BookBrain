import { useState } from 'react'
import { SUGGESTED_NAMES } from '../lib/viewerIdentity'

// One-time per-browser gate so the activity log can label events with a
// name. Self-reported, not verified — see viewerIdentity.ts.
export function WhoAmI({ onPick }: { onPick: (name: string) => void }) {
  const [other, setOther] = useState('')

  return (
    <div className="mx-auto max-w-sm px-6 pt-24 pb-12 text-center">
      <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="mx-auto h-12 w-12" />
      <h1 className="mt-5 text-xl font-semibold tracking-tight">Who's this?</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        Used to label the activity log — who searched, downloaded, or sent a book. Remembered on
        this browser only; asked just once.
      </p>
      <div className="mt-6 flex flex-col gap-2">
        {SUGGESTED_NAMES.map((name) => (
          <button key={name} className="btn btn-primary" onClick={() => onPick(name)}>
            {name}
          </button>
        ))}
      </div>
      <form
        className="mt-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (other.trim()) onPick(other.trim())
        }}
      >
        <input
          className="field min-w-0 flex-1"
          placeholder="Someone else…"
          value={other}
          onChange={(e) => setOther(e.target.value)}
        />
        <button type="submit" className="btn btn-neutral shrink-0" disabled={!other.trim()}>
          Use this
        </button>
      </form>
    </div>
  )
}
