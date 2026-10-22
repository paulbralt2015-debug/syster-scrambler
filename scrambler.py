"""Émulation du brouillage vidéo de type Syster (mélange de lignes) et
VideoCrypt (coupe-et-rotation de lignes), avec décodage par clé et décodage aveugle.

IMPORTANT : ce sont des ÉMULATIONS. Les paramètres exacts des systèmes d'origine
(taille de fenêtre, générateur pseudo-aléatoire, mots de contrôle) n'ont jamais été
publiés officiellement. Ce code est compatible avec lui-même, pas avec un vrai
décodeur Canal+ / Sky.
"""
import hashlib
from collections import deque

import cv2
import numpy as np

# Raster numérique PAL / SECAM : 625 lignes au total, 576 actives, 25 images/s
# (50 trames entrelacées de 288 lignes). PAL et SECAM ont la même structure de
# lignes ; ils ne diffèrent que par le codage de la couleur, ce qui n'a aucune
# incidence sur une manipulation de lignes faite sur des images décodées.
PAL_W, PAL_H, PAL_FPS = 720, 576, 25.0


def vers_pal(frame):
    """Met l'image au format 720x576 en conservant les proportions (bandes noires)."""
    h, w = frame.shape[:2]
    if (w, h) == (PAL_W, PAL_H):
        return frame
    s = min(PAL_W / w, PAL_H / h)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR
    small = cv2.resize(frame, (nw, nh), interpolation=interp)
    out = np.zeros((PAL_H, PAL_W, 3), np.uint8)
    y0, x0 = (PAL_H - nh) // 2, (PAL_W - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = small
    return out


def _graine(cle, idx, sel):
    h = hashlib.blake2b(f"{cle}|{idx}|{sel}".encode(), digest_size=8).digest()
    return int.from_bytes(h, "little")


# ---------------------------------------------------------------- Syster
def _perm_blocs(n, fenetre, rng):
    p = np.empty(n, dtype=np.int64)
    for s in range(0, n, fenetre):
        e = min(s + fenetre, n)
        p[s:e] = s + rng.permutation(e - s)
    return p


def syster_perm(h, cle, idx, fenetre=32, champs=True):
    """Permutation des lignes : mélange dans des fenêtres de `fenetre` lignes,
    séparément pour chaque trame (lignes paires / impaires) si champs=True."""
    if champs:
        perm = np.arange(h)
        for f in (0, 1):
            n = len(range(f, h, 2))
            rng = np.random.default_rng(_graine(cle, idx, f))
            perm[f::2] = f + 2 * _perm_blocs(n, fenetre, rng)
        return perm
    return _perm_blocs(h, fenetre, np.random.default_rng(_graine(cle, idx, 9)))


def syster_coder(frame, cle, idx, fenetre=32, champs=True):
    return frame[syster_perm(frame.shape[0], cle, idx, fenetre, champs)]


def syster_decoder(frame, cle, idx, fenetre=32, champs=True):
    perm = syster_perm(frame.shape[0], cle, idx, fenetre, champs)
    return frame[np.argsort(perm)]


# ------------------------------------------------------------- VideoCrypt
def vc_coupes(h, w, cle, idx):
    """Point de coupe de chaque ligne : 256 positions possibles."""
    rng = np.random.default_rng(_graine(cle, idx, "vc"))
    return rng.integers(0, 256, size=h) * w // 256


def vc_coder(frame, cle, idx):
    h, w = frame.shape[:2]
    c = vc_coupes(h, w, cle, idx)
    cols = (np.arange(w)[None, :] + c[:, None]) % w
    return np.take_along_axis(frame, cols[:, :, None], axis=1)


def vc_decoder(frame, cle, idx):
    h, w = frame.shape[:2]
    c = vc_coupes(h, w, cle, idx)
    cols = (np.arange(w)[None, :] - c[:, None]) % w
    return np.take_along_axis(frame, cols[:, :, None], axis=1)


# ------------------------------------------------------- décodage aveugle
def ordonner_lignes(gray, k=8, pas=8):
    """Retrouve l'ordre des lignes par continuité de l'image (sans clé)."""
    from scipy.spatial.distance import cdist
    H = gray.shape[0]
    g = gray[:, ::pas].astype(np.float32)
    D = cdist(g, g, 'cityblock') / g.shape[1]
    np.fill_diagonal(D, np.inf)
    nn = np.argsort(D, axis=1)[:, :k]
    aretes = sorted({(min(i, j), max(i, j)) for i in range(H) for j in nn[i]},
                    key=lambda e: D[e])
    parent = list(range(H))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    deg = np.zeros(H, int)
    adj = [[] for _ in range(H)]

    def lier(a, b):
        adj[a].append(b); adj[b].append(a)
        deg[a] += 1; deg[b] += 1
        parent[find(a)] = find(b)

    for a, b in aretes:
        if deg[a] < 2 and deg[b] < 2 and find(a) != find(b):
            lier(a, b)
    while True:
        bouts = np.array([n for n in range(H) if deg[n] < 2])
        comp = np.array([find(n) for n in bouts])
        if len(set(comp)) == 1:
            break
        sub = D[np.ix_(bouts, bouts)].copy()
        sub[comp[:, None] == comp[None, :]] = np.inf
        a, b = np.unravel_index(np.argmin(sub), sub.shape)
        lier(int(bouts[a]), int(bouts[b]))
    debut = next(n for n in range(H) if deg[n] < 2)
    ordre, prec, cur = [debut], -1, debut
    while len(ordre) < H:
        nxt = [n for n in adj[cur] if n != prec]
        if not nxt:
            break
        prec, cur = cur, nxt[0]
        ordre.append(cur)
    return np.array(ordre)


class AveugleSyster:
    """Remet les lignes en ordre sans clé, trame par trame."""

    def __init__(self, pas=8, champs=True):
        self.pas, self.champs = pas, champs
        self.prev = {}

    def reset(self):
        self.prev = {}

    def _un(self, part, cle):
        gray = cv2.cvtColor(part, cv2.COLOR_BGR2GRAY)
        ordre = ordonner_lignes(gray, pas=self.pas)
        small = gray[:, ::self.pas].astype(np.int16)
        p = self.prev.get(cle)
        if p is not None and p.shape == small.shape:
            a = np.abs(small[ordre] - p).mean()
            b = np.abs(small[ordre[::-1]] - p).mean()
            if b < a:
                ordre = ordre[::-1]
        self.prev[cle] = small[ordre]
        return part[ordre]

    def traiter(self, frame):
        if not self.champs:
            return self._un(frame, 0)
        out = np.empty_like(frame)
        for f in (0, 1):
            out[f::2] = self._un(frame[f::2], f)
        return out


class AveugleVideoCrypt:
    """Retrouve le point de coupe de chaque ligne par corrélation avec les
    lignes précédentes déjà corrigées. Le décalage horizontal global est
    inconnu : on l'ancre sur l'image précédente, et `decalage` permet un réglage manuel."""

    def __init__(self):
        self.ancre = None
        self.decalage = 0

    def reset(self):
        self.ancre = None

    def traiter(self, frame):
        H, W = frame.shape[:2]
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g -= g.mean(axis=1, keepdims=True)
        F = np.fft.rfft(g, axis=1)
        f = np.arange(F.shape[1])
        ks = np.zeros(H, dtype=int)
        recent = deque(maxlen=1)  # la ligne précédente seule : plus fiable qu'une moyenne

        if self.ancre is not None:
            c = np.fft.irfft(np.conj(self.ancre) * F[0], n=W)
            ks[0] = int(np.argmax(c))
        corr0 = F[0] * np.exp(2j * np.pi * f * ks[0] / W)
        recent.append(corr0)
        for i in range(1, H):
            ref = sum(recent) / len(recent)
            c = np.fft.irfft(np.conj(ref) * F[i], n=W)
            k = int(np.argmax(c))
            ks[i] = k
            recent.append(F[i] * np.exp(2j * np.pi * f * k / W))
        self.ancre = corr0
        cols = (np.arange(W)[None, :] + ks[:, None] + self.decalage) % W
        return np.take_along_axis(frame, cols[:, :, None], axis=1)
