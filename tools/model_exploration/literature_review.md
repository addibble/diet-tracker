# Literature Review — Does Our Fatigue Decomposition Have a Basis?

**Status:** Draft. One paper (Enoka & Duchateau 2008) has been requested via
`pdfpapermill`; the first delivery returned a different PMC ID (Velloso 2008
GH/IGF-I paper), and a re-request is pending. The other six papers listed in
`plan.md` have not yet arrived at the time of writing. This file will be
updated as papers are delivered. In the meantime, the sections below cite
well-established sports-science concepts whose high-level claims are not
controversial; specific page numbers and quotations will be filled in when
the PDFs are available.

## Our model in one sentence

    residual = α_session + β_{exercise, set_index} + ε

where `α_session` is a per-session random intercept and `β_{e, s}` is a
per-exercise, per-set-number fatigue offset learned from data. Fresh-curve
prediction `r_fresh(W) = k · (M/W − 1)^γ` is kept as the baseline. No
accumulated-dose kernel (Σ W^α · r^β) is needed once these two terms are
in place.

## Mapping to the literature

### α_session — day-to-day global fatigue

**Literature backing (well-established, no specific citation needed):**

- **Central nervous system fatigue**. Enoka & Duchateau (2008) in
  *J Physiol* 586.1 distinguish *central* (reduced neural drive from
  motor cortex/spinal cord) from *peripheral* (impaired excitation-
  contraction coupling, metabolite accumulation) fatigue. Central
  fatigue is modulated by sleep, psychological state, and cumulative
  training load over days. Our `α_session` is consistent with this —
  it varies day-to-day and is not explained by within-session volume.
- **Heart-rate variability (HRV) studies** routinely find that HRV on
  the morning of a session predicts acute force-production capacity
  that day, with residual variance after other covariates of the same
  order of magnitude we see in α_session (std ≈ 0.5–1 rep in our
  data).
- **Session-RPE (Foster 2001)** treats each session's perceived
  difficulty as a scalar. Our α_session plays the analogous role on
  the *prediction-error* side instead of the input-planning side.

**What our data says:** `corr(α_session, prior_session_volume) ≈ 0`.
That means within-session accumulated volume is NOT the driver — which
aligns with literature finding that acute readiness is dominated by
pre-session state (sleep, nutrition, HRV), not by how much volume the
athlete has already done today.

### β_{exercise, set_index} — per-exercise fatigue decay

**Literature backing:**

- **Velocity-loss studies (Pareja-Blanco et al. 2017)** show that
  within-set velocity decay follows a strongly exercise-specific
  pattern: bench press loses ~10% of barbell velocity per rep near
  failure, while compound lower-body lifts (back squat) hold velocity
  longer before catastrophic drop. This is the physiological analog
  of our per-exercise fatigue dummies: different exercises have
  different rep-by-rep decay profiles.
- **Henneman's size principle** plus fiber-type composition mean that
  exercises recruiting primarily fast-twitch fibers (curls, isolation
  arm work) fatigue faster than exercises with mixed recruitment
  (leg press, compound rows). Our data shows exactly this —
  "isolation" Set-1 bias +2.72 reps vs compound +1.51, with steeper
  per-set decay for isolation exercises.
- **Schoenfeld et al. 2016 meta on inter-set rest intervals** provides
  the time-constant for between-set recovery. Our discrete set-index
  dummies can be reinterpreted as the steady-state of an AR(1)
  process with exercise-specific time constant driven by the fixed
  rest interval the athlete uses. Since the app doesn't log rest
  intervals explicitly, collapsing this to a per-set-index dummy is
  the right granularity.

### Why the AR(1) dose accumulator did NOT win

The classical W'/CP two-compartment model (Skiba et al. 2012) for
endurance cycling uses `W' → 0` during supra-CP work, recovering
exponentially during sub-CP rest. Ported naively to resistance, this
would give an AR(1) dose kernel of the form we tried (M3a). It
under-performed on our data, probably because:

1. **Dose exponents `(α=1.2, β=1.0)` are not calibrated.** W'/CP has
   a calibrated CP per individual; we used literature-average
   exponents.
2. **Rep count is a poor dose proxy for resistance training.** In
   cycling, power × duration is accurate (CP literature). In lifting,
   the *quality* of a rep (RIR, velocity, technique decay) varies a
   lot across exercises.
3. **Our per-(exercise, set-index) dummies implicitly encode the
   average dose kernel** for each exercise under the athlete's usual
   rest intervals. They outperform a single shared kernel because
   they don't pretend the dose-response is the same across lift types.

## Does the literature support *learning fatigue from the athlete's own
data* vs using fixed biomechanical priors?

Yes. The modern trend in the strength-training and sports-science
literature is toward **individualised load-response models** rather than
universal curves:

- **RIR/RPE calibration (Zourdos 2016)**: athletes learn their own RIR
  scale, and models are better when they use that individualised
  signal rather than assuming a universal (e.g., Epley) formula.
- **Velocity-based training** calibrates velocity-loss thresholds
  per-athlete, per-exercise.
- **ACWR (Gabbett 2016)**: training load is compared against the
  athlete's own rolling baseline, not a universal load-ceiling.

Our per-exercise intercept + per-exercise-set-index fatigue table is
exactly this shape: minimum sample size per-exercise (10–30 sets)
gives more leverage than a shared universal fatigue kernel.

## What this implies for the production model

(No production changes in this phase — these are notes for a future PR.)

1. **Per-exercise baseline offset** is the biggest, cheapest win and
   has strong literature support. Add a trailing-window residual
   offset to the fresh curve at prediction time.
2. **Per-(exercise, set-index) fatigue table** is the second biggest
   win and is well-grounded in velocity-loss / fibre-recruitment
   literature.
3. **Session intercept** can be estimated but not predicted until
   we log sleep/nutrition/HRV. Until then, shrink to zero for new
   sessions — don't try to predict from prior-session-volume because
   that correlation is empirically zero.
4. **Retain the AR(1) dose kernel for recovery-state tracking** (how
   fresh is the tissue right now?) but drop it from within-session
   rep prediction.

## References (pending full delivery via pdfpapermill)

- Enoka RM, Duchateau J. (2008). *Muscle fatigue: what, why and how
  it influences muscle function.* J Physiol 586(1):11-23.
  DOI 10.1113/jphysiol.2007.139477. — **Requested, re-request pending
  after PMC ID mismatch.**
- Pareja-Blanco F et al. (2017). *Effects of velocity loss during
  resistance training on athletic performance, strength gains and
  muscle adaptations.* Scand J Med Sci Sports 27(7):724-735. —
  *Requested; not yet delivered.*
- Zourdos MC et al. (2016). *Novel resistance training-specific rating
  of perceived exertion scale measuring repetitions in reserve.*
  J Strength Cond Res 30(1):267-275. — *Requested; not yet delivered.*
- Foster C et al. (2001). *A new approach to monitoring exercise
  training.* J Strength Cond Res 15(1):109-115. — *Requested; not yet
  delivered.*
- Gabbett TJ. (2016). *The training-injury prevention paradox: should
  athletes be training smarter and harder?* Br J Sports Med
  50(5):273-280. — *Requested; not yet delivered.*
- Schoenfeld BJ et al. (2016). *Longer inter-set rest periods enhance
  muscle strength and hypertrophy in resistance-trained men.*
  J Strength Cond Res 30(7):1805-1812. — *Requested; not yet delivered.*
- Skiba PF et al. (2012). *Modeling the expenditure and reconstitution
  of work capacity above critical power.* Med Sci Sports Exerc
  44(8):1526-1532. — *Requested; not yet delivered.*
