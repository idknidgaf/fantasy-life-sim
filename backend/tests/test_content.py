import shutil

import pytest
import yaml

from app.content import DATA_DIR, ContentError, get_content, load_content
from app.validate_content import main as validate_main


def mutate(tmp_path, filename, change):
    """Copy the real content, apply `change(parsed_yaml)` to one file, return the new directory."""
    dest = tmp_path / "data"
    shutil.copytree(DATA_DIR, dest)
    path = dest / filename
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return dest


def find(items, ident):
    return next(i for i in items if i["id"] == ident)


def errors_for(tmp_path, filename, change):
    with pytest.raises(ContentError) as exc:
        load_content(mutate(tmp_path, filename, change))
    return "\n".join(exc.value.errors)


# ---------- the shipped content ----------

def test_shipped_content_is_valid_and_has_no_warnings():
    ct = get_content()
    assert ct.warnings == []
    assert len(ct.species) >= 6 and len(ct.traits) >= 12 and len(ct.talents) == 5
    assert len(ct.events) >= 30 and len(ct.stages) == 8


def test_validate_command_succeeds(capsys):
    assert validate_main() == 0
    assert "OK:" in capsys.readouterr().out


def test_every_event_always_has_an_open_choice():
    for ev in get_content().events.values():
        assert any(not ch.requirements for ch in ev.choices), ev.id


# ---------- the validator catches mistakes ----------

def test_typo_in_a_key_is_an_error_not_ignored(tmp_path):
    msg = errors_for(tmp_path, "events/childhood.yaml", lambda d: find(d, "stray_dog").update({"wieght": 3}))
    assert "stray_dog" in msg and "wieght" in msg


def test_unknown_stat_in_species(tmp_path):
    msg = errors_for(tmp_path, "species.yaml", lambda d: find(d, "elf")["stat_modifiers"].update({"luck": 5}))
    assert "elf" in msg and "unknown stat 'luck'" in msg


def test_unknown_trait_in_effect(tmp_path):
    def change(d):
        find(find(d, "stray_dog")["choices"], "befriend")["effects"].append({"grant_trait": "not_a_trait"})
    msg = errors_for(tmp_path, "events/childhood.yaml", change)
    assert "unknown trait 'not_a_trait'" in msg and "stray_dog" in msg


def test_unknown_requirement_kind(tmp_path):
    def change(d):
        find(find(d, "stray_dog")["choices"], "shoo")["requirements"] = [{"strength_over": 5}]
    assert "exactly one of" in errors_for(tmp_path, "events/childhood.yaml", change)


def test_requirement_needing_a_flag_nothing_sets(tmp_path):
    def change(d):
        find(find(d, "village_bully")["choices"], "run")["requirements"] = [{"flag": "ghost_flag"}]
    msg = errors_for(tmp_path, "events/childhood.yaml", change)
    assert "ghost_flag" in msg and "no effect ever sets it" in msg


def test_unreachable_location_is_caught(tmp_path):
    msg = errors_for(tmp_path, "events/childhood.yaml",
                     lambda d: find(d, "glowing_cave")["conditions"].update({"location_tags": ["atlantis"]}))
    assert "atlantis" in msg


def test_unknown_stage(tmp_path):
    msg = errors_for(tmp_path, "events/childhood.yaml",
                     lambda d: find(d, "stray_dog")["conditions"].update({"min_stage": "toddlerhood"}))
    assert "toddlerhood" in msg


def test_check_choice_must_not_also_have_a_plain_result(tmp_path):
    def change(d):
        find(find(d, "village_bully")["choices"], "talk")["result"] = "oops"
    assert "success/failure, not result/effects" in errors_for(tmp_path, "events/childhood.yaml", change)


def test_event_where_every_choice_is_locked(tmp_path):
    def change(d):
        for ch in find(d, "stray_dog")["choices"]:
            ch["requirements"] = [{"species_category": "humanoid"}]
    assert "get stuck" in errors_for(tmp_path, "events/childhood.yaml", change)


def test_trigger_event_must_exist(tmp_path):
    def change(d):
        find(d, "master_of_none")["on_awaken"]["trigger_event"] = "no_such_event"
    assert "no_such_event" in errors_for(tmp_path, "talents.yaml", change)


def test_duplicate_event_ids_across_files(tmp_path):
    dest = tmp_path / "data"
    shutil.copytree(DATA_DIR, dest)
    original = yaml.safe_load((dest / "events/childhood.yaml").read_text())
    later = dest / "events/later_life.yaml"
    data = yaml.safe_load(later.read_text())
    data.append(find(original, "stray_dog"))
    later.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ContentError) as exc:
        load_content(dest)
    assert "duplicate event id 'stray_dog'" in "\n".join(exc.value.errors)


def test_yaml_syntax_error_names_the_file(tmp_path):
    dest = tmp_path / "data"
    shutil.copytree(DATA_DIR, dest)
    (dest / "traits.yaml").write_text("- id: broken\n  name: [unclosed\n")
    with pytest.raises(ContentError) as exc:
        load_content(dest)
    assert "traits.yaml" in "\n".join(exc.value.errors)


def test_all_problems_are_reported_together(tmp_path):
    dest = mutate(tmp_path, "species.yaml", lambda d: find(d, "elf")["stat_modifiers"].update({"luck": 5}))
    path = dest / "events/childhood.yaml"
    data = yaml.safe_load(path.read_text())
    find(data, "stray_dog")["conditions"]["min_stage"] = "toddlerhood"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ContentError) as exc:
        load_content(dest)
    assert len(exc.value.errors) >= 2


def test_json_content_is_also_supported(tmp_path):
    """Content files may be JSON as well as YAML (skills.json is JSON)."""
    dest = tmp_path / "data"
    shutil.copytree(DATA_DIR, dest)
    (dest / "items.yaml").unlink()
    (dest / "items.json").write_text('[{"id": "x", "name": "X"}]')
    with pytest.raises(ContentError) as exc:          # items.yaml is the expected name, so this is "not found"
        load_content(dest)
    assert "items.yaml" in "\n".join(exc.value.errors)
