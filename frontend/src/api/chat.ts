import { request, BASE, readErrorDetail } from './_request';
import type { Macros } from './macros';
import type { Meal } from './meals';

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
  };
  const clientTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (clientTimezone) payload.client_timezone = clientTimezone;
  if (date) payload.date = date;
  if (meal_type) payload.meal_type = meal_type;
  if (notes) payload.notes = notes;
  if (model) payload.model = model;
  return payload;
}

export const chatMeal = (
  messages: ChatMessage[],
  date?: string,
  meal_type?: string,
  notes?: string,
  model?: string,
) => {
  const payload = buildChatPayload(messages, date, meal_type, notes, model);

  return request<ChatResponse>('/meals/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const chatMealWithProgress = async (
  messages: ChatMessage[],
  onEvent: (event: ChatProgressEvent) => void,
  date?: string,
  meal_type?: string,
  notes?: string,
  model?: string,
  signal?: AbortSignal,
): Promise<ChatResponse> => {
  const payload = buildChatPayload(messages, date, meal_type, notes, model);
  const res = await fetch(`${BASE}/meals/chat/stream`, {
    credentials: 'include',
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });

  const errorDetail = await readErrorDetail(res);
  if (errorDetail) {
    throw new Error(errorDetail);
  }

  if (!res.body) {
    return chatMeal(messages, date, meal_type, notes, model);
  }

  const decoder = new TextDecoder();
  const reader = res.body.getReader();
  let buffer = '';
  let finalResult: ChatResponse | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    while (true) {
      const newlineIndex = buffer.indexOf('\n');
      if (newlineIndex < 0) break;
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line) continue;

      const event = JSON.parse(line) as ChatProgressEvent;
      onEvent(event);
      if (event.type === 'result') {
        finalResult = event.data;
      }
      if (event.type === 'error') {
        throw new Error(event.detail || `Request failed (${event.status})`);
      }
    }
  }

  const tail = buffer.trim();
  if (tail) {
    const event = JSON.parse(tail) as ChatProgressEvent;
    onEvent(event);
    if (event.type === 'result') {
      finalResult = event.data;
    } else if (event.type === 'error') {
      throw new Error(event.detail || `Request failed (${event.status})`);
    }
  }

  if (finalResult) {
    return finalResult;
  }
  throw new Error('Chat stream ended without a result');
};
