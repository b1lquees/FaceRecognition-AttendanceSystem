import pytest

from attendance.attendance_db import (
    PAGE_SIZE,
    archive_summary,
    get_all_attendance,
    register_student,
)
from attendance.db import connect


def make_records(count, name="Alice"):
    """Insert `count` attendance rows for one person, one per day.

    Written straight to the database rather than through check_in(), which allows only
    one row per person per day by design -- so building a multi-page archive through it
    would need a hundred different people.
    """
    student_id = register_student(name)
    conn = connect()
    conn.executemany(
        "INSERT INTO attendance (student_id, date, time_in, confidence) VALUES (?, ?, ?, ?)",
        [(student_id, f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "09:00:00", 0.4)
         for i in range(count)],
    )
    conn.commit()
    conn.close()


# --- paging the query ------------------------------------------------------------

def test_a_page_is_limited(temp_db):
    make_records(PAGE_SIZE + 10)

    assert len(get_all_attendance(limit=PAGE_SIZE)) == PAGE_SIZE


def test_the_second_page_holds_the_rest(temp_db):
    make_records(PAGE_SIZE + 10)

    assert len(get_all_attendance(limit=PAGE_SIZE, offset=PAGE_SIZE)) == 10


def test_pages_do_not_overlap(temp_db):
    make_records(PAGE_SIZE + 10)

    first = get_all_attendance(limit=PAGE_SIZE)
    second = get_all_attendance(limit=PAGE_SIZE, offset=PAGE_SIZE)

    assert not (set(first) & set(second))


# the CSV export calls this with no limit, and exporting one page of a report would be a
# strange thing to hand somebody
def test_no_limit_returns_everything(temp_db):
    make_records(PAGE_SIZE + 10)

    assert len(get_all_attendance()) == PAGE_SIZE + 10


# --- searching -------------------------------------------------------------------

def test_search_matches_part_of_a_name(temp_db):
    make_records(2, name="Alice Chen")
    make_records(3, name="Bob Adeyemi")

    assert len(get_all_attendance(query="Chen")) == 2
    assert len(get_all_attendance(query="alice")) == 2  # LIKE is case-insensitive here


def test_search_that_matches_nothing(temp_db):
    make_records(2, name="Alice")

    assert get_all_attendance(query="Zebedee") == []


# % and _ are LIKE wildcards. unescaped, a search for "%" would match every row, which
# is the opposite of what somebody typing it expects.
@pytest.mark.parametrize("wildcard", ["%", "_", "%%"])
def test_wildcards_are_searched_for_literally(temp_db, wildcard):
    make_records(3, name="Alice")

    assert get_all_attendance(query=wildcard) == []


def test_a_name_containing_a_wildcard_can_still_be_found(temp_db):
    make_records(2, name="100% Attendance")

    assert len(get_all_attendance(query="100%")) == 2


# --- the summary counts ----------------------------------------------------------

# counted in sql, not from the rows the page received. counting the page would report
# "50 records" however much history existed.
def test_the_summary_counts_the_whole_archive_not_one_page(temp_db):
    make_records(PAGE_SIZE + 10)

    total, people, days = archive_summary()

    assert total == PAGE_SIZE + 10
    assert people == 1
    assert days == PAGE_SIZE + 10


def test_the_summary_respects_the_search(temp_db):
    make_records(2, name="Alice")
    make_records(5, name="Bob")

    total, people, days = archive_summary(query="Alice")

    assert (total, people) == (2, 1)


# --- through the page ------------------------------------------------------------

def test_the_page_shows_pagination_when_there_is_more_than_one(client, login, temp_db):
    make_records(PAGE_SIZE + 1)
    login()

    body = client.get("/attendance/all").get_data(as_text=True)

    assert "Page 1 of 2" in body
    assert "page=2" in body


def test_no_pagination_controls_when_everything_fits(client, login, temp_db):
    make_records(3)
    login()

    body = client.get("/attendance/all").get_data(as_text=True)

    assert "Page 1 of" not in body


# paging must carry the search term, or page two would quietly show unfiltered results
# underneath a heading that still says the search is active
def test_the_search_term_survives_paging(client, login, temp_db):
    make_records(PAGE_SIZE + 5, name="Alice")
    make_records(3, name="Bob")
    login()

    body = client.get("/attendance/all?q=Alice").get_data(as_text=True)

    assert "q=Alice" in body
    assert "Page 1 of 2" in body


def test_a_search_with_no_matches_says_so(client, login, temp_db):
    make_records(3, name="Alice")
    login()

    body = client.get("/attendance/all?q=Zebedee").get_data(as_text=True)

    assert "Nothing matched" in body
    # and offers the way out, which is different from the never-any-records empty state
    assert "Clear search" in body


# a stale bookmark to ?page=999 on a shrinking archive is not worth blocking somebody
# with, so it clamps rather than 404s
@pytest.mark.parametrize("page", ["999", "0", "-4"])
def test_an_out_of_range_page_is_clamped(client, login, temp_db, page):
    make_records(3)
    login()

    response = client.get(f"/attendance/all?page={page}")

    assert response.status_code == 200


# type=int hands back None rather than raising, so a mangled url shows page one
def test_a_nonsense_page_number_does_not_error(client, login, temp_db):
    make_records(3)
    login()

    assert client.get("/attendance/all?page=banana").status_code == 200


def test_the_stats_shown_are_the_archive_totals(client, login, temp_db):
    make_records(PAGE_SIZE + 10)
    login()

    body = client.get("/attendance/all").get_data(as_text=True)

    # the total, not the fifty rows on this page
    assert str(PAGE_SIZE + 10) in body


# the export is not paginated: it is the whole archive or it is misleading
def test_the_csv_export_is_not_limited_to_a_page(client, login, temp_db):
    make_records(PAGE_SIZE + 10)
    login(role="admin")

    body = client.get("/attendance/export").get_data(as_text=True)

    assert len(body.strip().splitlines()) == PAGE_SIZE + 10 + 1  # + header


# --- searching by date as well as name --------------------------------------------

# the capability the javascript filter had and the first sql version lost: it matched
# anything in the row, so "2026-08" to see one month was something people could do
def test_search_matches_a_date(temp_db):
    make_records(3, name="Alice")

    assert len(get_all_attendance(query="2026-01")) > 0


def test_search_matches_a_full_date(temp_db):
    make_records(5, name="Alice")
    one_date = get_all_attendance()[0][1]

    found = get_all_attendance(query=one_date)

    assert len(found) == 1
    assert found[0][1] == one_date


def test_search_still_matches_a_name(temp_db):
    make_records(2, name="Alice Chen")
    make_records(3, name="Bob Adeyemi")

    assert len(get_all_attendance(query="Chen")) == 2


# a term matching a name and a term matching a date both work through the same box, which
# is why the clause is an OR rather than two separate fields
def test_a_name_and_a_date_search_use_the_same_box(temp_db):
    make_records(2, name="Alice")
    make_records(3, name="Bob")

    by_name = get_all_attendance(query="Alice")
    by_date = get_all_attendance(query="2026-")

    assert len(by_name) == 2
    assert len(by_date) == 5


def test_the_summary_counts_a_date_search_too(temp_db):
    make_records(4, name="Alice")

    total, people, days = archive_summary(query="2026-")

    assert total == 4
