#!/usr/bin/env python3
"""
Movies.py — Etiquette des films .mkv a partir de TMDB (donnees en francais via l'API).

Meme principe que TV_Shows.py, mais pour les films : pas de saisons/episodes, et
l'association se fait par RECHERCHE TMDB sur le titre + l'annee extraits du nom.

Ecrit DIRECTEMENT dans chaque .mkv (sans re-encodage ni remux) :
  - le titre et la DATE de sortie dans les informations de segment
    (sortie du pays de --language : fr-FR -> sortie francaise, pas la sortie d'origine)
  - le synopsis, le realisateur, les scenaristes, le casting, les genres (tags)
  - les tags de STATISTIQUES de piste (debit, duree, nb d'images)  [--no-stats]
  - l'affiche du film comme jaquette (attachment "cover.jpg")
  - le nom des pistes AUDIO       -> codec + canaux + debit (ex. "E-AC-3 5.1 640 kb/s")
  - le nom des pistes SOUS-TITRES -> uniquement les drapeaux actifs (Forced, SDH...), ou "Full"
  - les DRAPEAUX 'par defaut'     -> une seule piste audio par defaut (la FR), aucun sous-titre

Dependances EXTERNES (dans le PATH) : mkvpropedit + mkvmerge (MKVToolNix), ffprobe (FFmpeg).
Aucune dependance pip. Necessite Internet (API TMDB + jaquettes).

Cle TMDB (par priorite) : fichier .env a la racine du depot (TMDB_KEY=...)  >  variable
d'env TMDB_API_KEY  >  constante TMDB_KEY.
(Le meme .env sert a tous les scripts du depot : la cle n'est ecrite qu'une fois.)

Structure attendue : soit un sous-dossier par film (les .mkv dedans), soit des .mkv a plat
dans --dir. Le titre et l'annee sont lus dans le nom (dossier ou fichier), ex. "Inception (2010)".
Un prefixe d'ordre de saga "{n} - " est detecte et retire pour la recherche ("1 - Iron Man"
-> recherche "Iron Man") ; l'ordre est inscrit comme numero dans la collection (tag PART_NUMBER).

Usage :
  python Metadata.py --dir "D:\\Films"                         # simulation (n'ecrit rien)
  python Metadata.py --dir "D:\\Films" --apply                 # applique
  python Metadata.py --dir "D:\\Films\\Inception (2010)" --tmdb-id 27205 --apply   # force l'id (1 film)
  python Metadata.py --dir "D:\\Films" --verify                # verifie seulement

Options : --apply --verify --skip-done --artwork
          --no-cover --no-date --no-audio-names --no-sub-names --no-flags --no-stats
          --tmdb-id (force, si un seul film) --language (defaut fr-FR) --image-size (w780)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape
from urllib.parse import quote
from urllib.request import urlopen, Request
from urllib.error import URLError

# ============================================================================
# Cle API TMDB : colle-la ici entre les guillemets pour ne plus avoir a la
# retaper. Priorite : .env > env TMDB_API_KEY > ceci.
TMDB_KEY = ""
# ============================================================================

def load_dotenv(filename=".env"):
    """Charge un fichier .env (lignes CLE=valeur) dans les variables d'environnement.

    Le fichier est cherche en remontant depuis le dossier du script, puis depuis le
    dossier courant ; on s'arrete au premier trouve. Les variables deja definies
    dans l'environnement ne sont jamais ecrasees. Aucune dependance pip.
    """
    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for folder in (start, *start.parents):
            path = folder / filename
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                name, value = name.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if name:
                    os.environ.setdefault(name, value)
            return path
    return None


TMDB_IMG_BASE = "https://image.tmdb.org/t/p/"
TMDB_API = "https://api.themoviedb.org/3"
ARTWORK_LANG = "en-US"     # affiche (folder.jpg) recuperee en anglais

# Tokens de "release" a retirer du nom avant la recherche.
QUALITY_RE = re.compile(
    r"\b(1080p|2160p|4k|720p|480p|x265|x264|h ?265|h ?264|hevc|avc|aac|ac3|eac3|dts|truehd|"
    r"web[- ]?dl|web[- ]?rip|blu[- ]?ray|bdrip|brrip|hdrip|dvdrip|remux|multi|truefrench|"
    r"vff|vfi|vfq|vf2|vf|vostfr|vo|hdr10?|10bit|8bit|dolby|atmos|imax|extended|remastered)\b",
    re.IGNORECASE)


# ----------------------------------------------------------------------------
# 1. Detection des films sur le disque + lecture titre/annee
# ----------------------------------------------------------------------------
def find_movies(root):
    """(films, foldered). films = [(dossier, mkv_principal, nom_brut), ...].
    - Sous-dossiers contenant des .mkv -> un film par dossier (nom = dossier).
    - Sinon, chaque .mkv de --dir -> un film (nom = fichier)."""
    root = Path(root)
    if not root.is_dir():
        return [], False
    foldered = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        mkvs = sorted(sub.glob("*.mkv"))
        if mkvs:
            main = max(mkvs, key=lambda f: f.stat().st_size)   # le plus gros = le film
            foldered.append((sub, main, sub.name))
    if foldered:
        return foldered, True
    return [(root, f, f.stem) for f in sorted(root.glob("*.mkv"))], False


ORDER_RE = re.compile(r"^\s*(\d{1,3})\s*[-\u2013\u2014]\s+")   # "1 - ", "01 - " (tiret obligatoire)


def parse_title_year(name):
    """'Inception (2010)' -> ('Inception', '2010', None). Annee entre () prioritaire.
    Un prefixe d'ordre de saga '{n} - ' est detecte et retire ('1 - Iron Man' -> ordre 1)."""
    order = None
    mo = ORDER_RE.match(name)
    if mo:                                 # prefixe d'ordre "{n} - " -> retire du titre
        order = int(mo.group(1))
        name = name[mo.end():]
    mb = re.search(r"[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]", name)
    if mb:                                 # annee entre parentheses/crochets = la bonne
        year, s = mb.group(1), name[:mb.start()]
    else:
        bare = list(re.finditer(r"(?<!\d)(19|20)\d{2}(?!\d)", name))
        if bare:                           # sinon, derniere annee "nue"
            year, s = bare[-1].group(0), name[:bare[-1].start()]
        else:
            year, s = None, name
    s = re.sub(r"[._]", " ", s)
    s = re.sub(r"[\(\)\[\]{}]", " ", s)
    s = QUALITY_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s, year, order


# ----------------------------------------------------------------------------
# 2. Source TMDB
# ----------------------------------------------------------------------------
def _tmdb_get(endpoint, key, language):
    """GET sur l'API TMDB v3. Cle v3 (hex) -> parametre api_key ; token v4 (JWT) -> Bearer."""
    url = f"{TMDB_API}/{endpoint}"
    sep = "&" if "?" in url else "?"
    headers = {"User-Agent": "movies_mkv/1.0", "Accept": "application/json"}
    if key.startswith("ey") and key.count(".") == 2:
        headers["Authorization"] = f"Bearer {key}"
        url += f"{sep}language={language}"
    else:
        url += f"{sep}api_key={key}&language={language}"
    with urlopen(Request(url, headers=headers), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def tmdb_search_movie(title, year, key, language):
    endpoint = f"search/movie?query={quote(title)}"
    if year:
        endpoint += f"&year={year}"
    return _tmdb_get(endpoint, key, language).get("results", [])


def tmdb_movie(movie_id, key, language):
    """Details du film + credits (realisateur, scenaristes, casting)."""
    return _tmdb_get(f"movie/{movie_id}?append_to_response=credits", key, language)


# Ordre de preference des types de sortie TMDB :
# theatrale > theatrale limitee > premiere > numerique > physique > TV.
RELEASE_TYPE_ORDER = (3, 2, 1, 4, 5, 6)


def release_region(language):
    """'fr-FR' -> 'FR'. None si la langue ne precise aucun pays."""
    part = language.split("-")[-1].upper()
    return part if len(part) == 2 and part != language.upper() else None


def tmdb_release_date(movie_id, key, language, region):
    """Date de sortie du film dans `region` (ex. 'FR'), via /release_dates.

    Le champ `release_date` des details renvoie TOUJOURS la sortie d'origine
    (souvent americaine), meme interroge en fr-FR : il faut cet endpoint pour
    obtenir la sortie nationale. On retient le type le plus pertinent (voir
    RELEASE_TYPE_ORDER) et, a type egal, la date la plus ancienne — sinon une
    ressortie en salles prendrait le pas sur la sortie initiale.
    Retourne None si le pays est absent ou en cas d'echec reseau.
    """
    try:
        results = _tmdb_get(f"movie/{movie_id}/release_dates", key, language).get("results", [])
    except (URLError, OSError):
        return None
    dates = next((r.get("release_dates", []) for r in results
                  if r.get("iso_3166_1") == region), [])
    for wanted in RELEASE_TYPE_ORDER:
        same = sorted(d["release_date"][:10] for d in dates
                      if d.get("type") == wanted and d.get("release_date"))
        if same:
            return same[0]
    return None


# ----------------------------------------------------------------------------
# 3. Construction des tags Matroska (TargetTypeValue 50 = film, 70 = collection)
# ----------------------------------------------------------------------------
def _unique(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def build_movie_tags_xml(movie, max_actors=20):
    credits = movie.get("credits", {})
    directors = _unique(c.get("name") for c in credits.get("crew", []) if c.get("job") == "Director")
    writers = _unique(c.get("name") for c in credits.get("crew", []) if c.get("department") == "Writing")
    actors = []
    for a in credits.get("cast", [])[:max_actors]:
        name, char = a.get("name", ""), a.get("character", "")
        actors.append(f"{name} ({char})" if char else name)
    genres = ", ".join(g.get("name", "") for g in movie.get("genres", []))

    def simple(name, value):
        return f"      <Simple><Name>{escape(name)}</Name><String>{escape(str(value))}</String></Simple>"

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Tags>"]
    coll = (movie.get("belongs_to_collection") or {}).get("name")
    if coll:
        coll_tag = ["  <Tag>", "    <Targets><TargetTypeValue>70</TargetTypeValue></Targets>",
                    simple("TITLE", coll)]
        if movie.get("_order"):                       # ordre de la saga (prefixe "{n} - ")
            coll_tag.append(simple("PART_NUMBER", movie["_order"]))
        coll_tag.append("  </Tag>")
        lines += coll_tag
    tag = ["  <Tag>", "    <Targets><TargetTypeValue>50</TargetTypeValue></Targets>",
           simple("TITLE", movie.get("title", ""))]
    if movie.get("overview"):
        tag.append(simple("SYNOPSIS", movie["overview"]))
        tag.append(simple("SUMMARY", movie["overview"]))
    if movie.get("release_date"):
        tag.append(simple("DATE_RELEASED", movie["release_date"]))
    for d in directors:
        tag.append(simple("DIRECTOR", d))
    for w in writers:
        tag.append(simple("WRITTEN_BY", w))
    for a in actors:
        tag.append(simple("ACTOR", a))
    if genres:
        tag.append(simple("GENRE", genres))
    tag.append("  </Tag>")
    lines += tag
    lines.append("</Tags>")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 4. Lecture / ecriture .mkv  (helpers identiques a TV_Shows.py)
# ----------------------------------------------------------------------------
def mkv_identify(path):
    try:
        return json.loads(subprocess.run(["mkvmerge", "-J", str(path)],
                                         capture_output=True, text=True, check=True).stdout)
    except Exception:
        return None


def download_cover(image_path, size, dest):
    url = f"{TMDB_IMG_BASE}{size}{image_path}"
    req = Request(url, headers={"User-Agent": "movies_mkv/1.0"})
    with urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


CHANNELS = {1: "1.0", 2: "2.0", 3: "2.1", 4: "4.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}

def audio_track_name(track):
    p = track.get("properties", {})
    codec = (track.get("codec") or "").strip()
    ch = p.get("audio_channels")
    layout = CHANNELS.get(ch, f"{ch}ch" if ch else "")
    br = track.get("_bitrate_kbps")
    rate = f"{br} kb/s" if br else ""
    return " ".join(x for x in (codec, layout, rate) if x)


SUB_FLAGS = [
    ("forced_track", "Forced"),
    ("flag_hearing_impaired", "SDH"),
    ("flag_visual_impaired", "AD"),
    ("flag_text_descriptions", "Text descriptions"),
    ("flag_commentary", "Commentary"),
    ("flag_original", "Original"),
]

def subtitle_track_name(track):
    p = track.get("properties", {})
    labels = " ".join(label for key, label in SUB_FLAGS if p.get(key))
    return labels or "Full"


def track_selectors(info):
    audios, subs = [], []
    ai = si = 0
    for tr in (info or {}).get("tracks", []):
        if tr.get("type") == "audio":
            ai += 1
            audios.append((f"a{ai}", tr))
        elif tr.get("type") == "subtitles":
            si += 1
            subs.append((f"s{si}", tr))
    return audios, subs


def _current_name(track):
    return track.get("properties", {}).get("track_name") or ""


def _lang(track):
    p = track.get("properties", {})
    return p.get("language_ietf") or p.get("language") or "?"


def _is_french(track):
    return _lang(track).lower().startswith("fr")


def primary_audio_sel(audios):
    for sel, tr in audios:
        if _is_french(tr):
            return sel
    return audios[0][0] if audios else None


def audio_bitrates(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
                             capture_output=True, text=True, check=True).stdout
        res, ai = {}, 0
        for s in json.loads(out).get("streams", []):
            if s.get("codec_type") == "audio":
                ai += 1
                br = s.get("bit_rate")
                if br and str(br).isdigit():
                    res[ai] = round(int(br) / 1000)
        return res
    except Exception:
        return {}


def track_preview_lines(info, args):
    lines = []
    audios, subs = track_selectors(info)
    primary = primary_audio_sel(audios) if not args.no_flags else None
    if not args.no_audio_names:
        for sel, tr in audios:
            mark = " (defaut)" if sel == primary else ""
            lines.append(f"      audio {sel} [{_lang(tr)}]{mark} : "
                         f"{_current_name(tr) or '(vide)'!r} -> {audio_track_name(tr) or '(vide)'!r}")
    if not args.no_sub_names:
        for sel, tr in subs:
            p = tr.get("properties", {})
            flags = [label for key, label in SUB_FLAGS if p.get(key)]
            lines.append(f"      st {sel} [{_lang(tr)}] drapeaux={','.join(flags) or 'aucun'} : "
                         f"{_current_name(tr) or '(vide)'!r} -> {subtitle_track_name(tr)!r}")
    return lines


def verify_movie(info, movie, args):
    checks = []
    cont = (info or {}).get("container", {}).get("properties", {})
    checks.append(("titre", cont.get("title") == movie.get("title"), cont.get("title") or "(absent)"))
    if not args.no_date and movie.get("release_date"):
        cur = (cont.get("date_utc") or cont.get("date_local") or "")[:10]
        checks.append(("date", cur == movie["release_date"], cur or "(absente)"))
    if not args.no_cover:
        has = any(a.get("file_name", "").lower() == "cover.jpg" for a in (info or {}).get("attachments", []))
        checks.append(("jaquette", has, "presente" if has else "absente"))
    audios, subs = track_selectors(info)
    if not args.no_audio_names:
        for sel, tr in audios:
            checks.append((f"audio {sel}", _current_name(tr) == audio_track_name(tr), _current_name(tr) or "(vide)"))
    if not args.no_sub_names:
        for sel, tr in subs:
            checks.append((f"st {sel}", _current_name(tr) == subtitle_track_name(tr), _current_name(tr) or "(vide)"))
    return checks


def apply_to_movie(path, movie, args, info):
    path = path.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "tags.xml").write_text(build_movie_tags_xml(movie), encoding="utf-8")

        cmd = ["mkvpropedit", str(path), "--edit", "info", "--set", f"title={movie.get('title', '')}"]
        if not args.no_date and movie.get("release_date"):
            cmd += ["--set", f"date={movie['release_date']}T00:00:00Z"]

        cmd += ["--tags", "all:tags.xml"]
        if not args.no_stats:
            cmd += ["--add-track-statistics-tags"]

        audios, subs = track_selectors(info)
        primary = primary_audio_sel(audios) if not args.no_flags else None
        for sel, tr in audios:
            sets = []
            if not args.no_audio_names:
                nm = audio_track_name(tr)
                sets += ["--set", f"name={nm}"] if nm else (["--delete", "name"] if _current_name(tr) else [])
            if not args.no_flags:
                sets += ["--set", f"flag-default={1 if sel == primary else 0}"]
            if sets:
                cmd += ["--edit", f"track:{sel}"] + sets
        for sel, tr in subs:
            sets = []
            if not args.no_sub_names:
                nm = subtitle_track_name(tr)
                sets += ["--set", f"name={nm}"] if nm else (["--delete", "name"] if _current_name(tr) else [])
            if not args.no_flags:
                sets += ["--set", "flag-default=0"]
            if sets:
                cmd += ["--edit", f"track:{sel}"] + sets

        if not args.no_cover and movie.get("poster_path"):
            try:
                download_cover(movie["poster_path"], args.image_size, tmp / "cover.jpg")
                if any(a.get("file_name", "").lower() == "cover.jpg" for a in (info or {}).get("attachments", [])):
                    cmd += ["--delete-attachment", "name:cover.jpg"]
                cmd += ["--attachment-name", "cover.jpg", "--attachment-mime-type", "image/jpeg",
                        "--add-attachment", "cover.jpg"]
            except (URLError, OSError) as e:
                print(f"      jaquette ignoree ({e})")

        res = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        return res.returncode, (res.stdout + res.stderr).strip()


def write_poster(poster_path, folder, apply):
    """Ecrit folder.jpg (vignette de dossier Windows) depuis un poster TMDB."""
    if not poster_path:
        return "pas d'affiche TMDB"
    dest = Path(folder) / "folder.jpg"
    if not apply:
        return f"ecrirait {dest.name}"
    try:
        download_cover(poster_path, "w500", dest)
        return "folder.jpg ecrit"
    except (URLError, OSError) as e:
        return f"echec ({e})"


def _english_poster(fetch_fn, fallback):
    try:
        return fetch_fn().get("poster_path") or fallback
    except (URLError, OSError):
        return fallback


# ----------------------------------------------------------------------------
# 5. Verification des outils
# ----------------------------------------------------------------------------
def check_tools(args):
    missing = [t for t in ("mkvpropedit", "mkvmerge") if shutil.which(t) is None]
    if missing:
        print("Outils manquants dans le PATH :", ", ".join(missing))
        print("  Installe MKVToolNix : winget install MoritzBunkus.MKVToolNix")
        sys.exit(1)
    args.no_probe = shutil.which("ffprobe") is None
    if args.no_probe:
        print("ffprobe absent -> debit audio + verif. durees desactives "
              "(winget install Gyan.FFmpeg)\n")


# ----------------------------------------------------------------------------
# 6. Traitement d'un film
# ----------------------------------------------------------------------------
def process_movie(folder, mkv, movie, args, foldered):
    info = mkv_identify(mkv)
    if info and not args.no_probe:
        brs = audio_bitrates(mkv)
        ai = 0
        for tr in info.get("tracks", []):
            if tr.get("type") == "audio":
                ai += 1
                if ai in brs:
                    tr["_bitrate_kbps"] = brs[ai]

    if args.verify:
        diffs = [(lbl, det) for lbl, ok, det in verify_movie(info, movie, args) if not ok]
        if diffs:
            for lbl, det in diffs:
                print(f"      [DIFF] {lbl} : actuel = {det!r}")
        else:
            print("      [OK] deja conforme")
        return

    if not args.no_date and movie.get("release_date"):
        origine = f" (sortie {movie['_date_region']})" if movie.get("_date_region") else " (sortie d'origine)"
        print(f"      date -> {movie['release_date']}{origine}")
    for line in track_preview_lines(info, args):
        print(line)

    if args.apply:
        if args.skip_done and all(ok for _, ok, _ in verify_movie(info, movie, args)):
            print("      [SKIP] deja a jour")
        else:
            code, msg = apply_to_movie(mkv, movie, args, info)
            print(f"      [{'OK' if code == 0 else 'ECHEC'}]" + (f" {msg}" if code else ""))

    if args.artwork and foldered:
        poster = _english_poster(lambda: tmdb_movie(movie["id"], args.tmdb_key, ARTWORK_LANG),
                                 movie.get("poster_path"))
        print(f"      affiche (EN) : {write_poster(poster, folder, args.apply)}")


# ----------------------------------------------------------------------------
# 7. Programme principal
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Etiquette des films .mkv depuis TMDB (en francais).")
    ap.add_argument("--dir", required=True,
                    help="Dossier de films (un sous-dossier par film, ou des .mkv a plat)")
    ap.add_argument("--tmdb-id", help="Force l'id TMDB (utile si --dir ne contient qu'un seul film)")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--apply", action="store_true", help="Applique reellement (defaut : simulation)")
    ap.add_argument("--verify", action="store_true", help="Verifie seulement (aucune ecriture)")
    ap.add_argument("--skip-done", action="store_true", help="Saute les films deja conformes")
    ap.add_argument("--no-cover", action="store_true", help="N'embarque pas la jaquette")
    ap.add_argument("--no-date", action="store_true", help="Ne modifie pas la date du segment")
    ap.add_argument("--no-audio-names", action="store_true", help="Ne renomme pas les pistes audio")
    ap.add_argument("--no-sub-names", action="store_true", help="Ne renomme pas les pistes de sous-titres")
    ap.add_argument("--no-flags", action="store_true", help="Ne touche pas aux drapeaux 'par defaut'")
    ap.add_argument("--no-stats", action="store_true", help="N'ajoute pas les tags de statistiques")
    ap.add_argument("--artwork", action="store_true", help="Ecrit folder.jpg (affiche EN) par film")
    ap.add_argument("--image-size", default="w780", help="Taille TMDB : w300 / w780 / original")
    args = ap.parse_args()

    check_tools(args)
    load_dotenv()  # rend disponibles les cles du fichier .env (non committe)
    args.tmdb_key = (os.environ.get("TMDB_API_KEY")
                     or os.environ.get("TMDB_KEY") or TMDB_KEY or None)
    if not args.tmdb_key:
        sys.exit("Aucune cle TMDB. Renseigne la ligne TMDB_KEY=... du fichier .env "
                 "(voir .env.example), la variable d'environnement TMDB_API_KEY, "
                 "ou la constante TMDB_KEY en haut du fichier.")

    if args.verify:
        mode = "VERIFICATION (aucune ecriture)"
    elif args.apply:
        mode = "APPLICATION"
    else:
        mode = "SIMULATION (rien ne sera ecrit ; ajoute --apply pour appliquer)"
    print(f"=== {mode} ===   source : TMDB {args.language}\n")

    movies, foldered = find_movies(args.dir)
    if not movies:
        print(f"Aucun .mkv trouve dans : {args.dir}")
        return

    matched = 0
    for folder, mkv, rawname in movies:
        print(f"--- {mkv.name} ---")
        title, year, order = parse_title_year(rawname)
        if args.tmdb_id and len(movies) == 1:
            movie_id = args.tmdb_id
        else:
            try:
                results = tmdb_search_movie(title, year, args.tmdb_key, args.language)
                if not results and year:
                    results = tmdb_search_movie(title, None, args.tmdb_key, args.language)
            except (URLError, OSError) as e:
                print(f"  echec recherche TMDB : {e}\n")
                continue
            if not results:
                print(f"  [NON ASSOCIE] recherche '{title}'"
                      + (f" ({year})" if year else "") + " -> aucun resultat\n")
                continue
            movie_id = results[0]["id"]
            print(f"  recherche : '{title}'" + (f" ({year})" if year else "")
                  + (f" [ordre {order}]" if order else "")
                  + f" -> {results[0].get('title')} ({(results[0].get('release_date') or '?')[:4]}) [id {movie_id}]")

        try:
            movie = tmdb_movie(movie_id, args.tmdb_key, args.language)
        except (URLError, OSError) as e:
            print(f"  echec details TMDB : {e}\n")
            continue
        movie["_order"] = order

        # Sortie nationale (fr-FR -> FR) : sans ca, TMDB donne la sortie d'origine.
        region = release_region(args.language)
        local = (tmdb_release_date(movie_id, args.tmdb_key, args.language, region)
                 if region and not args.no_date else None)
        if local:
            movie["release_date"], movie["_date_region"] = local, region

        matched += 1
        process_movie(folder, mkv, movie, args, foldered)
        print()

    print(f"TOTAL : {matched}/{len(movies)} film(s) associe(s).")


if __name__ == "__main__":
    main()