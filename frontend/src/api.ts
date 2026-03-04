const BASE = '/api';

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
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
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
  total_calories: number;
  total_fat: number;
  total_saturated_fat: number;
  total_cholesterol: number;
  total_sodium: number;
  total_carbs: number;
  total_fiber: number;
  total_protein: number;
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
  const form = new FormData();
  form.append('image', file);
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
  meal_type?: string; notes?: string;
  items?: { food_id?: number; recipe_id?: number; amount_grams: number }[];
}) => request<Meal>(`/meals/${id}`, { method: 'PUT', body: JSON.stringify(data) });

export const deleteMeal = (id: number) =>
  request<void>(`/meals/${id}`, { method: 'DELETE' });

// Daily
export const getDailySummary = (date: string) =>
  request<DailySummary>(`/daily/${date}`);

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
  name: string;
  amount_grams: number;
  source: string;
  serving_size_grams: number;
  macros_per_serving: Macros;
}

export interface ChatResponse {
  message: string;
  proposed_items: ChatProposedItem[] | null;
  saved_meal: Meal | null;
  edit_meal_id: number | null;
}

export const chatMeal = (
  messages: ChatMessage[],
  date: string,
  meal_type: string,
  notes?: string,
) =>
  request<ChatResponse>('/meals/chat', {
    method: 'POST',
    body: JSON.stringify({ messages, date, meal_type, notes }),
  });
