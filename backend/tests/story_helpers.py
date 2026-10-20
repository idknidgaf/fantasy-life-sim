"""Synthetic fixtures for the story engine tests. These are test data only, never game content."""
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app import engine as sim
from app.content import get_content
from app.models import Campaign, CharacterRecord
from app.story.context import StoryContext
from app.story.library import StoryLibrary, build_library


def memory_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def make_life(session: Session, seed=1, age=0, campaign: Campaign | None = None) -> CharacterRecord:
    if campaign is None:
        campaign = Campaign(name="t", seed=seed)
        session.add(campaign)
        session.flush()
    c = sim.new_character("Test", "human", seed, talent_chance=0)
    c.pending_event = None
    c.age = age
    rec = CharacterRecord.from_character(campaign.id, c)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return rec


def ev(event_id: str, choices=None, **kw) -> dict:
    """A minimal valid event dict."""
    return {"id": event_id, "title": event_id.title(), "narrative": "Something happens.",
            "choices": choices if choices is not None else [{"id": "ok", "text": "OK"}], **kw}


def library(*events: dict, content=None) -> StoryLibrary:
    return build_library([("test", e) for e in events], content or get_content())


def ctx_for(session, rec, lib=None) -> StoryContext:
    return StoryContext(session=session, record=rec, library=lib or library(), content=get_content())


def free_trait(rec: CharacterRecord) -> str:
    """A real trait the life does not own and that has no prerequisites."""
    ct = get_content()
    return next(t.id for t in ct.traits.values() if not t.prerequisites and t.id not in rec.traits)
