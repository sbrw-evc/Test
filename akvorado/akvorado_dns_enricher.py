#!/usr/bin/env python3
"""
akvorado_dns_enricher.py — обогащение DstAddr из Akvorado именами сервисов.

Для каждого DstAddr из таблицы flows:
  1. PTR-запись и TLS-сертификат на IP:443 (CN/SAN) -> имя сервиса (dnsname);
  2. A/AAAA этого имени и имён из SAN сертификата, плюс хосты, которые
     подтягивает главная страница сайта (HTML, inline-JS, внешние скрипты,
     заголовки CSP/Link) — всё резолвится -> dnsallips;
  3. результат пишется в CSV-источник словаря Akvorado,
     затем выполняется SYSTEM RELOAD DICTIONARY.

Работает инкрементально (для cron): состояние хранится в JSON,
записи перепроверяются по истечении --ttl, неактивные IP удаляются через --expire.

Зависимости: pip install aiohttp dnspython cryptography   (Python 3.9+)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import fcntl
import ipaddress
import json
import logging
import os
import re
import ssl
import sys
import tempfile
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import aiohttp
import dns.asyncresolver
import dns.exception
import dns.reversename
from cryptography import x509
from cryptography.x509.oid import NameOID

log = logging.getLogger("dns-enricher")

NAME_COL, IPS_COL = "dnsname", "dnsallips"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# URL-ы вида https://host, //host, https:\/\/host (экранированные в JS/JSON)
HOST_RE = re.compile(
    r"(?:https?:)?\\?/\\?/"
    r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})"
    r"(?![a-z0-9-])",
    re.I,
)
LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

# Домены-«шум», которые встречаются в любой разметке (xmlns, doctype и т.п.)
IGNORED_DOMAINS = ("w3.org", "schema.org", "ogp.me", "purl.org", "xmlns.com",
                   "example.com", "example.org", "example.net")
# Непубличные зоны — не годятся как имя сервиса
BAD_TLDS = {"invalid", "local", "localdomain", "internal", "lan", "arpa",
            "localhost", "home", "corp", "test"}

SQL_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ---------------------------------------------------------------- helpers ---

def is_hostname(h: str) -> bool:
    if not h or len(h) > 253 or "." not in h:
        return False
    labels = h.split(".")
    if not labels[-1].isalpha():
        return False
    return all(LABEL_RE.fullmatch(lbl) for lbl in labels)


def clean_host(h: str | None) -> str | None:
    if not h:
        return None
    h = h.strip().lower().rstrip(".")
    if h.startswith("*."):
        h = h[2:]
    if not is_hostname(h):
        return None
    if any(h == d or h.endswith("." + d) for d in IGNORED_DOMAINS):
        return None
    return h


def good_service_name(h: str) -> bool:
    return h.rsplit(".", 1)[-1] not in BAD_TLDS


def pick_name(cert_names: list[str], ptr: str | None) -> tuple[str, bool]:
    """Имя сервиса: сначала CN/SAN сертификата, потом PTR.
    Второе значение — можно ли пытаться открыть сайт по этому имени."""
    for n in cert_names:
        h = clean_host(n)
        if h and good_service_name(h):
            return h, True
    h = clean_host(ptr)
    if h and good_service_name(h):
        return h, False
    return "", False


def normalize_ip(raw: str):
    try:
        ip = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def csv_key(ip, ipv4_mapped: bool) -> str:
    # DstAddr в Akvorado хранится как IPv6, IPv4 — в виде ::ffff:a.b.c.d
    if ip.version == 4 and ipv4_mapped:
        return f"::ffff:{ip}"
    return str(ip)


def sort_ips(ips) -> list[str]:
    parsed = []
    for s in ips:
        with contextlib.suppress(ValueError):
            parsed.append(ipaddress.ip_address(s))
    parsed.sort(key=lambda a: (a.version, int(a)))
    return [str(a) for a in parsed]


def hosts_in_text(text: str) -> dict[str, None]:
    out: dict[str, None] = {}
    for m in HOST_RE.finditer(text):
        h = clean_host(m.group(1))
        if h:
            out[h] = None
    return out


def hosts_in_headers(headers) -> dict[str, None]:
    out: dict[str, None] = {}
    for hname in ("Content-Security-Policy",
                  "Content-Security-Policy-Report-Only", "Link"):
        for value in headers.getall(hname, []):
            out.update(hosts_in_text(value))
            # CSP допускает хосты без схемы: cdn.example.com, *.example.com
            for tok in re.split(r"[\s;,<>]+", value):
                tok = re.sub(r"^[a-z]+://", "", tok, flags=re.I)
                tok = tok.split("/")[0].split(":")[0]
                h = clean_host(tok)
                if h:
                    out[h] = None
    return out


def atomic_write(path: str, write_fn, mode: int = 0o644, inplace: bool = False):
    if inplace:
        # если файл смонтирован в контейнер отдельно (bind-mount файла),
        # os.replace сменит inode и контейнер его не увидит
        with open(path, "w", newline="", encoding="utf-8") as f:
            write_fn(f)
        return
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix="-" + os.path.basename(path))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)  # mkstemp создаёт 0600 — оркестратор не прочитает
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


# ------------------------------------------------------------------- DNS ----

class Resolver:
    def __init__(self, nameservers: list[str], timeout: float, concurrency: int):
        if nameservers:
            self.r = dns.asyncresolver.Resolver(configure=False)
            self.r.nameservers = nameservers
        else:
            self.r = dns.asyncresolver.Resolver()
        self.r.lifetime = timeout
        self.sem = asyncio.Semaphore(concurrency)
        self._fwd: dict[str, asyncio.Future] = {}

    async def _q(self, qname, rdtype):
        async with self.sem:
            try:
                return await self.r.resolve(qname, rdtype)
            except (dns.exception.DNSException, OSError):
                return None

    async def ptr(self, ip: str) -> str | None:
        ans = await self._q(dns.reversename.from_address(ip), "PTR")
        return str(ans[0].target).rstrip(".").lower() if ans else None

    def addrs(self, host: str) -> asyncio.Future:
        """A + AAAA (CNAME-цепочки разворачиваются), с дедупликацией запросов."""
        fut = self._fwd.get(host)
        if fut is None:
            fut = asyncio.ensure_future(self._addrs(host))
            self._fwd[host] = fut
        return fut

    async def _addrs(self, host: str) -> set[str]:
        out: set[str] = set()
        for ans in await asyncio.gather(self._q(host, "A"), self._q(host, "AAAA")):
            if ans:
                out.update(r.address for r in ans)
        return out


# ------------------------------------------------------------------- TLS ----

async def tls_names(ip: str, timeout: float) -> list[str]:
    """CN + SAN сертификата, который отдаёт IP:443 (без SNI)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, 443, ssl=ctx), timeout)
    except Exception:
        return []
    der = None
    try:
        sslobj = writer.get_extra_info("ssl_object")
        if sslobj:
            der = sslobj.getpeercert(binary_form=True)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), 1)
    if not der:
        return []
    try:
        cert = x509.load_der_x509_certificate(der)
    except Exception:
        return []
    names = [str(a.value) for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    with contextlib.suppress(x509.ExtensionNotFound):
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names += san.value.get_values_for_type(x509.DNSName)
    return names


# ----------------------------------------------------------------- crawl ----

class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            for k, v in attrs:
                if k == "src" and v:
                    self.scripts.append(v)


class Enricher:
    def __init__(self, args, http: aiohttp.ClientSession):
        self.args = args
        self.http = http
        ns = [s.strip() for s in args.nameservers.split(",") if s.strip()]
        self.res = Resolver(ns, args.timeout, args.dns_concurrency)
        self.sem = asyncio.Semaphore(args.concurrency)
        self.http_sem = asyncio.Semaphore(args.http_concurrency)
        self._svc: dict[str, asyncio.Future] = {}

    async def _get(self, url: str, want_body: bool):
        limit = self.args.max_body
        async with self.http_sem:
            async with self.http.get(url, allow_redirects=True, max_redirects=5) as resp:
                hosts: dict[str, None] = {}
                for h in [r.url.host for r in resp.history] + [resp.url.host]:
                    h = clean_host(h)
                    if h:
                        hosts[h] = None
                hosts.update(hosts_in_headers(resp.headers))
                ctype = resp.headers.get("Content-Type", "").lower()
                text = ""
                if want_body and any(t in ctype for t in ("html", "javascript", "json", "text")):
                    buf = bytearray()
                    async for chunk in resp.content.iter_chunked(65536):
                        buf += chunk
                        if len(buf) >= limit:
                            break
                    text = bytes(buf).decode("utf-8", "ignore")
                return str(resp.url), ctype, text, hosts

    async def _js_hosts(self, url: str) -> dict[str, None]:
        try:
            _, _, text, hosts = await self._get(url, True)
        except Exception as e:
            log.debug("js %s: %s", url, e)
            return {}
        hosts.update(hosts_in_text(text))
        return hosts

    async def crawl_site(self, name: str) -> dict[str, None]:
        """Хосты, к которым обращается главная страница сайта."""
        urls = [f"https://{name}/"]
        if not name.startswith("www."):
            urls.append(f"https://www.{name}/")
        for url in urls:
            try:
                final_url, ctype, text, hosts = await self._get(url, True)
            except Exception as e:
                log.debug("crawl %s: %s", url, e)
                continue
            if "html" in ctype and text:
                hosts.update(hosts_in_text(text))
                p = ScriptParser()
                with contextlib.suppress(Exception):
                    p.feed(text)
                scripts: list[str] = []
                for src in p.scripts:
                    u = urljoin(final_url, src)
                    if urlsplit(u).scheme in ("http", "https") and u not in scripts:
                        scripts.append(u)
                for h in await asyncio.gather(
                        *(self._js_hosts(u) for u in scripts[: self.args.max_scripts])):
                    hosts.update(h)
            return hosts
        return {}

    def service(self, name: str, cert_names: list[str], crawl: bool) -> asyncio.Future:
        """IP всего сервиса; кэшируется по имени — у Google/Yandex и т.п.
        сотни DstAddr с одним сертификатом, сайт обходится один раз."""
        fut = self._svc.get(name)
        if fut is None:
            fut = asyncio.ensure_future(self._service(name, cert_names, crawl))
            self._svc[name] = fut
        return fut

    async def _service(self, name: str, cert_names: list[str], crawl: bool) -> set[str]:
        hosts: dict[str, None] = {name: None}
        for n in cert_names[: self.args.max_san]:
            h = clean_host(n)
            if h:
                hosts[h] = None
        if crawl and self.args.crawl:
            for h in await self.crawl_site(name):
                if len(hosts) >= self.args.max_subhosts:
                    break
                hosts[h] = None
        log.debug("%s: резолв %d хостов", name, len(hosts))
        results = await asyncio.gather(*(self.res.addrs(h) for h in hosts))
        return set().union(*results) if results else set()

    async def enrich(self, ip: str) -> tuple[str, set[str]]:
        async with self.sem:
            ptr, cert_names = await asyncio.gather(
                self.res.ptr(ip), tls_names(ip, self.args.timeout))
        name, can_crawl = pick_name(cert_names, ptr)
        ips = {ip}
        if name:
            ips |= await self.service(name, cert_names, can_crawl)
        return name, ips


# ------------------------------------------------------------ ClickHouse ----

class ClickHouse:
    def __init__(self, session: aiohttp.ClientSession, args):
        self.s = session
        self.args = args

    async def query(self, sql: str) -> str:
        headers = {"X-ClickHouse-User": self.args.ch_user}
        if self.args.ch_password:
            headers["X-ClickHouse-Key"] = self.args.ch_password
        async with self.s.post(self.args.ch_url, params={"database": self.args.ch_database},
                               data=sql.encode(), headers=headers) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"ClickHouse HTTP {r.status}: {text[:500]}")
            return text


# ------------------------------------------------------------ state/CSV ----

def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("state %s повреждён (%s), начинаю заново", path, e)
        return {}


def read_csv(path: str, key_col: str):
    """Возвращает (header, key_column, rows)."""
    default = [key_col, NAME_COL, IPS_COL]
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        return default, key_col, []
    if not rows:
        return default, key_col, []
    header = [c.strip() for c in rows[0]]
    if NAME_COL in header and IPS_COL in header:
        keys = [c for c in header if c not in (NAME_COL, IPS_COL)]
        if keys:
            return header, keys[0], rows[1:]
        log.warning("в %s нет ключевой колонки (IP) — добавляю '%s'. "
                    "Она же должна быть в keys словаря в akvorado.yaml", path, key_col)
        return default, key_col, []
    log.warning("неожиданный заголовок %s: %s — использую %s", path, header, default)
    return default, key_col, []


# ------------------------------------------------------------------ main ----

async def run_enrichment(args, state: dict, targets: list[tuple[str, str]]):
    connector = aiohttp.TCPConnector(ssl=False, limit=args.http_concurrency * 2,
                                     ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=args.timeout * 3, sock_connect=args.timeout)
    headers = {"User-Agent": UA,
               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
               "Accept-Language": "ru,en;q=0.8"}
    async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                     headers=headers) as http:
        en = Enricher(args, http)

        async def one(key: str, probe: str):
            try:
                name, ips = await en.enrich(probe)
            except Exception as e:
                log.warning("%s: %s", probe, e)
                name, ips = "", {probe}
            return key, name, ips

        total, done, named = len(targets), 0, 0
        for coro in asyncio.as_completed([one(k, p) for k, p in targets]):
            key, name, ips = await coro
            state[key].update(name=name, ips=sort_ips(ips)[: args.max_ips], ts=time.time())
            done += 1
            named += bool(name)
            if done % 200 == 0 or done == total:
                log.info("обработано %d/%d, с именем: %d", done, total, named)


async def amain(args):
    for ident in (args.ch_database, args.flows_table, args.dict_name):
        if not SQL_IDENT.match(ident):
            raise SystemExit(f"недопустимый идентификатор: {ident}")

    now = time.time()
    header, key_col, csv_rows = read_csv(args.csv, args.key_column)
    state = load_state(args.state)

    if not state and csv_rows:
        # первый запуск: подхватываем то, что уже лежит в CSV
        ki, ni, ii = header.index(key_col), header.index(NAME_COL), header.index(IPS_COL)
        for r in csv_rows:
            if len(r) > max(ki, ni, ii) and r[ki]:
                state[r[ki]] = {"name": r[ni],
                                "ips": [x.strip() for x in r[ii].split(args.ips_sep.strip() or ",") if x.strip()],
                                "ts": 0, "seen": now}
        log.info("импортировано из CSV: %d записей", len(state))

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
        ch = ClickHouse(s, args)
        sql = f"""
            SELECT toString(DstAddr) AS ip, sum(Bytes) AS bytes
            FROM {args.ch_database}.{args.flows_table}
            WHERE TimeReceived > now() - INTERVAL {int(args.hours)} HOUR
            GROUP BY ip
            ORDER BY bytes DESC
            LIMIT {int(args.limit)}
            FORMAT TabSeparated"""
        text = await ch.query(sql)

        seen: list[tuple[str, str]] = []
        for line in text.splitlines():
            ip = normalize_ip(line.split("\t", 1)[0])
            if ip is None or ip.is_multicast or ip.is_unspecified:
                continue
            if not args.include_private and not ip.is_global:
                continue
            seen.append((csv_key(ip, args.ipv4_mapped), str(ip)))
        log.info("DstAddr из ClickHouse: %d", len(seen))

        targets = []
        for key, probe in seen:
            e = state.setdefault(key, {"name": "", "ips": [], "ts": 0})
            e["seen"] = now
            ttl = args.ttl if e.get("name") else args.retry_empty
            if now - e.get("ts", 0) >= ttl:
                targets.append((key, probe))
        log.info("к (пере)проверке: %d", len(targets))

        if targets:
            await run_enrichment(args, state, targets)

        expired = [k for k, e in state.items() if now - e.get("seen", 0) > args.expire]
        for k in expired:
            del state[k]
        if expired:
            log.info("удалено устаревших: %d", len(expired))

        rows = sorted((k, e) for k, e in state.items() if e.get("name"))
        if args.dry_run:
            for k, e in rows[:30]:
                print(k, e["name"], args.ips_sep.join(e["ips"][:10]), sep="\t")
            log.info("dry-run: в CSV попало бы %d строк", len(rows))
            return

        atomic_write(args.state, lambda f: json.dump(state, f, ensure_ascii=False), 0o600)

        def write_csv(f):
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            for k, e in rows:
                vals = {key_col: k, NAME_COL: e["name"], IPS_COL: args.ips_sep.join(e["ips"])}
                w.writerow([vals.get(c, "") for c in header])

        atomic_write(args.csv, write_csv, 0o644, inplace=args.inplace)
        log.info("записано в %s: %d строк", args.csv, len(rows))

        try:
            await ch.query(f"SYSTEM RELOAD DICTIONARY {args.ch_database}.{args.dict_name}")
            log.info("словарь %s.%s перезагружен", args.ch_database, args.dict_name)
        except Exception as e:
            log.error("не удалось перезагрузить словарь: %s", e)


def parse_args():
    env = os.getenv
    p = argparse.ArgumentParser(description="Akvorado DstAddr -> DNS-имя сервиса и все его IP")
    g = p.add_argument_group("ClickHouse")
    g.add_argument("--ch-url", default=env("CH_URL", "http://127.0.0.1:8123"))
    g.add_argument("--ch-user", default=env("CH_USER", "default"))
    g.add_argument("--ch-password", default=env("CH_PASSWORD", ""))
    g.add_argument("--ch-database", default=env("CH_DATABASE", "default"))
    g.add_argument("--flows-table", default="flows")
    g.add_argument("--dict-name", default="custom_dict_dst_ip_dns_dict")
    g.add_argument("--hours", type=int, default=24, help="за сколько часов брать DstAddr")
    g.add_argument("--limit", type=int, default=5000, help="топ DstAddr по трафику")

    g = p.add_argument_group("Файлы")
    g.add_argument("--csv", default="/opt/akvorado/config/dns_ip_dns.csv")
    g.add_argument("--state", default="/opt/akvorado/config/.dns_ip_dns.state.json")
    g.add_argument("--lock", default="/tmp/akvorado-dns-enricher.lock")
    g.add_argument("--key-column", default="addr",
                   help="имя ключевой колонки, если её нет в существующем CSV")
    g.add_argument("--ips-sep", default=",", help="разделитель IP в dnsallips")
    g.add_argument("--ipv4-mapped", action=argparse.BooleanOptionalAction, default=True,
                   help="писать IPv4-ключи как ::ffff:a.b.c.d (как DstAddr в Akvorado)")
    g.add_argument("--inplace", action="store_true",
                   help="перезаписывать CSV на месте (если смонтирован файл, а не каталог)")

    g = p.add_argument_group("Поиск")
    g.add_argument("--nameservers", default=env("DNS_SERVERS", ""),
                   help="DNS-серверы через запятую (по умолчанию resolv.conf)")
    g.add_argument("--concurrency", type=int, default=100, help="параллельных IP-проб")
    g.add_argument("--dns-concurrency", type=int, default=200)
    g.add_argument("--http-concurrency", type=int, default=30)
    g.add_argument("--timeout", type=float, default=5.0)
    g.add_argument("--crawl", action=argparse.BooleanOptionalAction, default=True,
                   help="открывать сайт и собирать подгружаемые хосты")
    g.add_argument("--max-scripts", type=int, default=8, help="внешних JS на сайт")
    g.add_argument("--max-san", type=int, default=20, help="имён из SAN на сертификат")
    g.add_argument("--max-subhosts", type=int, default=80, help="хостов на сервис")
    g.add_argument("--max-ips", type=int, default=500, help="IP в dnsallips")
    g.add_argument("--max-body", type=int, default=2_000_000)
    g.add_argument("--include-private", action="store_true")

    g = p.add_argument_group("Расписание")
    g.add_argument("--ttl", type=int, default=7 * 86400, help="перепроверка найденных, сек")
    g.add_argument("--retry-empty", type=int, default=86400, help="перепроверка ненайденных, сек")
    g.add_argument("--expire", type=int, default=30 * 86400,
                   help="удалять IP, не встречавшиеся в трафике столько секунд")

    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    lock = open(args.lock, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("предыдущий запуск ещё работает — выхожу")
        return

    started = time.time()
    try:
        asyncio.run(amain(args))
    except SystemExit:
        raise
    except Exception:
        log.exception("ошибка выполнения")
        sys.exit(1)
    log.info("готово за %.1f с", time.time() - started)


if __name__ == "__main__":
    main()
