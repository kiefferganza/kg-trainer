# KG Trainer

An AI personal trainer that coaches from the athlete's own logged training data, pulled from whatever training app they use, as long as that app has an API.

## Apps and adapters

**Training App**:
A third-party app where the athlete logs workouts and, optionally, receives their program (Liftosaur, Hevy).
_Avoid_: Backend, provider, platform

**Active App**:
The one Training App a store is connected to. All history, bodyweight and plan pushes go through it; a store never mixes apps.
_Avoid_: Primary app, source

**Adapter**:
The skill's translation layer for one Training App, written against the Adapter Contract and tested like any other script.
_Avoid_: Connector, integration, client, driver

**Adapter Contract**:
The fixed set of operations every Adapter offers or declines, defined by the skill rather than by any one app.
_Avoid_: Interface, API, protocol

**Capability**:
One operation in the Adapter Contract that an Adapter may or may not support. Reading workout history is the only required Capability.
_Avoid_: Feature, permission

**Fallback**:
The named, skill-owned way of covering a Capability the Active App lacks, such as a markdown plan the athlete enters by hand when the app cannot receive a push.
_Avoid_: Workaround, degraded mode

## Plans and exercises

**Canonical Plan**:
The app-neutral source of truth for a training block: weeks, days, exercises, prescriptions and Progression Rules. Adapters compile it into whatever the Active App understands.
_Avoid_: Program text, routine, Liftoscript (that is one compiled form)

**Progression Rule**:
How an exercise's prescription changes from session to session, written once in the Canonical Plan. The Active App runs it natively when it can; otherwise the coach applies it at check-in.
_Avoid_: Progression scheme, auto-progression

**Native Rule**:
A Progression Rule written in one Training App's own language, valid only when that app is the Active App. Under any other app it is refused, never ignored.
_Avoid_: Custom rule, escape hatch

**Workout Log**:
The athlete's logged sessions in the skill's own shape, weights in kilograms and exercises under Canonical Exercise Names, whichever app they came from.
_Avoid_: History, workouts cache

**Canonical Exercise Name**:
The skill's own name for an exercise, carrying equipment as a suffix (`Bicep Curl, Cable`). Muscle mapping, photos and lift analysis key on it; Adapters translate app names to and from it.
_Avoid_: Exercise title, app exercise name
