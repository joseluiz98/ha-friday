"""Resumo da nota do modelo por tentativa numa sessão guiada (guided_sessions.sh). Lê, da entrada padrão, a linha do
rescore.py e o .json da sessão. Limiar: 0,6 para a Friday, 0,5 para a Sexta-feira."""
import json, sys

s = json.loads(sys.stdin.readline())["s"]
info = json.loads(sys.stdin.readline())
maxs = [max(s[int((t + 0.2) / 0.08):int((t + 5.8) / 0.08)], default=0) for t in info["ticks"]]
lim = 0.6 if info["word"] == "friday" else 0.5
rot = info.get("style") or "TV %s" % info.get("tv_app")
ok = lambda xs: sum(m >= lim for m in xs)
print(f"{rot:42} {ok(maxs):2}/20 (perto {ok(maxs[:10])}/10, longe {ok(maxs[10:])}/10)")
print("   notas:", " ".join(f"{m:.2f}" for m in maxs))
