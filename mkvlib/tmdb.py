"""Client de l'API TMDB v3, partage par tous les scripts.

Gère les deux formes de clé (clé v3 en paramètre, token v4 en Bearer), distingue les échecs définitifs des échecs temporaires, et réessaie ces derniers : une série un peu longue représente des centaines de requêtes (une par saison, plus une par vignette du récap), assez pour croiser un 429 ou une coupure réseau passagère.
"""

import json
import random
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/"
WEB_BASE = "https://www.themoviedb.org"


def movie_url(movie_id):
    """Adresse de la fiche d'un film sur le site TMDB."""
    return f"{WEB_BASE}/movie/{movie_id}"

# Ordre de préférence des types de sortie TMDB : théâtrale > théâtrale limitée > première > numérique > physique > TV.
RELEASE_TYPE_ORDER = (3, 2, 1, 4, 5, 6)
MAX_RETRY_WAIT = 30        # secondes : plafond du Retry-After renvoyé par TMDB

class TmdbError(Exception):
    """Échec ponctuel : l'appelant peut sauter cet élément et continuer."""

class TmdbAuthError(Exception):
    """Clé refusée (401/403) : rien ne fonctionnera, autant s'arrêter tout de suite.

    Volontairement hors de TmdbError : les scripts rattrapent TmdbError élément par élément, et une clé invalide ne doit pas se transformer en une cascade de "saison ignorée" sans jamais dire pourquoi.
    """

def release_region(language):
    """'fr-FR' -> 'FR'. None si la langue ne precise aucun pays."""
    part = language.split("-")[-1].upper()
    return part if len(part) == 2 and part != language.upper() else None


class Tmdb:
    def __init__(self, key, language="fr-FR", user_agent="mkv_editors/1.0", timeout=30, attempts=3, cache=None):
        self.key = key
        self.language = language
        self.user_agent = user_agent
        self.timeout = timeout
        self.attempts = attempts
        self.cache = cache

    # ------------------------------------------------------------------ HTTP
    @staticmethod
    def _retry_after(headers):
        """Délai demande par TMDB dans l'en-tête Retry-After, si exploitable."""
        value = (headers or {}).get("Retry-After")
        if value and str(value).strip().isdigit():
            return min(int(value), MAX_RETRY_WAIT)
        return None

    def _request(self, url, headers):
        """GET brut avec réessais. Retourne (octets, en-têtes)."""
        for attempt in range(1, self.attempts + 1):
            wait = None
            try:
                with urlopen(Request(url, headers=headers), timeout=self.timeout) as r:
                    return r.read(), r.headers
            except HTTPError as e:            # sous-classe d'URLError : à rattraper avant
                if e.code in (401, 403):
                    raise TmdbAuthError(f"cle TMDB refusee par l'API (HTTP {e.code}). Verifie la ligne TMDB_KEY=... de ton fichier .env.") from e
                if e.code == 404:
                    raise TmdbError("introuvable sur TMDB (HTTP 404)") from e
                if e.code != 429 and e.code < 500:
                    raise TmdbError(f"HTTP {e.code} {e.reason}") from e
                failure, wait = e, self._retry_after(e.headers)   # 429 / panne serveur
            except (URLError, OSError) as e:                      # réseau, DNS, timeout
                failure = e
            if attempt == self.attempts:
                raise TmdbError(str(failure)) from failure
            # Attente exponentielle + bruit : les vignettes du récap partent à 8 threads, sans bruit elles reviendraient toutes taper au même instant.
            time.sleep(wait if wait is not None else 2 ** (attempt - 1) + random.uniform(0, 0.5))

    def get(self, endpoint, language=None):
        """GET sur un endpoint de l'API, decode en JSON. Passe par le cache s'il y en a un."""
        langue = language or self.language
        # La clé de cache ne retient que la langue et le chemin : jamais la clé d'API.
        cle = f"{langue}:{endpoint}"
        if self.cache is not None:
            garde = self.cache.get(cle)
            if garde is not None:
                return garde

        url = f"{API}/{endpoint}"
        sep = "&" if "?" in url else "?"
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.key.startswith("ey") and self.key.count(".") == 2:   # token de lecture v4
            headers["Authorization"] = f"Bearer {self.key}"
            url += f"{sep}language={langue}"
        else:                                                        # clé API v3
            url += f"{sep}api_key={self.key}&language={langue}"
        body, _ = self._request(url, headers)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise TmdbError(f"reponse TMDB illisible : {e}") from e
        if self.cache is not None:
            self.cache.put(cle, data)
        return data

    # ----------------------------------------------------------------- Films
    def search_movie(self, title, year=None):
        endpoint = f"search/movie?query={quote(title)}"
        if year:
            endpoint += f"&year={year}"
        return self.get(endpoint).get("results", [])

    def search_collection(self, name):
        """Collections TMDB portant ce nom - une saga se cherche comme un film."""
        return self.get(f"search/collection?query={quote(name)}").get("results", [])

    def movie(self, movie_id, language=None):
        """Détails du film + crédits (réalisateur, scénaristes, casting)."""
        return self.get(f"movie/{movie_id}?append_to_response=credits", language)

    def local_release_date(self, movie_id, region):
        """Date de sortie du film dans `region` (ex. 'FR'), via /release_dates.

        Le champ `release_date` des détails renvoie TOUJOURS la sortie d'origine (souvent américaine), même interroge en fr-FR : il faut cet endpoint pour obtenir la sortie nationale. On retient le type le plus pertinent (voir RELEASE_TYPE_ORDER) et, à type égal, la date la plus ancienne — sinon une ressortie en salles prendrait le pas sur la sortie initiale. Retourne None si le pays est absent ou en cas d'échec réseau.
        """
        try:
            results = self.get(f"movie/{movie_id}/release_dates").get("results", [])
        except TmdbError:
            return None
        dates = next((r.get("release_dates", []) for r in results if r.get("iso_3166_1") == region), [])
        for wanted in RELEASE_TYPE_ORDER:
            same = sorted(d["release_date"][:10] for d in dates if d.get("type") == wanted and d.get("release_date"))
            if same:
                return same[0]
        return None

    def collection(self, collection_id, language=None):
        """Composition d'une saga : tous ses films, même ceux qu'on ne possède pas."""
        return self.get(f"collection/{collection_id}", language)

    # ---------------------------------------------------------------- Séries
    def search_tv(self, name, year=None):
        endpoint = f"search/tv?query={quote(name)}"
        if year:
            endpoint += f"&first_air_date_year={year}"
        return self.get(endpoint).get("results", [])

    def series(self, show_id, language=None):
        """Détails de la série (nom, synopsis, poster, date de première diffusion...)."""
        return self.get(f"tv/{show_id}", language)

    def season(self, show_id, season_number, language=None):
        """Données d'une saison, MÊME structure qu'un export JSON TMDB."""
        return self.get(f"tv/{show_id}/season/{season_number}", language)

    # ---------------------------------------------------------------- Images
    def image(self, image_path, size):
        """Télécharge une image TMDB et retourne ses octets.

        Compare la taille reçue à l'en-tête Content-Length : un flux coupé en cours de route leve une erreur au lieu de produire silencieusement un JPEG tronque — embarque tel quel dans le .mkv, il y resterait.
        """
        body, headers = self._request(f"{IMG_BASE}{size}{image_path}", {"User-Agent": self.user_agent})
        announced = headers.get("Content-Length")
        if announced and str(announced).isdigit() and len(body) != int(announced):
            raise TmdbError(f"telechargement incomplet ({len(body)}/{announced} octets)")
        return body

    def save_image(self, image_path, size, dest):
        """Écrit une image TMDB sur le disque (jaquette embarquee, folder.jpg)."""
        Path(dest).write_bytes(self.image(image_path, size))
