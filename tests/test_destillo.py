"""Tests for Destillo — YouTube knowledge capture pipeline."""
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, "/opt/destillo/app")


class TestURLValidation:
    def test_extract_video_id_standard(self):
        from transcriber import extract_video_id
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_video_id_short(self):
        from transcriber import extract_video_id
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_video_id_embed(self):
        from transcriber import extract_video_id
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_video_id_invalid(self):
        from transcriber import extract_video_id
        assert extract_video_id("https://example.com") is None
        assert extract_video_id("not a url") is None

    def test_extract_video_id_with_params(self):
        from transcriber import extract_video_id
        assert extract_video_id("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=30s") == "jNQXAC9IVRw"

    def test_normalize_url_standard(self):
        from transcriber import normalize_url
        assert "dQw4w9WgXcQ" in normalize_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_normalize_url_shorts(self):
        from transcriber import normalize_url
        assert "dQw4w9WgXcQ" in normalize_url("https://youtu.be/dQw4w9WgXcQ")


class TestMarkdownStorage:
    def test_slugify_basic(self):
        from storage import slugify
        assert slugify("Hello World") == "hello-world"

    def test_slugify_special_chars(self):
        from storage import slugify
        assert slugify("Test: Video (2024) - Review!") == "test-video-2024---review"

    def test_slugify_truncates(self):
        from storage import slugify
        assert len(slugify("a" * 100)) <= 60

    def test_write_markdown_creates_file(self):
        from storage import write_markdown
        import config_manager
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "default_storage_path": tmp,
                "subject_areas": [],
                "default_subject_area": "misc"
            }
            original = config_manager.load_config
            config_manager.load_config = lambda: config
            try:
                parsed = {
                    "subject_area": "misc",
                    "summary": "A test summary.",
                    "key_points": ["Point one"],
                    "quotes": [],
                    "tags": ["test", "example"],
                    "related_concepts": []
                }
                fp = write_markdown(
                    url="https://youtube.com/watch?v=test123",
                    title="Test Video",
                    channel="TestChannel",
                    parsed=parsed,
                    transcript="Hello world"
                )
                assert os.path.exists(fp)
                content = open(fp).read()
                assert "---" in content
                assert "source: destillo" in content
                assert "chapters: []" in content
                assert "## My Notes" in content
                assert "TestChannel" in content
                assert "Hello world" in content
            finally:
                config_manager.load_config = original


class TestDatabase:
    @pytest.fixture(autouse=True)
    def setup_db(self):
        import database as db
        db.DB_PATH = "/tmp/destillo_test.db"
        if os.path.exists(db.DB_PATH):
            os.remove(db.DB_PATH)
        db.init_db()
        yield
        if os.path.exists(db.DB_PATH):
            os.remove(db.DB_PATH)

    def test_add_and_get_item(self):
        import database as db
        iid = db.add_item("https://youtube.com/watch?v=test123")
        assert iid > 0
        item = db.get_item(iid)
        assert item["status"] == "queued"

    def test_add_duplicate_url(self):
        import database as db
        iid = db.add_item("https://youtube.com/watch?v=test123")
        iid2 = db.add_item("https://youtube.com/watch?v=test123")
        assert iid2 == iid  # returns existing ID on duplicate

    def test_update_item_status(self):
        import database as db
        iid = db.add_item("https://youtube.com/watch?v=test123")
        db.update_item(iid, status="processing")
        assert db.get_item(iid)["status"] == "processing"

    def test_get_queued_items(self):
        import database as db
        db.add_item("https://youtube.com/watch?v=one")
        db.add_item("https://youtube.com/watch?v=two")
        assert len(db.get_queued_items()) == 2

    def test_stats(self):
        import database as db
        i1 = db.add_item("https://youtube.com/watch?v=one")
        db.update_item(i1, status="done", subject_area="tech")
        i2 = db.add_item("https://youtube.com/watch?v=two")
        db.update_item(i2, status="error")
        stats = db.get_stats()
        assert stats["total"] == 1
        assert stats["errors"] == 1

    def test_delete_item(self):
        import database as db
        iid = db.add_item("https://youtube.com/watch?v=del")
        db.delete_item(iid)
        assert db.get_item(iid) is None


class TestLLMParser:
    def test_strip_json_fences(self):
        import re
        raw = "```json\n" + '{"subject_area": "tech"}' + "\n```"
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        assert json.loads(cleaned)["subject_area"] == "tech"

    def test_strip_control_chars(self):
        import re
        raw = '{"summary": "test' + "\u0000" + 'content"}'
        cleaned = re.sub("[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
        assert json.loads(cleaned)["summary"] == "testcontent"
