"""A job's folder in the workspace (lib/job_notes.py): named after the job and its id, followed when the job is
renamed, deleted with the job."""

import asyncio
from pathlib import Path

import pytest

from aifred.lib import file_manager, job_notes
from aifred.lib.config import SCHEDULER_JOBS_FOLDER


@pytest.fixture(autouse=True)
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(file_manager, "DOCUMENTS_DIR", tmp_path / "documents")
    monkeypatch.setattr("aifred.lib.document_store.get_document_store", lambda: None)  # never the real search index
    (tmp_path / "documents").mkdir()
    return tmp_path / "documents"


def test_the_folder_and_the_notes_file_carry_the_name_and_the_id(workspace):
    folder, notes = job_notes.sync(41, "Black Week Airsoft Deal-Check (AAP01 Build)")
    assert folder == f"{SCHEDULER_JOBS_FOLDER}/black-week-airsoft-deal-check-aap01-build_job-41"
    assert notes == f"{folder}/black-week-airsoft-deal-check-aap01-build_job-41.md"
    assert (workspace / folder).is_dir()
    assert not job_notes.has_notes(notes)


def test_syncing_again_changes_nothing(workspace):
    first = job_notes.sync(7, "Morgenbriefing")
    (workspace / first[1]).write_text("Notizen")
    assert job_notes.sync(7, "Morgenbriefing") == first
    assert job_notes.has_notes(first[1])


def test_a_renamed_job_takes_its_folder_and_its_notes_along(workspace):
    old_folder, old_notes = job_notes.sync(7, "Morgenbriefing")
    (workspace / old_notes).write_text("Notizen")
    (workspace / old_folder / "daten.csv").write_text("a,b")
    folder, notes = job_notes.sync(7, "Morgen-Mail")
    assert folder == f"{SCHEDULER_JOBS_FOLDER}/morgen-mail_job-7"
    assert (workspace / notes).read_text() == "Notizen"
    assert (workspace / folder / "daten.csv").read_text() == "a,b"
    assert not (workspace / old_folder).exists()


def test_an_id_is_not_taken_for_a_longer_one(workspace):
    job_notes.sync(41, "Eins")
    folder, _notes = job_notes.sync(1, "Zwei")
    assert folder == f"{SCHEDULER_JOBS_FOLDER}/zwei_job-1"
    assert (workspace / SCHEDULER_JOBS_FOLDER / "eins_job-41").is_dir()


def test_a_name_without_letters_still_makes_a_name(workspace):
    assert job_notes.sync(3, "???")[0] == f"{SCHEDULER_JOBS_FOLDER}/unnamed_job-3"


def test_removing_deletes_the_folder_with_everything_in_it(workspace, monkeypatch):
    class NoIndex:
        def list_documents(self):
            return []

    monkeypatch.setattr("aifred.lib.document_store.get_document_store", lambda: NoIndex())
    folder, notes = job_notes.sync(7, "Morgenbriefing")
    (workspace / notes).write_text("Notizen")
    (workspace / folder / "daten.csv").write_text("a,b")
    other = job_notes.sync(8, "Anderer")[0]
    asyncio.run(job_notes.remove(7))
    assert not (workspace / folder).exists()
    assert (workspace / other).is_dir()
    asyncio.run(job_notes.remove(7))   # nothing there: nothing to do
