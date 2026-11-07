"""统一设置应用图标，子窗口继承默认图标。"""
import tkinter as tk

from kandu.core_config import asset_path


def set_app_icon(root):
    # 分别提供小尺寸图稿，避免 Windows 把大图自动缩成模糊的标题栏图标。
    root._app_icons = tuple(tk.PhotoImage(master=root, file=str(asset_path(name)))
                            for name in ("kandu.png", "kandu-32.png", "kandu-16.png"))
    # default=True 同时用于主窗口和后续创建的所有 Toplevel，包含多层设置弹窗。
    root.iconphoto(True, *root._app_icons)

    def on_map(event):
        if event.widget is root:
            # Tk 首次创建 Windows 窗口类时会用大图派生小图；映射后重设一次，
            # 才会采用提供的 16 像素图稿。后续窗口直接继承这些图标。
            root.unbind("<Map>", binding)
            root.iconphoto(True, *root._app_icons)

    binding = root.bind("<Map>", on_map, add="+")
