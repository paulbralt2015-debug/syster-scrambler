"""Lecture audio en flux (ffmpeg + sounddevice) avec inversion de spectre.

L'inversion de spectre est symétrique : l'appliquer à un son clair le brouille,
l'appliquer à un son brouillé le remet en clair."""
import queue
import shutil
import subprocess
import threading

import numpy as np
from scipy.signal import butter, sosfilt

SR = 32000
CH = 2


def trouver_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


class InverseurSpectre:
    """Filtre passe-bas -> multiplication par une porteuse fc -> filtre passe-bas.
    Une composante f devient fc - f. Fonctionne bloc par bloc (états conservés)."""

    def __init__(self, fc=6800.0, sr=SR, canaux=CH):
        self.sr, self.canaux = sr, canaux
        self.fc = None
        self.regler(fc)

    def regler(self, fc):
        fc = float(min(max(fc, 500.0), self.sr / 2 * 0.9))
        if fc == self.fc:
            return
        self.fc = fc
        self.sos = butter(8, fc / (self.sr / 2), output="sos")
        n = self.sos.shape[0]
        self.zi1 = np.zeros((n, self.canaux, 2))
        self.zi2 = np.zeros((n, self.canaux, 2))
        self.n0 = 0

    def reset(self):
        n = self.sos.shape[0]
        self.zi1 = np.zeros((n, self.canaux, 2))
        self.zi2 = np.zeros((n, self.canaux, 2))
        self.n0 = 0

    def traiter(self, x):
        """x : tableau float (canaux, n)."""
        n = x.shape[1]
        y, self.zi1 = sosfilt(self.sos, x, axis=-1, zi=self.zi1)
        t = (self.n0 + np.arange(n)) / self.sr
        y = y * (2.0 * np.cos(2 * np.pi * self.fc * t))[None, :]
        y, self.zi2 = sosfilt(self.sos, y, axis=-1, zi=self.zi2)
        self.n0 += n
        return y


class MoteurAudio:
    def __init__(self):
        self.ffmpeg = trouver_ffmpeg()
        self.sd = None
        self.erreur = None
        self.stream = None
        self.proc = None
        self.q = queue.Queue(maxsize=48)
        self.pend = bytearray()
        self.verrou = threading.Lock()
        self.pause = True
        self.actif = False
        self.fini = False
        self.joues = 0
        self.t0 = 0.0
        self.inverser = False
        self.inv = InverseurSpectre()
        self._etat_inv = False
        try:
            import sounddevice as sd
            self.sd = sd
        except Exception as e:  # PortAudio absent, etc.
            self.erreur = f"sounddevice : {e}"
        if self.ffmpeg is None and self.erreur is None:
            self.erreur = "ffmpeg introuvable"

    @property
    def disponible(self):
        return self.sd is not None and self.ffmpeg is not None

    def regler_fc(self, fc):
        self.inv.regler(fc)

    def demarrer(self, chemin, t):
        """Lance la lecture audio de `chemin` à partir de t secondes."""
        if not self.disponible:
            return
        self.arreter_flux()
        q = queue.Queue(maxsize=48)
        cmd = [self.ffmpeg, "-loglevel", "quiet", "-ss", f"{max(t, 0):.3f}",
               "-i", chemin, "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
               "-ac", str(CH), "-ar", str(SR), "-"]
        try:
            kw = {}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, **kw)
        except Exception as e:
            self.erreur = f"ffmpeg : {e}"
            return

        def lire():
            try:
                while True:
                    d = proc.stdout.read(16384)
                    if not d:
                        break
                    q.put(d)
            finally:
                q.put(None)

        threading.Thread(target=lire, daemon=True).start()
        with self.verrou:
            self.proc, self.q = proc, q
            self.pend = bytearray()
            self.joues = 0
            self.t0 = t
            self.fini = False
            self.actif = True
            self.inv.reset()
        if self.stream is None:
            try:
                self.stream = self.sd.OutputStream(
                    samplerate=SR, channels=CH, dtype="int16",
                    blocksize=1024, callback=self._cb)
                self.stream.start()
            except Exception as e:
                self.erreur = f"sortie audio : {e}"
                self.stream = None
                self.actif = False

    def arreter_flux(self):
        with self.verrou:
            self.actif = False
            proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def fermer(self):
        self.arreter_flux()
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def temps(self):
        """Temps média (s) selon l'horloge audio, ou None si indisponible."""
        if not self.actif or self.pause or self.joues == 0:
            return None
        if self.fini and not self.pend:
            return None
        return self.t0 + self.joues / SR

    def _cb(self, outdata, frames, temps, statut):
        if self.pause or not self.actif or not self.verrou.acquire(False):
            outdata.fill(0)
            return
        try:
            besoin = frames * CH * 2
            while len(self.pend) < besoin and not self.fini:
                try:
                    c = self.q.get_nowait()
                except queue.Empty:
                    break
                if c is None:
                    self.fini = True
                    break
                self.pend += c
            prend = min(len(self.pend), besoin) // (CH * 2) * (CH * 2)
            outdata.fill(0)
            if prend:
                brut = bytes(self.pend[:prend])
                del self.pend[:prend]
                x = np.frombuffer(brut, dtype=np.int16).reshape(-1, CH).T
                x = x.astype(np.float64) / 32768.0
                if self.inverser:
                    if not self._etat_inv:
                        self.inv.reset()
                    x = self.inv.traiter(x)
                self._etat_inv = self.inverser
                y = (np.clip(x, -1, 1) * 32767).astype(np.int16)
                outdata[:y.shape[1]] = y.T
                self.joues += y.shape[1]
        finally:
            self.verrou.release()
