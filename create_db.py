import sqlite3

# SQLiteデータベースを作成（ローカル環境でのみ使用）
conn = sqlite3.connect('tickets.db')
cursor = conn.cursor()

# テーブル作成の例
cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            guild_id INTEGER,
            user_id INTEGER,
            tickets INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )""")
cursor.execute("""
        CREATE TABLE IF NOT EXISTS invitations (
            guild_id INTEGER,
            user_id INTEGER,
            invites INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )""")

conn.commit()
conn.close()

print("データベースとテーブルが作成されました。")
