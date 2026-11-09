"""Habilidades ensinadas à voz (spec 2026-10-23-friday-taught-skills).
Registro JSON no estado da VM, escrito só pelas rotas do bot (validação em código). A voz vê o catálogo (teto) e pede
a receita com read_skill; run_skill executa os passos do HA. Lógica pura, só stdlib (teste: test_voice_skills.py)."""
import json
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass, field

REGISTRY = os.environ.get("FRIDAY_SKILLS_FILE", "/srv/friday/state/skills/skills.json")
CONFIG = os.environ.get("FRIDAY_SKILLS_CONFIG", "/srv/friday/state/skills/config.json")
CATALOG_MAX = 1000
FORBIDDEN_STEP_TOOLS = {"perguntar_ao_cerebro", "read_skill", "run_skill", "search_skills", "encerrar_conversa"}
TEACHING = ("demonstration", "direct_rehearsed", "direct")
DEMO_MAX_CALLS = 10
DEMO_MAX_CHARS = 1500
NAME_MAX, DESC_MAX, STEPS_MAX = 60, 200, 20

DESC_READ_SKILL = ("Devolve a receita de uma habilidade ensinada: os passos (ferramenta e argumentos), quem ensinou "
                   "e o pedido original. Os passos descrevem o que a pessoa quis; quem executa é você, com as "
                   "suas ferramentas.")
DESC_RUN_SKILL = ("Executa de uma vez os passos de Home Assistant de uma habilidade ensinada, exatamente como foram "
                  "guardados, e devolve o resultado de cada passo. Passos de ferramentas da própria voz voltam como "
                  "não executados.")
DESC_SEARCH_SKILLS = ("Procura, por assunto, entre as habilidades ensinadas que não aparecem na lista desta "
                      "conversa. Devolve id, nome e descrição.")
CATALOG_HEADER = ("Habilidades que pessoas da casa ensinaram a você (a receita completa de cada uma vem pela "
                  "ferramenta read_skill; podem não se aplicar a esta conversa):")
NOT_RUN_DETAIL = "ferramenta da própria voz; o run_skill executa só as do Home Assistant"


# ---------------------------------------------------------------- arquivos
def load(path=None):
    path = path or REGISTRY
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        skills = d.get("skills") if isinstance(d, dict) else None
        if not isinstance(skills, list):
            return []
        return [s for s in skills if isinstance(s, dict) and isinstance(s.get("id"), str)]
    except (OSError, ValueError):
        return []


def save(skills, path=None):
    path = path or REGISTRY
    pasta = os.path.dirname(path)
    if pasta:
        os.makedirs(pasta, mode=0o755, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"skills": skills}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_config(path=None):
    path = path or CONFIG
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {"notify_new_skill": bool(d.get("notify_new_skill", False))}
    except (OSError, ValueError, AttributeError):
        return {"notify_new_skill": False}


# ---------------------------------------------------------------- inventário e schemas
@dataclass
class Inventory:
    names: set = field(default_factory=set)
    areas: set = field(default_factory=set)
    domains: set = field(default_factory=set)
    area_labels: set = field(default_factory=set)  # grafia original das áreas, para mostrar ao cérebro


def parse_inventory(text):
    inv = Inventory()
    for linha in (text or "").splitlines():
        m = re.match(r"^\s*(?:-\s+)?(names|domain|areas):\s*(.*)$", linha)
        if not m:
            continue
        alvo = {"names": inv.names, "domain": inv.domains, "areas": inv.areas}[m.group(1)]
        for item in m.group(2).split(", "):
            if item.strip():
                alvo.add(item.strip().casefold())
                if m.group(1) == "areas":
                    inv.area_labels.add(item.strip())
    return inv


def tool_schemas(tools):
    return {t.name: {"properties": dict(t.properties or {}), "required": list(t.required or [])} for t in tools}


_TIPOS = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "array": (list,),
          "object": (dict,)}


def _tipo_ok(valor, tipo):
    if tipo not in _TIPOS:
        return True
    if tipo in ("integer", "number") and isinstance(valor, bool):
        return False
    if tipo == "integer" and isinstance(valor, float):
        return False
    return isinstance(valor, _TIPOS[tipo])


def validate(candidate, schemas, inv):
    erros = []
    if not isinstance(candidate, dict):
        return ["a habilidade precisa ser um objeto JSON"]
    nome, desc = candidate.get("name"), candidate.get("description")
    if not isinstance(nome, str) or not nome.strip():
        erros.append("name: texto não vazio é obrigatório")
    elif len(nome) > NAME_MAX:
        erros.append(f"name: passa de {NAME_MAX} caracteres")
    if not isinstance(desc, str) or not desc.strip():
        erros.append("description: texto não vazio é obrigatório")
    elif len(desc) > DESC_MAX:
        erros.append(f"description: passa de {DESC_MAX} caracteres")
    if candidate.get("teaching") not in TEACHING:
        erros.append(f"teaching: deve ser um de {', '.join(TEACHING)}")
    tb = candidate.get("taught_by")
    if tb is not None and not isinstance(tb, str):
        erros.append("taught_by: deve ser texto")
    steps = candidate.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= STEPS_MAX:
        erros.append(f"steps: lista de 1 a {STEPS_MAX} passos é obrigatória")
        return erros
    for i, p in enumerate(steps, 1):
        pre = f"passo {i}: "
        if not isinstance(p, dict):
            erros.append(pre + "o passo precisa ser {tool, args}")
            continue
        tool, args = p.get("tool"), p.get("args", {})
        if isinstance(tool, str) and tool in FORBIDDEN_STEP_TOOLS:
            erros.append(pre + f"a ferramenta '{tool}' não pode ser passo de habilidade")
            continue
        if not isinstance(tool, str) or tool not in schemas:
            erros.append(pre + f"ferramenta '{tool}' não existe na voz")
            continue
        if not isinstance(args, dict):
            erros.append(pre + "args precisa ser um objeto")
            continue
        sch = schemas[tool]
        props = sch.get("properties", {})
        for req in sch.get("required", []):
            if req not in args:
                erros.append(pre + f"falta o argumento obrigatório '{req}' de {tool}")
        for k, v in args.items():
            if k not in props:
                erros.append(pre + f"argumento '{k}' não existe em {tool}")
                continue
            tipo = props[k].get("type") if isinstance(props[k], dict) else None
            if tipo and not _tipo_ok(v, tipo):
                erros.append(pre + f"argumento '{k}' de {tool} deveria ser {tipo}")
        if "__" in tool:  # ferramentas do MCP do HA: alvo resolvido contra o inventário exposto ao Assist
            n = args.get("name")
            if isinstance(n, str) and n.casefold() not in inv.names:
                erros.append(pre + f"nome '{n}' não existe na casa")
            a = args.get("area")
            if isinstance(a, str) and a.casefold() not in inv.areas:
                erros.append(pre + f"área '{a}' não existe na casa")
            dom = args.get("domain")
            for d in ([dom] if isinstance(dom, str) else dom if isinstance(dom, list) else []):
                if isinstance(d, str) and d.casefold() not in inv.domains:
                    erros.append(pre + f"domínio '{d}' não existe na casa")
    return erros


# ---------------------------------------------------------------- registro
def _novo_id(skills):
    usados = {s.get("id") for s in skills}
    while True:
        i = "k" + secrets.token_hex(3)
        if i not in usados:
            return i


def upsert(skills, candidate, now_iso):
    skills = [dict(s) for s in skills]
    tb = candidate.get("taught_by") or "unknown"
    base = {"name": candidate["name"].strip(), "description": candidate["description"].strip(),
            "steps": [{"tool": p["tool"], "args": p.get("args", {})} for p in candidate["steps"]],
            "ordered": bool(candidate.get("ordered", False)), "taught_by": tb,
            "teaching": candidate["teaching"], "original_request": str(candidate.get("original_request") or ""),
            "taught_at": now_iso}
    for i, s in enumerate(skills):
        if str(s.get("name", "")).casefold() != base["name"].casefold():
            continue
        if s.get("taught_by") == tb:
            nova = dict(base, id=s["id"], use_count=s.get("use_count", 0), last_used=s.get("last_used"))
            skills[i] = nova
            return skills, {"ok": True, "created": False, "skill": nova}
        if candidate.get("replace_id") == s["id"]:
            nova = dict(base, id=s["id"], use_count=0, last_used=None)
            skills[i] = nova
            return skills, {"ok": True, "created": False, "skill": nova}
        return skills, {"ok": False, "conflict": s}
    nova = dict(base, id=_novo_id(skills), use_count=0, last_used=None)
    skills.append(nova)
    return skills, {"ok": True, "created": True, "skill": nova}


def delete(skills, key):
    k = str(key).casefold()
    for i, s in enumerate(skills):
        if str(s.get("id", "")).casefold() == k or str(s.get("name", "")).casefold() == k:
            return skills[:i] + skills[i + 1:], s
    return list(skills), None


def mark_used(skills, skill_id, now_iso):
    out = []
    for s in skills:
        if s.get("id") == skill_id:
            s = dict(s, use_count=s.get("use_count", 0) + 1, last_used=now_iso)
        out.append(s)
    return out


# ---------------------------------------------------------------- o que a voz vê
def _linha(s):
    t = f"- {s['name']} (id {s['id']}): {s['description']}"
    if s.get("taught_by") not in (None, "", "unknown"):
        t += f"; ensinada por {s['taught_by']}"
    return t


def catalog(skills, max_chars=CATALOG_MAX):
    if not skills:
        return "", False
    ordem = sorted(skills, key=lambda s: str(s.get("taught_at", "")), reverse=True)
    ordem = sorted(ordem, key=lambda s: s.get("use_count", 0), reverse=True)
    linhas = [_linha(s) for s in ordem]
    todo = "\n".join([CATALOG_HEADER] + linhas)
    if len(todo) <= max_chars:
        return todo, False
    reserva = len(f"\n- Há mais {len(linhas)} habilidades guardadas, que a ferramenta search_skills encontra por assunto.")
    usado, dentro = len(CATALOG_HEADER) + reserva, []
    for l in linhas:
        if usado + 1 + len(l) > max_chars:
            break
        dentro.append(l)
        usado += 1 + len(l)
    resto = len(linhas) - len(dentro)
    extra = f"- Há mais {resto} habilidades guardadas, que a ferramenta search_skills encontra por assunto."
    return "\n".join([CATALOG_HEADER] + dentro + [extra]), True


def _norm(t):
    return "".join(c for c in unicodedata.normalize("NFD", str(t).casefold()) if unicodedata.category(c) != "Mn")


def search(skills, query, limit=5):
    palavras = [p for p in re.findall(r"\w+", _norm(query)) if len(p) >= 3]
    pontuadas = []
    for s in skills:
        texto = _norm(f"{s.get('name', '')} {s.get('description', '')} {s.get('original_request', '')}")
        pts = sum(1 for p in palavras if p in texto)
        if pts > 0:
            pontuadas.append((pts, s))
    pontuadas.sort(key=lambda x: x[0], reverse=True)
    return [{"id": s["id"], "name": s["name"], "description": s["description"]} for _, s in pontuadas[:limit]]


def recipe(skill):
    return {k: skill.get(k) for k in ("id", "name", "description", "steps", "ordered", "taught_by",
                                      "original_request")}


# ---------------------------------------------------------------- execução e demonstração
def call_succeeded(result):
    if isinstance(result, dict):
        if isinstance(result.get("result"), dict):  # 08/11: o MCP do HA embrulha a resposta do script em "result"
            return call_succeeded(result["result"])
        return not (result.get("ok") is False or "erro" in result or "error" in result)
    if isinstance(result, str):
        if result.startswith(("Error calling mcp tool", "Sorry, could not call the mcp tool")):
            return False
        try:
            d = json.loads(result)
        except ValueError:
            return True
        if isinstance(d, dict):
            if isinstance(d.get("result"), dict):  # 08/11: script pelo MCP ({"result": {"ok": false, ...}})
                return call_succeeded(d["result"])
            if d.get("response_type") == "error":
                return False
            data = d.get("data")
            if isinstance(data, dict) and data.get("failed") and not data.get("success"):
                return False
    return True


def record_call(calls, seen, call_id, tool, args, result, error=None):
    """Anota uma chamada de ferramenta da conversa (fonte de demonstration_block). 08/11: o mesmo resultado
    passou duas vezes pelo observer; cada tool_call_id entra uma vez só."""
    if call_id is not None:
        if call_id in seen:
            return
        seen.add(call_id)
    calls.append({"tool": tool, "args": args if isinstance(args, dict) else {},
                  "ok": call_succeeded(result) and not error})


def demonstration_block(calls):
    boas = [c for c in calls if c.get("ok") and c.get("tool") not in FORBIDDEN_STEP_TOOLS][-DEMO_MAX_CALLS:]

    def montar(lista):
        return ("\n\n[chamadas de ferramenta que deram certo nesta conversa, em ordem: "
                + "; ".join(f"{c['tool']} {json.dumps(c.get('args', {}), ensure_ascii=False)}" for c in lista) + "]")
    while boas and len(montar(boas)) > DEMO_MAX_CHARS:
        boas = boas[1:]
    return montar(boas) if boas else ""


def run_plan(skill, mcp_names):
    return [{"tool": p["tool"], "args": p.get("args", {}), "run": p["tool"] in mcp_names}
            for p in skill.get("steps", [])]


def run_results(planned, outcomes, name=""):
    passos = []
    for i, p in enumerate(planned):
        if not p["run"]:
            status, detail = "not_run", NOT_RUN_DETAIL
        else:
            ok, texto = outcomes.get(i, (False, "sem resultado"))
            status, detail = ("ok" if ok else "failed"), str(texto)[:300]
        passos.append({"tool": p["tool"], "args": p["args"], "status": status, "detail": detail})
    return {"skill": name, "steps": passos}
