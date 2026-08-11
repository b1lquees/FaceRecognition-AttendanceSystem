from attendance.db import connect  # uses ATTENDANCE_DB if set, else attendance.db

conn = connect()
conn.execute("""
    DELETE FROM attendance 
    WHERE student_id = (SELECT id FROM students WHERE name = ?)
""", ("Bilquees",))
conn.commit()
conn.close()

print("Deleted Bilquees's attendance record(s).")