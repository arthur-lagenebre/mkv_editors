"""Cache disque des réponses TMDB.

Repasser --verify sur une médiathèque refait chaque appel : une requête par film, par saison, par saga. Les fiches TMDB ne bougeant pas d'un jour à l'autre, les réponses sont gardées quelques jours sur le disque.

Le cache vit HORS de la médiathèque (LOCALAPPDATA sous Windows, ~/.cache ailleurs) : rien ne doit apparaitre à côté des films. La clé d'une entrée ne contient jamais la clé d'API - seulement la langue et le chemin interroge.
"""

import hashlib
import json
import os
import time
from pathlib import Path

TTL = 7 * 24 * 3600        # une semaine : une fiche TMDB ne change pas plus vite
DOSSIER = "mkv_editors"

def default_folder():
    """Emplacement du cache, selon le système."""
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return Path(base) / DOSSIER / "tmdb"


class Cache:
    """Réponses JSON gardées sur le disque, une par fichier.

    Toute panne du cache est sans conséquence : une lecture qui echoue est un défaut de cache, une écriture qui echoue est ignorée. Il ne doit jamais empêcher un script de tourner.
    """

    def __init__(self, folder=None, ttl=TTL, read=True):
        self.folder = Path(folder) if folder else default_folder()
        self.ttl = ttl
        self.read = read            # --no-cache : on n'en lit plus, on le rafraîchit
        self._purged = False

    def _path(self, key):
        empreinte = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.folder / f"{empreinte}.json"

    def get(self, key):
        """Réponse en cache, ou None (absente, périmée, illisible, ou --no-cache)."""
        if not self.read:
            return None
        path = self._path(key)
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            entree = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return entree.get("body") if entree.get("key") == key else None

    def put(self, key, body):
        """Range une réponse. Silencieux en cas d'échec."""
        path = self._path(key)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._purge()
            provisoire = path.with_suffix(".tmp")
            provisoire.write_text(json.dumps({"key": key, "body": body}), encoding="utf-8")
            provisoire.replace(path)      # remplacement atomique
        except (OSError, ValueError, TypeError):
            pass

    def _purge(self):
        """Retire les entrées périmées, une fois par exécution."""
        if self._purged:
            return
        self._purged = True
        limite = time.time() - self.ttl
        try:
            for fichier in self.folder.glob("*.json"):
                if fichier.stat().st_mtime < limite:
                    fichier.unlink(missing_ok=True)
        except OSError:
            pass
