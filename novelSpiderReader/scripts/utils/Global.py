import json
import os


# ===================== 全局路径 =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOOKS_DIR = os.path.join(BASE_DIR, "books")
COVERS_DIR = os.path.join(BASE_DIR, "covers")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "reader.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(COVERS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ===================== 配置 =====================
DEFAULT_CONFIG = {
    "font_size": 14,
    "night_mode": False,
    "download_thread": 3,
    "log_level": "INFO",
    "log_enabled": True
}

if not os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)

with open(CONFIG_PATH, encoding="utf-8") as f:
    APP_CONFIG = json.load(f)



# ===================== 预置网站 =====================
PRESET_SITES = [
    (1, "笔趣阁", "https://www.biquge.com.cn", "#info h1::text", "#info p:nth-child(2)::text",
     "#fmimg img::attr(src)", "#intro::text", "#list dd a", "::text", "::attr(href)", "#content::text",
     "广告,笔趣阁,请记住本书首发域名,无弹窗"),
    (2, "纵横中文网", "https://book.zongheng.com", ".book-info h1::text", ".au-name a::text",
     ".book-img img::attr(src)", ".book-dec p::text", ".chapter-list li a", "::text", "::attr(href)", ".read-content::text",
     "纵横中文网,小说阅读,无弹窗,广告"),
    (3, "起点中文网", "https://book.qidian.com", ".book-info h1::text", ".writer::text",
     "#bookImg img::attr(src)", ".book-intro p::text", ".volume li a", "::text", "::attr(href)", ".read-content p::text",
     "起点中文网,广告,无弹窗"),
    (4, "七猫小说", "https://www.qimao.com", ".detail-info-title::text", ".author-name::text",
     ".detail-cover img::attr(src)", ".brief::text", ".chapter-list li a", "::text", "::attr(href)", ".content-body p::text",
     "七猫,广告,无弹窗"),
    (5, "番茄小说", "https://fanqienovel.com", ".book-name::text", ".author-name::text",
     ".book-cover img::attr(src)", ".book-desc::text", ".chapter-list li a", "::text", "::attr(href)", ".content p::text",
     "番茄小说,广告,无弹窗"),
    (6, "17K小说网", "https://www.17k.com", ".bookInfo h1::text", ".author a::text",
     ".bookImg img::attr(src)", ".bookIntro p::text", ".chapterList li a", "::text", "::attr(href)", ".readContent p::text",
     "17K,广告,无弹窗"),
    (7, "书旗小说", "https://www.shuqi.com", ".book-name::text", ".author::text",
     ".book-cover img::attr(src)", ".desc::text", ".chapter-list li a", "::text", "::attr(href)", ".content p::text",
     "书旗,广告,无弹窗"),
    (8, "潇湘书院", "https://www.xxsy.net", ".book-name::text", ".author::text",
     ".book-cover img::attr(src)", ".desc::text", ".chapter-list li a", "::text", "::attr(href)", ".content p::text",
     "潇湘,广告,无弹窗"),
    (9, "红袖添香", "https://www.hongxiu.com", ".book-name::text", ".author::text",
     ".book-cover img::attr(src)", ".desc::text", ".chapter-list li a", "::text", "::attr(href)", ".content p::text",
     "红袖,广告,无弹窗"),
    (10, "逐浪小说", "https://www.zhulang.com", ".book-info h1::text", ".author::text",
     ".book-img img::attr(src)", ".book-desc::text", ".chapter-list li a", "::text", "::attr(href)", ".content p::text",
     "逐浪,广告,无弹窗"),
]

PRESET_HOMEPAGES = [
    (1, "https://www.biquge.com.cn/top/"),
    (2, "https://book.zongheng.com/rank.html"),
    (3, "https://book.qidian.com/rank/"),
    (4, "https://www.qimao.com/rank/"),
    (5, "https://fanqienovel.com/rank/"),
    (6, "https://www.17k.com/rank/"),
    (7, "https://www.shuqi.com/rank/"),
    (8, "https://www.xxsy.net/rank/"),
    (9, "https://www.hongxiu.com/rank/"),
    (10, "https://www.zhulang.com/rank/"),
]
