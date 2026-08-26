"""Images TMDB encodées dans une fiche HTML.

Les vignettes voyagent en base64 DANS la page : la fiche est un fichier unique, deplacable et partageable tel quel, sans dossier d'images à côté. La page précédente sert de cache - régénérer ne retélécharge que ce qui a changé.
"""

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .tmdb import TmdbError

MIME = "image/jpeg"
WORKERS = 8

# Une clé d'image = taille TMDB + chemin TMDB (ex. "w300/aBc123.jpg"). Le chemin change dès que l'image change cote TMDB : l'invalidation est automatique.
KEY_RE = re.compile(r"^[\w./-]+$")

# Retrouve les images déjà encodées dans une fiche précédente. 'data-still' est l'ancien nom de l'attribut : les fiches déjà générées restent lisibles.
EMBEDDED_RE = re.compile(r"<img data-(?:img|still)='([^']+)' src='(data:[^']+)'")


def image_key(image_path, size):
    """Clé de cache d'une image TMDB, ou None si le chemin est inattendu."""
    if not image_path:
        return None
    key = f"{size}{image_path}"
    return key if KEY_RE.match(key) else None


def read_embedded(page_path):
    """{clé: data-URI} déjà présents dans une fiche existante."""
    try:
        html = Path(page_path).read_text(encoding="utf-8")
    except OSError:
        return {}
    return dict(EMBEDDED_RE.findall(html))


def tag(key, uri, **attrs):
    """Balise <img> encodée, écrite dans la forme que read_embedded sait relire."""
    extra = "".join(f" {nom}='{valeur}'" for nom, valeur in attrs.items())
    return f"<img data-img='{key}' src='{uri}' alt=''{extra} decoding='async' loading='lazy'>"


def fetch(needed, cached, size, tmdb, label="image"):
    """Résout {clé: chemin TMDB} en {clé: data-URI}.

    Reprend ce que la fiche existante contenait déjà et télécharge le reste en parallèle (une médiathèque = des centaines d'images : en séquentiel, chacune paie son propre aller-retour TLS).
    """
    images = {k: cached[k] for k in needed if k in cached}
    todo = sorted((k, p) for k, p in needed.items() if k not in images)
    if not todo:
        if images:
            print(f"  [recap] {len(images)} {label}(s) reprise(s) de la fiche existante")
        return images

    print(f"  [recap] {len(todo)} {label}(s) a telecharger" + (f", {len(images)} reprise(s) de la fiche existante" if images else ""))

    def grab(item):
        key, path = item
        try:
            raw = tmdb.image(path, size)
        except TmdbError as e:
            print(f"      {label} ignoree ({path}) : {e}")
            return key, None
        return key, f"data:{MIME};base64," + base64.b64encode(raw).decode("ascii")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for key, uri in pool.map(grab, todo):
            if uri:
                images[key] = uri
    return images
