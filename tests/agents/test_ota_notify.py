from zoneinfo import ZoneInfo
import datetime as dt
import pytest
from friday_server_agent.ota import notify as n
SP = ZoneInfo("America/Sao_Paulo")
at = lambda h: dt.datetime(2026, 11, 10, h, 0, tzinfo=SP).timestamp()

def test_text():
    assert n.format_exhausted("Mark da sala", "v0.3.0", "health", "no_program", "v0.2.1") == \
        "F.R.I.D.A.Y.: a atualização v0.3.0 de Mark da sala falhou em health (no_program) depois de 5 tentativas. Segue na versão v0.2.1."

def test_text_without_previous_version():
    assert n.format_exhausted("d", "v0.3.0", "s", "e", None).endswith("Segue na versão anterior.")

def test_night_push_waits_until_morning():
    sent, pushes = [], []
    run = lambda cmd, **kw: (sent.append(cmd), type("R", (), {"returncode": 0})())[1]
    nt = n.Notifier("123", run=run, clock=lambda: at(23), tz=SP)
    nt.queue("x", pushes)
    assert nt.flush(pushes) == 0 and sent == [] and len(pushes) == 1
    nt2 = n.Notifier("123", run=run, clock=lambda: at(23) + 9 * 3600, tz=SP)
    assert nt2.flush(pushes) == 1 and pushes == [] and sent[0][-2:] == ["-m", "x"]
    assert sent[0] == ["oc", "message", "send", "--channel", "telegram", "--target", "123", "-m", "x"]

def test_failed_send_is_kept():
    pushes = [{"at": 0, "text": "x"}]
    run = lambda cmd, **kw: type("R", (), {"returncode": 1})()
    assert n.Notifier("123", run=run, clock=lambda: 1).flush(pushes) == 0 and len(pushes) == 1

def test_empty_chat_rejected():
    with pytest.raises(ValueError):
        n.Notifier("")


def test_withdrawn_message():
    m = n.format_withdrawn("sat-1", "v0.4.0", "health", "program not answering", "v0.3.0")
    assert "v0.4.0" in m and "sat-1" in m and "program not answering" in m and "v0.3.0" in m and "alpha" in m


def test_format_promoted():
    assert n.format_promoted("v0.3.7") == \
        "F.R.I.D.A.Y. v0.3.7 passou na prova do alpha e agora é a versão stable."


def test_format_block_too_late():
    text = n.format_block_too_late("v0.4.3")
    assert "v0.4.3" in text and "tarde demais" in text
