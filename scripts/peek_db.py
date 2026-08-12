from attendance.db import connect  # uses ATTENDANCE_DB if set, else attendance.db

conn = connect()
for row in conn.execute("""
    SELECT attendance.id, students.name, attendance.date, attendance.time, attendance.confidence
    FROM attendance
    JOIN students ON attendance.student_id = students.id
"""):
    print(row)
conn.close()
