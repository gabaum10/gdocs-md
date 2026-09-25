"""Registry atomicity and the merge-on-save fix. A lighter,
faster version of the review's own concurrency probe (2000
reads/200 writes) -- enough processes and iterations to reliably hit the
race, without the full probe's runtime."""

import multiprocessing as mp
import time
from pathlib import Path

from gdocs_md import registry
from gdocs_md.errors import GdocsMdError


def _writer(path, n):
    for _ in range(n):
        registry.save_registry(path, {f"/f{j}.md": {"doc_id": f"d{j}"} for j in range(200)})


def _reader(path, n, result_queue):
    bad = 0
    for _ in range(n):
        try:
            registry.load_registry(path)
        except GdocsMdError:
            bad += 1
    result_queue.put(bad)


def test_concurrent_reads_never_see_a_torn_file(tmp_path):
    path = tmp_path / "registry.json"
    registry.save_registry(path, {})

    q = mp.Queue()
    writer = mp.Process(target=_writer, args=(path, 60))
    reader = mp.Process(target=_reader, args=(path, 400, q))
    writer.start()
    reader.start()
    writer.join()
    reader.join()

    assert q.get() == 0


def _create_like(path, key, barrier):
    reg = registry.load_registry(path)
    barrier.wait()
    time.sleep(0.01)
    reg[key] = {"doc_id": key}
    registry.save_registry(path, reg)


def test_two_concurrent_creates_both_survive(tmp_path):
    path = tmp_path / "registry.json"
    registry.save_registry(path, {})

    barrier = mp.Barrier(2)
    a = mp.Process(target=_create_like, args=(path, "/a.md", barrier))
    b = mp.Process(target=_create_like, args=(path, "/b.md", barrier))
    a.start()
    b.start()
    a.join()
    b.join()

    keys = set(registry.load_registry(path))
    assert keys == {"/a.md", "/b.md"}


def test_positive_control_merge_false_loses_the_concurrent_write(tmp_path):
    """Without the merge (the pre-fix shape: a blind overwrite), the
    second writer's save has no way to see the first writer's key -- this
    reproduces that directly (no race needed) to show merge=True is what's
    actually preventing the loss, not incidental timing."""
    path = tmp_path / "registry.json"
    registry.save_registry(path, {"/a.md": {"doc_id": "a"}})
    # A second "writer" that loaded BEFORE /a.md existed, saving with
    # merge=False -- the historical (buggy) behavior.
    registry.save_registry(path, {"/b.md": {"doc_id": "b"}}, merge=False)
    assert set(registry.load_registry(path)) == {"/b.md"}
