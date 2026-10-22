"""Bounded public-page transport. No ambient proxy, cookies or automatic resources."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import time
import threading
from urllib.parse import urlsplit, urlunsplit, urljoin
from urllib.robotparser import RobotFileParser

MAX_CONTENT = 512 * 1024
TIME_LIMIT = 20
USER_AGENT = 'ProspectReviewResearch/0.4'


def public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified or address.is_loopback or address.is_link_local:
        raise ValueError('Only globally routable public addresses are allowed')
    if isinstance(address, ipaddress.IPv4Address) and address in ipaddress.ip_network('192.0.0.0/24'):
        raise ValueError('Special-purpose address range is unsupported')
    if isinstance(address, ipaddress.IPv6Address) and (address not in ipaddress.ip_network('2000::/3') or address in ipaddress.ip_network('2001::/23')):
        raise ValueError('Only ordinary global IPv6 unicast ranges are supported')
    if isinstance(address, ipaddress.IPv6Address) and (address.ipv4_mapped or address.sixtofour or address.teredo):
        raise ValueError('Translated/tunneled addresses are unsupported')
    return str(address)


def validate_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r'[\x00-\x20\x7f\\]', value):
        raise ValueError('Use a bounded public HTTP(S) URL without whitespace or controls')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if parsed.scheme not in ('http', 'https') or not host or parsed.username is not None or parsed.password is not None or '@' in parsed.netloc:
            raise ValueError('Use a public HTTP(S) URL without credentials')
        if parsed.port not in (None, 80, 443):
            raise ValueError('Only ports 80 and 443 are supported')
        if '%' in host or host.endswith('.'):
            raise ValueError('Alternate host spellings are unsupported')
        try:
            public_address(host)
        except ValueError:
            # DNS names cannot be numeric/alternate IP spellings or local single labels.
            host = host.encode('idna').decode('ascii').lower()
            labels = host.split('.')
            if len(labels) < 2 or labels[-1].isdigit() or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
                raise ValueError('Use a public DNS hostname or globally routable IP')
        port = parsed.port
        authority = ('[' + host + ']') if ':' in host else host
        if port is not None:
            authority += ':' + str(port)
        return urlunsplit((parsed.scheme, authority, parsed.path or '/', parsed.query, ''))
    except (UnicodeError, OverflowError) as exc:
        raise ValueError('Invalid URL hostname') from exc



def redirect_url(base, location):
    # urljoin/urlsplit normalize some raw controls. Reject before either parser.
    if not isinstance(location, str) or not location or len(location) > 2048 or re.search(r'[\x00-\x20\x7f\\]', location):
        raise ValueError('Redirect destination contains invalid whitespace, controls or escapes')
    return validate_url(urljoin(base, location))


def resolve(host, port, timeout):
    """Killable DNS lookup: no indefinitely abandoned resolver threads."""
    code = 'import socket,json,sys;print(json.dumps(sorted(set(x[4][0] for x in socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)))))'
    try:
        result = subprocess.run([sys.executable, '-c', code, host, str(port)], capture_output=True, timeout=max(.01, timeout), check=True)
        addresses = json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        raise ValueError('Public hostname lookup failed or timed out') from exc
    if not addresses:
        raise ValueError('Public hostname has no addresses')
    return [public_address(address) for address in addresses]


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout, secure=False):
        super().__init__(host, port=port, timeout=timeout)
        self.address, self.secure = address, secure
        self.active_socket = None
        self.aborted = threading.Event()

    def connect(self):
        # A numeric sockaddr connects directly; socket.create_connection would re-resolve.
        family = socket.AF_INET6 if ':' in self.address else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            self.active_socket = sock
            if self.aborted.is_set():
                raise OSError("Capture deadline expired")
            sock.settimeout(self.timeout)
            sock.connect((self.address, self.port))
            if self.secure:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
                self.active_socket = sock
                if self.aborted.is_set():
                    raise OSError("Capture deadline expired")
                sock.do_handshake()
            self.sock = sock
        except BaseException:
            sock.close()
            raise


    def abort(self):
        self.aborted.set()
        sock = self.active_socket
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def _request(url, deadline, max_bytes=MAX_CONTENT):
    url = validate_url(url)
    parsed = urlsplit(url)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('Page check timed out')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = resolve(parsed.hostname, port, remaining)  # validates ALL DNS answers
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('Page check timed out')
    connection = PinnedHTTPConnection(parsed.hostname, port, addresses[0], remaining, parsed.scheme == 'https')
    watchdog = threading.Timer(max(.001, deadline - time.monotonic()), connection.abort)
    watchdog.daemon = True
    watchdog.start()
    try:
        connection.request('GET', urlunsplit(('', '', parsed.path or '/', parsed.query, '')), headers={
            'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain,application/xhtml+xml', 'Accept-Encoding': 'identity',
            'Host': parsed.netloc, 'Connection': 'close'})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if headers.get('content-encoding', 'identity').lower() != 'identity':
            raise ValueError('Compressed page responses are unsupported')
        if 'content-length' in headers and int(headers['content-length']) > max_bytes:
            raise ValueError('Page exceeds the capture size limit')
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Page check timed out')
            if connection.sock:
                connection.sock.settimeout(remaining)
            # read1 returns after one underlying read, preserving a total deadline.
            chunk = response.read1(min(16384, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise ValueError('Page exceeds the capture size limit')
        if time.monotonic() >= deadline or connection.aborted.is_set():
            raise ValueError('Page check timed out')
        return response.status, headers, b''.join(chunks)
    except (OSError, http.client.HTTPException) as exc:
        raise ValueError('Page unavailable or connection timed out; no absence conclusion') from exc
    finally:
        watchdog.cancel()
        watchdog.join()
        connection.close()


def fetch_page(url):
    deadline = time.monotonic() + TIME_LIMIT
    current = validate_url(url)
    robots_cache = {}
    for hop in range(4):
        parsed = urlsplit(current)
        origin = urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
        if origin not in robots_cache:
            robots_url = origin + '/robots.txt'
            for robots_hop in range(4):
                status, headers, content = _request(robots_url, deadline, 65536)
                if status in (301, 302, 303, 307, 308):
                    if robots_hop == 3 or not headers.get('location'):
                        raise ValueError('Robots redirect limit or missing destination')
                    robots_url = redirect_url(robots_url, headers['location'])
                    continue
                if status in (404, 410):
                    robots_cache[origin] = None
                elif status == 200:
                    parser = RobotFileParser()
                    parser.parse(content.decode('utf-8', errors='replace').splitlines())
                    robots_cache[origin] = parser
                else:
                    raise ValueError('Robots permission unavailable; page not captured')
                break
        policy = robots_cache[origin]
        if policy is not None and not policy.can_fetch(USER_AGENT, current):
            raise ValueError('Robots policy disallows this page')
        status, headers, content = _request(current, deadline)
        if status in (301, 302, 303, 307, 308):
            if hop == 3 or not headers.get('location'):
                raise ValueError('Page redirect limit or missing destination')
            current = redirect_url(current, headers['location'])
            continue
        if status != 200:
            raise ValueError('Page unavailable (HTTP %s); no absence conclusion' % status)
        media = headers.get('content-type', '').split(';')[0].strip().lower()
        if media not in ('text/html', 'text/plain', 'application/xhtml+xml'):
            raise ValueError('Only HTML and plain text pages are supported')
        return current, content, media
    raise ValueError('Page redirect limit reached')
