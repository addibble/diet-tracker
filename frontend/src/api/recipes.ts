import { request } from './_request';
import type { Macros } from './macros';

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

export const getRecipes = () => request<Recipe[]>('/recipes');

export const createRecipe = (
  data: { name: string; components: { food_id: number; amount_grams: number }[] },
) => request<Recipe>('/recipes', { method: 'POST', body: JSON.stringify(data) });

export const updateRecipe = (
  id: number,
  data: { name?: string; components?: { food_id: number; amount_grams: number }[] },
) => request<Recipe>(`/recipes/${id}`, { method: 'PUT', body: JSON.stringify(data) });

export const deleteRecipe = (id: number) =>
  request<void>(`/recipes/${id}`, { method: 'DELETE' });
