"""Client de l'API MusicBrainz et de Cover Art Archive, pour les scripts de musique.

MusicBrainz est à la musique ce que TMDB est aux films : une base ouverte, sans clé, ou chaque album existe en autant de "sorties" (releases) qu'il a connu d'éditions - le CD français, le vinyle, la réédition de 2021 - regroupées sous un même "release group". Cover Art Archive en garde les pochettes.

Deux règles d'usage, que l'API fait respecter : un User-Agent qui dit qui appelle, et au plus une requête par seconde. Au-delà, elle répond 503 - et en pratique, depuis urllib, elle coupe aussi une connexion sur cinq en pleine négociation TLS. Les deux se réessaient.
"""

import json
import random
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://musicbrainz.org/ws/2"
WEB_BASE = "https://musicbrainz.org"
COVER_ART = "https://coverartarchive.org"
USER_AGENT = "mkv_editors/1.0 ( https://github.com/arthur-lagenebre/mkv_editors )"

# Une requête par seconde, c'est la règle ; mesuré, 1,1 s d'écart entre deux départs passe là où une relance à 1 s déclenchait déjà un 503.
MIN_INTERVAL = 1.1
MAX_RETRY_WAIT = 30
COVER_SIZES = ("250", "500", "1200", "original")

# Caractères qui ont un sens dans la syntaxe de recherche Lucene : "TRON: Legacy" chercherait sinon un champ nommé TRON.
LUCENE_SPECIAL_RE = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def release_url(mbid):
    """Adresse de la fiche d'une sortie sur le site MusicBrainz."""
    return f"{WEB_BASE}/release/{mbid}"


def escape_query(text):
    return LUCENE_SPECIAL_RE.sub(r"\\\1", text or "")


class MusicBrainzError(Exception):
    """Échec ponctuel : l'appelant peut sauter cet album et continuer."""


def _retry_after(headers):
    value = (headers or {}).get("Retry-After")
    if value and str(value).strip().isdigit():
        return min(int(value), MAX_RETRY_WAIT)
    return None


class MusicBrainz:
    def __init__(self, user_agent=USER_AGENT, timeout=30, attempts=4, cache=None, interval=MIN_INTERVAL):
        self.user_agent = user_agent
        self.timeout = timeout
        self.attempts = attempts
        self.cache = cache
        self.interval = interval
        self._last_start = None

    # ------------------------------------------------------------------ HTTP
    def _wait_turn(self):
        """Espace les requêtes à l'API. Une réponse servie par le cache ne passe pas par ici, et ne coûte donc rien."""
        if self._last_start is not None:
            wait = self._last_start + self.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last_start = time.monotonic()

    def _request(self, url, accept, throttle=True):
        """GET brut avec réessais. Retourne (octets, en-têtes). Chaque tentative compte pour la limite : une connexion coupée a été vue par le serveur."""
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        for attempt in range(1, self.attempts + 1):
            if throttle:
                self._wait_turn()
            wait = None
            try:
                with urlopen(Request(url, headers=headers), timeout=self.timeout) as r:
                    return r.read(), r.headers
            except HTTPError as e:            # sous-classe d'URLError : à rattraper avant
                if e.code == 404:
                    raise MusicBrainzError("introuvable (HTTP 404)") from e
                if e.code not in (429, 503) and e.code < 500:
                    raise MusicBrainzError(f"HTTP {e.code} {e.reason}") from e
                failure, wait = e, _retry_after(e.headers)
            except (URLError, OSError) as e:  # réseau, DNS, timeout, connexion coupée
                failure = e
            if attempt == self.attempts:
                raise MusicBrainzError(str(failure)) from failure
            time.sleep(wait if wait is not None else 2 ** attempt + random.uniform(0, 0.5))

    def get(self, endpoint):
        """GET sur un endpoint de l'API, décodé en JSON. Passe par le cache s'il y en a un."""
        if self.cache is not None:
            kept = self.cache.get(endpoint)
            if kept is not None:
                return kept
        sep = "&" if "?" in endpoint else "?"
        body, _ = self._request(f"{API}/{endpoint}{sep}fmt=json", "application/json")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise MusicBrainzError(f"reponse MusicBrainz illisible : {e}") from e
        if self.cache is not None:
            self.cache.put(endpoint, data)
        return data

    # --------------------------------------------------------------- Sorties
    def search_releases(self, album, artist=None, exact=True, limit=25):
        """Sorties dont le titre (et l'artiste) correspondent.

        `exact` cherche les expressions entières ; sans, chaque mot compte à part - le repli pour un titre écrit autrement que dans la base ("Violent Demise - The Last Days" ne rend rien en expression).
        """
        def term(field, text):
            if exact:
                return f'{field}:"{text.replace(chr(92), " ").replace(chr(34), " ")}"'
            return f"{field}:({escape_query(text)})"

        query = term("release", album) + (f" AND {term('artist', artist)}" if artist else "")
        return self.get(f"release/?query={quote(query, safe='')}&limit={limit}").get("releases", [])

    def release(self, mbid):
        """Sortie complète : disques, pistes, enregistrements, artistes, labels, release group."""
        return self.get(f"release/{mbid}?inc=recordings+artist-credits+labels+release-groups+genres")

    def release_group(self, mbid):
        """Release group et ses genres - c'est à ce niveau que la communauté les vote."""
        return self.get(f"release-group/{mbid}?inc=genres")

    # --------------------------------------------------------------- Pochettes
    def _image(self, url):
        body, headers = self._request(url, "image/*", throttle=False)
        announced = headers.get("Content-Length")
        if announced and str(announced).isdigit() and len(body) != int(announced):
            raise MusicBrainzError(f"telechargement incomplet ({len(body)}/{announced} octets)")
        return body

    def cover(self, release_id, release_group_id=None, size="1200"):
        """Octets de la pochette (recto) d'une sortie, ou None si Cover Art Archive n'en a pas.

        Faute d'image pour l'édition précise, celle du release group fait l'affaire : c'est le même album. Sans `release_id` - la fiche de la sortie dit déjà qu'elle n'en a pas - seul le release group est demandé. Cover Art Archive n'est pas soumis à la limite d'une requête par seconde.
        """
        name = "front" if size == "original" else f"front-{size}"
        targets = [f"{COVER_ART}/release/{release_id}/{name}"] if release_id else []
        if release_group_id:
            targets.append(f"{COVER_ART}/release-group/{release_group_id}/{name}")
        for url in targets:
            try:
                return self._image(url)
            except MusicBrainzError as e:
                if "404" not in str(e):
                    raise
        return None
