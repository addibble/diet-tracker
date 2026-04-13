import sqlite3

conn = sqlite3.connect('production_db_backup_2026-04-12-164418.db')

print("=== Sessions today (April 12) ===")
rows = conn.execute("""
    SELECT ws.id, ws.date, ws.started_at, ws.finished_at
    FROM workout_sessions ws
    WHERE ws.date >= '2026-04-12'
    ORDER BY ws.id
""").fetchall()
for r in rows:
    print(f"  Session {r[0]}: date={r[1]} started={r[2]} finished={r[3]}")

print()

# Get columns of workout_sets
cols = conn.execute("PRAGMA table_info(workout_sets)").fetchall()
print("workout_sets columns:", [c[1] for c in cols])
print()

# Get exercises in today's sessions
session_ids = [r[0] for r in rows]
if session_ids:
    placeholders = ','.join('?' * len(session_ids))
    sets = conn.execute(f"""
        SELECT e.name, ws2.weight, ws2.reps, ws2.rpe, ws2.set_order,
               ws2.completed_at, e.id, ws2.session_id
        FROM workout_sets ws2
        JOIN exercises e ON e.id = ws2.exercise_id
        WHERE ws2.session_id IN ({placeholders})
        ORDER BY ws2.session_id, e.name, ws2.set_order
    """, session_ids).fetchall()

    print(f"=== Sets logged today ({len(sets)} total) ===")
    for r in sets:
        print(f"  {r[0]}: Set {r[4]} - {r[1]} lb x {r[2]} reps @ RPE {r[3]}  (session {r[7]})")
else:
    print("No sessions found for today")

conn.close()
