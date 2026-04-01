import { useEffect, useMemo, useState } from 'react'
import {
  createRehabPlan,
  createTissueCondition,
  getTissues,
  getExercises,
  getRehabProtocols,
  getTissueReadiness,
  getTrackedTissueReadiness,
  updateRehabPlan,
  updateExercise,
  type RehabProtocol,
  type TrackedTissueReadiness,
  type WkTissue,
  type WkExercise,
  type WkTissueReadiness,
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

function formatLastWorked(isoDate: string | null, hoursSince: number | null): string {
  if (!isoDate || hoursSince == null) return 'never'
  if (hoursSince < 1) return '<1h ago'
  if (hoursSince < 24) return `${Math.round(hoursSince)}h ago`
  const days = hoursSince / 24
  if (days < 1.5) return '1d ago'
  return `${Math.round(days)}d ago`
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

// ── Tissue View ──

function TissueView({
  tissues,
  exercises,
  readinessMap,
  trackedReadinessByTissue,
  rehabProtocols,
  onSave,
}: {
  tissues: TissueWithExercises[]
  exercises: WkExercise[]
  readinessMap: Map<number, WkTissueReadiness>
  trackedReadinessByTissue: Map<number, TrackedTissueReadiness[]>
  rehabProtocols: RehabProtocol[]
  onSave: () => void
}) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!search) return tissues
    const q = search.toLowerCase()
    return tissues.filter(
      (t) => t.name.toLowerCase().includes(q) || t.display_name.toLowerCase().includes(q),
    )
  }, [tissues, search])

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
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mb-3 text-[10px]">
        {Object.entries(TYPE_BADGE).map(([type, cls]) => (
          <span key={type} className={`px-1.5 py-px rounded font-medium ${cls}`}>{type}</span>
        ))}
        <span className="text-gray-400 ml-2">|</span>
        {Object.entries(CONDITION_COLORS).map(([status, cls]) => (
          <span key={status} className={`px-1.5 py-px rounded font-medium ${cls}`}>{status}</span>
        ))}
        <span className="text-gray-400 ml-2">|</span>
        {Object.entries(ROLE_COLORS).map(([role, cls]) => (
          <span key={role} className={`px-1.5 py-px rounded font-medium ${cls}`}>{role}</span>
        ))}
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100">
        {filtered.length === 0 && (
          <p className="text-sm text-gray-500 p-4">No tissues found.</p>
        )}
        {filtered.map((t) => (
          <div key={t.id} className="px-4 py-2 hover:bg-gray-50/80 transition-colors">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-gray-800">{t.display_name}</span>
              <span className="text-[10px] font-mono text-gray-400">{t.name}</span>
              <span className={`text-[10px] px-1.5 py-px rounded font-medium ${TYPE_BADGE[t.type] ?? 'bg-gray-100 text-gray-600'}`}>
                {t.type}
              </span>
              {(() => {
                const r = readinessMap.get(t.id)
                const cond = r?.condition
                return cond ? (
                  <span className={`text-[10px] px-1.5 py-px rounded font-medium ${CONDITION_COLORS[cond.status] ?? 'bg-gray-100 text-gray-600'}`}>
                    {cond.status}{cond.severity > 0 ? ` (${cond.severity})` : ''}
                  </span>
                ) : null
              })()}
              {(() => {
                const r = readinessMap.get(t.id)
                return (
                  <span className="text-[10px] text-gray-400" title={r?.last_trained ?? 'never'}>
                    ⏱ {formatLastWorked(r?.last_trained ?? null, r?.hours_since ?? null)}
                  </span>
                )
              })()}
              <span className="text-[10px] text-gray-400">{t.recovery_hours}h</span>
              {t.model_config && (
                <span className="text-[10px] text-gray-400">
                  cap {t.model_config.capacity_prior.toFixed(1)} · rec {t.model_config.recovery_tau_days.toFixed(1)}d
                </span>
              )}
            </div>

            {t.exercises.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {t.exercises.map((ex) => {
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
            )}

            {trackedReadinessByTissue.get(t.id)?.length ? (
              <div className="mt-2 grid gap-2">
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
        ))}
      </div>
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
  const [painThreshold, setPainThreshold] = useState(
    String(
      tracked.active_rehab_plan?.pain_monitoring_threshold
      ?? selectedProtocol?.default_pain_monitoring_threshold
      ?? 3,
    ),
  )
  const [nextDayFlare, setNextDayFlare] = useState(
    String(
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
      String(
        tracked.active_rehab_plan?.pain_monitoring_threshold
        ?? nextSelectedProtocol?.default_pain_monitoring_threshold
        ?? 3,
      ),
    )
    setNextDayFlare(
      String(
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
      setPainThreshold(String(protocol.default_pain_monitoring_threshold))
      setNextDayFlare(String(protocol.default_max_next_day_flare))
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
        pain_monitoring_threshold: Number(painThreshold) || 0,
        max_next_day_flare: Number(nextDayFlare) || 0,
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
          <div className="grid gap-2 md:grid-cols-2">
            <label className="grid gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Pain threshold</span>
              <input
                type="number"
                min={0}
                max={10}
                value={painThreshold}
                onChange={(e) => setPainThreshold(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              />
            </label>
            <label className="grid gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-gray-500">Next-day flare max</span>
              <input
                type="number"
                min={0}
                max={10}
                value={nextDayFlare}
                onChange={(e) => setNextDayFlare(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-xs"
              />
            </label>
          </div>
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

// ── Exercise View ──

function ExerciseView({ exercises, onSave }: { exercises: WkExercise[]; onSave: () => void }) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!search) return exercises
    const q = search.toLowerCase()
    return exercises.filter((e) => e.name.toLowerCase().includes(q))
  }, [exercises, search])

  return (
    <div>
      <input
        type="text"
        placeholder="Filter exercises..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full max-w-sm border border-gray-300 rounded-lg px-3 py-1.5 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-blue-400"
      />

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100">
        {filtered.length === 0 && (
          <p className="text-sm text-gray-500 p-4">No exercises found.</p>
        )}
        {filtered.map((ex) => (
          <div key={ex.id} className="px-4 py-2.5 hover:bg-gray-50/80 transition-colors">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-800">{ex.name}</span>
              {ex.equipment && (
                <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-px rounded">
                  {ex.equipment}
                </span>
              )}
            </div>

            {ex.tissues.length > 0 ? (
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
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
              <p className="text-[11px] text-gray-400 mt-0.5 italic">No tissue mappings</p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main Page ──

export default function TissueAdminPage() {
  const [tissues, setTissues] = useState<WkTissue[]>([])
  const [exercises, setExercises] = useState<WkExercise[]>([])
  const [readiness, setReadiness] = useState<WkTissueReadiness[]>([])
  const [trackedReadiness, setTrackedReadiness] = useState<TrackedTissueReadiness[]>([])
  const [rehabProtocols, setRehabProtocols] = useState<RehabProtocol[]>([])
  const [view, setView] = useState<View>('tissues')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      const [t, e, r, tr, protocols] = await Promise.all([
        getTissues(),
        getExercises(),
        getTissueReadiness(),
        getTrackedTissueReadiness(),
        getRehabProtocols(),
      ])
      setTissues(t)
      setExercises(e)
      setReadiness(r)
      setTrackedReadiness(tr)
      setRehabProtocols(protocols)
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
          <h1 className="text-lg font-semibold text-gray-800">Tissue & Exercise Admin</h1>
          <p className="text-sm text-gray-500">
            Use the <span className="font-medium text-gray-700">Tissues</span> view below to manage per-side conditions and rehab plans.
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

      {view === 'tissues' && (
        <TissueView
          tissues={tissuesWithExercises}
          exercises={exercises}
          readinessMap={readinessMap}
          trackedReadinessByTissue={trackedReadinessByTissue}
          rehabProtocols={rehabProtocols}
          onSave={load}
        />
      )}

      {view === 'exercises' && (
        <ExerciseView exercises={exercises} onSave={load} />
      )}
    </div>
  )
}
