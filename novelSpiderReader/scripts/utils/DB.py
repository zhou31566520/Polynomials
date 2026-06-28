import sqlite3

from scripts.main.app import logger
from scripts.utils.Global import DB_PATH


# ===================== 数据库 =====================
class DB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.c = self.conn.cursor()
        self.init_tables()

    def init_tables(self):
        self.c.execute('''CREATE TABLE IF NOT EXISTS t_novel_site (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT, domain TEXT, title_selector TEXT, author_selector TEXT,
            cover_selector TEXT, desc_selector TEXT, chapter_list_selector TEXT,
            chapter_title_selector TEXT, chapter_url_selector TEXT,
            content_selector TEXT, filter_words TEXT)''')

        self.c.execute('''CREATE TABLE IF NOT EXISTS books (
            book_id TEXT PRIMARY KEY, title TEXT, author TEXT, cover TEXT,
            desc TEXT, url TEXT, word_count INTEGER, add_time TEXT)''')

        self.c.execute('''CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT, title TEXT,
            url TEXT, content TEXT, idx INTEGER)''')

        self.c.execute('''CREATE TABLE IF NOT EXISTS shelf (
            id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT, title TEXT)''')

        self.c.execute('''CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT, chapter_idx INTEGER,
            content TEXT, create_time TEXT)''')
        self.conn.commit()

    def run(self, sql, args=()):
        try:
            self.c.execute(sql, args)
            self.conn.commit()
        except Exception as e:
            logger.error(f"DB Error: {e}")

    def fetch(self, sql, args=()):
        try:
            self.c.execute(sql, args)
            return self.c.fetchall()
        except Exception as e:
            logger.error(f"DB Fetch Error: {e}")
            return []

db = DB()
if __name__ == '__main__':
    logger.info("DB Test")
    # 查看DB中有哪些表
    tables = db.fetch("SELECT name FROM sqlite_master WHERE type='table'")
    logger.info(f"Tables: {tables}")
    # 查看表的字段
    for table in tables:
        sql = f"PRAGMA table_info({table[0]})"
        fields = db.fetch(sql)
        logger.info(f"Fields: {fields}")
        # 查看表的数据
        sql = f"SELECT * FROM {table[0]}"
        rows = db.fetch(sql)
        logger.info(f"Rows: {rows}")
    # 清空表 t_novel_site
    db.run("DELETE FROM t_novel_site")


