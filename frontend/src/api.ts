const BASE = '/api';
const MAX_IMPORT_IMAGE_BYTES = 1_500_000;
const MAX_IMPORT_IMAGE_DIMENSION = 1600;

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
  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    if (res.status === 413) {
      throw new Error('Image is too large. Please retake closer or crop the photo.');
    }

    let detail = '';
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const err = await res.json().catch(() => ({ detail: '' }));
      detail = String(err.detail || '');
    } else {
      const text = await res.text().catch(() => '');
      detail = text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    }

    throw new Error(detail || res.statusText || 'Request failed');
  }
  if (res.status === 204) return undefined as T;
  return res.json();
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

export interface DashboardTrends {
  start_date: string;
  end_date: string;
  latest_weight_lb: number | null;
  latest_weight_logged_at: string | null;
  days: DashboardTrendDay[];
  weight_days: WeightDay[];
  weight_regression: WeightRegression | null;
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

export interface WkExercise {
  id: number;
  name: string;
  equipment: string | null;
  notes: string | null;
  tissues: WkExerciseTissueMapping[];
}

export interface WkExerciseTissueMapping {
  tissue_id: number;
  tissue_name: string;
  tissue_display_name: string;
  role: string;
  loading_factor: number;
}

export interface WkSetDetail {
  id: number;
  exercise_id: number;
  exercise_name: string;
  set_order: number;
  reps: number | null;
  weight: number | null;
  duration_secs: number | null;
  distance_steps: number | null;
  rpe: number | null;
  rep_completion: string | null;
  notes: string | null;
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

export interface WkRoutineExercise {
  id: number;
  exercise_id: number;
  exercise_name: string;
  equipment: string | null;
  target_sets: number;
  target_rep_min: number | null;
  target_rep_max: number | null;
  sort_order: number;
  active: number;
  notes: string | null;
  last_performance: {
    date: string;
    sets: { reps: number | null; weight: number | null; rep_completion: string | null }[];
  } | null;
}

export interface WkTissue {
  id: number;
  name: string;
  display_name: string;
  type: string;
  parent_id: number | null;
  recovery_hours: number;
}

export interface WkTissueCondition {
  status: string;
  severity: number;
  max_loading_factor: number | null;
  recovery_hours_override: number | null;
}

export interface WkTissueReadiness {
  tissue: WkTissue;
  condition: WkTissueCondition | null;
  last_trained: string | null;
  hours_since: number | null;
  effective_recovery_hours: number;
  recovery_pct: number;
  ready: boolean;
  exercises_available: {
    exercise_id: number;
    exercise_name: string;
    role: string;
    target_sets: number;
    target_rep_min: number | null;
    target_rep_max: number | null;
  }[];
}

export interface WkExerciseHistory {
  exercise: WkExercise;
  sessions: {
    date: string;
    sets: { set_order: number; reps: number | null; weight: number | null; rep_completion: string | null }[];
    max_weight: number;
    total_volume: number;
    rep_completions: string[];
  }[];
}

// Workout API functions
export const getExercises = (search?: string) =>
  request<WkExercise[]>(`/exercises${search ? `?search=${encodeURIComponent(search)}` : ''}`);

export const getWorkoutSessions = (startDate?: string, endDate?: string, limit?: number) => {
  const params: string[] = [];
  if (startDate) params.push(`start_date=${startDate}`);
  if (endDate) params.push(`end_date=${endDate}`);
  if (limit) params.push(`limit=${limit}`);
  return request<WkSession[]>(`/workout-sessions${params.length ? `?${params.join('&')}` : ''}`);
};

export const getWorkoutSession = (id: number) =>
  request<WkSession>(`/workout-sessions/${id}`);

export const getTissueReadiness = () =>
  request<WkTissueReadiness[]>('/tissue-readiness');

export const getRoutine = () =>
  request<WkRoutineExercise[]>('/routine');

export const getExerciseHistory = (id: number, limit?: number) =>
  request<WkExerciseHistory>(`/exercises/${id}/history${limit ? `?limit=${limit}` : ''}`);

export const getTissues = (tree?: boolean) =>
  request<WkTissue[]>(`/tissues${tree ? '?tree=true' : ''}`);

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
  saved_meal: Meal | null;
  edit_meal_id: number | null;
  data_changed: boolean;
  rep_check: RepCheckExercise[] | null;
}

export interface ChatModelOption {
  id: string;
  name: string;
  provider: string;
  input_cost_per_million: number;
  output_cost_per_million: number;
  created: number;
}

export interface ChatModelsResponse {
  default_model: string;
  models: ChatModelOption[];
}

export const getChatModels = () =>
  request<ChatModelsResponse>('/meals/chat/models');

export const chatMeal = (
  messages: ChatMessage[],
  date?: string,
  meal_type?: string,
  notes?: string,
  model?: string,
) => {
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

  return request<ChatResponse>('/meals/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
