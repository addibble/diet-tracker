// Concurrency throttle for bootstrap-band fetches. Bootstrap is the
// most expensive backend call per exercise (~600 ms cold, ~5 ms warm).
// When the dashboard expands several exercises simultaneously, we
// don't want to send N parallel cold requests — that would fan out
// to N × 150 scipy fits server-side. The backend's single-flight
// lock dedupes same-key concurrent requests, but each cold key still
// costs a worker thread, so we additionally cap client-side parallelism.

import { getCurveBands, type CurveBandsResponse } from '../api/planner';

const MAX_CONCURRENT = 2;

type Task = () => Promise<void>;

let active = 0;
const queue: Task[] = [];

function pump(): void {
  while (active < MAX_CONCURRENT && queue.length > 0) {
    const task = queue.shift()!;
    active += 1;
    void task().finally(() => {
      active -= 1;
      pump();
    });
  }
}

export function fetchCurveBandsThrottled(
  exerciseId: number,
  date: string,
): Promise<CurveBandsResponse> {
  return new Promise((resolve, reject) => {
    queue.push(async () => {
      try {
        resolve(await getCurveBands(exerciseId, date));
      } catch (err) {
        reject(err);
      }
    });
    pump();
  });
}
