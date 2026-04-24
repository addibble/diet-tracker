import { request } from './_request';
import type { ExerciseSchemeHistory } from './workouts';

// Helper: append ?as_of=... query param when provided
const plannerQ = (path: string, asOf?: string) =>
  asOf ? `${path}${path.includes('?') ? '&' : '?'}as_of=${asOf}` : path;

export interface SavedPlan {
  id: number;
  date: string;
  status: string;
  day_label: string;
  target_regions: string[];
  workout_session_id: number | null;
  exercises: SavedPlanExercise[];
}

export interface SavedPlanExercise {
  pde_id: number;
  exercise_id: number;
  exercise_name: string;
  equipment: string | null;
  allow_heavy_loading?: boolean;
  heavy_available?: boolean;
  heavy_blocked_reason?: string | null;
  load_input_mode: string;
  set_metric_mode?: string;
  laterality?: 'bilateral' | 'unilateral' | 'either';
  target_sets: number;
  target_rep_min: number | null;
  target_rep_max: number | null;
  rep_scheme: string | null;
  target_weight: number | null;
  performed_side?: 'left' | 'right' | 'center' | 'bilateral' | null;
  side_explanation?: string | null;
  selection_note?: string | null;
  blocked_variant?: string | null;
  protected_tissues?: string[];
  workflow_role?: 'group' | 'rehab' | 'accessory' | null;
  group_label?: string | null;
  scheme_history?: ExerciseSchemeHistory;
  completed_sets: {
    id: number;
    set_order: number;
    performed_side?: 'left' | 'right' | 'center' | 'bilateral' | null;
    reps: number | null;
    weight: number | null;
    duration_secs: number | null;
    distance_steps: number | null;
    started_at: string | null;
    completed_at: string | null;
    rpe: number | null;
    rep_completion: string | null;
    notes: string | null;
  }[];
  sets_done: number;
  done: boolean;
}

export interface ExerciseMenuItem {
  exercise_id: number;
  name: string;
  days_since_trained: number | null;
  allow_heavy_loading: boolean;
  load_input_mode: string;
  set_metric_mode?: string;
  is_bodyweight: boolean;
  recent_rpe_sets: number;
  has_curve_fit: boolean;
  heavy_available?: boolean;
  heavy_blocked_reason?: string | null;
  target_sets?: number;
}

export interface WeeklyExerciseItem extends ExerciseMenuItem {
  group: string;
  confidence: number;
}

export interface GroupMenuEntry {
  name: string;
  available: boolean;
  cooldown_days: number;
  days_since_freshest: number | null;
  exercises: WeeklyExerciseItem[];
}

export interface GroupMenuResponse {
  groups: GroupMenuEntry[];
}

export interface PrescribeNextRequest {
  exercise_id: number;
  prior_sets: { weight: number; reps: number; rpe: number }[];
  actual_weight?: number | null;
  training_mode?: 'heavy' | 'volume';
}

export interface PrescribeNextResponse {
  has_curve: boolean;
  fit_tier?: string;
  n_obs?: number;
  exercise_complete?: boolean;
  inflection_detected?: boolean | null;
  estimated_1rm?: number | null;
  training_mode?: 'heavy' | 'volume';
  /** Unit of the y-axis for this exercise. "reps" | "duration" | "distance". */
  metric_kind?: 'reps' | 'duration' | 'distance';
  /** Short display unit: "reps" | "s" | "steps". */
  display_unit?: string;
  next_set?: {
    set_number: number;
    proposed_weight: number | null;
    effective_weight: number;
    target_reps: number;
    target_rpe: number;
    target_rir: number;
    r_fail: number;
    acceptable_rep_min: number;
    acceptable_rep_max: number;
    // Space-explicit aliases emitted alongside the legacy fields. Prefer
    // these in new code: suffixes make the weight space and rep space
    // unambiguous. See backend/app/units.py for the canonical definitions.
    proposed_entered_weight_lb?: number | null;
    effective_weight_lb?: number;
    target_reps_done?: number;
    r_fail_rtf?: number;
    /** Same number as target_reps, but labeled with the exercise's metric. */
    target_endurance?: number;
    metric_kind?: 'reps' | 'duration' | 'distance';
    display_unit?: string;
  } | null;
  // Fallback / bodyweight
  fallback_weight?: number | null;
  message?: string;
  is_bodyweight?: boolean;
  suggestion?: {
    sets: number;
    reps_per_set: number;
    endurance_per_set?: number;
    metric_kind?: 'reps' | 'duration' | 'distance';
    display_unit?: string;
    notes: string;
  };
  // Curve chart data (tier 1/2)
  curve?: {
    M: number;
    k: number;
    gamma: number;
    fit_tier?: string;
    n_obs?: number;
    max_observed_weight?: number;
    weight_space?: 'entered' | 'effective';
    x_axis_space?: 'entered';
    bw_offset?: number;
    ext_mult?: number;
    metric_kind?: 'reps' | 'duration' | 'distance';
    display_unit?: string;
  } | null;
  // Curve fit over observations strictly before today (for the completed view).
  curve_prior?: {
    M: number;
    k: number;
    gamma: number;
    fit_tier?: string;
    n_obs?: number;
    max_observed_weight?: number;
    weight_space?: 'entered' | 'effective';
    x_axis_space?: 'entered';
    bw_offset?: number;
    ext_mult?: number;
    metric_kind?: 'reps' | 'duration' | 'distance';
    display_unit?: string;
  } | null;
  observations?: {
    weight: number;
    reps: number;
    rir?: number;
    age_days: number;
  }[];
  // Bootstrap scheme hint (when has_curve === false, not bodyweight)
  scheme?: {
    set_number: number;
    target_reps: number;
    target_rir: number;
    r_fail: number;
    acceptable_rep_min: number;
    acceptable_rep_max: number;
  } | null;
}

export interface QuickStartResponse {
  workout_session_id: number;
  exercise_ids: number[];
  exercise_names: string[];
  date: string;
}

export const getActivePlan = (asOf?: string) =>
  request<SavedPlan>(plannerQ('/planner/active', asOf)).catch(() => null);

export const deletePlan = (asOf?: string) =>
  request<void>(plannerQ('/planner/active', asOf), { method: 'DELETE' });

export const completeActivePlan = (asOf?: string) =>
  request<SavedPlan>(plannerQ('/planner/active/complete', asOf), {
    method: 'POST',
  });

export const addPlanExercise = (
  exercises: {
    exercise_id: number; target_sets?: number; target_reps?: string;
    target_weight?: number | null; rep_scheme?: string;
  }[],
  asOf?: string,
) =>
  request<SavedPlan>(plannerQ('/planner/active/exercises', asOf), {
    method: 'POST', body: JSON.stringify({ exercises }),
  });

export const removePlanExercise = (exerciseId: number, asOf?: string) =>
  request<SavedPlan>(plannerQ(`/planner/active/exercises/${exerciseId}`, asOf), {
    method: 'DELETE',
  });

export const reorderPlanExercises = (pdeIds: number[], asOf?: string) =>
  request<SavedPlan>(plannerQ('/planner/active/reorder', asOf), {
    method: 'PATCH', body: JSON.stringify({ pde_ids: pdeIds }),
  });

export const updateProgramDayExercise = (pdeId: number, data: {
  target_sets?: number;
  target_rep_min?: number | null;
  target_rep_max?: number | null;
  target_weight?: number | null;
  rep_scheme?: string | null;
  performed_side?: 'left' | 'right' | 'center' | 'bilateral' | null;
  side_explanation?: string | null;
  sort_order?: number;
}) =>
  request<SavedPlanExercise>(`/program-day-exercises/${pdeId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });

export const getExerciseMenu = (workoutSessionId?: number) => {
  const params = workoutSessionId ? `?workout_session_id=${workoutSessionId}` : '';
  return request<ExerciseMenuItem[]>(`/planner/exercise-menu${params}`);
};

export const getWeeklyMenu = () =>
  request<GroupMenuResponse>('/planner/weekly-menu');

export const prescribeNext = (data: PrescribeNextRequest) =>
  request<PrescribeNextResponse>('/planner/prescribe-next', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export interface CurveSnapshotResponse {
  has_curve: boolean;
  is_bodyweight?: boolean;
  curve?: {
    M: number;
    k: number;
    gamma: number;
    fit_tier?: string;
    n_obs?: number;
    max_observed_weight?: number;
    weight_space?: 'entered' | 'effective';
    x_axis_space?: 'entered';
    bw_offset?: number;
    ext_mult?: number;
  } | null;
  curve_prior?: {
    M: number;
    k: number;
    gamma: number;
    fit_tier?: string;
    n_obs?: number;
    max_observed_weight?: number;
    weight_space?: 'entered' | 'effective';
    x_axis_space?: 'entered';
    bw_offset?: number;
    ext_mult?: number;
  } | null;
  observations?: {
    weight: number;
    reps: number;
    rir?: number;
    age_days: number;
  }[];
}

export const getCurveSnapshot = (exerciseId: number, date: string) =>
  request<CurveSnapshotResponse>(
    `/planner/curve-snapshot/${exerciseId}?date=${encodeURIComponent(date)}`,
  );

export interface FatigueProfileResponse {
  exercise_id: number;
  has_data: boolean;
  is_bodyweight?: boolean;
  session_observations: {
    set_index: number;
    weight: number;
    effective_weight: number;
    reps: number;
    rpe: number;
    rtf: number;
    session_date?: string;
  }[];
  model_prediction: {
    set_index: number;
    weight: number;
    effective_weight: number;
    predicted_rtf: number;
    beta_used: number;
    beta_learned: boolean;
  }[];
  beta_per_set: number[];
  beta_learned_flags: boolean[];
  beta_source: 'learned' | 'fallback';
  n_history_sessions: number;
  curve?: {
    M: number;
    k: number;
    gamma: number;
    weight_space?: 'entered' | 'effective';
    bw_offset?: number;
    ext_mult?: number;
    max_observed_weight?: number;
  } | null;
}

export const getFatigueProfile = (
  exerciseId: number, days: number = 30, sessionDate?: string,
) => {
  const params = new URLSearchParams({ days: String(days) });
  if (sessionDate) params.set('session_date', sessionDate);
  return request<FatigueProfileResponse>(
    `/planner/fatigue-profile/${exerciseId}?${params.toString()}`,
  );
};

export const quickStart = (exerciseIds: number[], date?: string) =>
  request<QuickStartResponse>('/planner/quick-start', {
    method: 'POST',
    body: JSON.stringify({
      exercise_ids: exerciseIds,
      ...(date ? { date } : {}),
    }),
  });
