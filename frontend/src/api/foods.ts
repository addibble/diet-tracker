import { request, optimizeImageForUpload } from './_request';
import type { Macros } from './macros';

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

// Helper to get a food's macro value per serving by macro key
export function foodMacroPerServing(food: Food, macro: keyof Macros): number {
  const key = `${macro}_per_serving` as keyof Food;
  return food[key] as number;
}

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

export const searchFoodsAndRecipes = (search: string) =>
  request<FoodSearchResult[]>(
    `/food-search?search=${encodeURIComponent(search)}`,
  );
