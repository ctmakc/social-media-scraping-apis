from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ig_followers import FollowerRecord, Store


def test_store_dedup_and_resume():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "followers.sqlite3"
        store = Store(db)
        target_id = "123"
        rows = [
            FollowerRecord("1", "alpha", "Alpha"),
            FollowerRecord("2", "beta", "Beta"),
            FollowerRecord("2", "beta", "Beta"),
        ]
        store.save_page("target", target_id, rows, "cursor-2", False)
        assert store.count(target_id) == 2
        assert store.get_cursor(target_id) == "cursor-2"
        assert not store.is_completed(target_id)

        store.save_page("target", target_id, [], "", True)
        assert store.is_completed(target_id)

        out = Path(tmp) / "followers.csv"
        assert store.export_csv(target_id, out) == 2
        assert out.read_text(encoding="utf-8").splitlines()[0].startswith("user_id,username")
        store.close()
