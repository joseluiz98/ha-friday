"""Official Gemini API cost from the Cloud Billing export in BigQuery (REST, stdlib + system openssl)."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ledger
import pricing

STATE_DIR = ledger.STATE_DIR
SKU_MAP_PATH = Path(__file__).with_name("sku_map.json")
DEFAULT_FACTOR = 7.4
SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"
BQ = "https://bigquery.googleapis.com/bigquery/v2"

QUERY = """SELECT FORMAT_DATE('%F', DATE(usage_start_time, 'America/Sao_Paulo')) AS day,
       sku.id AS sku_id, sku.description AS sku, cost_type, currency,
       SUM(cost) AS cost, SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits,
       SUM(usage.amount) AS usage_amount, ANY_VALUE(usage.unit) AS usage_unit
FROM `{table}`
WHERE DATE(_PARTITIONTIME) >= DATE_SUB(CURRENT_DATE(), INTERVAL 45 DAY)
  AND DATE(usage_start_time, 'America/Sao_Paulo') BETWEEN DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 40 DAY)
                                                      AND DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 1 DAY)
  AND (service.description = 'Gemini API' OR service.id = 'AEFD-7695-64FA')
GROUP BY 1, 2, 3, 4, 5"""


def mask(text, secrets):
    text = str(text)
    for s in sorted({x for x in secrets if x}, key=len, reverse=True):
        text = text.replace(s, "***")
    return text


def default_http(method, url, headers=None, body=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} em {method} {url.split('?')[0]}: {e.read()[:300].decode('utf-8', 'replace')}")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def access_token(sa, run=subprocess.run, http=None):
    http = http or default_http
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": sa["client_email"], "scope": SCOPE, "aud": sa["token_uri"],
                              "iat": now, "exp": now + 3600}).encode())
    signing_input = f"{head}.{claims}"
    fd, path = tempfile.mkstemp(prefix="gcpkey")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(sa["private_key"])
        os.chmod(path, 0o600)
        res = run(["openssl", "dgst", "-sha256", "-sign", path], input=signing_input.encode(),
                  capture_output=True, check=True)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    jwt = f"{signing_input}.{_b64(res.stdout)}"
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": jwt}).encode()
    resp = http("POST", sa["token_uri"], {"Content-Type": "application/x-www-form-urlencoded"}, body)
    return resp["access_token"]


def _auth(token, extra=None):
    h = {"Authorization": f"Bearer {token}"}
    h.update(extra or {})
    return h


def find_table(project, dataset, token, http):
    resp = http("GET", f"{BQ}/projects/{project}/datasets/{dataset}/tables", _auth(token))
    for t in resp.get("tables", []):
        tid = t["tableReference"]["tableId"]
        if tid.startswith("gcp_billing_export_v1_"):
            return f"{project}.{dataset}.{tid}"
    raise RuntimeError("export ainda não criou a tabela (gcp_billing_export_v1_*)")


def _convert(resp, fields_cache):
    fields = resp["schema"]["fields"]
    out = []
    for r in resp.get("rows", []):
        item = {}
        for f, cell in zip(fields, r["f"]):
            v = cell.get("v")
            if v is not None:
                t = f["type"]
                if t in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
                    v = float(v)
                elif t in ("INTEGER", "INT64"):
                    v = int(v)
            item[f["name"]] = v
        out.append(item)
    return out


def run_query(project, sql, token, http, sleep=time.sleep, timeout=120):
    resp = http("POST", f"{BQ}/projects/{project}/queries", _auth(token, {"Content-Type": "application/json"}),
                json.dumps({"query": sql, "useLegacySql": False, "timeoutMs": 30000}).encode())
    ref = resp.get("jobReference", {})
    deadline = time.monotonic() + timeout
    while not resp.get("jobComplete"):
        if time.monotonic() > deadline:
            raise RuntimeError("consulta do BigQuery não terminou em 120 s")
        sleep(2)
        q = {"location": ref["location"]} if ref.get("location") else {}
        url = f"{BQ}/projects/{project}/queries/{ref['jobId']}" + ("?" + urllib.parse.urlencode(q) if q else "")
        resp = http("GET", url, _auth(token))
    rows = _convert(resp, None)
    while resp.get("pageToken"):
        q = {"pageToken": resp["pageToken"]}
        if ref.get("location"):
            q["location"] = ref["location"]
        resp = http("GET", f"{BQ}/projects/{project}/queries/{ref['jobId']}?" + urllib.parse.urlencode(q), _auth(token))
        rows += _convert(resp, None)
    return rows


def pull(state_dir=STATE_DIR, http=None, run=subprocess.run, sleep=time.sleep, secrets=None):
    state_dir = Path(state_dir)
    secrets = secrets if secrets is not None else set()
    cfg_f, key_f = state_dir / "gcp.json", state_dir / "gcp-service-account.json"
    if not cfg_f.exists() or not key_f.exists():
        print("BigQuery não configurado")
        return 0
    http = http or default_http
    cfg = json.loads(cfg_f.read_text())
    sa = json.loads(key_f.read_text())
    secrets.add(sa["private_key"])
    secrets.update(l for l in sa["private_key"].splitlines() if len(l) >= 8 and not l.startswith("-----"))
    token = access_token(sa, run=run, http=http)
    secrets.add(token)
    table = find_table(cfg["project"], cfg["dataset"], token, http)
    rows = run_query(cfg["project"], QUERY.format(table=table), token, http, sleep=sleep)
    by_day = {}
    for r in rows:
        by_day.setdefault(r.pop("day"), []).append(r)
    by_month = {}
    for day, lines in by_day.items():
        by_month.setdefault(day[:7], {})[day] = lines
    out = state_dir / "official"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    for month, days in by_month.items():
        f = out / f"{month}.json"
        try:
            cur = json.loads(f.read_text())
        except (OSError, ValueError):
            cur = {"days": {}}
        cur["days"].update(days)
        cur["pulled_at"] = stamp
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False))
        tmp.replace(f)
    return len(by_day)


def load_sku_map(path=SKU_MAP_PATH):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def official_by_day(first, last, state_dir=STATE_DIR, sku_map=None, today=None):
    sku_map = load_sku_map() if sku_map is None else sku_map
    today = today or datetime.now(ledger.BRT).date()
    cache, out = {}, {}
    day = first
    while day <= last:
        month = day.strftime("%Y-%m")
        if month not in cache:
            try:
                cache[month] = json.loads((Path(state_dir) / "official" / f"{month}.json").read_text())["days"]
            except (OSError, ValueError, KeyError):
                cache[month] = {}
        lines = cache[month].get(day.isoformat())
        if lines is not None:
            by_model, total = {}, 0.0
            for l in lines:
                net = float(l.get("cost") or 0) + float(l.get("credits") or 0)
                key = sku_map.get(l.get("sku_id"), "unmapped")
                by_model[key] = by_model.get(key, 0.0) + net
                total += net
            out[day] = {"total_brl": total, "by_model": by_model, "closed": day <= today - timedelta(days=3)}
        day += timedelta(days=1)
    return out


def _prev_month(m):
    return (m.replace(day=1) - timedelta(days=1)).replace(day=1)


def _factor_for(month, records, prices, official):
    """Sum official BRL of measured models / sum list USD of the same models, closed days with usage records."""
    off_sum = usd_sum = 0.0
    for day, o in official.items():
        if (day.year, day.month) != (month.year, month.month) or not o["closed"]:
            continue
        day_recs = [r for r in records
                    if datetime.fromisoformat(r["ts"]).astimezone(ledger.BRT).date() == day and r["model"].removeprefix("models/") in prices]
        if not day_recs:
            continue
        models = {r["model"].removeprefix("models/") for r in day_recs}
        d_usd = 0.0
        for r in day_recs:
            c = pricing.list_cost_usd(r, prices)
            if c is not None:
                d_usd += c
        d_off = sum(v for k, v in o["by_model"].items() if k in models)
        if d_usd > 0:
            off_sum += d_off
            usd_sum += d_usd
    return off_sum / usd_sum if usd_sum > 0 and off_sum > 0 else None


def month_factor(month, records, prices, official, today=None):
    month = month.replace(day=1)
    f = _factor_for(month, records, prices, official)
    if f:
        return f, "oficial do mês"
    m = month
    for _ in range(6):
        m = _prev_month(m)
        f = _factor_for(m, records, prices, official)
        if f:
            return f, "mês anterior"
    return DEFAULT_FACTOR, "padrão 7,4"


def main(state_dir=STATE_DIR, http=None, run=subprocess.run, sleep=time.sleep):
    secrets = set()
    try:
        n = pull(state_dir=state_dir, http=http, run=run, sleep=sleep, secrets=secrets)
    except Exception as e:
        print("erro no custo oficial: " + mask(f"{type(e).__name__}: {e}", secrets)[:500], file=sys.stderr)
        return 1
    if (Path(state_dir) / "gcp.json").exists():
        print(f"{n} dias gravados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
