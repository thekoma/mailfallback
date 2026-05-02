import os
import shutil
import tempfile

from mailfallback.services.migration_worker import copy_tree, prescan, verify_copy


def test_prescan_counts_files_and_bytes():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "sub"))
        with open(os.path.join(d, "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(d, "sub", "b.txt"), "w") as f:
            f.write("world!")
        files, bytes_total = prescan(d)
        assert files == 2
        assert bytes_total == 11


def test_prescan_empty_dir():
    with tempfile.TemporaryDirectory() as d:
        files, bytes_total = prescan(d)
        assert files == 0
        assert bytes_total == 0


def test_copy_tree_copies_all_files():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        os.makedirs(os.path.join(src, "cur"))
        with open(os.path.join(src, "cur", "msg1"), "w") as f:
            f.write("mail1")
        os.makedirs(os.path.join(src, "Sent", "cur"))
        with open(os.path.join(src, "Sent", "cur", "msg2"), "w") as f:
            f.write("mail2x")

        progress = {"files": 0, "bytes": 0}

        def on_progress(copied_files, copied_bytes):
            progress["files"] = copied_files
            progress["bytes"] = copied_bytes

        copy_tree(src, dst, on_progress=on_progress)

        assert os.path.exists(os.path.join(dst, "cur", "msg1"))
        assert os.path.exists(os.path.join(dst, "Sent", "cur", "msg2"))
        assert progress["files"] == 2
        assert progress["bytes"] == 11


def test_copy_tree_skips_existing():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        with open(os.path.join(src, "existing"), "w") as f:
            f.write("original")
        os.makedirs(dst, exist_ok=True)
        with open(os.path.join(dst, "existing"), "w") as f:
            f.write("already_here")

        copy_tree(src, dst, on_progress=lambda f, b: None)

        with open(os.path.join(dst, "existing")) as f:
            assert f.read() == "already_here"


def test_copy_tree_resume_after_partial():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        with open(os.path.join(src, "a"), "w") as f:
            f.write("aaa")
        with open(os.path.join(src, "b"), "w") as f:
            f.write("bbb")
        with open(os.path.join(dst, "a"), "w") as f:
            f.write("aaa")

        progress = {"files": 0}
        copy_tree(src, dst, on_progress=lambda f, b: progress.update(files=f))

        assert os.path.exists(os.path.join(dst, "b"))
        assert progress["files"] == 2


def test_verify_copy_success():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        os.makedirs(os.path.join(src, "sub"))
        with open(os.path.join(src, "a"), "w") as f:
            f.write("x")
        with open(os.path.join(src, "sub", "b"), "w") as f:
            f.write("y")
        copy_tree(src, dst, on_progress=lambda f, b: None)
        ok, _detail = verify_copy(src, dst)
        assert ok is True


def test_verify_copy_missing_file():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        with open(os.path.join(src, "a"), "w") as f:
            f.write("x")
        with open(os.path.join(src, "b"), "w") as f:
            f.write("y")
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(os.path.join(src, "a"), os.path.join(dst, "a"))
        ok, detail = verify_copy(src, dst)
        assert ok is False
        assert "1" in detail
