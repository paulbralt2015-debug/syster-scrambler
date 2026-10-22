"""Lecteur vidéo : codage / décodage Syster et VideoCrypt (émulation) en temps réel,
avec son (inversion de spectre) et raster PAL / SECAM."""
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

import cv2
from PIL import Image, ImageTk

import scrambler as sc
from audio import MoteurAudio

SYSTEMES = ["Aucun", "Syster", "VideoCrypt"]
ACTIONS = ["Coder", "Décoder (avec la clé)", "Décoder (aveugle)"]
FORMATS = ["PAL / SECAM (720x576, 25 i/s)", "Format d'origine"]
QUALITES = {"Rapide": 16, "Normal": 8, "Précis": 2}


class Lecteur:
    def __init__(self, root, chemin=None):
        self.root = root
        root.title("Lecteur Syster / VideoCrypt")
        root.geometry("1000x700")
        root.minsize(640, 420)

        self.audio = MoteurAudio()
        self.cap = None
        self.chemin = None
        self.fps = 25.0
        self.total = 0
        self.pos = 0
        self.playing = False
        self.pending_open = chemin
        self.pending_seek = None
        self.latest = None
        self.proc_fps = 0.0
        self.running = True
        self.dragging = False
        self.ref_media, self.ref_wall = 0.0, time.time()

        self.v_systeme = tk.StringVar(value="Syster")
        self.v_action = tk.StringVar(value="Coder")
        self.v_format = tk.StringVar(value=FORMATS[0])
        self.v_cle = tk.StringVar(value="secret")
        self.v_fenetre = tk.IntVar(value=32)
        self.v_champs = tk.BooleanVar(value=True)
        self.v_son = tk.BooleanVar(value=False)
        self.v_fc = tk.IntVar(value=6800)
        self.v_qualite = tk.StringVar(value="Normal")
        self.v_flip = tk.BooleanVar(value=False)
        # copie « simple » des réglages, lue par le thread de lecture
        # (tkinter n'est pas fait pour être appelé depuis un autre thread)
        self.cfg = {}
        self._sync()

        self.aveugle_syster = sc.AveugleSyster()
        self.aveugle_vc = sc.AveugleVideoCrypt()
        self._cle_aveugle = None

        self._ui()
        threading.Thread(target=self._worker, daemon=True).start()
        root.after(15, self._refresh)
        root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------ interface
    def _ui(self):
        self.video = tk.Label(self.root, bg="black")
        self.video.pack(fill="both", expand=True)
        self.slider = ttk.Scale(self.root, from_=0, to=1000)
        self.slider.pack(fill="x", padx=8, pady=(4, 0))
        self.slider.bind("<ButtonPress-1>", lambda e: setattr(self, "dragging", True))
        self.slider.bind("<ButtonRelease-1>", self._slider_release)

        l1 = ttk.Frame(self.root); l1.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Button(l1, text="Ouvrir…", command=self.ouvrir).pack(side="left")
        self.btn = ttk.Button(l1, text="▶ Lecture", command=self.toggle)
        self.btn.pack(side="left", padx=6)
        ttk.Label(l1, text="Format :").pack(side="left", padx=(10, 2))
        ttk.Combobox(l1, textvariable=self.v_format, values=FORMATS, width=28,
                     state="readonly").pack(side="left")
        ttk.Label(l1, text="Système :").pack(side="left", padx=(10, 2))
        ttk.Combobox(l1, textvariable=self.v_systeme, values=SYSTEMES, width=11,
                     state="readonly").pack(side="left")
        ttk.Label(l1, text="Action :").pack(side="left", padx=(10, 2))
        ttk.Combobox(l1, textvariable=self.v_action, values=ACTIONS, width=20,
                     state="readonly").pack(side="left")

        l2 = ttk.Frame(self.root); l2.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(l2, text="Clé :").pack(side="left")
        ttk.Entry(l2, textvariable=self.v_cle, width=14).pack(side="left", padx=(2, 10))
        ttk.Label(l2, text="Fenêtre Syster (lignes) :").pack(side="left")
        ttk.Spinbox(l2, from_=4, to=144, increment=4, textvariable=self.v_fenetre,
                    width=5).pack(side="left", padx=(2, 10))
        ttk.Checkbutton(l2, text="Par trame (entrelacé)", variable=self.v_champs).pack(side="left")
        ttk.Label(l2, text="Qualité aveugle :").pack(side="left", padx=(10, 2))
        ttk.Combobox(l2, textvariable=self.v_qualite, values=list(QUALITES), width=8,
                     state="readonly").pack(side="left")
        ttk.Checkbutton(l2, text="Inverser haut/bas (F)", variable=self.v_flip).pack(side="left", padx=10)

        l3 = ttk.Frame(self.root); l3.pack(fill="x", padx=8, pady=6)
        ttk.Checkbutton(l3, text="Son : inversion de spectre (S)", variable=self.v_son).pack(side="left")
        ttk.Label(l3, text="fc (Hz) :").pack(side="left", padx=(10, 2))
        ttk.Scale(l3, from_=1000, to=12000, variable=self.v_fc, length=200).pack(side="left")
        self.lbl_fc = ttk.Label(l3, text="6800", width=6)
        self.lbl_fc.pack(side="left")
        self.status = ttk.Label(l3, text="Aucun fichier")
        self.status.pack(side="right")

        r = self.root
        r.bind("<space>", lambda e: self.toggle())
        r.bind("s", lambda e: self.v_son.set(not self.v_son.get()))
        r.bind("f", lambda e: self.v_flip.set(not self.v_flip.get()))
        r.bind("<Left>", lambda e: self._seek_rel(-5))
        r.bind("<Right>", lambda e: self._seek_rel(5))
        r.bind("<Prior>", lambda e: self._decaler(-8))   # Page haut / bas :
        r.bind("<Next>", lambda e: self._decaler(8))     # recaler le décodage aveugle VideoCrypt

    def _decaler(self, d):
        self.aveugle_vc.decalage += d

    def _sync(self):
        def lire(var, defaut):
            try:
                return var.get()
            except Exception:
                return defaut
        self.cfg = {
            "systeme": lire(self.v_systeme, "Aucun"),
            "action": lire(self.v_action, "Coder"),
            "pal": lire(self.v_format, FORMATS[0]) == FORMATS[0],
            "cle": lire(self.v_cle, ""),
            "fenetre": max(2, int(lire(self.v_fenetre, 32) or 32)),
            "champs": bool(lire(self.v_champs, True)),
            "pas": QUALITES.get(lire(self.v_qualite, "Normal"), 8),
            "flip": bool(lire(self.v_flip, False)),
        }
        self.audio.inverser = bool(lire(self.v_son, False))
        self.audio.regler_fc(lire(self.v_fc, 6800))

    def ouvrir(self):
        p = filedialog.askopenfilename(filetypes=[
            ("Vidéos", "*.mp4 *.mkv *.avi *.mov *.webm *.ts *.mpg"), ("Tous", "*.*")])
        if p:
            self.pending_open = p

    def toggle(self):
        if self.cap is None:
            return
        self.playing = not self.playing
        self._etat_lecture()

    def _etat_lecture(self):
        self.audio.pause = not self.playing
        self.ref_media, self.ref_wall = self.pos / self.fps, time.time()

    def _seek_rel(self, s):
        if self.cap is not None:
            self.pending_seek = max(0, int(self.pos + s * self.fps))

    def _slider_release(self, _):
        self.dragging = False
        if self.total:
            self.pending_seek = int(self.slider.get() / 1000 * self.total)

    def _refresh(self):
        self._sync()
        img = self.latest
        if img is not None:
            w, h = max(self.video.winfo_width(), 2), max(self.video.winfo_height(), 2)
            ih, iw = img.shape[:2]
            s = min(w / iw, h / ih)
            pil = Image.fromarray(img).resize((max(1, int(iw * s)), max(1, int(ih * s))))
            self.photo = ImageTk.PhotoImage(pil)
            self.video.config(image=self.photo)
        if self.total and not self.dragging:
            self.slider.set(self.pos / self.total * 1000)
        self.lbl_fc.config(text=str(int(self.v_fc.get())))
        if self.cap is not None:
            t = self.pos / self.fps
            son = ("son indisponible (" + str(self.audio.erreur) + ")"
                   if not self.audio.disponible else
                   ("son inversé" if self.v_son.get() else "son normal"))
            self.status.config(text=f"{int(t // 60):02d}:{int(t % 60):02d} | "
                                    f"{self.proc_fps:.1f} i/s | {son}")
            self.btn.config(text="⏸ Pause" if self.playing else "▶ Lecture")
        if self.running:
            self.root.after(15, self._refresh)

    def _close(self):
        self.running = False
        self.audio.fermer()
        self.root.destroy()

    # --------------------------------------------------------------- calcul
    def _traiter(self, frame, idx):
        c = self.cfg
        if c["pal"]:
            frame = sc.vers_pal(frame)
        s, a = c["systeme"], c["action"]
        if s != "Aucun":
            if a == "Coder":
                frame = (sc.syster_coder(frame, c["cle"], idx, c["fenetre"], c["champs"])
                         if s == "Syster" else sc.vc_coder(frame, c["cle"], idx))
            elif a.startswith("Décoder (avec"):
                frame = (sc.syster_decoder(frame, c["cle"], idx, c["fenetre"], c["champs"])
                         if s == "Syster" else sc.vc_decoder(frame, c["cle"], idx))
            else:
                if s == "Syster":
                    self.aveugle_syster.pas = c["pas"]
                    self.aveugle_syster.champs = c["champs"]
                    frame = self.aveugle_syster.traiter(frame)
                else:
                    frame = self.aveugle_vc.traiter(frame)
        if c["flip"]:
            frame = frame[::-1]
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def _reset_aveugles(self):
        self.aveugle_syster.reset()
        self.aveugle_vc.reset()

    def _temps_media(self):
        ta = self.audio.temps()
        now = time.time()
        if ta is not None:
            self.ref_media, self.ref_wall = ta, now
            return ta
        return self.ref_media + (now - self.ref_wall)

    def _worker(self):
        derniere = time.time()
        while self.running:
            if self.pending_open:
                p, self.pending_open = self.pending_open, None
                cap = cv2.VideoCapture(p)
                if cap.isOpened():
                    if self.cap is not None:
                        self.cap.release()
                    self.cap, self.chemin = cap, p
                    self.fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                    self.total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    self.pos = 0
                    self.pending_seek = 0
                    self.playing = True
                    self.root.title("Lecteur Syster / VideoCrypt — " + os.path.basename(p))
                    self.audio.pause = False
            if self.cap is None:
                time.sleep(0.05)
                continue

            if self.pending_seek is not None:
                n, self.pending_seek = self.pending_seek, None
                n = min(n, max(self.total - 1, 0))
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, n)
                self.pos = n
                self._reset_aveugles()
                self.audio.pause = not self.playing
                self.audio.demarrer(self.chemin, n / self.fps)
                self.ref_media, self.ref_wall = n / self.fps, time.time()
                if not self.playing:
                    ok, f = self.cap.read()
                    if ok:
                        self.latest = self._traiter(f, n)
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, n)
                continue

            if not self.playing:
                time.sleep(0.03)
                continue

            cible = int(self._temps_media() * self.fps)
            while self.pos < cible - 1:          # rester en temps réel
                if not self.cap.grab():
                    break
                self.pos += 1
            ok, frame = self.cap.read()
            if not ok:
                self.playing = False
                self.audio.pause = True
                self.pending_seek = 0
                continue
            idx = self.pos
            self.pos += 1
            self.latest = self._traiter(frame, idx)

            now = time.time()
            self.proc_fps = 0.8 * self.proc_fps + 0.2 / max(now - derniere, 1e-6)
            derniere = now
            delai = self.pos / self.fps - self._temps_media()
            if delai > 0:
                time.sleep(min(delai, 0.05))


def main():
    import sys
    root = tk.Tk()
    Lecteur(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
