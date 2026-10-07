"""Deterministic loopback-only fixtures; no production endpoints or pip needed."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
import unittest
from unittest.mock import patch
import urllib.error

import wheel_integrity_check as integrity


WHEEL = b"fixture wheel bytes\x00\xff" * 10000
META = b"Metadata-Version: 2.1\nName: torch\nVersion: 2.4.0+cpu\n\n"
FILENAME = "torch-2.4.0%2Bcpu-cp311-cp311-linux_x86_64.whl"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class IntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = {}
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append(self.path)
                status, data, headers = cls.routes.get(self.path, (404, b"absent", {}))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%s" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.routes.clear()
        self.requests.clear()

    def fixture(self, href=None, attribute=None, wheel=WHEEL, metadata=META,
                wheel_hash=True, metadata_hash=True):
        path = "/whl/cpu/" + FILENAME
        href = href or path
        if wheel_hash:
            href += "#sha256=" + sha(WHEEL)
        attr = ""
        if attribute:
            attr = ' %s="%s"' % (attribute, "sha256=" + sha(META) if metadata_hash else "true")
        self.routes["/whl/cpu/torch/"] = (200, ('<a%s href="%s">wheel</a>' % (attr, href)).encode(), {})
        self.routes[path] = (200, wheel, {})
        self.routes[path + ".metadata"] = (200, metadata, {})

    def check(self, **kwargs):
        return integrity.check_sample(self.base + "/whl/cpu", "torch==2.4.0+cpu", **kwargs)

    def test_legacy_relative_root_and_absolute_links(self):
        for href in ("../" + FILENAME, "/whl/cpu/" + FILENAME,
                     self.base + "/whl/cpu/" + FILENAME):
            with self.subTest(href=href):
                self.fixture(href=href, attribute="data-core-metadata")
                result = self.check()
                self.assertEqual(result["status"], "pass")
                self.assertEqual(result["wheels"][0]["wheel"]["bytes"], len(WHEEL))
                self.assertEqual(result["wheels"][0]["metadata"]["actual_sha256"], sha(META))

    def test_redirected_index_resolves_relative_link(self):
        self.fixture(attribute="data-core-metadata")
        page = self.routes["/whl/cpu/torch/"][1].replace(
            ("/whl/cpu/" + FILENAME).encode(), FILENAME.encode())
        self.routes["/whl/cpu/torch/"] = (302, b"", {"Location": "/redirected/"})
        self.routes["/redirected/"] = (200, page, {})
        self.routes["/redirected/" + FILENAME] = (302, b"", {"Location": "/whl/cpu/" + FILENAME})
        self.routes["/redirected/" + FILENAME + ".metadata"] = (200, META, {})
        self.assertEqual(self.check()["status"], "pass")

    def test_origin_link_path(self):
        path = "/whl/origin/cpu/" + FILENAME
        self.fixture(href=path, attribute="data-core-metadata")
        self.routes[path] = (200, WHEEL, {})
        self.routes[path + ".metadata"] = (200, META, {})
        self.assertEqual(self.check()["status"], "pass")
        self.assertIn(path, self.requests)

    @contextlib.contextmanager
    def external_server(self):
        requests = []
        routes = self.routes
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                status, data, headers = routes.get(self.path, (404, b"absent", {}))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield "http://127.0.0.1:%s" % server.server_port, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_require_index_origin_rejects_external_links_before_request(self):
        with self.external_server() as (external, requests):
            self.fixture(href=external + "/whl/cpu/" + FILENAME,
                         attribute="data-core-metadata")
            result = self.check(require_index_origin=True)
            self.assertEqual(result["status"], "fail")
            for key in ("wheel", "metadata"):
                self.assertEqual(result["wheels"][0][key]["status"], "fail")
                self.assertIn("cross-origin", result["wheels"][0][key]["reason"])
            self.assertEqual(requests, [])
            self.assertEqual(self.check()["status"], "pass")
            self.assertEqual(len(requests), 2)

    def test_require_index_origin_blocks_index_wheel_metadata_redirects(self):
        with self.external_server() as (external, requests):
            for resource in ("/whl/cpu/torch/", "/whl/cpu/" + FILENAME,
                             "/whl/cpu/" + FILENAME + ".metadata"):
                with self.subTest(resource=resource):
                    self.fixture(attribute="data-core-metadata")
                    target = "/external" + resource
                    self.routes[target] = self.routes[resource]
                    self.routes[resource] = (302, b"", {"Location": external + target})
                    requests.clear()
                    result = self.check(require_index_origin=True)
                    self.assertEqual(result["status"], "fail")
                    detail = (result if resource.endswith("torch/") else
                              result["wheels"][0]["metadata" if resource.endswith(".metadata") else "wheel"])
                    self.assertIn("cross-origin", detail["reason"])
                    self.assertEqual(requests, [])
                    self.assertEqual(self.check()["status"], "pass")
                    expected_requests = ([target, "/whl/cpu/" + FILENAME,
                                          "/whl/cpu/" + FILENAME + ".metadata"]
                                         if resource.endswith("torch/") else [target])
                    self.assertEqual(requests, expected_requests)

    def test_origin_comparison_includes_scheme_and_port(self):
        allowed = integrity.origin(self.base)
        for url in (self.base.replace("http:", "https:"), "http://127.0.0.1"):
            with self.assertRaisesRegex(ValueError, "cross-origin"):
                integrity.require_origin(url, allowed)
        integrity.require_origin(self.base + "/another/path?query=1", allowed)

    def test_require_index_origin_cli_and_same_origin_redirects(self):
        self.fixture(attribute="data-core-metadata")
        for resource in ("/whl/cpu/torch/", "/whl/cpu/" + FILENAME,
                         "/whl/cpu/" + FILENAME + ".metadata"):
            target = "/same" + resource
            self.routes[target] = self.routes[resource]
            self.routes[resource] = (302, b"", {"Location": target})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = integrity.main(["--require-index-origin", "--sample",
                                   self.base + "/whl/cpu", "torch==2.4.0+cpu"])
        self.assertEqual(code, 0)
        sample = json.loads(output.getvalue())["samples"][0]
        self.assertEqual(sample["final_index_url"], self.base + "/same/whl/cpu/torch/")
        for key in ("wheel", "metadata"):
            self.assertTrue(sample["wheels"][0][key]["final_url"].startswith(self.base + "/same/"))

    def test_declared_length_bounds_and_truncation(self):
        for length in (str(len(WHEEL) + 1), str(2 * 1024 * 1024 * 1024)):
            self.fixture()
            self.routes["/whl/cpu/" + FILENAME] = (200, WHEEL, {"Content-Length": length})
            self.assertEqual(self.check()["status"], "fail")

    def test_metadata_legacy_attribute(self):
        self.fixture(attribute="data-dist-info-metadata")
        self.assertEqual(self.check()["status"], "pass")

    def test_corrupt_wheel(self):
        self.fixture(wheel=WHEEL + b"corruption")
        self.assertEqual(self.check()["wheels"][0]["wheel"]["status"], "fail")

    def test_corrupt_metadata(self):
        self.fixture(attribute="data-core-metadata", metadata=META + b"corruption")
        self.assertEqual(self.check()["status"], "fail")

    def test_missing_wheel_hash_is_not_verified(self):
        self.fixture(wheel_hash=False, attribute="data-core-metadata")
        self.assertEqual(self.check()["status"], "not_verified")

    def test_advertised_metadata_without_hash_is_not_verified(self):
        self.fixture(attribute="data-core-metadata", metadata_hash=False)
        self.assertEqual(self.check()["status"], "not_verified")

    def test_unadvertised_metadata_not_requested(self):
        self.fixture()
        result = self.check()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["wheels"][0]["metadata"]["status"], "not_verified")
        self.assertFalse(any(".metadata" in url for url in self.requests))

    def test_http_failures_index_wheel_metadata(self):
        for resource in ("/whl/cpu/torch/", "/whl/cpu/" + FILENAME,
                         "/whl/cpu/" + FILENAME + ".metadata"):
            with self.subTest(resource=resource):
                self.fixture(attribute="data-core-metadata")
                self.routes[resource] = (503, b"unavailable", {})
                self.assertEqual(self.check()["status"], "fail")

    def test_network_error_and_timeout(self):
        for error in (urllib.error.URLError("fixture connection refused"), TimeoutError("fixture timeout")):
            with patch.object(integrity.urllib.request, "urlopen", side_effect=error):
                self.assertEqual(self.check()["status"], "fail")

    def test_streaming_byte_bounds(self):
        self.fixture(attribute="data-core-metadata")
        for kwargs in ({"max_index_bytes": 10}, {"max_wheel_bytes": 10},
                       {"max_metadata_bytes": 10}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.check(**kwargs)["status"], "fail")

    def test_bad_hash_and_content_encoding(self):
        self.fixture()
        route = self.routes["/whl/cpu/torch/"]
        self.routes["/whl/cpu/torch/"] = (200, route[1].replace(sha(WHEEL).encode(), b"invalid"), {})
        self.assertEqual(self.check()["status"], "not_verified")
        self.fixture()
        self.routes["/whl/cpu/" + FILENAME] = (200, WHEEL, {"Content-Encoding": "gzip"})
        self.assertEqual(self.check()["status"], "fail")

    def test_missing_pin_and_explicit_sampling(self):
        self.fixture()
        self.assertEqual(integrity.check_sample(self.base + "/whl/cpu", "torch==9.9")["status"], "not_verified")
        page = self.routes["/whl/cpu/torch/"][1]
        second = page.replace(b"cp311", b"cp312")
        self.routes["/whl/cpu/torch/"] = (200, second + page, {})
        result = self.check()
        self.assertEqual(result["matching_wheels"], 2)
        self.assertEqual(len(result["wheels"]), 1)
        self.assertIn("cp311", result["wheels"][0]["wheel"]["url"])

    def test_query_preserved_metadata_suffix_before_query(self):
        self.fixture(href="/whl/cpu/" + FILENAME + "?download=1", attribute="data-core-metadata")
        self.routes["/whl/cpu/" + FILENAME + "?download=1"] = (200, WHEEL, {})
        self.routes["/whl/cpu/" + FILENAME + ".metadata?download=1"] = (200, META, {})
        self.assertEqual(self.check()["status"], "pass")

    def test_cli_torch_and_selected_dependency_exit_codes(self):
        self.fixture()
        dep = b"dependency wheel"
        self.routes["/pypi/simple/filelock/"] = (200, (
            '<a href="/packages/filelock-3.16.1-py3-none-any.whl#sha256=%s">dep</a>' % sha(dep)).encode(), {})
        self.routes["/packages/filelock-3.16.1-py3-none-any.whl"] = (200, dep, {})
        args = ["--sample", self.base + "/whl/cpu", "torch==2.4.0+cpu",
                "--sample", self.base + "/pypi/simple", "filelock==3.16.1"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(integrity.main(args), 0)
        self.assertEqual(len(json.loads(output.getvalue())["samples"]), 2)
        for wheel_hash, wheel, expected in ((False, WHEEL, 2), (True, b"bad", 1)):
            self.fixture(wheel_hash=wheel_hash, wheel=wheel)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(integrity.main(args), expected)


if __name__ == "__main__":
    unittest.main()
