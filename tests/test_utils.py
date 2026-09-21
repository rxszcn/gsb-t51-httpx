import json
import logging
import os
import random

import pytest

import httpx
from httpx._utils import URLPattern, get_environment_proxies


@pytest.mark.parametrize(
    "encoding",
    (
        "utf-32",
        "utf-8-sig",
        "utf-16",
        "utf-8",
        "utf-16-be",
        "utf-16-le",
        "utf-32-be",
        "utf-32-le",
    ),
)
def test_encoded(encoding):
    content = '{"abc": 123}'.encode(encoding)
    response = httpx.Response(200, content=content)
    assert response.json() == {"abc": 123}


def test_bad_utf_like_encoding():
    content = b"\x00\x00\x00\x00"
    response = httpx.Response(200, content=content)
    with pytest.raises(json.decoder.JSONDecodeError):
        response.json()


@pytest.mark.parametrize(
    ("encoding", "expected"),
    (
        ("utf-16-be", "utf-16"),
        ("utf-16-le", "utf-16"),
        ("utf-32-be", "utf-32"),
        ("utf-32-le", "utf-32"),
    ),
)
def test_guess_by_bom(encoding, expected):
    content = '\ufeff{"abc": 123}'.encode(encoding)
    response = httpx.Response(200, content=content)
    assert response.json() == {"abc": 123}


def test_logging_request(server, caplog):
    caplog.set_level(logging.INFO)
    with httpx.Client() as client:
        response = client.get(server.url)
        assert response.status_code == 200

    assert caplog.record_tuples == [
        (
            "httpx",
            logging.INFO,
            'HTTP Request: GET http://127.0.0.1:8000/ "HTTP/1.1 200 OK"',
        )
    ]


def test_logging_redirect_chain(server, caplog):
    caplog.set_level(logging.INFO)
    with httpx.Client(follow_redirects=True) as client:
        response = client.get(server.url.copy_with(path="/redirect_301"))
        assert response.status_code == 200

    assert caplog.record_tuples == [
        (
            "httpx",
            logging.INFO,
            "HTTP Request: GET http://127.0.0.1:8000/redirect_301"
            ' "HTTP/1.1 301 Moved Permanently"',
        ),
        (
            "httpx",
            logging.INFO,
            'HTTP Request: GET http://127.0.0.1:8000/ "HTTP/1.1 200 OK"',
        ),
    ]


@pytest.mark.parametrize(
    ["environment", "proxies"],
    [
        ({}, {}),
        ({"HTTP_PROXY": "http://127.0.0.1"}, {"http://": "http://127.0.0.1"}),
        (
            {"https_proxy": "http://127.0.0.1", "HTTP_PROXY": "https://127.0.0.1"},
            {"https://": "http://127.0.0.1", "http://": "https://127.0.0.1"},
        ),
        ({"all_proxy": "http://127.0.0.1"}, {"all://": "http://127.0.0.1"}),
        ({"TRAVIS_APT_PROXY": "http://127.0.0.1"}, {}),
        ({"no_proxy": "127.0.0.1"}, {"all://127.0.0.1": None}),
        ({"no_proxy": "192.168.0.0/16"}, {"all://192.168.0.0/16": None}),
        ({"no_proxy": "10.0.0.0/8"}, {"all://10.0.0.0/8": None}),
        ({"no_proxy": "2001:db8::/32"}, {"all://[2001:db8::]/32": None}),
        (
            {"no_proxy": "192.168.0.0/16,2001:db8::/32"},
            {
                "all://192.168.0.0/16": None,
                "all://[2001:db8::]/32": None,
            },
        ),
        ({"no_proxy": "::1"}, {"all://[::1]": None}),
        ({"no_proxy": "localhost"}, {"all://localhost": None}),
        ({"no_proxy": "github.com"}, {"all://*github.com": None}),
        ({"no_proxy": ".github.com"}, {"all://*.github.com": None}),
        ({"no_proxy": "http://github.com"}, {"http://github.com": None}),
    ],
)
def test_get_environment_proxies(environment, proxies):
    os.environ.update(environment)

    assert get_environment_proxies() == proxies


@pytest.mark.parametrize(
    ["pattern", "url", "expected"],
    [
        ("http://example.com", "http://example.com", True),
        ("http://example.com", "https://example.com", False),
        ("http://example.com", "http://other.com", False),
        ("http://example.com:123", "http://example.com:123", True),
        ("http://example.com:123", "http://example.com:456", False),
        ("http://example.com:123", "http://example.com", False),
        ("all://example.com", "http://example.com", True),
        ("all://example.com", "https://example.com", True),
        ("http://", "http://example.com", True),
        ("http://", "https://example.com", False),
        ("all://", "https://example.com:123", True),
        ("", "https://example.com:123", True),
        # IPv4 networks match any address in range, port is ignored.
        ("all://192.168.0.0/16", "http://192.168.0.0", True),
        ("all://192.168.0.0/16", "http://192.168.0.5", True),
        ("all://192.168.0.0/16", "http://192.168.255.254", True),
        ("all://192.168.0.0/16", "http://192.168.0.5:8080", True),
        ("all://192.168.0.0/16", "http://192.169.0.5", False),
        ("all://10.0.0.0/8", "http://10.1.2.3", True),
        ("all://10.0.0.0/8", "http://192.168.0.5", False),
        # Networks don't match hostnames or the wrong scheme.
        ("all://192.168.0.0/16", "http://example.com", False),
        ("https://10.0.0.0/8", "http://10.1.2.3", False),
        ("https://10.0.0.0/8", "https://10.1.2.3", True),
        # IPv6 networks match any address in range, port is ignored.
        ("all://[2001:db8::]/32", "http://[2001:db8::1]", True),
        ("all://[2001:db8::]/32", "http://[2001:db8:abcd::1]:8080", True),
        ("all://[2001:db8::]/32", "http://[2001:db9::1]", False),
        ("all://[fe80::]/10", "http://[fe80::1]", True),
        # Non-strict network parsing tolerates host bits being set.
        ("all://192.168.1.5/24", "http://192.168.1.9", True),
    ],
)
def test_url_matches(pattern, url, expected):
    pattern = URLPattern(pattern)
    assert pattern.matches(httpx.URL(url)) == expected


def test_pattern_priority():
    matchers = [
        URLPattern("all://"),
        URLPattern("http://"),
        URLPattern("http://example.com"),
        URLPattern("http://example.com:123"),
    ]
    random.shuffle(matchers)
    assert sorted(matchers) == [
        URLPattern("http://example.com:123"),
        URLPattern("http://example.com"),
        URLPattern("http://"),
        URLPattern("all://"),
    ]


def test_pattern_priority_networks():
    # Exact hosts beat IP networks, longer prefixes beat shorter ones,
    # and networks beat wildcard domains. Ordering must be deterministic
    # regardless of insertion order.
    matchers = [
        URLPattern("all://"),
        URLPattern("all://*example.com"),
        URLPattern("all://10.0.0.0/8"),
        URLPattern("all://10.0.1.0/16"),
        URLPattern("all://10.0.1.5"),
    ]
    random.shuffle(matchers)
    assert sorted(matchers) == [
        URLPattern("all://10.0.1.5"),
        URLPattern("all://10.0.1.0/16"),
        URLPattern("all://10.0.0.0/8"),
        URLPattern("all://*example.com"),
        URLPattern("all://"),
    ]
