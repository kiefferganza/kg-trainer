# kg-trainer

## What it is

A Claude Code skill that acts as a personal trainer, coaching from the workouts you have actually logged. It reads your history from a training app, designs a 6–12 week block, sends it to that app, runs weekly weigh-in and diet check-ins, and builds a local dashboard.

It works with any training app that has an API and a kg-trainer adapter:

| App | Status |
|---|---|
| Liftosaur | Reads, plan push and update verified live. Needs Liftosaur premium for the API. |
| Hevy | Reads and plan push built and tested on recorded responses, not yet verified live. Needs Hevy Pro. |

Two rules hold throughout: every number comes from a logged set, a script or something you said, and every step writes its state to disk so next week builds on this one.

## How to set it up

1. **Install the skill.** Symlink it into your personal skills, then start a new Claude Code session.

   ```bash
   ln -sfn "$PWD" ~/.claude/skills/kg-trainer
   ```

2. **Add your app's API key.** Copy `.env.example` to `.env` in the project you open Claude Code in, fill in your app's key (`LIFTOSAUR_API_KEY` or `HEVY_API_KEY`), and `chmod 600 .env`. Liftosaur keys are under Settings → API Keys. An exported variable wins over `.env`; the key is never printed.

3. **Choose your app and check the key.** Run these from your project, never from this repo, because the scripts keep your data in `kg-trainer-data/` at your project's root.

   ```bash
   ~/.claude/skills/kg-trainer/scripts/app.py use liftosaur
   ~/.claude/skills/kg-trainer/scripts/app.py ping
   ```

   Add `kg-trainer-data/` to your project's `.gitignore`; the scripts warn until you do. With no app at all, the skill still works from numbers you give it, labelled as self-reported.

## Example usage

Tell Claude what you want and the skill walks the steps, stopping twice for your confirmation (once after it tells you what your data says, and once on the plan before anything is sent to your app):

> Build me a 10-week hypertrophy block. I train four days a week, an hour each.

After that, these keywords drive the ongoing coaching:

| Say | What happens |
|---|---|
| `UPDATE` | Re-pulls your history and rebuilds the dashboard |
| `HOW AM I DOING` | Compares logged sets against the plan and says which lifts are ready to go up |
| `PROGRESS IT` | Works out next week's loads from what you logged and sends the update on your yes |
| `IT HURTS` | Swaps the exercises that aggravate it while keeping the muscle trained |
| `NEXT BLOCK` | Designs the next block from everything logged since |
| `check-in` | Weekly weigh-in trend, one adjustment to calories or training, recorded |

The scripts also run on their own; each takes `--help`:

```bash
~/.claude/skills/kg-trainer/scripts/app.py pull            # cache every workout and weigh-in
~/.claude/skills/kg-trainer/scripts/analyze.py report      # baseline tables
~/.claude/skills/kg-trainer/scripts/plan.py stats kg-trainer-data/plans/meso-01.plan.json
~/.claude/skills/kg-trainer/scripts/dashboard.py build --open
```

Programming, load management and calorie targets are in scope; diagnosing injuries is not. Nothing here is medical or dietetic advice.
