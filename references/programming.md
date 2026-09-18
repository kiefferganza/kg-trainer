# Programming reference

Everything here is a default to reason from, not a prescription to copy. The profile beats the template every time: if the client has 45-minute sessions, the six-exercise day does not fit, and the template loses.

## Universal structure

**Mesocycle:** 4–6 accumulation weeks, then 1 deload. Volume rises across the accumulation weeks; intensity (proximity to failure) rises with it. The deload halves sets and stops at ~RIR 4–5 with the same exercises and roughly the same loads.

**RIR and RPE:** RIR = reps left in reserve. RPE = 10 − RIR. RPE 8 means 2 reps left. Use RIR when prescribing hypertrophy work and RPE when prescribing strength work — that is the convention the client will meet everywhere else, and mixing the two in one plan is how people end up training two RIR harder than intended. Whichever you pick, define it in the plan file.

**Exercise order:** the lift that most needs a fresh nervous system goes first. That is the heaviest compound in strength work and the primary target's most fatiguing movement in hypertrophy work. Isolation and machine work goes last, where accumulated fatigue costs the least.

**Warmups:** for the first heavy compound of a session, 3–4 ramping sets (empty bar × 8, ~40% × 5, ~60% × 3, ~80% × 1). One or two for the second compound. None for isolation work after the muscle is warm. Always write warmups into the plan — an unwritten warmup is an unperformed warmup.

**Stall rule:** if a lift fails to progress on load or reps for two consecutive sessions at the same prescribed RIR, drop the load 10% and build back. Do not let a client grind the same number for a month; that is how a plateau becomes an injury.

**Autoregulation:** the prescription is a target, not a contract. If the client hits the top of the range two reps early at the prescribed RIR, the load was too light — they add weight. If they miss the bottom of the range, they hold the load. Write this rule into the plan so the client can self-correct without waiting a week for you.

## Volume landmarks (sets per muscle per week)

| | Maintenance | Productive range | Upper bound for most |
|---|---|---|---|
| Chest, back, quads, hamstrings, glutes | 6–8 | 10–20 | ~22 |
| Shoulders (side delts), biceps, triceps, calves | 6 | 12–20 | ~25 |
| Rear delts, forearms, abs | 4 | 8–16 | ~20 |
| Traps, lower back (beyond compound work) | 2–4 | 4–10 | ~12 |

Start a new client's mesocycle near the **bottom** of the productive range and add sets across weeks. Starting at the top leaves nowhere to progress and buries recovery in week one. Count only hard sets (RIR ≤ 3); warmups do not count, and an indirect set (triceps in a bench press) counts as roughly half.

State the per-muscle weekly total in the plan file and say which range it sits in. A plan that claims "14–18 sets per muscle" without a per-muscle count is unfalsifiable — the client cannot check it and neither can you at the next check-in.

## Bodybuilding / hypertrophy

- **Frequency:** each muscle 2× per week. That is the setting where the volume above is achievable without 25-set sessions.
- **Rep ranges:** compounds 5–10, machines and isolations 10–20. Growth is similar across that span when sets are taken close enough to failure, so pick the range where the exercise is *good*: heavy rows beat heavy cable flyes, and 15-rep cable flyes beat 15-rep deadlifts.
- **Proximity to failure:** 1–3 RIR on compounds, 0–2 RIR on isolations. Last set of an isolation may go to failure. Compounds should not, routinely — the fatigue cost is out of proportion to the stimulus.
- **Exercise selection per muscle:** one movement loading the stretched position (incline curl, RDL, overhead triceps extension, chest-supported row) and one in the shortened or mid position (spider curl, leg curl, pushdown, cable row). Prefer movements with a good stimulus-to-fatigue ratio: high tension on the target, low systemic and joint cost.
- **Progression:** double progression on the rep range. Hit the top of the range for every prescribed set → add the smallest available load next session and restart at the bottom. Upper body +2.5 kg, lower body +5 kg, or one pin on a machine.

**Split by frequency:**

| Sessions/wk | Split |
|---|---|
| 3 | Full body ×3, or Push / Pull / Legs |
| 4 | Upper / Lower ×2 — the default, best volume-per-session balance |
| 5 | Upper / Lower / Push / Pull / Legs |
| 6 | Push / Pull / Legs ×2 |

## Powerlifting

- **Block structure:** hypertrophy block (4–6 wk, 65–75% 1RM, 6–12 reps) → strength block (4–6 wk, 75–87%, 3–6 reps) → peaking block (2–3 wk, 87–95%, 1–3 reps) → taper.
- **Frequency:** squat and bench 2–3×/wk, deadlift 1–2×/wk (it costs the most to recover from).
- **Prescription:** use percentage *and* an RPE cap — "5×3 @ 80%, cap RPE 8". The percentage sets the plan, the cap stops a bad day becoming a bad month.
- **Main-lift progression:** add load week to week within a block, reset at the next block with a higher starting percentage. Top single @ RPE 8 followed by back-off sets at 85–90% of that single is the workhorse scheme.
- **Accessories:** chosen against a named weak point (sticking point off the chest → close-grip and pause work; lockout → rack pulls and blocks). Write down which weakness each accessory targets, or it is filler.
- **Peaking:** last heavy session 7–10 days out; openers at ~92% about 10 days out; taper volume ~50% in the final week, keep intensity.

## Endurance

- **Intensity distribution:** polarized, ~80% of sessions easy (zone 2, conversational), ~20% hard (threshold/VO2max). The failure mode in self-coached endurance training is running the easy days too hard, so the plan must state a pace or HR ceiling for easy work, not just the word "easy".
- **Zones:** Z1 recovery, Z2 aerobic base (the bulk), Z3 tempo, Z4 threshold, Z5 VO2max. If the client has no lactate or FTP test, anchor zones to a recent race result or a max-HR estimate and say the zones are approximate.
- **Weekly shape:** one long session, one to two quality sessions (intervals or threshold), the rest easy. Never two quality sessions back to back.
- **Volume progression:** raise weekly volume ~10% at most, then hold or cut. Three build weeks to one recovery week (−30–40% volume).
- **Strength support:** 2 short sessions/wk, heavy and low-volume (3–5 sets, 3–6 reps), on the same day as a hard endurance session rather than on an easy day, to protect recovery days.
- **Hevy fit:** Hevy is a lifting log. Cardio maps onto `distance_duration` exercise types and loses pace/HR detail. Push the strength sessions to Hevy and keep the endurance prescription in the markdown plan.

## Hybrid / concurrent

Interference is real but manageable. Separate a hard lift and a hard run by at least 6 hours, or put them on different days. When both must share a session, do the one that matches the primary goal first. Cut total volume relative to a single-discipline plan — the client is recovering from both.

## Injuries and limitations

Substitute the pattern, do not delete the muscle. A client who cannot flat barbell bench can usually low-incline dumbbell press, use a neutral grip, or press in a machine's fixed path pain-free — the chest still gets trained. Rules:

1. Keep the target muscle trained through some pain-free range.
2. Change one variable at a time (grip, angle, implement, range) so you learn what the problem actually was.
3. Load below the previous working weight and slow the eccentric on the retest.
4. **Refer out** for radiating pain, numbness, tingling, night pain, or joint instability. Say plainly that this is past what a training plan should manage, and keep coaching the rest of the plan meanwhile.

## Diet: calories and macros

**Week 0 — estimate.** Mifflin-St Jeor BMR:
- male: `10 × kg + 6.25 × cm − 5 × age + 5`
- female: `10 × kg + 6.25 × cm − 5 × age − 161`

Multiply by daily activity (this covers life outside the gym, not the training itself): sedentary desk job 1.2, light activity or ~7k steps 1.375, moderate 1.55, physical job 1.725. Then apply the goal:

| Goal | Rate | Calorie offset |
|---|---|---|
| Build muscle (lean gain) | +0.25–0.5% BW/wk | +10–15% |
| Recomposition | ~0 | maintenance |
| Slight fat loss while training hard | −0.25–0.5% BW/wk | −10–15% |
| Fat loss priority | −0.5–1.0% BW/wk | −20–25% |

Macros: **protein** 1.6–2.2 g/kg (the top of that range in a deficit), **fat** 0.6–1.0 g/kg, **carbs** fill the remainder. Round to something a human can hit.

**Week 2 onward — measure, don't estimate.** Once there are two clean 7-day windows, the observed rate replaces the formula:

```
observed TDEE = reported intake − (rate_kg_per_week × 7700 / 7)
```

`scripts/checkin.py trend --calories N` computes this. A formula is a guess about a person; two weeks of weigh-ins is a measurement of *this* person. Prefer the measurement, while remembering that short-window weight change includes water and glycogen, so treat the first observed figure as a wide-error-bar estimate.

**Adjustment size:** ±150–250 kcal, one change at a time, then hold two weeks before touching it again. Protein stays fixed unless bodyweight moves materially; put the change into carbs, and into fat only if carbs are already low.
