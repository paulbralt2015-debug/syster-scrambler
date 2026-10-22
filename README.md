# Lecteur Syster / VideoCrypt

Lecteur vidéo qui **code et décode en temps réel** (émulation) :
- le brouillage de type **Syster / Nagravision** : mélange de lignes par fenêtres, par trame ;
- le brouillage de type **VideoCrypt** : coupe-et-rotation de chaque ligne (256 positions) ;
- le **son** par inversion de spectre (fc réglable) ;
- le raster **PAL / SECAM** : 720x576, 25 i/s, trames de 288 lignes.

## Lancer
    pip install -r requirements.txt
    python player.py [fichier.mp4]

## Modes
| Action | Fonction |
|---|---|
| Coder | brouille la vidéo avec la clé |
| Décoder (avec la clé) | inverse exactement un brouillage produit par ce lecteur (même clé, même fenêtre) |
| Décoder (aveugle) | reconstitue l'image sans clé par analyse de continuité (résultat partiel sur vidéos recompressées) |

Touches : Espace lecture/pause, S son inversé, F haut/bas, ←/→ ±5 s,
Page haut / Page bas : recaler horizontalement le décodage aveugle VideoCrypt.

## Important
- Ce sont des **émulations** : les paramètres des vrais systèmes ne sont pas publics.
  Les vidéos codées ici ne se décodent pas avec un vrai décodeur, et inversement.
- PAL et SECAM ont la même structure de lignes (625 lignes, 25 i/s) ; ils ne diffèrent que
  par la couleur, sans effet sur ce traitement. Une seule option « PAL / SECAM » est donc proposée.
- Le son passe par ffmpeg (fourni par `imageio-ffmpeg`) et `sounddevice`.
- Tests : `python tests/test_scrambler.py`

## Avertissement
À utiliser sur vos propres vidéos ou des enregistrements dont vous détenez les droits.
Ne publiez aucune vidéo dans ce dépôt.
