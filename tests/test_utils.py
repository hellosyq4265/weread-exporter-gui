
from weread_exporter import utils

def test_wr_hash():
    assert utils.wr_hash("42557145") == "f343248072895ed9f34f408"
    assert utils.wr_hash("14") == "aab325601eaab3238922e53"


def test_format_filename_sanitizes_windows_names():
    assert utils.format_filename("a/b:*?") == "a%2fb%3a%2a%3f"
    assert utils.format_filename("CON.txt") == "_CON.txt"
    assert utils.format_filename("   ") == "untitled"
    assert utils.format_filename("line\x00break") == "line%00break"


def test_validate_book_id():
    for book_id in ("42557145", "book_id-2", "ABC_xyz-9"):
        utils.validate_book_id(book_id)

    for book_id in ("", "book/id", "book id", "../book", "书籍"):
        try:
            utils.validate_book_id(book_id)
        except ValueError:
            continue
        raise AssertionError("invalid book ID was accepted: %r" % book_id)
