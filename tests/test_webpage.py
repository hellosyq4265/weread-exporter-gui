import asyncio
import json

from weread_exporter import utils
from weread_exporter.webpage import WeReadWebPage


class FakePage:
    def __init__(self):
        self.navigation_called = False
        self.mode_checks = 0

    async def evaluate(self, script):
        if "b.click" in script:
            return True
        if "querySelector" in script:
            self.mode_checks += 1
            return self.mode_checks == 1
        raise AssertionError(script)

    async def waitForNavigation(self, *args, **kwargs):
        self.navigation_called = True


def test_legacy_reader_switch_does_not_wait_for_navigation():
    async def run_test():
        fake_page = FakePage()
        page = object.__new__(WeReadWebPage)
        page._page = fake_page
        await page._ensure_legacy_reader()
        assert not fake_page.navigation_called

    asyncio.run(run_test())


def test_user_info_accepts_string_zero_and_refreshes_all_cookies():
    async def run_test():
        page = object.__new__(WeReadWebPage)
        page._cookie = {"wr_vid": "vid", "wr_skey": "old"}
        page._cookie_path = None

        responses = [
            json.dumps({"errCode": -2012}).encode("utf-8"),
            json.dumps({"errCode": "0", "name": "test"}).encode("utf-8"),
        ]
        original_fetch = utils.fetch

        async def fake_fetch(url, headers=None, respond_with_headers=False, **kwargs):
            if respond_with_headers:
                return (
                    200,
                    {
                        "Set-Cookie": (
                            "wr_skey=new; Path=/\n"
                            "wr_name=test; Path=/"
                        )
                    },
                    b"",
                )
            return responses.pop(0)

        utils.fetch = fake_fetch
        try:
            info = await page.get_user_info()
        finally:
            utils.fetch = original_fetch

        assert info["name"] == "test"
        assert page._cookie["wr_skey"] == "new"
        assert page._cookie["wr_name"] == "test"

    asyncio.run(run_test())
