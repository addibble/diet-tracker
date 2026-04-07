const BASE = '/api';
const MAX_IMPORT_IMAGE_BYTES = 1_500_000;
const MAX_IMPORT_IMAGE_DIMENSION = 1600;

function normalizeServerErrorText(status: number, rawText: string): string {
  const normalized = rawText.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  const lower = normalized.toLowerCase();
  const isCloudflareErrorPage = lower.includes('cloudflare') && (
    lower.includes('bad gateway')
    || lower.includes('host error')
    || lower.includes('gateway timeout')
  );

  if (status >= 500 && isCloudflareErrorPage) {
    return (
      'Gateway error between Cloudflare and the app/model provider. '
      + 'Retry in a minute or switch models.'
    );
  }

  return normalized;
}

function blobToFile(blob: Blob, originalName: string): File {
  const baseName = originalName.replace(/\.[^.]+$/, '') || 'upload';
  return new File([blob], `${baseName}.jpg`, { type: 'image/jpeg' });
}

function loadImageFromFile(file: File): Promise<HTMLImageElement> {
  const objectUrl = URL.createObjectURL(file);
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error('Could not read image'));
    };
    img.src = objectUrl;
  });
}

function canvasToBlob(
  canvas: HTMLCanvasElement,
  quality: number,
): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), 'image/jpeg', quality);
  });
}

async function optimizeImageForUpload(file: File): Promise<File> {
  if (!file.type.startsWith('image/')) return file;
  if (file.size <= MAX_IMPORT_IMAGE_BYTES) return file;

  try {
    const img = await loadImageFromFile(file);
    const maxDim = Math.max(img.naturalWidth, img.naturalHeight);
    const scale = maxDim > MAX_IMPORT_IMAGE_DIMENSION ? MAX_IMPORT_IMAGE_DIMENSION / maxDim : 1;

    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));

    const ctx = canvas.getContext('2d');
    if (!ctx) return file;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    const qualityCandidates = [0.82, 0.72, 0.62, 0.52];
    let bestBlob: Blob | null = null;

    for (const quality of qualityCandidates) {
      const blob = await canvasToBlob(canvas, quality);
      if (!blob) continue;
      bestBlob = blob;
      if (blob.size <= MAX_IMPORT_IMAGE_BYTES) {
        return blobToFile(blob, file.name);
      }
    }

    if (!bestBlob) return file;
    if (bestBlob.size < file.size) return blobToFile(bestBlob, file.name);
    return file;
  } catch {
    return file;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const isFormData = options?.body instanceof FormData;
  const headers = new Headers(options?.headers ?? {});
  if (!isFormData && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers,
  });
  const errorDetail = await readErrorDetail(res);
  if (errorDetail) {
    throw new Error(errorDetail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

async function readErrorDetail(res: Response): Promise<string | null> {
  if (res.status === 401) {
    window.location.href = '/login';
    return 'Unauthorized';
  }
  if (!res.ok) {
    if (res.status === 413) {
      return 'Image is too large. Please retake closer or crop the photo.';
    }

    let detail = '';
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const err = await res.json().catch(() => ({ detail: '' }));
      detail = String(err.detail || '');
    } else {
      const text = await res.text().catch(() => '');
      detail = normalizeServerErrorText(res.status, text);
    }

    return detail || res.statusText || 'Request failed';
  }
  return null;
}

// Macro fields shared across all interfaces
export interface Macros {
  calories: number;
  fat: number;
  saturated_fat: number;
  cholesterol: number;
  sodium: number;
  carbs: number;
  fiber: number;
  protein: number;
}

export const MACRO_KEYS: (keyof Macros)[] = [
  'calories', 'fat', 'saturated_fat', 'cholesterol',
  'sodium', 'carbs', 'fiber', 'protein',
];

export const MACRO_LABELS: Record<keyof Macros, string> = {
  calories: 'Cal', fat: 'Fat', saturated_fat: 'Sat Fat', cholesterol: 'Chol',
  sodium: 'Sodium', carbs: 'Carbs', fiber: 'Fiber', protein: 'Protein',
};

export const MACRO_UNITS: Record<keyof Macros, string> = {
  calories: 'kcal', fat: 'g', saturated_fat: 'g', cholesterol: 'mg',
  sodium: 'mg', carbs: 'g', fiber: 'g', protein: 'g',
};

// Helper to get a food's macro value per serving by macro key
export function foodMacroPerServing(food: Food, macro: keyof Macros): number {
  const key = `${macro}_per_serving` as keyof Food;
  return food[key] as number;
}

// Helper to get a recipe's total macro value by macro key
export function recipeTotalMacro(recipe: Recipe, macro: keyof Macros): number {
  const key = `total_${macro}` as keyof Recipe;
  return recipe[key] as number;
}

export interface Food {
  id: number;
  name: string;
  brand: string | null;
  serving_size_grams: number;
  calories_per_serving: number;
  fat_per_serving: number;
  saturated_fat_per_serving: number;
  cholesterol_per_serving: number;
  sodium_per_serving: number;
  carbs_per_serving: number;
  fiber_per_serving: number;
  protein_per_serving: number;
  source: string;
}

export interface FoodImportResult {
  name: string;
  brand: string | null;
  serving_size_grams: number;
  calories_per_serving: number;
  fat_per_serving: number;
  saturated_fat_per_serving: number;
  cholesterol_per_serving: number;
  sodium_per_serving: number;
  carbs_per_serving: number;
  fiber_per_serving: number;
  protein_per_serving: number;
}

export interface RecipeComponent extends Macros {
  id: number;
  food_id: number;
  food_name: string;
  amount_grams: number;
}

export interface Recipe {
  id: number;
  name: string;
  components: RecipeComponent[];
  total_grams: number;
  total_calories: number;
  total_fat: number;
  total_saturated_fat: number;
  total_cholesterol: number;
  total_sodium: number;
  total_carbs: number;
  total_fiber: number;
  total_protein: number;
}

export interface MealItem extends Macros {
  id: number;
  food_id: number | null;
  recipe_id: number | null;
  name: string;
  grams: number;
}

export interface Meal {
  id: number;
  date: string;
  meal_type: string;
  notes: string | null;
  items: MealItem[];
  total_calories: number;
  total_fat: number;
  total_saturated_fat: number;
  total_cholesterol: number;
  total_sodium: number;
  total_carbs: number;
  total_fiber: number;
  total_protein: number;
}

export interface DailySummary {
  date: string;
  meals: Meal[];
  active_macro_target: MacroTarget | null;
  total_calories: number;
  total_fat: number;
  total_saturated_fat: number;
  total_cholesterol: number;
  total_sodium: number;
  total_carbs: number;
  total_fiber: number;
  total_protein: number;
}

export interface MacroCalorieBreakdown {
  fat: number;
  carbs: number;
  protein: number;
}

export interface DashboardTrendDay {
  date: string;
  total_calories: number;
  total_fat: number;
  total_saturated_fat: number;
  total_cholesterol: number;
  total_sodium: number;
  total_carbs: number;
  total_fiber: number;
  total_protein: number;
  macro_calories: MacroCalorieBreakdown;
  macro_calorie_percentages: MacroCalorieBreakdown;
  active_macro_target: MacroTarget | null;
  weight_lb: number | null;
  weight_logged_at: string | null;
}

export interface WeightRegressionPoint {
  date: string;
  weight_lb: number;
}

export interface WeightRegression {
  points_used: number;
  slope_lb_per_day: number;
  slope_lb_per_week: number;
  start_weight_lb: number;
  end_weight_lb: number;
  line: WeightRegressionPoint[];
}

export interface WeightDay {
  date: string;
  weight_lb: number;
  weight_logged_at: string;
}

export interface CalorieStats {
  avg_calories_per_day: number;
  std_calories_per_day: number;
  days_counted: number;
}

export interface DashboardTrends {
  start_date: string;
  end_date: string;
  latest_weight_lb: number | null;
  latest_weight_logged_at: string | null;
  days: DashboardTrendDay[];
  weight_days: WeightDay[];
  weight_regression: WeightRegression | null;
  calorie_stats: CalorieStats | null;
  tdee_estimate: number | null;
}

export interface MacroTarget extends Macros {
  id: number;
  day: string;
  next_day: string | null;
}

// Parsed meal item from LLM
export interface ParsedItem {
  name: string;
  amount_grams: number;
  food_id: number | null;
  source: string; // "db", "usda", or "unknown"
  serving_size_grams: number;
  macros_per_serving: Macros;
}

export interface ParseResult {
  items: ParsedItem[];
  new_foods: Food[]; // Foods auto-created from USDA
}

// Auth
export const login = (password: string) =>
  request('/auth/login', { method: 'POST', body: JSON.stringify({ password }) });

export const logout = () =>
  request('/auth/logout', { method: 'POST' });

// Foods
export const getFoods = (search?: string) =>
  request<Food[]>(`/foods${search ? `?search=${encodeURIComponent(search)}` : ''}`);

export const createFood = (data: Omit<Food, 'id' | 'source'>) =>
  request<Food>('/foods', { method: 'POST', body: JSON.stringify(data) });

export const importFoodLabel = async (file: File) => {
  const uploadFile = await optimizeImageForUpload(file);
  const form = new FormData();
  form.append('image', uploadFile, uploadFile.name);
  return request<FoodImportResult>('/foods/import-label', {
    method: 'POST',
    body: form,
  });
};

export const updateFood = (id: number, data: Partial<Food>) =>
  request<Food>(`/foods/${id}`, { method: 'PUT', body: JSON.stringify(data) });

export const deleteFood = (id: number) =>
  request<void>(`/foods/${id}`, { method: 'DELETE' });

// Recipes
export const getRecipes = () => request<Recipe[]>('/recipes');

export const getRecipe = (id: number) => request<Recipe>(`/recipes/${id}`);

export const createRecipe = (data: { name: string; components: { food_id: number; amount_grams: number }[] }) =>
  request<Recipe>('/recipes', { method: 'POST', body: JSON.stringify(data) });

export const updateRecipe = (id: number, data: { name?: string; components?: { food_id: number; amount_grams: number }[] }) =>
  request<Recipe>(`/recipes/${id}`, { method: 'PUT', body: JSON.stringify(data) });

export const deleteRecipe = (id: number) =>
  request<void>(`/recipes/${id}`, { method: 'DELETE' });

// Food & recipe search (combined)
export interface FoodSearchResult {
  type: 'food' | 'recipe';
  id: number;
  name: string;
  brand?: string | null;
  serving_size_grams?: number;
  calories_per_serving?: number;
  total_grams?: number;
  total_calories?: number;
}

export const searchFoodsAndRecipes = (search: string) =>
  request<FoodSearchResult[]>(
    `/food-search?search=${encodeURIComponent(search)}`,
  );

// Meals
export const getMeals = (date?: string) =>
  request<Meal[]>(`/meals${date ? `?date=${date}` : ''}`);

export const createMeal = (data: {
  date: string; meal_type: string; notes?: string;
  items: { food_id?: number; recipe_id?: number; amount_grams: number }[];
}) => request<Meal>('/meals', { method: 'POST', body: JSON.stringify(data) });

export const updateMeal = (id: number, data: {
  date?: string; meal_type?: string; notes?: string;
  items?: { food_id?: number; recipe_id?: number; amount_grams: number }[];
}) => request<Meal>(`/meals/${id}`, { method: 'PUT', body: JSON.stringify(data) });

export const deleteMeal = (id: number) =>
  request<void>(`/meals/${id}`, { method: 'DELETE' });

// Meal Items (individual item CRUD)
export const updateMealItem = (itemId: number, data: { amount_grams: number }) =>
  request<Meal>(`/meal-items/${itemId}`, {
    method: 'PATCH', body: JSON.stringify(data),
  });

export const addMealItem = (mealId: number, data: {
  food_id?: number; recipe_id?: number; amount_grams: number;
}) => request<Meal>(`/meals/${mealId}/items`, {
  method: 'POST', body: JSON.stringify(data),
});

export const deleteMealItem = (itemId: number) =>
  request<void>(`/meal-items/${itemId}`, { method: 'DELETE' });

// Workouts
export interface Workout {
  id: number;
  sync_key: string;
  date: string;
  workout_type: string;
  duration_minutes: number;
  active_calories: number;
  total_calories: number | null;
  distance_km: number | null;
  source: string | null;
}

export const getWorkouts = (date: string) =>
  request<Workout[]>(`/workouts?date=${date}`);

// Daily
export const getDailySummary = (date: string) =>
  request<DailySummary>(`/daily/${date}`);

export const getDashboardTrends = (endDate: string) =>
  request<DashboardTrends>(`/dashboard/trends?end_date=${encodeURIComponent(endDate)}`);

export const putTodayWeight = (weightLb: number) =>
  request<{ id: number; weight_lb: number; logged_at: string }>('/dashboard/weight', {
    method: 'PUT',
    body: JSON.stringify({ weight_lb: weightLb }),
  });

export const upsertMacroTarget = (
  data: { day: string } & Macros,
) => request<MacroTarget>('/macro-targets', {
  method: 'POST',
  body: JSON.stringify(data),
});

export const getMacroTargets = (startDate?: string, endDate?: string) => {
  const query: string[] = []
  if (startDate) query.push(`start_date=${encodeURIComponent(startDate)}`)
  if (endDate) query.push(`end_date=${encodeURIComponent(endDate)}`)
  const suffix = query.length > 0 ? `?${query.join('&')}` : ''
  return request<MacroTarget[]>(`/macro-targets${suffix}`)
}

// ── Workout Tracking ──

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

export interface WkSetDetail {
  id: number;
  session_id?: number;
  exercise_id: number;
  exercise_name: string;
  set_order: number;
  performed_side: 'left' | 'right' | 'center' | 'bilateral' | null;
  reps: number | null;
  weight: number | null;
  duration_secs: number | null;
  distance_steps: number | null;
  started_at: string | null;
  completed_at: string | null;
  rpe: number | null;
  rep_completion: string | null;
  notes: string | null;
  scheme_history?: ExerciseSchemeHistory;
  tissue_feedback: WkSetTissueFeedback[];
}

export interface WkSession {
  id: number;
  date: string;
  started_at: string | null;
  finished_at: string | null;
  notes: string | null;
  created_at: string;
  sets: WkSetDetail[];
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

export interface WkTissueModelConfig {
  capacity_prior: number;
  recovery_tau_days: number;
  fatigue_tau_days: number;
  collapse_drop_threshold: number;
  ramp_sensitivity: number;
  risk_sensitivity: number;
}

export interface WkTissueCondition {
  status: string;
  severity: number;
  max_loading_factor: number | null;
  recovery_hours_override: number | null;
}

export interface TrackedTissueCondition {
  id: number;
  status: 'healthy' | 'tender' | 'injured' | 'rehabbing';
  severity: number;
  max_loading_factor: number | null;
  recovery_hours_override: number | null;
  rehab_protocol: string | null;
  notes: string | null;
  updated_at: string;
}

export interface RehabProtocolStage {
  id: string;
  label: string;
  focus: string;
}

export interface RehabProtocol {
  id: string;
  title: string;
  category: string;
  summary: string;
  default_pain_monitoring_threshold: number;
  default_max_next_day_flare: number;
  stages: RehabProtocolStage[];
}

export interface RehabPlanSummary {
  id: number;
  protocol_id: string;
  protocol_title: string;
  stage_id: string;
  stage_label: string;
  status: 'active' | 'paused' | 'completed';
  pain_monitoring_threshold: number;
  max_next_day_flare: number;
  sessions_per_week_target: number | null;
  max_weekly_set_progression: number | null;
  max_load_progression_pct: number | null;
  notes: string | null;
  started_at?: string;
  updated_at?: string;
  tracked_tissue_id?: number;
  tracked_tissue_display_name?: string | null;
  tissue_id?: number | null;
  tissue_name?: string | null;
}

export interface RehabCheckInSummary {
  id: number;
  rehab_plan_id: number | null;
  pain_0_10: number;
  stiffness_0_10: number;
  weakness_0_10: number;
  neural_symptoms_0_10: number;
  during_load_pain_0_10: number;
  next_day_flare: number;
  confidence_0_10: number;
  notes: string | null;
  recorded_at: string;
}

export interface WkTissueReadiness {
  tissue: WkTissue;
  condition: WkTissueCondition | null;
  last_trained: string | null;
  hours_since: number | null;
  effective_recovery_hours: number;
  recovery_pct: number;
  ready: boolean;
  volume_7d: number;
  exercises_available: {
    exercise_id: number;
    exercise_name: string;
    role: string;
    target_sets: number;
    target_rep_min: number | null;
    target_rep_max: number | null;
  }[];
}

export interface TrackedTissueReadiness {
  tracked_tissue: {
    id: number;
    tissue_id: number;
    tissue_name: string;
    tissue_display_name: string;
    tissue_type: string;
    region: string;
    side: 'left' | 'right' | 'center';
    display_name: string;
    tracking_mode: 'paired' | 'center';
    active: boolean;
  };
  condition: TrackedTissueCondition | null;
  active_rehab_plan: RehabPlanSummary | null;
  latest_rehab_check_in: RehabCheckInSummary | null;
  last_trained: string | null;
  hours_since: number | null;
  effective_recovery_hours: number;
  recovery_pct: number;
  protected: boolean;
  ready: boolean;
  volume_7d: number;
  cross_education_7d: number;
  exercises_available: {
    exercise_id: number;
    exercise_name: string;
    laterality: 'bilateral' | 'unilateral' | 'either';
    laterality_mode: 'bilateral_equal' | 'selected_side_only' | 'selected_side_primary' | 'contralateral_carryover';
    role: string;
    target_sets: number;
    target_rep_min: number | null;
    target_rep_max: number | null;
  }[];
}

export interface WkSetTissueFeedback {
  id?: number;
  tracked_tissue_id: number;
  tracked_tissue_display_name?: string;
  pain_0_10: number;
  symptom_note: string | null;
  recorded_at?: string;
  above_threshold?: boolean;
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

export interface TrainingModelWindow {
  id: number;
  start_date: string;
  end_date: string;
  kind: string;
  notes: string | null;
  exclude_from_model: boolean;
}

export interface TrainingModelTissueSummary {
  tissue: WkTissue & Required<Pick<WkTissue, 'model_config'>>;
  current_capacity: number;
  baseline_capacity: number;
  capacity_trend_30d_pct: number;
  normalized_load: number;
  acute_fatigue: number;
  chronic_load: number;
  recovery_estimate: number;
  learned_recovery_days: number;
  ramp_ratio: number;
  risk_7d: number;
  risk_14d: number;
  collapse_count: number;
  contributors: string[];
  current_condition: {
    status: string;
    severity: number;
    notes: string | null;
    updated_at: string;
  } | null;
  recent_collapses: string[];
}

export interface TrainingModelExerciseInsight {
  id: number;
  name: string;
  equipment: string | null;
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
  in_active_program: boolean;
  weighted_risk_7d: number;
  weighted_risk_14d: number;
  max_tissue_risk_7d: number;
  weighted_normalized_load: number;
  suitability_score: number;
  recommendation: 'avoid' | 'caution' | 'good';
  recommendation_reason: string;
  recommendation_details: string[];
  blocked_tissues: string[];
  favored_tissues: string[];
  current_e1rm: number | null;
  peak_e1rm: number | null;
  tissues: {
    tissue_id: number;
    tissue_name: string;
    tissue_display_name: string;
    tissue_type: string;
    routing_factor: number;
    tissue_risk_7d: number;
    tissue_risk_14d: number;
    tissue_normalized_load: number;
    recovery_state: number;
    confidence: number;
    trouble_association: number;
  }[];
}

export interface TrainingModelSummary {
  as_of: string;
  overview: {
    at_risk_count: number;
    recovering_count: number;
    tracked_tissues: number;
    excluded_windows: TrainingModelWindow[];
  };
  tissues: TrainingModelTissueSummary[];
  exercises: TrainingModelExerciseInsight[];
}

export interface TrainingModelHistoryPoint {
  date: string;
  raw_load: number;
  normalized_load: number;
  capacity_state: number;
  acute_fatigue: number;
  chronic_load: number;
  recovery_state: number;
  ramp_ratio: number;
  risk_7d: number;
  risk_14d: number;
  collapse_flag: boolean;
  contributors: string[];
}

export interface TrainingModelTissueHistory {
  tissue: WkTissue & Required<Pick<WkTissue, 'model_config'>>;
  as_of: string;
  learned_recovery_days: number;
  baseline_capacity: number;
  capacity_trend_30d_pct: number;
  collapse_dates: string[];
  overload_dates: string[];
  history: TrainingModelHistoryPoint[];
}

// ── Recovery Check-in Types ──

export interface TissuePainCheckIn {
  id: number;
  date: string;
  region: string;
  tracked_tissue_id: number | null;
  check_in_kind: 'pain';
  target_kind: 'tracked_tissue' | 'region';
  target_key: string;
  target_label: string;
  tracked_tissue: RecoveryCheckInTrackedTissue | null;
  pain_0_10: number;
  notes: string | null;
}

export interface RegionSorenessCheckIn {
  id: number;
  date: string;
  region: string;
  tracked_tissue_id: null;
  check_in_kind: 'soreness';
  target_kind: 'region';
  target_key: string;
  target_label: string;
  tracked_tissue: null;
  soreness_0_10: number;
  notes: string | null;
}

export type RecoveryCheckIn = TissuePainCheckIn | RegionSorenessCheckIn;

export interface RecoveryCheckInTrackedTissue {
  id: number;
  tissue_id: number;
  tissue_name: string;
  tissue_display_name: string;
  tissue_type: string;
  region: string;
  side: 'left' | 'right' | 'center';
  display_name: string;
  tracking_mode: 'paired' | 'center';
  active: boolean;
}

export interface RecoveryCheckInTarget {
  target_key: string;
  check_in_kind: 'pain' | 'soreness';
  target_kind: 'tracked_tissue' | 'region';
  region: string;
  tracked_tissue_id: number | null;
  target_label: string;
  tracked_tissue: RecoveryCheckInTrackedTissue | null;
  reasons?: { code: string; label: string }[];
  existing_check_in?: RecoveryCheckIn | null;
}

export interface RecoveryCheckInTargetsResponse {
  date: string;
  targets: RecoveryCheckInTarget[];
  pain_targets: RecoveryCheckInTarget[];
  soreness_targets: RecoveryCheckInTarget[];
  today_check_ins: RecoveryCheckIn[];
  today_pain_check_ins: TissuePainCheckIn[];
  today_soreness_check_ins: RegionSorenessCheckIn[];
  other_options: {
    regions: RecoveryCheckInTarget[];
    tracked_tissues: RecoveryCheckInTarget[];
    soreness_regions: RecoveryCheckInTarget[];
    pain_tracked_tissues: RecoveryCheckInTarget[];
  };
}

export interface RegionInfo {
  region: string;
  label: string;
  tissues: {
    id: number;
    name: string;
    display_name: string;
    type: string;
    regions: string[];
    is_primary: boolean;
  }[];
}

// ── Exercise Strength Types ──

export interface ExerciseStrength {
  exercise_id: number;
  exercise_name: string;
  as_of: string;
  current_e1rm: number;
  peak_e1rm: number;
  trend: 'rising' | 'stable' | 'falling';
  trend_pct: number;
  history: { date: string; e1rm: number }[];
}

// ── Planner Types ──

export interface PlannerExercisePrescription {
  exercise_id: number;
  exercise_name: string;
  equipment: string | null;
  laterality?: 'bilateral' | 'unilateral' | 'either';
  performed_side?: 'left' | 'right' | 'center' | 'bilateral' | null;
  rep_scheme: RepScheme;
  target_sets: number;
  target_reps: string;
  target_weight: number | null;
  rationale: string;
  overload_note: string | null;
  weight_adjustment_note?: string | null;
  side_explanation?: string | null;
  selection_note?: string | null;
  blocked_variant?: string | null;
  protected_tissues?: string[];
  workflow_role?: 'group' | 'rehab' | 'accessory' | null;
  group_label?: string | null;
  selected: boolean;
  selectable?: boolean;
  planner_status?: 'ready' | 'overworked' | 'blocked';
  planner_reason?: string;
  ready_tomorrow?: boolean;
  ready_tomorrow_reason?: string | null;
  readiness_score?: number;
  days_since_last?: number;
  recommendation?: 'good' | 'caution' | 'avoid';
  last_performance: {
    date: string;
    rep_scheme: RepScheme;
    sets: ExerciseHistorySet[];
  } | null;
  scheme_history: ExerciseSchemeHistory;
}

export interface PlannerDayPlan {
  group_id: string;
  day_label: string;
  readiness_score: number;
  days_since_last: number | null;
  target_regions: string[];
  exercise_count: number;
  core_exercise_count: number;
  exercises: PlannerExercisePrescription[];
  rationale: string;
}

export interface PlannerGroupBrief {
  group_id: string;
  day_label: string;
  target_regions: string[];
  exercise_count: number;
  available_count: number;
  ready_tomorrow_count: number;
  days_since_last: number;
  readiness_score: number;
  rationale: string;
  exercises: PlannerExercisePrescription[];
}

export interface PlannerFilteredTissue {
  tracked_tissue_id: number;
  tissue_id: number;
  target_label: string;
  status: string;
  reason: string;
}

export interface PlannerTodayResponse {
  as_of: string;
  today_plan: PlannerDayPlan | null;
  tomorrow_plan: PlannerDayPlan | null;
  groups: PlannerGroupBrief[];
  filtered_tissues: PlannerFilteredTissue[];
  message: string | null;
}

// Workout API functions
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

// Individual workout set CRUD
export interface WkSetTissueFeedbackInput {
  tracked_tissue_id: number;
  pain_0_10: number;
  symptom_note?: string | null;
}

export interface WorkoutSetUpdateInput {
  performed_side?: 'left' | 'right' | 'center' | 'bilateral' | null;
  reps?: number | null;
  weight?: number | null;
  duration_secs?: number | null;
  distance_steps?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  rpe?: number | null;
  rep_completion?: string | null;
  notes?: string | null;
  tissue_feedback?: WkSetTissueFeedbackInput[];
}

export interface WorkoutSetCreateInput extends WorkoutSetUpdateInput {
  exercise_id: number;
  set_order?: number;
}

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

// ProgramDayExercise target editing
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

export const getTissueReadiness = () =>
  request<WkTissueReadiness[]>('/tissue-readiness');

export const getTrackedTissueReadiness = () =>
  request<TrackedTissueReadiness[]>('/tissue-readiness/tracked');

export const getExerciseHistory = (id: number, limit?: number) =>
  request<WkExerciseHistory>(`/exercises/${id}/history${limit ? `?limit=${limit}` : ''}`);

export const getTissues = () =>
  request<WkTissue[]>('/tissues');

export const getRehabProtocols = () =>
  request<RehabProtocol[]>('/tissues/rehab-protocols');

export const createTissueCondition = (data: {
  tissue_id?: number | null;
  tracked_tissue_id?: number | null;
  status: 'healthy' | 'tender' | 'injured' | 'rehabbing';
  severity?: number;
  max_loading_factor?: number | null;
  recovery_hours_override?: number | null;
  rehab_protocol?: string | null;
  notes?: string | null;
}) =>
  request<TrackedTissueCondition & {
    tissue_id: number;
    tracked_tissue_id: number | null;
    tracked_tissue_display_name: string | null;
  }>('/tissues/conditions', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const createRehabPlan = (data: {
  tracked_tissue_id: number;
  protocol_id: string;
  stage_id: string;
  status?: 'active' | 'paused' | 'completed';
  pain_monitoring_threshold?: number | null;
  max_next_day_flare?: number | null;
  sessions_per_week_target?: number | null;
  max_weekly_set_progression?: number | null;
  max_load_progression_pct?: number | null;
  notes?: string | null;
}) =>
  request<RehabPlanSummary>('/tissues/rehab-plans', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const updateRehabPlan = (
  rehabPlanId: number,
  data: {
    protocol_id?: string;
    stage_id?: string;
    status?: 'active' | 'paused' | 'completed';
    pain_monitoring_threshold?: number | null;
    max_next_day_flare?: number | null;
    sessions_per_week_target?: number | null;
    max_weekly_set_progression?: number | null;
    max_load_progression_pct?: number | null;
    notes?: string | null;
  },
) =>
  request<RehabPlanSummary>(`/tissues/rehab-plans/${rehabPlanId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });

export const getTrainingModelSummary = (asOf?: string, includeExercises = false) => {
  const query = new URLSearchParams();
  if (asOf) query.set('as_of', asOf);
  if (includeExercises) query.set('include_exercises', 'true');
  const suffix = query.size ? `?${query.toString()}` : '';
  return request<TrainingModelSummary>(`/training-model/summary${suffix}`);
};

export const getTrainingModelExercises = (
  params?: {
    asOf?: string;
    sortBy?: 'risk_7d' | 'risk_14d' | 'suitability' | 'normalized_load';
    direction?: 'asc' | 'desc';
    limit?: number;
    recommendation?: 'avoid' | 'caution' | 'good';
  },
) => {
  const query = new URLSearchParams();
  if (params?.asOf) query.set('as_of', params.asOf);
  if (params?.sortBy) query.set('sort_by', params.sortBy);
  if (params?.direction) query.set('direction', params.direction);
  if (params?.limit) query.set('limit', String(params.limit));
  if (params?.recommendation) query.set('recommendation', params.recommendation);
  const suffix = query.size ? `?${query.toString()}` : '';
  return request<TrainingModelExerciseInsight[]>(`/training-model/exercises${suffix}`);
};

export const getTrainingModelTissueHistory = (tissueId: number, days = 90, asOf?: string) => {
  const params = [`days=${days}`];
  if (asOf) params.push(`as_of=${asOf}`);
  return request<TrainingModelTissueHistory>(
    `/training-model/tissues/${tissueId}/history?${params.join('&')}`,
  );
};

// ── Exercise Strength ──

export const getExerciseStrength = (exerciseId: number, days = 90, asOf?: string) => {
  const params = [`days=${days}`];
  if (asOf) params.push(`as_of=${asOf}`);
  return request<ExerciseStrength>(
    `/training-model/exercises/${exerciseId}/strength?${params.join('&')}`,
  );
};

// ── Recovery Check-ins ──

export const createRecoveryCheckIn = (data: {
  date: string;
  region?: string;
  tracked_tissue_id?: number;
  soreness_0_10?: number;
  pain_0_10?: number;
  notes?: string;
}) =>
  request<RecoveryCheckIn>('/training-model/check-in', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const getRecoveryCheckIns = (date?: string, startDate?: string, endDate?: string) => {
  const params: string[] = [];
  if (date) params.push(`date=${date}`);
  if (startDate) params.push(`start_date=${startDate}`);
  if (endDate) params.push(`end_date=${endDate}`);
  return request<RecoveryCheckIn[]>(
    `/training-model/check-ins${params.length ? `?${params.join('&')}` : ''}`,
  );
};

export const getRecoveryCheckInTargets = (date?: string) =>
  request<RecoveryCheckInTargetsResponse>(
    `/training-model/check-in-targets${date ? `?date=${date}` : ''}`,
  );

export const getRegions = () =>
  request<RegionInfo[]>('/training-model/regions');

// ── Planner ──

// Helper: append ?as_of=... query param when provided
const plannerQ = (path: string, asOf?: string) =>
  asOf ? `${path}${path.includes('?') ? '&' : '?'}as_of=${asOf}` : path;

export const getPlannerToday = (asOf?: string) =>
  request<PlannerTodayResponse>(plannerQ('/planner/today', asOf));

export const savePlan = (
  dayLabel: string,
  targetRegions: string[],
  exercises: PlannerExercisePrescription[],
  asOf?: string,
) =>
  request<SavedPlan>(plannerQ('/planner/save', asOf), {
    method: 'POST',
    body: JSON.stringify({
      day_label: dayLabel,
      target_regions: targetRegions,
      exercises,
    }),
  });

export const getActivePlan = (asOf?: string) =>
  request<SavedPlan>(plannerQ('/planner/active', asOf)).catch(() => null);

export const deletePlan = (asOf?: string) =>
  request<void>(plannerQ('/planner/active', asOf), { method: 'DELETE' });

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

export interface VolumeByRegion {
  dates: string[];
  regions: string[];
  daily: Record<string, Record<string, number>>;
  totals: Record<string, number>;
  region_labels: Record<string, string>;
}

export const getVolumeByRegion = (days = 7, asOf?: string) =>
  request<VolumeByRegion>(plannerQ(`/training-model/volume-by-region?days=${days}`, asOf));

export const startPlan = (asOf?: string) =>
  request<{ workout_session_id: number }>(plannerQ('/planner/start', asOf), { method: 'POST' });

export const completePlan = (asOf?: string) =>
  request<{ id: number; status: string }>(plannerQ('/planner/complete', asOf), { method: 'POST' });

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
  load_input_mode: string;
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
    tissue_feedback: WkSetTissueFeedback[];
  }[];
  sets_done: number;
  done: boolean;
}

// Parse meal with LLM
export const parseMeal = (description: string) =>
  request<ParseResult>('/meals/parse', {
    method: 'POST',
    body: JSON.stringify({ description }),
  });

// Chat-based meal logging
export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatProposedItem {
  food_id: number | null;
  recipe_id?: number | null;
  name: string;
  amount_grams: number;
  source: string;
  serving_size_grams: number;
  macros_per_serving: Macros;
  group?: string;
  source_recipe_id?: number;
}

export interface RepCheckExercise {
  exercise_name: string;
  weight: number | null;
  target_sets: number;
  target_rep_min: number;
  target_rep_max: number;
}

export interface ChatResponse {
  message: string;
  proposed_items: ChatProposedItem[] | null;
  proposed_date: string;
  proposed_meal_type: string;
  saved_meal: Meal | null;
  edit_meal_id: number | null;
  data_changed: boolean;
  rep_check: RepCheckExercise[] | null;
  workout_session_id: number | null;
}

export interface ChatModelOption {
  id: string;
  name: string;
  provider: string;
  input_cost_per_million: number;
  output_cost_per_million: number;
  created: number;
  tier?: 'low' | 'medium' | 'high_reasoning';
  tier_label?: string;
}

export interface ChatModelsResponse {
  default_model: string;
  models: ChatModelOption[];
}

export const getChatModels = () =>
  request<ChatModelsResponse>('/meals/chat/models');

function buildChatPayload(
  messages: ChatMessage[],
  date?: string,
  meal_type?: string,
  notes?: string,
  model?: string,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    messages,
    client_now_iso: new Date().toISOString(),
  }
  const clientTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  if (clientTimezone) payload.client_timezone = clientTimezone
  if (date) payload.date = date
  if (meal_type) payload.meal_type = meal_type
  if (notes) payload.notes = notes
  if (model) payload.model = model
  return payload
}

export interface ChatProgressStatusEvent {
  type: 'status';
  run_id: string;
  stage: 'queued' | 'processing';
  message: string;
  elapsed_ms: number;
  activity_source: 'backend' | 'openrouter' | 'local_tool' | 'finalizing' | null;
  last_activity_event: string | null;
  last_activity_event_age_ms: number | null;
  active_tool_name: string | null;
  last_upstream_event: string | null;
  last_upstream_event_age_ms: number | null;
  last_upstream_status_code: number | null;
  openrouter_request_id: string | null;
  openrouter_completion_id: string | null;
  upstream_cf_ray: string | null;
  upstream_attempt: number | null;
  upstream_round: number | null;
  stream_line: string | null;
  text: string | null;
  tool_args: string | null;
  tool_result: string | null;
}

export interface ChatProgressResultEvent {
  type: 'result';
  run_id: string;
  data: ChatResponse;
}

export interface ChatProgressErrorEvent {
  type: 'error';
  run_id: string;
  status: number;
  detail: string;
}

export type ChatProgressEvent =
  | ChatProgressStatusEvent
  | ChatProgressResultEvent
  | ChatProgressErrorEvent;

export const chatMeal = (
  messages: ChatMessage[],
  date?: string,
  meal_type?: string,
  notes?: string,
  model?: string,
) => {
  const payload = buildChatPayload(messages, date, meal_type, notes, model)

  return request<ChatResponse>('/meals/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export const chatMealWithProgress = async (
  messages: ChatMessage[],
  onEvent: (event: ChatProgressEvent) => void,
  date?: string,
  meal_type?: string,
  notes?: string,
  model?: string,
  signal?: AbortSignal,
): Promise<ChatResponse> => {
  const payload = buildChatPayload(messages, date, meal_type, notes, model)
  const res = await fetch(`${BASE}/meals/chat/stream`, {
    credentials: 'include',
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })

  const errorDetail = await readErrorDetail(res)
  if (errorDetail) {
    throw new Error(errorDetail)
  }

  if (!res.body) {
    return chatMeal(messages, date, meal_type, notes, model)
  }

  const decoder = new TextDecoder()
  const reader = res.body.getReader()
  let buffer = ''
  let finalResult: ChatResponse | null = null

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    while (true) {
      const newlineIndex = buffer.indexOf('\n')
      if (newlineIndex < 0) break
      const line = buffer.slice(0, newlineIndex).trim()
      buffer = buffer.slice(newlineIndex + 1)
      if (!line) continue

      const event = JSON.parse(line) as ChatProgressEvent
      onEvent(event)
      if (event.type === 'result') {
        finalResult = event.data
      }
      if (event.type === 'error') {
        throw new Error(event.detail || `Request failed (${event.status})`)
      }
    }
  }

  const tail = buffer.trim()
  if (tail) {
    const event = JSON.parse(tail) as ChatProgressEvent
    onEvent(event)
    if (event.type === 'result') {
      finalResult = event.data
    } else if (event.type === 'error') {
      throw new Error(event.detail || `Request failed (${event.status})`)
    }
  }

  if (finalResult) {
    return finalResult
  }
  throw new Error('Chat stream ended without a result')
}
