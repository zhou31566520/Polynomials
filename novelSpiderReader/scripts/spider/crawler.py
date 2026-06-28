import datetime
import traceback

import requests
from bs4 import BeautifulSoup

from scripts.main.app import logger
from scripts.utils.DB import db
from scripts.utils.Global import PRESET_HOMEPAGES


# ===================== 爬虫引擎 =====================
class Crawler:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    @staticmethod
    def parse(soup, sel):
        try:
            if "::" not in sel:
                return soup.select_one(sel).get_text(strip=True) if soup.select_one(sel) else ""
            s, t = sel.split("::", 1)
            tag = soup.select_one(s)
            if not tag: return ""
            if t == "text": return tag.get_text(strip=True)
            if t.startswith("attr("): return tag.get(t[5:-1], "")
        except:
            pass
        return ""

    @staticmethod
    def crawl_book(url, site_id):
        try:
            site = db.fetch("SELECT * FROM t_novel_site WHERE id=?", (site_id,))[0]
            r = requests.get(url, headers=Crawler.headers, timeout=8)
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            book_id = str(hash(url))
            title = Crawler.parse(soup, site[3])
            author = Crawler.parse(soup, site[4]).replace("作者：", "")
            cover = Crawler.parse(soup, site[5])
            desc = Crawler.parse(soup, site[6])
            if not title: return
            db.run('REPLACE INTO books VALUES (?,?,?,?,?,?,?,?)',
                   (book_id, title, author, cover, desc, url, 0, datetime.now().strftime("%Y-%m-%d %H:%M")))
            logger.info(f"已爬取：{title}")
        except:
            pass

    @staticmethod
    def auto_crawl():
        logger.info("开始自动爬取热门小说")
        for sid, hp in PRESET_HOMEPAGES:
            try:
                logger.info(f"开始爬取：{hp}")
                r = requests.get(hp, headers=Crawler.headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.select("a"):
                    href = a.get("href", "")
                    if href and ("/book/" in href or ".html" in href):
                        logger.info(f"发现小说：{href}")
                        domain = db.fetch("SELECT domain FROM t_novel_site WHERE id=?", (sid,))[0][2]
                        Crawler.crawl_book(href if href.startswith("http") else domain + href, sid)
                    else:
                        logger.info(f"发现链接：{href}")
            except Exception as e:
                logger.error(f"爬取失败：{hp} - {e}")
                logger.error(traceback.format_exc(e))
        logger.info("自动爬取完成")
