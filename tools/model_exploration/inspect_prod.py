"""Quick inspection of the 2026-04-19 prod DBs."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent / "prod_dbs_2026-04-19"
ATHLETES = {
    "Drew": "3d91e0e958b64d8eae86fdde4ff72783",
    "Haewon": "217a656ff91b4a5481da5353110cd29c",
}


def main() -> None:
    for name, uid in ATHLETES.items():
        db = next((ROOT / uid).glob("*.db"))
        c = sqlite3.connect(db)
        cur = c.cursor()
        tables = [r[0] for r in cur.execute(
            "select name from sqlite_master where type='table' order by name"
        ).fetchall()]
        set_table = next((t for t in tables if t.lower() == "workout_sets" or t.lower() == "workoutset"), None)
        if set_table is None:
            # Fallback: any table with both rpe and reps columns
            for t in tables:
                cols = [c[1] for c in cur.execute(f"pragma table_info({t})").fetchall()]
                if "rpe" in cols and "reps" in cols:
                    set_table = t
                    break
        print(f"\n== {name} ({uid[:8]}) ==")
        print(f"  tables: {len(tables)}  set_table: {set_table}")
        if set_table:
            cols = [c[1] for c in cur.execute(f"pragma table_info({set_table})").fetchall()]
            sess_col = "workout_session_id" if "workout_session_id" in cols else "session_id"
            n_total = cur.execute(f"select count(*) from {set_table}").fetchone()[0]
            n_rpe = cur.execute(
                f"select count(*) from {set_table} where rpe is not null and reps > 0"
            ).fetchone()[0]
            n_ex = cur.execute(
                f"select count(distinct exercise_id) from {set_table} where rpe is not null"
            ).fetchone()[0]
            n_ses = cur.execute(
                f"select count(distinct {sess_col}) from {set_table} where rpe is not null"
            ).fetchone()[0]
            print(f"  {set_table}: {n_total} rows, {n_rpe} RPE-valid, "
                  f"{n_ex} exercises, {n_ses} sessions")
        c.close()


if __name__ == "__main__":
    main()
