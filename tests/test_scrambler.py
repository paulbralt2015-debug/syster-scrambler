"""Tests automatiques : python tests/test_scrambler.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cv2, numpy as np
from scrambler import *
from audio import InverseurSpectre, SR


def image(h=576, w=720, graine=1):
    rng = np.random.default_rng(graine)
    n = rng.random((h // 8 + 2, w // 8 + 2, 3)).astype(np.float32)
    img = cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
    img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    cv2.circle(img, (w // 2, h // 2), 120, (255, 255, 255), -1)
    cv2.rectangle(img, (50, 50), (250, 200), (0, 0, 0), -1)
    img = cv2.GaussianBlur(img, (5, 5), 0)
    return np.clip(img.astype(int) + rng.normal(0, 2, img.shape), 0, 255).astype(np.uint8)


def test_roundtrip_cle():
    img = image()
    for champs in (True, False):
        for fen in (8, 32, 100):
            s = syster_coder(img, "cle", 7, fen, champs)
            assert not np.array_equal(s, img)
            assert np.array_equal(syster_decoder(s, "cle", 7, fen, champs), img)
    s = vc_coder(img, "cle", 3)
    assert not np.array_equal(s, img)
    assert np.array_equal(vc_decoder(s, "cle", 3), img)
    assert not np.array_equal(vc_decoder(s, "autre", 3), img)
    print("aller-retour avec clé : OK")


def test_syster_aveugle():
    img = image()
    for champs in (True, False):
        s = syster_coder(img, "k", 1, 32, champs)
        d = AveugleSyster(champs=champs)
        r = d.traiter(s)
        e = min(np.abs(r.astype(int) - img).mean(), np.abs(r[::-1].astype(int) - img).mean())
        print(f"Syster aveugle champs={champs} : erreur moyenne {e:.2f}")


def test_vc_aveugle():
    img = image()
    d = AveugleVideoCrypt()
    for i in range(3):
        s = vc_coder(img, "k", i)
        t = time.time(); r = d.traiter(s); dt = time.time() - t
        # compare en autorisant un décalage circulaire global
        best = min(np.abs(np.roll(r, sh, axis=1).astype(int) - img).mean() for sh in range(0, 720, 1))
        print(f"VideoCrypt aveugle image {i} : erreur {best:.2f}  ({dt*1000:.0f} ms)")


def test_audio():
    t = np.arange(SR * 2) / SR
    x = (0.4 * np.sin(2 * np.pi * 500 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t))[None, :].repeat(2, 0)
    a = InverseurSpectre(6800)
    y = np.concatenate([a.traiter(x[:, i:i + 1024]) for i in range(0, x.shape[1], 1024)], axis=1)
    spec = np.abs(np.fft.rfft(y[0, SR // 2:])); fr = np.fft.rfftfreq(len(y[0, SR // 2:]), 1 / SR)
    pics = sorted(fr[np.argsort(spec)[-2:]])
    print("pics après inversion (attendu 5300 et 6300) :", [round(p) for p in pics])
    b = InverseurSpectre(6800)
    z = np.concatenate([b.traiter(y[:, i:i + 1024]) for i in range(0, y.shape[1], 1024)], axis=1)
    spec = np.abs(np.fft.rfft(z[0, SR // 2:])); pics = sorted(fr[np.argsort(spec)[-2:]])
    print("pics après 2e inversion (attendu 500 et 1500) :", [round(p) for p in pics])
    c = np.corrcoef(x[0, SR // 2:], z[0, SR // 2:])[0, 1]
    print("corrélation avec l'original :", round(abs(c), 3))


if __name__ == "__main__":
    test_roundtrip_cle(); test_syster_aveugle(); test_vc_aveugle(); test_audio()
