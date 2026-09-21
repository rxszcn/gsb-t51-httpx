from __future__ import annotations

import ipaddress
import os
import re
import typing
from urllib.request import getproxies

from ._types import PrimitiveData

if typing.TYPE_CHECKING:  # pragma: no cover
    from ._urls import URL


def primitive_value_to_str(value: PrimitiveData) -> str:
    """
    Coerce a primitive data type into a string value.

    Note that we prefer JSON-style 'true'/'false' for boolean values here.
    """
    if value is True:
        return "true"
    elif value is False:
        return "false"
    elif value is None:
        return ""
    return str(value)


def get_environment_proxies() -> dict[str, str | None]:
    """Gets proxy information from the environment"""

    # urllib.request.getproxies() falls back on System
    # Registry and Config for proxies on Windows and macOS.
    # We don't want to propagate non-HTTP proxies into
    # our configuration such as 'TRAVIS_APT_PROXY'.
    proxy_info = getproxies()
    mounts: dict[str, str | None] = {}

    for scheme in ("http", "https", "all"):
        if proxy_info.get(scheme):
            hostname = proxy_info[scheme]
            mounts[f"{scheme}://"] = (
                hostname if "://" in hostname else f"http://{hostname}"
            )

    no_proxy_hosts = [host.strip() for host in proxy_info.get("no", "").split(",")]
    for hostname in no_proxy_hosts:
        # See https://curl.haxx.se/libcurl/c/CURLOPT_NOPROXY.html for details
        # on how names in `NO_PROXY` are handled.
        if hostname == "*":
            # If NO_PROXY=* is used or if "*" occurs as any one of the comma
            # separated hostnames, then we should just bypass any information
            # from HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, and always ignore
            # proxies.
            return {}
        elif hostname:
            # NO_PROXY=.google.com is marked as "all://*.google.com,
            #   which disables "www.google.com" but not "google.com"
            # NO_PROXY=google.com is marked as "all://*google.com,
            #   which disables "www.google.com" and "google.com".
            #   (But not "wwwgoogle.com")
            # NO_PROXY can include domains, IPv6, IPv4 addresses,
            # IPv4/IPv6 CIDR ranges and "localhost".
            #   NO_PROXY=example.com,::1,localhost,192.168.0.0/16,2001:db8::/32
            if "://" in hostname:
                mounts[hostname] = None
            elif is_ip_network(hostname):
                # CIDR ranges are treated as IP networks, so any address
                # inside the range bypasses the proxy, matching curl.
                if is_ipv6_hostname(hostname.split("/")[0]):
                    address, prefix = hostname.split("/")
                    mounts[f"all://[{address}]/{prefix}"] = None
                else:
                    mounts[f"all://{hostname}"] = None
            elif is_ipv4_hostname(hostname):
                mounts[f"all://{hostname}"] = None
            elif is_ipv6_hostname(hostname):
                mounts[f"all://[{hostname}]"] = None
            elif hostname.lower() == "localhost":
                mounts[f"all://{hostname}"] = None
            else:
                mounts[f"all://*{hostname}"] = None

    return mounts


def to_bytes(value: str | bytes, encoding: str = "utf-8") -> bytes:
    return value.encode(encoding) if isinstance(value, str) else value


def to_str(value: str | bytes, encoding: str = "utf-8") -> str:
    return value if isinstance(value, str) else value.decode(encoding)


def to_bytes_or_str(value: str, match_type_of: typing.AnyStr) -> typing.AnyStr:
    return value if isinstance(match_type_of, str) else value.encode()


def unquote(value: str) -> str:
    return value[1:-1] if value[0] == value[-1] == '"' else value


def peek_filelike_length(stream: typing.Any) -> int | None:
    """
    Given a file-like stream object, return its length in number of bytes
    without reading it into memory.
    """
    try:
        # Is it an actual file?
        fd = stream.fileno()
        # Yup, seems to be an actual file.
        length = os.fstat(fd).st_size
    except (AttributeError, OSError):
        # No... Maybe it's something that supports random access, like `io.BytesIO`?
        try:
            # Assuming so, go to end of stream to figure out its length,
            # then put it back in place.
            offset = stream.tell()
            length = stream.seek(0, os.SEEK_END)
            stream.seek(offset)
        except (AttributeError, OSError):
            # Not even that? Sorry, we're doomed...
            return None

    return length


class URLPattern:
    """
    A utility class currently used for making lookups against proxy keys...

    # Wildcard matching...
    >>> pattern = URLPattern("all://")
    >>> pattern.matches(httpx.URL("http://example.com"))
    True

    # Witch scheme matching...
    >>> pattern = URLPattern("https://")
    >>> pattern.matches(httpx.URL("https://example.com"))
    True
    >>> pattern.matches(httpx.URL("http://example.com"))
    False

    # With domain matching...
    >>> pattern = URLPattern("https://example.com")
    >>> pattern.matches(httpx.URL("https://example.com"))
    True
    >>> pattern.matches(httpx.URL("http://example.com"))
    False
    >>> pattern.matches(httpx.URL("https://other.com"))
    False

    # Wildcard scheme, with domain matching...
    >>> pattern = URLPattern("all://example.com")
    >>> pattern.matches(httpx.URL("https://example.com"))
    True
    >>> pattern.matches(httpx.URL("http://example.com"))
    True
    >>> pattern.matches(httpx.URL("https://other.com"))
    False

    # With port matching...
    >>> pattern = URLPattern("https://example.com:1234")
    >>> pattern.matches(httpx.URL("https://example.com:1234"))
    True
    >>> pattern.matches(httpx.URL("https://example.com"))
    False
    """

    def __init__(self, pattern: str) -> None:
        from ._urls import URL

        if pattern and ":" not in pattern:
            raise ValueError(
                f"Proxy keys should use proper URL forms rather "
                f"than plain scheme strings. "
                f'Instead of "{pattern}", use "{pattern}://"'
            )

        self.pattern = pattern
        network = _pattern_network(pattern)
        if network is not None:
            # An IP network, such as "all://192.168.0.0/16" or
            # "all://[2001:db8::]/32", matches any address in the range.
            url = URL(_pattern_without_network(pattern))
            self.scheme = "" if url.scheme == "all" else url.scheme
            self.host = str(network.network_address)
            self.port = None
            self.host_regex = None
            self.host_kind = 1
            self.network: (
                ipaddress.IPv4Network | ipaddress.IPv6Network | None
            ) = network
            return

        self.network = None
        url = URL(pattern)
        self.scheme = "" if url.scheme == "all" else url.scheme
        self.host = "" if url.host == "*" else url.host
        self.port = url.port
        if not url.host or url.host == "*":
            self.host_regex: typing.Pattern[str] | None = None
            self.host_kind = 3
        elif url.host.startswith("*."):
            # *.example.com should match "www.example.com", but not "example.com"
            domain = re.escape(url.host[2:])
            self.host_regex = re.compile(f"^.+\\.{domain}$")
            self.host_kind = 2
        elif url.host.startswith("*"):
            # *example.com should match "www.example.com" and "example.com"
            domain = re.escape(url.host[1:])
            self.host_regex = re.compile(f"^(.+\\.)?{domain}$")
            self.host_kind = 2
        else:
            # example.com should match "example.com" but not "www.example.com"
            domain = re.escape(url.host)
            self.host_regex = re.compile(f"^{domain}$")
            self.host_kind = 0

    def matches(self, other: URL) -> bool:
        if self.scheme and self.scheme != other.scheme:
            return False
        if self.network is not None:
            # Ports are intentionally ignored when matching IP networks.
            try:
                return ipaddress.ip_address(other.host) in self.network
            except ValueError:
                return False
        if (
            self.host
            and self.host_regex is not None
            and not self.host_regex.match(other.host)
        ):
            return False
        if self.port is not None and self.port != other.port:
            return False
        return True

    @property
    def priority(self) -> tuple[int, int, int, int, str]:
        """
        The priority allows URLPattern instances to be sortable, so that
        we can match from most specific to least specific.
        """
        # URLs with a port should take priority over URLs without a port.
        port_priority = 0 if self.port is not None else 1
        # Exact hosts and IP networks take priority over wildcard hosts,
        # and more specific (longer-prefix) networks match first.
        if self.network is not None:
            host_priority = -self.network.prefixlen
        else:
            host_priority = -len(self.host)
        # Longer schemes should match first.
        scheme_priority = -len(self.scheme)
        # The final tiebreaker keeps ordering independent of insertion order.
        return (
            port_priority,
            self.host_kind,
            host_priority,
            scheme_priority,
            self.pattern,
        )

    def __hash__(self) -> int:
        return hash(self.pattern)

    def __lt__(self, other: URLPattern) -> bool:
        return self.priority < other.priority

    def __eq__(self, other: typing.Any) -> bool:
        return isinstance(other, URLPattern) and self.pattern == other.pattern


def is_ipv4_hostname(hostname: str) -> bool:
    try:
        ipaddress.IPv4Address(hostname.split("/")[0])
    except Exception:
        return False
    return True


def is_ipv6_hostname(hostname: str) -> bool:
    try:
        ipaddress.IPv6Address(hostname.split("/")[0])
    except Exception:
        return False
    return True


def is_ip_network(hostname: str) -> bool:
    try:
        ipaddress.ip_network(hostname, strict=False)
    except ValueError:
        return False
    return "/" in hostname


# Matches an IP network in the authority of a proxy key, such as
# "all://192.168.0.0/16" or "all://[2001:db8::]/32". The CIDR prefix
# is a trailing "/N" component of the URL.
_IP_NETWORK_RE = re.compile(
    r"^(?P<base>[^:]*://(?:\[(?P<ipv6>[^\]]*)\]|(?P<ipv4>[^/:\]]*)))/(?P<prefix>\d+)$"
)


def _pattern_network(
    pattern: str,
) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    match = _IP_NETWORK_RE.match(pattern)
    if match is None:
        return None
    address = match.group("ipv6") or match.group("ipv4")
    try:
        return ipaddress.ip_network(
            f"{address}/{match.group('prefix')}", strict=False
        )
    except ValueError:
        return None


def _pattern_without_network(pattern: str) -> str:
    match = _IP_NETWORK_RE.match(pattern)
    assert match is not None
    return match.group("base")
