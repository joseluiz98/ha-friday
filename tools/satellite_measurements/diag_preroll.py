import os
"""DESCARTÁVEL: a 1ª palavra se perde quando a pré-gravação sai em rajada logo ao conectar?
Usa o conexao.ConexaoFriday do produto: enfileira a fala ANTES de abrir, depois o corte (1,5 s de zeros).
Uso (em ~/satelite): ~/oww/bin/python medicao/diag_preroll.py <início_s>"""
import asyncio, sys, time, wave
sys.path.insert(0, "/home/jose/satelite")
from conexao import ConexaoFriday

GRAV = "/home/jose/gravacoes/tentativas_sexta_feira_20261005_184810.wav"


async def main(inicio):
    with wave.open(GRAV) as w:
        w.setpos(int(16000 * inicio))
        fala = w.readframes(int(16000 * 2.0))
    eventos, t0 = [], time.monotonic()
    c = ConexaoFriday(os.environ["FRIDAY_URL"], {"origem": "satelite", "satelite_id": "medicao"},
                      lambda n, v: eventos.append((round(time.monotonic() - t0, 2), n, v)), lambda b: None)
    c.enviar(fala[: len(fala) // 2560 * 2560])
    c.enviar_silencio(1.5)
    await c.abrir()
    await asyncio.sleep(12)
    await c.fechar()
    print(eventos)

asyncio.run(main(float(sys.argv[1])))
