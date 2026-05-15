import sqlite3

conn = sqlite3.connect("evoting.db")
cursor = conn.cursor()

cursor.execute("UPDATE users SET role='admin' WHERE username='mayur'")

conn.commit()
conn.close()

print("Admin role updated successfully")