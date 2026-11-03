"""Gemini cost report (pt-BR) for F.R.I.D.A.Y.: day, month, range, projection. Spec 2026-10-23."""
import argparse
import calendar
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import ledger
import official as off
import pricing

HISTORY_DAYS = 200


def brl(x):
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + "R$ " + s


def num(n):
    return f"{int(n):,}".replace(",", ".")


def dm(d):
    return d.strftime("%d/%m")


def rec_day(r):
    return datetime.fromisoformat(r["ts"]).astimezone(ledger.BRT).date()


def total_tokens(r):
    return sum(r["tokens"].values())


class Ctx:
    def __init__(self, state_dir, today, sku_map):
        self.state, self.today = Path(state_dir), today
        self.prices = pricing.load_prices()
        first = today - timedelta(days=HISTORY_DAYS)
        self.records, self.skipped = ledger.read(first, today, self.state)
        self.official = off.official_by_day(first, today, self.state, sku_map=sku_map, today=today)
        self._f = {}
        self.unpriced = defaultdict(lambda: [0, 0])  # model -> [calls, tokens]
        self._unpriced_seen = set()

    def factor(self, month):
        key = (month.year, month.month)
        if key not in self._f:
            self._f[key] = off.month_factor(month.replace(day=1), self.records, self.prices, self.official, self.today)
        return self._f[key]

    def est(self, r):
        """Estimated BRL of a record, or None (unpriced)."""
        usd = pricing.list_cost_usd(r, self.prices)
        if usd is None:
            return None
        return usd * self.factor(rec_day(r))[0]

    def total(self, recs, track_unpriced=True):
        t = 0.0
        for r in recs:
            v = self.est(r)
            if v is None:
                if track_unpriced and r["id"] not in self._unpriced_seen:
                    self._unpriced_seen.add(r["id"])
                    u = self.unpriced[r["model"]]
                    u[0] += 1
                    u[1] += total_tokens(r)
            else:
                t += v
        return t


def breakdown(ctx, recs, field):
    g = defaultdict(lambda: [0.0, 0])
    for r in recs:
        v = ctx.est(r)
        a = g[r[field]]
        a[0] += v or 0.0
        a[1] += 1
    return sorted(((k, a[0], a[1]) for k, a in g.items()), key=lambda x: (-x[1], x[0]))


def section(ctx, title, recs, field):
    lines = [f"{title} (estimado):"]
    for k, v, n in breakdown(ctx, recs, field):
        lines.append(f"  {str(k):<18} {brl(v):>14}   {n} chamada{'s' if n != 1 else ''}")
    if len(lines) == 1:
        lines.append("  (nada medido)")
    return lines


def tally(ctx, recs):
    # also registers unpriced models
    ctx.total(recs)


def sections(ctx, recs):
    tally(ctx, recs)
    out = []
    out += section(ctx, "Por fonte", recs, "source") + [""]
    out += section(ctx, "Por origem", recs, "origin") + [""]
    out += section(ctx, "Por modelo", recs, "model")
    return out


def footer(ctx):
    lines = ["", "-- Fontes dos dados --"]
    last = {}
    for r in ctx.records:
        if r["source"] not in last or r["ts"] > last[r["source"]]:
            last[r["source"]] = r["ts"]
    for s in ("voice", "brain", "lucid"):
        if s in last:
            t = datetime.fromisoformat(last[s]).astimezone(ledger.BRT)
            lines.append(f"Última linha de {s}: {t:%d/%m %H:%M}")
        else:
            lines.append(f"Última linha de {s}: nenhuma")
    cur = ctx.state / "brain_cursor.json"
    if cur.exists():
        t = datetime.fromtimestamp(cur.stat().st_mtime, ledger.BRT)
        lines.append(f"Última coleta do cérebro: {t:%d/%m %H:%M}")
    pulled = None
    odir = ctx.state / "official"
    if odir.exists():
        for f in odir.glob("*.json"):
            try:
                p = json.loads(f.read_text()).get("pulled_at")
            except (OSError, ValueError):
                continue
            if p and (pulled is None or p > pulled):
                pulled = p
    if pulled:
        t = datetime.fromisoformat(pulled).astimezone(ledger.BRT)
        lines.append(f"Último custo oficial: {t:%d/%m %H:%M}")
    else:
        lines.append("Custo oficial: ainda não disponível (só estimado)")
    if ctx.skipped:
        lines.append(f"Atenção: {ctx.skipped} linha(s) inválida(s) puladas no registro de uso")
    for m, (n, t) in sorted(ctx.unpriced.items()):
        lines.append(f"Atenção: modelo {m} sem preço na tabela: {n} chamada(s), {num(t)} tokens (não somados)")
    return lines


def factor_line(ctx, month):
    f, label = ctx.factor(month)
    return f"Fator R$/US$ de {month:%m/%Y}: {f:.2f}".replace(".", ",") + f" ({label})"


def parse_day(s, today):
    if s == "hoje":
        return today
    if s == "ontem":
        return today - timedelta(days=1)
    return date.fromisoformat(s)


def parse_month(s, today):
    if s == "este":
        return today.replace(day=1)
    if s == "passado":
        return (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    y, m = s.split("-")
    return date(int(y), int(m), 1)


def cmd_day(ctx, args):
    d = parse_day(args.when, ctx.today)
    recs = [r for r in ctx.records if rec_day(r) == d]
    est = ctx.total(recs)
    lines = [f"Dia {d:%d/%m/%Y}", f"Total medido, estimado: {brl(est)} em {len(recs)} chamadas"]
    o = ctx.official.get(d)
    if o:
        lines.append(f"Total oficial: {brl(o['total_brl'])}" + ("" if o["closed"] else " (parcial, o Google ainda corrige)"))
        lines.append(f"Não medido (oficial menos estimado): {brl(o['total_brl'] - est)}")
    else:
        lines.append("Total oficial: ainda não disponível para este dia")
    lines.append(factor_line(ctx, d))
    lines.append("")
    lines += sections(ctx, recs)
    return lines


def cmd_month(ctx, args):
    m = parse_month(args.when, ctx.today)
    last = min(m.replace(day=calendar.monthrange(m.year, m.month)[1]), ctx.today)
    by_day = defaultdict(list)
    for r in ctx.records:
        d = rec_day(r)
        if m <= d <= last:
            by_day[d].append(r)
    total, n_off, n_est, n_none = 0.0, 0, 0, 0
    d = m
    while d <= last:
        o = ctx.official.get(d)
        if o and o["closed"]:
            total += o["total_brl"]
            n_off += 1
        elif by_day.get(d):
            total += ctx.total(by_day[d])
            n_est += 1
        else:
            n_none += 1
        d += timedelta(days=1)
    recs = [r for rs in by_day.values() for r in rs]
    lines = [f"Mês {m:%m/%Y}", f"Total do mês: {brl(total)}",
             f"  {n_off} dia(s) pelo oficial, {n_est} dia(s) pelo estimado, {n_none} dia(s) sem dado",
             factor_line(ctx, m), "", "Medido no mês:"]
    lines += sections(ctx, recs)
    return lines


def cmd_range(ctx, args):
    first, last = date.fromisoformat(args.first), date.fromisoformat(args.last)
    recs = [r for r in ctx.records if first <= rec_day(r) <= last
            and (not args.source or r["source"] == args.source) and (not args.origin or r["origin"] == args.origin)]
    filt = "".join([f" fonte={args.source}" if args.source else "", f" origem={args.origin}" if args.origin else ""])
    n = len(recs)
    lines = [f"Período {dm(first)} a {dm(last)}{filt}",
             f"Total medido, estimado: {brl(ctx.total(recs))} em {n} chamada{'s' if n != 1 else ''}", ""]
    lines += sections(ctx, recs)
    return lines


def cmd_projection(ctx, args):
    t = ctx.today
    nxt = (t.replace(day=1) + timedelta(days=32)).replace(day=1)
    days_next = calendar.monthrange(nxt.year, nxt.month)[1]
    win_first, win_last = t - timedelta(days=30), t - timedelta(days=1)
    recs = [r for r in ctx.records if win_first <= rec_day(r) <= win_last]
    per_model = defaultdict(lambda: dict.fromkeys(ledger.TOKEN_FIELDS, 0))
    for r in recs:
        for k, v in r["tokens"].items():
            per_model[r["model"]][k] += v
    first_seen = {}
    for r in ctx.records:
        d = rec_day(r)
        if r["model"] not in first_seen or d < first_seen[r["model"]]:
            first_seen[r["model"]] = d
    usd, lines = 0.0, []
    for model, tok in sorted(per_model.items()):
        pseudo = {"model": model, "tokens": tok, "ts": datetime(t.year, t.month, t.day, 12, tzinfo=ledger.BRT).isoformat()}
        c = pricing.list_cost_usd(pseudo, ctx.prices, day=nxt)
        if c is None:
            u = ctx.unpriced[model]
            u[0] += 1
            u[1] += sum(tok.values())
            continue
        start = max(win_first, first_seen.get(model, win_first))
        covered = (win_last - start).days + 1
        month_usd = c / covered * days_next
        usd += month_usd
        usd_txt = f"{month_usd:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        note = f" (medido desde {dm(start)}: média de {covered} dia(s))" if covered < 30 else ""
        lines.append(f"  {model}: US$ {usd_txt} de lista no mês{note}")
    f, label = ctx.factor(t)
    measured = usd * f
    nm_days = []
    for d, o in ctx.official.items():
        if win_first <= d <= win_last:
            est_d = ctx.total([r for r in recs if rec_day(r) == d], track_unpriced=False)
            nm_days.append(o["total_brl"] - est_d)
    nm = (sum(nm_days) / len(nm_days)) * days_next if nm_days else 0.0
    out = [f"Projeção para {nxt:%m/%Y} ({days_next} dias)",
           f"Base: média diária de {dm(win_first)} a {dm(win_last)} (30 dias completos), preço vigente em {nxt:%d/%m/%Y}",
           f"Medido projetado, estimado: {brl(measured)}"]
    out += lines
    if nm_days:
        out.append(f"Não medido projetado (média de {len(nm_days)} dia(s) com oficial): {brl(nm)}")
    else:
        out.append("Não medido: sem dados oficiais ainda (não entra na projeção)")
    out.append(f"Total projetado: {brl(measured + nm)}")
    out.append(f"Fator R$/US$: {f:.2f}".replace(".", ",") + f" ({label})")
    return out


def build_parser():
    p = argparse.ArgumentParser(prog="cost_report", description="Relatório de custo do Gemini da F.R.I.D.A.Y.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("day")
    s.add_argument("when", nargs="?", default="ontem")
    s = sub.add_parser("month")
    s.add_argument("when", nargs="?", default="este")
    s = sub.add_parser("range")
    s.add_argument("first")
    s.add_argument("last")
    s.add_argument("--source")
    s.add_argument("--origin")
    sub.add_parser("projection")
    return p


def run(argv, state_dir=None, today=None, sku_map=None):
    args = build_parser().parse_args(argv)
    state_dir = state_dir or os.environ.get("FRIDAY_COST_STATE", str(ledger.STATE_DIR))
    today = today or datetime.now(ledger.BRT).date()
    ctx = Ctx(state_dir, today, sku_map)
    lines = {"day": cmd_day, "month": cmd_month, "range": cmd_range, "projection": cmd_projection}[args.cmd](ctx, args)
    return "\n".join(lines + footer(ctx))


def main(argv=None):
    print(run(sys.argv[1:] if argv is None else argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
