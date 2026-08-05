import json
import os
import tempfile
from unittest.mock import patch

import gui


class Control:
    def GetValue(self):
        return ""


def test_discover_incomplete_export():
    with tempfile.TemporaryDirectory() as temp_root:
        workdir = os.path.join(temp_root, "weread_export_test")
        book_dir = os.path.join(workdir, "cache", "book-1")
        chapter_dir = os.path.join(book_dir, "chapters")
        os.makedirs(chapter_dir)
        with open(os.path.join(book_dir, "meta.json"), "w", encoding="utf-8") as fp:
            json.dump(
                {
                    "title": "Test Book",
                    "chapters": [{"id": "a"}, {"id": "b"}],
                },
                fp,
            )
        with open(os.path.join(chapter_dir, "1-a.md"), "w", encoding="utf-8") as fp:
            fp.write("done")

        frame = gui.ExporterFrame.__new__(gui.ExporterFrame)
        frame.out_dir_ctrl = Control()
        with patch("gui.tempfile.gettempdir", return_value=temp_root):
            info = frame._discover_resume()

        assert info["book_id"] == "book-1"
        assert info["completed_chapters"] == 1
        assert info["total_chapters"] == 2
