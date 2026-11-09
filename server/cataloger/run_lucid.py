#!/usr/bin/env python3
"""Programa `cataloger`: Lucid Dreaming (spec 2026-10-21 memória compartilhada, revisão de 21/10).

Para cada dia pendente (até N por noite): prepara o dia (prepare_day.py --next), faz UMA chamada REST ao
Gemini 3.8 Flash que devolve JSON (fatos, diretrizes, vistas) e o próprio código grava na memória da `friday`.
O modelo não toca em arquivo. Guarda backup (prepare_day), confere o resultado (check_and_restore), mede o
custo pelo usageMetadata e anexa uma linha a state/runs.ndjson. Só stdlib.

Uso: run_lucid.py [--max-days N]    (padrão 3)
"""
import json
import os
import re
import shutil
import sys
import subprocess
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

def _local_config():
    """O nome do dono vem da configuração local (nunca do repo). Instalado, local_config.py fica ao lado deste arquivo;
    no repo, ele mora em voice_gateway/."""
    try:
        import local_config
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "voice_gateway"))
        import local_config
    return local_config


_DEFAULTS = {
    "NDJSON_DIR": "/mnt/haos/share/claude-voice/conversations",
    "CATALOGER_WS": "/srv/friday/state/cataloger",
    "FRIDAY_WS": "/srv/friday/home/.openclaw/workspace/friday",
    "PREPARE_DAY": "/opt/friday/cataloger/prepare_day.py",
    "GEMINI_KEY_FILE": "/srv/friday/secrets/gemini_api_key",
}
TZ = ZoneInfo("America/Sao_Paulo")
USER_MD_CAP = 3800
DIRECTIVES_VIEW_MAX = 1600
BRIEFING_MAX, CAPABILITIES_MAX = 1200, 400
DEFAULT_MODEL = "gemini-3.8-flash"


def read_model(path=None):
    """Lucid model chosen in Home Assistant (models.json, key `lucid`); anything unusable gives the default."""
    try:
        with open(path or os.environ.get("FRIDAY_MODELS_FILE", "/srv/friday/state/models.json"), encoding="utf-8") as f:
            name = json.load(f).get("lucid")
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", name):
            return name
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_MODEL


MODEL = read_model()
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
RETRY_CODES = (429, 500, 503)
FACTS_HEADER = "## Aprendido nas conversas de voz"
DIRECTIVES_HEADER = "## Directives"
# US$ por 1M de tokens (3.8 Flash): entrada, leitura de cache, saída (+ raciocínio)
PRICE_IN, PRICE_CACHE, PRICE_OUT = 0.75, 0.075, 3.75


class LucidCallError(Exception):
    pass


def cfg(name):
    return os.environ.get(name) or _DEFAULTS[name]


# ---------------------------------------------------------------- guarda (inalterada)
def lost_lines(before, after):
    """Linhas não vazias de `before` ausentes em `after`, ignorando as do Lucid (`<!-- lucid:`)."""
    existentes = set(after.splitlines())
    return [l for l in before.splitlines() if l.strip() and "<!-- lucid:" not in l and l not in existentes]


def lucid_count(text):
    return sum(1 for l in text.splitlines() if "<!-- lucid:" in l)


def _read(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except OSError:
        return ""


def check_and_restore(friday_ws, backup_dir):
    """Confere o resultado; restaura MEMORY.md e USER.md do backup se algo deu errado."""
    friday_ws, backup_dir = Path(friday_ws), Path(backup_dir)
    mem_antes, mem_depois = _read(backup_dir / "MEMORY.md"), _read(friday_ws / "MEMORY.md")
    user_depois = _read(friday_ws / "USER.md")
    if lost_lines(mem_antes, mem_depois):
        status = "restored_memory_loss"
    elif len(user_depois) > USER_MD_CAP:
        status = "restored_user_md_over_cap"
    else:
        return "ok"
    for nome in ("MEMORY.md", "USER.md"):
        if (backup_dir / nome).exists():
            shutil.copyfile(backup_dir / nome, friday_ws / nome)  # só o conteúdo: dono e modo ficam
    return status


def record(date_, started_at, finished_at, turns_in, lucid_before, lucid_after, facts_added, facts_corrected,
           directives_changed, discarded, user_md_chars, briefing_chars, capabilities_chars, cost_usd, status):
    return {
        "date": date_, "started_at": started_at, "finished_at": finished_at, "turns_in": turns_in,
        "lucid_lines_before": lucid_before, "lucid_lines_after": lucid_after, "facts_added": facts_added,
        "facts_corrected": facts_corrected, "directives_changed": directives_changed, "discarded": discarded,
        "user_md_chars": user_md_chars, "briefing_chars": briefing_chars, "capabilities_chars": capabilities_chars,
        "cost_usd": cost_usd, "status": status,
    }


# ---------------------------------------------------------------- chamada ao modelo
def build_request(system, memory, user_md, skills, day, day_text):
    texto = (f"# MEMORY.md atual\n{memory}\n\n# USER.md atual\n{user_md}\n\n# Skills prontas do cérebro\n{skills}\n\n"
             f"# Conversas de voz de {day} (horário de Brasília)\n{day_text}")
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": texto}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }


def call_gemini(body, key, urlopen=urllib.request.urlopen, sleep=time.sleep):
    """POST único (com até 3 tentativas para 429/500/503). A chave só vai no cabeçalho."""
    dados = json.dumps(body).encode("utf-8")
    espera = [10, 20]
    for tentativa in range(3):
        req = urllib.request.Request(URL, data=dados, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urlopen(req, timeout=600) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                corpo = e.read().decode("utf-8", "replace")
            except Exception:
                corpo = ""
            finally:
                e.close()
            if e.code in RETRY_CODES and tentativa < 2:
                sleep(espera[tentativa])
                continue
            msg = f"HTTP {e.code}: {corpo[:300]}"
            raise LucidCallError(msg.replace(key, "***")) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise LucidCallError(f"{type(e).__name__}: {str(e)[:200]}".replace(key, "***")) from None
    raise LucidCallError("sem resposta")  # inalcançável


def response_text(resp):
    partes = resp["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in partes)


def _items(d, nome):
    v = d.get(nome)
    if not isinstance(v, list):
        raise ValueError(f"{nome} não é lista")
    saida = []
    for it in v:
        if not isinstance(it, dict) or not isinstance(it.get("text"), str) or not it["text"].strip():
            raise ValueError(f"item de {nome} sem text")
        rep = it.get("replaces")
        if rep is not None and not isinstance(rep, str):
            raise ValueError(f"replaces inválido em {nome}")
        ev = it.get("evidence", [])
        if not isinstance(ev, list) or not all(isinstance(x, str) for x in ev):
            raise ValueError(f"evidence inválido em {nome}")
        saida.append({"text": it["text"], "replaces": rep, "evidence": ev})
    return saida


def parse_result(text):
    try:
        d = json.loads(text)
    except ValueError as e:
        raise ValueError(f"JSON inválido: {e}") from None
    if not isinstance(d, dict):
        raise ValueError("resposta não é objeto")
    for nome in ("voice_briefing", "brain_capabilities"):
        if not isinstance(d.get(nome), str):
            raise ValueError(f"{nome} não é texto")
    disc = d.get("discarded", [])
    if not isinstance(disc, list):
        raise ValueError("discarded não é lista")
    vd = d.get("voice_directives")
    if not (isinstance(vd, list) and all(isinstance(x, str) for x in vd)):
        vd = None  # chave opcional: tipo errado = ausente (a vista de diretrizes não muda)
    vc = d.get("voice_directive_conflicts")
    if not (isinstance(vc, list) and all(isinstance(x, str) for x in vc)):
        vc = []
    return {"facts": _items(d, "facts"), "directives": _items(d, "directives"),
            "voice_briefing": d["voice_briefing"], "brain_capabilities": d["brain_capabilities"],
            "discarded": disc, "voice_directives": vd, "voice_directive_conflicts": vc}


def usage_cost(usage):
    prompt = usage.get("promptTokenCount") or 0
    cached = usage.get("cachedContentTokenCount") or 0
    saida = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
    return round(((prompt - cached) * PRICE_IN + cached * PRICE_CACHE + saida * PRICE_OUT) / 1e6, 6)


def record_usage(usage, day, started_at=None):
    """Appends the call's tokens to the cost usage ledger (spec 2026-10-23). Never raises: the dream goes on."""
    try:
        from datetime import timezone
        sys.path.insert(0, "/opt/friday/cost")  # installed
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cost"))  # repo: server/cost next to server/cataloger
        import ledger
        prompt = int(usage.get("promptTokenCount") or 0)
        cached = int(usage.get("cachedContentTokenCount") or 0)
        rec = ledger.make_record(
            datetime.now(timezone.utc), f"lucid:{day}:{started_at}", "lucid", "lucid", MODEL, f"lucid:{day}", None,
            {"input_text": max(0, prompt - cached), "cache_read_text": cached,
             "output_text": int(usage.get("candidatesTokenCount") or 0),
             "reasoning": int(usage.get("thoughtsTokenCount") or 0)})
        ledger.append([rec], Path(os.environ.get("FRIDAY_COST_STATE", str(ledger.STATE_DIR))))
    except Exception as e:
        print(f"aviso: uso não gravado no registro de custo: {type(e).__name__}", file=sys.stderr)


# ---------------------------------------------------------------- texto
def cut(text, max_chars):
    text = text.strip()
    if len(text) <= max_chars:
        return text
    if text[max_chars].isspace():
        return text[:max_chars].rstrip()
    corte = text[:max_chars]
    m = list(re.finditer(r"\s", corte))
    return corte[:m[-1].start()].rstrip() if m else corte


def lucid_tag(day, evidence):
    return f"<!-- lucid: date={day} evidence={','.join(evidence)} said_by=unknown source=voice -->"


def _clean(text):
    """Uma linha só, sem delimitadores de comentário que quebrariam a tag."""
    t = text.replace("<!--", "").replace("-->", "")
    return " ".join(t.split())


def _clean_evidence(ev):
    return [e for e in (re.sub(r"[^0-9:]", "", x) for x in ev) if e]


_LUCID_END = re.compile(r"\s*<!-- lucid:.*?-->\s*$")


def _line_text(line):
    """Texto de uma linha `- texto [<!-- lucid: … -->]`; None se não for item."""
    if not line.startswith("- "):
        return None
    return _LUCID_END.sub("", line[2:]).strip()


def _section(lines, header):
    """(início, fim) das linhas do corpo da seção (sem o cabeçalho), ou None."""
    try:
        i = lines.index(header)
    except ValueError:
        return None
    j = len(lines)
    for k in range(i + 1, len(lines)):
        if lines[k].startswith("## "):
            j = k
            break
    return i + 1, j


def apply_facts(memory, facts, day):
    """(texto novo, nº acrescentados, nº corrigidos). Só toca na seção do Lucid."""
    added = corrected = 0
    texto = memory
    for f in facts:
        t = _clean(f["text"])
        if not t:
            continue
        tag = lucid_tag(day, _clean_evidence(f.get("evidence") or []))
        nova = f"- {t} {tag}"
        lines = texto.split("\n")
        sec = _section(lines, FACTS_HEADER)
        if sec is None:
            base = texto if texto.endswith("\n") else texto + "\n"
            sep = "" if (base.endswith("\n\n") or base == "\n") else "\n"
            texto = f"{base}{sep}{FACTS_HEADER}\n{nova}\n"
            added += 1
            continue
        ini, fim = sec
        textos = [_line_text(l) for l in lines[ini:fim]]
        if t in textos:
            continue
        rep = f.get("replaces")
        rep = _clean(rep) if isinstance(rep, str) else None
        if rep and rep in textos:
            lines[ini + textos.index(rep)] = nova
            corrected += 1
        else:
            ultimo = ini - 1
            for k in range(ini, fim):
                if lines[k].strip():
                    ultimo = k
            lines.insert(ultimo + 1, nova)
            added += 1
        texto = "\n".join(lines)
    return texto, added, corrected


_META = re.compile(r"^<!-- observed: (\S+) \| status: (\w+) -->$")


def _blocks(lines, ini, fim):
    """Blocos da seção: lista de (índice da meta, índice da diretriz, status, texto)."""
    out = []
    k = ini
    while k < fim:
        m = _META.match(lines[k])
        if m and k + 2 < fim + 0 and lines[k + 1] == "" and lines[k + 2].startswith("- "):
            out.append((k, k + 2, m.group(2), _line_text(lines[k + 2])))
            k += 3
        else:
            k += 1
    return out


def apply_directives(user_md, directives, day):
    """(texto novo, nº de diretrizes gravadas). Só dentro de `## Directives`."""
    escritas = 0
    texto = user_md
    for d in directives:
        t = _clean(d["text"])
        if not t:
            continue
        lines = texto.split("\n")
        sec = _section(lines, DIRECTIVES_HEADER)
        if sec is None:
            continue
        ini, fim = sec
        blocos = _blocks(lines, ini, fim)
        ativos = [b for b in blocos if b[2] == "active"]
        if t in [b[3] for b in ativos]:
            continue
        tag = lucid_tag(day, _clean_evidence(d.get("evidence") or []))
        novo = ["", f"<!-- observed: {day} | status: active -->", "", f"- {t} {tag}"]
        rep = d.get("replaces")
        if rep is not None:
            rep = _clean(rep)
            alvo = [b for b in ativos if b[3] == rep]
            if not alvo:
                continue  # replaces que não casa: descartada
            meta_i, dir_i = alvo[0][0], alvo[0][1]
            lines[meta_i] = lines[meta_i].replace("status: active", "status: superseded")
            lines[dir_i + 1:dir_i + 1] = novo
        else:
            ultimo = ini - 1
            for k in range(ini, fim):
                if lines[k].strip():
                    ultimo = k
            lines[ultimo + 1:ultimo + 1] = novo
        texto = "\n".join(lines)
        escritas += 1
    return texto, escritas


RELATED_HEADER = "## Related"


def normalize_user_md(user_md):
    """Diretrizes (bloco meta + linha vazia + `- …`) fora de `## Directives` e de cercas ``` voltam para o fim
    da seção; `## Related` some se só sobrou rodapé de links (o modelo do OpenClaw o põe no fim do arquivo e o
    cérebro escreve depois dele, 23/10). Determinístico e idempotente."""
    lines = user_md.split("\n")
    sec = _section(lines, DIRECTIVES_HEADER)
    if sec is None:
        return user_md
    ini, fim = sec
    mover, remover, cerca = [], set(), False
    k = 0
    while k < len(lines):
        if lines[k].lstrip().startswith("```"):
            cerca = not cerca
        elif not cerca and not (ini <= k < fim):
            if _META.match(lines[k]) and k + 2 < len(lines) and lines[k + 1] == "" and lines[k + 2].startswith("- "):
                mover.append([lines[k], "", lines[k + 2]])
                remover.update([k, k + 1, k + 2])
                if k + 3 < len(lines) and lines[k + 3] == "":
                    remover.add(k + 3)
                k += 3
                continue
        k += 1
    if mover:
        lines = [l for i, l in enumerate(lines) if i not in remover]
        ini, fim = _section(lines, DIRECTIVES_HEADER)
        ultimo = ini - 1
        for i in range(ini, fim):
            if lines[i].strip():
                ultimo = i
        novos = []
        for b in mover:
            novos += [""] + b
        lines[ultimo + 1:ultimo + 1] = novos
    rel = _section(lines, RELATED_HEADER)
    if rel is not None:
        ri, rj = rel
        corpo = lines[ri:rj]
        if all((not l.strip()) or l.startswith("- [") for l in corpo):
            antes = lines[:ri - 1]
            depois = lines[rj:]
            while antes and not antes[-1].strip():
                antes.pop()
            if depois:
                lines = antes + [""] + depois
            else:
                lines = antes + [""]
    texto = "\n".join(lines)
    return texto


def voice_directives_view(user_md, selected, conflicts):
    """(vista, excluídas) da vista de diretrizes da voz; None se o modelo não mandou a chave."""
    if selected is None:
        return None
    lines = user_md.split("\n")
    sec = _section(lines, DIRECTIVES_HEADER)
    ativas = [b[3] for b in _blocks(lines, *sec) if b[2] == "active"] if sec else []
    escolhidas = []
    for t in selected:
        t = _clean(_LUCID_END.sub("", t))  # o modelo pode copiar a linha com a tag do Lucid
        if t in ativas and t not in escolhidas:
            escolhidas.append(t)
    linhas = ["- " + t for t in escolhidas] + ["- Em conflito: " + _clean(c) for c in conflicts]
    # Corte por linha inteira: uma diretriz cortada no meio não bateria com o USER.md no bot.
    vista, cabidas = [], set()
    for linha in linhas:
        if len("\n".join(vista + [linha])) > DIRECTIVES_VIEW_MAX:
            break
        vista.append(linha)
        cabidas.add(linha[2:])
    return "\n".join(vista), [t for t in ativas if t not in cabidas]


def move_superseded(user_md):
    """(USER.md sem os blocos superseded, os blocos removidos como `meta\\n\\n- diretriz\\n`)."""
    lines = user_md.split("\n")
    sec = _section(lines, DIRECTIVES_HEADER)
    if sec is None:
        return user_md, []
    ini, fim = sec
    remover, removidos = set(), []
    for meta_i, dir_i, status, _ in _blocks(lines, ini, fim):
        if status != "superseded":
            continue
        remover.update([meta_i, meta_i + 1, dir_i])
        if dir_i + 1 < fim and lines[dir_i + 1] == "":
            remover.add(dir_i + 1)
        removidos.append(f"{lines[meta_i]}\n\n{lines[dir_i]}\n")
    if not removidos:
        return user_md, []
    novas = [l for i, l in enumerate(lines) if i not in remover]
    for k in range(1, len(novas)):  # cabeçalho seguinte nunca fica colado ao texto
        if novas[k].startswith("## ") and novas[k - 1].strip():
            novas.insert(k, "")
            break
    return "\n".join(novas), removidos


# ---------------------------------------------------------------- execução
def _now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def _today():
    forcado = os.environ.get("TODAY")
    return date.fromisoformat(forcado) if forcado else datetime.now(TZ).date()


def _append_run(rec):
    p = Path(cfg("CATALOGER_WS")) / "state" / "runs.ndjson"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _prepare(*args):
    return subprocess.run([sys.executable, cfg("PREPARE_DAY"), *args], capture_output=True, text=True, timeout=300)


def _empty_record(day, status):
    agora = _now()
    return record(day, agora, agora, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0, status)


def _write_if_changed(path, antes, depois):
    if depois != antes:
        with open(path, "w", encoding="utf-8") as f:  # arquivo existente: dono e modo ficam
            f.write(depois)


def _process_day(day, turns, key):
    cat, fri = Path(cfg("CATALOGER_WS")), Path(cfg("FRIDAY_WS"))
    started_at = _now()
    mem0, user0 = _read(fri / "MEMORY.md"), _read(fri / "USER.md")
    user0n = normalize_user_md(user0)
    lucid_before = lucid_count(mem0)
    system = _read(Path(__file__).with_name("lucid_prompt.md")).replace("{DONO}", _local_config().owner())
    body = build_request(system, mem0, user0, _read(cat / "inbox" / "brain-skills.md"), day,
                         _read(cat / "inbox" / "voice-day.md"))
    cost, status = 0.0, "ok"
    added = corrected = directives = discarded = 0
    resp = None
    try:
        resp = call_gemini(body, key)
        cost = usage_cost(resp.get("usageMetadata") or {})
        record_usage(resp.get("usageMetadata") or {}, day, started_at)  # the cost existed even if parsing fails
        result = parse_result(response_text(resp))
    except (LucidCallError, ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
        if resp is not None:
            cost = usage_cost(resp.get("usageMetadata") or {}) if isinstance(resp, dict) else 0.0
        print(f"chamada/resposta inválida: {type(e).__name__}: {str(e)[:200].replace(key, '***')}", file=sys.stderr)
        status, result = "run_failed", None
    if result is not None:
        mem1, added, corrected = apply_facts(mem0, result["facts"], day)
        user1, directives = apply_directives(user0n, result["directives"], day)
        user1, movidos = move_superseded(user1)
        if len(user1) > USER_MD_CAP:  # sem as diretrizes novas
            user1, movidos = move_superseded(user0n)
            directives, status = 0, "user_md_full"
        _write_if_changed(fri / "MEMORY.md", mem0, mem1)
        _write_if_changed(fri / "USER.md", user0, user1)
        if movidos:
            arq = fri / "memory" / "user-superseded.md"
            arq.parent.mkdir(parents=True, exist_ok=True)
            novo = not arq.exists()
            with open(arq, "a", encoding="utf-8") as f:
                if novo:
                    f.write("# Diretrizes superseded do USER.md (movidas pelo Lucid Dreaming)\n\n")
                for b in movidos:
                    f.write(f"<!-- moved: {_today().isoformat()} -->\n{b}\n")
        (cat / "out").mkdir(parents=True, exist_ok=True)
        (cat / "out" / "voice-briefing.md").write_text(cut(result["voice_briefing"], BRIEFING_MAX), encoding="utf-8")
        (cat / "out" / "brain-capabilities.md").write_text(cut(result["brain_capabilities"], CAPABILITIES_MAX),
                                                           encoding="utf-8")
        discarded = len(result["discarded"])
        guarda = check_and_restore(fri, cat / "backups" / day.replace("-", ""))
        if guarda != "ok":
            status = guarda
        else:  # vista de diretrizes da voz; sem a chave no JSON, a anterior fica como está
            vista = voice_directives_view(user1, result.get("voice_directives"),
                                          result.get("voice_directive_conflicts", []))
            if vista is not None:
                (cat / "out" / "voice-directives.md").write_text(vista[0], encoding="utf-8")
                (cat / "out" / "voice-directives-excluded.json").write_text(
                    json.dumps({"generated_at": _now(), "excluded": vista[1]}, ensure_ascii=False), encoding="utf-8")
    rec = record(day, started_at, _now(), turns, lucid_before, lucid_count(_read(fri / "MEMORY.md")), added,
                 corrected, directives, discarded, len(_read(fri / "USER.md")),
                 len(_read(cat / "out" / "voice-briefing.md")), len(_read(cat / "out" / "brain-capabilities.md")),
                 cost, status)
    if result is not None:  # 28/10: o motivo de cada descarte, para auditar o que ficou de fora
        rec["discarded_items"] = [{"text": str(i.get("text", ""))[:300], "reason": str(i.get("reason", ""))[:300]}
                                  for i in result["discarded"][:5] if isinstance(i, dict)]
    _append_run(rec)
    if status in ("ok", "user_md_full"):
        _prepare("--done", day)
    print(f"{day} {status} {cost:.4f}")
    return status


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    max_days = 3
    if argv[:1] == ["--max-days"] and len(argv) == 2:
        max_days = int(argv[1])
    try:
        key = Path(cfg("GEMINI_KEY_FILE")).read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if not key:
        _append_run(_empty_record((_today() - timedelta(days=1)).isoformat(), "run_failed"))
        print("chave do Gemini ausente", file=sys.stderr)
        return 1
    processados = 0
    for _ in range(max_days):
        r = _prepare("--next")
        if r.returncode != 0:
            print(f"prepare_day falhou: {r.stderr[-300:]}", file=sys.stderr)
            return 1
        saida = r.stdout.strip()
        if not saida:
            if processados == 0:
                ontem = (_today() - timedelta(days=1)).isoformat()
                status = "no_turns" if Path(cfg("NDJSON_DIR")).is_dir() else "skipped_samba"
                _append_run(_empty_record(ontem, status))
            break
        dia, n = saida.split()
        status = _process_day(dia, int(n), key)
        processados += 1
        if status not in ("ok", "user_md_full"):
            break  # o mesmo dia seria preparado de novo: fica para a próxima noite
    return 0


if __name__ == "__main__":
    sys.exit(main())
