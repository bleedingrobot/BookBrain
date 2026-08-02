export function ConfidenceBar({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="text-xs text-neutral-400">no score</span>
  }

  const color =
    value >= 85
      ? 'bg-green-500'
      : value >= 70
        ? 'bg-amber-500'
        : 'bg-red-500'

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
        <div className={`h-full ${color}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </div>
      <span className="text-xs text-neutral-500">{value}</span>
    </div>
  )
}
