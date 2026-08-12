from datetime import datetime

from .db import connect  # single source of truth for where the database lives


def get_student_id(name):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM students WHERE name = ?", (name,))
    row = cursor.fetchone()  # returns (id,) if found, or None
    conn.close()
    return row[0] if row else None

def list_students():
    """Everyone enrolled, for the admin dropdown that links an account to a person."""
    conn = connect()
    rows = conn.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    conn.close()
    return rows


def register_student(name):
    conn = connect()
    cursor = conn.cursor()

    # same single-statement pattern as mark_attendance, for the same reason: students.name
    # is declared UNIQUE, so if two requests try to register the same new person at the
    # same moment, the second insert is ignored instead of raising an IntegrityError
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "INSERT OR IGNORE INTO students (name, date_registered) VALUES (?, ?)",
        (name, today)
    )
    conn.commit()

    # lastrowid is only meaningful when a row was actually inserted, so read the id back
    # instead -- that returns the already-registered student's id when the insert was ignored
    cursor.execute("SELECT id FROM students WHERE name = ?", (name,))
    student_id = cursor.fetchone()[0]
    conn.close()
    return student_id

def check_in(name, confidence):
    """Record someone arriving. Returns "checked_in" or "already_in".

    Was called mark_attendance() when arriving was the only thing recorded.
    """
    student_id = get_student_id(name)
    if student_id is None:
        student_id = register_student(name)  # auto-register if this name has never been seen

    conn = connect()
    cursor = conn.cursor()

    # strftime converts the datetime object into a formatted string
    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")

    # this used to be a SELECT to check for an existing row, then an INSERT if there
    # wasn't one. the problem with that is the gap between the two statements: the camera
    # posts a frame every 1.5s, and two requests being handled at the same time could both
    # run the SELECT, both see nothing, and both INSERT -- two records for the same day.
    #
    # OR IGNORE closes that gap by making it a single statement. it leans on the UNIQUE
    # index on (date, student_id) created in db.py: if a row for this person on this day
    # already exists, sqlite silently skips the insert instead of raising.
    # the ? placeholders are still doing the same job as before -- they protect against
    # sql injection and handle the quoting/formatting of the values automatically.
    cursor.execute(
        "INSERT OR IGNORE INTO attendance (student_id, date, time_in, confidence) "
        "VALUES (?, ?, ?, ?)",
        (student_id, today, now_time, confidence)
    )
    conn.commit()

    # rowcount is 1 when the row went in, and 0 when the unique index rejected it --
    # which is exactly the "is this an arrival we have not already recorded" answer
    inserted = cursor.rowcount == 1
    conn.close()
    return "checked_in" if inserted else "already_in"


def check_out(name):
    """Record someone leaving. Returns "checked_out", "already_out" or "not_checked_in".

    Checking out is deliberately not automatic. Setting time_out on every sighting would
    mean walking past the camera five minutes after arriving recorded a five-minute day,
    and it would make "already checked in" impossible to report meaningfully.
    """
    student_id = get_student_id(name)
    if student_id is None:
        return "not_checked_in"  # never enrolled, so certainly never checked in

    conn = connect()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")

    # the "AND time_out IS NULL" is what makes this safe to call repeatedly: the update
    # only lands the first time, so two frames arriving together cannot both succeed and
    # the recorded leaving time is the first one, not whichever request finished last
    cursor.execute("""
        UPDATE attendance SET time_out = ?
        WHERE student_id = ? AND date = ? AND time_out IS NULL
    """, (now_time, student_id, today))
    conn.commit()

    if cursor.rowcount == 1:
        conn.close()
        return "checked_out"

    # nothing was updated, which means either there is no row for today at all or it
    # already has a leaving time. those need different messages, so ask.
    existing = cursor.execute(
        "SELECT 1 FROM attendance WHERE student_id = ? AND date = ?", (student_id, today)
    ).fetchone()
    conn.close()
    return "already_out" if existing else "not_checked_in"


def get_todays_attendance():
    conn = connect()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT students.name, attendance.date, attendance.time_in,
               attendance.time_out, attendance.confidence
        FROM attendance
        JOIN students on attendance.student_id = students.id
        WHERE attendance.date = ?
        ORDER BY attendance.time_in DESC
    """, (today,))
    # fetchall because we want every row -- this is the full attendance list
    rows = cursor.fetchall()
    conn.close()
    return rows

# how many rows one page of the archive shows
PAGE_SIZE = 50


def name_filter(query):
    """Build the LIKE pattern and clause for a name search.

    % and _ are wildcards in LIKE, so a search for "100%" would otherwise match every
    row. They are escaped, and ESCAPE names the escape character explicitly because
    sqlite has no default one.
    """
    if not query:
        return "", ()

    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "WHERE students.name LIKE ? ESCAPE '\\'", (f"%{escaped}%",)


def archive_summary(query=""):
    """Totals across the whole archive: (rows, distinct people, distinct days).

    Counted in sql rather than from the rows handed to the page, because the page is now
    one slice of the archive. Counting what was rendered would have quietly reported
    "50 records, 12 people" no matter how much history existed.
    """
    where, params = name_filter(query)
    conn = connect()
    row = conn.execute(f"""
        SELECT COUNT(*),
               COUNT(DISTINCT students.name),
               COUNT(DISTINCT attendance.date)
        FROM attendance
        JOIN students ON attendance.student_id = students.id
        {where}
    """, params).fetchone()
    conn.close()
    return row


def get_all_attendance(limit=None, offset=0, query=""):
    """Attendance rows, newest first.

    limit=None returns everything, which is what the CSV export wants -- exporting one
    page of a report would be a strange thing to hand somebody. The page itself passes a
    limit, because rendering every row ever recorded is fine at fifty and not at fifty
    thousand.
    """
    where, params = name_filter(query)

    # LIMIT -1 is sqlite's "no limit", which keeps this one statement rather than two
    sql = f"""
        SELECT students.name, attendance.date, attendance.time_in,
               attendance.time_out, attendance.confidence
        FROM attendance
        JOIN students ON attendance.student_id = students.id
        {where}
        ORDER BY attendance.date DESC, attendance.time_in DESC
        LIMIT ? OFFSET ?
    """

    conn = connect()
    rows = conn.execute(sql, (*params, -1 if limit is None else limit, offset)).fetchall()
    conn.close()
    return rows
