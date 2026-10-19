import random

from app import engine as sim
from app.content import get_content


def make(species="human", seed=1, **overrides):
    """A newborn with no hidden talent, optionally with attributes overridden."""
    c = sim.new_character("Test", species, seed, talent_chance=0)
    c.pending_event = None                      # tests set up their own scenes
    for key, value in overrides.items():
        setattr(c, key, value)
    return c


def play_life(seed, species="human", talent_chance=sim.TALENT_BIRTH_CHANCE, max_steps=5000):
    """Live a whole life, choosing randomly among available options."""
    ct = get_content()
    c = sim.new_character("Test", species, seed, talent_chance=talent_chance)
    pick = random.Random(f"{seed}:player")
    for _ in range(max_steps):
        if not c.alive:
            return c
        if c.pending_event:
            options = [o for o in sim.event_choice_state(c, ct, c.pending_event) if o["available"]]
            sim.resolve_event(c, seed, c.pending_event, pick.choice(options)["id"])
        else:
            actions = sim.offer_actions(c, seed)
            if actions and pick.random() < 0.9:
                sim.perform_action(c, seed, pick.choice(actions)["id"])
            sim.advance_turn(c, seed)
    raise AssertionError("character never died")
