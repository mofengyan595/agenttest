import json
import multiprocessing
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from agenttest.report import ReportStore, RunReport
from agenttest.report_files import ReportDirectory, pinned_directory, writer_lock


def report():
    return RunReport(scenario_sha256="test", contract_sha256="test", environment="isolated",
                     target_origin="http://127.0.0.1:8080")


def test_transient_windows_replace_lock_retries_only_publication(tmp_path, monkeypatch):
    replace, calls = ReportDirectory.replace, []

    def transient(directory, source, target):
        calls.append(source)
        if len(calls) <= 2:
            error = PermissionError("synthetic Windows sharing error")
            error.winerror = 5
            raise error
        return replace(directory, source, target)

    monkeypatch.setattr(ReportDirectory, "replace", transient)
    monkeypatch.setattr("agenttest.report.time.sleep", lambda delay: None)
    store, value = ReportStore(tmp_path), report()
    store.save(value)
    assert len(calls) == 3 and store.read(value.run_id) == value


@pytest.mark.parametrize("winerror,expected_calls", [(5, 5), (32, 5), (33, 5), (None, 1)])
def test_permanent_or_non_windows_denial_still_fails(tmp_path, monkeypatch,
                                                    winerror, expected_calls):
    calls = []

    def denied(directory, source, target):
        calls.append(source)
        error = PermissionError("permanent denial")
        if winerror is not None:
            error.winerror = winerror
        raise error

    monkeypatch.setattr(ReportDirectory, "replace", denied)
    monkeypatch.setattr("agenttest.report.time.sleep", lambda delay: None)
    with pytest.raises(PermissionError):
        ReportStore(tmp_path).save(report())
    assert len(calls) == expected_calls


def _save_worker(root, raw, barrier, queue):
    barrier.wait(timeout=20)
    try:
        ReportStore(Path(root)).save(RunReport.model_validate_json(raw))
        queue.put("saved")
    except ValueError:
        queue.put("conflict")


@pytest.mark.parametrize("same_id", [True, False])
@pytest.mark.parametrize("processes", [False, True])
def test_concurrent_snapshots_have_one_writer_per_id(tmp_path, same_id, processes):
    one, two = report(), report()
    if same_id:
        two.run_id = one.run_id
    if processes:
        context = multiprocessing.get_context("spawn")
        barrier, queue = context.Barrier(2), context.Queue()
        workers = [context.Process(target=_save_worker,
                   args=(str(tmp_path), value.model_dump_json(), barrier, queue))
                   for value in (one, two)]
        for worker in workers:
            worker.start()
        try:
            results = [queue.get(timeout=30) for _ in workers]
        finally:
            for worker in workers:
                worker.join(timeout=10)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=10)
        assert all(worker.exitcode == 0 for worker in workers)
        queue.close()
    else:
        barrier = threading.Barrier(2)

        def save(value):
            barrier.wait(timeout=5)
            try:
                ReportStore(tmp_path).save(value)
                return "saved"
            except ValueError:
                return "conflict"

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(save, (one, two)))
    assert results.count("saved") == (1 if same_id else 2)
    for value in (one, two):
        assert ReportStore(tmp_path).read(value.run_id).revision == 1


def test_stale_snapshot_cannot_replace_newer_state(tmp_path):
    store, value = ReportStore(tmp_path), report()
    store.save(value)
    old = store.read(value.run_id)
    value.completed = True
    store.save(value)
    with pytest.raises(ValueError, match="revision conflict"):
        store.save(old)
    assert store.read(value.run_id).completed
    assert old.revision == 1 and value.revision == 2


def test_failed_publication_preserves_previous_bytes_and_version(tmp_path, monkeypatch):
    store, value = ReportStore(tmp_path), report()
    path = store.save(value)
    previous = path.read_bytes()
    value.completed = True

    def fail(*args):
        raise PermissionError("injected publication failure")

    monkeypatch.setattr(ReportDirectory, "replace", fail)
    with pytest.raises(PermissionError):
        store.save(value)
    assert path.read_bytes() == previous and value.revision == 1
    assert not list(path.parent.glob("*.tmp"))


def _crash_writer(root, run_id):
    with pinned_directory(Path(root), run_id, create=True) as directory:
        with writer_lock(directory):
            fd = directory.open("report-interrupted.tmp", create=True)
            os.write(fd, b"partial")
            os._exit(17)


def test_crash_releases_lock_and_orphan_is_never_published(tmp_path):
    value = report()
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_writer, args=(str(tmp_path), value.run_id))
    process.start()
    process.join(timeout=20)
    if process.is_alive():
        process.terminate()
        process.join(timeout=10)
    assert process.exitcode == 17
    store = ReportStore(tmp_path)
    store.save(value)
    assert store.read(value.run_id) == value
    assert (tmp_path / value.run_id / "report-interrupted.tmp").read_bytes() == b"partial"


@pytest.mark.parametrize("run_id", ["../x", "A" * 32, "f" * 31, "g" * 32,
                                   "12345678-1234-1234-1234-123456789abc", ""])
def test_save_and_read_use_the_same_id_format(tmp_path, run_id):
    value, store = report(), ReportStore(tmp_path)
    value.run_id = run_id
    for action in (lambda: store.save(value), lambda: store.read(run_id)):
        with pytest.raises(ValueError, match="Invalid run id"):
            action()
    assert not list(tmp_path.iterdir())


def test_legacy_revision_is_readable_and_updatable(tmp_path):
    value, store = report(), ReportStore(tmp_path)
    folder = tmp_path / value.run_id
    folder.mkdir()
    data = value.model_dump(exclude={"revision"})
    (folder / "report.json").write_text(json.dumps(data), encoding="utf-8")
    old = store.read(value.run_id)
    assert old.revision == 0 and "revision" not in old.model_fields_set
    store.save(old)
    assert store.read(value.run_id).revision == 1


@pytest.mark.parametrize("component", ["root", "ancestor", "run", "report", "lock",
                                       "temporary", "unique_temporary"])
def test_preplanted_links_never_write_outside_root(tmp_path, monkeypatch, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    root = tmp_path / "reports"
    root.mkdir()
    value = report()
    folder = root / value.run_id
    folder.mkdir()
    directory_link = component in {"root", "ancestor", "run"}
    if component == "root":
        folder.rmdir()
        root.rmdir()
        target = root
    elif component == "ancestor":
        folder.rmdir()
        root.rmdir()
        target, root = root, root / "nested"
    else:
        target = {"run": folder, "report": folder / "report.json",
                  "lock": folder / ".writer.lock", "temporary": folder / "report.tmp",
                  "unique_temporary": folder / f"report-{'f' * 32}.tmp"}[component]
        if component == "run":
            folder.rmdir()
    try:
        target.symlink_to(outside if directory_link else sentinel,
                          target_is_directory=directory_link)
    except OSError as exc:
        code = exc.winerror if os.name == "nt" else exc.errno
        pytest.skip(f"Symlink creation unavailable: {code}")
    store = ReportStore(root)
    if component == "unique_temporary":
        monkeypatch.setattr("agenttest.report.uuid4", lambda: SimpleNamespace(hex="f" * 32))
    if component == "temporary":
        # Fixed old temporary name is ignored; unique O_EXCL names cannot follow links.
        store.save(value)
        assert store.read(value.run_id) == value
    else:
        with pytest.raises((ValueError, OSError)):
            store.save(value)
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert sorted(p.name for p in outside.iterdir()) == ["sentinel"]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction/reparse semantics")
@pytest.mark.parametrize("component", ["root", "run"])
def test_windows_junction_without_symlink_privilege(tmp_path, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "reports"
    value = report()
    if component == "run":
        root.mkdir()
    junction = root if component == "root" else root / value.run_id
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                            capture_output=True, check=False)
    if result.returncode:
        pytest.skip("Junction creation not available on this filesystem")
    with pytest.raises(ValueError, match="Linked"):
        ReportStore(root).save(value)
    assert not list(outside.iterdir())


def test_directory_replacement_cannot_redirect_publication(tmp_path, monkeypatch):
    value, store = report(), ReportStore(tmp_path)
    store.save(value)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "report.json"
    sentinel.write_text("untouched", encoding="utf-8")
    original = ReportDirectory.replace
    replaced = []

    def race(directory, source, target):
        if os.name == "nt":
            with pytest.raises(PermissionError):
                directory.path.rename(tmp_path / "moved")
        else:
            directory.path.rename(tmp_path / "moved")
            directory.path.symlink_to(outside, target_is_directory=True)
        replaced.append(True)
        return original(directory, source, target)

    monkeypatch.setattr(ReportDirectory, "replace", race)
    store.save(value)
    assert replaced and sentinel.read_text(encoding="utf-8") == "untouched"
    if os.name == "nt":
        assert store.read(value.run_id) == value
    else:
        assert (tmp_path / "moved" / "report.json").is_file()
        with pytest.raises(ValueError, match="Linked"):
            store.read(value.run_id)


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_real_windows_reader_lock_preserves_report(tmp_path):
    store, value = ReportStore(tmp_path), report()
    path = store.save(value)
    previous = path.read_bytes()
    with path.open("rb"):
        value.completed = True
        with pytest.raises(PermissionError):
            store.save(value)
    assert path.read_bytes() == previous and value.revision == 1
    store.save(value)
    assert store.read(value.run_id).completed
