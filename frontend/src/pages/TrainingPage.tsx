import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import SymptomSeverityRow from '../components/SymptomSeverityRow'
import {
  symptomDbToSeverity,
  symptomSeverityToDb,
  type SymptomSeverityLevel,
} from '../components/symptomSeverity'
import WorkoutSetEditor from '../components/WorkoutSetEditor'
import StrengthPlannerCard, { type SelectedExercise } from '../components/StrengthPlannerCard'
import {
  createRecoveryCheckIn,
  deletePlan,
  getActivePlan,
  getPlannerToday,
  getRecoveryCheckInTargets,
  savePlan,
  startPlan,
  completePlan,
  type PlannerExercisePrescription,
  type PlannerGroupBrief,
  type PlannerTodayResponse,
  type RecoveryCheckIn,
  type RecoveryCheckInTarget,
  type RecoveryCheckInTargetsResponse,
  type SavedPlan,
} from '../api'
import {
  formatSchemeHistorySummary,
  repSchemeColor,
  repSchemeLabel,
} from '../lib/workoutSchemes'
import { regionLabel } from '../lib/regions'

function today() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function pluralize(count: number, singular: string, plural = `${singular}s`) {
  return count === 1 ? singular : plural
}

function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-gray-200 ${className}`} />
}

function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-3 rounded-2xl border border-gray-200 bg-white p-5">
      <Skeleton className="h-5 w-32" />
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton key={index} className={`h-4 ${index === lines - 1 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

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
  const [severity, setSeverity] = useState<SymptomSeverityLevel>(0)
  const [saving, setSaving] = useState(false)
  const [expanded, setExpanded] = useState(true)

  const allTargets = useMemo(() => {
    const byKey = new Map<string, RecoveryCheckInTarget>()
    for (const target of checkInData.targets) byKey.set(target.target_key, target)
    for (const target of checkInData.other_options.pain_tracked_tissues) byKey.set(target.target_key, target)
    for (const target of checkInData.other_options.soreness_regions) byKey.set(target.target_key, target)
    return byKey
  }, [checkInData])

  const checkInByTarget = useMemo(
    () => Object.fromEntries(checkInData.today_check_ins.map(checkIn => [checkIn.target_key, checkIn])),
    [checkInData.today_check_ins],
  )

  const selectedTarget = selectedKey ? allTargets.get(selectedKey) ?? null : null
  const savedKeys = useMemo(
    () => new Set(checkInData.today_check_ins.map(checkIn => checkIn.target_key)),
    [checkInData.today_check_ins],
  )
  const painWorkflowTargets = checkInData.pain_targets
  const sorenessWorkflowTargets = checkInData.soreness_targets
  const workflowTargets = useMemo(
    () => [...checkInData.pain_targets, ...checkInData.soreness_targets],
    [checkInData.pain_targets, checkInData.soreness_targets],
  )
  const workflowDoneCount = useMemo(
    () => workflowTargets.filter(target => savedKeys.has(target.target_key)).length,
    [savedKeys, workflowTargets],
  )
  const workflowComplete = workflowTargets.length > 0 && workflowDoneCount >= workflowTargets.length
  const collapsedSummary = useMemo(
    () => checkInData.today_check_ins.slice(0, 3).map(checkIn =>
      `${checkIn.target_label} (${checkIn.check_in_kind === 'pain'
        ? `pain ${checkIn.pain_0_10}/10`
        : `soreness ${checkIn.soreness_0_10}/10`})`,
    ),
    [checkInData.today_check_ins],
  )
  const prevWorkflowComplete = useRef(workflowComplete)

  useEffect(() => {
    if (workflowComplete && !prevWorkflowComplete.current) {
      setExpanded(false)
    }
    prevWorkflowComplete.current = workflowComplete
  }, [workflowComplete])

  useEffect(() => {
    if (!selectedTarget) {
      setSeverity(0)
      return
    }
    const checkIn = checkInByTarget[selectedTarget.target_key] ?? selectedTarget.existing_check_in ?? null
    if (checkIn) {
      setSeverity(symptomDbToSeverity(
        checkIn.check_in_kind === 'pain' ? checkIn.pain_0_10 : checkIn.soreness_0_10,
      ))
      return
    }
    setSeverity(0)
  }, [checkInByTarget, selectedTarget])

  const submit = async () => {
    if (!selectedTarget) return
    setSaving(true)
    try {
      if (selectedTarget.check_in_kind === 'pain') {
        await createRecoveryCheckIn({
          date: today(),
          tracked_tissue_id: selectedTarget.tracked_tissue_id ?? undefined,
          pain_0_10: symptomSeverityToDb(severity),
        })
      } else {
        await createRecoveryCheckIn({
          date: today(),
          region: selectedTarget.region,
          soreness_0_10: symptomSeverityToDb(severity),
        })
      }
      setSelectedKey(null)
      setOtherKey('')
      setShowOther(false)
      setSeverity(0)
      onSubmit()
    } finally {
      setSaving(false)
    }
  }

  const checkInSummary = (checkIn: RecoveryCheckIn) =>
    checkIn.check_in_kind === 'pain'
      ? `Pain ${checkIn.pain_0_10}/10`
      : `Soreness ${checkIn.soreness_0_10}/10`

  const renderCheckInEditor = (target: RecoveryCheckInTarget, currentCheckIn: RecoveryCheckIn | null) => (
    <div className="mt-3 space-y-3 rounded-xl border border-gray-200 bg-white/80 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-gray-700">{target.target_label}</p>
          <p className="mt-0.5 text-[11px] text-gray-500">
            {target.target_kind === 'tracked_tissue'
              ? `${regionLabel(target.region)} - specific tissue`
              : `${regionLabel(target.region)} - region`}
          </p>
          <p className="mt-2 text-[11px] text-gray-500">
            {target.check_in_kind === 'pain'
              ? 'Rate pain only. This directly protects today\'s exercise choices for the tracked tissue.'
              : 'Rate soreness only. This feeds recovery learning and ranking without directly hard-blocking exercises.'}
          </p>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
          target.check_in_kind === 'pain'
            ? 'bg-rose-100 text-rose-700'
            : 'bg-sky-100 text-sky-700'
        }`}>
          {target.check_in_kind === 'pain' ? 'Pain check-in' : 'Soreness check-in'}
        </span>
      </div>

      {target.reasons && target.reasons.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {target.reasons.map(reason => (
            <span key={reason.code} className="rounded-full border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] text-gray-600">
              {reason.label}
            </span>
          ))}
        </div>
      )}

      {currentCheckIn && (
        <p className="text-[11px] text-gray-500">
          Already checked in today ({checkInSummary(currentCheckIn)}). Update it if anything changed.
        </p>
      )}

      <SymptomSeverityRow
        label={target.check_in_kind === 'pain' ? 'Pain' : 'Soreness'}
        value={severity}
        onChange={setSeverity}
        showDescription={false}
      />

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => {
            setSelectedKey(null)
            setOtherKey('')
          }}
          className="flex-1 rounded-lg border border-gray-200 bg-white py-2 text-sm font-medium text-gray-700 transition-colors hover:border-gray-300"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={saving}
          className="flex-1 rounded-lg bg-gray-900 py-2 text-sm font-medium text-white transition-colors hover:bg-gray-800 disabled:opacity-50"
        >
          {saving ? 'Saving...' : currentCheckIn ? 'Update' : 'Save'}
        </button>
      </div>
    </div>
  )

  const renderTargetList = (
    title: string,
    description: string,
    targets: RecoveryCheckInTarget[],
    emptyMessage: string,
  ) => (
    <div className="space-y-2">
      <div className="px-1">
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-700">{title}</h4>
          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-600">
            {targets.filter(target => savedKeys.has(target.target_key)).length}/{targets.length || 0}
          </span>
        </div>
        <p className="mt-1 text-[11px] text-gray-500">{description}</p>
      </div>

      {targets.length > 0 ? (
        targets.map(target => {
          const done = savedKeys.has(target.target_key)
          const active = selectedTarget?.target_key === target.target_key
          const currentCheckIn = checkInByTarget[target.target_key] ?? target.existing_check_in ?? null
          return (
            <div
              key={target.target_key}
              className={`rounded-xl border p-3 transition-all ${
                active
                  ? 'border-gray-900 bg-gray-900 text-white shadow-sm'
                  : done
                    ? 'border-emerald-200 bg-emerald-50 text-gray-900'
                    : 'border-gray-200 bg-white text-gray-900'
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  setOtherKey('')
                  setSelectedKey(active ? null : target.target_key)
                }}
                className="w-full text-left"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-medium">{target.target_label}</p>
                      <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                        active
                          ? 'bg-white/15 text-white'
                          : target.check_in_kind === 'pain'
                            ? 'bg-rose-100 text-rose-700'
                            : 'bg-sky-100 text-sky-700'
                      }`}>
                        {target.check_in_kind === 'pain' ? 'pain' : 'soreness'}
                      </span>
                    </div>
                    <p className={`mt-0.5 text-[11px] ${active ? 'text-gray-300' : 'text-gray-500'}`}>
                      {target.target_kind === 'tracked_tissue'
                        ? `${regionLabel(target.region)} - specific tissue`
                        : `${regionLabel(target.region)} - region`}
                    </p>
                  </div>
                  {done && (
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      active ? 'bg-white/15 text-white' : 'bg-emerald-100 text-emerald-700'
                    }`}>
                      Checked in
                    </span>
                  )}
                </div>

                {target.reasons && target.reasons.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {target.reasons.map(reason => (
                      <span
                        key={reason.code}
                        className={`rounded-full border px-1.5 py-0.5 text-[10px] ${
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
                  <p className={`mt-2 text-[11px] ${active ? 'text-gray-200' : 'text-gray-500'}`}>
                    {checkInSummary(currentCheckIn)}
                  </p>
                )}
              </button>
              {active && renderCheckInEditor(target, currentCheckIn)}
            </div>
          )
        })
      ) : (
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 px-3 py-2 text-xs text-gray-500">
          {emptyMessage}
        </div>
      )}
    </div>
  )

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-900">Tissue Check-In</h3>
            {workflowComplete && (
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                complete
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-gray-500">
            {expanded
              ? 'Start with pain-only check-ins for injured or rehabbing tissues, then log soreness for regions that were worked recently or stayed sore.'
              : workflowComplete
                ? 'All queued pain and soreness check-ins are logged for today.'
                : 'Re-open to review or adjust today\'s training check-ins.'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-500">
            {workflowTargets.length > 0 ? `${workflowDoneCount}/${workflowTargets.length} done` : `${checkInData.today_check_ins.length} logged`}
          </span>
          <button
            type="button"
            onClick={() => setExpanded(current => !current)}
            className="rounded-lg border border-gray-200 px-2.5 py-1 text-[11px] font-medium text-gray-600 transition-colors hover:border-gray-300 hover:text-gray-800"
          >
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        </div>
      </div>

      {!expanded && (
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 px-3 py-3 text-xs text-gray-500">
          {collapsedSummary.length > 0
            ? `Checked in: ${collapsedSummary.join(' - ')}${checkInData.today_check_ins.length > collapsedSummary.length ? '...' : ''}`
            : 'No check-ins logged yet.'}
        </div>
      )}

      {expanded && (
        <>
          {workflowTargets.length > 0 ? (
            <div className="mb-4 space-y-4">
              {renderTargetList(
                'Protected tissues',
                'These pain-only check-ins control protection and planner gating for injured or rehabbing tissues.',
                painWorkflowTargets,
                'No injured or rehabbing tissues need a dedicated pain check-in right now.',
              )}
              {renderTargetList(
                'Recovery regions',
                'These soreness-only check-ins help the model learn how recent load translated into recovery time.',
                sorenessWorkflowTargets,
                'No recent muscle groups need a soreness check-in right now.',
              )}
            </div>
          ) : (
            <div className="mb-4 rounded-xl border border-gray-100 bg-gray-50/70 px-3 py-2 text-xs text-gray-500">
              Nothing is queued for today. Use <span className="font-medium text-gray-700">Other</span> if something still feels off or deserves recovery tracking.
            </div>
          )}

          <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-medium text-gray-700">Something else worth tracking?</p>
                <p className="mt-0.5 text-[11px] text-gray-500">Add another tracked tissue pain check-in or another recovery region without checking in on everything.</p>
              </div>
              <button
                type="button"
                onClick={() => setShowOther(current => !current)}
                className={`rounded-lg border px-2.5 py-1 text-xs transition-all ${
                  showOther
                    ? 'border-gray-900 bg-gray-900 text-white'
                    : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                }`}
              >
                Other
              </button>
            </div>

            {showOther && (
              <div className="mt-3">
                {checkInData.other_options.pain_tracked_tissues.length > 0 || checkInData.other_options.soreness_regions.length > 0 ? (
                  <select
                    className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700"
                    value={otherKey}
                    onChange={event => {
                      const nextKey = event.target.value
                      setOtherKey(nextKey)
                      setSelectedKey(nextKey || null)
                    }}
                  >
                    <option value="">Select another area...</option>
                    {checkInData.other_options.pain_tracked_tissues.length > 0 && (
                      <optgroup label="Pain check-ins">
                        {checkInData.other_options.pain_tracked_tissues.map(target => (
                          <option key={target.target_key} value={target.target_key}>
                            {target.target_label}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {checkInData.other_options.soreness_regions.length > 0 && (
                      <optgroup label="Soreness regions">
                        {checkInData.other_options.soreness_regions.map(target => (
                          <option key={target.target_key} value={target.target_key}>
                            {target.target_label}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                ) : (
                  <p className="text-[11px] text-gray-500">All available tracked tissues and regions are already on today's list.</p>
                )}
              </div>
            )}

            {showOther && selectedTarget && !workflowTargets.some(target => target.target_key === selectedTarget.target_key) && (
              renderCheckInEditor(
                selectedTarget,
                checkInByTarget[selectedTarget.target_key] ?? selectedTarget.existing_check_in ?? null,
              )
            )}
          </div>
        </>
      )}
    </div>
  )
}

const workflowRoleColor = (role?: string | null) =>
  role === 'rehab' ? 'bg-purple-100 text-purple-700'
    : role === 'accessory' ? 'bg-amber-100 text-amber-700'
      : 'bg-gray-100 text-gray-700'

const workflowRoleLabel = (role?: string | null) =>
  role === 'rehab' ? 'rehab'
    : role === 'accessory' ? 'accessory'
      : 'group'

const plannerStatusTone = (status?: string) =>
  status === 'ready' ? 'bg-emerald-100 text-emerald-700'
    : status === 'overworked' ? 'bg-amber-100 text-amber-700'
      : 'bg-red-100 text-red-700'

const plannerStatusLabel = (status?: string) =>
  status === 'ready' ? 'ready'
    : status === 'overworked' ? 'overworked'
      : 'blocked'

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
  const groupLabels = useMemo(
    () => Array.from(new Set(plan.exercises.map(exercise => exercise.group_label).filter(Boolean))),
    [plan.exercises],
  )

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-900">Today's Workout</h3>
            <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
              isCompleted ? 'bg-emerald-50 text-emerald-700'
                : isStarted ? 'bg-blue-50 text-blue-700'
                  : 'bg-gray-100 text-gray-600'
            }`}>
              {plan.status}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-gray-500">
            {plan.day_label === 'Mixed Training' ? 'Multi-group session' : plan.day_label}
          </p>
        </div>
        {!isCompleted && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={cancelling}
            className="text-[10px] text-red-400 transition-colors hover:text-red-600 disabled:opacity-50"
          >
            {cancelling ? 'cancelling...' : 'cancel workout'}
          </button>
        )}
      </div>

      {groupLabels.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {groupLabels.map(label => (
            <span key={label} className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-600">
              {label}
            </span>
          ))}
        </div>
      )}

      {plan.target_regions.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {plan.target_regions.map(region => (
            <span key={region} className="rounded-full bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
              {regionLabel(region)}
            </span>
          ))}
        </div>
      )}

      {!isStarted && (
        <>
          <WorkoutSetEditor
            mode="plan"
            planExercises={plan.exercises}
            onPlanChanged={onRefresh}
            asOf={today()}
          />
          <button
            type="button"
            onClick={handleStart}
            disabled={starting || plan.exercises.length === 0}
            className="mt-3 w-full rounded-xl bg-gray-900 py-2 text-xs font-medium text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
          >
            {starting ? 'Starting...' : 'Start Workout'}
          </button>
        </>
      )}

      {isStarted && !isCompleted && plan.workout_session_id && (
        <>
          <WorkoutSetEditor
            mode="log"
            sessionId={plan.workout_session_id}
            onSessionChanged={onRefresh}
          />
          <button
            type="button"
            onClick={handleComplete}
            disabled={completing}
            className="mt-3 w-full rounded-xl bg-emerald-600 py-2 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-40"
          >
            {completing ? 'Completing...' : 'Complete Workout'}
          </button>
        </>
      )}

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

function PlanExerciseList({
  exercises,
  checkedIds,
  onToggle,
  readOnly = false,
}: {
  exercises: PlannerExercisePrescription[]
  checkedIds?: Set<number>
  onToggle?: (exerciseId: number) => void
  readOnly?: boolean
}) {
  if (exercises.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 p-4 text-center">
        <p className="text-xs text-gray-500">No matching exercises are queued for this category.</p>
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {exercises.map(exercise => {
        const checked = checkedIds?.has(exercise.exercise_id) ?? exercise.selected !== false
        const selectable = exercise.selectable !== false
        const schemeHistorySummary = formatSchemeHistorySummary(exercise.scheme_history)
        const className = `w-full rounded-xl border p-3 text-left transition-all ${
          selectable
            ? checked
              ? 'border-gray-300 bg-white'
              : 'border-gray-200 bg-gray-50/60 hover:border-gray-300'
            : 'cursor-default border-red-100 bg-red-50/60 opacity-80'
        }`

        const content = (
          <div className="flex items-start gap-2.5">
            {!readOnly && (
              <div className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border-2 transition-colors ${
                checked && selectable ? 'border-gray-900 bg-gray-900' : selectable ? 'border-gray-300 bg-white' : 'border-red-200 bg-white'
              }`}>
                {checked && selectable && (
                  <svg className="h-2.5 w-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </div>
            )}

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-sm font-medium text-gray-900">{exercise.exercise_name}</span>
                <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${plannerStatusTone(exercise.planner_status)}`}>
                  {plannerStatusLabel(exercise.planner_status)}
                </span>
                {exercise.rep_scheme && selectable && (
                  <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${repSchemeColor(exercise.rep_scheme)}`}>
                    {repSchemeLabel(exercise.rep_scheme)}
                  </span>
                )}
                <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${workflowRoleColor(exercise.workflow_role)}`}>
                  {workflowRoleLabel(exercise.workflow_role)}
                </span>
                {exercise.ready_tomorrow && (
                  <span className="rounded-full bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-700">
                    ready tomorrow
                  </span>
                )}
                {exercise.performed_side && exercise.performed_side !== 'bilateral' && (
                  <span className="rounded-full bg-purple-100 px-1.5 py-0.5 text-[10px] font-medium text-purple-700">
                    {exercise.performed_side}
                  </span>
                )}
              </div>

              {selectable ? (
                <div className="mt-0.5 text-[11px] font-medium text-gray-600">
                  {exercise.target_sets} x {exercise.target_reps}
                  {exercise.target_weight != null && <> @ <span className="text-gray-900">{exercise.target_weight} lb</span></>}
                </div>
              ) : (
                <div className="mt-0.5 text-[11px] font-medium text-red-700">Unavailable today</div>
              )}

              {exercise.days_since_last != null && (
                <div className="mt-0.5 text-[10px] text-gray-500">
                  Freshness: {exercise.days_since_last.toFixed(1)} weighted days
                  {exercise.readiness_score != null && ` - readiness ${Math.round(exercise.readiness_score * 100)}%`}
                </div>
              )}

              {exercise.planner_reason && (
                <div className={`mt-0.5 text-[10px] ${
                  exercise.planner_status === 'blocked'
                    ? 'text-red-600'
                    : exercise.planner_status === 'overworked'
                      ? 'text-amber-600'
                      : 'text-emerald-600'
                }`}>
                  {exercise.planner_reason}
                </div>
              )}

              {exercise.ready_tomorrow_reason && (
                <div className="mt-0.5 text-[10px] text-sky-600">{exercise.ready_tomorrow_reason}</div>
              )}

              {exercise.side_explanation && (
                <div className="mt-0.5 text-[10px] text-purple-600">{exercise.side_explanation}</div>
              )}
              {exercise.selection_note && (
                <div className="mt-0.5 text-[10px] text-blue-600">{exercise.selection_note}</div>
              )}
              {exercise.weight_adjustment_note && (
                <div className="mt-0.5 text-[10px] text-orange-600">{exercise.weight_adjustment_note}</div>
              )}
              {exercise.overload_note && (
                <div className="mt-0.5 text-[10px] text-amber-600">{exercise.overload_note}</div>
              )}
              {schemeHistorySummary && (
                <div className="mt-0.5 text-[10px] text-gray-500">Recent: {schemeHistorySummary}</div>
              )}
            </div>
          </div>
        )

        if (readOnly || !selectable) {
          return (
            <div key={exercise.exercise_id} className={className}>
              {content}
            </div>
          )
        }

        return (
          <button
            key={exercise.exercise_id}
            type="button"
            onClick={() => onToggle?.(exercise.exercise_id)}
            className={className}
          >
            {content}
          </button>
        )
      })}
    </div>
  )
}

function PlannerGroupCard({
  group,
  checkedIds,
  onToggle,
  readOnly,
}: {
  group: PlannerGroupBrief
  checkedIds: Set<number>
  onToggle: (exerciseId: number) => void
  readOnly: boolean
}) {
  const selectedCount = group.exercises.filter(exercise => checkedIds.has(exercise.exercise_id)).length

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold text-gray-900">{group.day_label}</h4>
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-700">
              {group.available_count}/{group.exercise_count} selectable
            </span>
            {group.ready_tomorrow_count > 0 && (
              <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-medium text-sky-700">
                {group.ready_tomorrow_count} {pluralize(group.ready_tomorrow_count, 'option')} ready tomorrow
              </span>
            )}
          </div>
          <p className="mt-1 text-[11px] text-gray-500">{group.rationale}</p>
        </div>
        <div className="text-right">
          <p className="text-xs text-gray-500">{Math.round(group.readiness_score * 100)}% ready</p>
          <p className="mt-0.5 text-[11px] text-gray-400">{group.days_since_last.toFixed(1)} weighted days</p>
          <p className="mt-0.5 text-[11px] text-gray-400">{selectedCount} selected</p>
        </div>
      </div>

      {group.target_regions.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {group.target_regions.map(region => (
            <span key={region} className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-600">
              {regionLabel(region)}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3">
        <PlanExerciseList
          exercises={group.exercises}
          checkedIds={checkedIds}
          onToggle={onToggle}
          readOnly={readOnly}
        />
      </div>
    </div>
  )
}

function PlannerCard({
  planner,
  onRefresh,
  onSave,
  collapseWhenPlanned,
}: {
  planner: PlannerTodayResponse
  onRefresh?: () => void
  onSave?: (dayLabel: string, regions: string[], exercises: PlannerExercisePrescription[]) => void
  collapseWhenPlanned: boolean
}) {
  const groups = planner.groups
  const readOnly = !onSave
  const defaultCheckedIds = useMemo(
    () => new Set(
      groups.flatMap(group =>
        group.exercises
          .filter(exercise => exercise.selected !== false && exercise.selectable !== false)
          .map(exercise => exercise.exercise_id),
      ),
    ),
    [groups],
  )
  const [checkedIds, setCheckedIds] = useState<Set<number>>(defaultCheckedIds)
  const [expanded, setExpanded] = useState(true)
  const prevCollapseWhenPlanned = useRef(collapseWhenPlanned)

  useEffect(() => {
    setCheckedIds(defaultCheckedIds)
  }, [defaultCheckedIds])

  useEffect(() => {
    if (!collapseWhenPlanned) {
      setExpanded(true)
    } else if (!prevCollapseWhenPlanned.current) {
      setExpanded(false)
    }
    prevCollapseWhenPlanned.current = collapseWhenPlanned
  }, [collapseWhenPlanned])

  const toggleExercise = (exerciseId: number) => {
    setCheckedIds(current => {
      const next = new Set(current)
      if (next.has(exerciseId)) next.delete(exerciseId)
      else next.add(exerciseId)
      return next
    })
  }

  const selectedExercises = useMemo(
    () => groups.flatMap(group => group.exercises.filter(exercise => checkedIds.has(exercise.exercise_id))),
    [checkedIds, groups],
  )
  const selectedGroupLabels = useMemo(
    () => Array.from(new Set(
      groups
        .filter(group => group.exercises.some(exercise => checkedIds.has(exercise.exercise_id)))
        .map(group => group.day_label),
    )),
    [checkedIds, groups],
  )
  const selectedRegions = useMemo(
    () => Array.from(new Set(
      groups
        .filter(group => group.exercises.some(exercise => checkedIds.has(exercise.exercise_id)))
        .flatMap(group => group.target_regions),
    )),
    [checkedIds, groups],
  )

  if (groups.length === 0) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">Planner Workflow</h3>
          {onRefresh && (
            <button type="button" onClick={onRefresh} className="text-[10px] text-gray-400 hover:text-gray-600">
              refresh
            </button>
          )}
        </div>
        <p className="text-sm text-gray-500">{planner.message || 'No exercises are available for planning right now.'}</p>
      </div>
    )
  }

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-900">Planner Workflow</h3>
            {collapseWhenPlanned && (
              <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700">
                current workout planned
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-gray-500">
            Ranked categories put the freshest, safest movements first so you can assemble today's session across multiple groups.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-lg bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
            {groups.length} {pluralize(groups.length, 'category', 'categories')}
          </span>
          {onRefresh && (
            <button type="button" onClick={onRefresh} className="text-[10px] text-gray-400 hover:text-gray-600">
              refresh
            </button>
          )}
          <button
            type="button"
            onClick={() => setExpanded(current => !current)}
            className="rounded-lg border border-gray-200 px-2.5 py-1 text-[11px] font-medium text-gray-600 transition-colors hover:border-gray-300 hover:text-gray-800"
          >
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        </div>
      </div>

      {planner.filtered_tissues.length > 0 && (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-3">
          <p className="text-xs font-medium text-amber-800">Filtered out of general loading today</p>
          <p className="mt-1 text-[11px] text-amber-700">
            {planner.filtered_tissues.map(tissue => `${tissue.target_label} (${tissue.reason})`).join(' - ')}
          </p>
        </div>
      )}

      <div className="mt-4 rounded-xl border border-gray-100 bg-gray-50/70 px-3 py-3 text-xs text-gray-600">
        {selectedExercises.length > 0
          ? `${selectedExercises.length} ${pluralize(selectedExercises.length, 'movement')} selected across ${selectedGroupLabels.length} ${pluralize(selectedGroupLabels.length, 'category', 'categories')}.`
          : 'No movements selected yet.'}
        {selectedGroupLabels.length > 0 && ` ${selectedGroupLabels.join(' - ')}`}
      </div>

      {!expanded && (
        <div className="mt-3 text-xs text-gray-500">
          {collapseWhenPlanned
            ? 'Re-open the planner any time to review ranked alternatives and tomorrow-ready tags.'
            : "Re-open the planner to adjust today's movement selection."}
        </div>
      )}

      {expanded && (
        <>
          <div className="mt-4 space-y-4">
            {groups.map(group => (
              <PlannerGroupCard
                key={group.group_id}
                group={group}
                checkedIds={checkedIds}
                onToggle={toggleExercise}
                readOnly={readOnly}
              />
            ))}
          </div>

          {readOnly ? (
            <p className="mt-4 text-xs text-gray-500">
              Use the workout card above to edit the current session. The ranked planner stays available here for reference.
            </p>
          ) : (
            <>
              <p className={`mt-4 text-xs ${
                selectedExercises.length >= 7 && selectedExercises.length <= 10
                  ? 'text-emerald-600'
                  : 'text-amber-600'
              }`}>
                Aim for about 7-10 movements. You currently have {selectedExercises.length} selected.
              </p>
              <button
                type="button"
                onClick={() => {
                  const dayLabel = selectedGroupLabels.length === 1 ? selectedGroupLabels[0] : 'Mixed Training'
                  onSave?.(dayLabel, selectedRegions, selectedExercises)
                }}
                disabled={selectedExercises.length === 0}
                className="mt-3 w-full rounded-xl bg-gray-900 py-2 text-xs font-medium text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
              >
                Save Today's Plan ({selectedExercises.length} {pluralize(selectedExercises.length, 'movement')})
              </button>
            </>
          )}
        </>
      )}
    </div>
  )
}

export default function TrainingPage() {
  const [checkInData, setCheckInData] = useState<RecoveryCheckInTargetsResponse | null>(null)
  const [quickLoaded, setQuickLoaded] = useState(false)
  const [planner, setPlanner] = useState<PlannerTodayResponse | null>(null)
  const [plannerLoading, setPlannerLoading] = useState(true)
  const [activePlan, setActivePlan] = useState<SavedPlan | null>(null)

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
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getPlannerToday(today()).then(data => {
      if (cancelled) return
      setPlanner(data)
      setPlannerLoading(false)
    }).catch(() => {
      if (!cancelled) {
        setPlanner(null)
        setPlannerLoading(false)
      }
    })
    return () => {
      cancelled = true
    }
  }, [])

  const refreshPlanner = useCallback(() => {
    setPlannerLoading(true)
    getPlannerToday(today()).then(data => {
      setPlanner(data)
      setPlannerLoading(false)
    }).catch(() => {
      setPlanner(null)
      setPlannerLoading(false)
    })
  }, [])

  const refreshActivePlan = useCallback(() => {
    getActivePlan(today()).then(plan => {
      setActivePlan(plan?.status === 'completed' ? null : plan)
    })
  }, [])

  useEffect(() => {
    refreshActivePlan()
  }, [refreshActivePlan])

  const refreshCheckIns = useCallback(() => {
    getRecoveryCheckInTargets(today()).then(setCheckInData).catch(() => {})
    refreshPlanner()
    refreshActivePlan()
  }, [refreshActivePlan, refreshPlanner])

  const handleSavePlan = useCallback((dayLabel: string, regions: string[], exercises: PlannerExercisePrescription[]) => {
    savePlan(dayLabel, regions, exercises, today()).then(() => {
      refreshPlanner()
      refreshActivePlan()
    }).catch(() => {})
  }, [refreshActivePlan, refreshPlanner])

  const handleStrengthSave = useCallback((selected: SelectedExercise[]) => {
    const exercises: PlannerExercisePrescription[] = selected.map(ex => {
      const rx = ex.prescription
      const sets = rx?.sets
      let targetSets = 3
      let targetReps = '8-12'
      let targetWeight: number | null = null

      if (ex.is_bodyweight && rx?.suggestion) {
        targetSets = rx.suggestion.sets
        targetReps = String(rx.suggestion.reps_per_set)
      } else if (sets && sets.length > 0) {
        targetSets = sets.length
        const minReps = Math.min(...sets.map(s => s.acceptable_rep_min))
        const maxReps = Math.max(...sets.map(s => s.acceptable_rep_max))
        targetReps = `${minReps}-${maxReps}`
        targetWeight = sets[0].proposed_weight
      } else if (rx?.fallback_weight != null) {
        targetWeight = rx.fallback_weight
      }

      return {
        exercise_id: ex.exercise_id,
        exercise_name: ex.name,
        equipment: null,
        target_sets: targetSets,
        target_reps: targetReps,
        target_weight: targetWeight,
        rep_scheme: ex.allow_heavy_loading ? 'heavy' : 'medium',
        rationale: 'Strength curve prescription',
        overload_note: null,
        selected: true,
        last_performance: null,
        scheme_history: { heavy: null, medium: null, volume: null },
      } satisfies PlannerExercisePrescription
    })
    savePlan('Strength Session', [], exercises, today()).then(() => {
      refreshPlanner()
      refreshActivePlan()
    }).catch(() => {})
  }, [refreshActivePlan, refreshPlanner])

  return (
    <ScrollablePage>
      <div className="space-y-4 pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-lg font-bold text-gray-900">Training</h1>
          <span className="text-xs tabular-nums text-gray-400">{today()}</span>
        </div>

        {!quickLoaded ? (
          <div className="space-y-4">
            <CardSkeleton lines={5} />
            <CardSkeleton lines={8} />
          </div>
        ) : (
          <>
            {checkInData && (
              <CheckInCard
                checkInData={checkInData}
                onSubmit={refreshCheckIns}
              />
            )}

            {activePlan && (
              <ActivePlanCard
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
            )}

            {/* Strength curve planner — shown when no active plan */}
            {!activePlan && (
              <StrengthPlannerCard
                onSave={handleStrengthSave}
                collapseWhenPlanned={Boolean(activePlan)}
              />
            )}

            {plannerLoading
              ? <CardSkeleton lines={10} />
              : planner && (
                <PlannerCard
                  planner={planner}
                  onRefresh={refreshPlanner}
                  onSave={activePlan ? undefined : handleSavePlan}
                  collapseWhenPlanned={Boolean(activePlan)}
                />
              )}
          </>
        )}
      </div>
    </ScrollablePage>
  )
}
