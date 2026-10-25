# Social Actions: the yearly budget

Every playable character gets **3 Social Actions per in-game year**, a budget of its own, separate from the Life actions
(the life stage's `action_count`). This task adds only the budget: there is no socialising content yet, so starting a
social action spends one and does nothing else (no event, no history entry, no NPC changes).

## Rules (all in `backend/app/engine.py`, backend-authoritative)

| | |
|---|---|
| Budget | `SOCIAL_ACTIONS_PER_YEAR = 3`, the same at every life stage; none for the dead (`social_budget`, `social_remaining`) |
| Start | `POST /api/social/start` with optional `{"request_id": "..."}`. Spends one. `409` when none remain, the character has died, or an event needs a decision. The balance cannot go below zero |
| Retry safety | A `request_id` already processed this year spends nothing more and still returns `200`, even if the budget has since run out. The UI sends a fresh id per click. Ids are 1-64 of `A-Za-z0-9_.:-`; a bad one is `422` |
| New year | `advance_turn`, the one Age Up transition, sets `social_actions_used = 0` (and clears the year's request ids) in the same step that clears `actions_taken`. It *sets*, never adds, so repeating it cannot overfill the budget. A refused Age Up (event pending, dead) changes nothing |
| Persistence | `CharacterRecord.social_actions_used` and `social_requests`. A save from before this gets both columns in place (`init_db`) and a full budget. `repair` brings a corrupt stored value into 0-3 |
| Heirs | A new playable life is a new character, so it starts with a full budget |
| Independence | Social actions never touch `actions_taken` or the Life budget, and Life actions never touch the social budget. `GET /api/actions` is unchanged: it still lists Life actions only, and still becomes empty when those are spent, while Socialise stays available |

`CharacterRead` carries `social_budget` and `social_remaining`.

## UI

The Life tab has a "Social" section: `Social Actions: 2/3` and a **Socialise** button (disabled at 0 or while busy). It is
outside the Life-actions list, so it stays usable when every Life action is spent. It is a placeholder entry point only.

The existing Life action **Socialize** (Lifestyle, +happiness, costs a Life action) is unchanged; it is a different thing.
