"""Viewing and exporting attendance records."""

import csv
import io

from flask import Blueprint, Response, render_template, request

from ..attendance_db import (
    PAGE_SIZE,
    archive_summary,
    get_all_attendance,
    get_todays_attendance,
)
from ..clock import local_date
from ..decorators import admin_required, login_required

records_bp = Blueprint("records", __name__)


@records_bp.route("/attendance/today")
@login_required
def attendance_today():
    records = get_todays_attendance()
    # the template shows today's date in the subtitle, so pass it in as a second variable.
    # anything passed here becomes a normal variable inside the html: {{ today }}
    today = local_date()

    # a row is (name, date, time_in, time_out, confidence). counting the ones with no
    # time_out here rather than in the template, because jinja cannot index a plain tuple
    # by position from inside a filter like selectattr
    still_here = sum(1 for row in records if not row[3])

    return render_template(
        "attendance_today.html", records=records, today=today, still_here=still_here
    )


@records_bp.route("/attendance/all")
@login_required
def attendance_all():
    # type=int makes Flask hand back None rather than raising on "?page=banana", so a
    # mangled url shows page one instead of a 500
    page = request.args.get("page", default=1, type=int) or 1
    query = (request.args.get("q") or "").strip()

    total, people, days = archive_summary(query)
    # ceiling division without importing math: how many pages of PAGE_SIZE this needs,
    # and at least one so an empty archive still renders "page 1 of 1"
    pages = max(1, -(-total // PAGE_SIZE))
    # clamped rather than 404'd: ?page=999 on a shrinking archive is a stale bookmark,
    # not an error worth blocking someone with
    page = min(max(page, 1), pages)

    records = get_all_attendance(
        limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, query=query
    )

    return render_template(
        "attendance_all.html",
        records=records,
        page=page,
        pages=pages,
        total=total,
        people=people,
        days=days,
        query=query,
        page_size=PAGE_SIZE,
    )


@records_bp.route("/attendance/export")
@admin_required
def export_attendance():
    records = get_all_attendance()

    # build the file in memory rather than writing it to disk. writing to disk would
    # raise questions this avoids entirely: where the temp file lives, what happens when
    # two people export at once, and who deletes it afterwards.
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Date", "Time In", "Time Out", "Confidence"])  # header row
    writer.writerows(records)  # each record is a tuple of values

    return Response(
        output.getvalue(),  # the entire in-memory file as a string
        mimetype="text/csv",  # tells the browser what kind of file this is
        # Content-Disposition: attachment is the standard http mechanism that makes the
        # browser download the response as a file instead of displaying it
        headers={"Content-Disposition": "attachment; filename=attendance.csv"},
    )
