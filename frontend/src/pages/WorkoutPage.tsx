import React, { useEffect, useMemo, useState } from 'react'
import {
  getTrainingModelSummary,
  getWorkoutSessions,
  getExerciseHistory,
  getExercises,
  type TrainingModelExerciseInsight,
  type TrainingModelSummary,
  type TrainingModelTissueHistory,
  type WkTissueReadiness,
  type WkSession,
  type WkExerciseHistory,
  type WkExercise,
} from '../api'

// ── Helpers ──

function repDot(completion: string | null): string {
  if (completion === 'full') return 'bg-green-500'
  if (completion === 'partial') return 'bg-yellow-500'
  if (completion === 'failed') return 'bg-red-500'
  return 'bg-gray-300'
}

function hoursLabel(hours: number | null): string {
  if (hours === null) return 'Never'
  if (hours < 1) return '<1h ago'
  if (hours < 24) return `${Math.round(hours)}h ago`
  return `${Math.round(hours / 24)}d ago`
}

function groupSetsByExercise(sets: WkSession['sets']) {
  const map = new Map<string, typeof sets>()
  for (const s of sets) {
    const list = map.get(s.exercise_name) || []
    list.push(s)
    map.set(s.exercise_name, list)
  }
  return map
}

// ── Tissue Status Table ──

type SortKey = 'name' | 'status' | 'last_worked' | 'recovery' | 'volume_7d'

function recoveryBarClass(pct: number, status: string | undefined): string {
  if (status === 'injured' || status === 'tender') return 'bg-purple-400'
  if (pct >= 100) return 'bg-emerald-400'
  if (pct >= 75) return 'bg-yellow-400'
  return 'bg-red-400'
}

function statusBadge(condition: WkTissueReadiness['condition']): React.ReactNode {
  if (!condition || condition.status === 'healthy') return null
  const cfg: Record<string, string> = {
    tender:    'bg-amber-100 text-amber-700 border-amber-200',
    injured:   'bg-red-100 text-red-700 border-red-200',
    rehabbing: 'bg-purple-100 text-purple-700 border-purple-200',
  }
  return (
    <span className={`inline-block text-[10px] font-medium px-1.5 py-px rounded border leading-tight ${cfg[condition.status] ?? 'bg-gray-100 text-gray-600 border-gray-200'}`}>
      {condition.status}
    </span>
  )
}

export function TissueStatusTable({ readiness }: { readiness: WkTissueReadiness[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('recovery')
  const [sortAsc, setSortAsc] = useState(true)

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc((a) => !a)
    else { setSortKey(key); setSortAsc(key === 'name' || key === 'status') }
  }

  const sorted = useMemo(() => {
    return [...readiness].sort((a, b) => {
      let cmp = 0
      if (sortKey === 'name')       cmp = a.tissue.display_name.localeCompare(b.tissue.display_name)
      else if (sortKey === 'status')  cmp = (a.condition?.status ?? 'healthy').localeCompare(b.condition?.status ?? 'healthy')
      else if (sortKey === 'last_worked') cmp = (a.hours_since ?? Infinity) - (b.hours_since ?? Infinity)
      else if (sortKey === 'recovery')    cmp = a.recovery_pct - b.recovery_pct
      else if (sortKey === 'volume_7d')   cmp = a.volume_7d - b.volume_7d
      return sortAsc ? cmp : -cmp
    })
  }, [readiness, sortKey, sortAsc])

  const SortBtn = ({ k, label }: { k: SortKey; label: string }) => (
    <button
      onClick={() => toggleSort(k)}
      className={`flex items-center gap-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] transition-colors select-none ${sortKey === k ? 'text-gray-700' : 'text-gray-400 hover:text-gray-600'}`}
    >
      {label}
      {sortKey === k && <span className="opacity-60">{sortAsc ? '↑' : '↓'}</span>}
    </button>
  )

  if (readiness.length === 0) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">Tissue Status</p>
        <p className="text-sm text-gray-500 mt-2">No tissue data yet.</p>
      </section>
    )
  }

  return (
    <section className="bg-white border border-gray-200 rounded-2xl overflow-hidden">
      {/* Header bar */}
      <div className="px-5 pt-5 pb-3 border-b border-gray-100">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">Tissue Status</p>
        {/* Column headers / sort controls */}
        <div className="mt-2 grid items-center" style={{ gridTemplateColumns: '1fr 70px 64px 80px 72px' }}>
          <SortBtn k="name" label="Tissue" />
          <SortBtn k="status" label="Status" />
          <SortBtn k="last_worked" label="Last" />
          <SortBtn k="recovery" label="Recovery" />
          <div className="text-right"><SortBtn k="volume_7d" label="7d Vol" /></div>
        </div>
      </div>

      <div className="divide-y divide-gray-50">
        {sorted.map((r, i) => (
          <TissueRow key={r.tissue.id} r={r} striped={i % 2 === 1} />
        ))}
      </div>
    </section>
  )
}

function TissueRow({
  r,
  striped,
}: {
  r: WkTissueReadiness
  striped: boolean
}) {
  const barClass = recoveryBarClass(r.recovery_pct, r.condition?.status)

  return (
    <div
      className={`grid items-center px-5 py-2 text-xs transition-colors hover:bg-gray-50/80 ${striped ? 'bg-gray-50/40' : ''}`}
      style={{ gridTemplateColumns: '1fr 70px 64px 80px 72px' }}
    >
      {/* Name */}
      <span className="text-gray-700 font-medium truncate pr-2">
        {r.tissue.display_name}
      </span>

      {/* Status */}
      <span>{statusBadge(r.condition) ?? <span className="text-[10px] text-gray-300">—</span>}</span>

      {/* Last worked */}
      <span className="text-gray-400 tabular-nums">{hoursLabel(r.hours_since)}</span>

      {/* Recovery bar + % */}
      <div className="flex items-center gap-1.5">
        <div className="relative h-1.5 w-10 rounded-full bg-gray-100 overflow-hidden flex-shrink-0">
          <div
            className={`absolute inset-y-0 left-0 rounded-full ${barClass} transition-all duration-500`}
            style={{ width: `${Math.min(100, r.recovery_pct)}%` }}
          />
        </div>
        <span className={`tabular-nums font-medium ${barClass.replace('bg-', 'text-').replace('-400', '-600')}`}>
          {Math.round(r.recovery_pct)}%
        </span>
      </div>

      {/* 7d volume */}
      <div className="text-right tabular-nums">
        {r.volume_7d > 0 ? (
          <span className="text-gray-600 font-medium">
            {r.volume_7d >= 1000
              ? `${(r.volume_7d / 1000).toFixed(1)}k`
              : r.volume_7d.toLocaleString()}
          </span>
        ) : (
          <span className="text-gray-300">—</span>
        )}
      </div>
    </div>
  )
}

// ── Today's Suggested Workout ──

function SuggestedWorkoutCard({
  trainingExercises,
}: {
  trainingExercises: TrainingModelExerciseInsight[]
}) {
  const active = [...trainingExercises]
    .filter((e) => e.in_active_program)
    .sort((a, b) => {
      const leftRank = recommendationSortRank(a.recommendation)
      const rightRank = recommendationSortRank(b.recommendation)
      if (leftRank !== rightRank) return leftRank - rightRank
      return b.suitability_score - a.suitability_score
    })

  if (active.length === 0) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Today's Workout
        </p>
        <p className="text-sm text-gray-500 mt-2">
          No program exercises. Use the chat to set up your training program.
        </p>
      </section>
    )
  }

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
        Today's Workout
      </p>
      <div className="mt-3 space-y-2">
        {active.map((e) => (
          <div
            key={e.id}
            className="rounded-xl bg-gray-50 border border-gray-100 px-3 py-2"
          >
            <div className="flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium text-gray-800 truncate">
                    {e.name}
                  </p>
                  <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${recommendationClass(e.recommendation)}`}>
                    {e.recommendation}
                  </span>
                </div>
                <p className="mt-1 text-[11px] text-gray-600">
                  {e.recommendation_reason}
                </p>
              </div>
            </div>
            {e.recommendation_details.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {e.recommendation_details.slice(0, 3).map((detail) => (
                  <span
                    key={detail}
                    className="rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10px] font-medium text-gray-600"
                  >
                    {detail}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function pctClass(value: number): string {
  if (value >= 75) return 'text-red-600 bg-red-50 border-red-100'
  if (value >= 55) return 'text-amber-700 bg-amber-50 border-amber-100'
  return 'text-emerald-700 bg-emerald-50 border-emerald-100'
}

export function TrainingModelCard({
  summary,
  history,
  selectedTissueId,
  onSelectTissue,
}: {
  summary: TrainingModelSummary | null
  history: TrainingModelTissueHistory | null
  selectedTissueId: number | null
  onSelectTissue: (value: number | null) => void
}) {
  if (!summary) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Training Model
        </p>
        <p className="text-sm text-gray-500 mt-2">No training model data yet.</p>
      </section>
    )
  }

  const atRisk = summary.tissues.filter((t) => t.risk_7d >= 60).slice(0, 6)
  const recovering = summary.tissues
    .filter((t) => t.recovery_estimate >= 0.75 && t.normalized_load < 0.8)
    .slice(0, 6)
  const selected = history?.tissue.id ?? selectedTissueId ?? summary.tissues[0]?.tissue.id ?? null
    const recentHistory = history?.history.slice(-8) ?? []

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4">
      <div className="flex flex-wrap items-start gap-3 justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
            Training Model
          </p>
          <p className="text-sm text-gray-500 mt-1">
            Forecasting tissue trouble before collapse from normalized load, ramp, and rebound history.
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <span className="rounded-full border border-red-100 bg-red-50 px-3 py-1 text-xs font-medium text-red-700">
            {summary.overview.at_risk_count} at risk
          </span>
          <span className="rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
            {summary.overview.recovering_count} recovering well
          </span>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-400">At-Risk Soon</p>
          <div className="mt-3 space-y-2">
            {atRisk.length === 0 && (
              <p className="text-sm text-gray-500">No tissues currently above the alert threshold.</p>
            )}
            {atRisk.map((item) => (
              <button
                key={item.tissue.id}
                onClick={() => onSelectTissue(item.tissue.id)}
                className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-left hover:border-gray-300 transition-colors"
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{item.tissue.display_name}</p>
                    <p className="text-xs text-gray-500">
                      Norm load {item.normalized_load.toFixed(2)} · ramp {item.ramp_ratio.toFixed(2)}
                    </p>
                  </div>
                  <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${pctClass(item.risk_7d)}`}>
                    {item.risk_7d}% / 7d
                  </span>
                </div>
                {item.contributors.length > 0 && (
                  <p className="mt-1 text-[11px] text-gray-500">
                    Drivers: {item.contributors.join(', ')}
                  </p>
                )}
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-400">Recovering Well</p>
          <div className="mt-3 space-y-2">
            {recovering.length === 0 && (
              <p className="text-sm text-gray-500">No tissues currently in a strong rebound state.</p>
            )}
            {recovering.map((item) => (
              <button
                key={item.tissue.id}
                onClick={() => onSelectTissue(item.tissue.id)}
                className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-left hover:border-gray-300 transition-colors"
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{item.tissue.display_name}</p>
                    <p className="text-xs text-gray-500">
                      Recovery {Math.round(item.recovery_estimate * 100)}% · learned {item.learned_recovery_days.toFixed(1)}d
                    </p>
                  </div>
                  <span className="rounded-full border border-emerald-100 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
                    {item.risk_14d}% / 14d
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-gray-100 p-4">
        <div className="flex flex-wrap items-center gap-3 justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-400">Tissue History</p>
            <p className="text-sm text-gray-500 mt-1">
              Current learned capacity vs normalized demand and flagged collapse dates.
            </p>
          </div>
          <select
            value={selected ?? ''}
            onChange={(e) => onSelectTissue(e.target.value ? Number(e.target.value) : null)}
            className="w-full max-w-xs rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 bg-white"
          >
            <option value="">Select a tissue...</option>
            {summary.tissues.map((item) => (
              <option key={item.tissue.id} value={item.tissue.id}>
                {item.tissue.display_name}
              </option>
            ))}
          </select>
        </div>

        {history && (
          <div className="mt-4 space-y-3">
            <div className="grid gap-3 md:grid-cols-4">
              <StatChip label="Capacity" value={history.history.at(-1)?.capacity_state.toFixed(2) ?? '—'} />
              <StatChip label="Norm Load" value={history.history.at(-1)?.normalized_load.toFixed(2) ?? '—'} />
              <StatChip label="Risk 7d" value={`${history.history.at(-1)?.risk_7d ?? 0}%`} />
              <StatChip label="Recovery" value={`${Math.round((history.history.at(-1)?.recovery_state ?? 0) * 100)}%`} />
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-400">Recent Collapse Windows</p>
              <p className="mt-1 text-sm text-gray-600">
                {history.collapse_dates.length > 0
                  ? history.collapse_dates.slice(-5).join(', ')
                  : 'No collapse windows flagged in this range.'}
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-[0.14em] text-gray-400">
                    <th className="py-2 pr-3">Date</th>
                    <th className="py-2 pr-3">Norm</th>
                    <th className="py-2 pr-3">Cap</th>
                    <th className="py-2 pr-3">Risk7</th>
                    <th className="py-2 pr-3">Collapse</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {[...recentHistory].reverse().map((point) => (
                    <tr key={point.date}>
                      <td className="py-2 pr-3 text-gray-700">{point.date}</td>
                      <td className="py-2 pr-3 text-gray-600">{point.normalized_load.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-gray-600">{point.capacity_state.toFixed(2)}</td>
                      <td className="py-2 pr-3">
                        <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${pctClass(point.risk_7d)}`}>
                          {point.risk_7d}%
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-gray-600">{point.collapse_flag ? 'Yes' : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

export function StatChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-400">{label}</p>
      <p className="mt-1 text-lg font-semibold text-gray-800">{value}</p>
    </div>
  )
}

function recommendationClass(value: TrainingModelExerciseInsight['recommendation']): string {
  if (value === 'avoid') return 'text-red-700 bg-red-50 border-red-100'
  if (value === 'caution') return 'text-amber-700 bg-amber-50 border-amber-100'
  return 'text-emerald-700 bg-emerald-50 border-emerald-100'
}

function recommendationSortRank(value: TrainingModelExerciseInsight['recommendation'] | undefined): number {
  if (value === 'good') return 0
  if (value === 'caution') return 1
  if (value === 'avoid') return 2
  return 3
}

function ExerciseRiskBoard({
  summary,
  exercises,
}: {
  summary: TrainingModelSummary | null
  exercises: TrainingModelExerciseInsight[]
}) {
  if (!summary) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Today by Exercise
        </p>
        <p className="text-sm text-gray-500 mt-2">No exercise risk data yet.</p>
      </section>
    )
  }

  const programFirst = [
    ...exercises.filter((item) => item.in_active_program),
    ...exercises.filter((item) => !item.in_active_program),
  ]
  const avoid = [...programFirst]
    .filter((item) => item.recommendation === 'avoid')
    .sort((a, b) => b.weighted_risk_7d - a.weighted_risk_7d)
    .slice(0, 6)
  const caution = [...programFirst]
    .filter((item) => item.recommendation === 'caution')
    .sort((a, b) => b.weighted_risk_7d - a.weighted_risk_7d)
    .slice(0, 6)
  const good = [...programFirst]
    .filter((item) => item.recommendation === 'good')
    .sort((a, b) => b.suitability_score - a.suitability_score)
    .slice(0, 6)

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
            Today by Exercise
          </p>
          <p className="text-sm text-gray-500 mt-1">
            Ranked directly from tissue risk, ramp, and recovery so you can choose exercises without decoding tissue rows.
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <span className="rounded-full border border-red-100 bg-red-50 px-3 py-1 text-xs font-medium text-red-700">
            {summary.overview.at_risk_count} tissues at risk
          </span>
          <span className="rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
            {summary.overview.recovering_count} recovering well
          </span>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ExerciseColumn title="Avoid Today" items={avoid} emptyText="No exercises are currently flagged to avoid." />
        <ExerciseColumn title="Use Caution" items={caution} emptyText="No exercises currently sit in the caution band." />
        <ExerciseColumn title="Good Candidates" items={good} emptyText="No low-risk candidates available yet." />
      </div>
    </section>
  )
}

function ExerciseColumn({
  title,
  items,
  emptyText,
}: {
  title: string
  items: TrainingModelExerciseInsight[]
  emptyText: string
}) {
  return (
    <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-4">
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-400">{title}</p>
      <div className="mt-3 space-y-2">
        {items.length === 0 && (
          <p className="text-sm text-gray-500">{emptyText}</p>
        )}
        {items.map((item) => (
          <div key={item.id} className="rounded-xl border border-gray-200 bg-white px-3 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-800 truncate">{item.name}</p>
                <p className="text-xs text-gray-500">
                  Risk {item.weighted_risk_7d.toFixed(0)}% · suitability {item.suitability_score.toFixed(0)}
                </p>
              </div>
              <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${recommendationClass(item.recommendation)}`}>
                {item.recommendation}
              </span>
            </div>
            <p className="mt-2 text-[11px] text-gray-600">
              {item.recommendation_reason}
            </p>
            {item.recommendation_details.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {item.recommendation_details.slice(0, 3).map((detail) => (
                  <span
                    key={detail}
                    className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-600"
                  >
                    {detail}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Recent Sessions ──

function RecentSessionsCard({ sessions }: { sessions: WkSession[] }) {
  const [expandedId, setExpandedId] = useState<number | null>(null)

  if (sessions.length === 0) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Recent Sessions
        </p>
        <p className="text-sm text-gray-500 mt-2">No workout sessions logged yet.</p>
      </section>
    )
  }

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
        Recent Sessions
      </p>
      <div className="mt-3 space-y-2">
        {sessions.map((ws) => {
          const exerciseMap = groupSetsByExercise(ws.sets)
          const totalVolume = ws.sets.reduce(
            (sum, s) => sum + (s.reps || 0) * (s.weight || 0),
            0,
          )
          const isExpanded = expandedId === ws.id

          return (
            <div key={ws.id} className="rounded-xl border border-gray-200">
              <button
                onClick={() => setExpandedId(isExpanded ? null : ws.id)}
                className="w-full text-left px-3 py-2 flex items-center gap-3"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800">
                    {new Date(ws.date + 'T12:00:00').toLocaleDateString('en-US', {
                      weekday: 'short',
                      month: 'short',
                      day: 'numeric',
                    })}
                  </p>
                  <p className="text-xs text-gray-500">
                    {exerciseMap.size} exercise{exerciseMap.size !== 1 ? 's' : ''}
                    {totalVolume > 0 && ` · ${Math.round(totalVolume).toLocaleString()} lbs vol`}
                  </p>
                </div>
                <div className="flex gap-0.5">
                  {ws.sets
                    .filter((s) => s.rep_completion)
                    .slice(0, 8)
                    .map((s, i) => (
                      <span
                        key={i}
                        className={`w-2 h-2 rounded-full ${repDot(s.rep_completion)}`}
                      />
                    ))}
                </div>
                <span className="text-gray-400 text-xs">{isExpanded ? '−' : '+'}</span>
              </button>
              {isExpanded && (
                <div className="px-3 pb-3 space-y-2">
                  {Array.from(exerciseMap.entries()).map(([name, sets]) => (
                    <div key={name}>
                      <p className="text-xs font-medium text-gray-700">{name}</p>
                      <div className="flex flex-wrap gap-1 mt-0.5">
                        {sets.map((s) => (
                          <span
                            key={s.id}
                            className="text-[11px] text-gray-500 bg-gray-50 rounded px-1.5 py-0.5"
                          >
                            {s.reps != null && s.weight != null
                              ? `${s.weight}×${s.reps}`
                              : s.duration_secs != null
                                ? `${s.duration_secs}s`
                                : '—'}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                  {ws.notes && (
                    <p className="text-xs text-gray-400 italic">{ws.notes}</p>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── Exercise Progress Chart ──

function ExerciseProgressCard({
  exercises,
}: {
  exercises: WkExercise[]
}) {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [history, setHistory] = useState<WkExerciseHistory | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (selectedId === null) {
      setHistory(null)
      return
    }
    setLoading(true)
    getExerciseHistory(selectedId, 20)
      .then(setHistory)
      .catch(() => setHistory(null))
      .finally(() => setLoading(false))
  }, [selectedId])

  const chartData = useMemo(() => {
    if (!history || history.sessions.length === 0) return null
    const sessions = [...history.sessions].reverse() // oldest first
    const maxWeight = Math.max(...sessions.map((s) => s.max_weight))
    const maxVolume = Math.max(...sessions.map((s) => s.total_volume))
    return { sessions, maxWeight, maxVolume }
  }, [history])

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
        Exercise Progress
      </p>
      <select
        className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 bg-white"
        value={selectedId ?? ''}
        onChange={(e) =>
          setSelectedId(e.target.value ? Number(e.target.value) : null)
        }
      >
        <option value="">Select an exercise...</option>
        {exercises.map((ex) => (
          <option key={ex.id} value={ex.id}>
            {ex.name}
          </option>
        ))}
      </select>

      {loading && (
        <p className="text-sm text-gray-400 mt-3">Loading...</p>
      )}

      {chartData && (
        <div className="mt-3">
          {/* Max Weight Line Chart */}
          <p className="text-xs text-gray-500 mb-1">Max Weight (lbs)</p>
          <svg viewBox="0 0 340 140" className="w-full h-36">
            <WeightLineChart
              data={chartData.sessions.map((s) => ({
                label: s.date.slice(5),
                value: s.max_weight,
              }))}
              maxValue={chartData.maxWeight}
              width={340}
              height={140}
              color="#0f766e"
            />
          </svg>

          {/* Volume Bar Chart */}
          <p className="text-xs text-gray-500 mt-3 mb-1">Volume (lbs)</p>
          <svg viewBox="0 0 340 120" className="w-full h-28">
            <VolumeBarChart
              data={chartData.sessions.map((s) => ({
                label: s.date.slice(5),
                value: s.total_volume,
              }))}
              maxValue={chartData.maxVolume}
              width={340}
              height={120}
            />
          </svg>
        </div>
      )}

      {selectedId && !loading && !chartData && (
        <p className="text-sm text-gray-400 mt-3">No history for this exercise.</p>
      )}
    </section>
  )
}

// ── SVG Chart Components ──

function WeightLineChart({
  data,
  maxValue,
  width,
  height,
  color,
}: {
  data: { label: string; value: number }[]
  maxValue: number
  width: number
  height: number
  color: string
}) {
  const margin = { top: 12, right: 16, bottom: 24, left: 32 }
  const plotW = width - margin.left - margin.right
  const plotH = height - margin.top - margin.bottom
  const padding = maxValue * 0.15
  const scaledMax = maxValue + padding
  const scaledMin = Math.max(0, Math.min(...data.map((d) => d.value)) - padding)
  const range = scaledMax - scaledMin || 1
  const step = data.length > 1 ? plotW / (data.length - 1) : plotW

  const toX = (i: number) => margin.left + i * step
  const toY = (v: number) => margin.top + ((scaledMax - v) / range) * plotH

  const points = data.map((d, i) => `${toX(i)},${toY(d.value)}`).join(' ')

  // Y-axis guides
  const ySteps = 3
  const guides = Array.from({ length: ySteps + 1 }, (_, i) => {
    const val = scaledMin + (range / ySteps) * i
    return { val, y: toY(val) }
  })

  return (
    <g>
      {guides.map((g, i) => (
        <g key={i}>
          <line
            x1={margin.left}
            x2={width - margin.right}
            y1={g.y}
            y2={g.y}
            stroke="#e5e7eb"
            strokeDasharray="3,3"
          />
          <text x={margin.left - 4} y={g.y + 3} textAnchor="end" className="text-[9px] fill-gray-400">
            {Math.round(g.val)}
          </text>
        </g>
      ))}
      <polyline points={points} fill="none" stroke={color} strokeWidth={2.5} strokeLinejoin="round" />
      {data.map((d, i) => (
        <g key={i}>
          <circle cx={toX(i)} cy={toY(d.value)} r={3} fill={color} />
          {data.length <= 12 && (
            <text
              x={toX(i)}
              y={height - 4}
              textAnchor="middle"
              className="text-[8px] fill-gray-400"
            >
              {d.label}
            </text>
          )}
        </g>
      ))}
    </g>
  )
}

function VolumeBarChart({
  data,
  maxValue,
  width,
  height,
}: {
  data: { label: string; value: number }[]
  maxValue: number
  width: number
  height: number
}) {
  const margin = { top: 8, right: 16, bottom: 20, left: 32 }
  const plotW = width - margin.left - margin.right
  const plotH = height - margin.top - margin.bottom
  const barW = Math.max(4, plotW / data.length - 2)
  const step = plotW / data.length
  const max = maxValue || 1

  return (
    <g>
      <line
        x1={margin.left}
        x2={width - margin.right}
        y1={margin.top + plotH}
        y2={margin.top + plotH}
        stroke="#e5e7eb"
      />
      {data.map((d, i) => {
        const barH = (d.value / max) * plotH
        const x = margin.left + i * step + (step - barW) / 2
        const y = margin.top + plotH - barH
        return (
          <g key={i}>
            <rect x={x} y={y} width={barW} height={barH} rx={2} fill="#3b82f6" opacity={0.7} />
            {data.length <= 12 && (
              <text
                x={margin.left + i * step + step / 2}
                y={height - 4}
                textAnchor="middle"
                className="text-[8px] fill-gray-400"
              >
                {d.label}
              </text>
            )}
          </g>
        )
      })}
      <text x={margin.left - 4} y={margin.top + 6} textAnchor="end" className="text-[9px] fill-gray-400">
        {Math.round(max).toLocaleString()}
      </text>
      <text x={margin.left - 4} y={margin.top + plotH + 3} textAnchor="end" className="text-[9px] fill-gray-400">
        0
      </text>
    </g>
  )
}

// ── Main Page ──

export default function WorkoutPage() {
  const [trainingModel, setTrainingModel] = useState<TrainingModelSummary | null>(null)
  const [trainingExercises, setTrainingExercises] = useState<TrainingModelExerciseInsight[]>([])
  const [sessions, setSessions] = useState<WkSession[]>([])
  const [exercises, setExercises] = useState<WkExercise[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const [tm, s, ex] = await Promise.all([
          getTrainingModelSummary(undefined, true),
          getWorkoutSessions(undefined, undefined, 10),
          getExercises(),
        ])
        setTrainingModel(tm)
        setSessions(s)
        setExercises(ex)
        const ranked = [...tm.exercises].sort((a, b) => b.suitability_score - a.suitability_score)
        setTrainingExercises(ranked)
      } catch {
        // reset
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-sm text-gray-400">Loading workout data...</p>
      </div>
    )
  }

  return (
    <div className="space-y-4 pb-4 overflow-y-auto h-full">
      <ExerciseRiskBoard summary={trainingModel} exercises={trainingExercises} />
      <SuggestedWorkoutCard trainingExercises={trainingExercises} />
      <RecentSessionsCard sessions={sessions} />
      <ExerciseProgressCard exercises={exercises} />
    </div>
  )
}
