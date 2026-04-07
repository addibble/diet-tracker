import { useEffect, useMemo, useState } from 'react'
import SymptomSeverityRow from '../components/SymptomSeverityRow'
import {
  symptomDbToSeverity,
  symptomSeverityToDb,
  type SymptomSeverityLevel,
} from '../components/symptomSeverity'
import {
  applyExerciseMappingWarning,
  createRehabPlan,
  createTissueCondition,
  getExerciseHistory,
  getTrainingModelSummary,
  getRegions,
  getTissues,
  getExercises,
  getRehabProtocols,
  getTissueReadiness,
  getTrackedTissueReadiness,
  updateRehabPlan,
  updateExercise,
  type RehabProtocol,
  type RegionInfo,
  type RepScheme,
  type TrainingModelExerciseInsight,
  type TrainingModelSummary,
  type TrackedTissueReadiness,
  type WkExerciseHistory,
  type WkTissue,
  type WkExercise,
  type WkTissueReadiness,
  type TrainingModelTissueSummary,
} from '../api'

// ── Types ──

type View = 'tissues' | 'exercises'

interface TissueWithExercises extends WkTissue {
  exercises: {
    exercise_id: number;
    exercise_name: string;
    role: string;
    loading_factor: number;
    routing_factor: number;
  }[]
}

// ── Style Constants ──

const TYPE_BADGE: Record<string, string> = {
  muscle: 'bg-green-100 text-green-700',
  tendon: 'bg-orange-100 text-orange-700',
  joint: 'bg-red-100 text-red-700',
}

const ROLE_COLORS: Record<string, string> = {
  primary: 'bg-emerald-100 text-emerald-800',
  secondary: 'bg-sky-100 text-sky-800',
  stabilizer: 'bg-gray-100 text-gray-600',
}

const CONDITION_COLORS: Record<string, string> = {
  healthy: 'bg-green-100 text-green-700',
  tender: 'bg-yellow-100 text-yellow-700',
  injured: 'bg-red-100 text-red-700',
  rehabbing: 'bg-purple-100 text-purple-700',
}

const CONDITION_OPTIONS = [
  { value: 'healthy', label: 'Healthy' },
  { value: 'tender', label: 'Tender' },
  { value: 'injured', label: 'Injured' },
  { value: 'rehabbing', label: 'Rehabbing' },
] as const

type TissueTypeFilter = keyof typeof TYPE_BADGE
type ConditionFilter = keyof typeof CONDITION_COLORS
type ExerciseRoleFilter = keyof typeof ROLE_COLORS

const TISSUE_FILTER_OPTIONS: { value: TissueTypeFilter; label: string }[] = [
  { value: 'muscle', label: 'Muscle' },
  { value: 'tendon', label: 'Tendon' },
  { value: 'joint', label: 'Joint' },
]

const CONDITION_FILTER_OPTIONS: { value: ConditionFilter; label: string }[] = [
  { value: 'healthy', label: 'Healthy' },
  { value: 'tender', label: 'Tender' },
  { value: 'injured', label: 'Injured' },
  { value: 'rehabbing', label: 'Rehabbing' },
]

const ROLE_FILTER_OPTIONS: { value: ExerciseRoleFilter; label: string }[] = [
  { value: 'primary', label: 'Primary' },
  { value: 'secondary', label: 'Secondary' },
  { value: 'stabilizer', label: 'Stabilizer' },
]

type TissueStatusFilter = 'recovered' | 'monitor' | 'elevated' | 'overworked'

const STATUS_COLORS: Record<TissueStatusFilter, string> = {
  recovered: 'bg-green-100 text-green-700',
  monitor: 'bg-yellow-100 text-yellow-700',
  elevated: 'bg-orange-100 text-orange-700',
  overworked: 'bg-red-100 text-red-700',
}

const STATUS_FILTER_OPTIONS: { value: TissueStatusFilter; label: string }[] = [
  { value: 'recovered', label: 'Recovered' },
  { value: 'monitor', label: 'Monitor' },
  { value: 'elevated', label: 'Elevated' },
  { value: 'overworked', label: 'Overworked' },
]

function getTissueStatus(summary: TrainingModelTissueSummary): TissueStatusFilter {
  if (summary.risk_7d >= 75) return 'overworked'
  if (summary.risk_7d >= 55) return 'elevated'
  if (summary.risk_7d >= 30) return 'monitor'
  return 'recovered'
}

type SortField = 'name' | 'capacity_trend' | 'risk' | 'recovery'
type SortDir = 'asc' | 'desc'

const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: 'name', label: 'Name' },
  { value: 'capacity_trend', label: 'Capacity Trend' },
  { value: 'risk', label: 'Risk (Status)' },
  { value: 'recovery', label: 'Recovery' },
]

const LATERALITY_OPTIONS = [
  { value: 'bilateral', label: 'Bilateral' },
  { value: 'unilateral', label: 'Unilateral' },
  { value: 'either', label: 'Either side' },
] as const

const LOAD_INPUT_MODE_OPTIONS = [
  { value: 'external_weight', label: 'External weight' },
  { value: 'bodyweight', label: 'Bodyweight' },
  { value: 'mixed', label: 'Mixed (bodyweight + external)' },
  { value: 'assisted_bodyweight', label: 'Assisted bodyweight' },
  { value: 'carry', label: 'Carry load' },
] as const

const SET_METRIC_MODE_OPTIONS = [
  { value: 'reps', label: 'Reps' },
  { value: 'duration', label: 'Duration' },
  { value: 'distance', label: 'Distance' },
  { value: 'hybrid', label: 'Hybrid' },
] as const

const GRIP_STYLE_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'pronated', label: 'Pronated' },
  { value: 'supinated', label: 'Supinated' },
  { value: 'mixed', label: 'Mixed' },
] as const

const GRIP_WIDTH_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'narrow', label: 'Narrow' },
  { value: 'shoulder_width', label: 'Shoulder width' },
  { value: 'wide', label: 'Wide' },
  { value: 'variable', label: 'Variable' },
] as const

const SUPPORT_STYLE_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'unsupported', label: 'Unsupported' },
  { value: 'chest_supported', label: 'Chest supported' },
  { value: 'bench_supported', label: 'Bench supported' },
  { value: 'cable_stabilized', label: 'Cable stabilized' },
  { value: 'machine', label: 'Machine' },
] as const

function formatLastWorked(isoDate: string | null, hoursSince: number | null): string {
  if (!isoDate || hoursSince == null) return 'never'
  if (hoursSince < 1) return '<1h ago'
  if (hoursSince < 24) return `${Math.round(hoursSince)}h ago`
  const days = hoursSince / 24
  if (days < 1.5) return '1d ago'
  return `${Math.round(days)}d ago`
}

function daysSinceDate(isoDate: string | null): number | null {
  if (!isoDate) return null
  const msPerDay = 86400000
  return Math.round((new Date().getTime() - new Date(isoDate).getTime()) / msPerDay)
}

function formatSide(side: string) {
  if (side === 'left') return 'L'
  if (side === 'right') return 'R'
  if (side === 'center') return 'C'
  return side
}

function parseNullableNumber(value: string): number | null {
  if (!value.trim()) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function titleCase(value: string | null | undefined): string {
  if (!value) return '—'
  return value
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase())
}

function weightFieldLabel(exercise: WkExercise): string {
  if (exercise.load_input_mode === 'assisted_bodyweight') return 'Assist'
  if (exercise.load_input_mode === 'mixed') return 'External load'
  if (exercise.external_load_multiplier > 1 && exercise.laterality === 'bilateral') {
    return 'Weight / side'
  }
  return 'Weight'
}

function loadPreviewLines(exercise: WkExercise): string[] {
  const preview = exercise.load_preview
  const lines: string[] = []
  const sampleInput =
    preview.sample_input_weight != null ? `${preview.sample_input_weight} lb entered` : 'No entered load'
  const bodyweightLine =
    preview.bodyweight_component > 0
      ? `${preview.sample_bodyweight} lb bodyweight sample -> ${preview.bodyweight_component.toFixed(1)} lb effective bodyweight`
      : `${preview.sample_bodyweight} lb bodyweight sample`
  lines.push(sampleInput)
  lines.push(bodyweightLine)
  lines.push(`Effective load: ${preview.effective_weight.toFixed(1)} lb`)
  if (preview.external_load_multiplier !== 1) {
    lines.push(`External multiplier: x${preview.external_load_multiplier.toFixed(2)}`)
  }
  return lines
}

function collectTissueConditionFilters(
  tissueId: number,
  readinessMap: Map<number, WkTissueReadiness>,
  trackedReadinessByTissue: Map<number, TrackedTissueReadiness[]>,
): Set<ConditionFilter> {
  const filters = new Set<ConditionFilter>()
  const tissueCondition = readinessMap.get(tissueId)?.condition?.status

  if (tissueCondition && tissueCondition in CONDITION_COLORS) {
    filters.add(tissueCondition as ConditionFilter)
  }

  for (const tracked of trackedReadinessByTissue.get(tissueId) ?? []) {
    const trackedCondition = tracked.condition?.status
    if (trackedCondition && trackedCondition in CONDITION_COLORS) {
      filters.add(trackedCondition as ConditionFilter)
    }
  }

  if (filters.size === 0) {
    filters.add('healthy')
  }

  return filters
}

function FilterButton({
  label,
  toneClassName,
  selected,
  onClick,
}: {
  label: string
  toneClassName: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
        selected
          ? `${toneClassName} border-transparent shadow-sm`
          : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-700'
      }`}
    >
      {label}
    </button>
  )
}

// ── Components ──

function LoadingEditor({
  value,
  role,
  exerciseId,
  tissueId,
  exercise,
  onSave,
}: {
  value: number
  role: string
  exerciseId: number
  tissueId: number
  exercise: WkExercise
  onSave: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(value)
  const [editRole, setEditRole] = useState(role)
  const [saving, setSaving] = useState(false)

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        className="text-xs font-mono hover:bg-gray-100 px-1.5 py-0.5 rounded transition-colors"
        title="Click to edit"
      >
        {value.toFixed(2)}
      </button>
    )
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const updatedTissues = exercise.tissues.map((t) =>
        t.tissue_id === tissueId
          ? {
              tissue_id: t.tissue_id,
              role: editRole,
              loading_factor: loading,
              routing_factor: t.routing_factor,
              fatigue_factor: t.fatigue_factor,
              joint_strain_factor: t.joint_strain_factor,
              tendon_strain_factor: t.tendon_strain_factor,
              laterality_mode: t.laterality_mode,
            }
          : {
              tissue_id: t.tissue_id,
              role: t.role,
              loading_factor: t.loading_factor,
              routing_factor: t.routing_factor,
              fatigue_factor: t.fatigue_factor,
              joint_strain_factor: t.joint_strain_factor,
              tendon_strain_factor: t.tendon_strain_factor,
              laterality_mode: t.laterality_mode,
            }
      )
      await updateExercise(exerciseId, { tissues: updatedTissues })
      onSave()
      setEditing(false)
    } catch (e) {
      console.error('Failed to save', e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <span className="inline-flex items-center gap-1">
      <input
        type="number"
        min={0}
        max={1}
        step={0.05}
        value={loading}
        onChange={(e) => setLoading(parseFloat(e.target.value) || 0)}
        className="w-16 text-xs font-mono border border-gray-300 rounded px-1 py-0.5"
        autoFocus
      />
      <select
        value={editRole}
        onChange={(e) => setEditRole(e.target.value)}
        className="text-[10px] border border-gray-300 rounded px-1 py-0.5"
      >
        <option value="primary">primary</option>
        <option value="secondary">secondary</option>
        <option value="stabilizer">stabilizer</option>
      </select>
      <button
        onClick={handleSave}
        disabled={saving}
        className="text-[10px] bg-blue-500 text-white px-1.5 py-0.5 rounded hover:bg-blue-600 disabled:opacity-50"
      >
        {saving ? '...' : 'OK'}
      </button>
      <button
        onClick={() => { setEditing(false); setLoading(value); setEditRole(role) }}
        className="text-[10px] text-gray-500 hover:text-gray-700 px-1"
      >
        X
      </button>
    </span>
  )
}

function ExerciseMetadataEditor({
  exercise,
  onSave,
}: {
  exercise: WkExercise
  onSave: () => void
}) {
  const [laterality, setLaterality] = useState(exercise.laterality)
  const [loadInputMode, setLoadInputMode] = useState(exercise.load_input_mode)
  const [allowHeavyLoading, setAllowHeavyLoading] = useState(exercise.allow_heavy_loading)
  const [bodyweightFraction, setBodyweightFraction] = useState(String(exercise.bodyweight_fraction))
  const [externalLoadMultiplier, setExternalLoadMultiplier] = useState(
    String(exercise.external_load_multiplier),
  )
  const [setMetricMode, setSetMetricMode] = useState(exercise.set_metric_mode)
  const [estimatedMinutesPerSet, setEstimatedMinutesPerSet] = useState(
    String(exercise.estimated_minutes_per_set),
  )
  const [variantGroup, setVariantGroup] = useState(exercise.variant_group ?? '')
  const [gripStyle, setGripStyle] = useState(exercise.grip_style)
  const [gripWidth, setGripWidth] = useState(exercise.grip_width)
  const [supportStyle, setSupportStyle] = useState(exercise.support_style)
  const [notes, setNotes] = useState(exercise.notes ?? '')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLaterality(exercise.laterality)
    setLoadInputMode(exercise.load_input_mode)
    setAllowHeavyLoading(exercise.allow_heavy_loading)
    setBodyweightFraction(String(exercise.bodyweight_fraction))
    setExternalLoadMultiplier(String(exercise.external_load_multiplier))
    setSetMetricMode(exercise.set_metric_mode)
    setEstimatedMinutesPerSet(String(exercise.estimated_minutes_per_set))
    setVariantGroup(exercise.variant_group ?? '')
    setGripStyle(exercise.grip_style)
    setGripWidth(exercise.grip_width)
    setSupportStyle(exercise.support_style)
    setNotes(exercise.notes ?? '')
  }, [exercise])

  const save = async () => {
    setSaving(true)
    try {
      await updateExercise(exercise.id, {
        laterality,
        load_input_mode: loadInputMode,
        allow_heavy_loading: allowHeavyLoading,
        bodyweight_fraction: Number(bodyweightFraction) || 0,
        external_load_multiplier: Number(externalLoadMultiplier) || 1,
        set_metric_mode: setMetricMode,
        estimated_minutes_per_set: Number(estimatedMinutesPerSet) || 0,
        variant_group: variantGroup.trim() || null,
        grip_style: gripStyle,
        grip_width: gripWidth,
        support_style: supportStyle,
        notes: notes.trim() || null,
      })
      await onSave()
    } catch (error) {
      console.error('Failed to save exercise metadata', error)
      alert(error instanceof Error ? error.message : 'Failed to save exercise metadata')
    } finally {
      setSaving(false)
    }
  }

  return (
    <details className="mt-3 rounded-lg border border-gray-200 bg-gray-50/70 p-3">
      <summary className="cursor-pointer text-[11px] font-medium text-gray-700">
        Edit exercise metadata
      </summary>
      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Laterality</span>
          <select
            value={laterality}
            onChange={(e) => setLaterality(e.target.value as WkExercise['laterality'])}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          >
            {LATERALITY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Load mode</span>
          <select
            value={loadInputMode}
            onChange={(e) => setLoadInputMode(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          >
            {LOAD_INPUT_MODE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Set metric</span>
          <select
            value={setMetricMode}
            onChange={(e) => setSetMetricMode(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          >
            {SET_METRIC_MODE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Bodyweight fraction</span>
          <input
            type="number"
            min={0}
            step={0.05}
            value={bodyweightFraction}
            onChange={(e) => setBodyweightFraction(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">External multiplier</span>
          <input
            type="number"
            min={0}
            step={0.05}
            value={externalLoadMultiplier}
            onChange={(e) => setExternalLoadMultiplier(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Minutes / set</span>
          <input
            type="number"
            min={0}
            step={0.25}
            value={estimatedMinutesPerSet}
            onChange={(e) => setEstimatedMinutesPerSet(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          />
        </label>
        <label className="flex items-center gap-2 rounded border border-gray-200 bg-white px-3 py-2">
          <input
            type="checkbox"
            checked={allowHeavyLoading}
            onChange={(e) => setAllowHeavyLoading(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <span className="text-[11px] text-gray-700">
            Allow heavy loading
          </span>
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Variant group</span>
          <input
            type="text"
            value={variantGroup}
            onChange={(e) => setVariantGroup(e.target.value)}
            placeholder="e.g. pull_up_vertical"
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Grip style</span>
          <select
            value={gripStyle}
            onChange={(e) => setGripStyle(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          >
            {GRIP_STYLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Grip width</span>
          <select
            value={gripWidth}
            onChange={(e) => setGripWidth(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          >
            {GRIP_WIDTH_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Support style</span>
          <select
            value={supportStyle}
            onChange={(e) => setSupportStyle(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          >
            {SUPPORT_STYLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="mt-3 grid gap-1">
        <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Notes</span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          className="rounded border border-gray-300 px-2 py-1 text-xs"
        />
      </label>
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="rounded bg-blue-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save metadata'}
        </button>
        <span className="text-[10px] text-gray-500">
          <code>{weightFieldLabel(exercise)}</code> stays the user-entered field; volume uses the effective load preview below.
        </span>
      </div>
    </details>
  )
}

// ── Tissue View ──

function TissueView({
  tissues,
  exercises,
  readinessMap,
  trackedReadinessByTissue,
  rehabProtocols,
  modelTissues,
  regions,
  onSave,
}: {
  tissues: TissueWithExercises[]
  exercises: WkExercise[]
  readinessMap: Map<number, WkTissueReadiness>
  trackedReadinessByTissue: Map<number, TrackedTissueReadiness[]>
  rehabProtocols: RehabProtocol[]
  modelTissues: TrainingModelTissueSummary[]
  regions: RegionInfo[]
  onSave: () => void
}) {
  const [search, setSearch] = useState('')
  const [selectedTypes, setSelectedTypes] = useState<TissueTypeFilter[]>([])
  const [selectedConditions, setSelectedConditions] = useState<ConditionFilter[]>([])
  const [selectedRoles, setSelectedRoles] = useState<ExerciseRoleFilter[]>([])
  const [selectedStatuses, setSelectedStatuses] = useState<TissueStatusFilter[]>([])
  const [selectedRegions, setSelectedRegions] = useState<string[]>([])
  const [sortField, setSortField] = useState<SortField>('name')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [expandedTissues, setExpandedTissues] = useState<Set<number>>(new Set())
  const toggleTypeFilter = (value: TissueTypeFilter) => {
    setSelectedTypes((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    )
  }

  const toggleConditionFilter = (value: ConditionFilter) => {
    setSelectedConditions((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    )
  }

  const toggleRoleFilter = (value: ExerciseRoleFilter) => {
    setSelectedRoles((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    )
  }

  const toggleStatusFilter = (value: TissueStatusFilter) => {
    setSelectedStatuses((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    )
  }

  const toggleRegionFilter = (value: string) => {
    setSelectedRegions((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    )
  }

  const toggleExpanded = (tissueId: number) => {
    setExpandedTissues((current) => {
      const next = new Set(current)
      if (next.has(tissueId)) next.delete(tissueId)
      else next.add(tissueId)
      return next
    })
  }

  // Build lookup maps
  const modelByTissueId = useMemo(() => {
    const map = new Map<number, TrainingModelTissueSummary>()
    for (const mt of modelTissues) map.set(mt.tissue.id, mt)
    return map
  }, [modelTissues])

  const regionOptions = useMemo(() => {
    const unique = new Set<string>()
    for (const r of regions) unique.add(r.region)
    return Array.from(unique).sort().map((r) => {
      const info = regions.find((ri) => ri.region === r)
      return { value: r, label: info?.label ?? titleCase(r) }
    })
  }, [regions])

  // Build region membership map for filtering
  const tissueRegions = useMemo(() => {
    const map = new Map<number, Set<string>>()
    for (const r of regions) {
      for (const t of r.tissues) {
        const existing = map.get(t.id) ?? new Set()
        existing.add(r.region)
        map.set(t.id, existing)
      }
    }
    return map
  }, [regions])

  const hasTagFilters =
    selectedTypes.length > 0 || selectedConditions.length > 0 || selectedRoles.length > 0 ||
    selectedStatuses.length > 0 || selectedRegions.length > 0

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()

    const result = tissues.filter((t) => {
      if (q && !t.name.toLowerCase().includes(q) && !t.display_name.toLowerCase().includes(q)) {
        return false
      }

      if (selectedTypes.length > 0 && !selectedTypes.includes(t.type as TissueTypeFilter)) {
        return false
      }

      if (selectedConditions.length > 0) {
        const conditionFilters = collectTissueConditionFilters(
          t.id,
          readinessMap,
          trackedReadinessByTissue,
        )
        if (!selectedConditions.some((filter) => conditionFilters.has(filter))) {
          return false
        }
      }

      if (selectedStatuses.length > 0) {
        const model = modelByTissueId.get(t.id)
        if (!model) return false
        const status = getTissueStatus(model)
        if (!selectedStatuses.includes(status)) return false
      }

      if (selectedRegions.length > 0) {
        const tRegions = tissueRegions.get(t.id)
        if (!tRegions || !selectedRegions.some((r) => tRegions.has(r))) return false
      }

      return true
    })

    // Sort
    result.sort((a, b) => {
      let cmp = 0
      const ma = modelByTissueId.get(a.id)
      const mb = modelByTissueId.get(b.id)
      switch (sortField) {
        case 'name':
          cmp = a.display_name.localeCompare(b.display_name)
          break
        case 'capacity_trend':
          cmp = (ma?.capacity_trend_30d_pct ?? 0) - (mb?.capacity_trend_30d_pct ?? 0)
          break
        case 'risk':
          cmp = (ma?.risk_7d ?? 0) - (mb?.risk_7d ?? 0)
          break
        case 'recovery':
          cmp = (ma?.recovery_estimate ?? 0) - (mb?.recovery_estimate ?? 0)
          break
      }
      return sortDir === 'desc' ? -cmp : cmp
    })

    return result
  }, [tissues, search, selectedTypes, selectedConditions, selectedStatuses, selectedRegions, readinessMap, trackedReadinessByTissue, modelByTissueId, tissueRegions, sortField, sortDir])

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <input
          type="text"
          placeholder="Filter tissues..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 max-w-sm border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
        {hasTagFilters && (
          <button
            type="button"
            onClick={() => {
              setSelectedTypes([])
              setSelectedConditions([])
              setSelectedRoles([])
              setSelectedStatuses([])
              setSelectedRegions([])
            }}
            className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="mb-3 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
            Tissue type
          </span>
          {TISSUE_FILTER_OPTIONS.map((option) => (
            <FilterButton
              key={option.value}
              label={option.label}
              toneClassName={TYPE_BADGE[option.value]}
              selected={selectedTypes.includes(option.value)}
              onClick={() => toggleTypeFilter(option.value)}
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
            Condition
          </span>
          {CONDITION_FILTER_OPTIONS.map((option) => (
            <FilterButton
              key={option.value}
              label={option.label}
              toneClassName={CONDITION_COLORS[option.value]}
              selected={selectedConditions.includes(option.value)}
              onClick={() => toggleConditionFilter(option.value)}
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
            Tissue status
          </span>
          {STATUS_FILTER_OPTIONS.map((option) => (
            <FilterButton
              key={option.value}
              label={option.label}
              toneClassName={STATUS_COLORS[option.value]}
              selected={selectedStatuses.includes(option.value)}
              onClick={() => toggleStatusFilter(option.value)}
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
            Region
          </span>
          {regionOptions.map((option) => (
            <FilterButton
              key={option.value}
              label={option.label}
              toneClassName="bg-blue-100 text-blue-700"
              selected={selectedRegions.includes(option.value)}
              onClick={() => toggleRegionFilter(option.value)}
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
            Exercise role
          </span>
          {ROLE_FILTER_OPTIONS.map((option) => (
            <FilterButton
              key={option.value}
              label={option.label}
              toneClassName={ROLE_COLORS[option.value]}
              selected={selectedRoles.includes(option.value)}
              onClick={() => toggleRoleFilter(option.value)}
            />
          ))}
        </div>
        <p className="text-[10px] text-gray-400">
          Role filters only change the exercise mappings shown inside each tissue row.
        </p>

        {/* Sort controls */}
        <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-gray-100">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
            Sort by
          </span>
          {SORT_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                if (sortField === option.value) {
                  setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
                } else {
                  setSortField(option.value)
                  setSortDir(option.value === 'name' ? 'asc' : 'desc')
                }
              }}
              className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                sortField === option.value
                  ? 'bg-gray-800 text-white border-transparent shadow-sm'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-700'
              }`}
            >
              {option.label}
              {sortField === option.value && (sortDir === 'asc' ? ' ↑' : ' ↓')}
            </button>
          ))}
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100">
        {filtered.length === 0 && (
          <p className="text-sm text-gray-500 p-4">No tissues match the current filters.</p>
        )}
        {filtered.map((t) => {
          const readiness = readinessMap.get(t.id)
          const condition = readiness?.condition
          const tissueConditionFilters = collectTissueConditionFilters(
            t.id,
            readinessMap,
            trackedReadinessByTissue,
          )
          const showHealthyConditionTag =
            !condition && tissueConditionFilters.size === 1 && tissueConditionFilters.has('healthy')
          const visibleExercises =
            selectedRoles.length === 0
              ? t.exercises
              : t.exercises.filter((ex) => selectedRoles.includes(ex.role as ExerciseRoleFilter))
          const model = modelByTissueId.get(t.id)
          const status = model ? getTissueStatus(model) : null
          const isExpanded = expandedTissues.has(t.id)
          const daysSince = daysSinceDate(model?.last_trained_date ?? null)

          return (
            <div key={t.id} className="px-4 py-2 hover:bg-gray-50/80 transition-colors">
              {/* Row 1: Identity + quick model badges */}
              <div className="flex items-center gap-2 flex-wrap cursor-pointer" onClick={() => toggleExpanded(t.id)}>
                <span className="text-[10px] text-gray-300 select-none">{isExpanded ? '▼' : '▶'}</span>
                <span className="text-sm font-medium text-gray-800">{t.display_name}</span>
                <span className="text-[10px] font-mono text-gray-400">{t.name}</span>
                <span className={`text-[10px] px-1.5 py-px rounded font-medium ${TYPE_BADGE[t.type] ?? 'bg-gray-100 text-gray-600'}`}>
                  {t.type}
                </span>
                {condition ? (
                  <span className={`text-[10px] px-1.5 py-px rounded font-medium ${CONDITION_COLORS[condition.status] ?? 'bg-gray-100 text-gray-600'}`}>
                    {condition.status}
                    {condition.severity > 0 ? ` (${condition.severity})` : ''}
                  </span>
                ) : showHealthyConditionTag ? (
                  <span className={`text-[10px] px-1.5 py-px rounded font-medium ${CONDITION_COLORS.healthy}`}>
                    healthy
                  </span>
                ) : null}
                {status && (
                  <span className={`text-[10px] px-1.5 py-px rounded font-medium ${STATUS_COLORS[status]}`}>
                    {status}
                  </span>
                )}
                {model && (
                  <>
                    <span className="text-[10px] text-gray-500" title="Recovery estimate (0-1)">
                      🔋 {(model.recovery_estimate * 100).toFixed(0)}%
                    </span>
                    <span className="text-[10px] text-gray-500" title={`Risk 7d: ${model.risk_7d}%`}>
                      ⚠️ {model.risk_7d}%
                    </span>
                    <span
                      className={`text-[10px] ${model.capacity_trend_30d_pct >= 0 ? 'text-green-600' : 'text-red-500'}`}
                      title={`Capacity trend 30d: ${model.capacity_trend_30d_pct > 0 ? '+' : ''}${model.capacity_trend_30d_pct.toFixed(1)}%`}
                    >
                      {model.capacity_trend_30d_pct >= 0 ? '↑' : '↓'} {Math.abs(model.capacity_trend_30d_pct).toFixed(1)}%
                    </span>
                    {model.overworked !== 'good' && (
                      <span className={`text-[10px] px-1.5 py-px rounded font-medium ${model.overworked === 'avoid' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                        {model.overworked}
                      </span>
                    )}
                  </>
                )}
                <span className="text-[10px] text-gray-400" title={model?.last_trained_date ?? readiness?.last_trained ?? 'never'}>
                  ⏱ {daysSince != null
                    ? `${daysSince}d ago`
                    : formatLastWorked(readiness?.last_trained ?? null, readiness?.hours_since ?? null)}
                </span>
              </div>

              {/* Expandable model detail panel */}
              {isExpanded && model && (() => {
                const cfg = t.model_config
                const f = model.risk_features_7d
                const sorenessClamp = Math.max(0.75, Math.min(1.0, 1 - model.current_soreness * 0.04))
                const recovBase = 1 / (1 + model.acute_fatigue)
                return (
                <div className="mt-2 ml-6 p-3 bg-gray-50 rounded-lg border border-gray-200 font-mono text-[10px] leading-relaxed space-y-1.5">
                  {/* DB Config */}
                  <div className="text-[9px] text-gray-400 flex flex-wrap gap-x-3">
                    <span>recovery_hours=<b className="text-blue-600">{t.recovery_hours}h</b></span>
                    {cfg && <>
                      <span>capacity_prior=<b className="text-blue-600">{cfg.capacity_prior}</b></span>
                      <span>recovery_tau=<b className="text-blue-600">{cfg.recovery_tau_days}d</b></span>
                      <span>fatigue_tau=<b className="text-blue-600">{cfg.fatigue_tau_days}d</b></span>
                      <span>collapse_drop=<b className="text-blue-600">{cfg.collapse_drop_threshold}</b></span>
                      <span>ramp_sens=<b className="text-blue-600">{cfg.ramp_sensitivity}</b></span>
                      <span>risk_sens=<b className="text-blue-600">{cfg.risk_sensitivity}</b></span>
                    </>}
                    <span>soreness=<b className="text-blue-600">{model.current_soreness}</b></span>
                    <span>regions=<b className="text-blue-600">{model.tissue_regions.join(',') || '—'}</b></span>
                    <span>last_trained=<b className="text-blue-600">{daysSince != null ? `${daysSince}d ago` : '—'}</b></span>
                  </div>

                  <hr className="border-gray-200" />

                  {/* Recovery */}
                  <div>
                    <span className="text-gray-900 font-semibold">recovery_state</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">1/(1 + <b className="text-amber-700">{model.acute_fatigue.toFixed(3)}</b><span className="text-gray-400 text-[8px]"> [acute_fatigue]</span>) × clamp(1 − <b className="text-blue-600">{model.current_soreness}</b><span className="text-gray-400 text-[8px]"> [soreness]</span>×0.04, 0.75, 1.0)</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">{recovBase.toFixed(3)} × {sorenessClamp.toFixed(3)}</span>
                    <span className="text-gray-400"> = </span>
                    <b className="text-gray-900">{model.recovery_estimate.toFixed(3)}</b>
                  </div>

                  <div>
                    <span className="text-gray-900 font-semibold">acute_fatigue</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">decay(prior, <b className="text-blue-600">{cfg?.fatigue_tau_days ?? '?'}d</b><span className="text-gray-400 text-[8px]"> [fatigue_tau]</span>) + <b className="text-amber-700">{model.fatigue_input.toFixed(3)}</b><span className="text-gray-400 text-[8px]"> [fatigue_input]</span> / max(<b className="text-amber-700">{model.current_capacity.toFixed(3)}</b><span className="text-gray-400 text-[8px]"> [capacity]</span>, 1.0)</span>
                    <span className="text-gray-400"> = </span>
                    <b className="text-gray-900">{model.acute_fatigue.toFixed(3)}</b>
                  </div>

                  <div>
                    <span className="text-gray-900 font-semibold">learned_recovery_days</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">max(<b className="text-amber-700">{model.volume_rebound.toFixed(2)}</b><span className="text-gray-400 text-[8px]"> [vol_rebound]</span>×0.85, 0.7×<b className="text-amber-700">{model.volume_rebound.toFixed(2)}</b><span className="text-gray-400 text-[8px]"> [vol_rebound]</span> + 0.3×<b className="text-amber-700">{model.subjective_days != null ? model.subjective_days.toFixed(2) : '—'}</b><span className="text-gray-400 text-[8px]"> [subj_days]</span>)</span>
                    <span className="text-gray-400"> = </span>
                    <b className="text-gray-900">{model.learned_recovery_days.toFixed(2)}d</b>
                  </div>

                  <hr className="border-gray-200" />

                  {/* Load & Capacity */}
                  <div>
                    <span className="text-gray-900 font-semibold">normalized_load</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600"><b className="text-amber-700">{model.raw_load.toFixed(3)}</b><span className="text-gray-400 text-[8px]"> [raw_load]</span> / max(<b className="text-amber-700">{model.current_capacity.toFixed(3)}</b><span className="text-gray-400 text-[8px]"> [capacity]</span>, 1.0)</span>
                    <span className="text-gray-400"> = </span>
                    <b className="text-gray-900">{model.normalized_load.toFixed(3)}</b>
                  </div>

                  <div>
                    <span className="text-gray-900 font-semibold">chronic_load</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">decay(prior, max(7, <b className="text-amber-700">{model.learned_recovery_days.toFixed(1)}</b><span className="text-gray-400 text-[8px]"> [recovery_days]</span>×6)) + <b className="text-amber-700">{model.normalized_load.toFixed(3)}</b><span className="text-gray-400 text-[8px]"> [norm_load]</span></span>
                    <span className="text-gray-400"> = </span>
                    <b className="text-gray-900">{model.chronic_load.toFixed(3)}</b>
                  </div>

                  <div>
                    <span className="text-gray-900 font-semibold">current_capacity</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">max(<b className="text-amber-700">{model.baseline_capacity.toFixed(3)}</b><span className="text-gray-400 text-[8px]"> [baseline]</span>×0.55, drift + adaptation − penalty)</span>
                    <span className="text-gray-400"> = </span>
                    <b className="text-gray-900">{model.current_capacity.toFixed(3)}</b>
                  </div>

                  <div>
                    <span className="text-gray-900 font-semibold">baseline_capacity</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">p75 of non-zero training loads (prior=<b className="text-blue-600">{cfg?.capacity_prior ?? '?'}</b><span className="text-gray-400 text-[8px]"> [capacity_prior]</span>)</span>
                    <span className="text-gray-400"> = </span>
                    <b className="text-gray-900">{model.baseline_capacity.toFixed(3)}</b>
                  </div>

                  <div>
                    <span className="text-gray-900 font-semibold">ramp_ratio</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">7d_avg / max(28d_avg/4, <b className="text-amber-700">{model.baseline_capacity.toFixed(3)}</b><span className="text-gray-400 text-[8px]"> [baseline]</span>×0.15, 1.0)</span>
                    <span className="text-gray-400"> = </span>
                    <b className="text-gray-900">{model.ramp_ratio.toFixed(3)}</b>
                  </div>

                  <div>
                    <span className="text-gray-900 font-semibold">capacity_trend_30d</span>
                    <span className="text-gray-400"> = </span>
                    <b className={`${model.capacity_trend_30d_pct >= 0 ? 'text-emerald-700' : 'text-red-600'}`}>
                      {model.capacity_trend_30d_pct > 0 ? '+' : ''}{model.capacity_trend_30d_pct.toFixed(2)}%
                    </b>
                    <span className="text-gray-400 ml-2 text-[9px]">collapses: {model.collapse_count}</span>
                  </div>

                  <hr className="border-gray-200" />

                  {/* Risk */}
                  <div>
                    <span className="text-gray-900 font-semibold">risk_7d</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600">sigmoid(Σ w×feat) × <b className="text-blue-600">{cfg?.risk_sensitivity ?? 1}</b><span className="text-gray-400 text-[8px]"> [risk_sens]</span></span>
                    <span className="text-gray-400"> → </span>
                    <b className={`${model.risk_7d >= 75 ? 'text-red-600' : model.risk_7d >= 55 ? 'text-amber-600' : model.risk_7d >= 30 ? 'text-yellow-600' : 'text-emerald-600'}`}>
                      {model.risk_7d}%
                    </b>
                    <span className="text-gray-400 ml-2 text-[9px]">
                      risk_14d=<b>{model.risk_14d}%</b>
                    </span>
                  </div>

                  {f && (
                    <div className="ml-4 text-[9px] text-gray-500">
                      <span className="text-gray-400">features (post-threshold): </span>
                      {Object.entries(f).map(([key, val]) => (
                        <span key={key} className="mr-2">
                          {key}=<b className={val > 0 ? 'text-amber-700' : 'text-gray-400'}>{val.toFixed(4)}</b>
                          <span className="text-gray-300">
                            ×{({normalized_load: '.31', acute_ratio: '.24', ramp_ratio: '.20', condition: '.10', prior: '.07', failures: '.05', soreness: '.07'} as Record<string, string>)[key] ?? '?'}
                          </span>
                        </span>
                      ))}
                    </div>
                  )}

                  <div className="ml-4 text-[9px] text-gray-500">
                    <span className="text-gray-400">raw inputs: </span>
                    <b className="text-amber-700">{model.condition_severity}</b><span className="text-gray-400"> [cond_severity] </span>
                    <b className="text-amber-700">{model.prior_event_signal.toFixed(3)}</b><span className="text-gray-400"> [prior_event] </span>
                    <b className="text-amber-700">{model.failure_count}</b><span className="text-gray-400"> [failures] </span>
                    <b className="text-blue-600">{cfg?.ramp_sensitivity ?? 1}</b><span className="text-gray-400"> [ramp_sens]</span>
                  </div>

                  {model.contributors.length > 0 && (
                    <div className="ml-4 text-[9px] text-gray-500">
                      <span className="text-gray-400">top contributors: </span>{model.contributors.join(', ')}
                    </div>
                  )}

                  <div>
                    <span className="text-gray-900 font-semibold">overworked</span>
                    <span className="text-gray-400"> = </span>
                    <span className="text-gray-600"><b className="text-amber-700">{model.risk_7d}</b><span className="text-gray-400 text-[8px]"> [risk_7d]</span> {model.risk_7d >= 75 ? '≥ 75' : model.risk_7d >= 55 ? '≥ 55' : '< 55'}</span>
                    <span className="text-gray-400"> → </span>
                    <b className={`${model.overworked === 'avoid' ? 'text-red-600' : model.overworked === 'caution' ? 'text-amber-600' : 'text-emerald-600'}`}>
                      {model.overworked}
                    </b>
                  </div>
                </div>
                )
              })()}

              {visibleExercises.length > 0 ? (
                <div className="mt-1 ml-6 flex flex-wrap gap-x-3 gap-y-1">
                  {visibleExercises.map((ex) => {
                    const fullExercise = exercises.find((e) => e.id === ex.exercise_id)
                    return (
                      <span key={ex.exercise_id} className="inline-flex items-center gap-1 text-[11px]">
                        <span className="text-gray-600">{ex.exercise_name}</span>
                        <span className={`px-1 py-px rounded text-[9px] font-medium ${ROLE_COLORS[ex.role] ?? ROLE_COLORS.stabilizer}`}>
                          {ex.role}
                        </span>
                        {fullExercise && (
                          <LoadingEditor
                            value={ex.loading_factor}
                            role={ex.role}
                            exerciseId={ex.exercise_id}
                            tissueId={t.id}
                            exercise={fullExercise}
                            onSave={onSave}
                          />
                        )}
                        <span className="text-[10px] font-mono text-gray-400">
                          r{ex.routing_factor.toFixed(2)}
                        </span>
                      </span>
                    )
                  })}
                </div>
              ) : t.exercises.length > 0 && selectedRoles.length > 0 ? (
                <p className="mt-1 ml-6 text-[11px] italic text-gray-400">
                  No exercise mappings match the selected role filters.
                </p>
              ) : null}

              {trackedReadinessByTissue.get(t.id)?.length ? (
                <div className="mt-2 ml-6 grid gap-2">
                  {trackedReadinessByTissue.get(t.id)!.map((tracked) => (
                    <TrackedTissueCard
                      key={tracked.tracked_tissue.id}
                      tracked={tracked}
                      rehabProtocols={rehabProtocols}
                      onSave={onSave}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          )
        })}
      </div>

      {/* Calculation Reference */}
      <CalculationReference />
    </div>
  )
}

function CalculationReference() {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-6 rounded-xl border border-gray-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        <span>📐 Calculation Reference — How Every Value Is Computed</span>
        <span className="text-gray-400">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="px-4 pb-4 text-[11px] leading-relaxed text-gray-700 space-y-4">
          <section>
            <h3 className="font-semibold text-gray-900 text-xs mb-1">DB-Stored Values (Source of Truth)</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-0.5 font-mono text-[10px]">
              <div><span className="text-blue-600">recovery_hours</span> — Tissue table (default 48h)</div>
              <div><span className="text-blue-600">capacity_prior</span> — TissueModelConfig (default 1.0)</div>
              <div><span className="text-blue-600">recovery_tau_days</span> — TissueModelConfig (default 3.0d)</div>
              <div><span className="text-blue-600">fatigue_tau_days</span> — TissueModelConfig (default 2.0d)</div>
              <div><span className="text-blue-600">collapse_drop_threshold</span> — TissueModelConfig (default 0.45)</div>
              <div><span className="text-blue-600">ramp_sensitivity</span> — TissueModelConfig (default 1.0)</div>
              <div><span className="text-blue-600">risk_sensitivity</span> — TissueModelConfig (default 1.0)</div>
              <div><span className="text-blue-600">soreness_0_10</span> — RegionSorenessCheckIn → distributed to tissues by exercise exposure</div>
              <div><span className="text-blue-600">condition status/severity</span> — TissueCondition (append-only log, latest row wins)</div>
            </div>
          </section>

          <section>
            <h3 className="font-semibold text-gray-900 text-xs mb-1">Derived Values (with formulas)</h3>
            <div className="space-y-2 font-mono text-[10px]">
              <Formula
                name="fatigue_input"
                formula="max(fatigue_load, strain_load) for joint/tendon; else fatigue_load"
              />
              <Formula
                name="acute_fatigue"
                formula="decay(prior, fatigue_tau) + fatigue_input / max(capacity, 1.0)"
                note="decay(v, τ) = v × e^(-1/τ)"
              />
              <Formula
                name="recovery_state"
                formula="1/(1 + acute_fatigue) × clamp(1 − soreness×0.04, 0.75, 1.0)"
                note="Same as recovery_estimate. Range 0-1."
              />
              <Formula
                name="chronic_load"
                formula="decay(prior, max(7, recovery_days×6)) + normalized_load"
              />
              <Formula
                name="capacity"
                formula="max(baseline×0.55, drift + adaptation − penalty)"
                note="drift = cap + (baseline−cap)×0.04 | adaptation = baseline × max(0, min(load,1.15)−0.45) × 0.03 × recovery | penalty = baseline × max(0, load−1.25) × 0.035"
              />
              <Formula
                name="ramp_ratio"
                formula="7d_avg / max(28d_avg/4, baseline×0.15, 1.0)"
                note=">1.0 = acute overload, >1.3 = collapse risk"
              />
              <Formula
                name="baseline_capacity"
                formula="75th percentile of non-zero training day loads (min 1.0)"
              />
              <Formula
                name="learned_recovery_days"
                formula="blend(volume_rebound, subjective_days)"
                note="final = max(vol_rebound×0.85, 0.7×vol_rebound + 0.3×subjective_days)"
              />
              <Formula
                name="volume_rebound"
                formula="(seed + median_rebound_days) / 2"
                note="seed = recovery_hours/24 or recovery_tau_days. Rebound = days until load returns to 80% of pre-dip level."
              />
              <Formula
                name="subjective_days"
                formula="median(days until soreness ≤ 2 post-workout)"
                note="Peak soreness checked +1 to +3d. Bonus: +1.0d if peak≥7, +0.5d if peak≥4."
              />
              <Formula
                name="risk_7d"
                formula="sigmoid(Σ weight_i × feature_i) × 100"
                note="Features: load(31%), fatigue(24%), ramp(20%), condition(10%), prior(7%), failures(5%), soreness(7%). Sigmoid: 100/(1+e^(-4×(score−0.45)))"
              />
              <Formula
                name="overworked"
                formula="'avoid' if risk_7d≥75, 'caution' if risk_7d≥55, else 'good'"
              />
              <Formula
                name="normalized_load"
                formula="raw_load / max(current_capacity, 1.0)"
              />
              <Formula
                name="capacity_trend_30d"
                formula="(recent_cap − earlier_cap) / earlier_cap × 100"
              />
            </div>
          </section>

          <section>
            <h3 className="font-semibold text-gray-900 text-xs mb-1">Global Constants (planner)</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-0.5 font-mono text-[10px]">
              <div>TISSUE_FATIGUE_HARD_FLOOR = 0.4</div>
              <div>TISSUE_FATIGUE_SOFT_FLOOR = 0.7</div>
              <div>REST_DAY_THRESHOLD = 0.35</div>
              <div>DEFAULT_FRESHNESS_DAYS = 21</div>
            </div>
          </section>

          <section>
            <h3 className="font-semibold text-gray-900 text-xs mb-1">Potentially Dead Code</h3>
            <ul className="list-disc list-inside text-[10px] text-gray-500 space-y-0.5">
              <li><b>RecoveryCheckIn.readiness_0_10</b> — stored in DB but always defaults to 5, never read</li>
              <li><b>ramp_sensitivity / risk_sensitivity</b> — in TissueModelConfig but always 1.0, never configured per-tissue</li>
              <li><b>fatigue_factor</b> on ExerciseTissue — mostly overridden by routing_factor logic</li>
              <li><b>Dual 7d/14d risk</b> — nearly identical calculation, ramp dampened ×0.9 for 14d</li>
            </ul>
          </section>
        </div>
      )}
    </div>
  )
}

function Formula({ name, formula, note }: { name: string; formula: string; note?: string }) {
  return (
    <div>
      <span className="text-gray-900 font-semibold">{name}</span>
      <span className="text-gray-500"> = </span>
      <span className="text-gray-700">{formula}</span>
      {note && <div className="ml-4 text-[9px] text-gray-400 italic">{note}</div>}
    </div>
  )
}

function TrackedTissueCard({
  tracked,
  rehabProtocols,
  onSave,
}: {
  tracked: TrackedTissueReadiness
  rehabProtocols: RehabProtocol[]
  onSave: () => void
}) {
  const [showConditionEditor, setShowConditionEditor] = useState(false)
  const [showPlanEditor, setShowPlanEditor] = useState(false)
  const [savingCondition, setSavingCondition] = useState(false)
  const [savingPlan, setSavingPlan] = useState(false)

  const [conditionStatus, setConditionStatus] = useState<
    'healthy' | 'tender' | 'injured' | 'rehabbing'
  >(tracked.condition?.status ?? 'healthy')
  const [conditionSeverity, setConditionSeverity] = useState(String(tracked.condition?.severity ?? 0))
  const [conditionMaxLoading, setConditionMaxLoading] = useState(
    tracked.condition?.max_loading_factor?.toString() ?? '',
  )
  const [conditionProtocolId, setConditionProtocolId] = useState(
    tracked.condition?.rehab_protocol ?? tracked.active_rehab_plan?.protocol_id ?? '',
  )
  const [conditionNotes, setConditionNotes] = useState(tracked.condition?.notes ?? '')

  const defaultPlanProtocolId =
    tracked.active_rehab_plan?.protocol_id
    ?? tracked.condition?.rehab_protocol
    ?? rehabProtocols[0]?.id
    ?? ''
  const [planProtocolId, setPlanProtocolId] = useState(defaultPlanProtocolId)
  const selectedProtocol =
    rehabProtocols.find((protocol) => protocol.id === planProtocolId) ?? rehabProtocols[0] ?? null
  const [stageId, setStageId] = useState(
    tracked.active_rehab_plan?.stage_id
      ?? selectedProtocol?.stages[0]?.id
      ?? '',
  )
  const [planStatus, setPlanStatus] = useState<'active' | 'paused' | 'completed'>(
    tracked.active_rehab_plan?.status ?? 'active',
  )
  const [painThreshold, setPainThreshold] = useState<SymptomSeverityLevel>(
    symptomDbToSeverity(
      tracked.active_rehab_plan?.pain_monitoring_threshold
      ?? selectedProtocol?.default_pain_monitoring_threshold
      ?? 3,
    ),
  )
  const [nextDayFlare, setNextDayFlare] = useState<SymptomSeverityLevel>(
    symptomDbToSeverity(
      tracked.active_rehab_plan?.max_next_day_flare
      ?? selectedProtocol?.default_max_next_day_flare
      ?? 2,
    ),
  )
  const [planNotes, setPlanNotes] = useState(tracked.active_rehab_plan?.notes ?? '')

  const chipClasses = tracked.protected
    ? 'bg-purple-50 border-purple-200 text-purple-700'
    : tracked.ready
      ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
      : 'bg-gray-50 border-gray-200 text-gray-600'

  useEffect(() => {
    setConditionStatus(tracked.condition?.status ?? 'healthy')
    setConditionSeverity(String(tracked.condition?.severity ?? 0))
    setConditionMaxLoading(tracked.condition?.max_loading_factor?.toString() ?? '')
    setConditionProtocolId(
      tracked.condition?.rehab_protocol ?? tracked.active_rehab_plan?.protocol_id ?? '',
    )
    setConditionNotes(tracked.condition?.notes ?? '')

    const nextPlanProtocolId =
      tracked.active_rehab_plan?.protocol_id
      ?? tracked.condition?.rehab_protocol
      ?? rehabProtocols[0]?.id
      ?? ''
    const nextSelectedProtocol =
      rehabProtocols.find((protocol) => protocol.id === nextPlanProtocolId) ?? rehabProtocols[0] ?? null

    setPlanProtocolId(nextPlanProtocolId)
    setStageId(tracked.active_rehab_plan?.stage_id ?? nextSelectedProtocol?.stages[0]?.id ?? '')
    setPlanStatus(tracked.active_rehab_plan?.status ?? 'active')
    setPainThreshold(
      symptomDbToSeverity(
        tracked.active_rehab_plan?.pain_monitoring_threshold
        ?? nextSelectedProtocol?.default_pain_monitoring_threshold
        ?? 3,
      ),
    )
    setNextDayFlare(
      symptomDbToSeverity(
        tracked.active_rehab_plan?.max_next_day_flare
        ?? nextSelectedProtocol?.default_max_next_day_flare
        ?? 2,
      ),
    )
    setPlanNotes(tracked.active_rehab_plan?.notes ?? '')
  }, [tracked, rehabProtocols])

  const handlePlanProtocolChange = (nextProtocolId: string) => {
    setPlanProtocolId(nextProtocolId)
    const protocol = rehabProtocols.find((item) => item.id === nextProtocolId)
    if (protocol) {
      setStageId(protocol.stages[0]?.id ?? '')
      setPainThreshold(symptomDbToSeverity(protocol.default_pain_monitoring_threshold))
      setNextDayFlare(symptomDbToSeverity(protocol.default_max_next_day_flare))
    }
  }

  const saveCondition = async () => {
    setSavingCondition(true)
    try {
      await createTissueCondition({
        tracked_tissue_id: tracked.tracked_tissue.id,
        status: conditionStatus,
        severity: Number(conditionSeverity) || 0,
        max_loading_factor: parseNullableNumber(conditionMaxLoading),
        rehab_protocol: conditionProtocolId || null,
        notes: conditionNotes.trim() || null,
      })
      setShowConditionEditor(false)
      await onSave()
    } catch (error) {
      console.error('Failed to save tissue condition', error)
      alert(error instanceof Error ? error.message : 'Failed to save tissue condition')
    } finally {
      setSavingCondition(false)
    }
  }

  const savePlan = async () => {
    if (!planProtocolId || !stageId) {
      alert('Select a rehab protocol and stage first.')
      return
    }
    setSavingPlan(true)
    try {
      const payload = {
        tracked_tissue_id: tracked.tracked_tissue.id,
        protocol_id: planProtocolId,
        stage_id: stageId,
        status: planStatus,
        pain_monitoring_threshold: symptomSeverityToDb(painThreshold),
        max_next_day_flare: symptomSeverityToDb(nextDayFlare),
        notes: planNotes.trim() || null,
      }
      if (tracked.active_rehab_plan) {
        await updateRehabPlan(tracked.active_rehab_plan.id, {
          protocol_id: payload.protocol_id,
          stage_id: payload.stage_id,
          status: payload.status,
          pain_monitoring_threshold: payload.pain_monitoring_threshold,
          max_next_day_flare: payload.max_next_day_flare,
          notes: payload.notes,
        })
      } else {
        await createRehabPlan(payload)
      }
      setShowPlanEditor(false)
      await onSave()
    } catch (error) {
      console.error('Failed to save rehab plan', error)
      alert(error instanceof Error ? error.message : 'Failed to save rehab plan')
    } finally {
      setSavingPlan(false)
    }
  }

  return (
    <div className={`rounded-xl border px-3 py-2 text-[11px] ${chipClasses}`} title={tracked.last_trained ?? 'never'}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{formatSide(tracked.tracked_tissue.side)}</span>
            <span className="font-semibold">{tracked.tracked_tissue.display_name}</span>
            {tracked.condition && (
              <span className={`rounded px-1.5 py-px font-medium ${CONDITION_COLORS[tracked.condition.status] ?? 'bg-gray-100 text-gray-600'}`}>
                {tracked.condition.status}
                {tracked.condition.severity > 0 ? ` (${tracked.condition.severity})` : ''}
              </span>
            )}
            {tracked.active_rehab_plan && (
              <span className="rounded bg-white/70 px-1.5 py-px font-medium text-purple-700">
                {tracked.active_rehab_plan.protocol_title} · {tracked.active_rehab_plan.stage_label}
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-2 text-[10px] opacity-80">
            <span>vol {tracked.volume_7d}</span>
            {tracked.cross_education_7d > 0 && <span>ce {tracked.cross_education_7d}</span>}
            <span>{tracked.ready ? 'ready' : 'monitor'}</span>
            {tracked.latest_rehab_check_in && (
              <span>
                last check-in pain {tracked.latest_rehab_check_in.pain_0_10}/10
              </span>
            )}
          </div>
          {tracked.condition?.notes && (
            <p className="text-[10px] text-gray-600">{tracked.condition.notes}</p>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={() => setShowConditionEditor((value) => !value)}
            className="rounded border border-gray-300 bg-white px-2 py-1 text-[10px] text-gray-700 hover:bg-gray-50"
          >
            {tracked.condition ? 'Edit condition' : 'Add condition'}
          </button>
          <button
            type="button"
            onClick={() => setShowPlanEditor((value) => !value)}
            className="rounded border border-gray-300 bg-white px-2 py-1 text-[10px] text-gray-700 hover:bg-gray-50"
          >
            {tracked.active_rehab_plan ? 'Edit rehab plan' : 'Add rehab plan'}
          </button>
        </div>
      </div>

      {showConditionEditor && (
        <div className="mt-3 grid gap-2 rounded-lg border border-gray-200 bg-white p-3 text-gray-700">
          <div className="grid gap-2 md:grid-cols-4">
            <label className="grid gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Status</span>
              <select
                value={conditionStatus}
                onChange={(e) => setConditionStatus(e.target.value as 'healthy' | 'tender' | 'injured' | 'rehabbing')}
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              >
                {CONDITION_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            <label className="grid gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Severity</span>
              <input
                type="number"
                min={0}
                max={3}
                value={conditionSeverity}
                onChange={(e) => setConditionSeverity(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              />
            </label>
            <label className="grid gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Max loading</span>
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={conditionMaxLoading}
                onChange={(e) => setConditionMaxLoading(e.target.value)}
                placeholder="optional"
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              />
            </label>
            <label className="grid gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Protocol tag</span>
              <select
                value={conditionProtocolId}
                onChange={(e) => setConditionProtocolId(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              >
                <option value="">None</option>
                {rehabProtocols.map((protocol) => (
                  <option key={protocol.id} value={protocol.id}>{protocol.title}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="grid gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Notes</span>
            <textarea
              value={conditionNotes}
              onChange={(e) => setConditionNotes(e.target.value)}
              rows={2}
              className="rounded border border-gray-300 px-2 py-1 text-xs"
            />
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={saveCondition}
              disabled={savingCondition}
              className="rounded bg-blue-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {savingCondition ? 'Saving...' : 'Save condition'}
            </button>
            <button
              type="button"
              onClick={() => setShowConditionEditor(false)}
              className="rounded border border-gray-300 px-3 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {showPlanEditor && (
        <div className="mt-3 grid gap-2 rounded-lg border border-gray-200 bg-white p-3 text-gray-700">
          <div className="grid gap-2 md:grid-cols-4">
            <label className="grid gap-1 md:col-span-2">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Protocol</span>
              <select
                value={planProtocolId}
                onChange={(e) => handlePlanProtocolChange(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              >
                {rehabProtocols.map((protocol) => (
                  <option key={protocol.id} value={protocol.id}>{protocol.title}</option>
                ))}
              </select>
            </label>
            <label className="grid gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Stage</span>
              <select
                value={stageId}
                onChange={(e) => setStageId(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              >
                {(selectedProtocol?.stages ?? []).map((stage) => (
                  <option key={stage.id} value={stage.id}>{stage.label}</option>
                ))}
              </select>
            </label>
            <label className="grid gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Status</span>
              <select
                value={planStatus}
                onChange={(e) => setPlanStatus(e.target.value as 'active' | 'paused' | 'completed')}
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              >
                <option value="active">Active</option>
                <option value="paused">Paused</option>
                <option value="completed">Completed</option>
              </select>
            </label>
          </div>
          {selectedProtocol && (
            <p className="text-[10px] text-gray-500">{selectedProtocol.summary}</p>
          )}
          <div className="grid gap-3 md:grid-cols-2">
            <SymptomSeverityRow
              label="During-load pain ceiling"
              value={painThreshold}
              onChange={setPainThreshold}
            />
            <SymptomSeverityRow
              label="Next-day flare ceiling"
              value={nextDayFlare}
              onChange={setNextDayFlare}
            />
          </div>
          <p className="text-[10px] text-gray-500">
            These use the same clinical severity categories as the recovery check-in.
          </p>
          <label className="grid gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Notes</span>
            <textarea
              value={planNotes}
              onChange={(e) => setPlanNotes(e.target.value)}
              rows={2}
              className="rounded border border-gray-300 px-2 py-1 text-xs"
            />
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={savePlan}
              disabled={savingPlan}
              className="rounded bg-purple-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-purple-700 disabled:opacity-50"
            >
              {savingPlan ? 'Saving...' : tracked.active_rehab_plan ? 'Update rehab plan' : 'Create rehab plan'}
            </button>
            <button
              type="button"
              onClick={() => setShowPlanEditor(false)}
              className="rounded border border-gray-300 px-3 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Exercise Progress Chart (inline per-exercise) ──

const SCHEME_COLORS: Record<RepScheme, string> = {
  heavy: '#ef4444',
  medium: '#f59e0b',
  volume: '#3b82f6',
}

function ExerciseProgressInline({ exerciseId }: { exerciseId: number }) {
  const [history, setHistory] = useState<WkExerciseHistory | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getExerciseHistory(exerciseId, 200)
      .then(data => { if (!cancelled) setHistory(data) })
      .catch(() => { if (!cancelled) setHistory(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [exerciseId])

  const sessionData = useMemo(() => {
    if (!history) return []
    return history.sessions
      .map(session => {
        // Find max weight from fully-completed sets
        let maxWeight = 0
        for (const set of session.sets) {
          if (set.weight && set.weight > 0 && set.rep_completion === 'full') {
            maxWeight = Math.max(maxWeight, set.weight)
          }
        }
        // Fall back to any set if none were "full"
        if (maxWeight === 0) {
          for (const set of session.sets) {
            if (set.weight && set.weight > 0) maxWeight = Math.max(maxWeight, set.weight)
          }
        }
        return {
          date: session.date,
          scheme: session.rep_scheme,
          maxWeight,
          totalVolume: session.total_volume,
        }
      })
      .filter(s => s.maxWeight > 0)
      .sort((a, b) => a.date.localeCompare(b.date))
  }, [history])

  if (loading) return <p className="text-[10px] text-gray-400 py-1">Loading chart...</p>
  if (!history || sessionData.length === 0) return <p className="text-[10px] text-gray-400 py-1">No session history.</p>

  const svgWidth = 400
  const chartHeight = 120
  const marginLeft = 38
  const marginRight = 8
  const marginTop = 8
  const marginBottom = 22
  const plotWidth = svgWidth - marginLeft - marginRight
  const plotHeight = chartHeight - marginTop - marginBottom

  const maxWeight = Math.max(...sessionData.map(s => s.maxWeight)) * 1.1
  const count = sessionData.length
  const step = count > 1 ? plotWidth / (count - 1) : plotWidth / 2

  const toX = (i: number) => marginLeft + (count > 1 ? i * step : plotWidth / 2)
  const toY = (v: number) => marginTop + ((maxWeight - v) / maxWeight) * plotHeight

  // Build per-scheme polylines
  const byScheme: Record<string, { x: number; y: number }[]> = { heavy: [], medium: [], volume: [] }
  sessionData.forEach((s, i) => {
    byScheme[s.scheme]?.push({ x: toX(i), y: toY(s.maxWeight) })
  })

  const sessions = history.sessions.length
  const schemes = [...new Set(sessionData.map(s => s.scheme))]

  // Summary stats per scheme
  const schemeStats = schemes.map(scheme => {
    const pts = sessionData.filter(s => s.scheme === scheme)
    const best = Math.max(...pts.map(p => p.maxWeight))
    const latest = pts[pts.length - 1]?.maxWeight ?? 0
    return { scheme, best, latest, count: pts.length }
  })

  return (
    <div className="mt-2">
      <div className="flex items-center gap-3 mb-1">
        <span className="text-[10px] uppercase tracking-wider text-gray-400 font-semibold">Max Weight by Scheme</span>
        <span className="text-[10px] text-gray-400">{sessions} sessions</span>
        <div className="flex gap-2 ml-auto">
          {schemes.map(s => (
            <span key={s} className="flex items-center gap-1 text-[10px]">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: SCHEME_COLORS[s] }} />
              {titleCase(s)}
            </span>
          ))}
        </div>
      </div>

      {schemeStats.length > 0 && (
        <div className="flex gap-3 mb-1.5">
          {schemeStats.map(st => (
            <div key={st.scheme} className="text-[10px] text-gray-500">
              <span className="font-medium" style={{ color: SCHEME_COLORS[st.scheme] }}>{titleCase(st.scheme)}</span>
              {': '}best {st.best}lb, latest {st.latest}lb ({st.count})
            </div>
          ))}
        </div>
      )}

      <svg viewBox={`0 0 ${svgWidth} ${chartHeight}`} className="w-full" style={{ height: `${chartHeight}px` }}>
        {/* Grid lines */}
        {[0, 0.5, 1].map((frac, i) => {
          const val = frac * maxWeight
          const y = toY(val)
          return (
            <g key={i}>
              <line x1={marginLeft} x2={svgWidth - marginRight} y1={y} y2={y} stroke="#e5e7eb" strokeDasharray="3 3" />
              <text x={marginLeft - 3} y={y + 3} textAnchor="end" fontSize="8" fill="#9ca3af">{Math.round(val)}</text>
            </g>
          )
        })}

        {/* Dots for each session (colored by scheme) */}
        {sessionData.map((s, i) => (
          <circle key={`${s.date}-${s.scheme}`} cx={toX(i)} cy={toY(s.maxWeight)} r={2.5}
            fill={SCHEME_COLORS[s.scheme]} opacity={0.8} />
        ))}

        {/* Connect dots within same scheme */}
        {Object.entries(byScheme).map(([scheme, pts]) => (
          pts.length >= 2 && (
            <polyline key={scheme}
              points={pts.map(p => `${p.x},${p.y}`).join(' ')}
              fill="none" stroke={SCHEME_COLORS[scheme as RepScheme]} strokeWidth={1.5}
              strokeLinejoin="round" opacity={0.6} />
          )
        ))}

        {/* Date labels (sparse) */}
        {sessionData.map((s, i) => {
          const showLabel = count <= 20 || i === 0 || i === count - 1 || i % Math.ceil(count / 8) === 0
          return showLabel ? (
            <text key={`label-${i}`} x={toX(i)} y={chartHeight - 4} textAnchor="middle" fontSize="7" fill="#9ca3af">
              {new Date(s.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
            </text>
          ) : null
        })}
      </svg>
    </div>
  )
}

// ── Exercise View ──

const RECOMMENDATION_BADGE: Record<string, string> = {
  avoid: 'bg-red-100 text-red-700 border-red-200',
  caution: 'bg-amber-100 text-amber-700 border-amber-200',
  good: 'bg-emerald-100 text-emerald-700 border-emerald-200',
}

const RISK_COLOR = (risk: number) =>
  risk >= 75 ? 'text-red-600' : risk >= 55 ? 'text-amber-600' : risk >= 30 ? 'text-yellow-600' : 'text-emerald-600'

function ExerciseView({
  exercises,
  tissues,
  exerciseInsights,
  regions,
  onSave,
}: {
  exercises: WkExercise[]
  tissues: WkTissue[]
  exerciseInsights: TrainingModelExerciseInsight[]
  regions: RegionInfo[]
  onSave: () => void
}) {
  const [search, setSearch] = useState('')
  const [selectedRecommendations, setSelectedRecommendations] = useState<string[]>([])
  const [selectedRegions, setSelectedRegions] = useState<string[]>([])
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [applyingWarningKey, setApplyingWarningKey] = useState<string | null>(null)

  const tissueNameById = useMemo(
    () => new Map(tissues.map((tissue) => [tissue.id, tissue.display_name])),
    [tissues],
  )

  const insightById = useMemo(() => {
    const map = new Map<number, TrainingModelExerciseInsight>()
    for (const ins of exerciseInsights) map.set(ins.id, ins)
    return map
  }, [exerciseInsights])

  // Build exercise→regions mapping via tissue mappings
  const exerciseRegions = useMemo(() => {
    const tissueToRegions = new Map<number, Set<string>>()
    for (const region of regions) {
      for (const tissue of region.tissues) {
        const existing = tissueToRegions.get(tissue.id) ?? new Set()
        existing.add(region.region)
        tissueToRegions.set(tissue.id, existing)
      }
    }
    const result = new Map<number, Set<string>>()
    for (const ex of exercises) {
      const exRegions = new Set<string>()
      for (const mapping of ex.tissues) {
        const tRegions = tissueToRegions.get(mapping.tissue_id)
        if (tRegions) tRegions.forEach(r => exRegions.add(r))
      }
      result.set(ex.id, exRegions)
    }
    return result
  }, [exercises, regions])

  const allRegionOptions = useMemo(() => {
    return regions.map(r => ({ value: r.region, label: r.label }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [regions])

  const hasFilters = selectedRecommendations.length > 0 || selectedRegions.length > 0

  const filtered = useMemo(() => {
    let result = exercises
    if (search) {
      const q = search.toLowerCase()
      result = result.filter((e) => e.name.toLowerCase().includes(q))
    }
    if (selectedRecommendations.length > 0) {
      result = result.filter(e => {
        const ins = insightById.get(e.id)
        return ins && selectedRecommendations.includes(ins.recommendation)
      })
    }
    if (selectedRegions.length > 0) {
      result = result.filter(e => {
        const eRegions = exerciseRegions.get(e.id)
        return eRegions && selectedRegions.some(r => eRegions.has(r))
      })
    }
    return result
  }, [exercises, search, selectedRecommendations, selectedRegions, insightById, exerciseRegions])

  const toggleRecommendation = (val: string) => {
    setSelectedRecommendations(cur => cur.includes(val) ? cur.filter(v => v !== val) : [...cur, val])
  }

  const toggleRegion = (val: string) => {
    setSelectedRegions(cur => cur.includes(val) ? cur.filter(v => v !== val) : [...cur, val])
  }

  const handleApplyWarning = async (
    exerciseId: number,
    warning: WkExercise['mapping_warnings'][number],
  ) => {
    const warningKey = `${exerciseId}-${warning.code}-${warning.source_tissue_id}-${warning.target_tissue_id}`
    setApplyingWarningKey(warningKey)
    try {
      await applyExerciseMappingWarning(exerciseId, {
        code: warning.code,
        source_tissue_id: warning.source_tissue_id,
        target_tissue_id: warning.target_tissue_id,
      })
      await onSave()
    } catch (error) {
      console.error('Failed to apply mapping warning', error)
      alert(error instanceof Error ? error.message : 'Failed to add suggested mapping')
    } finally {
      setApplyingWarningKey(null)
    }
  }

  const recCounts = useMemo(() => {
    const counts = { good: 0, caution: 0, avoid: 0 }
    for (const ex of exercises) {
      const ins = insightById.get(ex.id)
      if (ins) counts[ins.recommendation]++
    }
    return counts
  }, [exercises, insightById])

  return (
    <div>
      {/* Filters */}
      <div className="space-y-2 mb-3">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            placeholder="Filter exercises..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 min-w-[200px] max-w-sm border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
          {hasFilters && (
            <button
              type="button"
              onClick={() => { setSelectedRecommendations([]); setSelectedRegions([]) }}
              className="text-[10px] text-gray-400 hover:text-gray-600 underline"
            >
              Clear filters
            </button>
          )}
        </div>

        {/* Recommendation filter */}
        <div className="flex flex-wrap gap-1.5">
          <span className="text-[10px] text-gray-400 font-medium self-center mr-1">Status:</span>
          {(['good', 'caution', 'avoid'] as const).map(rec => (
            <button
              key={rec}
              type="button"
              onClick={() => toggleRecommendation(rec)}
              className={`rounded-lg border px-2 py-0.5 text-[11px] transition-all ${
                selectedRecommendations.includes(rec)
                  ? RECOMMENDATION_BADGE[rec] + ' font-semibold border-current'
                  : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300'
              }`}
            >
              {titleCase(rec)} ({recCounts[rec]})
            </button>
          ))}
        </div>

        {/* Region filter */}
        {allRegionOptions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            <span className="text-[10px] text-gray-400 font-medium self-center mr-1">Region:</span>
            {allRegionOptions.map(opt => (
              <button
                key={opt.value}
                type="button"
                onClick={() => toggleRegion(opt.value)}
                className={`rounded-lg border px-2 py-0.5 text-[11px] transition-all ${
                  selectedRegions.includes(opt.value)
                    ? 'border-sky-300 bg-sky-100 font-semibold text-sky-700'
                    : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <p className="text-[11px] text-gray-400 mb-2">{filtered.length} exercise{filtered.length !== 1 ? 's' : ''}</p>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100">
        {filtered.length === 0 && (
          <p className="text-sm text-gray-500 p-4">No exercises found.</p>
        )}
        {filtered.map((ex) => {
          const insight = insightById.get(ex.id)
          const isExpanded = expandedId === ex.id
          return (
            <div key={ex.id} className="px-4 py-2.5 hover:bg-gray-50/80 transition-colors">
              {/* Header row with status integration */}
              <div className="flex items-center gap-2 flex-wrap cursor-pointer" onClick={() => setExpandedId(isExpanded ? null : ex.id)}>
                <span className="text-[10px] text-gray-300 select-none">{isExpanded ? '▼' : '▶'}</span>
                <span className="text-sm font-medium text-gray-800">{ex.name}</span>
                {insight && (
                  <span className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${RECOMMENDATION_BADGE[insight.recommendation]}`}>
                    {insight.recommendation}
                  </span>
                )}
                {ex.equipment && (
                  <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-px rounded">
                    {ex.equipment}
                  </span>
                )}
                <span className="text-[10px] rounded bg-blue-50 px-1.5 py-px text-blue-700">
                  {titleCase(ex.load_input_mode)}
                </span>
                <span className="text-[10px] rounded bg-purple-50 px-1.5 py-px text-purple-700">
                  {titleCase(ex.set_metric_mode)}
                </span>
                {ex.variant_group && (
                  <span className="text-[10px] rounded bg-emerald-50 px-1.5 py-px text-emerald-700">
                    {ex.variant_group}
                  </span>
                )}
                {/* Risk score on the right */}
                {insight && (
                  <div className="ml-auto flex items-center gap-2">
                    {insight.current_e1rm != null && (
                      <span className="text-[10px] text-gray-400">e1RM: {insight.current_e1rm}lb</span>
                    )}
                    <span className="text-[10px] text-gray-400">suit: {Math.round(insight.suitability_score)}%</span>
                    <span className={`text-base font-bold tabular-nums ${RISK_COLOR(insight.weighted_risk_7d)}`}>
                      {Math.round(insight.weighted_risk_7d)}
                    </span>
                    <span className="text-[10px] text-gray-400">%</span>
                  </div>
                )}
              </div>

              {/* Risk details line */}
              {insight && insight.recommendation_details.length > 0 && (
                <div className="mt-0.5 ml-5 flex flex-wrap gap-1">
                  {insight.recommendation_details.slice(0, 3).map(detail => (
                    <span key={detail} className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">
                      {detail}
                    </span>
                  ))}
                </div>
              )}

              {/* Expanded content */}
              {isExpanded && (
                <div className="mt-2 ml-5 space-y-2">
                  {/* Progress chart */}
                  <ExerciseProgressInline exerciseId={ex.id} />

                  {/* Metadata panels */}
                  <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded-lg border border-gray-200 bg-white p-2 text-[11px] text-gray-600">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
                        Load semantics
                      </div>
                      <div className="mt-1 space-y-0.5">
                        <p>{weightFieldLabel(ex)}</p>
                        <p>Bodyweight fraction: {ex.bodyweight_fraction.toFixed(2)}</p>
                        <p>External multiplier: x{ex.external_load_multiplier.toFixed(2)}</p>
                      </div>
                    </div>
                    <div className="rounded-lg border border-gray-200 bg-white p-2 text-[11px] text-gray-600">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
                        Variant metadata
                      </div>
                      <div className="mt-1 space-y-0.5">
                        <p>Grip: {titleCase(ex.grip_style)}</p>
                        <p>Width: {titleCase(ex.grip_width)}</p>
                        <p>Support: {titleCase(ex.support_style)}</p>
                      </div>
                    </div>
                    <div className="rounded-lg border border-gray-200 bg-white p-2 text-[11px] text-gray-600 md:col-span-2">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">
                        Load preview
                      </div>
                      <div className="mt-1 space-y-0.5">
                        {loadPreviewLines(ex).map((line) => (
                          <p key={line}>{line}</p>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Mapping warnings */}
                  {ex.mapping_warnings.length > 0 && (
                    <div className="space-y-1">
                      {ex.mapping_warnings.map((warning, index) => (
                        <div
                          key={`${warning.code}-${warning.source_tissue_id}-${warning.target_tissue_id}-${index}`}
                          className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800"
                        >
                          <p className="font-medium">{warning.message}</p>
                          <p className="mt-0.5 text-[10px] text-amber-700">
                            Suggested companion tissue: {tissueNameById.get(warning.target_tissue_id) ?? `#${warning.target_tissue_id}`}
                          </p>
                          {warning.suggested_mapping && (
                            <p className="mt-0.5 text-[10px] text-amber-700">
                              Quick-add defaults: {warning.suggested_mapping.role}, load {warning.suggested_mapping.loading_factor.toFixed(2)}, laterality {titleCase(warning.suggested_mapping.laterality_mode)}
                            </p>
                          )}
                          {warning.code === 'missing-related-tissue' && warning.suggested_mapping && (
                            <button
                              type="button"
                              onClick={() => handleApplyWarning(ex.id, warning)}
                              disabled={applyingWarningKey === `${ex.id}-${warning.code}-${warning.source_tissue_id}-${warning.target_tissue_id}`}
                              className="mt-2 rounded border border-amber-300 bg-white px-2 py-1 text-[10px] font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
                            >
                              {applyingWarningKey === `${ex.id}-${warning.code}-${warning.source_tissue_id}-${warning.target_tissue_id}`
                                ? 'Adding...'
                                : 'Add suggested mapping'}
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Tissue mappings */}
                  {ex.tissues.length > 0 ? (
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      {ex.tissues.map((t) => (
                        <span key={t.tissue_id} className="inline-flex items-center gap-1 text-[11px]">
                          <span className="text-gray-600">{t.tissue_display_name}</span>
                          <span className="text-[10px] font-mono text-gray-400">({t.tissue_name})</span>
                          <span className={`text-[10px] px-1.5 py-px rounded font-medium ${TYPE_BADGE[t.tissue_type] ?? 'bg-gray-100 text-gray-600'}`}>
                            {t.tissue_type}
                          </span>
                          <span className={`px-1 py-px rounded text-[9px] font-medium ${ROLE_COLORS[t.role] ?? ROLE_COLORS.stabilizer}`}>
                            {t.role}
                          </span>
                          <span className="text-[10px] font-mono text-gray-400">
                            r{t.routing_factor.toFixed(2)}
                          </span>
                          <LoadingEditor
                            value={t.loading_factor}
                            role={t.role}
                            exerciseId={ex.id}
                            tissueId={t.tissue_id}
                            exercise={ex}
                            onSave={onSave}
                          />
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[11px] text-gray-400 italic">No tissue mappings</p>
                  )}

                  <ExerciseMetadataEditor exercise={ex} onSave={onSave} />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Main Page ──

export default function TissueAdminPage() {
  const [tissues, setTissues] = useState<WkTissue[]>([])
  const [regions, setRegions] = useState<RegionInfo[]>([])
  const [exercises, setExercises] = useState<WkExercise[]>([])
  const [readiness, setReadiness] = useState<WkTissueReadiness[]>([])
  const [trackedReadiness, setTrackedReadiness] = useState<TrackedTissueReadiness[]>([])
  const [rehabProtocols, setRehabProtocols] = useState<RehabProtocol[]>([])
  const [modelSummary, setModelSummary] = useState<TrainingModelSummary | null>(null)
  const [view, setView] = useState<View>('tissues')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      const [t, regionData, e, r, tr, protocols, summary] = await Promise.all([
        getTissues(),
        getRegions(),
        getExercises(),
        getTissueReadiness(),
        getTrackedTissueReadiness(),
        getRehabProtocols(),
        getTrainingModelSummary(undefined, true).catch(() => null),
      ])
      setTissues(t)
      setRegions(regionData)
      setExercises(e)
      setReadiness(r)
      setTrackedReadiness(tr)
      setRehabProtocols(protocols)
      setModelSummary(summary)
    } catch (err) {
      console.error('Failed to load data', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // Attach exercise mappings to tissues
  const tissuesWithExercises: TissueWithExercises[] = useMemo(() => {
    return tissues
      .map((t) => {
        const exs: TissueWithExercises['exercises'] = []
        for (const ex of exercises) {
          for (const m of ex.tissues) {
            if (m.tissue_id === t.id) {
              exs.push({
                exercise_id: ex.id,
                exercise_name: ex.name,
                role: m.role,
                loading_factor: m.loading_factor,
                routing_factor: m.routing_factor,
              })
            }
          }
        }
        return { ...t, exercises: exs }
      })
      .sort((a, b) => a.display_name.localeCompare(b.display_name))
  }, [tissues, exercises])

  const readinessMap = useMemo(() => {
    const map = new Map<number, WkTissueReadiness>()
    for (const r of readiness) map.set(r.tissue.id, r)
    return map
  }, [readiness])

  const trackedReadinessByTissue = useMemo(() => {
    const map = new Map<number, TrackedTissueReadiness[]>()
    for (const row of trackedReadiness) {
      const tissueId = row.tracked_tissue.tissue_id
      const existing = map.get(tissueId) ?? []
      existing.push(row)
      map.set(tissueId, existing)
    }
    return map
  }, [trackedReadiness])

  if (loading) {
    return <div className="text-sm text-gray-500 p-6">Loading...</div>
  }

  return (
    <div className="space-y-4 pb-8 overflow-y-auto h-full">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-lg font-semibold text-gray-800">Tissues & Exercises</h1>
          <p className="text-sm text-gray-500">
            Review training-model status, then manage the detailed tissue and exercise metadata below.
          </p>
        </div>
        <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5">
          <button
            onClick={() => setView('tissues')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors font-medium ${
              view === 'tissues' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Tissues ({tissues.length})
          </button>
          <button
            onClick={() => setView('exercises')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors font-medium ${
              view === 'exercises' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Exercises ({exercises.length})
          </button>
        </div>
      </div>

      {modelSummary && (
        <div className="flex flex-wrap gap-3">
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-1.5">
            <span className="text-xs font-medium text-red-600">{modelSummary.overview.at_risk_count} at risk</span>
          </div>
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5">
            <span className="text-xs font-medium text-emerald-600">{modelSummary.overview.recovering_count} recovering</span>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5">
            <span className="text-xs font-medium text-gray-600">{modelSummary.overview.tracked_tissues} tracked</span>
          </div>
        </div>
      )}

      {view === 'tissues' && (
        <div className="space-y-4">
          <TissueView
            tissues={tissuesWithExercises}
            exercises={exercises}
            readinessMap={readinessMap}
            trackedReadinessByTissue={trackedReadinessByTissue}
            rehabProtocols={rehabProtocols}
            modelTissues={modelSummary?.tissues ?? []}
            regions={regions}
            onSave={load}
          />
        </div>
      )}

      {view === 'exercises' && (
        <div className="space-y-4">
          <ExerciseView
            exercises={exercises}
            tissues={tissues}
            exerciseInsights={modelSummary?.exercises ?? []}
            regions={regions}
            onSave={load}
          />
        </div>
      )}
    </div>
  )
}
