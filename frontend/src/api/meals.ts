import { request } from './_request';
import type { Macros, MacroTarget } from './macros';

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

export const getDailySummary = (date: string) =>
  request<DailySummary>(`/daily/${date}`);
