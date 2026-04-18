import { request } from './_request';

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

export interface MacroTarget extends Macros {
  id: number;
  day: string;
  next_day: string | null;
}

export const upsertMacroTarget = (
  data: { day: string } & Macros,
) => request<MacroTarget>('/macro-targets', {
  method: 'POST',
  body: JSON.stringify(data),
});

export const getMacroTargets = (startDate?: string, endDate?: string) => {
  const query: string[] = [];
  if (startDate) query.push(`start_date=${encodeURIComponent(startDate)}`);
  if (endDate) query.push(`end_date=${encodeURIComponent(endDate)}`);
  const suffix = query.length > 0 ? `?${query.join('&')}` : '';
  return request<MacroTarget[]>(`/macro-targets${suffix}`);
};
