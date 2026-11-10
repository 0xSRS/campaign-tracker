import pytest

from core.constants import CSV_HEADER
from core.exceptions import CsvFileError, CsvFormatError
from models.import_schemas import DuplicateKind
from services.csv_import import CsvImportService

H = CSV_HEADER
U = "https://www.skills.google/public_profiles/"


def write(tmp_path, content, encoding="utf-8"):
    path = tmp_path / "in.csv"
    path.write_bytes(content.encode(encoding))
    return path


def run(tmp_path, content, lookup=None, encoding="utf-8"):
    return CsvImportService(lookup).import_file(write(tmp_path, content, encoding))


def test_valid_csv(tmp_path):
    r = run(tmp_path, f"{H}\n{U}a\n{U}b\n{U}c\n")
    s = r.summary
    assert (s.total_rows, s.valid_urls, s.new_profiles) == (3, 3, 3)
    assert s.existing_profiles == s.duplicate_rows == s.invalid_urls == 0
    assert [p.row_number for p in r.profiles] == [2, 3, 4]


def test_header_only_is_zero_rows(tmp_path):
    assert run(tmp_path, f"{H}\n").summary.total_rows == 0


def test_missing_header(tmp_path):
    with pytest.raises(CsvFormatError, match="header row is missing"):
        run(tmp_path, f"{U}a\n{U}b\n")


def test_wrong_header(tmp_path):
    with pytest.raises(CsvFormatError, match="Unexpected header"):
        run(tmp_path, "Email\nx@y.com\n")


def test_header_is_case_and_space_tolerant(tmp_path):
    assert run(tmp_path, f"  {H.upper()} \n{U}a\n").summary.new_profiles == 1


def test_multiple_columns_in_header(tmp_path):
    with pytest.raises(CsvFormatError, match="single column"):
        run(tmp_path, f"{H},Name\n{U}a,Bob\n")


def test_extra_column_in_data_row_is_reported(tmp_path):
    r = run(tmp_path, f"{H}\n{U}a,Bob\n{U}b\n")
    assert r.summary.import_errors == 1
    assert r.summary.new_profiles == 1
    assert r.error_rows[0].row_number == 2


def test_trailing_comma_is_tolerated(tmp_path):
    assert run(tmp_path, f"{H},\n{U}a,\n").summary.new_profiles == 1


@pytest.mark.parametrize("content", ["", "   \n\n", "\n"])
def test_empty_file(tmp_path, content):
    with pytest.raises(CsvFormatError, match="empty"):
        run(tmp_path, content)


def test_blank_rows_are_counted(tmp_path):
    r = run(tmp_path, f"{H}\n\n{U}a\n   \n,\n{U}b\n")
    assert r.summary.total_rows == 5
    assert r.summary.blank_rows == 3
    assert r.summary.new_profiles == 2


def test_invalid_urls_are_kept_with_reasons(tmp_path):
    r = run(tmp_path, f"{H}\nhttp://www.skills.google/public_profiles/a\n{U}ok\nbanana\n")
    assert r.summary.invalid_urls == 2
    assert r.summary.new_profiles == 1
    assert [i.row_number for i in r.invalid_rows] == [2, 4]
    assert r.invalid_rows[0].raw_value.startswith("http://")
    assert "https" in r.invalid_rows[0].reason


def test_exact_duplicates(tmp_path):
    r = run(tmp_path, f"{H}\n{U}a\n{U}a\n")
    assert r.summary.duplicate_rows == 1
    assert r.summary.new_profiles == 1
    assert r.duplicates[0].kind == DuplicateKind.EXACT
    assert r.duplicates[0].first_row_number == 2


def test_duplicates_after_safe_normalization(tmp_path):
    r = run(tmp_path, (
        f"{H}\n{U}a\nhttps://skills.google/public_profiles/a/\n"
        "HTTPS://WWW.SKILLS.GOOGLE/public_profiles/a?utm=x\n"
    ))
    assert r.summary.duplicate_rows == 2
    assert {d.kind for d in r.duplicates} == {DuplicateKind.NORMALIZED}
    assert len(r.profiles) == 1
    assert r.profiles[0].original_url == U + "a"  # first original preserved


def test_unicode_bom_whitespace_and_crlf(tmp_path):
    content = f"{H}\r\n\u00a0{U}a\u00a0\r\n  {U}b  \r\n"
    r = run(tmp_path, content, encoding="utf-8-sig")
    assert r.summary.new_profiles == 2
    assert r.profiles[0].original_url == U + "a"


def test_non_ascii_and_internal_space_are_invalid(tmp_path):
    r = run(tmp_path, f"{H}\n{U}छात्र\n{U}a b\n")
    assert r.summary.invalid_urls == 2


def test_file_not_found(tmp_path):
    with pytest.raises(CsvFileError, match="not found"):
        CsvImportService().import_file(tmp_path / "nope.csv")


def test_unreadable_binary_file(tmp_path):
    path = tmp_path / "bin.csv"
    path.write_bytes(b"\x80\x81\x82\xff\xfe")
    with pytest.raises(CsvFileError, match="UTF-8"):
        CsvImportService().import_file(path)


def test_directory_instead_of_file(tmp_path):
    with pytest.raises(CsvFileError):
        CsvImportService().import_file(tmp_path)


class FakeLookup:
    def known_normalized_urls(self):
        return {U + "a", U + "Xy"}


def test_existing_profiles_are_flagged(tmp_path):
    r = run(tmp_path, f"{H}\n{U}a\n{U}b\n", lookup=FakeLookup())
    assert r.summary.existing_profiles == 1
    assert r.summary.new_profiles == 1
    assert {p.profile_id: p.already_known for p in r.profiles} == {"a": True, "b": False}


def test_case_only_id_difference_goes_to_review(tmp_path):
    r = run(tmp_path, f"{H}\n{U}xy\n{U}b\n", lookup=FakeLookup())
    assert r.summary.needs_review == 1
    assert [p.profile_id for p in r.profiles] == ["b"]
    assert r.review_items[0].row_number == 2


def test_case_collision_inside_file_goes_to_review(tmp_path):
    r = run(tmp_path, f"{H}\n{U}abc\n{U}ABC\n")
    assert len(r.profiles) == 1
    assert r.summary.needs_review == 1


def test_summary_counts_add_up(tmp_path):
    r = run(tmp_path, f"{H}\n{U}a\n{U}a\n\nbad\n{U}b,extra\n{U}c\n")
    s = r.summary
    assert s.total_rows == s.valid_urls + s.invalid_urls + s.blank_rows + s.import_errors
    assert s.valid_urls == s.new_profiles + s.existing_profiles + s.duplicate_rows + s.needs_review