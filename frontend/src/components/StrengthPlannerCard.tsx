import { useEffect, useMemo, useState } from 'react'
import {
  getWeeklyMenu,
  type ExerciseMenuItem,
  type GroupMenuResponse,
  type WeeklyExerciseItem,
} from '../api'

interface StrengthPlannerCardProps {
  onStart: (exerciseIds: number[], exercises: ExerciseMenuItem[]) => void
  disabled?: boolean
}

export interface SelectedExercise {
  exercise_id: number
  name: string
  allow_heavy_loading: boolean
  is_bodyweight: boolean
  load_input_mode: string
}

const GROUP_COLORS: Record<string, { bg: string; text: string; border: string; badge: string }> = {
  Push:      { bg: 'bg-red-50',    text: 'text-red-700',    border: 'border-red-200',    badge: 'bg-red-100' },
  Pull:      { bg: 'bg-blue-50',   text: 'text-blue-700',   border: 'border-blue-200',   badge: 'bg-blue-100' },
  Legs:      { bg: 'bg-amber-50',  text: 'text-amber-700',  border: 'border-amber-200',  badge: 'bg-amber-100' },
  Shoulders: { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200', badge: 'bg-purple-100' },
  Core:      { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200', badge: 'bg-orange-100' },
}

const MAX_GROUPS = 2

export default function StrengthPlannerCard({
  onStart,
  disabled,
}: StrengthPlannerCardProps) {
  const [menuData, setMenuData] = useState<GroupMenuResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set())
  const [expanded, setExpanded] = useState(!disabled)
  const [starting, setStarting] = useState(false)
  const [selectedGroups, setSelectedGroups] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    getWeeklyMenu().then(data => {
      if (cancelled) return
      setMenuData(data)
      setLoading(false)
    }).catch(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  // Global exercise map for lookups
  const exerciseById = useMemo(() => {
    if (!menuData) return new Map<number, WeeklyExerciseItem>()
    const map = new Map<number, WeeklyExerciseItem>()
    for (const group of menuData.groups) {
      for (const ex of group.exercises) {
        if (!map.has(ex.exercise_id)) map.set(ex.exercise_id, ex)
      }
    }
    return map
  }, [menuData])

  // Groups sorted: available first, then unavailable
  const sortedGroups = useMemo(() => {
    if (!menuData) return []
    return [...menuData.groups].sort((a, b) => {
      if (a.available !== b.available) return a.available ? -1 : 1
      return 0
    })
  }, [menuData])

  const toggleGroup = (groupName: string) => {
    setSelectedGroups(prev => {
      const next = new Set(prev)
      if (next.has(groupName)) {
        next.delete(groupName)
        // Uncheck all exercises in this group
        const groupExIds = new Set(
          menuData?.groups.find(g => g.name === groupName)?.exercises.map(e => e.exercise_id) ?? []
        )
        setCheckedIds(prevIds => {
          const nextIds = new Set(prevIds)
          for (const id of groupExIds) nextIds.delete(id)
          return nextIds
        })
      } else if (next.size < MAX_GROUPS) {
        next.add(groupName)
      }
      return next
    })
  }

  const toggleExercise = (exerciseId: number) => {
    setCheckedIds(prev => {
      const next = new Set(prev)
      if (next.has(exerciseId)) next.delete(exerciseId)
      else next.add(exerciseId)
      return next
    })
  }

  const handleStart = async () => {
    setStarting(true)
    const selectedExercises: ExerciseMenuItem[] = []
    const selectedIds: number[] = []
    for (const id of checkedIds) {
      const ex = exerciseById.get(id)
      if (ex) {
        selectedIds.push(id)
        selectedExercises.push(ex)
      }
    }
    onStart(selectedIds, selectedExercises)
    setStarting(false)
  }

  if (loading) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h3 className="text-sm font-semibold text-gray-900">Strength Planner</h3>
        <div className="mt-3 animate-pulse space-y-2">
          <div className="h-4 w-2/3 rounded bg-gray-200" />
          <div className="h-4 w-full rounded bg-gray-200" />
          <div className="h-4 w-3/4 rounded bg-gray-200" />
        </div>
      </div>
    )
  }

  if (!menuData) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h3 className="text-sm font-semibold text-gray-900">Strength Planner</h3>
        <p className="mt-2 text-sm text-gray-500">
          No exercises available. Add exercises to get started.
        </p>
      </div>
    )
  }

  const availableCount = sortedGroups.filter(g => g.available).length

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-900">Strength Planner</h3>
            {disabled && (
              <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700">
                workout active
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-gray-500">
            {availableCount === 0
              ? 'All groups on cooldown. Rest day!'
              : `${availableCount} group${availableCount !== 1 ? 's' : ''} available — pick up to ${MAX_GROUPS}.`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {checkedIds.size > 0 && (
            <span className="rounded-lg bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
              {checkedIds.size} selected
            </span>
          )}
          <button
            type="button"
            onClick={() => setExpanded(v => !v)}
            className="rounded-lg border border-gray-200 px-2.5 py-1 text-[11px] font-medium text-gray-600 transition-colors hover:border-gray-300 hover:text-gray-800"
          >
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        </div>
      </div>

      {/* Group selector */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {sortedGroups.map(group => {
          const colors = GROUP_COLORS[group.name] || { bg: 'bg-gray-50', text: 'text-gray-600', border: 'border-gray-200', badge: 'bg-gray-100' }
          const isSelected = selectedGroups.has(group.name)
          const canSelect = group.available && (isSelected || selectedGroups.size < MAX_GROUPS)
          return (
            <button
              key={group.name}
              type="button"
              onClick={() => canSelect && toggleGroup(group.name)}
              disabled={!canSelect && !isSelected}
              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-all ${
                isSelected
                  ? `${colors.bg} ${colors.text} ${colors.border} ring-2 ring-offset-1 ring-gray-400`
                  : group.available
                    ? `border-gray-200 bg-white text-gray-700 hover:${colors.border} hover:${colors.bg}`
                    : 'border-gray-100 bg-gray-50 text-gray-300'
              }`}
            >
              <span>{group.name}</span>
              <span className="ml-1.5 text-[10px] font-normal opacity-70">
                {group.days_since_freshest === null
                  ? '—'
                  : group.available
                    ? `${group.days_since_freshest}d`
                    : `${group.cooldown_days - group.days_since_freshest}d left`}
              </span>
            </button>
          )
        })}
      </div>

      {!expanded && selectedGroups.size > 0 && (
        <div className="mt-3 text-xs text-gray-500">
          {disabled
            ? 'Workout in progress. Expand to review exercise list.'
            : `${checkedIds.size} exercises selected. Expand to review.`}
        </div>
      )}

      {expanded && selectedGroups.size > 0 && (
        <>
          {sortedGroups
            .filter(g => selectedGroups.has(g.name))
            .map(group => {
              const colors = GROUP_COLORS[group.name] || { bg: 'bg-gray-50', text: 'text-gray-600', border: 'border-gray-200', badge: 'bg-gray-100' }
              return (
                <div key={group.name} className="mt-4">
                  <h4 className={`mb-2 text-xs font-semibold uppercase tracking-wide ${colors.text}`}>
                    {group.name}
                    <span className="ml-2 text-[10px] font-normal normal-case text-gray-400">
                      {group.exercises.length} exercises
                    </span>
                  </h4>
                  <div className="space-y-1.5">
                    {group.exercises.map(ex => (
                      <ExerciseMenuRow
                        key={ex.exercise_id}
                        item={ex}
                        checked={checkedIds.has(ex.exercise_id)}
                        onToggle={() => toggleExercise(ex.exercise_id)}
                      />
                    ))}
                  </div>
                </div>
              )
            })}

          <div className="mt-4 flex items-center justify-between">
            <p className={`text-xs ${
              checkedIds.size >= 5 && checkedIds.size <= 10
                ? 'text-emerald-600'
                : checkedIds.size > 0
                  ? 'text-amber-600'
                  : 'text-gray-400'
            }`}>
              {checkedIds.size === 0
                ? 'Select exercises to build your workout.'
                : `${checkedIds.size} exercise${checkedIds.size !== 1 ? 's' : ''} selected.`}
            </p>
          </div>

          <button
            type="button"
            onClick={handleStart}
            disabled={checkedIds.size === 0 || starting || disabled}
            className="mt-3 w-full rounded-xl bg-gray-900 py-2.5 text-xs font-medium text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
          >
            {starting
              ? 'Starting...'
              : `Start Workout (${checkedIds.size} exercise${checkedIds.size !== 1 ? 's' : ''})`}
          </button>
        </>
      )}

      {expanded && selectedGroups.size === 0 && (
        <div className="mt-4 rounded-xl border border-gray-100 bg-gray-50/60 p-4 text-center">
          <p className="text-sm font-medium text-gray-500">
            {availableCount === 0 ? 'Rest Day' : 'Select a group to get started'}
          </p>
          <p className="mt-1 text-xs text-gray-400">
            {availableCount === 0
              ? 'All groups are on cooldown. Recovery is part of the plan.'
              : 'Tap an available group above to see exercises.'}
          </p>
        </div>
      )}
    </div>
  )
}

function ExerciseMenuRow({
  item,
  checked,
  onToggle,
}: {
  item: WeeklyExerciseItem
  checked: boolean
  onToggle: () => void
}) {
  const freshnessColor = item.days_since_trained === null
    ? 'text-gray-400'
    : item.days_since_trained >= 5
      ? 'text-emerald-600'
      : item.days_since_trained >= 3
        ? 'text-gray-600'
        : 'text-amber-600'

  return (
    <button
      type="button"
      onClick={onToggle}
      className={`w-full rounded-xl border p-3 text-left transition-all ${
        checked
          ? 'border-gray-300 bg-white shadow-sm'
          : 'border-gray-200 bg-gray-50/60 hover:border-gray-300'
      }`}
    >
      <div className="flex items-start gap-2.5">
        <div className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border-2 transition-colors ${
          checked ? 'border-gray-900 bg-gray-900' : 'border-gray-300 bg-white'
        }`}>
          {checked && (
            <svg className="h-2.5 w-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-gray-900">{item.name}</span>
            {item.allow_heavy_loading && !item.is_bodyweight && (
              <span className="rounded-full bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
                heavy OK
              </span>
            )}
            {item.has_curve_fit && (
              <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                curve fit
              </span>
            )}
            {!item.has_curve_fit && !item.is_bodyweight && (
              <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                no curve
              </span>
            )}
            {item.is_bodyweight && (
              <span className="rounded-full bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-700">
                bodyweight
              </span>
            )}
          </div>

          <div className="mt-0.5 flex flex-wrap items-center gap-3 text-[11px]">
            <span className={freshnessColor}>
              {item.days_since_trained === null
                ? 'never trained'
                : item.days_since_trained === 0
                  ? 'trained today'
                  : `${item.days_since_trained}d ago`}
            </span>
            {item.recent_rpe_sets > 0 && (
              <span className="text-gray-500">
                {item.recent_rpe_sets} RPE set{item.recent_rpe_sets !== 1 ? 's' : ''} (30d)
              </span>
            )}
          </div>
        </div>
      </div>
    </button>
  )
}
