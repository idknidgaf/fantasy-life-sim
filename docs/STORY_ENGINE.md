# Story engine

A generic, data-driven engine for branching stories. **Events are data**; the engine only validates and
executes them. There is no per-story Python or JavaScript, and event data can never contain code.

This is separate from the older YAML event system in `backend/app/data/events/` (see `CONTENT_GUIDE.md`),
which is untouched and keeps working. Story-engine content lives in `backend/app/data/story/*.json`
(currently empty: the engine ships as an empty container).

Check content with `cd backend && python -m app.validate_content`. The server also refuses to start on invalid
story content. A machine-readable schema, including every registered condition/effect/trigger type, is in
`docs/story_event.schema.json` (regenerate with `python -m app.story.export_schema`).

## Content files

    { "schema_version": 1, "events": [ <event>, ... ] }

Any number of `*.json` files; event ids must be unique across all of them.

## Event

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable id, `^[a-z0-9][a-z0-9_.-]{0,63}$`. Never reuse or rename; saves refer to it. |
| `version` | no (1) | Bump when the event changes. Instances remember the version they were created from. |
| `compatible_versions` | no | Earlier versions whose in-flight instances this version can still resolve (choice ids unchanged). |
| `event_type` | no (`event`) | `event`, or `node`: a step in a sequence, only reached via `next_event`/`start_event` (trigger must be `manual`). |
| `title`, `narrative` | yes | Text shown to the player. |
| `trigger` | no (`manual`) | When the event is offered, see below. |
| `conditions` | no | Eligibility: a condition tree that must hold for the event to be offered. |
| `lifecycle` | no | How often it may occur, see below. |
| `choices` | yes (1+) | At least one. |

### Choice

| Field | Meaning |
|---|---|
| `id` | Stable id, unique within the event. |
| `text` | Display text. |
| `conditions` | Optional; if unmet the choice is shown locked and cannot be submitted. |
| `locked_hint` | Optional text shown while locked. |
| `result` | Optional outcome text written to the history log (falls back to `text`). |
| `effects` | Optional list of effects, applied in order. |
| `next_event` | Optional event id to offer next. Several choices may name the same one (convergence); omit to end the sequence. |

### Lifecycle

    "lifecycle": {"mode": "once_per_life" | "repeatable" | "campaign_unique",
                  "max_occurrences": 3,       # repeatable only, required there
                  "cooldown_years": 2,        # repeatable only
                  "skippable": true,          # explicit exception to "an encounter stops time", see below
                  "expires_after_years": 5}   # needs skippable: true

* `once_per_life` (default): one occurrence per character life. `campaign_unique`: one per campaign, across lives.
* An *occurrence* is any instance ever created (pending, presented, resolved or expired), except instances retired
  because a content update made them unresolvable. So a missed or expired one-time event does not come back.
* Only one open (pending/presented) instance of an event can exist per life, in every mode.
* A choice may point back at its own event only if the event is `repeatable`.
* **Encounters stop time.** By default, once an event is triggered its window stays up, and the player cannot Age Up
  or act, until it is resolved. `skippable: true` is the explicit exception: the player may Age Up past it, and it is
  withdrawn (status `expired`) once `expires_after_years` have passed, or stays until resolved if that is omitted.
  `expires_after_years` without `skippable` is rejected, because a blocking event stops time and could never expire.

### Instance status

`pending` (created) → `presented` (the frontend fetched it) → `resolved` (a choice was made, or `complete_event`).
`pending`/`presented` → `expired` (timeout, or its definition was removed or changed incompatibly).

"World year" in all timestamps is the character's **age**, the game's only clock.

## Conditions

Leaf: `{"type": "<name>", ...params}`. Composites: `{"all": [...]}`, `{"any": [...]}`, `{"not": {...}}`
(`all`/`any` need at least one child; nesting is limited to 12 levels). Unknown types and unknown or missing
parameters are rejected.

| Type | Params | True when |
|---|---|---|
| `age_at_least` / `age_at_most` | `value` (int ≥ 0) | age ≥ / ≤ value |
| `trait_owned` / `trait_absent` | `trait` | the character has / lacks the trait |
| `flag_equals` | `flag`, `value` (bool/number/string) | the flag is set and equal (type-aware: `true` ≠ `1`) |
| `flag_at_least` | `flag`, `value` (number) | numeric flag ≥ value; unset counts as 0 |
| `event_completed` | `event` | an instance of the event was resolved in this life |
| `choice_was_made` | `event`, `choice` | that choice was selected in this life |

## Effects

`{"type": "<name>", ...params}`, applied in order inside the resolution transaction.

| Type | Params | Does |
|---|---|---|
| `set_flag` | `flag`, `value` | sets a story flag |
| `increment_flag` | `flag`, `amount` (default 1) | adds to a numeric flag (unset = 0). Non-numeric flag → error, whole resolution rolls back |
| `grant_trait` | `trait` | grants a trait using the game's trait rules (already owned or unmet prerequisites: no-op) |
| `start_event` | `event` | offers another event; its `conditions` and lifecycle still apply, else it is skipped |
| `complete_event` | `event` | marks the event completed without a choice (closes an open instance, or records a finished one) |

Story flags (`StoryFlag`) are separate from the legacy `CharacterRecord.flags` list, because they carry values.

## Triggers

`"trigger": {"type": ..., ...params}`. A trigger says *when to consider* an event; eligibility `conditions` and the
lifecycle still decide whether it is actually offered.

| Type | Params | Considered when |
|---|---|---|
| `manual` (default) | none | never automatically; started with `POST /api/story/events/{id}/start` or a `start_event` effect |
| `age` | `min_age`, `max_age?` | the turn advances and the new age is within the range |
| `action` | `action` | the player performs exactly that action id (e.g. `play`, `train:archery`) |
| `state` | none | the turn advances or an action is performed, and the event's `conditions` hold |

### Gameplay integration (`main.py`)

Every hook runs its scans for a living character **inside the same database transaction that saves the change**, so
an encounter is created in the very request that caused it, and a failed scan undoes the action too.

| When | Order of events | Scans (in order) |
|---|---|---|
| Player clicks **Age Up** (`POST /api/turn/advance`) | the character's age and state are updated first, then triggers are evaluated against the new state | `age`, `state` |
| Player performs an action (`POST /api/actions/{id}`) | action applied, then | `action` (with that action id), `state` |
| Legacy event resolved (`POST /api/events/.../choices/...`) | decision applied, then | `state` |
| Story choice resolved | effects and `next_event` applied, then | `state` (reported as `triggered_events`) |
| New life created (`POST /api/campaign`) | | `age`, `state` (the story can begin at birth) |

So an encounter never waits for the player's next action: the frontend refetches after each of these and the
window appears straight away. Eligible events become persistent `pending` instances; the legacy `pending_event` is
never changed by the story engine.

## Encounter window: one at a time

* A pending **legacy** event always goes first.
* **Story** events queue as pending instances: blocking events oldest first, then skippable ones. Only the head of the
  queue is presented (marked `presented`), and only while no legacy event is pending. The server enforces this:
  submitting a choice for an event that is not at the head, or while a legacy event is pending, returns 409.
* While any legacy event or **blocking** story event is open, `perform_action` and `advance_turn` return 409 and
  `GET /api/actions` is empty. Skippable story events block neither.
* The frontend shows the presented event in the same window as legacy events (`StoryEventPanel`, same card and
  choice buttons as `EventPanel`) in place of the normal Character/Life area, and disables the tabs and the Age Up
  button until it is resolved (except for skippable events, where Age Up stays available).
* With both pending: resolve the legacy event, then the head story event is presented, then the next, and so on.

## History log

Resolving a story choice appends an entry to the character's existing history log, kind `story`,
text `"<title>: <result or choice text>"`, `event_id` `"story:<event id>"` (namespaced so the legacy "once" check can
never confuse it with a legacy event). It appears in `GET /api/history` and the existing History panel. Independently
of that, every choice and its effect results are always persisted in `StoryChoiceLog` and on the instance,
whether or not anything is ever shown.

## Execution pipeline (`app/story/service.py`)

1. `library.py` loads and validates definitions (per-event schema plus cross-references: unique ids, `next_event`,
   `start_event`/`complete_event`, `event_completed`/`choice_was_made` targets, trait ids).
2. Eligibility: lifecycle + `conditions` (`startable_reason`).
3. `start_event` / `scan_triggers` create a `StoryEventInstance`.
4. `get_pending` presents open instances, with each choice's availability.
5. `resolve_choice` validates the instance belongs to this life and the choice exists on it, then evaluates the
   choice's conditions.
6. It claims the instance with a conditional `UPDATE ... WHERE status IN (open)`, so only one request can win.
7. Effects are applied, a `StoryChoiceLog` row is appended, and `next_event` (if any) is started.
8. One commit. Any error rolls back *all* of it, including the claim.

Repeating the choice that already resolved an instance returns the stored result with `"replayed": true` and
changes nothing; a different choice gets 409. The database backs this with a unique choice log per instance and a
unique open instance per (life, event). `StoryChoiceLog` is append-only (ORM updates and deletes raise).

Event conditions are checked when an instance is created, not again at resolution; choice conditions are checked at
resolution.

## API (`/api/story`, acts on the active campaign's character)

| Route | Result |
|---|---|
| `GET /state` | story flags and recent instances |
| `GET /events/pending` | the queue of open events, head first, each with `skippable` and choices (`available`, `locked_hint`). Only the head is marked `presented` (and only if no legacy event is pending); the frontend shows it |
| `POST /events/{event_id}/start` | 201 new instance; only for `manual` events. 404 unknown, 409 not allowed |
| `POST /instances/{instance_id}/choices/{choice_id}` | 200 resolution; 400 unknown choice, 404 no such instance, 409 locked / already resolved / expired / not this event's turn, 422 an effect failed (nothing changed) |

## Extending the engine

A new condition, effect or trigger type is one decorated function and a parameter model, no event file changes:

    class HasGoldParams(StrictParams):
        amount: int = Field(ge=0)

    @conditions.register("has_gold", HasGoldParams)
    def _has_gold(p, ctx):            # ctx: StoryContext (session, record, library, content)
        return ctx.record.counters.get("gold", 0) >= p.amount

Effects register on `effects` the same way (return a dict describing what happened; raise `StoryEffectError`
to abort and roll back; never commit). Triggers register on `triggers` with a `(params, ctx, payload) -> bool` handler.

## Adding an event

Create or edit a file in `backend/app/data/story/`, using only the types above, then run
`python -m app.validate_content`. No Python changes are needed.

## Not implemented (by design)

* Any story content, a graphical story editor, or rich story presentation in the frontend. Authoring stays plain JSON
  checked by `python -m app.validate_content`.
* Story events are not included in the death summary's `events_faced` count (legacy events only).
