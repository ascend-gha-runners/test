#!/usr/bin/env python3
"""Verify explicitly pinned HTTP wheel samples, without installing/importing them.

JSON on stdout; exit 0 = pass, 1 = fail, 2 = not_verified. An unadvertised
metadata sidecar reports not_verified and does not gate wheel integrity.
"""

import argparse
import hashlib
from html.parser import HTMLParser
import json
import re
import sys
import urllib.parse
import urllib.request


CHUNK_SIZE = 64 * 1024


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def http_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("only absolute HTTP(S) URLs are supported")
    return url


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            attrs = dict(attrs)
            if attrs.get("href"):
                self.links.append(attrs)


def origin(url):
    parsed = urllib.parse.urlsplit(http_url(url))
    return parsed.scheme.lower(), parsed.netloc.lower()


def require_origin(url, expected_origin):
    if expected_origin is not None and origin(url) != expected_origin:
        raise ValueError("cross-origin URL rejected: %s; required index origin %s://%s" %
                         (url, *expected_origin))


class IndexOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, expected_origin):
        self.expected_origin = expected_origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Reject before urllib sends a request to the redirect destination.
        require_origin(newurl, self.expected_origin)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit, timeout, retain=False, required_origin=None):
    """Hash at most limit bytes; only retain bounded index HTML in memory."""
    http_url(url)
    require_origin(url, required_origin)
    request = urllib.request.Request(url, headers={
        "Accept-Encoding": "identity", "User-Agent": "ascend-wheel-integrity/1",
    })
    digest = hashlib.sha256()
    size = 0
    chunks = []
    open_url = (urllib.request.build_opener(IndexOriginRedirectHandler(required_origin)).open
                if required_origin is not None else urllib.request.urlopen)
    with open_url(request, timeout=timeout) as response:
        http_url(response.geturl())
        require_origin(response.geturl(), required_origin)
        if response.status != 200:
            raise ValueError("expected HTTP 200, got %s" % response.status)
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("unexpected content encoding; byte identity required")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > limit:
            raise ValueError("response exceeds byte limit")
        while True:
            chunk = response.read(min(CHUNK_SIZE, limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError("response exceeds byte limit")
            digest.update(chunk)
            if retain:
                chunks.append(chunk)
        if length is not None and size != int(length):
            raise ValueError("truncated response")
        return digest.hexdigest(), size, b"".join(chunks), response.geturl()


def hash_value(value):
    if re.fullmatch(r"[a-fA-F0-9]{64}", value or ""):
        return value.lower()
    return None


def verify(url, expected, limit, timeout, required_origin=None):
    result = {"url": url, "status": "not_verified", "expected_sha256": expected}
    try:
        require_origin(url, required_origin)
        if expected is None:
            result["reason"] = "missing or unsupported sha256 hash"
            return result
        actual, size, _, final_url = fetch(url, limit, timeout, required_origin=required_origin)
        result.update(actual_sha256=actual, bytes=size, final_url=final_url)
        result["status"] = "pass" if actual == expected else "fail"
        if actual != expected:
            result["reason"] = "sha256 mismatch"
    except Exception as exc:
        result.update(status="fail", reason=str(exc))
    return result


def combined(statuses):
    return "fail" if "fail" in statuses else (
        "not_verified" if "not_verified" in statuses else "pass")


def check_sample(index, pin, sample_count=1, timeout=20,
                 max_index_bytes=8 * 1024 * 1024,
                 max_metadata_bytes=8 * 1024 * 1024,
                 max_wheel_bytes=1024 * 1024 * 1024,
                 require_index_origin=False):
    """Select lexicographically first N matching wheel URLs, not host-compatible N."""
    package, version = pin.split("==", 1)
    package_url = index.rstrip("/") + "/" + normalize(package) + "/"
    report = {"index_url": index, "pin": pin, "package_url": package_url,
              "status": "not_verified", "wheels": []}
    try:
        required_origin = origin(index) if require_index_origin else None
        _, _, data, final_url = fetch(package_url, max_index_bytes, timeout, True,
                                      required_origin=required_origin)
        report["final_index_url"] = final_url
        parser = Links()
        parser.feed(data.decode("utf-8"))
        matches = {}
        for attrs in parser.links:
            url = urllib.parse.urljoin(final_url, attrs["href"])
            parsed = urllib.parse.urlsplit(url)
            filename = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
            parts = filename[:-4].split("-") if filename.endswith(".whl") else []
            if len(parts) not in (5, 6) or normalize(parts[0]) != normalize(package) or parts[1] != version:
                continue
            # Follow published paths without a hardcoded /whl rewrite.
            matches[url] = attrs
        report["matching_wheels"] = len(matches)
        for url in sorted(matches)[:sample_count]:
            attrs = matches[url]
            parsed = urllib.parse.urlsplit(url)
            fragments = urllib.parse.parse_qs(parsed.fragment)
            hashes = fragments.get("sha256", [])
            expected = hash_value(hashes[0]) if len(hashes) == 1 else None
            clean = urllib.parse.urlunsplit(parsed._replace(fragment=""))
            wheel = verify(clean, expected, max_wheel_bytes, timeout, required_origin)
            advertisements = [attrs[key] for key in
                              ("data-core-metadata", "data-dist-info-metadata")
                              if key in attrs]
            advertised = bool(advertisements)
            metadata = {"status": "not_verified", "reason": "not advertised"}
            if advertised:
                meta_url = urllib.parse.urlunsplit(parsed._replace(
                    path=parsed.path + ".metadata", fragment=""))
                meta_hashes = [hash_value(value[7:]) if value and value.startswith("sha256=")
                               else None for value in advertisements]
                if len(set(meta_hashes)) > 1:
                    metadata = {"status": "fail", "url": meta_url,
                                "reason": "conflicting metadata hash attributes"}
                else:
                    metadata = verify(meta_url, meta_hashes[0], max_metadata_bytes, timeout,
                                      required_origin)
            statuses = [wheel["status"]] + ([metadata["status"]] if advertised else [])
            report["wheels"].append({"wheel": wheel, "metadata": metadata,
                                     "metadata_advertised": advertised,
                                     "status": combined(statuses)})
        if report["wheels"]:
            report["status"] = combined([w["status"] for w in report["wheels"]])
        else:
            report["reason"] = "no wheels for exact pin; nothing verified"
    except Exception as exc:
        report.update(status="fail", reason=str(exc))
    return report


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", nargs=2, action="append", required=True,
                        metavar=("INDEX_URL", "PACKAGE==VERSION"),
                        help="repeat for torch and explicitly selected dependency pins")
    parser.add_argument("--sample-count", type=positive_int, default=1,
                        help="max wheels per pin, deterministic URL sort (default: 1)")
    parser.add_argument("--require-index-origin", action="store_true",
                        help="require resolved URLs and all redirects to stay on each sample index scheme/netloc")
    parser.add_argument("--timeout", type=positive_int, default=20,
                        help="per-socket HTTP timeout; workflow must also set a job timeout")
    parser.add_argument("--max-index-bytes", type=positive_int, default=8 * 1024 * 1024)
    parser.add_argument("--max-metadata-bytes", type=positive_int, default=8 * 1024 * 1024)
    parser.add_argument("--max-wheel-bytes", type=positive_int, default=1024 * 1024 * 1024)
    args = parser.parse_args(argv)
    for index, pin in args.sample:
        try:
            http_url(index)
        except ValueError as exc:
            parser.error(str(exc))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9_.+!]*", pin):
            parser.error("sample must use an exact package==version pin")
    results = [check_sample(index, pin, args.sample_count, args.timeout,
                            args.max_index_bytes, args.max_metadata_bytes,
                            args.max_wheel_bytes, args.require_index_origin)
               for index, pin in args.sample]
    status = combined([r["status"] for r in results])
    print(json.dumps({"status": status,
                      "scope": "configured wheel samples only; no dependency resolution, installation or runtime validation",
                      "samples": results}, indent=2))
    return {"pass": 0, "fail": 1, "not_verified": 2}[status]


if __name__ == "__main__":
    sys.exit(main())
