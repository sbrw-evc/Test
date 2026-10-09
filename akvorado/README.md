# Akvorado: DNS-имена и IP сервисов для DstAddr

Скрипт `akvorado_dns_enricher.py` по расписанию берёт адреса назначения (`DstAddr`) из ClickHouse Akvorado, определяет, какому сайту или сервису они принадлежат, собирает все IP этого сервиса и записывает результат в источник словаря `default.custom_dict_dst_ip_dns_dict`.

| Колонка словаря | Содержимое |
|---|---|
| ключ (например `addr`) | `DstAddr`, IPv4 в виде `::ffff:a.b.c.d` — как он хранится в Akvorado |
| имя (`dns_name` / `dnsname`) | имя сервиса/сайта, определённое по IP |
| IP (`dns_all_ips` / `dnsallips`) | все IP сервиса через запятую, включая хосты, которые подтягивает его страница |

Имена колонок скрипт берёт из самого словаря (`system.dictionaries`), поэтому они могут быть любыми.

Рассчитан на Akvorado 2.4.0 в Docker; на живой инсталляции ещё не запускался. Файл-источник: `/opt/akvorado/config/dns_ip_dns.csv` на хосте, `/etc/akvorado/dns_ip_dns.csv` в контейнере.

## Состав

| Файл | Назначение |
|---|---|
| `akvorado_dns_enricher.py` | скрипт обогащения (asyncio) |
| `requirements.txt` | зависимости Python |
| `akvorado-dns-enricher.cron` | пример задания для `/etc/cron.d` |

## Как работает

1. **Выборка.** Из `default.flows` берётся топ `DstAddr` по объёму трафика за последние `--hours` часов (по умолчанию 24 ч, 5000 адресов). Частные, multicast и служебные адреса отбрасываются (`--include-private` их оставляет).
2. **Определение сервиса.** Для каждого IP параллельно:
   - PTR-запись;
   - TLS-сертификат, который отдаёт `IP:443`: из CN/SAN берётся имя (`*.google.com` → `google.com`).

   Приоритет у сертификата: PTR часто указывает на балансировщик или CDN (`lb-140-82-121-4-iad.github.com`), а сертификат — на сам сервис. Если 443 закрыт, используется PTR.
3. **Сбор всех IP сервиса.** Резолвятся (A + AAAA, с разворотом CNAME):
   - имя сервиса и до `--max-san` имён из SAN сертификата;
   - хосты главной страницы `https://<имя>/` (или `https://www.<имя>/`): редиректы, ссылки в HTML и inline-JS, заголовки `Content-Security-Policy` и `Link`, а также URL внутри до `--max-scripts` внешних JS-файлов.

   Результат кэшируется по имени: сотни IP одного сервиса (Google, Яндекс, CDN) обходятся одним запросом к сайту.
4. **Запись.** CSV перезаписывается атомарно (временный файл → `rename`, права `0644`), затем выполняется `SYSTEM RELOAD DICTIONARY default.custom_dict_dst_ip_dns_dict`.
5. **Инкрементальность.** Состояние хранится в `/opt/akvorado/config/.dns_ip_dns.state.json`:
   - найденные записи перепроверяются раз в `--ttl` (7 суток);
   - ненайденные — раз в `--retry-empty` (сутки);
   - IP, не встречавшиеся в трафике `--expire` (30 суток), удаляются.

   При первом запуске уже существующие строки CSV импортируются в состояние.

## Установка

```bash
mkdir -p /opt/akvorado/dns-enricher
cp akvorado_dns_enricher.py requirements.txt /opt/akvorado/dns-enricher/
cd /opt/akvorado/dns-enricher
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Нужен Python 3.9+.

### Доступ к ClickHouse

В docker-compose Akvorado HTTP-порт ClickHouse (8123) не публикуется на хост, поэтому `http://localhost:8123` даёт `Connection refused`. Варианты:

- **рекомендуется:** `--ch-container akvorado-clickhouse-1` (или `CH_CONTAINER=…`). Скрипт сам получает IP контейнера через `docker inspect` при каждом запуске, так что смена IP после перезапуска не мешает. Нужен доступ к Docker (запуск от root);
- опубликовать порт в `docker-compose` (`127.0.0.1:8123:8123`) и оставить `--ch-url http://127.0.0.1:8123`.

Если ClickHouse отвечает ошибкой аутентификации, укажите `CH_USER`/`CH_PASSWORD` пользователя, под которым к нему подключается Akvorado.

### Формат CSV и словаря

Заголовок CSV строится по структуре словаря из `system.dictionaries`: сначала ключи, затем атрибуты. Колонка имени — атрибут, оканчивающийся на `name` (`dns_name`, `dnsname`); колонка IP — атрибут, содержащий `ips` (`dns_all_ips`, `dnsallips`). При неоднозначности их задают `--name-column` и `--ips-column`.

Если словарь прочитать не удалось, используется заголовок существующего CSV; когда в нём нет ключевой колонки, добавляется `addr` (`--key-column`).

Словарю обязательно нужна ключевая колонка с IP: CSV только с `dns_name,dns_all_ips` не к чему привязать. Ключ должен быть в `keys` словаря в `akvorado.yaml`. Если старый CSV не совпадает со словарём, скрипт предупреждает и перезаписывает его.

Ориентировочный пример конфигурации (сверьте имена полей с документацией вашей версии Akvorado):

```yaml
schema:
  custom-dictionaries:
    dst_ip_dns_dict:
      layout: complex_key_hashed
      keys:
        - name: addr
          type: String
      attributes:
        - name: dnsname
          type: String
        - name: dnsallips
          type: String
      source: dns_ip_dns.csv
      dimensions:
        - DstAddr
```

Формат ключа можно проверить запросом `SELECT toString(DstAddr) FROM flows LIMIT 5`. Если для IPv4 там не `::ffff:a.b.c.d`, запускайте с `--no-ipv4-mapped`.

## Проверка

```bash
cd /opt/akvorado/dns-enricher
./venv/bin/python akvorado_dns_enricher.py --ch-container akvorado-clickhouse-1 --dry-run -v --limit 50
```

В начале лога выводится определённая структура: `ключ=…, имя=…, IP=…` и итоговый заголовок CSV. `--dry-run` ничего не записывает и выводит первые 30 найденных строк. Затем выполните обычный запуск и проверьте словарь:

```sql
SELECT status, element_count, last_exception
FROM system.dictionaries
WHERE name = 'custom_dict_dst_ip_dns_dict';

SELECT * FROM default.custom_dict_dst_ip_dns_dict LIMIT 10;
```

## Расписание

```bash
cp akvorado-dns-enricher.cron /etc/cron.d/akvorado-dns-enricher
chmod 644 /etc/cron.d/akvorado-dns-enricher
```

Контейнер ClickHouse и пароль задаются переменными `CH_CONTAINER` и `CH_PASSWORD` в этом файле. Реальный пароль в репозиторий не коммитить. Лог: `/var/log/akvorado-dns-enricher.log`. Если предыдущий запуск ещё работает, новый сразу завершается.

## Параметры

| Параметр | По умолчанию | Описание |
|---|---|---|
| `--ch-container` / `CH_CONTAINER` | пусто | контейнер ClickHouse; IP берётся через `docker inspect`, `--ch-url` игнорируется |
| `--ch-port` / `CH_PORT` | `8123` | HTTP-порт ClickHouse в контейнере |
| `--ch-url` / `CH_URL` | `http://127.0.0.1:8123` | HTTP-интерфейс ClickHouse |
| `--ch-user` / `CH_USER` | `default` | пользователь |
| `--ch-password` / `CH_PASSWORD` | пусто | пароль |
| `--ch-database` / `CH_DATABASE` | `default` | база Akvorado |
| `--flows-table` | `flows` | таблица потоков |
| `--dict-name` | `custom_dict_dst_ip_dns_dict` | словарь для перезагрузки |
| `--hours` | `24` | окно выборки DstAddr |
| `--limit` | `5000` | сколько DstAddr брать (топ по байтам) |
| `--csv` | `/opt/akvorado/config/dns_ip_dns.csv` | файл-источник словаря |
| `--state` | `/opt/akvorado/config/.dns_ip_dns.state.json` | состояние между запусками |
| `--key-column` | `addr` | имя ключа, если словарь не прочитан и ключа нет в CSV |
| `--name-column` | авто | колонка имени сервиса |
| `--ips-column` | авто | колонка списка IP |
| `--ips-sep` | `,` | разделитель в `dnsallips` |
| `--ipv4-mapped` / `--no-ipv4-mapped` | вкл. | формат IPv4-ключа |
| `--inplace` | выкл. | писать CSV на месте, без `rename` |
| `--nameservers` / `DNS_SERVERS` | `resolv.conf` | DNS-серверы через запятую |
| `--concurrency` | `100` | параллельных проверок IP |
| `--dns-concurrency` | `200` | параллельных DNS-запросов |
| `--http-concurrency` | `30` | параллельных HTTP-запросов |
| `--timeout` | `5` | таймаут операции, с |
| `--crawl` / `--no-crawl` | вкл. | открывать сайты и собирать подгружаемые хосты |
| `--max-scripts` | `8` | внешних JS на сайт |
| `--max-san` | `20` | имён из SAN на сертификат |
| `--max-subhosts` | `80` | хостов на сервис |
| `--max-ips` | `500` | IP в `dnsallips` |
| `--ttl` | `604800` | перепроверка найденных, с |
| `--retry-empty` | `86400` | перепроверка ненайденных, с |
| `--expire` | `2592000` | удаление неактивных IP, с |
| `--dry-run` | — | не писать файлы |
| `-v` | — | подробный лог |

## Ограничения

- Сайт определяется по сертификату без SNI. Серверы, которые без SNI обрывают TLS-рукопожатие (часть Cloudflare, некоторые CDN), получают имя только из PTR, а если PTR нет — остаются без имени.
- Хосты, которые загружаются только при выполнении JavaScript в браузере, не обнаруживаются: страница разбирается статически. Полное покрытие возможно через headless-браузер (Playwright), но это заметно тяжелее по ресурсам.
- IP, относящиеся к общему CDN, могут попадать в `dnsallips` нескольких сервисов.
- Для сайтов с GeoDNS набор IP зависит от используемого DNS-сервера.
- Словарь берёт данные из CSV через оркестратор Akvorado. Если после `SYSTEM RELOAD DICTIONARY` данные не обновились, перезапустите оркестратор: `docker compose restart akvorado-orchestrator`.
- Если в контейнер смонтирован сам CSV-файл, а не каталог `config`, после `rename` контейнер продолжит видеть старый файл — используйте `--inplace`.
