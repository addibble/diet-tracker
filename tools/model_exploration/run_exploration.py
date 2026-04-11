"""Main runner for model exploration.

Runs all phases sequentially:
  Phase 1: Data audit
  Phase 2: Strength curve fitting
  Phase 3: Session fatigue
  Phase 4: M(t) evolution
  Phase 5: Full validation

Usage:
  cd tools/model_exploration
  python run_exploration.py [--phase N]  # run from phase N onward
"""

import sys
import time
from pathlib import Path


def main():
    start_phase = 1
    if len(sys.argv) > 2 and sys.argv[1] == "--phase":
        start_phase = int(sys.argv[2])

    print("=" * 100)
    print("UNIFIED FATIGUE-STRENGTH MODEL -- STANDALONE EXPLORATION")
    print("=" * 100)
    print(f"Starting from phase {start_phase}")
    print()

    t0 = time.time()

    # Phase 1: Data audit
    if start_phase <= 1:
        print("\n" + "#" * 100)
        print("#  PHASE 1: DATA AUDIT")
        print("#" * 100)
        from data_audit import run_data_audit
        tiers, ex_stats = run_data_audit()
        tier_assignments = {}
        for tier_name, entries in tiers.items():
            for e in entries:
                tier_assignments[e["exercise_id"]] = tier_name
    else:
        tiers = None
        tier_assignments = None

    # Phase 2: Strength curve fitting
    if start_phase <= 2:
        print("\n" + "#" * 100)
        print("#  PHASE 2: STRENGTH CURVE FITTING")
        print("#" * 100)
        from strength_curve import run_batch_fitting
        curve_results = run_batch_fitting(tier_assignments=tier_assignments)
    else:
        curve_results = None

    # Phase 3: Session fatigue
    if start_phase <= 3:
        print("\n" + "#" * 100)
        print("#  PHASE 3: SESSION FATIGUE")
        print("#" * 100)
        from session_fatigue import run_fatigue_analysis
        replays, fitted_fatigue_params = run_fatigue_analysis(curve_results)
    else:
        fitted_fatigue_params = None

    # Phase 4: Strength evolution
    if start_phase <= 4:
        print("\n" + "#" * 100)
        print("#  PHASE 4: STRENGTH EVOLUTION M(t)")
        print("#" * 100)
        from strength_evolution import run_evolution_analysis
        evo_results = run_evolution_analysis(curve_results, fatigue_params=fitted_fatigue_params)
    else:
        evo_results = None

    # Phase 5: Full validation
    if start_phase <= 5:
        print("\n" + "#" * 100)
        print("#  PHASE 5: FULL END-TO-END VALIDATION")
        print("#" * 100)
        from full_validation import run_full_validation
        validations = run_full_validation(curve_results, evo_results, fitted_fatigue_params)

    # Generate plots
    print("\n" + "#" * 100)
    print("#  GENERATING PLOTS")
    print("#" * 100)
    if curve_results:
        from visualize import plot_curve_gallery, plot_strength_curve
        successful = [r for r in curve_results if r.success]
        successful.sort(key=lambda x: x.n_observations, reverse=True)

        print("  Generating curve gallery...")
        plot_curve_gallery(successful, top_n=len(successful))

        # Plot individual curves for ALL exercises
        for r in successful:
            print(f"  Plotting curve: {r.exercise_name}...")
            plot_strength_curve(r)

    if evo_results:
        from visualize import plot_strength_evolution
        for evo in sorted(evo_results, key=lambda x: x.n_training_days, reverse=True):
            if evo.timeline:
                dates = [p.date for p in evo.timeline]
                ms = [p.M for p in evo.timeline]
                recs = [p.recovery_pct for p in evo.timeline]
                print(f"  Plotting M(t): {evo.exercise_name}...")
                plot_strength_evolution(evo.exercise_name, dates, ms, recs)

    if curve_results and evo_results:
        from session_fatigue import replay_all_sessions, FatigueParams
        from visualize import plot_session_fatigue_from_replay
        print("  Generating session fatigue plots...")
        fat_params = fitted_fatigue_params or FatigueParams()
        all_replays = replay_all_sessions(curve_results, params=fat_params)
        # Plot sessions with >=3 sets and RPE data, grouped by exercise
        rpe_replays = [r for r in all_replays if r.n_rpe_sets >= 2 and r.n_sets >= 3]
        seen_exercises = set()
        for replay in sorted(rpe_replays, key=lambda x: x.n_rpe_sets, reverse=True):
            if replay.exercise_name in seen_exercises:
                continue
            seen_exercises.add(replay.exercise_name)
            print(f"  Plotting fatigue: {replay.exercise_name} ({replay.date})...")
            plot_session_fatigue_from_replay(replay)

    elapsed = time.time() - t0
    print(f"\n{'=' * 100}")
    print(f"COMPLETE -- Total time: {elapsed:.1f}s")
    print(f"{'=' * 100}")


if __name__ == "__main__":
    main()
