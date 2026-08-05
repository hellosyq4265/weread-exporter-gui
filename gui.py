# -*- coding: utf-8 -*-
"""
WeRead Exporter GUI
微信读书导出工具 - 图形界面（wxPython）

无参数运行时启动图形界面；带参数时等价于原版命令行工具。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

import wx
import wx.adv as wx_adv

from weread_exporter import utils

APP_TITLE = "微信读书导出工具"
CLI_MARKER = "--cli"
CREATE_NO_WINDOW = 0x08000000
RESUME_FILE = "resume.json"

FORMATS = [
    ("md", "Markdown (.md)"),
    ("epub", "EPUB (.epub)"),
    ("pdf", "PDF (.pdf)"),
    ("txt", "TXT (.txt)"),
    ("mobi", "MOBI (.mobi)"),
]


def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def load_app_icon() -> wx.Icon:
    icon = wx.Icon()
    icon_path = resource_path(os.path.join("assets", "weread_exporter.ico"))
    if os.path.isfile(icon_path):
        icon.LoadFile(icon_path, wx.BITMAP_TYPE_ICO)
    return icon


class ExporterTaskBarIcon(wx_adv.TaskBarIcon):
    def __init__(self, frame: "ExporterFrame") -> None:
        super().__init__()
        self.frame = frame
        icon = load_app_icon()
        if icon.IsOk():
            self.SetIcon(icon, APP_TITLE)
        self.Bind(wx_adv.EVT_TASKBAR_LEFT_DCLICK, self._on_left_double_click)

    def CreatePopupMenu(self) -> wx.Menu:
        menu = wx.Menu()
        show_item = menu.Append(wx.ID_ANY, "显示窗口")
        exit_item = menu.Append(wx.ID_EXIT, "退出")
        menu.Bind(wx.EVT_MENU, self._show_frame, show_item)
        menu.Bind(wx.EVT_MENU, self._exit_frame, exit_item)
        return menu

    def _show_frame(self, _event) -> None:
        if self.frame.IsIconized():
            self.frame.Iconize(False)
        self.frame.Show()
        self.frame.Raise()

    def _on_left_double_click(self, _event) -> None:
        self._show_frame(None)

    def _exit_frame(self, _event) -> None:
        self.frame.Close()


def ensure_stdio() -> None:
    """打包为无控制台程序时，保证 stdout/stderr 至少指向空设备。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")


def run_cli(argv: list) -> None:
    if argv and argv[0] == CLI_MARKER:
        argv = argv[1:]
    ensure_stdio()
    # GUI 启动的子进程自动应答“继续执行”提示
    if os.environ.get("WEREAD_GUI_AUTO_YES") == "1":
        import builtins

        builtins.input = lambda prompt="": "Y"
    sys.argv = [sys.argv[0]] + list(argv)
    from weread_exporter.__main__ import main

    sys.exit(main())


class ExporterFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title=APP_TITLE, size=(720, 820))
        self.SetMinSize((640, 700))
        app_icon = load_app_icon()
        if app_icon.IsOk():
            self.SetIcon(app_icon)
        self._tray_icon = ExporterTaskBarIcon(self)
        self._proc: subprocess.Popen | None = None
        self._workdir: str | None = None
        self._running = False
        self._resume_info: dict | None = None
        self._notification = None
        self._build()
        self._load_config()
        self._resume_info = self._discover_resume()
        if self._resume_info:
            self._apply_resume_info(self._resume_info)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _build(self) -> None:
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(panel, label=APP_TITLE)
        font = title.GetFont()
        font.SetPointSize(14)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(font)
        root.Add(title, 0, wx.ALL, 10)

        # 书籍信息
        book_box = wx.StaticBox(panel, label="书籍信息")
        book_sizer = wx.StaticBoxSizer(book_box, wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(book_box, label="书籍 ID："), 0, wx.ALIGN_CENTER_VERTICAL)
        self.book_id_ctrl = wx.TextCtrl(book_box)
        row.Add(self.book_id_ctrl, 1, wx.LEFT | wx.EXPAND, 6)
        book_sizer.Add(row, 0, wx.EXPAND)
        hint = wx.StaticText(
            book_box,
            label=(
                "微信读书网页版打开书籍详情页，网址末尾的一串字符；"
                "也支持含下划线的书单 ID。"
            ),
        )
        hint.SetForegroundColour(wx.Colour(0x66, 0x66, 0x66))
        book_sizer.Add(hint, 0, wx.TOP, 4)
        root.Add(book_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 导出格式
        fmt_box = wx.StaticBox(panel, label="导出格式")
        fmt_sizer = wx.StaticBoxSizer(fmt_box, wx.VERTICAL)
        self.fmt_checks: dict = {}
        row = wx.BoxSizer(wx.HORIZONTAL)
        for fmt, label in FORMATS:
            check = wx.CheckBox(fmt_box, label=label)
            if fmt == "epub":
                check.SetValue(True)
            if fmt == "mobi" and sys.platform != "linux":
                check.Disable()
            self.fmt_checks[fmt] = check
            row.Add(check, 0, wx.RIGHT, 14)
        fmt_sizer.Add(row, 0)
        hint = wx.StaticText(
            fmt_box,
            label="MOBI 仅支持在 Linux 上生成；Windows 下请先导出 EPUB 再自行转换。",
        )
        hint.SetForegroundColour(wx.Colour(0x66, 0x66, 0x66))
        fmt_sizer.Add(hint, 0, wx.TOP, 4)
        root.Add(fmt_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 导出位置
        out_box = wx.StaticBox(panel, label="导出位置")
        out_sizer = wx.StaticBoxSizer(out_box, wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(out_box, label="保存到："), 0, wx.ALIGN_CENTER_VERTICAL)
        self.out_dir_ctrl = wx.TextCtrl(out_box)
        row.Add(self.out_dir_ctrl, 1, wx.LEFT | wx.EXPAND, 6)
        browse_btn = wx.Button(out_box, label="浏览…")
        browse_btn.Bind(wx.EVT_BUTTON, self._on_browse)
        row.Add(browse_btn, 0, wx.LEFT, 6)
        out_sizer.Add(row, 0, wx.EXPAND)
        hint = wx.StaticText(out_box, label="导出的文件将直接保存到所选文件夹。")
        hint.SetForegroundColour(wx.Colour(0x66, 0x66, 0x66))
        out_sizer.Add(hint, 0, wx.TOP, 4)
        root.Add(out_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # Cookie
        cookie_box = wx.StaticBox(panel, label="Cookie（登录凭证）")
        cookie_sizer = wx.StaticBoxSizer(cookie_box, wx.VERTICAL)
        self.cookie_ctrl = wx.TextCtrl(
            cookie_box,
            style=wx.TE_MULTILINE | wx.TE_WORDWRAP,
            size=(-1, 90),
        )
        cookie_sizer.Add(self.cookie_ctrl, 0, wx.EXPAND)
        hint = wx.StaticText(
            cookie_box,
            label=(
                "获取方式：登录 https://weread.qq.com → 按 F12 → Network（网络）→ "
                "点击任意 weread.qq.com 请求 → 复制请求头中的 Cookie 整段"
                "（形如 wr_vid=xxx; wr_skey=...）粘贴到上方。留空时必须勾选“强制登录”并扫码。"
            ),
        )
        hint.SetForegroundColour(wx.Colour(0x66, 0x66, 0x66))
        cookie_sizer.Add(hint, 0, wx.TOP, 4)
        root.Add(cookie_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 选项
        opt_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.headless_chk = wx.CheckBox(panel, label="无头模式")
        self.force_login_chk = wx.CheckBox(panel, label="强制登录（扫码）")
        self.mock_ua_chk = wx.CheckBox(panel, label="模拟 UA")
        self.single_page_chk = wx.CheckBox(panel, label="单页模式（上下滚动）")
        self.single_page_chk.SetValue(True)
        opt_sizer.Add(self.headless_chk, 0, wx.RIGHT, 16)
        opt_sizer.Add(self.force_login_chk, 0, wx.RIGHT, 16)
        opt_sizer.Add(self.mock_ua_chk, 0)
        opt_sizer.Add(self.single_page_chk, 0)
        root.Add(opt_sizer, 0, wx.LEFT | wx.TOP, 10)

        # 随机间隔
        interval_sizer = wx.BoxSizer(wx.HORIZONTAL)
        interval_sizer.Add(
            wx.StaticText(panel, label="章节间隔范围（秒）："),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        interval_sizer.Add(
            wx.StaticText(panel, label="最短"),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            4,
        )
        self.min_interval_spin = wx.SpinCtrl(panel, min=1, max=600, initial=18)
        interval_sizer.Add(self.min_interval_spin, 0, wx.RIGHT, 12)
        interval_sizer.Add(
            wx.StaticText(panel, label="最长"),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            4,
        )
        self.max_interval_spin = wx.SpinCtrl(panel, min=1, max=600, initial=42)
        interval_sizer.Add(self.max_interval_spin, 0, wx.RIGHT, 12)
        self.random_interval_chk = wx.CheckBox(panel, label="启用随机等待")
        self.random_interval_chk.SetValue(True)
        interval_sizer.Add(self.random_interval_chk, 0, wx.RIGHT, 8)
        interval_hint = wx.StaticText(
            panel, label="（每次章节之间随机等待，降低被风控识别的概率）"
        )
        interval_hint.SetForegroundColour(wx.Colour(0x66, 0x66, 0x66))
        interval_sizer.Add(interval_hint, 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(interval_sizer, 0, wx.LEFT | wx.TOP, 10)
        self.random_interval_chk.Bind(wx.EVT_CHECKBOX, self._on_random_interval_changed)

        # 按钮
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.start_btn = wx.Button(panel, label="开始导出")
        self.start_btn.Bind(wx.EVT_BUTTON, self._on_start)
        self.stop_btn = wx.Button(panel, label="停止")
        self.stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)
        self.stop_btn.Disable()
        clear_btn = wx.Button(panel, label="清空日志")
        clear_btn.Bind(wx.EVT_BUTTON, lambda _: self.log_ctrl.SetValue(""))
        btn_sizer.Add(self.start_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.stop_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(clear_btn, 0)
        root.Add(btn_sizer, 0, wx.LEFT | wx.TOP, 10)

        # 日志
        log_box = wx.StaticBox(panel, label="运行日志")
        log_sizer = wx.StaticBoxSizer(log_box, wx.VERTICAL)
        self.log_ctrl = wx.TextCtrl(
            log_box,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        log_sizer.Add(self.log_ctrl, 1, wx.EXPAND)
        root.Add(log_sizer, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        panel.SetSizer(root)

    def _on_browse(self, _event) -> None:
        dlg = wx.DirDialog(self, "选择导出位置", style=wx.DD_DEFAULT_STYLE)
        if dlg.ShowModal() == wx.ID_OK:
            self.out_dir_ctrl.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _append_log(self, text: str) -> None:
        self.log_ctrl.AppendText(text.rstrip("\r\n") + "\n")

    def _release_notification(self, notification) -> None:
        if self._notification is notification:
            self._notification = None

    def _show_windows_notification(self, title: str, message: str) -> None:
        if os.name != "nt" or wx_adv is None:
            return
        try:
            notification = wx_adv.NotificationMessage(
                title,
                message,
                parent=self,
            )
            notification.Show()
            self._notification = notification
            wx.CallLater(15000, self._release_notification, notification)
        except Exception as exc:
            self._append_log("Windows 通知发送失败：%s" % exc)

    @staticmethod
    def _terminate_process_tree(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=CREATE_NO_WINDOW,
                )
            except OSError:
                proc.terminate()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    @staticmethod
    def _remove_cookie_file(workdir: str) -> None:
        cookie_path = os.path.join(workdir, "cache", "cookie.txt")
        try:
            os.remove(cookie_path)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.start_btn.Enable(not running)
        self.stop_btn.Enable(running)
        self.random_interval_chk.Enable(not running)
        self._update_interval_controls()

    def _update_interval_controls(self) -> None:
        enabled = self.random_interval_chk.GetValue() and not self._running
        self.min_interval_spin.Enable(enabled)
        self.max_interval_spin.Enable(enabled)

    def _on_random_interval_changed(self, _event) -> None:
        self._update_interval_controls()

    def _config_path(self) -> str:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        cfg_dir = os.path.join(base, "WeReadExporterGUI")
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except Exception:
            pass
        return os.path.join(cfg_dir, "config.json")

    def _discover_resume(self) -> dict | None:
        candidates = []
        try:
            workdirs = [
                os.path.join(tempfile.gettempdir(), name)
                for name in os.listdir(tempfile.gettempdir())
                if name.startswith("weread_export_")
            ]
        except OSError:
            return None

        for workdir in workdirs:
            if not os.path.isdir(workdir):
                continue
            resume_data = {}
            resume_path = os.path.join(workdir, RESUME_FILE)
            try:
                if os.path.isfile(resume_path):
                    with open(resume_path, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    if isinstance(data, dict):
                        resume_data = data
            except (OSError, ValueError):
                continue

            cache_dir = os.path.join(workdir, "cache")
            if not os.path.isdir(cache_dir):
                continue
            try:
                book_dirs = [
                    os.path.join(cache_dir, name)
                    for name in os.listdir(cache_dir)
                    if os.path.isdir(os.path.join(cache_dir, name))
                ]
            except OSError:
                continue

            for book_dir in book_dirs:
                book_id = os.path.basename(book_dir)
                try:
                    utils.validate_book_id(book_id)
                except ValueError:
                    continue
                meta_path = os.path.join(book_dir, "meta.json")
                chapter_dir = os.path.join(book_dir, "chapters")
                if not os.path.isfile(meta_path) or not os.path.isdir(chapter_dir):
                    continue
                try:
                    with open(meta_path, "r", encoding="utf-8") as fp:
                        meta_data = json.load(fp)
                except (OSError, ValueError):
                    continue
                chapters = meta_data.get("chapters", [])
                title = meta_data.get("title", "untitled")
                if not isinstance(chapters, list) or not chapters:
                    continue
                chapter_files = []
                for chapter in chapters:
                    chapter_id = str(chapter.get("id", ""))
                    chapter_path = os.path.join(
                        chapter_dir, "%d-%s.md" % (len(chapter_files) + 1, chapter_id)
                    )
                    chapter_files.append(chapter_path)
                completed = sum(
                    os.path.isfile(path) and os.path.getsize(path) > 3
                    for path in chapter_files
                )
                output_dir = resume_data.get("out_dir") or self.out_dir_ctrl.GetValue().strip()
                formats = resume_data.get("formats")
                if not isinstance(formats, list) or not formats:
                    formats = ["md"] if completed == len(chapters) else ["epub"]
                formats = [fmt for fmt in formats if fmt in dict(FORMATS)]
                if not formats:
                    formats = ["epub"]
                if output_dir and completed == len(chapters):
                    expected = [
                        os.path.join(
                            output_dir,
                            "%s.%s" % (utils.format_filename(str(title)), fmt),
                        )
                        for fmt in formats
                    ]
                    if all(os.path.isfile(path) for path in expected):
                        continue
                candidate = dict(resume_data)
                candidate.update(
                    {
                        "workdir": workdir,
                        "book_id": resume_data.get("book_id") or book_id,
                        "out_dir": output_dir,
                        "formats": formats,
                        "title": str(title),
                        "completed_chapters": completed,
                        "total_chapters": len(chapters),
                    }
                )
                try:
                    mtimes = [os.path.getmtime(workdir), os.path.getmtime(meta_path)]
                    mtimes.extend(
                        os.path.getmtime(path)
                        for path in chapter_files
                        if os.path.isfile(path)
                    )
                except OSError:
                    continue
                candidate["mtime"] = max(mtimes)
                candidates.append(candidate)
                break

        if not candidates:
            return None
        return max(candidates, key=lambda item: item["mtime"])

    def _apply_resume_info(self, resume_info: dict) -> None:
        self.book_id_ctrl.SetValue(str(resume_info["book_id"]))
        if resume_info.get("out_dir"):
            self.out_dir_ctrl.SetValue(str(resume_info["out_dir"]))
        formats = set(resume_info.get("formats", []))
        for fmt, _ in FORMATS:
            self.fmt_checks[fmt].SetValue(fmt in formats)
        self.random_interval_chk.SetValue(bool(resume_info.get("random_interval", True)))
        self.min_interval_spin.SetValue(int(resume_info.get("min_interval", 18)))
        self.max_interval_spin.SetValue(int(resume_info.get("max_interval", 42)))
        self._update_interval_controls()
        self._append_log(
            "发现未完成导出：%s，已完成 %s/%s 章；点击“开始导出”将继续使用缓存。"
            % (
                resume_info.get("title", resume_info["book_id"]),
                resume_info.get("completed_chapters", 0),
                resume_info.get("total_chapters", 0),
            )
        )

    @staticmethod
    def _write_resume_info(workdir: str, data: dict) -> None:
        resume_data = {
            key: data[key]
            for key in (
                "book_id",
                "out_dir",
                "formats",
                "headless",
                "force_login",
                "mock_user_agent",
                "single_page",
                "random_interval",
                "min_interval",
                "max_interval",
            )
            if key in data
        }
        with open(
            os.path.join(workdir, RESUME_FILE), "w", encoding="utf-8"
        ) as fp:
            json.dump(resume_data, fp, ensure_ascii=False, indent=2)

    def _load_config(self) -> None:
        try:
            with open(self._config_path(), "r", encoding="utf-8") as fp:
                data = json.load(fp)
            self.book_id_ctrl.SetValue(data.get("book_id", ""))
            self.out_dir_ctrl.SetValue(data.get("out_dir", ""))
            self.min_interval_spin.SetValue(int(data.get("min_interval", 18)))
            self.max_interval_spin.SetValue(int(data.get("max_interval", 42)))
            self.single_page_chk.SetValue(bool(data.get("single_page", True)))
            self.random_interval_chk.SetValue(bool(data.get("random_interval", True)))
            self._update_interval_controls()
            if "cookie" in data:
                self._save_config()
        except Exception:
            pass

    def _save_config(self) -> None:
        data = {
            "book_id": self.book_id_ctrl.GetValue().strip(),
            "out_dir": self.out_dir_ctrl.GetValue().strip(),
            "min_interval": self.min_interval_spin.GetValue(),
            "max_interval": self.max_interval_spin.GetValue(),
            "single_page": self.single_page_chk.GetValue(),
            "random_interval": self.random_interval_chk.GetValue(),
        }
        try:
            with open(self._config_path(), "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _cli_command(self) -> list:
        if getattr(sys, "frozen", False):
            return [sys.executable, CLI_MARKER]
        return [sys.executable, os.path.abspath(__file__), CLI_MARKER]

    def _on_start(self, _event) -> None:
        book_id = self.book_id_ctrl.GetValue().strip()
        if not book_id:
            wx.MessageBox("请填写微信读书的书籍 ID（或书单 ID）。", "缺少书籍 ID", wx.OK | wx.ICON_WARNING)
            return
        try:
            utils.validate_book_id(book_id)
        except ValueError as exc:
            wx.MessageBox(str(exc), "书籍 ID 无效", wx.OK | wx.ICON_WARNING)
            return

        formats = [fmt for fmt, _ in FORMATS if self.fmt_checks[fmt].GetValue()]
        if not formats:
            wx.MessageBox("请至少勾选一种导出格式。", "缺少导出格式", wx.OK | wx.ICON_WARNING)
            return

        min_interval = self.min_interval_spin.GetValue()
        max_interval = self.max_interval_spin.GetValue()
        random_interval = self.random_interval_chk.GetValue()
        if random_interval and max_interval < min_interval:
            wx.MessageBox(
                "“最长间隔”不能小于“最短间隔”。",
                "间隔设置无效",
                wx.OK | wx.ICON_WARNING,
            )
            return

        out_dir = self.out_dir_ctrl.GetValue().strip()
        if not out_dir:
            wx.MessageBox("请选择导出文件保存的文件夹。", "缺少导出位置", wx.OK | wx.ICON_WARNING)
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as exc:
            wx.MessageBox("无法创建该文件夹：\n%s" % exc, "导出位置无效", wx.OK | wx.ICON_ERROR)
            return

        cookie = re.sub(r"[\r\n]+", "", self.cookie_ctrl.GetValue().strip())
        if not cookie and not self.force_login_chk.GetValue():
            wx.MessageBox(
                "请粘贴 Cookie，或勾选“强制登录（扫码）”后扫码登录。",
                "缺少 Cookie",
                wx.OK | wx.ICON_WARNING,
            )
            return

        self._save_config()

        resume_info = self._resume_info
        if not resume_info or resume_info.get("book_id") != book_id:
            resume_info = None
        if resume_info and os.path.isdir(resume_info.get("workdir", "")):
            workdir = resume_info["workdir"]
            self._append_log("继续未完成导出，复用缓存：%s" % workdir)
        else:
            workdir = tempfile.mkdtemp(prefix="weread_export_")
            resume_info = None
        self._workdir = workdir
        self._resume_info = {
            "workdir": workdir,
            "book_id": book_id,
            "out_dir": out_dir,
            "formats": formats,
            "headless": self.headless_chk.GetValue(),
            "force_login": self.force_login_chk.GetValue(),
            "mock_user_agent": self.mock_ua_chk.GetValue(),
            "single_page": self.single_page_chk.GetValue(),
            "random_interval": random_interval,
            "min_interval": min_interval,
            "max_interval": max_interval,
        }
        try:
            self._write_resume_info(workdir, self._resume_info)
        except OSError as exc:
            self._append_log("保存断点信息失败：%s" % exc)
        if cookie:
            cache_dir = os.path.join(workdir, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            with open(
                os.path.join(cache_dir, "cookie.txt"), "w", encoding="utf-8"
            ) as fp:
                fp.write(cookie)

        cmd = self._cli_command()
        cmd += ["-b", book_id]
        for fmt in formats:
            cmd += ["-o", fmt]
        if self.headless_chk.GetValue():
            cmd.append("--headless")
        if self.force_login_chk.GetValue():
            cmd.append("--force-login")
        if self.mock_ua_chk.GetValue():
            cmd.append("--mock-user-agent")
        if self.single_page_chk.GetValue():
            cmd.append("--single-page")
        if random_interval:
            cmd += [
                "--min-load-interval",
                str(min_interval),
                "--max-load-interval",
                str(max_interval),
            ]
        else:
            cmd.append("--no-random-interval")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["WEREAD_GUI_AUTO_YES"] = "1"

        self._append_log("=" * 60)
        self._append_log("开始导出：%s" % book_id)
        self._append_log("导出格式：%s" % ", ".join(formats))
        if random_interval:
            self._append_log("章节间隔：%d–%d 秒（随机）" % (min_interval, max_interval))
        else:
            self._append_log("章节间隔：不等待（已关闭随机等待）")
        self._append_log("工作目录：%s" % workdir)
        self._append_log("命令：%s" % " ".join(cmd))

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                env=env,
            )
        except Exception as exc:
            self._append_log("启动导出进程失败：%s" % exc)
            self._show_windows_notification(
                "导出无法启动",
                "导出进程启动失败，详情请查看 GUI 日志。",
            )
            self._remove_cookie_file(workdir)
            if resume_info is None:
                shutil.rmtree(workdir, ignore_errors=True)
            else:
                self._append_log("缓存已保留，可稍后重试继续导出。")
            self._workdir = None
            return

        self._proc = proc
        self._set_running(True)
        threading.Thread(
            target=self._watch_process,
            args=(proc, workdir, out_dir),
            daemon=True,
        ).start()

    def _watch_process(self, proc: subprocess.Popen, workdir: str, out_dir: str) -> None:
        try:
            for line in proc.stdout:
                wx.CallAfter(self._append_log, line.rstrip("\r\n"))
        except Exception as exc:
            wx.CallAfter(self._append_log, "读取日志失败：%s" % exc)
        proc.wait()
        wx.CallAfter(self._finish_export, proc, workdir, out_dir)

    @staticmethod
    def _copy_directory(src_dir: str, dst_dir: str) -> None:
        os.makedirs(dst_dir, exist_ok=True)
        for name in os.listdir(src_dir):
            src_path = os.path.join(src_dir, name)
            dst_path = os.path.join(dst_dir, name)
            if os.path.isdir(src_path):
                ExporterFrame._copy_directory(src_path, dst_path)
            else:
                shutil.copy2(src_path, dst_path)

    def _finish_export(
        self, proc: subprocess.Popen, workdir: str, out_dir: str
    ) -> None:
        rc = proc.returncode
        self._proc = None
        self._remove_cookie_file(workdir)
        if self._workdir == workdir:
            self._workdir = None
        self._set_running(False)
        self._append_log("导出进程结束，退出码：%s" % rc)

        src_dir = os.path.join(workdir, "output")
        moved = []
        copy_errors = False
        if rc == 0 and os.path.isdir(src_dir):
            for name in os.listdir(src_dir):
                src_path = os.path.join(src_dir, name)
                dst_path = os.path.join(out_dir, name)
                try:
                    if os.path.isdir(src_path):
                        self._copy_directory(src_path, dst_path)
                    elif os.path.isfile(src_path):
                        shutil.copy2(src_path, dst_path)
                    else:
                        continue
                    moved.append(name)
                except Exception as exc:
                    copy_errors = True
                    self._append_log("复制文件失败 %s：%s" % (name, exc))

        moved_files = [
            name for name in moved if os.path.isfile(os.path.join(out_dir, name))
        ]
        if rc == 0 and moved_files and not copy_errors:
            self._append_log("导出完成！文件已保存到：%s" % out_dir)
            for name in moved:
                self._append_log("  - %s" % name)
            message = "已生成：%s" % ", ".join(moved_files[:3])
            if len(moved_files) > 3:
                message += " 等 %d 个文件" % len(moved_files)
            self._show_windows_notification("导出完成", message)
            shutil.rmtree(workdir, ignore_errors=True)
            self._resume_info = None
            wx.MessageBox(
                "导出完成！文件已保存到：\n%s" % out_dir,
                "完成",
                wx.OK | wx.ICON_INFORMATION,
            )
        elif rc == 0 and copy_errors:
            self._append_log("导出已生成部分文件，但复制到目标目录失败；中间缓存已保留：%s" % workdir)
            self._show_windows_notification(
                "导出文件复制不完整",
                "部分文件未能复制，缓存已保留，可稍后重试。",
            )
            wx.MessageBox(
                "导出已生成部分文件，但复制到目标目录失败。\n\n缓存已保留，可稍后重试。",
                "导出未完成",
                wx.OK | wx.ICON_WARNING,
            )
        elif rc == 0:
            self._append_log(
                "导出流程正常结束，但未在 output 目录找到文件（书籍可能不可读或已被跳过）。"
            )
            self._append_log("中间文件保留在：%s" % workdir)
            self._show_windows_notification(
                "导出结束但未生成文件",
                "未找到可复制的输出文件，中间缓存已保留。",
            )
        else:
            self._append_log("导出失败。中间文件保留在：%s" % workdir)
            lines = self.log_ctrl.GetValue().splitlines()
            tail = "\n".join(lines[-10:]) if lines else ""
            wx.MessageBox(
                "导出失败（退出码 %s），请查看日志。\n\n最近日志：\n%s"
                % (rc, tail),
                "导出失败",
                wx.OK | wx.ICON_ERROR,
            )
            self._show_windows_notification(
                "导出失败",
                "退出码 %s；缓存已保留，请查看 GUI 日志。" % rc,
            )

    def _on_stop(self, _event) -> None:
        if self._proc and self._proc.poll() is None:
            self._append_log("正在停止导出…")
            try:
                self._terminate_process_tree(self._proc)
            except Exception as exc:
                self._append_log("停止进程失败：%s" % exc)

    def _on_close(self, event) -> None:
        if self._running:
            if (
                wx.MessageBox(
                    "导出正在进行，确定要退出吗？",
                    "确认退出",
                    wx.YES_NO | wx.ICON_QUESTION,
                )
                != wx.YES
            ):
                return
            self._on_stop(None)
        if self._workdir:
            self._remove_cookie_file(self._workdir)
        self._save_config()
        if self._tray_icon:
            self._tray_icon.RemoveIcon()
            self._tray_icon.Destroy()
            self._tray_icon = None
        self.Destroy()


def main() -> None:
    if len(sys.argv) > 1:
        run_cli(sys.argv[1:])
        return
    app = wx.App(False)
    frame = ExporterFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
