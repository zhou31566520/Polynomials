# ===================== 主界面 =====================
import json
import logging
import os
import threading
from datetime import datetime
from io import BytesIO
from logging.handlers import RotatingFileHandler
from tkinter import messagebox, ttk, scrolledtext

import requests
from PIL import Image, ImageTk
import tkinter as tk

from scripts.spider.crawler import Crawler
from scripts.utils.DB import db
from scripts.utils.Global import APP_CONFIG, CONFIG_PATH, PRESET_SITES


class AppLogger:
    def __init__(self):
        self.log_level = "INFO"
        self.log_enabled = True
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_dir = os.path.join(self.base_dir, "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = os.path.join(self.log_dir, "app.log")
        self.logger = logging.getLogger("NovelReader")
        self.logger.handlers.clear()
        self.logger.propagate = False
        self.callback = App.log  # 用于通知主界面显示日志的回调函数

    def setup(self):
        self.logger.handlers.clear()
        levels = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARN": logging.WARN, "ERROR": logging.ERROR}
        self.logger.setLevel(levels.get(self.log_level, logging.INFO))
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        file_handler = RotatingFileHandler(self.log_file, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        if self.log_enabled:
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)

    def debug(self, msg):
        self.logger.debug(msg)
        if self.callback: self.callback(msg, "DEBUG")
    def info(self, msg):
        self.logger.info(msg)
        if self.callback: self.callback(msg, "INFO")
    def warn(self, msg):
        self.logger.warning(msg)
        if self.callback: self.callback(msg, "WARN")
    def error(self, msg):
        self.logger.error(msg)
        if self.callback: self.callback(msg, "ERROR")

logger = AppLogger()
logger.setup()
logger.log_level = APP_CONFIG["log_level"]
logger.log_enabled = APP_CONFIG["log_enabled"]
logger.setup()


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("可配置式小说爬虫阅读器")
        self.root.geometry("1200x800")
        self.current_book = None
        self.current_chapter = 0
        self.log_history = []
        self.tooltip = None

        # 主面板
        self.main = tk.Frame(root)
        self.main.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 左侧菜单
        self.menu = tk.Frame(self.main, width=160, bg="#eee")
        self.menu.pack(side=tk.LEFT, fill=tk.Y)
        btn = {"width":16, "height":2}
        tk.Button(self.menu, text="书    城", **btn, command=self.show_bookstore).pack(pady=4)
        tk.Button(self.menu, text="书    架", **btn, command=self.show_shelf).pack(pady=4)
        tk.Button(self.menu, text="阅    读", **btn, command=self.show_read).pack(pady=4)
        tk.Button(self.menu, text="笔    记", **btn, command=self.show_notes).pack(pady=4)
        tk.Button(self.menu, text="设    置", **btn, command=self.show_settings).pack(pady=4)

        # 内容区
        self.content = tk.Frame(self.main)
        self.content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # 底部日志（3行）
        self.log_panel = tk.Frame(root, height=60, bg="#f5f5f5")
        self.log_panel.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_labels = [tk.Label(self.log_panel, anchor="w", bg="#f5f5f5") for _ in range(3)]
        for l in self.log_labels: l.pack(fill=tk.X, padx=5)
        # 注册日志回调，让日志显示在主界面
        logger.callback = self.log
        # 首次初始化
        if not db.fetch("SELECT 1 FROM t_novel_site"):
            logger.info("数据库中没有小说站点表，首次初始化...")
            for s in PRESET_SITES:
                db.run('INSERT INTO t_novel_site VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', s)
        else:
            logger.info("数据库中已存在小说站点表，无需首次初始化...")
            # 根据数据库中站点， 自动爬取热门小说
            threading.Thread(target=Crawler.auto_crawl, daemon=True).start()
            self.log("系统首次启动，自动爬取热门小说中...")
        # self.log_panel.pack(side=tk.BOTTOM, fill=tk.X)
        # self.log_labels = [tk.Label(self.log_panel, anchor="w", bg="#f5f5f5") for _ in range(3)]
        # for l in self.log_labels: l.pack(fill=tk.X, padx=5)

        # 首次初始化
        if not db.fetch("SELECT 1 FROM t_novel_site"):
            for s in PRESET_SITES:
                db.run('INSERT INTO t_novel_site VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', s)
            threading.Thread(target=Crawler.auto_crawl, daemon=True).start()
            self.log("系统首次启动，自动爬取热门小说中...")
        self.show_bookstore()

    def log(self, msg, level="INFO"):
        t = datetime.now().strftime("%H:%M:%S")
        line = f"[{t}] [{level}] {msg}"
        if level == "DEBUG": logger.debug(msg)
        elif level == "WARN": logger.warn(msg)
        elif level == "ERROR": logger.error(msg)
        else: logger.info(msg)
        self.log_history.append(line)
        if len(self.log_history) > 3:
            self.log_history.pop(0)
        for i, lbl in enumerate(self.log_labels):
            lbl.config(text=self.log_history[i] if i < len(self.log_history) else "")

    def clear(self):
        for w in self.content.winfo_children():
            w.destroy()

    # ===================== 书城 =====================
    def show_bookstore(self):
        self.clear()
        tk.Label(self.content, text="📚 全网小说书城", font=("微软雅黑",18)).pack(pady=10)
        canvas = tk.Canvas(self.content)
        scroll = ttk.Scrollbar(self.content, orient=tk.VERTICAL, command=canvas.yview)
        frame = tk.Frame(canvas)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        books = db.fetch("SELECT book_id,title,author,cover,desc FROM books")
        row, col, max_col = 0, 0, 4
        row_frame = None
        for b in books:
            if col == 0:
                row_frame = tk.Frame(frame)
                row_frame.pack(pady=10, padx=10)
            self.card(row_frame, *b)
            col += 1
            if col >= max_col:
                col = 0
                row += 1

    def card(self, parent, bid, title, author, cover, desc):
        card = tk.Frame(parent, width=200, height=280, bd=1, relief=tk.RIDGE)
        card.pack(side=tk.LEFT, padx=10)
        card.pack_propagate(0)
        lb = tk.Label(card)
        lb.pack(pady=5)
        try:
            if cover and cover.startswith("http"):
                res = requests.get(cover, timeout=5)
                img = Image.open(BytesIO(res.content)).resize((140,180), Image.Resampling.LANCZOS)
                img = ImageTk.PhotoImage(img)
                lb.config(image=img)
                lb.image = img
        except:
            lb.config(text="无封面")
        tk.Label(card, text=title, wraplength=180, font=("",10,"bold")).pack()
        tk.Label(card, text=f"作者：{author}").pack()

        def add():
            if db.fetch("SELECT 1 FROM shelf WHERE book_id=?", (bid,)):
                self.log(f"{title} 已在书架", "WARN")
                return
            db.run("INSERT INTO shelf (book_id,title) VALUES (?,?)", (bid, title))
            self.log(f"{title} 已加入书架")
            messagebox.showinfo("成功", "已加入书架")
        tk.Button(card, text="加入书架", bg="#4CAF50", fg="white", command=add).pack(pady=5)

        def tip(e):
            self.tooltip = tk.Toplevel()
            self.tooltip.wm_overrideredirect(1)
            self.tooltip.geometry(f"+{e.x_root+10}+{e.y_root+10}")
            tk.Label(self.tooltip, text=desc, wraplength=400, bg="#fff8dc", relief=tk.SOLID, padx=5, pady=3).pack()
        def hide(e):
            if self.tooltip: self.tooltip.destroy()
        card.bind("<Enter>", tip)
        card.bind("<Leave>", hide)
        card.bind("<Button-1>", lambda e: self.open_book(bid))

    def open_book(self, bid):
        self.current_book = bid
        title = db.fetch("SELECT title FROM books WHERE book_id=?", (bid,))[0][0]
        self.log(f"打开小说：{title}", "DEBUG")
        self.show_read()

    # ===================== 书架 =====================
    def show_shelf(self):
        self.clear()
        tk.Label(self.content, text="我的书架", font=("",16)).pack(pady=10)
        lst = tk.Listbox(self.content, width=100, height=25)
        lst.pack(fill=tk.BOTH, expand=True, padx=10)
        for t in db.fetch("SELECT title FROM shelf"):
            lst.insert(tk.END, t[0])
        def open_(e):
            idx = lst.curselection()
            if idx:
                t = lst.get(idx)
                bid = db.fetch("SELECT book_id FROM shelf WHERE title=?", (t,))[0][0]
                self.open_book(bid)
        lst.bind("<Double-1>", open_)

    # ===================== 阅读 =====================
    def show_read(self):
        self.clear()
        if not self.current_book:
            tk.Label(self.content, text="请先选择小说").pack()
            return
        top = tk.Frame(self.content)
        top.pack(pady=5)
        tk.Button(top, text="上一章", command=self.prev).grid(row=0,column=0,padx=5)
        tk.Button(top, text="下一章", command=self.next).grid(row=0,column=1,padx=5)
        tk.Button(top, text="目录", command=self.toc).grid(row=0,column=2,padx=5)
        self.ch_title = tk.Label(self.content, text="", font=("",14))
        self.ch_title.pack()
        self.read_area = scrolledtext.ScrolledText(self.content, font=("", APP_CONFIG["font_size"]))
        self.read_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.load_chapter()

    def load_chapter(self):
        d = db.fetch("SELECT title,content FROM chapters WHERE book_id=? AND idx=?", (self.current_book, self.current_chapter))
        if d:
            t, c = d[0]
            self.ch_title.config(text=t)
            self.read_area.delete(1.0, tk.END)
            self.read_area.insert(1.0, c)
            self.log(f"加载章节：{t}", "DEBUG")

    def prev(self):
        if self.current_chapter > 0:
            self.current_chapter -= 1
            self.load_chapter()
    def next(self):
        m = db.fetch("SELECT MAX(idx) FROM chapters WHERE book_id=?", (self.current_book,))[0][0] or 0
        if self.current_chapter < m:
            self.current_chapter += 1
            self.load_chapter()
    def toc(self):
        top = tk.Toplevel()
        top.title("目录")
        lst = tk.Listbox(top)
        lst.pack(fill=tk.BOTH, expand=True)
        cs = db.fetch("SELECT title,idx FROM chapters WHERE book_id=? ORDER BY idx", (self.current_book,))
        for c in cs: lst.insert(tk.END, c[0])
        def go():
            self.current_chapter = cs[lst.curselection()[0]][1]
            self.load_chapter()
            top.destroy()
        tk.Button(top, text="跳转", command=go).pack()

    # ===================== 笔记 =====================
    def show_notes(self):
        self.clear()
        tk.Label(self.content, text="笔记管理", font=("",16)).pack(pady=10)
        self.note_editor = scrolledtext.ScrolledText(self.content, height=10)
        self.note_editor.pack(fill=tk.BOTH, expand=True, padx=10)
        tk.Button(self.content, text="保存笔记", command=self.save_note).pack(pady=5)
        self.note_list = tk.Listbox(self.content, height=8)
        self.note_list.pack(fill=tk.BOTH, expand=True, padx=10)
        self.refresh_notes()

    def save_note(self):
        if not self.current_book: return
        c = self.note_editor.get(1.0, tk.END).strip()
        if not c: return
        db.run("INSERT INTO notes (book_id,chapter_idx,content,create_time) VALUES (?,?,?,?)",
               (self.current_book, self.current_chapter, c, datetime.now().strftime("%Y-%m-%d %H:%M")))
        self.log("笔记已保存")
        self.refresh_notes()

    def refresh_notes(self):
        self.note_list.delete(0, tk.END)
        for n in db.fetch("SELECT content FROM notes WHERE book_id=?", (self.current_book,)):
            self.note_list.insert(tk.END, n[0][:30] + "...")

    # ===================== 设置（网站配置 增删改查 悬浮窗） =====================
    def show_settings(self):
        self.clear()
        tk.Label(self.content, text="系统设置", font=("",16)).pack(pady=10)

        # 日志配置
        f1 = tk.Frame(self.content)
        f1.pack(pady=5)
        tk.Label(f1, text="日志：").grid(row=0,column=0)
        lv = tk.StringVar(value=APP_CONFIG["log_level"])
        cb = ttk.Combobox(f1, textvariable=lv, values=["DEBUG","INFO","WARN","ERROR"], state="readonly", width=8)
        cb.grid(row=0,column=1,padx=5)
        log_switch = tk.BooleanVar(value=APP_CONFIG["log_enabled"])
        tk.Checkbutton(f1, text="启用日志", variable=log_switch).grid(row=0,column=2,padx=5)

        def save_log():
            APP_CONFIG["log_level"] = lv.get()
            APP_CONFIG["log_enabled"] = log_switch.get()
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(APP_CONFIG, f, indent=2, ensure_ascii=False)
            logger.log_level = lv.get()
            logger.log_enabled = log_switch.get()
            logger.setup()
            self.log(f"日志配置已保存：{lv.get()}")
            messagebox.showinfo("成功", "已保存")
        tk.Button(f1, text="保存日志", command=save_log).grid(row=0,column=3,padx=5)

        # 网站配置
        tk.Label(self.content, text="网站爬虫配置（增删改查）").pack(pady=5)
        cols = ["ID","网站","域名"]
        tree = ttk.Treeview(self.content, columns=cols, show="headings")
        for c in cols: tree.heading(c, text=c)
        tree.pack(fill=tk.BOTH, expand=True, padx=10)
        for r in db.fetch("SELECT id,site_name,domain FROM t_novel_site"):
            tree.insert("", tk.END, values=r)

        def add():
            self.site_form(None)
        def edit():
            sel = tree.selection()
            if sel:
                data = tree.item(sel)["values"]
                self.site_form(data)
        def delete():
            sel = tree.selection()
            if sel and messagebox.askyesno("确认", "删除？"):
                db.run("DELETE FROM t_novel_site WHERE id=?", (tree.item(sel)["values"][0],))
                self.show_settings()
        f2 = tk.Frame(self.content)
        f2.pack(pady=5)
        tk.Button(f2, text="新增", command=add).grid(row=0,column=0,padx=5)
        tk.Button(f2, text="修改", command=edit).grid(row=0,column=1,padx=5)
        tk.Button(f2, text="删除", command=delete).grid(row=0,column=2,padx=5)

    def site_form(self, data):
        top = tk.Toplevel()
        top.title("网站配置")
        top.geometry("500x400")
        labels = ["网站名","域名","标题","作者","封面","简介","章节列表","章节标题","章节链接","内容","过滤词"]
        entries = []
        for i, t in enumerate(labels):
            tk.Label(top, text=t).grid(row=i, column=0, sticky="w")
            e = tk.Entry(top, width=40)
            e.grid(row=i, column=1, padx=5, pady=2)
            entries.append(e)
        if data:
            vals = db.fetch("SELECT * FROM t_novel_site WHERE id=?", (data[0],))[0]
            for i, v in enumerate(vals[1:]):
                entries[i].insert(0, v)
        def save():
            v = [e.get() for e in entries]
            if data:
                db.run('''UPDATE t_novel_site SET site_name=?,domain=?,title_selector=?,author_selector=?,
                cover_selector=?,desc_selector=?,chapter_list_selector=?,chapter_title_selector=?,
                chapter_url_selector=?,content_selector=?,filter_words=? WHERE id=?''', v + [data[0]])
            else:
                db.run('''INSERT INTO t_novel_site VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)''', v)
            top.destroy()
            self.show_settings()
            self.log("网站配置已保存")
        tk.Button(top, text="保存", command=save).grid(row=11, column=1, pady=10)
