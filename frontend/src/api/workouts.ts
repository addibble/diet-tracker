import { request } from './_request';

export interface WkExerciseLoadPreview {
  sample_input_weight: number | null;
  sample_bodyweight: number;
  bodyweight_component: number;
  effective_weight: number;
  set_metric_mode: string;
  external_load_multiplier: number;
}

export interface WkExerciseMappingWarning {
  code: string;
  message: string;
  source_tissue_id: number;
  target_tissue_id: number;
  suggested_mapping?: {
    role: string;
    loading_factor: number;
    routing_factor: number;
    fatigue_factor: number;
    joint_strain_factor: number;
    tendon_strain_factor: number;
    laterality_mode: 'bilateral_equal' | 'selected_side_only' | 'selected_side_primary' | 'contralateral_carryover';
  } | null;
}

export interface WkExerciseTissueMapping {
  tissue_id: number;
  tissue_name: string;
  tissue_display_name: string;
  tissue_type: string;
  role: string;
  loading_factor: number;
  routing_factor: number;
  fatigue_factor: number;
  joint_strain_factor: number;
  tendon_strain_factor: number;
  laterality_mode: 'bilateral_equal' | 'selected_side_only' | 'selected_side_primary' | 'contralateral_carryover';
}

export interface WkExercise {
  id: number;
  name: string;
  equipment: string | null;
  allow_heavy_loading: boolean;
  load_input_mode: string;
  laterality: 'bilateral' | 'unilateral' | 'either';
  bodyweight_fraction: number;
  external_load_multiplier: number;
  variant_group: string | null;
  grip_style: string;
  grip_width: string;
  support_style: string;
  set_metric_mode: string;
  estimated_minutes_per_set: number;
  load_preview: WkExerciseLoadPreview;
  notes: string | null;
  created_at?: string;
  tissues: WkExerciseTissueMapping[];
  mapping_warnings: WkExerciseMappingWarning[];
}

export type RepScheme = 'heavy' | 'medium' | 'volume';

export interface ExerciseHistorySet {
  set_order: number;
  reps: number | null;
  weight: number | null;
  duration_secs?: number | null;
  distance_steps?: number | null;
  rpe?: number | null;
  rep_completion: string | null;
  notes?: string | null;
}

export interface ExerciseSchemeHistoryEntry {
  date: string;
  rep_scheme: RepScheme;
  sets: ExerciseHistorySet[];
  max_weight: number;
  total_volume: number;
}

export interface ExerciseSchemeHistory {
  heavy: ExerciseSchemeHistoryEntry | null;
  medium: ExerciseSchemeHistoryEntry | null;
  volume: ExerciseSchemeHistoryEntry | null;
}

export interface WkSetDetail {
  id: number;
  session_id?: number;
  exercise_id: number;
  exercise_name: string;
  /**
   * Unit of ``endurance_value`` for this set's exercise. Drives label
   * formatting ("12 reps" vs "45s" vs "24 steps").
   */
  set_metric_mode?: 'reps' | 'duration' | 'distance';
  set_order: number;
  performed_side: 'left' | 'right' | 'center' | 'bilateral' | null;
  reps: number | null;
  weight: number | null;
  duration_secs: number | null;
  distance_steps: number | null;
  /** Unified y-axis quantity — unit determined by ``set_metric_mode``. */
  endurance_value: number | null;
  started_at: string | null;
  completed_at: string | null;
  rpe: number | null;
  rep_completion: string | null;
  notes: string | null;
  training_mode: 'heavy' | 'volume' | 'burnout' | null;
  scheme_history?: ExerciseSchemeHistory;
}

export interface WkSession {
  id: number;
  date: string;
  started_at: string | null;
  finished_at: string | null;
  notes: string | null;
  created_at: string;
  sets: WkSetDetail[];
  effective_volume?: number;
  readiness_beta?: number | null;
  readiness_label?: 'strong' | 'above_baseline' | 'baseline' | 'below_baseline' | 'fatigued' | null;
  readiness_pct?: number | null;
  readiness_clamped?: boolean;
}

export interface WkTissueModelConfig {
  capacity_prior: number;
  recovery_tau_days: number;
  fatigue_tau_days: number;
  collapse_drop_threshold: number;
  ramp_sensitivity: number;
  risk_sensitivity: number;
}

export interface WkTissue {
  id: number;
  name: string;
  display_name: string;
  type: string;
  tracking_mode?: 'paired' | 'center';
  region?: string;
  recovery_hours: number;
  notes?: string | null;
  model_config?: WkTissueModelConfig | null;
  tracked_tissues?: {
    id: number;
    side: 'left' | 'right' | 'center';
    display_name: string;
    active: boolean;
  }[];
}

export interface WkExerciseHistory {
  exercise: WkExercise;
  scheme_history: ExerciseSchemeHistory;
  sessions: {
    date: string;
    rep_scheme: RepScheme;
    sets: ExerciseHistorySet[];
    max_weight: number;
    total_volume: number;
    rep_completions: string[];
  }[];
}

export interface WorkoutSetUpdateInput {
  performed_side?: 'left' | 'right' | 'center' | 'bilateral' | null;
  reps?: number | null;
  weight?: number | null;
  duration_secs?: number | null;
  distance_steps?: number | null;
  /**
   * Unified y-axis quantity (reps/secs/steps, per the exercise's
   * ``set_metric_mode``). Send this directly in new code; the legacy
   * per-mode fields are still accepted for backward compat.
   */
  endurance_value?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  rpe?: number | null;
  rir?: number | null;
  rep_completion?: string | null;
  notes?: string | null;
  training_mode?: 'heavy' | 'volume' | 'burnout' | null;
}

export interface WorkoutSetCreateInput extends WorkoutSetUpdateInput {
  exercise_id: number;
  set_order?: number;
}

export const getExercises = (search?: string) =>
  request<WkExercise[]>(`/exercises${search ? `?search=${encodeURIComponent(search)}` : ''}`);

export const updateExercise = (id: number, data: {
  name?: string;
  equipment?: string | null;
  allow_heavy_loading?: boolean;
  load_input_mode?: string;
  laterality?: 'bilateral' | 'unilateral' | 'either';
  bodyweight_fraction?: number;
  external_load_multiplier?: number;
  variant_group?: string | null;
  grip_style?: string;
  grip_width?: string;
  support_style?: string;
  set_metric_mode?: string;
  estimated_minutes_per_set?: number;
  notes?: string | null;
  tissues?: {
    tissue_id: number;
    role: string;
    loading_factor: number;
    routing_factor?: number;
    fatigue_factor?: number;
    joint_strain_factor?: number;
    tendon_strain_factor?: number;
    laterality_mode?: 'bilateral_equal' | 'selected_side_only' | 'selected_side_primary' | 'contralateral_carryover';
  }[];
}) =>
  request<WkExercise>(`/exercises/${id}`, { method: 'PUT', body: JSON.stringify(data) });

export const applyExerciseMappingWarning = (
  id: number,
  data: {
    code: string;
    source_tissue_id: number;
    target_tissue_id: number;
  },
) =>
  request<WkExercise>(`/exercises/${id}/mapping-warnings/apply`, {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const getWorkoutSessions = (startDate?: string, endDate?: string, limit?: number) => {
  const params: string[] = [];
  if (startDate) params.push(`start_date=${startDate}`);
  if (endDate) params.push(`end_date=${endDate}`);
  if (limit) params.push(`limit=${limit}`);
  return request<WkSession[]>(`/workout-sessions${params.length ? `?${params.join('&')}` : ''}`);
};

export const getWorkoutSession = (id: number) =>
  request<WkSession>(`/workout-sessions/${id}`);

export interface ReadinessTrendPoint {
  date: string;
  session_id: number;
  readiness_beta: number | null;
  readiness_label: WkSession['readiness_label'];
  readiness_pct: number | null;
  readiness_clamped: boolean;
}

export interface ReadinessTrend {
  days: number;
  start: string;
  end: string;
  exercise_id?: number | null;
  points: ReadinessTrendPoint[];
}

export const getReadinessTrend = (days: number = 14) =>
  request<ReadinessTrend>(`/workout-sessions/readiness/trend?days=${days}`);

export const getExerciseReadinessTrend = (exerciseId: number, days: number = 14) =>
  request<ReadinessTrend>(
    `/workout-sessions/readiness/trend?days=${days}&exercise_id=${exerciseId}`,
  );

export interface SessionBetaPoint {
  exercise_id: number;
  exercise_name: string;
  set_id: number | null;
  set_index: number;
  set_order: number;
  weight: number | null;
  reps_done: number;
  rtf: number;
  beta: number | null;
  readiness_label: WkSession['readiness_label'];
  readiness_pct: number | null;
  readiness_clamped: boolean;
}

export interface SessionBetaGroup {
  group: string;
  points: SessionBetaPoint[];
}

export interface SessionBetaEvolution {
  session_id: number;
  groups: SessionBetaGroup[];
}

export const getSessionBetaEvolution = (sessionId: number) =>
  request<SessionBetaEvolution>(
    `/workout-sessions/${sessionId}/beta-evolution`,
  );

export const updateWorkoutSet = (setId: number, data: WorkoutSetUpdateInput) =>
  request<WkSetDetail>(`/workout-sets/${setId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });

export const addWorkoutSet = (sessionId: number, data: WorkoutSetCreateInput) =>
  request<WkSetDetail>(`/workout-sessions/${sessionId}/sets`, {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const deleteWorkoutSet = (setId: number) =>
  request<void>(`/workout-sets/${setId}`, { method: 'DELETE' });

export const deleteWorkoutSession = (sessionId: number) =>
  request<void>(`/workout-sessions/${sessionId}`, { method: 'DELETE' });

export const getExerciseHistory = (id: number, limit?: number) =>
  request<WkExerciseHistory>(`/exercises/${id}/history${limit ? `?limit=${limit}` : ''}`);

export const getTissues = () =>
  request<WkTissue[]>('/tissues');

