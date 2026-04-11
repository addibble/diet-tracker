"""Phase 1: Data audit and exploration.

Analyzes the production DB to understand data coverage, quality, and
assigns exercises to fitting tiers.
"""

from collections import defaultdict
from data_loader import (
    get_connection,
    load_all_sets,
    load_bodyweight_history,
    load_exercises,
    load_exercise_tissues,
    effective_weight,
    nearest_bodyweight,
)


def run_data_audit(db_path=None):
    conn = get_connection(db_path)
    sets = load_all_sets(conn)
    bw = load_bodyweight_history(conn)
    exercises = load_exercises(conn)
    exercise_tissues = load_exercise_tissues(conn)

    print(f"=== DATA AUDIT ===")
    print(f"Total exercises: {len(exercises)}")
    print(f"Total workout sets: {len(sets)}")
    print(f"Bodyweight log entries: {len(bw)}")
    print()

    # -- Per-exercise inventory --
    ex_stats: dict[int, dict] = {}
    for s in sets:
        eid = s.exercise_id
        if eid not in ex_stats:
            ex_stats[eid] = {
                "name": s.exercise_name,
                "equipment": s.equipment,
                "total_sets": 0,
                "sets_with_rpe": 0,
                "sets_with_completion": 0,
                "rpe_values": [],
                "weights": set(),
                "reps_values": [],
                "dates": set(),
                "sessions": set(),
                "load_input_mode": s.load_input_mode,
            }
        st = ex_stats[eid]
        st["total_sets"] += 1
        if s.rpe is not None:
            st["sets_with_rpe"] += 1
            st["rpe_values"].append(s.rpe)
        if s.rep_completion is not None:
            st["sets_with_completion"] += 1

        # Track effective weight
        body_w = nearest_bodyweight(bw, s.session_date)
        ew = effective_weight(s, body_w)
        if ew is not None:
            st["weights"].add(round(ew, 1))
        if s.reps is not None:
            st["reps_values"].append(s.reps)
        st["dates"].add(s.session_date)
        st["sessions"].add(s.session_id)

    # -- Tier assignment --
    tiers = {"tier1": [], "tier2": [], "tier3": []}

    for eid, st in sorted(ex_stats.items(), key=lambda x: x[1]["total_sets"], reverse=True):
        distinct_weights = len(st["weights"])
        n_rpe = st["sets_with_rpe"]
        n_total = st["total_sets"]
        n_sessions = len(st["sessions"])
        date_span = ""
        if st["dates"]:
            dates_sorted = sorted(st["dates"])
            date_span = f"{dates_sorted[0]} -> {dates_sorted[-1]}"

        tier_label = "tier3"
        if n_rpe >= 8 and distinct_weights >= 2:
            tier_label = "tier1"
        elif n_rpe >= 5 or n_total >= 15:
            tier_label = "tier2"

        entry = {
            "exercise_id": eid,
            "name": st["name"],
            "equipment": st["equipment"],
            "total_sets": n_total,
            "sets_with_rpe": n_rpe,
            "sets_with_completion": st["sets_with_completion"],
            "distinct_weights": distinct_weights,
            "weight_range": f"{min(st['weights'])}-{max(st['weights'])}" if st["weights"] else "N/A",
            "rep_range": f"{min(st['reps_values'])}-{max(st['reps_values'])}" if st["reps_values"] else "N/A",
            "sessions": n_sessions,
            "date_span": date_span,
            "tier": tier_label,
            "load_input_mode": st["load_input_mode"],
        }
        tiers[tier_label].append(entry)

    # -- Print results --
    print("=" * 100)
    print(f"{'TIER ASSIGNMENT SUMMARY':^100}")
    print("=" * 100)
    print(f"  Tier 1 (full fit: >=8 RPE sets, >=2 weights): {len(tiers['tier1'])} exercises")
    print(f"  Tier 2 (partial fit: >=5 RPE or >=15 total):  {len(tiers['tier2'])} exercises")
    print(f"  Tier 3 (priors only: <5 RPE, <15 total):     {len(tiers['tier3'])} exercises")
    print()

    for tier_name, tier_entries in tiers.items():
        if not tier_entries:
            continue
        label = {"tier1": "TIER 1 -- Full Fit", "tier2": "TIER 2 -- Partial Fit", "tier3": "TIER 3 -- Priors Only"}[tier_name]
        print(f"\n{'-' * 100}")
        print(f"  {label} ({len(tier_entries)} exercises)")
        print(f"{'-' * 100}")
        header = f"  {'Exercise':<40} {'Sets':>5} {'RPE':>4} {'Wts':>4} {'Weight Range':<15} {'Reps':<10} {'Sessions':>8} {'Span'}"
        print(header)
        print(f"  {'-' * 95}")
        for e in sorted(tier_entries, key=lambda x: x["total_sets"], reverse=True):
            print(f"  {e['name']:<40} {e['total_sets']:>5} {e['sets_with_rpe']:>4} {e['distinct_weights']:>4} {e['weight_range']:<15} {e['rep_range']:<10} {e['sessions']:>8} {e['date_span']}")

    # -- RPE distribution --
    all_rpes = []
    for st in ex_stats.values():
        all_rpes.extend(st["rpe_values"])

    print(f"\n{'=' * 100}")
    print(f"{'RPE DISTRIBUTION':^100}")
    print(f"{'=' * 100}")
    if all_rpes:
        from collections import Counter
        rpe_counts = Counter(all_rpes)
        for rpe_val in sorted(rpe_counts.keys()):
            bar = "#" * (rpe_counts[rpe_val] // 2)
            print(f"  RPE {rpe_val:>4}: {rpe_counts[rpe_val]:>5}  {bar}")
        print(f"  Total sets with RPE: {len(all_rpes)}")
    else:
        print("  No RPE data found!")

    # -- Completion label audit --
    completion_counts = defaultdict(int)
    for s in sets:
        label = s.rep_completion or "none"
        completion_counts[label] += 1

    print(f"\n{'=' * 100}")
    print(f"{'COMPLETION LABEL DISTRIBUTION':^100}")
    print(f"{'=' * 100}")
    for label, count in sorted(completion_counts.items(), key=lambda x: -x[1]):
        bar = "#" * (count // 5)
        print(f"  {label:<12}: {count:>5}  {bar}")

    # -- Tissue mapping coverage --
    tissue_map_by_exercise = defaultdict(list)
    for et in exercise_tissues:
        tissue_map_by_exercise[et["exercise_id"]].append(et)

    exercises_with_mappings = len(tissue_map_by_exercise)
    exercises_used = len(ex_stats)
    unmapped = [
        ex_stats[eid]["name"]
        for eid in ex_stats
        if eid not in tissue_map_by_exercise
    ]

    print(f"\n{'=' * 100}")
    print(f"{'TISSUE MAPPING COVERAGE':^100}")
    print(f"{'=' * 100}")
    print(f"  Exercises with workout data: {exercises_used}")
    print(f"  Exercises with tissue mappings: {exercises_with_mappings}")
    if unmapped:
        print(f"  Exercises WITHOUT tissue mappings ({len(unmapped)}):")
        for name in sorted(unmapped):
            print(f"    * {name}")

    conn.close()
    return tiers, ex_stats


if __name__ == "__main__":
    run_data_audit()
