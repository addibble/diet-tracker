import { useState, useEffect, useCallback, useMemo } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import SymptomSeverityRow from '../components/SymptomSeverityRow'
import {
  symptomDbToSeverity,
  symptomSeverityToDb,
  type SymptomSeverityLevel,
} from '../components/symptomSeverity'
import WorkoutSetEditor from '../components/WorkoutSetEditor'
import {
  getTrainingModelSummary,
  createRecoveryCheckIn,
  getRecoveryCheckInTargets,
  getExercises,
  getExerciseHistory,
  getPlannerToday,
  savePlan,
  getActivePlan,
  startPlan,
  completePlan,
  deletePlan,
  type TrainingModelSummary,
  type TrainingModelTissueSummary,
  type TrainingModelExerciseInsight,
  type RecoveryCheckInTarget,
  type RecoveryCheckInTargetsResponse,
  type WkExercise,
  type WkExerciseHistory,
  type PlannerTodayResponse,
  type PlannerExercisePrescription,
  type SavedPlan,
} from '../api'

// ── Helpers ──

function today() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const riskColor = (r: number) =>
  r >= 75 ? 'text-red-600' : r >= 55 ? 'text-amber-600' : r >= 30 ? 'text-yellow-600' : 'text-emerald-600'

const riskBg = (r: number) =>
  r >= 75 ? 'bg-red-50 border-red-200' : r >= 55 ? 'bg-amber-50 border-amber-200' : r >= 30 ? 'bg-yellow-50 border-yellow-200' : 'bg-emerald-50 border-emerald-200'

const recBadge = (rec: string) => {
  if (rec === 'avoid') return 'bg-red-100 text-red-700 border-red-200'
  if (rec === 'caution') return 'bg-amber-100 text-amber-700 border-amber-200'
  return 'bg-emerald-100 text-emerald-700 border-emerald-200'
}

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

function CheckInCard({
  checkInData,
  onSubmit,
}: {
  checkInData: RecoveryCheckInTargetsResponse
  onSubmit: () => void
}) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [otherKey, setOtherKey] = useState('')
  const [showOther, setShowOther] = useState(false)
  const [soreness, setSoreness] = useState<SymptomSeverityLevel>(0)
  const [pain, setPain] = useState<SymptomSeverityLevel>(0)
  const [stiffness, setStiffness] = useState<SymptomSeverityLevel>(0)
  const [saving, setSaving] = useState(false)
  const allTargets = useMemo(() => {
    const byKey = new Map<string, RecoveryCheckInTarget>()
    for (const target of checkInData.targets) byKey.set(target.target_key, target)
    for (const target of checkInData.other_options.tracked_tissues) byKey.set(target.target_key, target)
    for (const target of checkInData.other_options.regions) byKey.set(target.target_key, target)
    return byKey
  }, [checkInData])

  const checkInByTarget = useMemo(
    () => Object.fromEntries(checkInData.today_check_ins.map(checkIn => [checkIn.target_key, checkIn])),
    [checkInData.today_check_ins],
  )

  const selectedTarget = selectedKey ? allTargets.get(selectedKey) ?? null : null
  const selectedCheckIn = selectedTarget ? (checkInByTarget[selectedTarget.target_key] ?? selectedTarget.existing_check_in ?? null) : null
  const savedKeys = useMemo(() => new Set(checkInData.today_check_ins.map(checkIn => checkIn.target_key)), [checkInData.today_check_ins])

  useEffect(() => {
    if (!selectedTarget) {
      setSoreness(0)
      setPain(0)
      setStiffness(0)
      return
    }
    const ci = checkInByTarget[selectedTarget.target_key] ?? selectedTarget.existing_check_in ?? null
    if (ci) {
      setSoreness(symptomDbToSeverity(ci.soreness_0_10))
      setPain(symptomDbToSeverity(ci.pain_0_10))
      setStiffness(symptomDbToSeverity(ci.stiffness_0_10))
      return
    }
    setSoreness(0)
    setPain(0)
    setStiffness(0)
  }, [checkInByTarget, selectedTarget])

  // Auto-calculate readiness: start at 10, subtract for each nonzero category
  const readiness = Math.max(0, 10
    - (soreness > 0 ? soreness + 1 : 0)
    - (pain > 0 ? pain + 1 : 0)
    - (stiffness > 0 ? stiffness : 0)
  )

  const submit = async () => {
    if (!selectedTarget) return
    setSaving(true)
    try {
      await createRecoveryCheckIn({
        date: today(),
        region: selectedTarget.target_kind === 'region' ? selectedTarget.region : undefined,
        tracked_tissue_id: selectedTarget.target_kind === 'tracked_tissue'
          ? selectedTarget.tracked_tissue_id ?? undefined
          : undefined,
        soreness_0_10: symptomSeverityToDb(soreness),
        pain_0_10: symptomSeverityToDb(pain),
        stiffness_0_10: symptomSeverityToDb(stiffness),
        readiness_0_10: readiness,
      })
      setSelectedKey(null)
      setOtherKey('')
      setShowOther(false)
      setSoreness(0)
      setPain(0)
      setStiffness(0)
      onSubmit()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Recovery Check-in</h3>
          <p className="text-xs text-gray-500 mt-0.5">Check in on today&apos;s relevant areas only.</p>
        </div>
        <span className="text-xs font-medium text-gray-500">
          {checkInData.today_check_ins.length}/{checkInData.targets.length} done
        </span>
      </div>

      {checkInData.targets.length > 0 ? (
        <div className="space-y-2 mb-4">
          {checkInData.targets.map(target => {
            const done = savedKeys.has(target.target_key)
            const active = selectedTarget?.target_key === target.target_key
            const currentCheckIn = checkInByTarget[target.target_key] ?? target.existing_check_in ?? null
            return (
              <button
                key={target.target_key}
                onClick={() => {
                  setOtherKey('')
                  setSelectedKey(active ? null : target.target_key)
                }}
                className={`w-full text-left rounded-xl border p-3 transition-all ${
                  active
                    ? 'bg-gray-900 border-gray-900 text-white'
                    : done
                      ? 'bg-emerald-50 border-emerald-200 text-gray-900'
                      : 'bg-white border-gray-200 text-gray-900 hover:border-gray-300'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{target.target_label}</p>
                    <p className={`text-[11px] mt-0.5 ${active ? 'text-gray-300' : 'text-gray-500'}`}>
                      {target.target_kind === 'tracked_tissue'
                        ? `${regionLabel(target.region)} · specific tissue`
                        : `${regionLabel(target.region)} · region`}
                    </p>
                  </div>
                  {done && (
                    <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full font-medium ${
                      active ? 'bg-white/15 text-white' : 'bg-emerald-100 text-emerald-700'
                    }`}>
                      Checked in
                    </span>
                  )}
                </div>
                {target.reasons && target.reasons.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {target.reasons.map(reason => (
                      <span
                        key={reason.code}
                        className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                          active
                            ? 'border-white/20 bg-white/10 text-white'
                            : 'border-gray-200 bg-gray-50 text-gray-600'
                        }`}
                      >
                        {reason.label}
                      </span>
                    ))}
                  </div>
                )}
                {currentCheckIn && (
                  <p className={`text-[11px] mt-2 ${active ? 'text-gray-200' : 'text-gray-500'}`}>
                    Sore {currentCheckIn.soreness_0_10}/10 · Pain {currentCheckIn.pain_0_10}/10 · Stiff {currentCheckIn.stiffness_0_10}/10 · Readiness {currentCheckIn.readiness_0_10}/10
                  </p>
                )}
              </button>
            )
          })}
        </div>
      ) : (
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 px-3 py-2 mb-4 text-xs text-gray-500">
          Nothing specific is queued today. Use <span className="font-medium text-gray-700">Other</span> if something feels sore, stiff, or painful.
        </div>
      )}

      <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-3 mb-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium text-gray-700">Something else bothering you?</p>
            <p className="text-[11px] text-gray-500 mt-0.5">Add another tracked tissue or muscle group without checking in on everything.</p>
          </div>
          <button
            onClick={() => setShowOther(prev => !prev)}
            className={`px-2.5 py-1 text-xs rounded-lg border transition-all ${
              showOther
                ? 'bg-gray-900 text-white border-gray-900'
                : 'bg-white border-gray-200 text-gray-600 hover:border-gray-300'
            }`}
          >
            Other
          </button>
        </div>
        {showOther && (
          <div className="mt-3">
            {checkInData.other_options.tracked_tissues.length > 0 || checkInData.other_options.regions.length > 0 ? (
              <select
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 bg-white"
                value={otherKey}
                onChange={event => {
                  const nextKey = event.target.value
                  setOtherKey(nextKey)
                  setSelectedKey(nextKey || null)
                }}
              >
                <option value="">Select another area...</option>
                {checkInData.other_options.tracked_tissues.length > 0 && (
                  <optgroup label="Tracked tissues">
                    {checkInData.other_options.tracked_tissues.map(target => (
                      <option key={target.target_key} value={target.target_key}>
                        {target.target_label}
                      </option>
                    ))}
                  </optgroup>
                )}
                {checkInData.other_options.regions.length > 0 && (
                  <optgroup label="Regions">
                    {checkInData.other_options.regions.map(target => (
                      <option key={target.target_key} value={target.target_key}>
                        {target.target_label}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
            ) : (
              <p className="text-[11px] text-gray-500">All available tracked tissues and regions are already on today&apos;s list.</p>
            )}
          </div>
        )}
      </div>

      {selectedTarget && (
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-gray-700">{selectedTarget.target_label}</p>
              <p className="text-[11px] text-gray-500 mt-0.5">
                {selectedTarget.target_kind === 'tracked_tissue'
                  ? `${regionLabel(selectedTarget.region)} · specific tissue`
                  : `${regionLabel(selectedTarget.region)} · region`}
              </p>
            </div>
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
              readiness >= 8 ? 'bg-emerald-100 text-emerald-700' :
              readiness >= 5 ? 'bg-yellow-100 text-yellow-700' :
              'bg-red-100 text-red-700'
            }`}>Readiness: {readiness}/10</span>
          </div>
          {selectedTarget.reasons && selectedTarget.reasons.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {selectedTarget.reasons.map(reason => (
                <span key={reason.code} className="text-[10px] px-1.5 py-0.5 rounded-full border border-gray-200 bg-white text-gray-600">
                  {reason.label}
                </span>
              ))}
            </div>
          )}
          {selectedCheckIn && (
            <p className="text-[11px] text-gray-500">
              Already checked in today. Update it if anything changed.
            </p>
          )}
          <SymptomSeverityRow label="Soreness" value={soreness} onChange={setSoreness} showDescription={false} />
          <SymptomSeverityRow label="Pain" value={pain} onChange={setPain} showDescription={false} />
          <SymptomSeverityRow label="Stiffness" value={stiffness} onChange={setStiffness} showDescription={false} />
          <div className="flex gap-2">
            <button
              onClick={() => {
                setSelectedKey(null)
                setOtherKey('')
              }}
              className="flex-1 py-2 bg-white border border-gray-200 text-gray-700 text-sm font-medium rounded-lg hover:border-gray-300 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={submit}
              disabled={saving}
              className="flex-1 py-2 bg-gray-900 text-white text-sm font-medium rounded-lg hover:bg-gray-800 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Saving...' : selectedCheckIn ? 'Update' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Tissue & Exercise merged card ──

function TissueAndExerciseCard({ tissues, exercises }: { tissues: TrainingModelTissueSummary[], exercises: TrainingModelExerciseInsight[] }) {
  const [tab, setTab] = useState<'tissues' | 'exercises'>('tissues')
  const [tissueFilter, setTissueFilter] = useState<'all' | 'elevated' | 'monitor' | 'recovered'>('all')
  const [exFilter, setExFilter] = useState<'all' | 'avoid' | 'caution' | 'good'>('all')

  const atRisk = tissues.filter(t => t.risk_7d >= 55)
  const mid = tissues.filter(t => t.risk_7d >= 30 && t.risk_7d < 55)
  const recovering = tissues.filter(t => t.risk_7d < 30 && t.recovery_estimate >= 0.75)

  const tissueCounts = { elevated: atRisk.length, monitor: mid.length, recovered: recovering.length }
  const filteredTissues = tissueFilter === 'elevated' ? atRisk
    : tissueFilter === 'monitor' ? mid
    : tissueFilter === 'recovered' ? recovering
    : tissues

  const exCounts = {
    avoid: exercises.filter(e => e.recommendation === 'avoid').length,
    caution: exercises.filter(e => e.recommendation === 'caution').length,
    good: exercises.filter(e => e.recommendation === 'good').length,
  }
  const filteredEx = exFilter === 'all' ? exercises : exercises.filter(e => e.recommendation === exFilter)

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
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-900">Tissue & Exercise Status</h3>
        <div className="flex gap-1">
          <button onClick={() => setTab('tissues')} className={`px-3 py-1 text-xs rounded-lg border transition-all ${tab === 'tissues' ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-500 border-gray-200'}`}>Tissues</button>
          <button onClick={() => setTab('exercises')} className={`px-3 py-1 text-xs rounded-lg border transition-all ${tab === 'exercises' ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-500 border-gray-200'}`}>Exercises</button>
        </div>
      </div>

      {tab === 'tissues' && (
        <div>
          <div className="flex gap-1.5 mb-3 flex-wrap">
            {([
              ['all', 'All'],
              ['elevated', `Elevated (${tissueCounts.elevated})`],
              ['monitor', `Monitor (${tissueCounts.monitor})`],
              ['recovered', `Recovered (${tissueCounts.recovered})`],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setTissueFilter(key)}
                className={`px-2.5 py-1 text-xs rounded-lg border transition-all ${
                  tissueFilter === key
                    ? key === 'elevated' ? 'bg-red-100 text-red-700 border-red-300 font-semibold'
                      : key === 'monitor' ? 'bg-amber-100 text-amber-700 border-amber-300 font-semibold'
                      : key === 'recovered' ? 'bg-emerald-100 text-emerald-700 border-emerald-300 font-semibold'
                      : 'bg-gray-900 text-white border-gray-900 font-semibold'
                    : 'bg-white border-gray-200 text-gray-500 hover:border-gray-300'
                }`}
              >{label}</button>
            ))}
          </div>
          <div className="space-y-1.5 max-h-72 overflow-y-auto">
            {filteredTissues.map(t => <TissueRow key={t.tissue.id} t={t} />)}
            {filteredTissues.length === 0 && <p className="text-sm text-gray-400 py-2">No tissues in this category</p>}
          </div>
        </div>
      )}

      {tab === 'exercises' && (
        <div>
          <div className="flex gap-1.5 mb-3 flex-wrap">
            {([
              ['all', 'All'],
              ['good', `Good (${exCounts.good})`],
              ['caution', `Caution (${exCounts.caution})`],
              ['avoid', `Avoid (${exCounts.avoid})`],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setExFilter(key)}
                className={`px-2.5 py-1 text-xs rounded-lg border transition-all ${
                  exFilter === key
                    ? key === 'good' ? 'bg-emerald-100 text-emerald-700 border-emerald-300 font-semibold'
                      : key === 'caution' ? 'bg-amber-100 text-amber-700 border-amber-300 font-semibold'
                      : key === 'avoid' ? 'bg-red-100 text-red-700 border-red-300 font-semibold'
                      : 'bg-gray-900 text-white border-gray-900 font-semibold'
                    : 'bg-white border-gray-200 text-gray-500 hover:border-gray-300'
                }`}
              >{label}</button>
            ))}
          </div>
          <div className="space-y-1.5 max-h-72 overflow-y-auto">
            {filteredEx.map(ex => (
              <div key={ex.id} className="flex items-center gap-3 p-2.5 rounded-lg border border-gray-100 bg-gray-50/50">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 truncate">{ex.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${recBadge(ex.recommendation)}`}>{ex.recommendation}</span>
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
                  <span className={`text-base font-bold tabular-nums ${riskColor(ex.weighted_risk_7d)}`}>{Math.round(ex.weighted_risk_7d)}</span>
                  <span className="text-[10px] text-gray-400 ml-0.5">%</span>
                </div>
              </div>
            ))}
            {filteredEx.length === 0 && <p className="text-sm text-gray-400 py-2">No exercises in this category</p>}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Exercise Progress ──

const fmtVol = (v: number) =>
  v >= 10000 ? `${(v / 1000).toFixed(0)}k` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v).toString()

function ExerciseProgressCard({ exercises }: { exercises: WkExercise[] }) {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [history, setHistory] = useState<WkExerciseHistory | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!selectedId) { setHistory(null); return }
    setLoading(true)
    getExerciseHistory(selectedId, 200).then(setHistory).catch(() => setHistory(null)).finally(() => setLoading(false))
  }, [selectedId])

  // Aggregate history into months
  const monthlyData = useMemo(() => {
    if (!history) return []
    const byMonth: Record<string, { pr: number; volume: number; e1rm: number; sessions: number }> = {}
    for (const s of history.sessions) {
      const month = s.date.slice(0, 7)
      if (!byMonth[month]) byMonth[month] = { pr: 0, volume: 0, e1rm: 0, sessions: 0 }
      byMonth[month].pr = Math.max(byMonth[month].pr, s.max_weight)
      byMonth[month].volume += s.total_volume
      byMonth[month].sessions += 1
      for (const set of s.sets) {
        if (set.weight && set.reps && set.reps > 0) {
          const reps = Math.min(set.reps, 12)
          const epley = set.weight * (1 + 0.0333 * reps)
          byMonth[month].e1rm = Math.max(byMonth[month].e1rm, epley)
        }
      }
    }
    return Object.entries(byMonth)
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([month, d]) => ({
        month,
        label: new Date(month + '-15').toLocaleDateString(undefined, { month: 'short', year: '2-digit' }),
        ...d,
      }))
  }, [history])

  const allTimePR = monthlyData.length > 0 ? Math.max(...monthlyData.map(m => m.pr)) : 0
  const currentE1rm = monthlyData.length > 0 ? Math.round(monthlyData[monthlyData.length - 1].e1rm) : 0

  // SVG dual chart
  const svgW = 340, svgE1rmH = 140, svgVolH = 100
  const ml = 40, mr = 12, mt = 10, mb = 28
  const plotW = svgW - ml - mr

  const maxE1rm = Math.max(...monthlyData.map(m => m.e1rm), 1) * 1.1
  const maxVol = Math.max(...monthlyData.map(m => m.volume), 1)
  const n = monthlyData.length
  const step = n > 1 ? plotW / (n - 1) : plotW
  const barStep = plotW / Math.max(n, 1)
  const barW = Math.max(3, barStep * 0.6)

  const toX = (i: number) => ml + (n > 1 ? i * step : plotW / 2)
  const toY_e1rm = (v: number) => mt + ((maxE1rm - v) / maxE1rm) * (svgE1rmH - mt - mb)

  const showLabels = n <= 18

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <h3 className="text-sm font-semibold text-gray-900 mb-2">Exercise Progress</h3>
      <select
        className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 bg-white mb-3"
        value={selectedId ?? ''}
        onChange={e => setSelectedId(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">Select an exercise...</option>
        {exercises.map(ex => <option key={ex.id} value={ex.id}>{ex.name}</option>)}
      </select>

      {loading && <p className="text-sm text-gray-400">Loading...</p>}

      {!loading && selectedId && monthlyData.length === 0 && (
        <p className="text-sm text-gray-400">No history for this exercise.</p>
      )}

      {monthlyData.length > 0 && (
        <>
          {/* Stats row */}
          <div className="grid grid-cols-3 gap-2 mb-3">
            <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400">e1RM (cur)</p>
              <p className="text-base font-bold text-gray-900 mt-0.5">{currentE1rm} <span className="text-xs font-normal text-gray-400">lb</span></p>
            </div>
            <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400">PR</p>
              <p className="text-base font-bold text-gray-900 mt-0.5">{allTimePR} <span className="text-xs font-normal text-gray-400">lb</span></p>
            </div>
            <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400">Sessions</p>
              <p className="text-base font-bold text-gray-900 mt-0.5">{history?.sessions.length ?? 0}</p>
            </div>
          </div>

          {/* e1RM line chart */}
          <p className="text-[10px] uppercase tracking-[0.14em] text-gray-400 mb-1">Estimated 1RM (monthly)</p>
          <svg viewBox={`0 0 ${svgW} ${svgE1rmH}`} className="w-full" style={{ height: `${svgE1rmH}px` }}>
            {[0, 0.5, 1].map((f, i) => {
              const val = f * maxE1rm
              const y = toY_e1rm(val)
              return (
                <g key={i}>
                  <line x1={ml} x2={svgW - mr} y1={y} y2={y} stroke="#e5e7eb" strokeDasharray="3 3" />
                  <text x={ml - 3} y={y + 3} textAnchor="end" fontSize="9" fill="#9ca3af">{Math.round(val)}</text>
                </g>
              )
            })}
            <polyline
              points={monthlyData.map((m, i) => `${toX(i)},${toY_e1rm(m.e1rm)}`).join(' ')}
              fill="none" stroke="#f97316" strokeWidth={2} strokeLinejoin="round"
            />
            {monthlyData.map((m, i) => (
              <g key={m.month}>
                <circle cx={toX(i)} cy={toY_e1rm(m.e1rm)} r={3} fill="#f97316" />
                {showLabels && (
                  <text x={toX(i)} y={svgE1rmH - 6} textAnchor="middle" fontSize="8" fill="#6b7280">{m.label}</text>
                )}
              </g>
            ))}
          </svg>

          {/* Volume bar chart */}
          <p className="text-[10px] uppercase tracking-[0.14em] text-gray-400 mb-1 mt-3">Volume (monthly, lbs)</p>
          <svg viewBox={`0 0 ${svgW} ${svgVolH}`} className="w-full" style={{ height: `${svgVolH}px` }}>
            <line x1={ml} x2={svgW - mr} y1={svgVolH - mb} y2={svgVolH - mb} stroke="#e5e7eb" />
            <text x={ml - 3} y={mt + 4} textAnchor="end" fontSize="9" fill="#9ca3af">{fmtVol(maxVol)}</text>
            {monthlyData.map((m, i) => {
              const bH = Math.max(1, (m.volume / maxVol) * (svgVolH - mt - mb))
              const x = ml + i * barStep + (barStep - barW) / 2
              return (
                <g key={m.month}>
                  <rect x={x} y={svgVolH - mb - bH} width={barW} height={bH} rx={2} fill="#3b82f6" opacity={0.75} />
                  {showLabels && (
                    <text x={x + barW / 2} y={svgVolH - 6} textAnchor="middle" fontSize="8" fill="#6b7280">{m.label}</text>
                  )}
                </g>
              )
            })}
          </svg>

          {!showLabels && (
            <p className="text-[10px] text-gray-400 mt-1">{monthlyData[0]?.label} — {monthlyData[monthlyData.length - 1]?.label} ({n} months)</p>
          )}
        </>
      )}
    </div>
  )
}

// ── Planner Card ──

const schemeColor = (scheme: string) =>
  scheme === 'heavy' ? 'bg-red-100 text-red-700' :
  scheme === 'volume' ? 'bg-blue-100 text-blue-700' :
  'bg-green-100 text-green-700'

function ActivePlanCard({
  plan,
  onRefresh,
  onCancel,
  onComplete,
}: {
  plan: SavedPlan
  onRefresh: () => void
  onCancel: () => void
  onComplete: () => void
}) {
  const [starting, setStarting] = useState(false)
  const [completing, setCompleting] = useState(false)
  const [cancelling, setCancelling] = useState(false)

  const handleStart = async () => {
    setStarting(true)
    try {
      await startPlan(today())
      onRefresh()
    } finally {
      setStarting(false)
    }
  }

  const handleComplete = async () => {
    setCompleting(true)
    try {
      await completePlan(today())
      onComplete()
    } finally {
      setCompleting(false)
    }
  }

  const handleCancel = async () => {
    setCancelling(true)
    try {
      await deletePlan(today())
      onCancel()
    } finally {
      setCancelling(false)
    }
  }

  const isStarted = plan.status !== 'planned'
  const isCompleted = plan.status === 'completed'

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-gray-900">Today's Workout</h3>
          <span className="text-xs px-2 py-0.5 rounded-lg bg-gray-900 text-white font-medium">
            {plan.day_label}
          </span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
            isCompleted ? 'bg-emerald-50 text-emerald-700' :
            isStarted ? 'bg-blue-50 text-blue-700' :
            'bg-gray-100 text-gray-600'
          }`}>
            {plan.status}
          </span>
        </div>
        {!isCompleted && (
          <button
            onClick={handleCancel}
            disabled={cancelling}
            className="text-[10px] text-red-400 hover:text-red-600 disabled:opacity-50"
          >
            {cancelling ? 'cancelling…' : 'cancel workout'}
          </button>
        )}
      </div>

      {plan.target_regions.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3">
          {plan.target_regions.map(r => (
            <span
              key={r}
              className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600 font-medium"
            >
              {r}
            </span>
          ))}
        </div>
      )}

      {/* Phase 1: Plan mode (not started) */}
      {!isStarted && (
        <>
          <WorkoutSetEditor
            mode="plan"
            planExercises={plan.exercises}
            onPlanChanged={onRefresh}
            asOf={today()}
          />
          <button
            onClick={handleStart}
            disabled={starting || plan.exercises.length === 0}
            className="w-full mt-3 py-2 text-xs font-medium rounded-xl bg-gray-900
              hover:bg-gray-800 text-white transition-colors disabled:opacity-40"
          >
            {starting ? 'Starting…' : 'Start Workout'}
          </button>
        </>
      )}

      {/* Phase 2: Log mode (started, not completed) */}
      {isStarted && !isCompleted && plan.workout_session_id && (
        <>
          <WorkoutSetEditor
            mode="log"
            sessionId={plan.workout_session_id}
            onSessionChanged={onRefresh}
          />
          <button
            onClick={handleComplete}
            disabled={completing}
            className="w-full mt-3 py-2 text-xs font-medium rounded-xl bg-emerald-600
              hover:bg-emerald-700 text-white transition-colors disabled:opacity-40"
          >
            {completing ? 'Completing…' : 'Complete Workout'}
          </button>
        </>
      )}

      {/* Phase 3: Completed */}
      {isCompleted && plan.workout_session_id && (
        <WorkoutSetEditor
          mode="log"
          sessionId={plan.workout_session_id}
          onSessionChanged={onRefresh}
          compact
        />
      )}
    </div>
  )
}

function PlannerCard({ planner, onRefresh, onSave }: {
  planner: PlannerTodayResponse;
  onRefresh?: () => void;
  onSave?: (dayLabel: string, regions: string[], exercises: PlannerExercisePrescription[]) => void;
}) {
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set())
  const [initialized, setInitialized] = useState(false)

  // Initialize checked state from backend `selected` flags
  useEffect(() => {
    if (planner.suggestion?.exercises) {
      setCheckedIds(new Set(
        planner.suggestion.exercises
          .filter(e => e.selected !== false)
          .map(e => e.exercise_id)
      ))
      setInitialized(true)
    }
  }, [planner])

  const toggleExercise = (id: number) => {
    setCheckedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
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
  const selectedCount = exercises.filter(e => checkedIds.has(e.exercise_id)).length

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-gray-900">Today's Plan</h3>
          <span className="text-xs px-2 py-0.5 rounded-lg bg-gray-900 text-white font-medium">{s.day_label}</span>
        </div>
        <div className="flex items-center gap-2">
          {onRefresh && (
            <button onClick={onRefresh} className="text-[10px] text-gray-400 hover:text-gray-600">refresh</button>
          )}
          <span className="text-xs text-gray-500">{Math.round(s.readiness_score * 100)}% ready</span>
        </div>
      </div>

      {s.target_regions.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3 mt-2">
          {s.target_regions.map(r => (
            <span key={r} className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600 font-medium">
              {regionLabel(r)}
            </span>
          ))}
        </div>
      )}

      {initialized && exercises.length > 0 && (
        <div className="space-y-1.5 mb-3 max-h-[28rem] overflow-y-auto">
          {exercises.map((ex) => {
            const checked = checkedIds.has(ex.exercise_id)
            return (
              <button
                key={ex.exercise_id}
                onClick={() => toggleExercise(ex.exercise_id)}
                className={`w-full flex items-start gap-2.5 p-2.5 rounded-lg border text-left transition-all ${
                  checked
                    ? 'border-gray-300 bg-white'
                    : 'border-gray-100 bg-gray-50/50 opacity-60'
                }`}
              >
                <div className={`mt-0.5 w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors ${
                  checked ? 'bg-gray-900 border-gray-900' : 'bg-white border-gray-300'
                }`}>
                  {checked && (
                    <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-sm font-medium text-gray-900">{ex.exercise_name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${schemeColor(ex.rep_scheme)}`}>
                      {ex.rep_scheme}
                    </span>
                    {ex.performed_side && ex.performed_side !== 'bilateral' && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-purple-100 text-purple-700">
                        {ex.performed_side}
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-gray-600 mt-0.5 font-medium">
                    {ex.target_sets} x {ex.target_reps}
                    {ex.target_weight != null && <> @ <span className="text-gray-900">{ex.target_weight} lb</span></>}
                  </div>
                  {ex.side_explanation && (
                    <div className="text-[10px] text-purple-600 mt-0.5">{ex.side_explanation}</div>
                  )}
                  {ex.selection_note && (
                    <div className="text-[10px] text-blue-600 mt-0.5">{ex.selection_note}</div>
                  )}
                  {ex.weight_adjustment_note && (
                    <div className="text-[10px] text-orange-600 mt-0.5">{ex.weight_adjustment_note}</div>
                  )}
                  {ex.overload_note && (
                    <div className="text-[10px] text-amber-600 mt-0.5">{ex.overload_note}</div>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      )}

      {exercises.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 p-4 text-center mb-3">
          <p className="text-xs text-gray-500">No matching exercises found for these regions.</p>
        </div>
      )}

      {exercises.length > 0 && onSave && (
        <button
          onClick={() => onSave(
            s.day_label,
            s.target_regions,
            exercises.filter(e => checkedIds.has(e.exercise_id)),
          )}
          disabled={selectedCount === 0}
          className="w-full py-2 text-xs font-medium rounded-xl bg-gray-900 hover:bg-gray-800 text-white transition-colors mb-3 disabled:opacity-40"
        >
          Save Plan ({selectedCount} exercises) & Start in Chat
        </button>
      )}

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
      <div className="space-y-3">
        {growing.length > 0 && (
          <div>
            <p className="text-[10px] uppercase tracking-wider text-emerald-600 font-semibold mb-1.5">Growing</p>
            <div className="space-y-1.5">
              {growing.map(t => (
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
              {declining.map(t => (
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
  const [checkInData, setCheckInData] = useState<RecoveryCheckInTargetsResponse | null>(null)
  const [quickLoaded, setQuickLoaded] = useState(false)

  // Model-dependent state (slower to load)
  const [modelSummary, setModelSummary] = useState<TrainingModelSummary | null>(null)
  const [modelLoading, setModelLoading] = useState(true)

  // Planner state
  const [planner, setPlanner] = useState<PlannerTodayResponse | null>(null)
  const [plannerLoading, setPlannerLoading] = useState(true)

  // Active (saved) plan state
  const [activePlan, setActivePlan] = useState<SavedPlan | null>(null)

  // Exercise progress
  const [allExercises, setAllExercises] = useState<WkExercise[]>([])

  // Quick data load (targeted check-ins)
  useEffect(() => {
    let cancelled = false
    getRecoveryCheckInTargets(today()).then(data => {
      if (cancelled) return
      setCheckInData(data)
      setQuickLoaded(true)
    }).catch(() => {
      if (cancelled) return
      setCheckInData(null)
      setQuickLoaded(true)
    })
    return () => { cancelled = true }
  }, [])

  // Model data load (starts immediately but takes longer)
  useEffect(() => {
    let cancelled = false
    setModelLoading(true)
    getTrainingModelSummary(today(), true).then(data => {
      if (cancelled) return
      setModelSummary(data)
      setModelLoading(false)
    }).catch(() => { if (!cancelled) setModelLoading(false) })
    getExercises().then(setAllExercises).catch(() => {})
    return () => { cancelled = true }
  }, [])

  // Planner load
  useEffect(() => {
    let cancelled = false
    setPlannerLoading(true)
    getPlannerToday(today()).then(data => {
      if (cancelled) return
      setPlanner(data)
      setPlannerLoading(false)
    }).catch(() => { if (!cancelled) { setPlanner(null); setPlannerLoading(false) } })
    return () => { cancelled = true }
  }, [])

  const refreshPlanner = useCallback(() => {
    setPlannerLoading(true)
    getPlannerToday(today()).then(data => {
      setPlanner(data)
      setPlannerLoading(false)
    }).catch(() => { setPlanner(null); setPlannerLoading(false) })
  }, [])

  const refreshActivePlan = useCallback(() => {
    getActivePlan(today()).then(plan => {
      // Don't show completed plans — revert to planner for next session
      setActivePlan(plan?.status === 'completed' ? null : plan)
    })
  }, [])

  useEffect(() => {
    refreshActivePlan()
  }, [refreshActivePlan])

  const refreshCheckIns = useCallback(() => {
    getRecoveryCheckInTargets(today()).then(setCheckInData).catch(() => {})
    // Re-plan after check-in since readiness may have changed
    refreshPlanner()
  }, [refreshPlanner])

  const handleSavePlan = useCallback((dayLabel: string, regions: string[], exercises: PlannerExercisePrescription[]) => {
    savePlan(dayLabel, regions, exercises, today()).then(() => {
      refreshPlanner()
      refreshActivePlan()
    }).catch(() => {})
  }, [refreshPlanner, refreshActivePlan])

  return (
    <ScrollablePage>
      <div className="space-y-4 pb-4">
        {/* Header */}
        <div className="flex items-baseline justify-between">
          <h1 className="text-lg font-bold text-gray-900">Training</h1>
          <span className="text-xs text-gray-400 tabular-nums">{today()}</span>
        </div>

        {/* Row 1: Check-in + Exercise Progress (left) | Today's Plan (right) */}
        {!quickLoaded ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-4"><CardSkeleton lines={4} /><CardSkeleton lines={5} /></div>
            <CardSkeleton lines={8} />
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-start">
            {/* Left: Check-in stacked above Exercise Progress */}
            <div className="space-y-4">
              {checkInData && (
                <CheckInCard
                  checkInData={checkInData}
                  onSubmit={refreshCheckIns}
                />
              )}
              <ExerciseProgressCard exercises={allExercises} />
            </div>
            {/* Right: Today's Plan / Active Workout */}
            {activePlan
              ? <ActivePlanCard
                  plan={activePlan}
                  onRefresh={refreshActivePlan}
                  onCancel={() => {
                    setActivePlan(null)
                    refreshPlanner()
                  }}
                  onComplete={() => {
                    setActivePlan(null)
                    refreshPlanner()
                  }}
                />
              : plannerLoading
                ? <CardSkeleton lines={8} />
                : planner && (
                  <PlannerCard
                    planner={planner}
                    onRefresh={refreshPlanner}
                    onSave={handleSavePlan}
                  />
                )
            }
          </div>
        )}

        {/* Row 2: Tissue & Exercise (left) | Capacity Trends (right) */}
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

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-start">
              <TissueAndExerciseCard tissues={modelSummary.tissues} exercises={modelSummary.exercises} />
              <CapacityCard tissues={modelSummary.tissues} />
            </div>
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
