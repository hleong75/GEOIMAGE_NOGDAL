# GEOIMAGE_NOGDAL

> **Convertisseur IGN SCAN25 → PDF A4 sans GDAL**
>
> Application desktop professionnelle pour convertir des données raster IGN (SCAN25) en PDF A4 imprimable, **sans dépendance GDAL**.

---

## Fonctionnalités

| Fonctionnalité | Détail |
|---|---|
| 🔍 Scan intelligent | Parcours récursif, détection auto `.jp2`, `.tif`, `.ecw`, `.jpeg`, `.png` |
| 🗺️ Reconstruction mosaïque | Basée sur noms de fichiers **ou** sur `mosaique.vrt` (prioritaire) |
| 📐 Géoréférencement | Lecture fichiers `.tab` (MapInfo) et balises GeoTIFF internes |
| 🖨️ PDF A4 exact | DPI configurable (72–1200), portrait/paysage, marges paramétrables |
| 🖥️ Interface graphique | PyQt6, drag & drop, aperçu mosaïque, barre de progression |
| ✂️ Zone de conversion | Sélection rectangulaire directe dans l’aperçu pour exporter seulement une emprise |
| ⚡ Batch industriel | File d'attente multi-dossiers, multi-threading, gestion erreurs |
| 💾 Optimisation mémoire | Rendu tuile par tuile — la mosaïque complète n'est jamais en RAM |
| 🔑 Système de licence | Clé locale HMAC, mode démo (3 exports), activation sans réseau |
| 💻 Mode CLI | Utilisation sans GUI pour l'automatisation |

---

## Stack technique

- **Python 3.10+**
- **PyQt6** — Interface graphique
- **Pillow** — Traitement d'images
- **glymur** — JPEG2000 (`.jp2`)
- **tifffile** — GeoTIFF
- **reportlab** — Génération PDF
- **lxml** — Parsing VRT/XML (optionnel, stdlib `xml.etree` utilisé en fallback)

> ⚠️ **Aucune dépendance GDAL / conda requise.**

---

## Installation

```bash
# Cloner le dépôt
git clone https://github.com/hleong75/GEOIMAGE_NOGDAL.git
cd GEOIMAGE_NOGDAL

# Créer un environnement virtuel
python -m venv .venv
.venv\Scripts\activate      # Windows
# ou
source .venv/bin/activate   # Linux/macOS

# Installer les dépendances
pip install -r requirements.txt
```

> **Note Windows** : Pour le support JPEG2000 complet, installez `openjpeg` système ou utilisez le binaire fourni avec glymur.

---

## Utilisation

### Mode GUI

```bash
python main.py
```

1. **Glissez** un ou plusieurs dossiers IGN dans la zone de prévisualisation, ou utilisez **Fichier → Ouvrir dossier**
2. Configurez DPI, orientation et dossier de sortie dans le panneau droit
3. (Optionnel) tracez un rectangle dans l’aperçu pour limiter l’export à une zone
4. Cliquez **🖨 Convertir en PDF**

### Mode CLI

```bash
python main.py --cli --input /chemin/vers/SCAN25_3-0_XXX --output /dossier/sortie --dpi 300
```

Options :

| Option | Description | Défaut |
|---|---|---|
| `--input` | Dossier source (obligatoire en mode CLI) | — |
| `--output` | Dossier de sortie | Même que `--input` |
| `--dpi` | Résolution (72–1200) | `300` |
| `--landscape` | Orientation paysage | portrait |
| `--margin` | Marges en mm | `10.0` |

### Traitement en lot (GUI)

1. Onglet **Traitement en lot**
2. **+ Ajouter dossier(s)** pour chaque dossier SCAN25
3. **▶ Lancer tout**

---

## Structure du projet

```
GEOIMAGE_NOGDAL/
├── main.py                      # Point d'entrée (GUI + CLI)
├── requirements.txt
├── build.py                     # Script PyInstaller
├── src/
│   ├── core/
│   │   ├── scanner.py           # Scan récursif de fichiers raster
│   │   ├── georef.py            # Lecture .tab, VRT, GeoTIFF
│   │   ├── mosaic.py            # Reconstruction mosaïque (VRT + noms)
│   │   ├── pdf_converter.py     # Export PDF A4 page par page
│   │   ├── batch_processor.py   # File de traitement multi-thread
│   │   └── license.py           # Système de licence HMAC local
│   ├── ui/
│   │   ├── main_window.py       # Fenêtre principale PyQt6
│   │   ├── preview_widget.py    # Aperçu mosaïque avec zoom
│   │   ├── settings_panel.py    # Panneau paramètres
│   │   ├── batch_panel.py       # Panneau traitement en lot
│   │   └── log_widget.py        # Journal coloré
│   └── utils/
│       └── helpers.py           # Utilitaires divers
├── tests/
│   ├── test_scanner.py
│   ├── test_georef.py
│   ├── test_mosaic.py
│   ├── test_license.py
│   └── test_pdf_converter.py
└── assets/                      # Icônes et ressources
```

---

## Build exécutable (.exe)

```bash
pip install pyinstaller
python build.py
```

Le binaire est généré dans `dist/GEOIMAGE_NOGDAL.exe`.

---

## Format des données IGN SCAN25

### Nommage des tuiles

```
SC25_TOUR_XXXX_YYYY_L93_E100.jp2   (ou .tif)
```

- `XXXX` = coordonnée X Lambert 93 (÷ 1000 = km)
- `YYYY` = coordonnée Y Lambert 93 (÷ 1000 = km)

### Fichiers associés

| Extension | Usage |
|---|---|
| `.tab` | Géoréférencement MapInfo (bounding box + points de contrôle) |
| `.vrt` | Mosaïque GDAL Virtual Raster (parsing XML interne) |
| `.md5` | Checksum — **ignoré** |

### Stratégie de reconstruction

1. Si un fichier `mosaique.vrt` est présent → parsing XML (positions exactes)
2. Sinon → extraction des coordonnées XXXX/YYYY depuis les noms de fichiers

---

## Système de licence

| Mode | Exports | Activation |
|---|---|---|
| Démo | 3 exports | Aucune |
| Licencié | Illimité | Clé HMAC locale |

Pour obtenir une clé : **Licence → Informations machine** → transmettez l'identifiant.

---

## Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Architecture (MVC)

```
View  : src/ui/          ← PyQt6 widgets
Model : src/core/        ← Logique métier pure (testable sans GUI)
Entry : main.py          ← Orchestration + CLI
```

---

## Licence

Propriétaire — voir le système de licence intégré.
