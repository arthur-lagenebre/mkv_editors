#!/usr/bin/env python3
"""
tag_mkv.py — Etiquette des .mkv a partir de TMDB (donnees recuperees via l'API, en francais).

Ecrit DIRECTEMENT dans chaque .mkv (sans re-encodage ni remux, c'est quasi instantane) :
  - le titre et la DATE de sortie dans les informations de segment
  - le synopsis, les numeros saison/episode, realisateur(s), scenariste(s), casting voix (tags)
  - les tags de STATISTIQUES de piste (debit, duree, nb d'images)  [--no-stats pour desactiver]
  - la vignette de l'episode comme jaquette (attachment "cover.jpg")
  - le nom des pistes AUDIO       -> codec + canaux + debit (ex. "E-AC-3 5.1 640 kb/s")
  - le nom des pistes SOUS-TITRES -> uniquement les drapeaux actifs (Forced, SDH...), ou "Full"
  - les DRAPEAUX 'par defaut'     -> une seule piste audio par defaut (la FR), aucun sous-titre

=> Fichier 100% autonome : toutes les metadonnees voyagent avec le .mkv.

Dependances EXTERNES (a avoir dans le PATH) :
  - mkvpropedit et mkvmerge   -> paquet MKVToolNix
  - ffprobe                   -> paquet FFmpeg  (pour le debit audio + la verif. des durees)

Aucune dependance pip. Necessite un acces Internet (API TMDB + jaquettes).

Installation des outils (Windows) :
  winget install MoritzBunkus.MKVToolNix
  winget install Gyan.FFmpeg

Cle TMDB gratuite : themoviedb.org -> Parametres -> API. Fournie de 3 facons (par priorite) :
  1) --tmdb-key CLE   2) variable d'env TMDB_API_KEY   3) constante TMDB_KEY en haut du fichier

Usage — pointe --dir sur la RACINE de la serie (dossiers "Saison N"), --tmdb-id = l'id TMDB :

  # Simulation (n'ecrit rien) puis application :
  python tag_mkv.py --dir "...\\Secret Level" --tmdb-id 261579
  python tag_mkv.py --dir "...\\Secret Level" --tmdb-id 261579 --apply

  # Verification (lecture seule) : rapporte ce qui n'est pas encore conforme.
  python tag_mkv.py --dir "...\\Secret Level" --tmdb-id 261579 --verify

Structure attendue : un sous-dossier "Saison N" par saison (les .mkv dedans), chaque .mkv
prefixe par son numero d'episode ("01 - ...", "05 - ..."). Si --dir pointe directement sur
un dossier de saison, seule celle-ci est traitee.

Options principales :
  --tmdb-id STR    identifiant TMDB de la serie [OBLIGATOIRE]
  --tmdb-key STR   cle/token TMDB (sinon env TMDB_API_KEY ou constante TMDB_KEY)
  --language STR   langue TMDB (defaut : fr-FR)
  --series-name STR  force le nom de serie (sinon auto depuis TMDB)
  --apply          applique reellement (defaut : simulation)
  --verify         verifie seulement (aucune ecriture)
  --skip-done      saute les fichiers deja conformes
  --no-cover / --no-date / --no-audio-names / --no-sub-names / --no-flags / --no-stats
  --artwork        ecrit folder.jpg (vignette de dossier) en anglais (serie et chaque saison)
  --recap          genere une fiche recap HTML de la serie (onglets par saison)
  --image-size STR taille TMDB des images : w300 / w780 / original (defaut : w780)
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
from difflib import SequenceMatcher
from xml.sax.saxutils import escape
from urllib.request import urlopen, Request
from urllib.error import URLError

# ============================================================================
# Cle API TMDB : colle-la ici entre les guillemets pour ne plus avoir a la
# retaper (mode API). Priorite : --tmdb-key > variable d'env TMDB_API_KEY > ceci.
TMDB_KEY = "8575554c39a61d0515c279d2693c1773"
# ============================================================================

TMDB_IMG_BASE = "https://image.tmdb.org/t/p/"

# Regex de detection du numero d'episode dans le nom de fichier.
P_SE = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})")          # S01E05, S1E5
P_X = re.compile(r"(?<!\d)(\d{1,2})\s*[xX]\s*(\d{2,3})(?!\d)")     # 1x05  (lookarounds : evite 1920x1080)
P_EP = re.compile(r"[Ee]p(?:isode)?[\s._-]*(\d{1,3})")            # Episode 5, Ep05
P_LEAD = re.compile(r"^\s*(\d{1,2})[\s._\-]")                     # 01 - Titre, 1. Titre, 05_Titre
# Detection d'un dossier de saison : "Saison 1", "Season 02", "S1", "Saison_3"...
SEASON_RE = re.compile(r"^(?:saison|season|s)[\s_]*0*(\d+)$", re.IGNORECASE)


# ----------------------------------------------------------------------------
# 1. Detection des saisons sur le disque
# ----------------------------------------------------------------------------
def _season_number(name):
    m = SEASON_RE.match(Path(name).stem)
    return int(m.group(1)) if m else None


def find_seasons(root):
    """Retourne [(dossier_saison, numero), ...] pour chaque sous-dossier 'Saison N'.
    Liste vide si --dir ne contient aucun sous-dossier de saison (= saison unique)."""
    root = Path(root)
    if not root.is_dir():
        return []
    pairs = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        num = _season_number(sub.name)
        if num is not None:
            pairs.append((sub, num))
    return sorted(pairs, key=lambda x: x[1])


# ----------------------------------------------------------------------------
# 1b. Source TMDB (mode API) — recupere les donnees directement, en francais
# ----------------------------------------------------------------------------
TMDB_API = "https://api.themoviedb.org/3"

def _tmdb_get(endpoint, key, language):
    """GET sur l'API TMDB v3. Cle v3 (hex) -> parametre api_key ; token v4 (JWT) -> Bearer."""
    url = f"{TMDB_API}/{endpoint}"
    sep = "&" if "?" in url else "?"
    headers = {"User-Agent": "tag_mkv/1.0", "Accept": "application/json"}
    if key.startswith("ey") and key.count(".") == 2:          # token de lecture v4
        headers["Authorization"] = f"Bearer {key}"
        url += f"{sep}language={language}"
    else:                                                     # cle API v3
        url += f"{sep}api_key={key}&language={language}"
    with urlopen(Request(url, headers=headers), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def tmdb_series(show_id, key, language):
    """Details de la serie (nom, synopsis, poster, date de premiere diffusion...)."""
    return _tmdb_get(f"tv/{show_id}", key, language)


def tmdb_season(show_id, season_number, key, language):
    """Retourne les donnees d'une saison, MEME structure qu'un export JSON TMDB."""
    return _tmdb_get(f"tv/{show_id}/season/{season_number}", key, language)


# ----------------------------------------------------------------------------
# 2. Construction des tags Matroska (format XML attendu par mkvpropedit)
#    TargetTypeValue : 70 = COLLECTION (serie), 60 = SEASON, 50 = EPISODE
# ----------------------------------------------------------------------------
def _unique(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def build_tags_xml(season, ep, series_name, max_actors=20):
    directors = _unique(c.get("name") for c in ep.get("crew", []) if c.get("job") == "Director")
    writers = _unique(c.get("name") for c in ep.get("crew", []) if c.get("department") == "Writing")

    actors = []
    for g in ep.get("guest_stars", [])[:max_actors]:
        name, char = g.get("name", ""), g.get("character", "")
        actors.append(f"{name} ({char})" if char else name)

    def simple(name, value):
        return f"      <Simple><Name>{escape(name)}</Name><String>{escape(str(value))}</String></Simple>"

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Tags>"]

    # --- Niveau serie ---
    if series_name:
        lines += [
            "  <Tag>",
            "    <Targets><TargetTypeValue>70</TargetTypeValue></Targets>",
            simple("TITLE", series_name),
            "  </Tag>",
        ]

    # --- Niveau saison ---
    lines += [
        "  <Tag>",
        "    <Targets><TargetTypeValue>60</TargetTypeValue></Targets>",
        simple("PART_NUMBER", season.get("season_number", "")),
        simple("TITLE", season.get("name", "")),
        simple("TOTAL_PARTS", len(season.get("episodes", []))),
        "  </Tag>",
    ]

    # --- Niveau episode ---
    ep_tag = [
        "  <Tag>",
        "    <Targets><TargetTypeValue>50</TargetTypeValue></Targets>",
        simple("TITLE", ep.get("name", "")),
        simple("PART_NUMBER", ep.get("episode_number", "")),
    ]
    if ep.get("overview"):
        ep_tag.append(simple("SYNOPSIS", ep["overview"]))
        ep_tag.append(simple("SUMMARY", ep["overview"]))
    if ep.get("air_date"):
        ep_tag.append(simple("DATE_RELEASED", ep["air_date"]))
    for d in directors:
        ep_tag.append(simple("DIRECTOR", d))
    for w in writers:
        ep_tag.append(simple("WRITTEN_BY", w))
    for a in actors:
        ep_tag.append(simple("ACTOR", a))
    if ep.get("vote_average"):
        ep_tag.append(simple("COMMENT", f"TMDB {round(ep['vote_average'], 1)}/10 ({ep.get('vote_count', 0)} votes)"))
    ep_tag.append("  </Tag>")
    lines += ep_tag

    lines.append("</Tags>")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 3. Association fichier <-> episode
# ----------------------------------------------------------------------------
def detect_episode_number(filename):
    for pat in (P_SE, P_X):
        m = pat.search(filename)
        if m:
            return int(m.group(2))
    for pat in (P_EP, P_LEAD):
        m = pat.search(filename)
        if m:
            return int(m.group(1))
    return None


def best_title_match(filename, episodes):
    """Repli si le nom ne contient pas de numero : matche sur le titre du jeu."""
    stem = Path(filename).stem.lower()
    best, best_score = None, 0.0
    for ep in episodes:
        title = ep.get("name", "").lower()
        game = title.split(":")[0].strip()  # "warhammer 40,000: and they..." -> "warhammer 40,000"
        score = max(
            SequenceMatcher(None, stem, title).ratio(),
            SequenceMatcher(None, stem, game).ratio(),
        )
        if game and game in stem:           # le nom du jeu est present tel quel -> forte confiance
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = ep, score
    return best, best_score


def probe_duration_minutes(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout
        return float(json.loads(out)["format"]["duration"]) / 60.0
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 4. Ecriture dans le .mkv
# ----------------------------------------------------------------------------
def mkv_identify(path):
    """Retourne le JSON de 'mkvmerge -J' (pistes + pieces jointes), ou None."""
    try:
        return json.loads(subprocess.run(["mkvmerge", "-J", str(path)],
                                         capture_output=True, text=True, check=True).stdout)
    except Exception:
        return None


def download_cover(image_path, size, dest):
    url = f"{TMDB_IMG_BASE}{size}{image_path}"
    req = Request(url, headers={"User-Agent": "tag_mkv/1.0"})
    with urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


# Nom "qualite" d'une piste audio : codec + disposition des canaux.
CHANNELS = {1: "1.0", 2: "2.0", 3: "2.1", 4: "4.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}

def audio_track_name(track):
    p = track.get("properties", {})
    codec = (track.get("codec") or "").strip()
    ch = p.get("audio_channels")
    layout = CHANNELS.get(ch, f"{ch}ch" if ch else "")
    br = track.get("_bitrate_kbps")
    rate = f"{br} kb/s" if br else ""
    return " ".join(x for x in (codec, layout, rate) if x)


# Drapeaux "cochables" d'une piste de sous-titres -> etiquette a mettre dans le nom.
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
    return labels or "Full"    # aucun drapeau -> "Full"


def track_selectors(info):
    """(audios, subs) : listes de (selecteur mkvpropedit, piste), numerotees par type."""
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
    """Selecteur de la piste audio a marquer 'par defaut' : la francaise, sinon la premiere."""
    for sel, tr in audios:
        if _is_french(tr):
            return sel
    return audios[0][0] if audios else None


def audio_bitrates(path):
    """{index_audio_1based: kb/s} via ffprobe, ou {} si indisponible."""
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
    """Lignes d'apercu (simulation) des renommages de pistes pour un fichier."""
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


def verify_file(info, season, ep, args):
    """Compare l'etat actuel du .mkv a l'etat vise. Retourne [(label, ok, detail_actuel), ...]."""
    checks = []
    cont = (info or {}).get("container", {}).get("properties", {})
    checks.append(("titre", cont.get("title") == ep.get("name"), cont.get("title") or "(absent)"))
    if not args.no_date and ep.get("air_date"):
        cur = (cont.get("date_utc") or cont.get("date_local") or "")[:10]
        checks.append(("date", cur == ep["air_date"], cur or "(absente)"))
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


def apply_to_file(path, season, ep, args, info):
    """Ecrit dans le .mkv : titre + date, tags (+ stats), jaquette, renommage et
    drapeaux des pistes. Un dossier temp sert de cwd pour referencer tags.xml /
    cover.jpg en relatif (evite les soucis de ':' sous Windows)."""
    path = path.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "tags.xml").write_text(build_tags_xml(season, ep, args.series_name), encoding="utf-8")

        # --- Informations de segment : titre (+ date de sortie) ---
        cmd = ["mkvpropedit", str(path), "--edit", "info", "--set", f"title={ep.get('name', '')}"]
        if not args.no_date and ep.get("air_date"):
            cmd += ["--set", f"date={ep['air_date']}T00:00:00Z"]

        # --- Tags (+ statistiques de piste : debit, duree, nb images) ---
        cmd += ["--tags", "all:tags.xml"]
        if not args.no_stats:
            cmd += ["--add-track-statistics-tags"]

        # --- Pistes : nom + drapeau 'par defaut' (fusionnes par piste) ---
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
                sets += ["--set", "flag-default=0"]   # aucun sous-titre par defaut ; forced inchange
            if sets:
                cmd += ["--edit", f"track:{sel}"] + sets

        # --- Jaquette embarquee ---
        if not args.no_cover:
            img = ep.get("still_path") or season.get("poster_path")
            if img:
                try:
                    download_cover(img, args.image_size, tmp / "cover.jpg")
                    has_cover = any(a.get("file_name", "").lower() == "cover.jpg"
                                    for a in (info or {}).get("attachments", []))
                    if has_cover:
                        cmd += ["--delete-attachment", "name:cover.jpg"]
                    cmd += ["--attachment-name", "cover.jpg",
                            "--attachment-mime-type", "image/jpeg",
                            "--add-attachment", "cover.jpg"]
                except (URLError, OSError) as e:
                    print(f"      jaquette ignoree ({e})")

        res = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        return res.returncode, (res.stdout + res.stderr).strip()


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
        print("ffprobe absent -> verification par duree desactivee "
              "(winget install Gyan.FFmpeg)\n")


# ----------------------------------------------------------------------------
# 6. Traitement d'une saison
# ----------------------------------------------------------------------------
def process_season(mkv_dir, season, args):
    """Construit le plan d'une saison, l'affiche, et applique si --apply.
    Retourne (nb_associes, nb_fichiers)."""
    episodes = season.get("episodes", [])
    by_num = {e.get("episode_number"): e for e in episodes}
    files = sorted(Path(mkv_dir).glob("*.mkv"))
    if not files:
        print(f"  Aucun .mkv dans {mkv_dir}")
        return 0, 0

    plan = []  # (file, episode|None, methode, avertissement, info_mkvmerge|None)
    for f in files:
        f = f.resolve()
        num = detect_episode_number(f.name)
        if num in by_num:
            ep, method = by_num[num], f"n.{num:02d} (depuis le nom)"
        else:
            ep, score = best_title_match(f.name, episodes)
            method = f"titre (~{score:.0%})"
            if score < args.match_threshold:
                ep = None

        warn = ""
        info = None
        if ep:
            info = mkv_identify(f)          # pistes + pieces jointes (lecture seule)
            if info and not args.no_probe:
                brs = audio_bitrates(f)     # debit par piste audio, pour le nom
                ai = 0
                for tr in info.get("tracks", []):
                    if tr.get("type") == "audio":
                        ai += 1
                        if ai in brs:
                            tr["_bitrate_kbps"] = brs[ai]
                dmin = probe_duration_minutes(f)
                if dmin and ep.get("runtime") and abs(dmin - ep["runtime"]) > 3:
                    warn = f"duree {dmin:.0f}min vs {ep['runtime']}min attendues -> a verifier"
        plan.append((f, ep, method, warn, info))

    matched = 0
    for f, ep, method, warn, info in plan:
        if ep is None:
            print(f"  [NON ASSOCIE] {f.name}")
            continue
        matched += 1
        sn, en = season.get("season_number", 1), ep.get("episode_number", 0)
        print(f"  [S{sn:02d}E{en:02d}] {f.name}")
        print(f"            -> {ep.get('name', '')}   ({method})")
        if warn:
            print(f"            /!\\ {warn}")
        if args.verify:                     # mode verification : etat actuel vs vise
            checks = verify_file(info, season, ep, args)
            diffs = [(lbl, det) for lbl, ok, det in checks if not ok]
            if diffs:
                for lbl, det in diffs:
                    print(f"      [DIFF] {lbl} : actuel = {det!r}")
            else:
                print("      [OK] deja conforme")
            continue
        if not args.no_date and ep.get("air_date"):
            print(f"      date segment -> {ep['air_date']}")
        for line in track_preview_lines(info, args):
            print(line)

    if args.apply and not args.verify:
        print("  --- ecriture ---")
        for f, ep, _, _, info in plan:
            if ep is None:
                continue
            if args.skip_done and all(ok for _, ok, _ in verify_file(info, season, ep, args)):
                print(f"  [SKIP] {f.name} (deja a jour)")
                continue
            code, msg = apply_to_file(f, season, ep, args, info)
            status = "OK" if code == 0 else "ECHEC"
            print(f"  [{status}] {f.name}" + (f"  -> {msg}" if code != 0 else ""))

    print(f"  => {matched}/{len(plan)} associe(s).")
    pairs = [(f, ep) for f, ep, _, _, _ in plan if ep is not None]
    return matched, len(plan), pairs


# ----------------------------------------------------------------------------
# 7. Generateurs annexes (option) : posters de dossier + fiche recap HTML
# ----------------------------------------------------------------------------
POSTER_SIZE = "w500"       # les posters sont en portrait
STILL_SIZE = "w300"        # vignettes d'episode dans la fiche recap
ARTWORK_LANG = "en-US"     # affiches recuperees en anglais

FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def fr_date(iso):
    """'2024-12-10' -> '10 décembre 2024'."""
    try:
        y, m, d = iso.split("-")
        return f"{int(d)} {FR_MONTHS[int(m) - 1]} {y}"
    except Exception:
        return iso or ""


def write_poster(poster_path, folder, apply):
    """Ecrit folder.jpg dans 'folder' (vignette de dossier Windows) depuis un poster TMDB."""
    if not poster_path:
        return "pas d'affiche TMDB"
    dest = Path(folder) / "folder.jpg"
    if not apply:
        return f"ecrirait {dest.name}"
    try:
        download_cover(poster_path, POSTER_SIZE, dest)
        return "folder.jpg ecrit"
    except (URLError, OSError) as e:
        return f"echec ({e})"


def _english_poster(fetch_fn, fallback):
    """Recupere le poster_path en anglais, avec repli sur celui deja recupere."""
    try:
        return fetch_fn().get("poster_path") or fallback
    except (URLError, OSError):
        return fallback


def _write_text(path, text, apply):
    if not apply:
        return f"ecrirait {Path(path).name}"
    Path(path).write_text(text, encoding="utf-8")
    return f"{Path(path).name} ecrit"


def build_recap_html(series_name, show, seasons):
    """Page HTML autonome : onglets de saisons cliquables, une carte par episode."""
    def esc(s):
        return escape(str(s or ""))

    tabs, panels = [], []
    for i, (num, season) in enumerate(seasons):
        label = season.get("name") or f"Saison {num}"
        tabs.append(f"<button class='tab{' active' if i == 0 else ''}' data-s='{num}'>{esc(label)}</button>")
        cards = []
        for ep in season.get("episodes", []):
            still = ep.get("still_path")
            img = f"{TMDB_IMG_BASE}{STILL_SIZE}{still}" if still else ""
            rt = f" · {ep['runtime']} min" if ep.get("runtime") else ""
            cards.append(
                "<div class='ep'>"
                + (f"<img src='{esc(img)}' alt='' loading='lazy'>" if img else "<img alt=''>")
                + "<div class='meta'>"
                + f"<div><span class='n'>E{ep.get('episode_number', 0):02d}</span> "
                + f"<span class='t'>{esc(ep.get('name'))}</span></div>"
                + f"<div class='d'>{esc(fr_date(ep.get('air_date')))}{rt}</div>"
                + f"<div class='o'>{esc(ep.get('overview'))}</div>"
                + "</div></div>")
        panels.append(f"<section class='season' data-s='{num}'{'' if i == 0 else ' hidden'}>"
                      f"{''.join(cards)}</section>")

    return (
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
        f"<title>{esc(series_name)}</title>"
        "<style>"
        "body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#14151a;color:#e8e8ea}"
        ".wrap{max-width:1000px;margin:0 auto;padding:32px}"
        "h1{margin:0 0 4px}.sub{color:#9aa0aa;margin-bottom:22px}"
        ".tabs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:24px;"
        "position:sticky;top:0;background:#14151a;padding:12px 0;z-index:1}"
        ".tab{cursor:pointer;border:1px solid #2a2c34;background:#1c1e26;color:#c7ccd4;"
        "padding:7px 16px;border-radius:999px;font:inherit;font-size:14px}"
        ".tab:hover{background:#252833}"
        ".tab.active{background:#7cc4ff;border-color:#7cc4ff;color:#0d0e12;font-weight:600}"
        ".ep{display:flex;gap:16px;padding:14px 0;border-bottom:1px solid #21232b}"
        ".ep img{width:160px;height:90px;object-fit:cover;border-radius:8px;background:#21232b;flex:none}"
        ".ep .meta{flex:1}.ep .n{color:#7cc4ff;font-weight:600}"
        ".ep .t{font-weight:600}.ep .d{color:#9aa0aa;font-size:14px;margin:2px 0 6px}"
        ".ep .o{color:#c7ccd4;font-size:14px}"
        "</style></head><body><div class='wrap'>"
        f"<h1>{esc(series_name)}</h1>"
        f"<div class='sub'>{esc(show.get('overview', ''))}</div>"
        f"<nav class='tabs'>{''.join(tabs)}</nav>"
        f"{''.join(panels)}"
        "<script>"
        "document.querySelectorAll('.tab').forEach(function(b){"
        "b.onclick=function(){"
        "document.querySelectorAll('.tab').forEach(function(x){x.classList.toggle('active',x===b)});"
        "document.querySelectorAll('.season').forEach(function(s){s.hidden=s.dataset.s!==b.dataset.s})"
        "}});"
        "</script></div></body></html>"
    )


def generate_sidecars(root_dir, series_name, show, processed, args):
    """Ecrit posters de dossier (affiche EN) / fiche recap selon les options. processed =
    [(dossier_saison, numero, donnees_saison, [(mkv, episode), ...]), ...]."""
    if not (args.artwork or args.recap):
        return
    apply = args.apply and not args.verify
    print("--- annexes ---")

    if args.artwork:
        for folder, num, season, _ in processed:
            poster = _english_poster(
                lambda: tmdb_season(args.tmdb_id, num, args.tmdb_key, ARTWORK_LANG),
                season.get("poster_path"))
            print(f"  [saison {num}] affiche (EN) : {write_poster(poster, folder, apply)}")
        poster = _english_poster(
            lambda: tmdb_series(args.tmdb_id, args.tmdb_key, ARTWORK_LANG),
            show.get("poster_path"))
        print(f"  [serie] affiche (EN) : {write_poster(poster, root_dir, apply)}")

    if args.recap:
        html = build_recap_html(series_name, show, [(n, s) for _, n, s, _ in processed])
        out = Path(root_dir) / "recap.html"
        print(f"  [serie] {_write_text(out, html, apply)}")


# ----------------------------------------------------------------------------
# 8. Programme principal
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Etiquette des .mkv depuis TMDB (donnees en francais via l'API).")
    ap.add_argument("--dir", required=True,
                    help="Racine de la serie (dossiers 'Saison N') OU un seul dossier de saison")
    # --- Source TMDB ---
    ap.add_argument("--tmdb-id", required=True, help="Identifiant TMDB de la serie [OBLIGATOIRE]")
    ap.add_argument("--tmdb-key", help="Cle API TMDB (v3) ou token v4 ; sinon env TMDB_API_KEY ou constante")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--series-name", help="Force le nom de serie (sinon recupere automatiquement de TMDB)")
    # --- Ce qu'on ecrit ---
    ap.add_argument("--apply", action="store_true", help="Applique reellement (defaut : simulation)")
    ap.add_argument("--verify", action="store_true", help="Verifie seulement l'etat des fichiers (aucune ecriture)")
    ap.add_argument("--skip-done", action="store_true", help="Saute les fichiers deja conformes")
    ap.add_argument("--no-cover", action="store_true", help="N'embarque pas la jaquette")
    ap.add_argument("--no-date", action="store_true", help="Ne modifie pas la date du segment")
    ap.add_argument("--no-audio-names", action="store_true", help="Ne renomme pas les pistes audio")
    ap.add_argument("--no-sub-names", action="store_true", help="Ne renomme pas les pistes de sous-titres")
    ap.add_argument("--no-flags", action="store_true", help="Ne touche pas aux drapeaux 'par defaut'")
    ap.add_argument("--no-stats", action="store_true", help="N'ajoute pas les tags de statistiques de piste")
    # --- Generateurs annexes (option, ecrivent des fichiers a cote des .mkv) ---
    ap.add_argument("--artwork", action="store_true", help="Ecrit folder.jpg (vignette de dossier) en anglais")
    ap.add_argument("--recap", action="store_true", help="Genere une fiche recap HTML de la serie")
    ap.add_argument("--image-size", default="w780", help="Taille TMDB : w300 / w780 / original")
    ap.add_argument("--match-threshold", type=float, default=0.55,
                    help="Score minimal pour une association par titre (0-1)")
    args = ap.parse_args()

    check_tools(args)

    # Cle : ligne de commande > variable d'environnement > constante en haut du fichier
    args.tmdb_key = args.tmdb_key or os.environ.get("TMDB_API_KEY") or TMDB_KEY or None
    if not args.tmdb_key:
        sys.exit("Aucune cle TMDB. Renseigne --tmdb-key, la variable TMDB_API_KEY, "
                 "ou la constante TMDB_KEY en haut du fichier.")

    # Details de la serie via TMDB (nom auto, + poster/synopsis pour les annexes)
    try:
        show = tmdb_series(args.tmdb_id, args.tmdb_key, args.language)
    except (URLError, OSError) as e:
        sys.exit(f"Echec de l'appel TMDB (serie) : {e}")
    args.series_name = args.series_name or show.get("name", "")

    if args.verify:
        mode = "VERIFICATION (aucune ecriture)"
    elif args.apply:
        mode = "APPLICATION"
    else:
        mode = "SIMULATION (rien ne sera ecrit ; ajoute --apply pour appliquer)"
    print(f"=== {mode} ===")
    print(f"    serie : {args.series_name}   |   source : TMDB {args.language} (id {args.tmdb_id})\n")

    seasons = find_seasons(args.dir)
    if seasons:
        # --- Multi-saisons : --dir est la racine de la serie ---
        total_m = total_f = 0
        processed = []
        for sub, num in seasons:
            print(f"--- {sub.name}  (TMDB saison {num}) ---")
            try:
                data = tmdb_season(args.tmdb_id, num, args.tmdb_key, args.language)
            except (URLError, OSError) as e:
                print(f"  echec TMDB saison {num} : {e} -> saison ignoree\n")
                continue
            m, tot, pairs = process_season(sub, data, args)
            total_m += m
            total_f += tot
            processed.append((sub, num, data, pairs))
            print()
        print(f"TOTAL : {total_m}/{total_f} fichier(s) associe(s) sur {len(seasons)} saison(s) detectee(s).")
        generate_sidecars(args.dir, args.series_name, show, processed, args)
    else:
        # --- Saison unique : --dir contient directement les .mkv ---
        num = _season_number(Path(args.dir).name) or 1
        try:
            data = tmdb_season(args.tmdb_id, num, args.tmdb_key, args.language)
        except (URLError, OSError) as e:
            sys.exit(f"Echec de l'appel TMDB (saison {num}) : {e}")
        _, _, pairs = process_season(args.dir, data, args)
        generate_sidecars(args.dir, args.series_name, show, [(Path(args.dir), num, data, pairs)], args)


if __name__ == "__main__":
    main()
