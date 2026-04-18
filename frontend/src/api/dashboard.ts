import { request } from './_request';
import type { MacroTarget } from './macros';

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

export const getDashboardTrends = (endDate: string) =>
  request<DashboardTrends>(`/dashboard/trends?end_date=${encodeURIComponent(endDate)}`);

export interface VolumeByRegion {
  dates: string[];
  regions: string[];
  daily: Record<string, number[]>;
  totals: Record<string, number>;
}

export const getVolumeByRegion = (days = 10, endDate?: string) => {
  const params = new URLSearchParams({ days: String(days) });
  if (endDate) params.set('end_date', endDate);
  return request<VolumeByRegion>(`/dashboard/volume-by-region?${params.toString()}`);
};

export const putTodayWeight = (weightLb: number) =>
  request<{ id: number; weight_lb: number; logged_at: string }>('/dashboard/weight', {
    method: 'PUT',
    body: JSON.stringify({ weight_lb: weightLb }),
  });
