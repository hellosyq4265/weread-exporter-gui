"""
WebRead WebPage
"""

import asyncio
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from http.cookies import SimpleCookie
from typing import Dict, List, Optional, Union, Any, Tuple, cast, Set

import pyppeteer

from . import webproxy
from . import utils

if sys.version_info >= (3, 8):
    from typing import TYPE_CHECKING
else:
    from typing_extensions import TYPE_CHECKING


DETECT_HEADLESS_SCRIPT = """
const webdriver = navigator.webdriver === true;
const chromeObj = typeof window.chrome !== "undefined";
const pluginCount = navigator.plugins.length;
const languageCount = navigator.languages ? navigator.languages.length : 0;
const headlessUA = /HeadlessChrome/.test(navigator.userAgent);
const zeroOuterSize = (window.outerWidth === 0 && window.outerHeight === 0);
webdriver || !chromeObj || pluginCount === 0 || languageCount === 0  || headlessUA || zeroOuterSize;
"""


class WeReadWebPage(object):
    """WebRead WebPage"""

    root_url: str = "https://weread.qq.com"
    window_size: Tuple[int, int] = (1920, 1080)
    single_page_window_size: Tuple[int, int] = (960, 1080)

    def __init__(
        self,
        book_id: str,
        cookie_path: Optional[str] = None,
        webcache_path: Optional[str] = None,
    ) -> None:
        self._book_id: str = book_id
        self._cookie_path: Optional[str] = cookie_path
        self._cookie: Dict[str, str] = {}
        self._webcache_path: str = webcache_path or "cache"
        if not os.path.isdir(self._webcache_path):
            os.makedirs(self._webcache_path)
        self._home_url: str = "%s/web/bookDetail/%s" % (
            self.__class__.root_url,
            book_id,
        )
        self._chapter_root_url: str = self.__class__.root_url + "/web/reader/"
        self._hook_script_name: str = "1.%s.js" % "".join(
            [random.choice("0123456789abcdef") for _ in range(8)]
        )
        self._browser: Optional[pyppeteer.browser.Browser] = None
        self._page: Optional[pyppeteer.page.Page] = None
        self._browser_user_data_dir: Optional[str] = None
        self._popup_tasks: Set[Any] = set()
        self._load_cookie()
        self._url: str = ""
        self._proxy_installed: bool = False
        self._force_legacy_reader: bool = False
        self._headless: bool = False

    async def get_book_info(self) -> Dict[str, Any]:
        html = (await utils.fetch(self._home_url)).decode()
        pos1 = html.find("window.__INITIAL_STATE__")
        if pos1 <= 0:
            raise RuntimeError("Unexpected html: %s" % html)
        pos1 = html.find("=", pos1)
        pos2 = html.find("};", pos1)
        data = html[pos1 + 1 : pos2 + 1].strip()
        data = json.loads(data)
        book_info: Dict[str, Any] = {}
        book_info["title"] = data["reader"]["bookInfo"]["title"]
        book_info["author"] = data["reader"]["bookInfo"]["author"]
        book_info["cover"] = data["reader"]["bookInfo"]["cover"]
        book_info["intro"] = data["reader"]["bookInfo"]["intro"]
        book_info["chapters"] = []
        for chapter in data["reader"]["chapterInfos"]:
            chap = {
                "id": chapter["chapterUid"],
                "title": chapter["title"],
                "level": chapter["level"],
                "words": chapter["wordCount"],
                "anchors": [],
            }
            if chapter["anchors"]:
                for it in chapter["anchors"]:
                    chap["anchors"].append({"title": it["title"], "level": it["level"]})
            book_info["chapters"].append(chap)
        return book_info

    async def get_user_info(self) -> Dict[str, Any]:
        vid: str = self._cookie.get("wr_vid", "")
        if not vid:
            raise utils.InvalidUserError(
                "Cookie 缺少 wr_vid，请复制完整的 Cookie（wr_vid=xxx; wr_skey=...）"
            )
        url: str = "%s/web/user?userVid=%s" % (self.__class__.root_url, vid)
        headers: Dict[str, str] = {
            "Referer": self.__class__.root_url,
            "Cookie": self._format_cookie(),
        }
        rsp: bytes = await utils.fetch(url, headers=headers)
        rsp_data = json.loads(rsp.decode())
        err_code = self._normalise_error_code(rsp_data.get("errCode"))
        if err_code == -2012:
            # 尝试用首页返回的 Set-Cookie 刷新登录凭证
            try:
                result = await utils.fetch(
                    self.__class__.root_url,
                    headers=headers,
                    respond_with_headers=True,
                )
                _, rsp_headers, _ = cast(Tuple[int, Dict[str, str], bytes], result)
                set_cookie: str = ""
                if isinstance(rsp_headers, dict):
                    getall = getattr(rsp_headers, "getall", None)
                    if callable(getall):
                        set_cookie = "\n".join(
                            getall("Set-Cookie", [])
                        )
                    else:
                        set_cookie = rsp_headers.get("Set-Cookie", "") or ""
                if set_cookie:
                    refreshed = SimpleCookie()
                    refreshed.load(set_cookie)
                    for key, morsel in refreshed.items():
                        value = morsel.value
                        if not value:
                            continue
                        self._cookie[key] = value
                        logging.info(
                            "[%s] Update cookie %s"
                            % (self.__class__.__name__, key)
                        )
                    if refreshed:
                        self._save_cookie()
                        headers["Cookie"] = self._format_cookie()
                        rsp = await utils.fetch(url, headers=headers)
                        rsp_data = json.loads(rsp.decode())
                        err_code = self._normalise_error_code(rsp_data.get("errCode"))
            except Exception as exc:
                logging.warning(
                    "[%s] Refresh cookie failed: %s"
                    % (self.__class__.__name__, exc)
                )
        logging.info(
            "[%s] User info response errCode=%s",
            self.__class__.__name__,
            err_code,
        )
        if err_code in (-2010, -2012):
            raise utils.InvalidUserError(
                "Cookie 无效或已过期（errCode=%s），请重新复制 Cookie 或扫码登录"
                % err_code
            )
        if err_code not in (None, 0, ""):
            raise RuntimeError("Get user info failed: %s" % rsp_data)
        return rsp_data

    @staticmethod
    def _normalise_error_code(value: Any) -> Any:
        """将接口可能返回的字符串数字错误码转换为整数。"""
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"-?\d+", value):
                return int(value)
        return value

    def _load_cookie(self) -> None:
        self._cookie = {}
        if not self._cookie_path or not os.path.isfile(self._cookie_path):
            return
        with open(self._cookie_path) as fp:
            cookie = fp.read()
            try:
                cookie_data: Dict[str, str] = json.loads(cookie)
            except:
                for it in cookie.split(";"):
                    it = it.strip()
                    if "=" not in it:
                        continue
                    key, value = it.split("=", 1)
                    self._cookie[key] = value
            else:
                for key in cookie_data:
                    self._cookie[key] = cookie_data[key]

    def _save_cookie(self) -> None:
        if not self._cookie_path:
            return
        with open(self._cookie_path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(self._cookie))

    def _format_cookie(self, cookie: str = "") -> str:
        cookies: List[str] = []
        if cookie:
            cookies.append(cookie)
        cookie_map = dict(self._cookie)
        cookie_map["wr_useHorizonReader"] = "0"
        for key in cookie_map:
            cookies.append("%s=%s" % (key, cookie_map[key]))
        return "; ".join(cookies)

    async def _read_cookie(self) -> Dict[str, str]:
        cookies = await self._page.cookies()
        cookie_map = {}
        for cookie in cookies:
            cookie_map[cookie["name"]] = cookie["value"]
        return cookie_map

    async def _update_cookie(self) -> None:
        self._cookie = await self._read_cookie()

    async def check_valid(self) -> bool:
        html = await utils.fetch(self._home_url)
        if b'"soldout":1' in html:
            return False
        return True

    def _check_chrome(self) -> str:
        path_list = os.environ["PATH"].split(";" if sys.platform == "win32" else ":")
        for chrome in ("chrome", "msedge", "google-chrome", "google-chrome-stable"):
            if sys.platform == "win32":
                chrome += ".exe"
            for path in path_list:
                if os.path.isfile(os.path.join(path, chrome)):
                    return chrome

        if sys.platform == "darwin":
            chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            if os.path.isfile(chrome):
                return chrome

        if sys.platform == "win32":
            candidates = [
                os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("LocalAppData", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(os.environ.get("LocalAppData", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            ]
            for chrome in candidates:
                if chrome and os.path.isfile(chrome):
                    return chrome
            command = "where chrome"
        else:
            command = "which chrome"
        raise utils.ChromeNotInstalledError(
            "Please make sure `chrome` is installed, and the install path is added to PATH environment. \nYou can test that with `%s` command."
            % command
        )

    def _get_chrome_version(self, chrome_path: str) -> Optional[int]:
        """获取 Chrome 版本号的主版本号"""
        try:
            # 尝试获取 Chrome 版本
            result = subprocess.run(
                [chrome_path, "--version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                # 解析版本号，格式通常是 "Google Chrome 136.0.6776.0" 或 "Chromium 136.0.6776.0"
                version_match = re.search(r"(\d+)\.", result.stdout)
                if version_match:
                    return int(version_match.group(1))
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
            ValueError,
        ):
            # 如果获取版本失败，返回 None
            pass
        return None

    async def _close_unexpected_target(self, target: Any) -> None:
        if target is None or getattr(target, "type", "") != "page":
            return
        try:
            page = await target.page()
        except Exception:
            return
        if page is None or page is self._page:
            return
        target_url = getattr(target, "url", "")
        try:
            await page.close()
        except Exception:
            logging.debug(
                "[%s] Close unexpected page failed: %s",
                self.__class__.__name__,
                target_url,
                exc_info=True,
            )
        else:
            logging.info(
                "[%s] Close unexpected page: %s",
                self.__class__.__name__,
                target_url,
            )

    def _handle_target_created(self, target: Any) -> None:
        task = asyncio.ensure_future(self._close_unexpected_target(target))
        self._popup_tasks.add(task)
        task.add_done_callback(self._popup_tasks.discard)

    async def launch(
        self,
        headless: bool = False,
        single_page: bool = False,
        force_login: bool = False,
        use_default_profile: bool = False,
        mock_user_agent: bool = False,
        proxy_server: Optional[str] = None,
    ) -> None:
        logging.info("[%s] Launch url %s" % (self.__class__.__name__, self._home_url))
        chrome: str = self._check_chrome()

        # 检查 Chrome 版本并在使用默认 profile 时发出警告
        if use_default_profile:
            chrome_version = self._get_chrome_version(chrome)
            if chrome_version is not None and chrome_version >= 136:
                logging.warning(
                    "[%s] Chrome %d detected. Chrome 136+ no longer supports using default profile. Consider using --use-default-profile=false to avoid potential issues."
                    % (self.__class__.__name__, chrome_version)
                )

        args = [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-component-extensions-with-background-pages",
            "--remote-allow-origins=*",
        ]
        if headless:
            args.append("--headless=new")
            if sys.platform == "linux" and os.getuid() == 0:
                args.append("--no-sandbox")
        if use_default_profile:
            args.append("--user-data-dir")
        else:
            args.append("--window-size=%d,%d" % self.__class__.window_size)
            self._browser_user_data_dir = tempfile.mkdtemp(
                prefix="weread_browser_"
            )
            args.append("--user-data-dir=%s" % self._browser_user_data_dir)
        if mock_user_agent:
            args.append('--user-agent="%s"' % utils.generate_user_agent())
        if proxy_server:
            args.append("--proxy-server=%s" % proxy_server)
        args.append("about:blank")
        logging.info(
            "[%s] Chrome args: chrome %s" % (self.__class__.__name__, " ".join(args))
        )
        self._browser = await pyppeteer.launch(
            executablePath=chrome,
            ignoreDefaultArgs=["--enable-automation", "--disable-popup-blocking"],
            args=args,
            headless=False,
            defaultViewport=None,
            logLevel=logging.INFO,
        )
        self._headless = headless
        pages = await self._browser.pages()
        self._page = pages[0]
        for page in pages[1:]:
            try:
                await page.close()
            except Exception:
                logging.debug(
                    "[%s] Close extra initial page failed",
                    self.__class__.__name__,
                    exc_info=True,
                )
        self._browser.on("targetcreated", self._handle_target_created)
        await self._page.evaluateOnNewDocument(
            """() => {
            if (navigator.webdriver) {
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => {
                        console.log('navigator.webdriver is called');
                        console.log(new Error().stack);
                        return undefined;
                    }
                });
                var _hasOwnProperty = Object.prototype.hasOwnProperty;
                Object.prototype.hasOwnProperty = function (key) {
                    if (key === 'webdriver') {
                        console.log('hasOwnProperty', key, 'is called');
                        console.log(new Error().stack);
                        return false;
                    }
                    return _hasOwnProperty.call(this, key);
                };
                const originalQuery = navigator.permissions.query;
                navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
                );
            }
            if (navigator.plugins.length === 0) {
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
                Object.defineProperty(window, 'PluginArray', {
                    get: () => Array,
                });
            }
            if (navigator.languages.length === 0) {
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });
            }
            window.chrome = window.chrome || {
                runtime: {},
            };
        }
        """
        )
        # 直接在每个新文档创建时注入 hook 脚本，不再依赖请求拦截改写 HTML。
        # 这样即使 aiohttp 抓取页面失败，hook 也一定存在。
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.js"),
            "r",
            encoding="utf-8",
        ) as fp:
            hook_script = fp.read()
        await self._page._client.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": hook_script},
        )

        self._force_legacy_reader = single_page
        await self._page.setViewport(
            {
                "width": 0,
                "height": 0,
                "deviceScaleFactor": 0.3,
            }
        )
        detect_headless_result = await self._page.evaluate(DETECT_HEADLESS_SCRIPT)
        if detect_headless_result:
            logging.warning(
                "[%s] Headless mode detected, continue exporting"
                % self.__class__.__name__
            )

        if self._cookie.get("wr_vid"):
            try:
                user_info = await self.get_user_info()
            except utils.InvalidUserError as ex:
                logging.warning(
                    "[%s] Get user error: %s" % (self.__class__.__name__, ex)
                )
                self._cookie = {}
                if not force_login:
                    raise utils.LoginRequiredError(str(ex))
            else:
                logging.info(
                    "[%s] Current login user is %s"
                    % (self.__class__.__name__, user_info.get("name", "Anonymous"))
                )
        if self._cookie:
            await self._inject_cookie()

        await self._page.goto(self._home_url)
        # await self.wait_for_selector("div.readerFooter a")
        if force_login:
            await self.login()
        if self._cookie and not self._headless:
            await self.wait_for_avatar()
        self._page.on("console", self.handle_log)

    async def close(self) -> None:
        browser = self._browser
        browser_user_data_dir = self._browser_user_data_dir
        self._browser = self._page = None
        self._browser_user_data_dir = None
        self._proxy_installed = False
        for task in self._popup_tasks:
            task.cancel()
        self._popup_tasks.clear()
        if browser:
            try:
                await browser.close()
            except Exception:
                logging.exception("[%s] Close browser failed" % self.__class__.__name__)
        if browser_user_data_dir:
            shutil.rmtree(browser_user_data_dir, ignore_errors=True)

    async def get_html(self) -> str:
        return await self._page.evaluate("document.documentElement.outerHTML;")

    async def screenshot(self, save_path: str) -> None:
        await self._page.screenshot({"path": save_path})

    async def wait_for_selector(self, selector: str, timeout: int = 30) -> Any:
        try:
            return await self._page.waitForSelector(selector, timeout=timeout * 1000)
        except pyppeteer.errors.TimeoutError as ex:
            html = await self.get_html()
            html_path = "webpage.html"
            with open(html_path, "wb") as fp:
                if not isinstance(html, bytes):
                    html = html.encode("utf8")
                fp.write(html)
            logging.info(
                "[%s] Current html saved to %s" % (self.__class__.__name__, html_path)
            )
            screenshot_path = "screenshot.jpg"
            await self.screenshot(screenshot_path)
            logging.info(
                "[%s] Current screenshot saved to %s"
                % (self.__class__.__name__, screenshot_path)
            )
            raise ex

    def handle_log(self, message: Any) -> None:
        msg_type = getattr(message, "type", "log")
        if msg_type not in ("warning", "error"):
            return
        text = message.text
        if "preloaded using link preload" in text:
            return
        logging.info("[%s][Console] %s" % (self.__class__.__name__, text))
        with open("%s.log" % self._book_id, "a+", encoding="utf-8") as fp:
            fp.write("[%s] %s\n" % (self._url, text))

    async def wait_for_avatar(self, timeout: int = 30) -> None:
        time0 = time.time()
        while time.time() - time0 < timeout:
            avatar_url = await self._page.evaluate(
                "document.querySelector('img.wr_avatar_img') && document.querySelector('img.wr_avatar_img').getAttribute('src');"
            )
            if avatar_url is None or not avatar_url.endswith("Default.svg"):
                break
            await asyncio.sleep(5)
        else:
            raise RuntimeError("Wait for avatar timeout")

    async def _inject_cookie(self) -> None:
        cookie_map = dict(self._cookie)
        cookie_map["wr_useHorizonReader"] = "0"
        for key in cookie_map:
            logging.info(
                "[%s] Inject cookie %s" % (self.__class__.__name__, key)
            )
            await self._page.setCookie(
                {
                    "url": self.__class__.root_url,
                    "name": key,
                    "value": cookie_map[key],
                    "secure": True,
                }
            )

    async def login(self) -> bool:
        selectors = [
            "button.navBar_link_Login",
            "div.readerTopBar_right button.actionItem",
        ]
        for selector in selectors:
            script = (
                "var elem = document.querySelector('%s'); elem && elem.innerText"
                % (selector)
            )
            result = await self._page.evaluate(script)
            if not result:
                continue
            if "登录" not in result:
                continue
            await self._page.click(selector)
            script = "document.querySelector('div.menu_container img.wr_avatar_img')"
            time0 = time.time()
            while time.time() - time0 < 300:
                logging.info("[%s] Waiting for login" % self.__class__.__name__)
                await asyncio.sleep(10)
                result = await self._page.evaluate(script)
                if not result:
                    continue
                logging.info("[%s] Login success" % self.__class__.__name__)
                await self._update_cookie()
                self._save_cookie()
                return True
            else:
                raise RuntimeError("Login timeout")
        return False

    async def _get_from_cache_or_server(
        self, url: str, headers: Optional[Dict[str, str]] = None
    ) -> Tuple[int, Dict[str, str], bytes]:
        u: urllib.parse.ParseResult = urllib.parse.urlparse(url)
        path = os.path.join(
            self._webcache_path, "resources", u.path[1:].replace("/", os.sep)
        )
        if os.path.isfile(path):
            logging.info(
                "[%s] Url %s hit cache %d"
                % (self.__class__.__name__, url, os.path.getsize(path))
            )
            with open(path, "rb") as fp:
                return 200, {}, fp.read()

        dirpath = os.path.dirname(path)
        if not os.path.isdir(dirpath):
            os.makedirs(dirpath)
        result = await utils.fetch(url, headers=headers, respond_with_headers=True)
        # 当 respond_with_headers=True 时，返回类型确定是 Tuple[int, Dict[str, str], bytes]
        status, headers_resp, body = cast(Tuple[int, Dict[str, str], bytes], result)
        logging.info("[%s] Url %s return %d" % (self.__class__.__name__, url, status))
        if status == 200:
            with open(path, "wb") as fp:
                fp.write(body)
        return status, headers_resp, body

    def _log_request(self, request: "webproxy.WebRequest") -> None:
        if request.method == "POST":
            message = "[%s] %s %s" % (
                self.__class__.__name__,
                request.method,
                request.url,
            )
            if request.body:
                message += " %s" % request.content
            logging.info(message)

    def on_document_request(self, request: "webproxy.WebRequest") -> Dict[str, Any]:
        """ """
        cookie = request.headers.get("cookie", "")
        cookie += "; wr_useHorizonReader=0"
        request.headers["cookie"] = cookie
        return {"type": webproxy.EnumProxyType.Continue, "headers": request.headers}

    def on_document_response(self, response: "webproxy.WebResponse") -> Dict[str, Any]:
        content = response.content
        inject_script = (
            "<script src='https://cdn.weread.qq.com/web/%s'></script>\n"
            % self._hook_script_name
        )
        content = content.replace("</head>", inject_script + "</head>")
        headers = dict(response.headers)
        # aiohttp 会自动解压响应体，这里必须去掉压缩/长度头，否则浏览器解码失败
        for key in ("content-encoding", "content-length", "transfer-encoding"):
            headers.pop(key, None)
        return {
            "status": response.status,
            "headers": headers,
            "body": content.encode("utf-8"),
        }

    def on_hook_script_request(self, request: "webproxy.WebRequest") -> Dict[str, Any]:
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.js"),
            "rb",
        ) as fp:
            hook_script = fp.read()
            return {
                "type": webproxy.EnumProxyType.Mock,
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "body": hook_script,
            }

    def on_log_request(self, request: "webproxy.WebRequest") -> Dict[str, Any]:
        self._log_request(request)
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Request-Method": "*",
            "Access-Control-Allow-Headers": "*",
        }
        if request.method == "OPTIONS":
            return {
                "type": webproxy.EnumProxyType.Mock,
                "status": 200,
                "headers": headers,
            }
        if "/hera/logkv" in request.url or "/hera/osslog" in request.url:
            return {
                "type": webproxy.EnumProxyType.Mock,
                "status": 204,
                "headers": headers,
            }
        elif "chlog" in request.url:
            logging.info("[%s] Url %s return mock result" % (self.__class__.__name__, request.url))
            return {
                "type": webproxy.EnumProxyType.Mock,
                "status": 200,
                "headers": headers,
            }
        return {"type": webproxy.EnumProxyType.Block}

    def on_sentry_request(self, request: "webproxy.WebRequest") -> Dict[str, Any]:
        self._log_request(request)
        return {
            "type": webproxy.EnumProxyType.Mock,
            "status": 200,
        }

    def on_single_report_request(
        self, request: "webproxy.WebRequest"
    ) -> Dict[str, Any]:
        self._log_request(request)
        return {
            "type": webproxy.EnumProxyType.Mock,
            "status": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Request-Method": "*",
                "Access-Control-Allow-Headers": "*",
            },
            "body": '{"err_code":0,"msg":"suc"}',
        }

    def on_chapter_request(self, request: "webproxy.WebRequest") -> Dict[str, Any]:
        return {"type": webproxy.EnumProxyType.Continue}

    async def pre_load_page(self) -> None:
        if self._proxy_installed:
            return
        # await self._page.setRequestInterception(True)
        rules = [
            webproxy.ProxyRule("*/hera/*", self.on_log_request),
            webproxy.ProxyRule("*/sentry/*", self.on_sentry_request),
            webproxy.ProxyRule("*/river/single*", self.on_single_report_request),
        ]
        proxy = webproxy.WebProxy(self._page, rules)
        await proxy.setup_interception()
        self._proxy_installed = True
        # self._page.on("request", self.handle_request)

    async def get_markdown(self) -> str:
        hook_exists = await self._page.evaluate(
            "typeof canvasContextHandler !== 'undefined';"
        )
        if not hook_exists:
            raise RuntimeError(
                "Hook 未注入：页面没有按旧版阅读器加载，请检查网络或稍后重试"
            )
        script = "canvasContextHandler.data.complete;"
        time0 = time.time()
        while time.time() - time0 < 10:
            result = await self._page.evaluate(script)
            if result:
                break
            await asyncio.sleep(1)
        script = "canvasContextHandler.data.markdown;"
        result = await self._page.evaluate(script)
        if not result:
            await self._page.evaluate("canvasContextHandler.updateMarkdown();")
            result = await self._page.evaluate(script)
            if not result:
                raise RuntimeError("Wait for creating markdown timeout")
        return result

    async def _check_next_page(self) -> None:
        while True:
            try:
                await self.wait_for_selector("button.readerFooter_button", timeout=60)
            except pyppeteer.errors.TimeoutError:
                logging.info("[%s] load selector timeout " % self.__class__.__name__)
                break
            result = await self._page.evaluate(
                "document.getElementsByClassName('readerFooter_button')[0].innerText;"
            )
            if result == "下一页":
                logging.info("[%s] Go to next page" % self.__class__.__name__)
                await self._page.evaluate(
                    r"canvasContextHandler.data.markdown += '\n\n';"
                )
                await self.pre_load_page()
                await self._page.click("button.readerFooter_button")
                await asyncio.sleep(random.uniform(0.6, 1.6))
            elif result == "下一章":
                break
            elif result.startswith("登录"):
                if self._headless:
                    raise utils.LoginRequiredError(
                        "无头模式下页面提示需要登录，可能是无头特征被识别，请改用窗口模式"
                    )
                raise utils.LoginRequiredError()
            else:
                raise NotImplementedError(result)

    def _get_chapter_url(self, chapter_id: str) -> str:
        return "%s%sk%s" % (
            self._chapter_root_url,
            self._book_id,
            utils.wr_hash(str(chapter_id)),
        )

    async def goto_chapter(self, chapter_id: str, timeout: int = 120) -> None:
        logging.info("[%s] Go to chapter %s" % (self.__class__.__name__, chapter_id))
        # await self.clear_cache()
        await self.pre_load_page()
        self._url = self._get_chapter_url(chapter_id)
        await self._page.goto(self._url, timeout=1000 * timeout)
        if self._force_legacy_reader:
            await self._ensure_legacy_reader()
        try:
            await self._check_next_page()
        except utils.LoginRequiredError as ex:
            if self._headless:
                raise
            logging.warning("[%s] Login required: %s" % (self.__class__.__name__, ex))
            await self.login()
            return await self.goto_chapter(chapter_id, timeout=timeout)

    async def _wait_for_legacy_reader(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            is_horizontal = await self._page.evaluate(
                "() => document.querySelector('.readerControls_item.isHorizontalReader') !== null"
            )
            if not is_horizontal:
                return True
            await asyncio.sleep(0.25)
        return False

    async def _ensure_legacy_reader(self) -> None:
        """如果页面处于横向（双栏）阅读器，点击“上下滚动阅读”切回旧版单栏阅读器。"""
        is_horizontal = await self._page.evaluate(
            "() => document.querySelector('.readerControls_item.isHorizontalReader') !== null"
        )
        if not is_horizontal:
            return

        logging.info("[%s] Switching to legacy reader" % self.__class__.__name__)
        clicked = await self._page.evaluate(
            "() => { const b = document.querySelector('.readerControls_item.isHorizontalReader'); if (b) { b.click(); return true; } return false; }"
        )
        if clicked and await self._wait_for_legacy_reader():
            return

        if clicked:
            logging.warning(
                "[%s] Reader mode button did not finish switching; trying config API"
                % self.__class__.__name__
            )

        try:
            headers = {
                "Referer": self.__class__.root_url,
                "Cookie": self._format_cookie(),
                "Content-Type": "application/json",
            }
            await asyncio.wait_for(
                utils.fetch(
                    self.__class__.root_url + "/web/user_config/modify",
                    method="POST",
                    headers=headers,
                    data='{"useHorizonReader":0}',
                ),
                timeout=15,
            )
            await asyncio.wait_for(
                self._page.reload({"waitUntil": "domcontentloaded", "timeout": 15000}),
                timeout=20,
            )
            if await self._wait_for_legacy_reader(timeout=5):
                return
        except Exception as exc:
            logging.warning(
                "[%s] Switch to legacy reader failed: %s"
                % (self.__class__.__name__, exc)
            )

        raise RuntimeError("Reader mode switch timed out")

    async def clear_cache(self) -> None:
        await self._page.evaluate("canvasContextHandler.clearCanvasCache();")
