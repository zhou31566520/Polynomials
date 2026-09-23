#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Desc    : P2P 聊天室 GUI —— 公共频道 + 私聊 + 成员列表
import os
import sys
import time
import queue
import threading
from datetime import datetime

# 复用 P2PClient
sys.path.insert(0, os.path.dirname(__file__))
from p2pclient import P2PClient, SIGNAL_SERVER, P2P_PORT, get_local_ip, DOWNLOAD_DIR

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, simpledialog


# ============ 时间戳格式化 ============

def fmt_time(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except Exception:
        return ""


# ============ GUI 客户端 ============

class P2PChatApp:
    """
    界面布局:
    ┌─────────────────────────────────────────────────┐
    │ 工具栏: 连接 / 断开 / 节点名 / 端口 / 信令地址    │
    ├──────────┬──────────────────────────────────────┤
    │ 成员列表 │  公共聊天室 + 切换 tab               │
    │ (左侧)   │  ┌─────┬─────┬─────┐                │
    │          │  │公共  │私聊A│私聊B│  (Tab)         │
    │ 右键菜单  │  └─────┴─────┴─────┘                │
    │ → 私聊   │  [聊天记录显示区]                     │
    │ → 请求文件│  [输入框] [发送]                     │
    └──────────┴──────────────────────────────────────┘
    """

    def __init__(self, root, default_name=None):
        self.root = root
        self.root.title("P2P 聊天室")
        self.root.geometry("960x640")
        # ★ 优先用命令行传入的默认名
        if default_name:
            self._default_name = default_name
        else:
            self._default_name = f"node-{get_local_ip().split('.')[-1]}"

        self.client = None                 # P2PClient 实例
        self.msg_queue = queue.Queue()     # 后台线程 → GUI 线程 的事件队列
        self.is_connected = False
        self.private_tabs = {}             # peer_id → Tab UI 映射
        self.private_convs = {}            # peer_id → 消息列表

        self._build_ui()
        self._poll_queue()                 # 开始轮询事件队列
        self._poll_peers()                 # 定期刷新成员列表

    # ---------- UI 构建 ----------

    def _build_ui(self):
        # === 顶部工具栏 ===
        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(toolbar, text="节点名:").pack(side=tk.LEFT)
        # 原来: self.var_name = tk.StringVar(value=f"node-{get_local_ip().split('.')[-1]}")
        self.var_name = tk.StringVar(value=self._default_name)
        ttk.Entry(toolbar, textvariable=self.var_name, width=14).pack(side=tk.LEFT, padx=4)

        ttk.Label(toolbar, text="端口:").pack(side=tk.LEFT, padx=(10, 0))
        # "0" 表示随机端口
        self.var_port = tk.StringVar(value="0")
        ttk.Entry(toolbar, textvariable=self.var_port, width=6).pack(side=tk.LEFT, padx=4)

        ttk.Label(toolbar, text="信令:").pack(side=tk.LEFT, padx=(10, 0))
        self.var_server = tk.StringVar(value=SIGNAL_SERVER)
        ttk.Entry(toolbar, textvariable=self.var_server, width=22).pack(side=tk.LEFT, padx=4)

        self.btn_connect = ttk.Button(toolbar, text="连接", command=self._on_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=(14, 4))

        self.btn_disconnect = ttk.Button(toolbar, text="断开", command=self._on_disconnect, state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, padx=4)

        self.lbl_status = ttk.Label(toolbar, text="● 未连接", foreground="gray")
        self.lbl_status.pack(side=tk.RIGHT)

        # === 主体 PanedWindow (可拖动分隔左右) ===
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # --- 左侧: 成员列表 ---
        left_frame = ttk.LabelFrame(paned, text="在线成员", padding=4)
        paned.add(left_frame, weight=1)

        self.tree = ttk.Treeview(left_frame, columns=("name", "ip"), show="headings", selectmode="browse")
        self.tree.heading("name", text="节点名")
        self.tree.heading("ip", text="IP:端口")
        self.tree.column("name", width=120)
        self.tree.column("ip", width=120)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 右键菜单
        self.tree_menu = tk.Menu(self.root, tearoff=0)
        self.tree_menu.add_command(label="💬 开始私聊", command=self._start_private_chat)
        self.tree_menu.add_command(label="📁 请求文件", command=self._request_file_dialog)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="🔗 直接连接", command=self._direct_connect)

        self.tree.bind("<Button-3>", self._show_tree_menu)
        self.tree.bind("<Double-1>", lambda e: self._start_private_chat())

        # --- 右侧: 聊天区 (Notebook 多 Tab) ---
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=4)

        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # 公共聊天室 Tab
        self.tab_public = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_public, text="🌐 公共聊天室")

        # 公共聊天记录
        self.public_log = scrolledtext.ScrolledText(self.tab_public, state=tk.DISABLED, wrap=tk.WORD,
                                                     font=("Consolas", 10), bg="#fafafa")
        self.public_log.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 公共输入区
        public_input = ttk.Frame(self.tab_public)
        public_input.pack(fill=tk.X, padx=4, pady=(0, 4))
        self.entry_public = ttk.Entry(public_input)
        self.entry_public.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_public.bind("<Return>", lambda e: self._send_public())
        ttk.Button(public_input, text="发送 (Enter)", command=self._send_public).pack(side=tk.RIGHT, padx=4)

        # 底部信息栏
        self.lbl_info = ttk.Label(self.root, text=f"下载目录: {DOWNLOAD_DIR}", anchor="w", foreground="gray")
        self.lbl_info.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=2)

    # ---------- 右键菜单 ----------

    def _show_tree_menu(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            item = self.tree.identify_row(event.y)
            if item:
                self.tree.selection_set(item)
                self.tree_menu.tk_popup(event.x_root, event.y_root)

    def _get_selected_peer(self):
        sel = self.tree.selection()
        if not sel:
            return None
        pid = sel[0]
        if self.client and pid in self.client.peers:
            return pid, self.client.peers[pid]
        return None, None

    # ---------- 连接/断开 ----------

    def _on_connect(self):
        if self.is_connected:
            return
        name = self.var_name.get().strip() or "anonymous"
        try:
            port = int(self.var_port.get().strip())
        except ValueError:
            messagebox.showerror("错误", "端口必须是数字")
            return
        server = self.var_server.get().strip()

        self.client = P2PClient(name=name, port=port, signal_server=server)
        self.client.register_handler(self._on_client_event)

        # ★ 捕获具体异常，显示准确错误
        try:
            if not self.client.start():
                messagebox.showerror("错误", "启动失败（未知原因）")
                self.client = None
                return
        except RuntimeError as e:
            messagebox.showerror("启动失败", str(e))
            self.client = None
            return

        self.is_connected = True
        self.btn_connect.configure(state=tk.DISABLED)
        self.btn_disconnect.configure(state=tk.NORMAL)
        self.lbl_status.configure(text=f"● 已连接 {self.client.name}@{self.client.ip}:{self.client.port}", foreground="green")

        self._log_public("系统", f"✅ 已连接到信令服务器 {server}")
        self._log_public("系统", f"📋 节点 peer_id = {self.client.peer_id}")
        self._log_public("系统", f"🔌 监听端口 = {self.client.port}" + (" (随机)" if self.client.port != port else ""))

    def _on_disconnect(self):
        if not self.is_connected:
            return
        self.is_connected = False
        try:
            if self.client:
                self.client.stop()
        except Exception:
            pass
        self.client = None
        self.btn_connect.configure(state=tk.NORMAL)
        self.btn_disconnect.configure(state=tk.DISABLED)
        self.lbl_status.configure(text="● 未连接", foreground="gray")
        self._refresh_peers([])

    # ---------- 发送消息 ----------

    def _send_public(self):
        if not self.is_connected or not self.client:
            messagebox.showwarning("提示", "请先连接")
            return
        msg = self.entry_public.get().strip()
        if not msg:
            return
        # ★ 放到后台线程，不阻塞 GUI 主线程
        threading.Thread(target=self.client.broadcast_chat, args=(msg,), daemon=True).start()
        self._log_public(self.client.name, msg, self.client.peer_id, is_self=True)
        self.entry_public.delete(0, tk.END)

    def _send_private(self, peer_id):
        if not self.is_connected or not self.client:
            return
        entry = self.private_tabs.get(peer_id, {}).get("entry")
        if not entry:
            return
        msg = entry.get().strip()
        if not msg:
            return
        # ★ 私聊也放后台线程
        threading.Thread(
            target=self.client.send_private_chat, args=(peer_id, msg), daemon=True
        ).start()
        peer_name = self.client.peers.get(peer_id, {}).get("name", peer_id)
        self._log_private(peer_id, self.client.name, msg, self.client.peer_id, is_self=True)
        entry.delete(0, tk.END)

    # ---------- 私聊 Tab ----------

    def _start_private_chat(self):
        pid, info = self._get_selected_peer()
        if not pid:
            return
        self._ensure_private_tab(pid, info.get("name", pid))

    def _ensure_private_tab(self, peer_id, peer_name=None):
        if peer_id in self.private_tabs:
            # 已存在 → 切过去
            tab = self.private_tabs[peer_id]["frame"]
            for i in range(self.notebook.index("end")):
                if self.notebook.nametowidget(self.notebook.tabs(i, "path")) is tab:
                    self.notebook.select(i)
                    return

        # 新建 Tab
        frame = ttk.Frame(self.notebook)
        label = f"💬 {peer_name or peer_id[:6]}"
        self.notebook.add(frame, text=label)

        log_widget = scrolledtext.ScrolledText(frame, state=tk.DISABLED, wrap=tk.WORD,
                                                font=("Consolas", 10), bg="#f6fbff")
        log_widget.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        input_frame = ttk.Frame(frame)
        input_frame.pack(fill=tk.X, padx=4, pady=(0, 4))
        entry = ttk.Entry(input_frame)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.bind("<Return>", lambda e, pid=peer_id: self._send_private(pid))
        ttk.Button(input_frame, text="发送 (Enter)",
                   command=lambda: self._send_private(peer_id)).pack(side=tk.RIGHT, padx=4)

        self.private_tabs[peer_id] = {
            "frame": frame, "log": log_widget, "entry": entry, "name": peer_name or peer_id[:6],
        }
        self.private_convs.setdefault(peer_id, [])

        # 切到新 Tab
        self.notebook.select(self.notebook.index("end") - 1)

    # ---------- 直接连接 / 请求文件 ----------

    def _direct_connect(self):
        pid, info = self._get_selected_peer()
        if not pid or not self.client:
            return
        self.client.connect_to_peer(info["ip"], info["port"])

    def _request_file_dialog(self):
        pid, info = self._get_selected_peer()
        if not pid or not self.client:
            return
        filename = tk.simpledialog.askstring("请求文件", f"输入要向 {info.get('name')} 请求的文件名:", parent=self.root)
        if filename:
            threading.Thread(target=lambda: self.client.request_file(info["ip"], info["port"], filename),
                             daemon=True).start()

    # ---------- GUI 日志输出 ----------

    def _log_public(self, name, message, peer_id=None, is_self=False):
        self._append_text(self.public_log, name, message, peer_id, is_self)

    def _log_private(self, peer_id, name, message, from_id=None, is_self=False):
        tab = self.private_tabs.get(peer_id)
        if tab:
            self._append_text(tab["log"], name, message, from_id, is_self)
        # 私聊消息未开 Tab 也缓存，下次打开时显示
        self.private_convs.setdefault(peer_id, []).append({
            "time": fmt_time(time.time()), "name": name, "message": message, "self": is_self,
        })

    def _append_text(self, widget, name, message, peer_id=None, is_self=False):
        widget.configure(state=tk.NORMAL)
        color = "blue" if is_self else "#555"
        tag = f"tag_{int(time.time()*1000)}"
        widget.tag_configure(tag, foreground=color)
        ts = fmt_time(time.time())
        prefix = f"[{ts}] {'我' if is_self else name}"
        suffix = f" ({peer_id[:6]})" if peer_id else ""
        widget.insert(tk.END, f"{prefix}{suffix}: {message}\n", tag)
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    # ---------- 成员列表刷新 ----------

    def _refresh_peers(self, peers):
        # 清
        for item in self.tree.get_children():
            self.tree.delete(item)
        # 填充（peer_id 做 item id）
        for pid, info in peers:
            self.tree.insert("", tk.END, iid=pid, values=(info.get("name", ""), f"{info['ip']}:{info['port']}"))

    def _poll_peers(self):
        """后台刷新成员列表（每 2 秒）"""
        if self.is_connected and self.client:
            peers = sorted(self.client.peers.items(), key=lambda x: x[1].get("name", ""))
            current = set(self.tree.get_children())
            new_ids = {pid for pid, _ in peers}
            # 差集：新增
            for pid, info in peers:
                if pid not in current:
                    self.tree.insert("", tk.END, iid=pid, values=(info.get("name", ""), f"{info['ip']}:{info['port']}"))
            # 差集：离开
            for pid in current:
                if pid not in new_ids:
                    self.tree.delete(pid)
                    self._log_public("系统", f"⚠️ 节点 {pid[:6]} 已离线")
        self.root.after(2000, self._poll_peers)

    # ---------- 事件循环（从后台线程 → GUI） ----------

    def _on_client_event(self, event_type, data):
        """P2PClient 事件回调 —— 扔进队列，UI 线程处理"""
        self.msg_queue.put((event_type, data))

    def _poll_queue(self):
        """UI 线程轮询队列（每 100ms）"""
        try:
            while True:
                event_type, data = self.msg_queue.get_nowait()
                self._handle_event(event_type, data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_event(self, event_type, data):
        if event_type == "chat_broadcast":
            print(f"[GUI EVENT] chat_broadcast: {data.get('name')}({data.get('peer_id')}): {data.get('message','')[:20]}")
            self._log_public(data.get("name", data.get("peer_id", ""))[:12],
                             data.get("message", ""),
                             data.get("peer_id"))
        elif event_type == "chat_private":
            pid = data.get("peer_id", "")
            print(f"[GUI EVENT] chat_private from {pid}: {data.get('message','')[:20]}")
            self._ensure_private_tab(pid, data.get("name", pid))
            self._log_private(pid,
                              data.get("name", pid[:6]),
                              data.get("message", ""),
                              pid)


# ============ 入口 ============

def main():
    # ★ 支持命令行传节点名: python p2pclient_gui.py node-1
    default_name = f"node-{get_local_ip().split('.')[-1]}"
    if len(sys.argv) > 1:
        default_name = sys.argv[1]

    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass

    # ★ 把 default_name 传给 App
    app = P2PChatApp(root, default_name=default_name)
    root.mainloop()


if __name__ == "__main__":
    main()