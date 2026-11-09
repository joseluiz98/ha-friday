"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_hardware -v"""
import unittest
from unittest import mock
import hardware


class TestCaptura(unittest.TestCase):
    def test_captura_eof_levanta(self):
        with mock.patch.object(hardware.subprocess, "Popen") as P:
            P.return_value.stdout.read.return_value = b"\x00" * 100  # menos que um pedaço: arecord morreu
            c = hardware.Captura(chunk=2560)
            with self.assertRaises(EOFError):
                c.ler()

    def test_anel_nunca_escreve_0x05(self):
        self.assertNotIn(0x05, hardware.BITS_DO_ANEL.values())


class TestAltoFalanteSobDemanda(unittest.TestCase):
    """06/10: com o aplay sempre aberto (tocando silêncio) a nota da palavra "Friday" caiu (A/B: 9/10 com
    notas ~1,0 contra 8/10 com quatro notas na beira). O alto-falante abre só com som e fecha depois."""

    def test_abre_so_com_som_e_fecha_quando_ocioso(self):
        with mock.patch.object(hardware.subprocess, "Popen") as P:
            a = hardware.AltoFalante(ocioso_s=0.2)
            import time
            time.sleep(0.3)
            self.assertEqual(P.call_count, 0)          # parado: nada aberto
            a.tocar_pcm48(b"\x00\x00\x00\x00" * 10)
            time.sleep(0.1)
            self.assertEqual(P.call_count, 1)          # som: abriu
            time.sleep(0.5)
            P.return_value.stdin.close.assert_called()  # ocioso: fechou
            a.tocar_pcm48(b"\x00\x00\x00\x00" * 10)
            time.sleep(0.1)
            self.assertEqual(P.call_count, 2)          # som de novo: reabriu
            a.fechar()


class TestAltoFalanteSemEcoAoFechar(unittest.TestCase):
    """09/11: ao fechar o aplay ocioso (2 s depois do último som, já em underrun) o ALSA repetia o último trecho do
    buffer: o fim do bipe de erro voltava ~1,6 s depois (gravação do Alex). Fechar com silêncio no buffer."""

    def test_escreve_meio_segundo_de_silencio_antes_de_fechar(self):
        with mock.patch.object(hardware.subprocess, "Popen") as P:
            ordem = []
            P.return_value.stdin.write.side_effect = lambda b: ordem.append(("write", b))
            P.return_value.stdin.close.side_effect = lambda: ordem.append(("close", None))
            a = hardware.AltoFalante(ocioso_s=0.2)
            import time
            a.tocar_pcm48(b"\x10\x20" * 100)
            time.sleep(0.6)
            a.fechar()
            fechou = [i for i, (k, _) in enumerate(ordem) if k == "close"]
            self.assertTrue(fechou)
            antes = ordem[fechou[0] - 1]
            self.assertEqual(antes[0], "write")
            self.assertEqual(antes[1], bytes(hardware.SILENCIO_AO_FECHAR))
            self.assertGreaterEqual(len(antes[1]), 48000 * 2 * 2 // 2)  # >= 0,5 s de S16_LE estéreo 48 kHz

    def test_falha_ao_escrever_silencio_nao_impede_o_fechamento(self):
        with mock.patch.object(hardware.subprocess, "Popen") as P:
            a = hardware.AltoFalante(ocioso_s=5.0)
            a._p = P.return_value
            P.return_value.stdin.write.side_effect = BrokenPipeError()
            a._fechar_aplay()
            P.return_value.stdin.close.assert_called()
            self.assertIsNone(a._p)
            a.fechar()


class TestVolumeDoJabra(unittest.TestCase):
    """Spec 2026-10-12 §1: o controle PCM do Jabra (0–11) e as teclas de volume do próprio aparelho."""

    def test_ler_posicao_do_amixer(self):
        saida = ("Simple mixer control 'PCM',0\n  Capabilities: pvolume pvolume-joined pswitch pswitch-joined\n"
                 "  Playback channels: Mono\n  Limits: Playback 0 - 11\n  Mono: Playback 4 [36%] [-20.00dB] [on]\n")
        with mock.patch.object(hardware.subprocess, "run") as R:
            R.return_value.stdout = saida
            self.assertEqual(hardware.Mixer().ler(), 4)

    def test_escrever_posicao(self):
        with mock.patch.object(hardware.subprocess, "run") as R:
            hardware.Mixer().escrever(7)
            self.assertEqual(R.call_args[0][0], ["amixer", "-q", "-c", "USB", "sset", "PCM", "7"])

    def test_decodifica_teclas_de_volume(self):
        self.assertEqual(hardware.delta_da_tecla(1, 115, 1), +1)
        self.assertEqual(hardware.delta_da_tecla(1, 114, 1), -1)
        self.assertIsNone(hardware.delta_da_tecla(1, 115, 0))  # soltar a tecla não conta
        self.assertIsNone(hardware.delta_da_tecla(1, 113, 1))  # mudo: quem cuida é o detector de mudo
        self.assertIsNone(hardware.delta_da_tecla(0, 0, 0))

    def test_acha_o_teclado_do_jabra_pelo_nome(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            for n, nome in ((0, "Jabra SPEAK 410 USB Consumer Control"), (2, "Jabra SPEAK 410 USB")):
                os.makedirs(f"{d}/event{n}/device")
                with open(f"{d}/event{n}/device/name", "w") as f:
                    f.write(nome + "\n")
            self.assertEqual(hardware.achar_teclado_do_jabra(d), "/dev/input/event0")

    def test_anel_do_alarme_nao_usa_o_bit_do_botao(self):
        # o bit 0 ecoa no relatório do botão: piscar ou acender com ele deixaria o botão "surdo" no alarme
        self.assertEqual(hardware.BITS_DO_ANEL["alarme"] & 0x01, 0)
        self.assertNotIn(hardware.BITS_DO_ANEL["alarme"], (0x05, 0x08, 0x40))


class TestAltoFalanteLimpar(unittest.TestCase):
    def test_limpar_descarta_o_que_ainda_nao_tocou(self):
        with mock.patch.object(hardware.subprocess, "Popen"):
            a = hardware.AltoFalante(ocioso_s=0.2)
            a._vivo = False  # para o laço: só a fila importa aqui
            import time
            time.sleep(0.6)
            for _ in range(5):
                a.tocar_pcm48(b"\x00" * 4)
            a.limpar()
            self.assertTrue(a._fila.empty())


class TestAltoFalanteVoice(unittest.TestCase):
    def test_speaker_uses_voice_pcm(self):
        with mock.patch.object(hardware.subprocess, "Popen") as P:
            a = hardware.AltoFalante(ocioso_s=0.2)
            a._abrir()
            cmd = P.call_args[0][0]
            self.assertEqual(cmd[cmd.index("-D") + 1], "voice")
            a.fechar()

    def test_speaker_records_last_write(self):
        import time
        with mock.patch.object(hardware.subprocess, "Popen"):
            a = hardware.AltoFalante(ocioso_s=0.2)
            self.assertIsNone(a.ultimo_envio)
            a.tocar_pcm48(b"\0" * 4)
            time.sleep(0.8)
            self.assertIsNotNone(a.ultimo_envio)
            a.fechar()


class TestMusicMixer(unittest.TestCase):
    def test_music_mixer_command(self):
        with mock.patch.object(hardware.subprocess, "run") as R:
            hardware.MusicMixer().write(15)
            R.assert_called_once()
            self.assertEqual(R.call_args[0][0], ["amixer", "-q", "-c", "USB", "sset", "Music", "15%"])

    def test_music_mixer_prime_plays_silence_on_music_pcm(self):
        with mock.patch.object(hardware.subprocess, "run") as R:
            hardware.MusicMixer().prime()
            cmd = R.call_args[0][0]
            self.assertEqual(cmd[:4], ["aplay", "-q", "-D", "music"])
            self.assertEqual(R.call_args[1]["input"], b"\0" * (48000 // 10 * 4))

class TestBotaoCor(unittest.TestCase):
    def test_luzes_ouvindo_e_alarme(self):
        self.assertEqual(hardware.BITS_DO_ANEL["ouvindo"], 0x11)
        self.assertEqual(hardware.BITS_DO_ANEL["alarme"], 0x10)

    def test_esperar_toque_devolve_a_cor(self):
        import os
        from detectores import DetectorBotaoTelefone
        r, w = os.pipe()
        try:
            with mock.patch.object(hardware.os, "open", return_value=r):
                det = DetectorBotaoTelefone()
                det.comandou(0x00, -10.0)
                b = hardware.Botao(det, caminho="/dev/fake")
            os.write(w, bytes([3, 0, 0]))
            self.assertEqual(b.esperar_toque(), "vermelho")
        finally:
            os.close(r); os.close(w)

class TestAltoFalanteParaJa(unittest.TestCase):
    def _espera(self, cond, t=2.0):
        import time
        fim = time.monotonic() + t
        while time.monotonic() < fim and not cond():
            time.sleep(0.01)
        return cond()

    def test_parar_ja_esvazia_fila_e_mata_o_aplay(self):
        import tempfile, os, wave
        with mock.patch.object(hardware.subprocess, "Popen") as popen:
            a = hardware.AltoFalante(ocioso_s=5.0)
            try:
                a.tocar_pcm48(b"x" * 960)
                self.assertTrue(self._espera(lambda: popen.call_count == 1 and a.ultimo_envio is not None))
                p1 = popen.return_value
                for _ in range(5):
                    a.tocar_pcm48(b"\x00" * 960)
                a.parar_ja()
                self.assertTrue(a._fila.empty())
                p1.kill.assert_called()
                with tempfile.TemporaryDirectory() as d:
                    caminho = os.path.join(d, "s.wav")
                    with wave.open(caminho, "wb") as w:
                        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
                        w.writeframes(b"\x00" * 400)
                    a.tocar_wav(caminho)
                    self.assertTrue(self._espera(lambda: popen.call_count == 2))
            finally:
                a._vivo = False

    def test_parar_ja_sem_aplay_aberto_nao_levanta(self):
        with mock.patch.object(hardware.subprocess, "Popen"):
            a = hardware.AltoFalante(ocioso_s=5.0)
            try:
                a.parar_ja()
            finally:
                a._vivo = False

    def test_write_em_aplay_morto_por_parar_ja_nao_derruba_o_programa(self):
        # o laço pode estar escrevendo quando o aplay é morto: BrokenPipe aí NÃO é o Jabra fora (os._exit)
        with mock.patch.object(hardware.subprocess, "Popen") as popen, mock.patch.object(hardware.os, "_exit") as sai:
            a = hardware.AltoFalante(ocioso_s=5.0)
            try:
                def escreve_e_morre(_dados):
                    a.parar_ja()
                    raise BrokenPipeError
                popen.return_value.stdin.write.side_effect = escreve_e_morre
                a.tocar_pcm48(b"x" * 960)
                self.assertTrue(self._espera(lambda: popen.return_value.kill.called))
                import time; time.sleep(0.1)
                sai.assert_not_called()
            finally:
                a._vivo = False


if __name__ == "__main__":
    unittest.main()


class TestCaminhoDoJabra(unittest.TestCase):
    def _raiz(self, nomes):
        import tempfile, os
        raiz = tempfile.mkdtemp()
        for i, nome in enumerate(nomes):
            os.makedirs(os.path.join(raiz, f"hidraw{i}", "device"))
            with open(os.path.join(raiz, f"hidraw{i}", "device", "uevent"), "w") as f:
                f.write(f"HID_ID=0003:0000:0000\nHID_NAME={nome}\n")
        return raiz

    def test_acha_jabra_atras_de_outro_hid(self):  # 25/10: receptor Dell no hidraw0/1
        raiz = self._raiz(["LITEON Dell Wireless Device", "LITEON Dell Wireless Device", "Jabra SPEAK 410 USB"])
        self.assertEqual(hardware.caminho_do_jabra(raiz), "/dev/hidraw2")

    def test_sem_jabra_levanta(self):
        with self.assertRaises(RuntimeError):
            hardware.caminho_do_jabra(self._raiz(["LITEON Dell Wireless Device"]))


class TestControleMudo(unittest.TestCase):
    def test_mudo_corta_e_devolve_a_captura(self):
        chamadas = []
        m = hardware.ControleMudo(executar=lambda argv, timeout: chamadas.append(argv) or mock.Mock(returncode=0))
        self.assertTrue(m.aplicar(True)); self.assertTrue(m.aplicar(False))
        self.assertEqual(chamadas[0], ["amixer", "-q", "-c", "USB", "sset", "Headset", "nocap"])
        self.assertEqual(chamadas[1][-1], "cap")

    def test_falha_do_amixer_devolve_falso(self):
        m = hardware.ControleMudo(executar=lambda argv, timeout: mock.Mock(returncode=1))
        self.assertFalse(m.aplicar(True))
