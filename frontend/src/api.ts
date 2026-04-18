// Barrel module: the frontend imports everything from `./api`. Domain modules
// live under `./api/`. Keep this file as pure re-exports to preserve existing
// import paths while allowing focused modules to hold their own types and
// request helpers.
export * from './api/_request';
export * from './api/auth';
export * from './api/macros';
export * from './api/foods';
export * from './api/recipes';
export * from './api/meals';
export * from './api/dashboard';
export * from './api/workouts';
export * from './api/planner';
export * from './api/chat';
