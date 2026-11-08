"""An agent owns the prompt folder named by its id, and only that one (agent_config, agent_bundle).

Until 08.11.2026 deleting an agent removed every folder one of its prompt layers named, and an import kept the
bundle's paths while writing the files into the folder of the effective id: an agent renamed on a conflict read
the original's prompts, and deleting it removed the original's folder. A new id could also name a folder that is
no agent's (shared, utility, the Automatik's): its prompts went there, and deleting it took that folder."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from aifred.lib import agent_bundle, agent_config, prompt_loader
from aifred.lib.user_error import Conflict, Invalid

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@pytest.fixture
def prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A prompt tree with the template, AIfred's files and a folder of no agent (shared); agents.json with the defaults."""
    root = tmp_path / "prompts"
    for lang in ("de", "en"):
        shutil.copytree(PROMPTS / lang / agent_config.AGENT_TEMPLATE_DIR, root / lang / agent_config.AGENT_TEMPLATE_DIR)
        shutil.copytree(PROMPTS / lang / "aifred", root / lang / "aifred")
        (root / lang / "shared").mkdir()
        (root / lang / "shared" / "security_boundary.txt").write_text("Grenze", encoding="utf-8")
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", root)
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"agents": agent_config._default_agents()}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    monkeypatch.setattr(agent_bundle, "_load_settings", dict)
    monkeypatch.setattr(agent_bundle, "save_settings", lambda _settings: None)
    return root


def test_a_new_id_that_names_a_folder_of_no_agent_is_refused(prompts: Path) -> None:
    with pytest.raises(Invalid) as refused:
        agent_config.create_agent("shared", "Geteilt", "🤝", "")
    assert refused.value.key == "agent_id_taken"
    assert not (prompts / "de" / "shared" / "identity.txt").exists()


def test_deleting_an_agent_removes_its_own_folder_and_no_other(prompts: Path) -> None:
    agent_config.create_agent("gast", "Gast", "🙂", "")
    # A layer that points elsewhere, as Vision's personality is AIfred's file.
    agent_config.update_agent("gast", {"prompts": {**agent_config.load_agents_raw()["gast"]["prompts"],
                                                   "personality": "aifred/personality.txt"}})
    agent_config.delete_agent("gast")
    assert not (prompts / "de" / "gast").exists()
    assert (prompts / "de" / "aifred" / "personality.txt").is_file()
    assert (prompts / "en" / "aifred").is_dir()


def _bundle(agent_id: str, files: dict[str, str]) -> bytes:
    """A bundle of one agent whose layers point into its folder, with these files in both languages."""
    config = {"display_name": agent_id.title(), "emoji": "🙂", "description": "", "role": "agent",
              "prompts": {name.removesuffix(".txt"): f"{agent_id}/{name}" for name in files}}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format_version": agent_bundle.BUNDLE_FORMAT_VERSION, "agent_ids": [agent_id]}))
        base = f"agents/{agent_id}"
        archive.writestr(f"{base}/config.json", json.dumps(config))
        archive.writestr(f"{base}/sampling.json", "{}")
        archive.writestr(f"{base}/tts.json", "{}")
        for lang in ("de", "en"):
            for name, text in files.items():
                archive.writestr(f"{base}/prompts/{lang}/{name}", text)
    return buffer.getvalue()


def test_an_agent_renamed_on_import_reads_and_owns_its_own_folder(prompts: Path) -> None:
    agent_config.create_agent("gast", "Gast", "🙂", "")
    original = (prompts / "de" / "gast" / "identity.txt").read_text(encoding="utf-8")
    ids, _warnings = agent_bundle.import_bundle(_bundle("gast", {"identity.txt": "Importiert"}), conflict="rename", lang="de")
    assert ids == ["gast_imported"]
    imported = agent_config.load_agents_raw()["gast_imported"]
    assert imported["prompts"] == {"identity": "gast_imported/identity.txt"}
    assert (prompts / "de" / "gast_imported" / "identity.txt").read_text(encoding="utf-8") == "Importiert"
    agent_config.delete_agent("gast_imported")
    assert (prompts / "de" / "gast" / "identity.txt").read_text(encoding="utf-8") == original


def test_an_import_never_writes_into_a_folder_of_no_agent(prompts: Path) -> None:
    bundle = _bundle("shared", {"identity.txt": "Fremd"})
    with pytest.raises(Conflict):
        agent_bundle.import_bundle(bundle, conflict="abort", lang="de")
    with pytest.raises(Conflict) as refused:
        agent_bundle.import_bundle(bundle, conflict="overwrite", lang="de")
    assert refused.value.key == "agent_id_taken"
    assert not (prompts / "de" / "shared" / "identity.txt").exists()
    ids, _warnings = agent_bundle.import_bundle(bundle, conflict="rename", lang="de")
    assert ids == ["shared_imported"]
    assert (prompts / "de" / "shared" / "security_boundary.txt").read_text(encoding="utf-8") == "Grenze"


def test_the_preview_marks_a_taken_folder_as_a_conflict(prompts: Path) -> None:
    preview = agent_bundle.peek_bundle(_bundle("shared", {"identity.txt": "Fremd"}))
    assert preview["agents"][0]["exists_locally"] is True


def test_a_bundle_whose_agent_lacks_a_field_is_unreadable_and_changes_nothing(prompts: Path) -> None:
    source = zipfile.ZipFile(io.BytesIO(_bundle("gast", {"identity.txt": "Importiert"})))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith("config.json"):
                data = json.dumps({key: value for key, value in json.loads(data).items() if key != "description"}).encode()
            archive.writestr(name, data)
    with pytest.raises(agent_bundle.BundleUnreadable) as refused:
        agent_bundle.import_bundle(buffer.getvalue(), lang="de")
    assert refused.value.key == "bundle_config_incomplete"
    assert "gast" not in agent_config.load_agents_raw()
    assert agent_config.load_agents()   # agents.json still loads
