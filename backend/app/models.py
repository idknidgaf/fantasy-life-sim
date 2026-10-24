from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, event, text
from sqlmodel import JSON, Column, Field, SQLModel

from .character import Character


def _now() -> datetime:
    return datetime.now(timezone.utc)


JSON_FIELDS = ("stats", "skills", "axes", "traits", "flags", "counters", "items", "history",
               "queued_events", "actions_taken")
DICT_FIELDS = ("stats", "skills", "axes", "counters", "items")
PLAIN_FIELDS = ("name", "species_id", "age", "alive", "location", "cause_of_death",
                "health", "stress", "happiness",
                "pending_event", "hidden_talent", "talent_state")


class Campaign(SQLModel, table=True):
    """A saved game. Exactly one campaign is 'active' at a time."""
    id: int | None = Field(default=None, primary_key=True)
    name: str
    seed: int
    active: bool = True
    created_at: datetime = Field(default_factory=_now)
    # The world's clock, in years: it moves once for every year the playable character ages, and NEVER goes back, not
    # even when an heir takes over at an older age. NPCs are brought up to it exactly once per year (npc_world.py).
    world_year: int = 0


class CharacterRecord(SQLModel, table=True):
    """The character living inside a campaign.

    hidden_talent / talent_state are written once at creation and afterwards only change when
    the talent awakens. Loading a save never rolls anything.
    """
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    name: str
    species_id: str = "human"
    age: int = 0
    alive: bool = True
    location: str = "village"
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    skills: dict = Field(default_factory=dict, sa_column=Column(JSON))
    axes: dict = Field(default_factory=dict, sa_column=Column(JSON))
    traits: list = Field(default_factory=list, sa_column=Column(JSON))
    flags: list = Field(default_factory=list, sa_column=Column(JSON))
    counters: dict = Field(default_factory=dict, sa_column=Column(JSON))
    items: dict = Field(default_factory=dict, sa_column=Column(JSON))
    history: list = Field(default_factory=list, sa_column=Column(JSON))
    queued_events: list = Field(default_factory=list, sa_column=Column(JSON))
    actions_taken: list = Field(default_factory=list, sa_column=Column(JSON))
    cause_of_death: str | None = None
    health: int | None = None
    stress: float = 0.0
    happiness: float = 50.0
    pending_event: str | None = None
    hidden_talent: str | None = None
    talent_state: str = "none"

    @classmethod
    def from_character(cls, campaign_id: int, c: Character) -> "CharacterRecord":
        rec = cls(campaign_id=campaign_id, name=c.name)
        rec.update_from(c)
        return rec

    def to_character(self) -> Character:
        data = {f: getattr(self, f) for f in PLAIN_FIELDS}
        for f in JSON_FIELDS:
            value = getattr(self, f)
            data[f] = value.copy() if value is not None else ({} if f in DICT_FIELDS else [])
        return Character(**data)

    def update_from(self, c: Character) -> None:
        for f in PLAIN_FIELDS:
            setattr(self, f, getattr(c, f))
        for f in JSON_FIELDS:                       # fresh objects, so SQLAlchemy sees the change
            value = getattr(c, f)
            setattr(self, f, value.copy())


# ---------------------------------------------------------------- story engine
# Generic, data-driven story state. Event *definitions* live in JSON content files (app/data/story),
# never in the database; these tables hold only what happened to one character life. The "world
# year" recorded on every row is the character's age, which is the game's only clock.

OPEN_STATUSES = ("pending", "presented")


class StoryEventInstance(SQLModel, table=True):
    """One occurrence of an event definition in one life.

    status: pending -> presented -> resolved, or pending/presented -> expired.
    event_version is the definition version the instance was created from (save compatibility).
    """
    __table_args__ = (
        # At most one open instance of an event per life: a backstop against duplicate creation.
        Index("uq_story_open_instance", "character_id", "event_id", unique=True,
              sqlite_where=text("status IN ('pending','presented')"),
              postgresql_where=text("status IN ('pending','presented')")),
    )
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id", index=True)
    event_id: str = Field(index=True)
    event_version: int = 1
    status: str = Field(default="pending", index=True)
    origin: str = "manual"                       # manual | trigger:<type> | start_event:<id> | next:<id>
    parent_instance_id: int | None = Field(default=None, foreign_key="storyeventinstance.id")
    created_year: int = 0
    presented_year: int | None = None
    resolved_year: int | None = None
    expires_year: int | None = None
    end_reason: str | None = None                # expired: timeout | version_changed; resolved: choice | effect
    selected_choice_id: str | None = None
    outcome: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    presented_at: datetime | None = None
    resolved_at: datetime | None = None


class StoryChoiceLog(SQLModel, table=True):
    """Append-only record of every choice ever made. Rows are never updated or deleted by the engine."""
    __table_args__ = (UniqueConstraint("instance_id", name="uq_story_choice_instance"),)
    id: int | None = Field(default=None, primary_key=True)
    instance_id: int = Field(foreign_key="storyeventinstance.id", index=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id", index=True)
    event_id: str = Field(index=True)
    event_version: int = 1
    choice_id: str
    world_year: int = 0
    effects: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class StoryFlag(SQLModel, table=True):
    """A persistent story flag (bool / number / string) belonging to one character life."""
    __table_args__ = (UniqueConstraint("character_id", "key", name="uq_story_flag"),)
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id", index=True)
    key: str
    value: bool | int | float | str | None = Field(default=None, sa_column=Column(JSON))
    updated_year: int = 0


# ---------------------------------------------------------------- combat

class CombatRecord(SQLModel, table=True):
    """One resolved fight, written in the same transaction as its consequences. It is both the audit trail
    (what was rolled, whether a flee was attempted and how it went) and the saved battle summary: the summary
    stays here until the player dismisses it, so a reload shows it again instead of losing it, and nothing
    is ever recalculated."""
    __table_args__ = (
        # A story instance resolves once, so it can have only one record: a backstop against duplicates.
        UniqueConstraint("instance_id", name="uq_combat_instance"),
    )
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id", index=True)
    source: str = "legacy"                       # legacy | story
    event_id: str
    instance_id: int | None = Field(default=None, foreign_key="storyeventinstance.id")
    choice_id: str
    age: int = 0
    outcome: str                                 # victory | defeat | fled
    flee_attempted: bool = False
    flee_succeeded: bool | None = None
    summary: dict = Field(default_factory=dict, sa_column=Column(JSON))
    acknowledged: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_now)


@event.listens_for(StoryChoiceLog, "before_update")
@event.listens_for(StoryChoiceLog, "before_delete")
def _choice_log_is_append_only(mapper, connection, target):
    raise RuntimeError("StoryChoiceLog is append-only")


# ---------------------------------------------------------------- NPCs

class NpcRecord(SQLModel, table=True):
    """A saved NPC (persistent or named; generic NPCs are temporary and are never saved, see npc_store.py).

    The whole `Npc` lives in `data` as JSON, so the optional attribute sections need no columns of their own and
    the playable-character table is untouched. The other columns only exist so NPCs can be found by category,
    status, species or name without opening every blob. `npc_id` is the stable identity; the name is not.
    A new table, so existing saves need no migration: create_all adds it.
    """
    __table_args__ = (UniqueConstraint("campaign_id", "npc_id", name="uq_npc_campaign_id"),)
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    npc_id: str = Field(index=True)
    category: str = Field(index=True)
    status: str = Field(index=True)
    species_id: str
    name: str
    age: int = 0
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))


class KnownNpc(SQLModel, table=True):
    """The persistent record that the player's family has met an NPC. It is what makes an NPC "known" to the Character
    tab (see npc_api.py): generating an NPC, even saving one, does not. Scoped to the campaign (the family line), not to one
    playable life, so an heir still knows who their parent knew. One row per NPC (unique), so recording it twice is harmless.
    A new table, so existing saves need no migration."""
    __table_args__ = (UniqueConstraint("campaign_id", "npc_id", name="uq_known_npc"),)
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    npc_id: str = Field(index=True)
    source: str = "encounter"                    # encounter | story | birth | manual: how they were first met
    first_met_year: int = 0                      # the campaign's world year
    created_at: datetime = Field(default_factory=_now)
    # The player's PERSONAL relationship with this NPC (see awareness.py). Awareness above belongs to the family line;
    # this belongs to one playable life, named by `holder` (a `pc_<n>` ref). Read for anyone else it means "none":
    # an heir is aware of who their parent knew, but is not their parent's friend.
    holder: str | None = None
    acquainted: bool = False                     # an established personal relationship (Acquaintances)
    closeness: int | None = None                 # 0-100 while acquainted, else None
    stance: str = "open"                         # open | hostile | refused: whether further contact is possible
    acquainted_year: int | None = None           # the world year it was established


class CharacterRelationship(SQLModel, table=True):
    """One link between two characters, by their stable ids (see relationships.py, which validates every write).

    parent / mentor are directed: source is the parent (or mentor), target the child (or mentee). partner, friend
    and rival are symmetric and stored once with the two ids in sorted order, so (a, b) and (b, a) are one row.
    An endpoint is a persisted NPC's id, or `pc_<n>` for the campaign's playable character.

    The rules a database cannot express (two parents at most, no circular parentage, both ends exist) are checked in
    code. The unique constraint makes a retried write harmless, and the check constraint is a backstop against a
    character being related to itself. Foreign keys are not enforced by SQLite here, which is why deleting a
    character or a campaign removes its links explicitly instead of relying on the database.
    A new table, so existing saves need no migration.
    """
    __table_args__ = (
        UniqueConstraint("campaign_id", "kind", "source_id", "target_id", name="uq_relationship_edge"),
        CheckConstraint("source_id <> target_id", name="ck_relationship_not_self"),
        Index("ix_relationship_source", "campaign_id", "source_id", "kind"),     # a character's children, mentees...
        Index("ix_relationship_target", "campaign_id", "target_id", "kind"),     # a character's parents, mentors...
    )
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id")
    kind: str
    source_id: str
    target_id: str


# ---------------------------------------------------------------- legacy: lives and the playable role
# Both are new tables, so existing saves need no migration (create_all adds them).

class PlayableRole(SQLModel, table=True):
    """Which character the player controls in a campaign: a role held by one existing CharacterRecord, not a copy.

    One row per campaign (unique). Switching the playable character means pointing this row at another record.
    With no row (every save from before legacy existed) the campaign's newest record is the playable one."""
    __table_args__ = (UniqueConstraint("campaign_id", name="uq_playable_role_campaign"),)
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id")
    changed_at: datetime = Field(default_factory=_now)


class LifeRecord(SQLModel, table=True):
    """A completed life. It only points at the dead CharacterRecord, which already holds the identity, final
    attributes, history and cause of death (and the family links keep naming it), so nothing is copied.

    One row per dead character (unique): a death is recorded once however often it is processed. Choosing an heir
    fills successor_npc_id first, as the claim that makes a second attempt fail, then successor_character_id.
    successor_npc_id is kept because the heir's NPC row is retired once it has become the playable character,
    and it is what lets a repeated request recognise the choice it already carried out."""
    __table_args__ = (UniqueConstraint("character_id", name="uq_life_character"),)
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id")
    successor_character_id: int | None = Field(default=None, foreign_key="characterrecord.id")
    successor_npc_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None
