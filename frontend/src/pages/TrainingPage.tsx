import { useState, useEffect, useCallback } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import {
  getTrainingModelSummary,
  getRecoveryCheckIns,
  createRecoveryCheckIn,
  getRegions,
  getExerciseStrength,
  getPlannerToday,
  type TrainingModelSummary,
  type TrainingModelTissueSummary,
  type TrainingModelExerciseInsight,
  type RecoveryCheckIn,
  type RegionInfo,
  type ExerciseStrength,
  type PlannerTodayResponse,
  type PlannerExercisePrescription,
} from '../api'

// ── Helpers ──

const today = () => new Date().toISOString().slice(0, 10)

const riskColor = (r: number) =>
  r >= 75 ? 'text-red-600' : r >= 55 ? 'text-amber-600' : r >= 30 ? 'text-yellow-600' : 'text-emerald-600'

const riskBg = (r: number) =>
  r >= 75 ? 'bg-red-50 border-red-200' : r >= 55 ? 'bg-amber-50 border-amber-200' : r >= 30 ? 'bg-yellow-50 border-yellow-200' : 'bg-emerald-50 border-emerald-200'

const recBadge = (rec: string) => {
  if (rec === 'avoid') return 'bg-red-100 text-red-700 border-red-200'
  if (rec === 'caution') return 'bg-amber-100 text-amber-700 border-amber-200'
  return 'bg-emerald-100 text-emerald-700 border-emerald-200'
}

const trendIcon = (t: string) => t === 'rising' ? '\u2197' : t === 'falling' ? '\u2198' : '\u2192'
const trendColor = (t: string) => t === 'rising' ? 'text-emerald-600' : t === 'falling' ? 'text-red-600' : 'text-gray-500'

const regionLabel = (r: string) => r.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

const pctBar = (value: number, max: number, color: string) => (
  <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden w-full">
    <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
  </div>
)

// ── Skeleton ──

function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse bg-gray-200 rounded ${className}`} />
}

function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3">
      <Skeleton className="h-5 w-32" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={`h-4 ${i === lines - 1 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

// ── Recovery Check-in Card ──

// Map 0-3 UI scale to 0-10 DB scale
const SEVERITY_MAP = [0, 4, 7, 10] as const
const SEVERITY_LABELS = ['None', 'Some', 'Substantial', 'Severe'] as const
const SEVERITY_COLORS = [
  'bg-emerald-100 text-emerald-700 border-emerald-300',
  'bg-yellow-100 text-yellow-700 border-yellow-300',
  'bg-amber-100 text-amber-700 border-amber-300',
  'bg-red-100 text-red-700 border-red-300',
] as const

// Reverse-map 0-10 DB value back to 0-3 UI scale
const dbToSeverity = (v: number): number =>
  v >= 9 ? 3 : v >= 6 ? 2 : v >= 3 ? 1 : 0

function CheckInCard({
  regions,
  existingCheckIns,
  onSubmit,
}: {
  regions: RegionInfo[]
  existingCheckIns: RecoveryCheckIn[]
  onSubmit: () => void
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const [soreness, setSoreness] = useState(0)  // 0-3
  const [pain, setPain] = useState(0)           // 0-3
  const [stiffness, setStiffness] = useState(0) // 0-3
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<Set<string>>(new Set())

  // Build lookup map from region → check-in for pre-population
  const checkInByRegion = Object.fromEntries(existingCheckIns.map(c => [c.region, c]))

  useEffect(() => {
    setSaved(new Set(existingCheckIns.map(c => c.region)))
  }, [existingCheckIns])

  const selectRegion = (region: string | null) => {
    setSelected(region)
    if (region && checkInByRegion[region]) {
      const ci = checkInByRegion[region]
      setSoreness(dbToSeverity(ci.soreness_0_10))
      setPain(dbToSeverity(ci.pain_0_10))
      setStiffness(dbToSeverity(ci.stiffness_0_10))
    } else {
      setSoreness(0)
      setPain(0)
      setStiffness(0)
    }
  }

  // Auto-calculate readiness: start at 10, subtract for each nonzero category
  const readiness = Math.max(0, 10
    - (soreness > 0 ? soreness + 1 : 0)
    - (pain > 0 ? pain + 1 : 0)
    - (stiffness > 0 ? stiffness : 0)
  )

  const submit = async () => {
    if (!selected) return
    setSaving(true)
    try {
      await createRecoveryCheckIn({
        date: today(),
        region: selected,
        soreness_0_10: SEVERITY_MAP[soreness],
        pain_0_10: SEVERITY_MAP[pain],
        stiffness_0_10: SEVERITY_MAP[stiffness],
        readiness_0_10: readiness,
      })
      setSaved(prev => new Set([...prev, selected]))
      setSelected(null)
      setSoreness(0)
      setPain(0)
      setStiffness(0)
      onSubmit()
    } finally {
      setSaving(false)
    }
  }

  const SeverityRow = ({ label, value, onChange }: {
    label: string; value: number; onChange: (v: number) => void;
  }) => (
    <div className="space-y-1.5">
      <span className="text-xs font-medium text-gray-600">{label}</span>
      <div className="grid grid-cols-4 gap-1.5">
        {SEVERITY_LABELS.map((lbl, i) => (
          <button
            key={i}
            onClick={() => onChange(i)}
            className={`py-1.5 text-xs font-medium rounded-lg border transition-all ${
              value === i
                ? SEVERITY_COLORS[i]
                : 'bg-white border-gray-200 text-gray-400 hover:border-gray-300'
            }`}
          >
            {lbl}
          </button>
        ))}
      </div>
    </div>
  )

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <h3 className="text-sm font-semibold text-gray-900 mb-3">Recovery Check-in</h3>
      <div className="flex flex-wrap gap-1.5 mb-4">
        {regions.map(r => {
          const done = saved.has(r.region)
          const active = selected === r.region
          return (
            <button
              key={r.region}
              onClick={() => selectRegion(active ? null : r.region)}
              className={`px-2.5 py-1 text-xs rounded-lg border transition-all ${
                done
                  ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                  : active
                    ? 'bg-gray-900 border-gray-900 text-white'
                    : 'bg-white border-gray-200 text-gray-600 hover:border-gray-400'
              }`}
            >
              {done ? '\u2713 ' : ''}{regionLabel(r.region)}
            </button>
          )
        })}
      </div>
      {selected && (
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-gray-700">{regionLabel(selected)}</p>
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
              readiness >= 8 ? 'bg-emerald-100 text-emerald-700' :
              readiness >= 5 ? 'bg-yellow-100 text-yellow-700' :
              'bg-red-100 text-red-700'
            }`}>Readiness: {readiness}/10</span>
          </div>
          <SeverityRow label="Soreness" value={soreness} onChange={setSoreness} />
          <SeverityRow label="Pain" value={pain} onChange={setPain} />
          <SeverityRow label="Stiffness" value={stiffness} onChange={setStiffness} />
          <button
            onClick={submit}
            disabled={saving}
            className="w-full py-2 bg-gray-900 text-white text-sm font-medium rounded-lg hover:bg-gray-800 disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      )}
    </div>
  )
}

// ── Tissue Risk Overview ──

function TissueRiskCard({ tissues }: { tissues: TrainingModelTissueSummary[] }) {
  const atRisk = tissues.filter(t => t.risk_7d >= 55)
  const recovering = tissues.filter(t => t.risk_7d < 30 && t.recovery_estimate >= 0.75)
  const mid = tissues.filter(t => t.risk_7d >= 30 && t.risk_7d < 55)

  const TissueRow = ({ t }: { t: TrainingModelTissueSummary }) => (
    <div className={`flex items-center gap-3 p-2.5 rounded-lg border ${riskBg(t.risk_7d)}`}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-900 truncate">{t.tissue.display_name}</span>
          {t.current_condition && t.current_condition.status !== 'healthy' && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
              t.current_condition.status === 'injured' ? 'bg-red-200 text-red-800' :
              t.current_condition.status === 'tender' ? 'bg-amber-200 text-amber-800' :
              'bg-blue-200 text-blue-800'
            }`}>{t.current_condition.status}</span>
          )}
        </div>
        <div className="flex gap-3 mt-1 text-[11px] text-gray-500">
          <span>Cap: {t.capacity_trend_30d_pct > 0 ? '+' : ''}{t.capacity_trend_30d_pct}%/30d</span>
          <span>Rec: {Math.round(t.recovery_estimate * 100)}%</span>
          {t.contributors[0] && <span className="text-gray-400">{t.contributors[0]}</span>}
        </div>
      </div>
      <div className="text-right shrink-0">
        <span className={`text-lg font-bold tabular-nums ${riskColor(t.risk_7d)}`}>{t.risk_7d}</span>
        <span className="text-[10px] text-gray-400 ml-0.5">%</span>
      </div>
    </div>
  )

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <h3 className="text-sm font-semibold text-gray-900 mb-3">Tissue Risk Overview</h3>
      {atRisk.length > 0 && (
        <div className="mb-3">
          <p className="text-[10px] uppercase tracking-wider text-red-600 font-semibold mb-1.5">Elevated Risk</p>
          <div className="space-y-1.5">{atRisk.map(t => <TissueRow key={t.tissue.id} t={t} />)}</div>
        </div>
      )}
      {mid.length > 0 && (
        <div className="mb-3">
          <p className="text-[10px] uppercase tracking-wider text-amber-600 font-semibold mb-1.5">Monitor</p>
          <div className="space-y-1.5">{mid.map(t => <TissueRow key={t.tissue.id} t={t} />)}</div>
        </div>
      )}
      {recovering.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-emerald-600 font-semibold mb-1.5">Recovered</p>
          <div className="space-y-1.5">{recovering.slice(0, 5).map(t => <TissueRow key={t.tissue.id} t={t} />)}</div>
          {recovering.length > 5 && (
            <p className="text-[11px] text-gray-400 mt-1.5">+{recovering.length - 5} more recovered tissues</p>
          )}
        </div>
      )}
      {tissues.length === 0 && <p className="text-sm text-gray-400">No tissue data available</p>}
    </div>
  )
}

// ── Exercise Recommendations ──

function ExerciseRecCard({ exercises }: { exercises: TrainingModelExerciseInsight[] }) {
  const [filter, setFilter] = useState<'all' | 'avoid' | 'caution' | 'good'>('all')
  const filtered = filter === 'all' ? exercises : exercises.filter(e => e.recommendation === filter)
  const counts = {
    avoid: exercises.filter(e => e.recommendation === 'avoid').length,
    caution: exercises.filter(e => e.recommendation === 'caution').length,
    good: exercises.filter(e => e.recommendation === 'good').length,
  }

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <h3 className="text-sm font-semibold text-gray-900 mb-3">Exercise Recommendations</h3>
      <div className="flex gap-1.5 mb-3">
        {([['all', 'All', 'bg-gray-100 text-gray-700'], ['good', `Good (${counts.good})`, 'bg-emerald-100 text-emerald-700'], ['caution', `Caution (${counts.caution})`, 'bg-amber-100 text-amber-700'], ['avoid', `Avoid (${counts.avoid})`, 'bg-red-100 text-red-700']] as const).map(([key, label, colors]) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`px-2.5 py-1 text-xs rounded-lg border transition-all ${
              filter === key ? `${colors} border-current font-semibold` : 'bg-white border-gray-200 text-gray-500 hover:border-gray-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="space-y-1.5 max-h-80 overflow-y-auto">
        {filtered.map(ex => (
          <div key={ex.id} className="flex items-center gap-3 p-2.5 rounded-lg border border-gray-100 bg-gray-50/50 hover:bg-gray-50 transition-colors">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-gray-900 truncate">{ex.name}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${recBadge(ex.recommendation)}`}>
                  {ex.recommendation}
                </span>
              </div>
              <div className="flex gap-3 mt-0.5 text-[11px] text-gray-500">
                {ex.equipment && <span>{ex.equipment}</span>}
                {ex.current_e1rm && <span>e1RM: {ex.current_e1rm} lb</span>}
                <span>suit: {Math.round(ex.suitability_score)}%</span>
              </div>
              {ex.recommendation_details.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {ex.recommendation_details.slice(0, 2).map((d, i) => (
                    <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{d}</span>
                  ))}
                </div>
              )}
            </div>
            <div className="text-right shrink-0">
              <span className={`text-base font-bold tabular-nums ${riskColor(ex.weighted_risk_7d)}`}>
                {Math.round(ex.weighted_risk_7d)}
              </span>
              <span className="text-[10px] text-gray-400 ml-0.5">%</span>
            </div>
          </div>
        ))}
        {filtered.length === 0 && <p className="text-sm text-gray-400 py-2">No exercises in this category</p>}
      </div>
    </div>
  )
}

// ── Strength Trends ──

function StrengthCard({
  exercises,
  strengthData,
  onSelect,
  selectedId,
}: {
  exercises: TrainingModelExerciseInsight[]
  strengthData: ExerciseStrength | null
  onSelect: (id: number) => void
  selectedId: number | null
}) {
  const withE1rm = exercises.filter(e => e.current_e1rm != null).sort((a, b) => (b.current_e1rm ?? 0) - (a.current_e1rm ?? 0))
  if (withE1rm.length === 0) return null

  // Simple SVG sparkline for e1RM history
  const Sparkline = ({ data }: { data: { date: string; e1rm: number }[] }) => {
    if (data.length < 2) return null
    const values = data.map(d => d.e1rm)
    const min = Math.min(...values) * 0.95
    const max = Math.max(...values) * 1.05
    const w = 200
    const h = 40
    const points = data.map((d, i) => {
      const x = (i / (data.length - 1)) * w
      const y = h - ((d.e1rm - min) / (max - min)) * h
      return `${x},${y}`
    }).join(' ')
    return (
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-10">
        <polyline points={points} fill="none" stroke="#059669" strokeWidth="2" strokeLinejoin="round" />
        {data.length > 0 && (() => {
          const lastX = w
          const lastY = h - ((values[values.length - 1] - min) / (max - min)) * h
          return <circle cx={lastX} cy={lastY} r="3" fill="#059669" />
        })()}
      </svg>
    )
  }

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <h3 className="text-sm font-semibold text-gray-900 mb-3">Strength Trends</h3>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mb-3">
        {withE1rm.slice(0, 9).map(ex => (
          <button
            key={ex.id}
            onClick={() => onSelect(ex.id)}
            className={`p-2.5 rounded-lg border text-left transition-all ${
              selectedId === ex.id ? 'border-gray-900 bg-gray-50' : 'border-gray-100 hover:border-gray-300'
            }`}
          >
            <p className="text-xs font-medium text-gray-900 truncate">{ex.name}</p>
            <p className="text-lg font-bold tabular-nums text-gray-900">{ex.current_e1rm} <span className="text-xs font-normal text-gray-400">lb</span></p>
            {ex.peak_e1rm && ex.peak_e1rm > (ex.current_e1rm ?? 0) && (
              <p className="text-[10px] text-gray-400">peak: {ex.peak_e1rm}</p>
            )}
          </button>
        ))}
      </div>
      {strengthData && (
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-900">{strengthData.exercise_name}</span>
            <span className={`text-sm font-semibold ${trendColor(strengthData.trend)}`}>
              {trendIcon(strengthData.trend)} {strengthData.trend_pct > 0 ? '+' : ''}{strengthData.trend_pct}%
            </span>
          </div>
          <div className="flex gap-4 text-xs text-gray-500 mb-3">
            <span>Current: <strong className="text-gray-900">{strengthData.current_e1rm} lb</strong></span>
            <span>Peak: <strong className="text-gray-900">{strengthData.peak_e1rm} lb</strong></span>
          </div>
          {strengthData.history.length >= 2 && <Sparkline data={strengthData.history} />}
        </div>
      )}
    </div>
  )
}

// ── Planner Card ──

function buildChatPrompt(dayLabel: string, exercises: PlannerExercisePrescription[]): string {
  if (!exercises.length) return `I just finished my ${dayLabel} workout.`
  const lines = exercises.map(ex => {
    const weight = ex.target_weight != null ? ` @ ${ex.target_weight} lb` : ''
    return `${ex.exercise_name}: ${ex.target_sets}x${ex.target_reps}${weight}`
  })
  return `I just finished my ${dayLabel} workout:\n${lines.join('\n')}`
}

function PlannerCard({ planner, onRefresh }: { planner: PlannerTodayResponse; onRefresh?: () => void }) {
  const [copied, setCopied] = useState(false)

  const copyToChat = (dayLabel: string, exercises: PlannerExercisePrescription[]) => {
    const text = buildChatPrompt(dayLabel, exercises)
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  if (!planner.suggestion) {
    return (
      <div className="bg-white border border-gray-200 rounded-2xl p-5">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-gray-900">Today's Plan</h3>
          {onRefresh && (
            <button onClick={onRefresh} className="text-[10px] text-gray-400 hover:text-gray-600">refresh</button>
          )}
        </div>
        <p className="text-sm text-gray-500">{planner.message || 'No exercises available for planning.'}</p>
      </div>
    )
  }

  const s = planner.suggestion
  const exercises = s.exercises || []
  const schemeColor = (scheme: string) =>
    scheme === 'heavy' ? 'bg-red-100 text-red-700' :
    scheme === 'volume' ? 'bg-blue-100 text-blue-700' :
    'bg-green-100 text-green-700'

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-gray-900">Today's Plan</h3>
          <span className="text-xs px-2 py-0.5 rounded-lg bg-gray-900 text-white font-medium">{s.day_label}</span>
        </div>
        <div className="flex items-center gap-2">
          {onRefresh && (
            <button onClick={onRefresh} className="text-[10px] text-gray-400 hover:text-gray-600">↺ refresh</button>
          )}
          <span className="text-xs text-gray-500">{Math.round(s.readiness_score * 100)}% ready</span>
        </div>
      </div>

      {/* Target regions */}
      {s.target_regions.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3 mt-2">
          {s.target_regions.map(r => (
            <span key={r} className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600 font-medium">
              {regionLabel(r)}
            </span>
          ))}
        </div>
      )}

      {/* Exercises */}
      {exercises.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 p-4 text-center mb-3">
          <p className="text-xs text-gray-500 mb-1">No exercises selected — tissue mappings may not be configured.</p>
          <p className="text-[11px] text-gray-400">Ask the assistant to map exercises to tissues.</p>
        </div>
      ) : (
        <div className="space-y-2 mb-3">
          {exercises.map((ex, i) => (
            <div key={ex.exercise_id ?? i} className="flex items-start gap-3 p-2.5 rounded-lg border border-gray-100 bg-gray-50/50">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-sm font-medium text-gray-900">{ex.exercise_name}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${schemeColor(ex.rep_scheme)}`}>
                    {ex.rep_scheme}
                  </span>
                </div>
                <div className="text-[11px] text-gray-600 mt-0.5 font-medium">
                  {ex.target_sets} × {ex.target_reps}
                  {ex.target_weight != null && <> @ <span className="text-gray-900">{ex.target_weight} lb</span></>}
                </div>
                {ex.overload_note && (
                  <div className="text-[10px] text-amber-600 mt-0.5">{ex.overload_note}</div>
                )}
              </div>
              <div className="text-right shrink-0 text-[10px] text-gray-400 max-w-24 leading-tight">
                {ex.rationale}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Log workout action */}
      {exercises.length > 0 && (
        <button
          onClick={() => copyToChat(s.day_label, exercises)}
          className="w-full py-2 text-xs font-medium rounded-xl border border-gray-200 bg-gray-50 hover:bg-gray-100 text-gray-700 transition-colors mb-3"
        >
          {copied ? '✓ Copied — paste into chat after your workout' : '📋 Copy workout to log in chat'}
        </button>
      )}

      {/* Footer */}
      <div className="space-y-1 border-t border-gray-100 pt-2">
        {s.tomorrow_outlook && (
          <p className="text-[11px] text-gray-500">{s.tomorrow_outlook}</p>
        )}
        {planner.alternatives.length > 0 && (
          <p className="text-[11px] text-gray-400">
            Alt: {planner.alternatives.map(a => `${a.day_label} (${Math.round(a.readiness_score * 100)}%)`).join(' · ')}
          </p>
        )}
      </div>
    </div>
  )
}

// ── Capacity Trends ──

function CapacityCard({ tissues }: { tissues: TrainingModelTissueSummary[] }) {
  const sorted = [...tissues]
    .filter(t => t.capacity_trend_30d_pct !== 0)
    .sort((a, b) => b.capacity_trend_30d_pct - a.capacity_trend_30d_pct)

  if (sorted.length === 0) return null

  const growing = sorted.filter(t => t.capacity_trend_30d_pct > 0)
  const declining = sorted.filter(t => t.capacity_trend_30d_pct < 0)

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <h3 className="text-sm font-semibold text-gray-900 mb-3">Capacity Trends <span className="font-normal text-gray-400">30d</span></h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {growing.length > 0 && (
          <div>
            <p className="text-[10px] uppercase tracking-wider text-emerald-600 font-semibold mb-1.5">Growing</p>
            <div className="space-y-1.5">
              {growing.slice(0, 6).map(t => (
                <div key={t.tissue.id} className="flex items-center gap-2">
                  <span className="text-xs text-gray-700 flex-1 truncate">{t.tissue.display_name}</span>
                  <span className="text-xs font-semibold tabular-nums text-emerald-600">+{t.capacity_trend_30d_pct}%</span>
                  <div className="w-16">{pctBar(t.capacity_trend_30d_pct, 20, 'bg-emerald-400')}</div>
                </div>
              ))}
            </div>
          </div>
        )}
        {declining.length > 0 && (
          <div>
            <p className="text-[10px] uppercase tracking-wider text-red-600 font-semibold mb-1.5">Declining</p>
            <div className="space-y-1.5">
              {declining.slice(0, 6).map(t => (
                <div key={t.tissue.id} className="flex items-center gap-2">
                  <span className="text-xs text-gray-700 flex-1 truncate">{t.tissue.display_name}</span>
                  <span className="text-xs font-semibold tabular-nums text-red-600">{t.capacity_trend_30d_pct}%</span>
                  <div className="w-16">{pctBar(Math.abs(t.capacity_trend_30d_pct), 20, 'bg-red-400')}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main Page ──

export default function TrainingPage() {
  // Quick-loading state (no model computation needed)
  const [regions, setRegions] = useState<RegionInfo[] | null>(null)
  const [checkIns, setCheckIns] = useState<RecoveryCheckIn[]>([])
  const [quickLoaded, setQuickLoaded] = useState(false)

  // Model-dependent state (slower to load)
  const [modelSummary, setModelSummary] = useState<TrainingModelSummary | null>(null)
  const [modelLoading, setModelLoading] = useState(true)

  // Planner state
  const [planner, setPlanner] = useState<PlannerTodayResponse | null>(null)
  const [plannerLoading, setPlannerLoading] = useState(true)

  // Strength drill-down
  const [selectedExerciseId, setSelectedExerciseId] = useState<number | null>(null)
  const [strengthData, setStrengthData] = useState<ExerciseStrength | null>(null)

  // Quick data load (regions, routine, check-ins)
  useEffect(() => {
    let cancelled = false
    Promise.all([
      getRegions().catch(() => []),
      getRecoveryCheckIns(today()).catch(() => []),
    ]).then(([regionsData, checkInData]) => {
      if (cancelled) return
      setRegions(regionsData as RegionInfo[])
      setCheckIns(checkInData as RecoveryCheckIn[])
      setQuickLoaded(true)
    })
    return () => { cancelled = true }
  }, [])

  // Model data load (starts immediately but takes longer)
  useEffect(() => {
    let cancelled = false
    setModelLoading(true)
    getTrainingModelSummary(undefined, true).then(data => {
      if (cancelled) return
      setModelSummary(data)
      setModelLoading(false)
    }).catch(() => { if (!cancelled) setModelLoading(false) })
    return () => { cancelled = true }
  }, [])

  // Planner load
  useEffect(() => {
    let cancelled = false
    setPlannerLoading(true)
    getPlannerToday().then(data => {
      if (cancelled) return
      setPlanner(data)
      setPlannerLoading(false)
    }).catch(() => { if (!cancelled) { setPlanner(null); setPlannerLoading(false) } })
    return () => { cancelled = true }
  }, [])

  // Strength drill-down
  const loadStrength = useCallback((exerciseId: number) => {
    setSelectedExerciseId(exerciseId)
    getExerciseStrength(exerciseId).then(setStrengthData).catch(() => setStrengthData(null))
  }, [])

  const refreshPlanner = useCallback(() => {
    setPlannerLoading(true)
    getPlannerToday().then(data => {
      setPlanner(data)
      setPlannerLoading(false)
    }).catch(() => { setPlanner(null); setPlannerLoading(false) })
  }, [])

  const refreshCheckIns = useCallback(() => {
    getRecoveryCheckIns(today()).then(setCheckIns).catch(() => {})
    // Re-plan after check-in since readiness may have changed
    refreshPlanner()
  }, [refreshPlanner])

  return (
    <ScrollablePage>
      <div className="space-y-4 pb-4">
        {/* Header */}
        <div className="flex items-baseline justify-between">
          <h1 className="text-lg font-bold text-gray-900">Training</h1>
          <span className="text-xs text-gray-400 tabular-nums">{today()}</span>
        </div>

        {/* Row 1: Quick-loading sections */}
        {!quickLoaded ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <CardSkeleton lines={4} />
            <CardSkeleton lines={3} />
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Recovery Check-in */}
            {regions && (
              <CheckInCard
                regions={regions}
                existingCheckIns={checkIns}
                onSubmit={refreshCheckIns}
              />
            )}
            {/* Today's Plan */}
            {plannerLoading ? <CardSkeleton lines={4} /> : planner && <PlannerCard planner={planner} onRefresh={refreshPlanner} />}
          </div>
        )}

        {/* Row 2: Model-dependent sections */}
        {modelLoading ? (
          <>
            <div className="flex items-center gap-2 text-xs text-gray-400">
              <div className="w-3 h-3 border-2 border-gray-300 border-t-gray-600 rounded-full animate-spin" />
              Computing training model...
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <CardSkeleton lines={5} />
              <CardSkeleton lines={5} />
            </div>
            <CardSkeleton lines={3} />
          </>
        ) : modelSummary ? (
          <>
            {/* Overview stats */}
            <div className="flex gap-3 flex-wrap">
              <div className="px-3 py-1.5 rounded-lg bg-red-50 border border-red-200">
                <span className="text-xs text-red-600 font-medium">{modelSummary.overview.at_risk_count} at risk</span>
              </div>
              <div className="px-3 py-1.5 rounded-lg bg-emerald-50 border border-emerald-200">
                <span className="text-xs text-emerald-600 font-medium">{modelSummary.overview.recovering_count} recovering</span>
              </div>
              <div className="px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200">
                <span className="text-xs text-gray-600 font-medium">{modelSummary.overview.tracked_tissues} tracked</span>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Tissue Risk */}
              <TissueRiskCard tissues={modelSummary.tissues} />
              {/* Exercise Recommendations */}
              <ExerciseRecCard exercises={modelSummary.exercises} />
            </div>

            {/* Capacity Trends */}
            <CapacityCard tissues={modelSummary.tissues} />

            {/* Strength Trends */}
            {modelSummary.exercises.length > 0 && (
              <StrengthCard
                exercises={modelSummary.exercises}
                strengthData={strengthData}
                onSelect={loadStrength}
                selectedId={selectedExerciseId}
              />
            )}
          </>
        ) : (
          <div className="bg-white border border-gray-200 rounded-2xl p-5">
            <p className="text-sm text-gray-500">Training model could not be loaded. Log some workouts to get started.</p>
          </div>
        )}
      </div>
    </ScrollablePage>
  )
}
