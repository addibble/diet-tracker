import type {
  ExerciseHistorySet,
  ExerciseSchemeHistory,
  ExerciseSchemeHistoryEntry,
  RepScheme,
} from '../api'

export const REP_SCHEME_ORDER: RepScheme[] = ['heavy', 'medium', 'volume']

export function repSchemeLabel(scheme: string): string {
  if (scheme === 'heavy') return 'heavy'
  if (scheme === 'medium') return 'medium'
  if (scheme === 'volume') return 'volume'
  return scheme
}

export function repSchemeColor(scheme: string): string {
  if (scheme === 'heavy') return 'bg-red-100 text-red-700'
  if (scheme === 'volume') return 'bg-blue-100 text-blue-700'
  return 'bg-green-100 text-green-700'
}

export function formatSchemeHistorySummary(
  history: ExerciseSchemeHistory | null | undefined,
): string | null {
  if (!history) return null
  const parts = REP_SCHEME_ORDER
    .map((scheme) => formatSchemeHistoryEntry(history[scheme]))
    .filter(Boolean)
  if (parts.length === 0) return null
  return parts.join(' · ')
}

export function formatSchemeHistoryEntry(
  entry: ExerciseSchemeHistoryEntry | null | undefined,
): string | null {
  if (!entry) return null
  const sets = entry.sets.slice(0, 3).map(formatHistorySet).filter(Boolean).join(', ')
  if (!sets) return null
  return `${repSchemeLabel(entry.rep_scheme)} ${formatHistoryDate(entry.date)}: ${sets}`
}

function formatHistorySet(setRow: ExerciseHistorySet): string | null {
  if (setRow.reps != null) {
    return setRow.weight != null
      ? `${setRow.reps} @ ${formatWeight(setRow.weight)} lb`
      : `${setRow.reps}`
  }
  if (setRow.duration_secs != null) return `${setRow.duration_secs}s`
  if (setRow.distance_steps != null) return `${setRow.distance_steps} steps`
  return null
}

function formatHistoryDate(value: string): string {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  })
}

function formatWeight(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}
