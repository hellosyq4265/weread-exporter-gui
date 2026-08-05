import argparse
import asyncio
import logging
import os
import sys
from typing import Callable, Optional, List

if sys.version_info >= (3, 8):
    from typing import TYPE_CHECKING
else:
    from typing_extensions import TYPE_CHECKING

from . import utils, webpage


def patch_windows() -> None:
    bin_path: str = os.path.join(
        os.path.abspath(os.path.dirname(__file__)), "bin", "win32"
    )
    os.environ["PATH"] += ";" + bin_path
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(bin_path)  # pyright: ignore[reportUnusedCallResult]


def patch_macos() -> None:
    fallback_lib_path: str = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    paths = [path for path in fallback_lib_path.split(os.pathsep) if path]
    if "/opt/homebrew/lib" not in paths:
        paths.append("/opt/homebrew/lib")
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(paths)


def patch_generateRequestHash() -> None:
    from pyppeteer import network_manager

    orig_generateRequestHash: Callable[..., str] = network_manager.generateRequestHash

    def patched_generateRequestHash(request):
        request["headers"].pop("Origin", None)
        return orig_generateRequestHash(request)

    network_manager.generateRequestHash = patched_generateRequestHash


async def async_main() -> int:
    parser = argparse.ArgumentParser(
        prog="weread-exporter", description="WeRead book export cmdline tool"
    )
    parser.add_argument(
        "-b", "--book-id", help="book id"
    )  # pyright: ignore[reportUnusedCallResult]
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "-o",
        "--output-format",
        help="output file format",
        action="append",
        choices=["md", "epub", "pdf", "mobi", "txt"],
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--load-timeout",
        help="load chapter page timeout",
        type=int,
        default=60,
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--load-interval",
        help="base interval between chapters (seconds), random range is derived from it",
        type=int,
        default=30,
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--min-load-interval",
        help="minimum random interval between chapters (seconds)",
        type=int,
        default=None,
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--max-load-interval",
        help="maximum random interval between chapters (seconds)",
        type=int,
        default=None,
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--no-random-interval",
        help="disable the random wait between chapters",
        action="store_true",
        default=False,
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--css-file",
        help="overide default css style",
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--headless", help="chrome headless", action="store_true", default=False
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--single-page",
        help="open reader in single page mode (narrow window)",
        action="store_true",
        default=False,
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--force-login", help="force login first", action="store_true", default=False
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--use-default-profile",
        help="use default profile",
        action="store_true",
        default=False,
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--mock-user-agent",
        help="use mock user-agent",
        action="store_true",
        default=False,
    )
    parser.add_argument(  # pyright: ignore[reportUnusedCallResult]
        "--proxy-server",
        help="http proxy server, e.g. http://127.0.0.1:8888",
    )
    args = parser.parse_args()
    args.output_format = args.output_format or ["epub"]  # pyright: ignore[reportAny]
    if "mobi" in args.output_format and "epub" not in args.output_format:
        args.output_format.append("epub")  # pyright: ignore[reportUnusedCallResult]
    if not args.book_id:  # pyright: ignore[reportAny]
        parser.error("the following arguments are required: -b/--book-id")
    if args.book_id:  # pyright: ignore[reportAny]
        try:
            utils.validate_book_id(args.book_id)  # pyright: ignore[reportAny]
        except ValueError as ex:
            parser.error(str(ex))

    min_interval: Optional[int] = args.min_load_interval  # pyright: ignore[reportAny]
    max_interval: Optional[int] = args.max_load_interval  # pyright: ignore[reportAny]
    if min_interval is None and max_interval is None:
        min_interval = max(5, int(args.load_interval * 0.6))  # pyright: ignore[reportAny]
        max_interval = int(args.load_interval * 1.4)  # pyright: ignore[reportAny]
    elif min_interval is None:
        min_interval = max(1, max_interval - 10)
    elif max_interval is None:
        max_interval = min_interval + 10
    if max_interval < min_interval:
        min_interval, max_interval = max_interval, min_interval

    extra_css: Optional[str] = None
    if args.css_file:  # pyright: ignore[reportAny]
        if not os.path.isfile(args.css_file):  # pyright: ignore[reportAny]
            raise RuntimeError(
                "CSS file %s not exist" % args.css_file
            )  # pyright: ignore[reportAny]
        with open(args.css_file) as fp:  # pyright: ignore[reportAny]
            extra_css = fp.read()

    if "pdf" in args.output_format:
        utils.check_cairo_installed()

    from . import export

    if "_" in args.book_id:  # pyright: ignore[reportAny]
        # book list id
        book_list = [it["id"] for it in await utils.get_book_list(args.book_id)]
    else:
        book_list = [args.book_id]  # pyright: ignore[reportAny]

    for book_id in book_list:
        logging.info("Exporting book %s" % book_id)
        page = webpage.WeReadWebPage(
            book_id,
            cookie_path=os.path.join("cache", "cookie.txt"),
            webcache_path="cache",
        )
        if not await page.check_valid():
            logging.warning("Book %s status is invalid, stop exporting" % book_id)
            try:
                os.remove(os.path.join("cache", "cookie.txt"))
            except FileNotFoundError:
                pass
            except OSError as ex:
                logging.warning("Failed to remove temporary cookie file: %s" % ex)
            continue
        save_path = os.path.join("cache", book_id)
        output_dir = "output"
        if not os.path.isdir(output_dir):
            os.mkdir(output_dir)
        exporter = export.WeReadExporter(page, save_path)
        retry_count = 0
        try:
            while True:
                try:
                    await page.launch(
                        headless=args.headless,  # pyright: ignore[reportAny]
                        single_page=args.single_page,  # pyright: ignore[reportAny]
                        force_login=args.force_login,  # pyright: ignore[reportAny]
                        use_default_profile=args.use_default_profile,  # pyright: ignore[reportAny]
                        mock_user_agent=args.mock_user_agent,  # pyright: ignore[reportAny]
                        proxy_server=args.proxy_server,  # pyright: ignore[reportAny]
                    )
                except utils.BreakExportingError:
                    logging.info("Exit process...")
                    return -1
                except utils.LoginRequiredError as ex:
                    logging.error("Cookie 无效或已过期：%s" % ex)
                    return -1
                except RuntimeError:
                    retry_count += 1
                    if retry_count >= 5:
                        logging.error(
                            "Launch book %s home page failed too many times, stop exporting"
                            % book_id
                        )
                        return -1
                    logging.exception("Launch book %s home page failed" % book_id)
                    await page.close()
                    await asyncio.sleep(2)
                    continue

                try:
                    await exporter.export_markdown(
                        args.load_timeout,
                        args.load_interval,
                        min_interval,
                        max_interval,
                        random_interval=not args.no_random_interval,
                    )
                except utils.LoginRequiredError as ex:
                    logging.error("登录状态异常：%s" % ex)
                    return -1
                except utils.LoadChapterFailedError:
                    retry_count += 1
                    if retry_count >= 5:
                        logging.error(
                            "Load chapter failed too many times, stop exporting"
                        )
                        return -1
                    logging.warning("Load chapter failed, close browser and retry")
                    await page.close()
                else:
                    break
        finally:
            await page.close()
            try:
                os.remove(os.path.join("cache", "cookie.txt"))
            except FileNotFoundError:
                pass
            except OSError as ex:
                logging.warning("Failed to remove temporary cookie file: %s" % ex)

        await exporter.pre_process_markdown()
        title = await exporter.get_book_title()
        title = utils.format_filename(title)
        if "md" in args.output_format:
            save_path = os.path.join(output_dir, "%s.md" % title)
            if os.path.isfile(save_path):
                logging.info("File %s exist, ignore export" % save_path)
            else:
                await exporter.merge_markdown(save_path)
                logging.info("Save file %s complete" % save_path)
            exporter.copy_images(os.path.join(output_dir, "images"))
        if "epub" in args.output_format:
            save_path = os.path.join(output_dir, "%s.epub" % title)
            if os.path.isfile(save_path):
                logging.info("File %s exist, ignore export" % save_path)
            else:
                await exporter.markdown_to_epub(save_path, extra_css=extra_css)
                logging.info("Save file %s complete" % save_path)

        if "pdf" in args.output_format:
            save_path = os.path.join(output_dir, "%s.pdf" % title)
            if os.path.isfile(save_path):
                logging.info("File %s exist, ignore export" % save_path)
            else:
                image_format = "jpg"
                if sys.platform == "win32":
                    image_format = "png"
                await exporter.markdown_to_pdf(
                    save_path,
                    extra_css=extra_css,
                    image_format=image_format,
                )
                logging.info("Save file %s complete" % save_path)

        if "mobi" in args.output_format:
            if sys.platform != "linux":
                logging.error("Only linux system supported to export mobi format")
                return -1
            epub_path = os.path.join(output_dir, "%s.epub" % title)
            save_path = os.path.join(output_dir, "%s.mobi" % title)
            if os.path.isfile(save_path):
                logging.info("File %s exist, ignore export" % save_path)
            else:
                await exporter.epub_to_mobi(epub_path, save_path)
                if not os.path.isfile(save_path):
                    logging.warning("Create mobi file failed")
                    continue
                logging.info("Save file %s complete" % save_path)

        if "txt" in args.output_format:
            save_path = os.path.join(output_dir, "%s.txt" % title)
            if os.path.isfile(save_path):
                logging.info("File %s exist, ignore export" % save_path)
            else:
                await exporter.markdown_to_txt(save_path)
                logging.info("Save file %s complete" % save_path)
    return 0


def main() -> int:
    if sys.platform == "win32":
        patch_windows()
    elif sys.platform == "darwin":
        patch_macos()
    patch_generateRequestHash()
    logging.root.level = logging.INFO
    handler = logging.StreamHandler()
    fmt = "[%(asctime)s][%(levelname)s]%(message)s"
    formatter = logging.Formatter(fmt)
    handler.setFormatter(formatter)
    logging.root.addHandler(handler)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(async_main())
    except SystemExit:
        raise
    except:
        import traceback

        traceback.print_exc()
        return -1


if __name__ == "__main__":
    sys.exit(main())
