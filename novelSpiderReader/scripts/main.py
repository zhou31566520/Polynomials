import tkinter as tk

from scripts.main.app import App

# ===================== 启动 =====================
if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()