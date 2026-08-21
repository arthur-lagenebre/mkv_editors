"""Lecture et ecriture des fichiers .mkv (MKVToolNix + FFmpeg).

Films et episodes ne different que par les donnees a inscrire : le contenu vise
est decrit par un `Target`, ce qu'on s'autorise a modifier par des `Options`, et
la comparaison comme l'ecriture sont communes.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from .tmdb import TmdbError

# Echecs possibles d'un outil externe : binaire absent, code de retour non nul,
# sortie illisible. Rattrapes ensemble, mais jamais en masquant tout le reste.
TOOL_FAILURES = (subprocess.SubprocessError, OSError, json.JSONDecodeError)


def run_tool(cmd, **kwargs):
    """Lance un outil externe et recupere sa sortie, decodee en UTF-8.

    Preciser l'encodage n'est pas un detail : mkvmerge et ffprobe ecrivent leur
    JSON en UTF-8, alors que Python decoderait avec l'encodage local (cp1252 sous
    Windows). Il suffit d'un caractere absent de cp1252 dans un synopsis - le
    trait d'union typographique de "est-ce" en fournit un - pour que le decodage
    echoue dans un thread interne de subprocess : la sortie revient alors a None,
    sans erreur ni code de retour anormal, et le fichier devient illisible juste
    apres avoir ete etiquete.
    """
    return subprocess.run(cmd, capture_output=True,
                          encoding="utf-8", errors="replace", **kwargs)


def _reason(exc):
    """Message court expliquant l'echec d'un outil externe."""
    if isinstance(exc, subprocess.CalledProcessError):
        err = (exc.stderr or "").strip().splitlines()
        return err[0] if err else f"code {exc.returncode}"
    return str(exc)


def check_tools(needs_mkvtoolnix=True):
    """Verifie les outils externes. Retourne True si ffprobe est disponible.

    Sort du programme si MKVToolNix manque alors qu'on doit ecrire dans des .mkv.
    """
    if not needs_mkvtoolnix:
        return False
    missing = [t for t in ("mkvpropedit", "mkvmerge", "mkvextract")
               if shutil.which(t) is None]
    if missing:
        print("Outils manquants dans le PATH :", ", ".join(missing))
        print("  Installe MKVToolNix : winget install MoritzBunkus.MKVToolNix")
        sys.exit(1)
    if shutil.which("ffprobe") is None:
        print("ffprobe absent -> debit audio + verif. des durees desactives "
              "(winget install Gyan.FFmpeg)\n")
        return False
    return True


def identify(path):
    """(JSON de 'mkvmerge -J', remarque). L'info est None si le fichier est illisible.

    Rien n'est affiche ici : la remarque est rendue a l'appelant, qui l'imprime
    au bon endroit. C'est ce qui permet de lire plusieurs fichiers en parallele
    sans entrelacer les messages.
    """
    try:
        out = run_tool(["mkvmerge", "-J", str(path)], check=True).stdout
        if not out:
            return None, "lecture impossible par mkvmerge (sortie vide)"
        return json.loads(out), ""
    except TOOL_FAILURES as e:
        return None, f"lecture impossible par mkvmerge ({_reason(e)})"


@dataclass
class Probe:
    """Ce que ffprobe nous apprend d'un fichier."""
    duration_min: float | None = None
    audio_bitrates: dict = field(default_factory=dict)   # {index audio 1-based: kb/s}


def probe(path):
    """Duree ET debit de chaque piste audio, en UN seul appel ffprobe.

    Les deux informations venaient de deux appels distincts, soit deux processus
    lances par fichier la ou un seul suffit.
    """
    try:
        out = run_tool(["ffprobe", "-v", "quiet", "-print_format", "json",
                        "-show_format", "-show_streams", str(path)], check=True).stdout
        data = json.loads(out) if out else None
    except TOOL_FAILURES:
        return Probe()
    if not isinstance(data, dict):
        return Probe()

    duration = None
    raw = data.get("format", {}).get("duration")
    try:
        duration = float(raw) / 60.0 if raw is not None else None
    except (TypeError, ValueError):
        duration = None

    bitrates, index = {}, 0
    for s in data.get("streams", []):
        if s.get("codec_type") == "audio":
            index += 1
            br = s.get("bit_rate")
            if br and str(br).isdigit():
                bitrates[index] = round(int(br) / 1000)
    return Probe(duration, bitrates)


def annotate_bitrates(info, path):
    """Injecte les debits ffprobe dans les pistes audio de `info`, pour leur nom.

    Retourne le Probe complet (la duree sert a reperer un fichier tronque).
    """
    result = probe(path)
    index = 0
    for tr in (info or {}).get("tracks", []):
        if tr.get("type") == "audio":
            index += 1
            if index in result.audio_bitrates:
                tr["_bitrate_kbps"] = result.audio_bitrates[index]
    return result


def parse_tags(xml):
    """{(niveau de cible, nom, valeur)} pour les tags de niveau film/saison/serie.

    Les tags de PISTE sont ecartes : ce sont les statistiques ecrites par
    --add-track-statistics-tags, qui ne viennent pas de TMDB. Et un bloc sans
    TargetTypeValue vaut 50 : c'est la valeur par defaut de Matroska, que
    mkvpropedit omet a l'ecriture - sans cette equivalence, un fichier
    fraichement etiquete paraitrait deja different de ce qu'on vient d'y ecrire.
    """
    try:
        root = ElementTree.fromstring(xml.lstrip("﻿"))
    except (ElementTree.ParseError, AttributeError):
        return set()
    tags = set()
    for bloc in root.findall("Tag"):
        targets = bloc.find("Targets")
        if targets is not None and targets.find("TrackUID") is not None:
            continue
        niveau = targets.find("TargetTypeValue") if targets is not None else None
        niveau = int(niveau.text) if (niveau is not None and niveau.text) else 50
        for simple in bloc.findall("Simple"):
            tags.add((niveau, simple.findtext("Name") or "", simple.findtext("String") or ""))
    return tags


def read_tags(path):
    """Tags deja ecrits dans le fichier, sous la forme rendue par parse_tags.

    mkvmerge -J ne donne pas leur contenu : il faut passer par mkvextract.
    """
    try:
        out = run_tool(["mkvextract", str(path), "tags", "-"], check=True).stdout
    except TOOL_FAILURES:
        return set()
    return parse_tags(out) if out else set()


READ_WORKERS = 8      # lectures simultanees : c'est de l'attente de sous-processus


@dataclass
class Reading:
    """Ce qu'on a pu lire d'un fichier, sans rien afficher."""
    info: dict | None = None
    probe: Probe = field(default_factory=Probe)
    tags: set | None = None        # None = non relus (lecture non demandee)
    note: str = ""


def inspect(path, with_probe=True, with_tags=False):
    """Lecture complete d'un fichier. Sans affichage : appelable en parallele.

    Les tags ne sont relus que si on en a besoin (--verify, --skip-done) : c'est
    un sous-processus de plus par fichier.
    """
    info, note = identify(path)
    probe = annotate_bitrates(info, path) if (info and with_probe) else Probe()
    tags = read_tags(path) if (info and with_tags) else None
    return Reading(info, probe, tags, note)


def inspect_all(paths, with_probe=True, with_tags=False, workers=READ_WORKERS):
    """{chemin: Reading} pour plusieurs fichiers, lus en parallele.

    Chaque fichier coute deux a trois sous-processus qu'on passe son temps a
    attendre : une saison entiere se lit en un seul de ces delais.
    """
    paths = list(paths)
    lire = lambda p: inspect(p, with_probe, with_tags)      # noqa: E731
    if len(paths) < 2:
        return {p: lire(p) for p in paths}
    with ThreadPoolExecutor(max_workers=min(workers, len(paths))) as pool:
        return dict(zip(paths, pool.map(lire, paths)))


# --------------------------------------------------------------------------
# Noms de pistes
# --------------------------------------------------------------------------
CHANNELS = {1: "1.0", 2: "2.0", 3: "2.1", 4: "4.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}

# Drapeaux "cochables" d'une piste de sous-titres -> etiquette a mettre dans le nom.
SUB_FLAGS = [
    ("forced_track", "Forced"),
    ("flag_hearing_impaired", "SDH"),
    ("flag_visual_impaired", "AD"),
    ("flag_text_descriptions", "Text descriptions"),
    ("flag_commentary", "Commentary"),
    ("flag_original", "Original"),
]


def audio_track_name(track):
    """Nom "qualite" d'une piste audio : codec + canaux + debit."""
    p = track.get("properties", {})
    codec = (track.get("codec") or "").strip()
    ch = p.get("audio_channels")
    layout = CHANNELS.get(ch, f"{ch}ch" if ch else "")
    br = track.get("_bitrate_kbps")
    rate = f"{br} kb/s" if br else ""
    return " ".join(x for x in (codec, layout, rate) if x)


def subtitle_track_name(track):
    """Drapeaux actifs d'une piste de sous-titres, ou 'Full' si elle n'en a aucun."""
    p = track.get("properties", {})
    labels = " ".join(label for key, label in SUB_FLAGS if p.get(key))
    return labels or "Full"


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


def current_name(track):
    return track.get("properties", {}).get("track_name") or ""


def lang(track):
    p = track.get("properties", {})
    return p.get("language_ietf") or p.get("language") or "?"


def is_french(track):
    return lang(track).lower().startswith("fr")


def primary_audio_sel(audios):
    """Piste audio a marquer 'par defaut' : la francaise, sinon la premiere."""
    for sel, tr in audios:
        if is_french(tr):
            return sel
    return audios[0][0] if audios else None


def has_cover(info):
    return any(a.get("file_name", "").lower() == "cover.jpg"
               for a in (info or {}).get("attachments", []))


# --------------------------------------------------------------------------
# Tags Matroska (XML attendu par mkvpropedit)
#   TargetTypeValue : 70 = COLLECTION (serie, saga), 60 = SEASON, 50 = FILM/EPISODE
# --------------------------------------------------------------------------
def unique(seq):
    """Valeurs non vides, sans doublon, dans l'ordre d'apparition."""
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def simple(name, value):
    """Une ligne <Simple> de tag Matroska (contenu echappe)."""
    return f"      <Simple><Name>{escape(name)}</Name><String>{escape(str(value))}</String></Simple>"


def tag_block(target_type, lines):
    """Un bloc <Tag> vise sur un TargetTypeValue donne."""
    return ["  <Tag>",
            f"    <Targets><TargetTypeValue>{target_type}</TargetTypeValue></Targets>",
            *lines,
            "  </Tag>"]


def tags_document(blocks):
    """Assemble des blocs <Tag> en un document XML complet."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Tags>"]
    for block in blocks:
        lines += block
    lines.append("</Tags>")
    return "\n".join(lines)


def credits_lines(crew, cast, max_actors=20):
    """Lignes <Simple> DIRECTOR / WRITTEN_BY / ACTOR a partir des credits TMDB."""
    lines = []
    for name in unique(c.get("name") for c in crew if c.get("job") == "Director"):
        lines.append(simple("DIRECTOR", name))
    for name in unique(c.get("name") for c in crew if c.get("department") == "Writing"):
        lines.append(simple("WRITTEN_BY", name))
    for person in cast[:max_actors]:
        name, char = person.get("name", ""), person.get("character", "")
        lines.append(simple("ACTOR", f"{name} ({char})" if char else name))
    return lines


# --------------------------------------------------------------------------
# Comparaison et ecriture
# --------------------------------------------------------------------------
@dataclass
class Options:
    """Ce qu'on s'autorise a modifier : miroir des drapeaux --no-* de la ligne de commande."""
    cover: bool = True
    date: bool = True
    audio_names: bool = True
    sub_names: bool = True
    flags: bool = True
    stats: bool = True
    image_size: str = "w780"

    @classmethod
    def from_args(cls, args):
        return cls(cover=not args.no_cover,
                   date=not args.no_date,
                   audio_names=not args.no_audio_names,
                   sub_names=not args.no_sub_names,
                   flags=not args.no_flags,
                   stats=not args.no_stats,
                   image_size=args.image_size)


@dataclass
class Report:
    """Ce qu'un traitement a donne. Additionnable pour totaliser une serie.

    `diffs` et `failures` sont ce qui reste a corriger : ils decident du code de
    sortie, pour qu'un script sache si le passage s'est bien termine.
    """
    matched: int = 0        # fichiers associes a une fiche TMDB
    total: int = 0          # fichiers vus
    diffs: int = 0          # fichiers non conformes (--verify)
    failures: int = 0       # ecritures en echec

    def __add__(self, other):
        return Report(self.matched + other.matched, self.total + other.total,
                      self.diffs + other.diffs, self.failures + other.failures)

    @property
    def exit_code(self):
        return 1 if (self.diffs or self.failures) else 0

    def epilogue(self):
        """Ligne finale a afficher quand quelque chose n'est pas passe."""
        restes = []
        if self.diffs:
            restes.append(f"{self.diffs} fichier(s) non conforme(s)")
        if self.failures:
            restes.append(f"{self.failures} ecriture(s) en echec")
        return " ; ".join(restes)


@dataclass
class Target:
    """Etat vise pour un fichier : ce que TMDB dit qu'il devrait contenir."""
    title: str
    date: str | None = None        # "AAAA-MM-JJ"
    tags_xml: str = ""
    poster: str | None = None      # chemin TMDB de la jaquette a embarquer


def track_preview_lines(info, opts):
    """Lignes d'apercu (simulation) des renommages de pistes pour un fichier."""
    lines = []
    audios, subs = track_selectors(info)
    primary = primary_audio_sel(audios) if opts.flags else None
    if opts.audio_names:
        for sel, tr in audios:
            mark = " (defaut)" if sel == primary else ""
            lines.append(f"      audio {sel} [{lang(tr)}]{mark} : "
                         f"{current_name(tr) or '(vide)'!r} -> {audio_track_name(tr) or '(vide)'!r}")
    if opts.sub_names:
        for sel, tr in subs:
            p = tr.get("properties", {})
            flags = [label for key, label in SUB_FLAGS if p.get(key)]
            lines.append(f"      st {sel} [{lang(tr)}] drapeaux={','.join(flags) or 'aucun'} : "
                         f"{current_name(tr) or '(vide)'!r} -> {subtitle_track_name(tr)!r}")
    return lines


def verify(info, target, opts, tags=None):
    """Compare l'etat actuel du .mkv a l'etat vise. [(label, ok, detail_actuel), ...]

    `tags` vient de read_tags ; a None, les tags ne sont pas compares - c'est le
    cas quand on ne les a pas relus.
    """
    checks = []
    cont = (info or {}).get("container", {}).get("properties", {})
    checks.append(("titre", cont.get("title") == target.title, cont.get("title") or "(absent)"))
    if opts.date and target.date:
        cur = (cont.get("date_utc") or cont.get("date_local") or "")[:10]
        checks.append(("date", cur == target.date, cur or "(absente)"))
    if opts.cover and target.poster:
        # Sans jaquette disponible cote TMDB, en exiger une signalerait un ecart
        # que rien ne peut combler (et --skip-done ne sauterait plus jamais rien).
        present = has_cover(info)
        checks.append(("jaquette", present, "presente" if present else "absente"))
    audios, subs = track_selectors(info)
    if opts.audio_names:
        for sel, tr in audios:
            checks.append((f"audio {sel}", current_name(tr) == audio_track_name(tr),
                           current_name(tr) or "(vide)"))
    if opts.sub_names:
        for sel, tr in subs:
            checks.append((f"st {sel}", current_name(tr) == subtitle_track_name(tr),
                           current_name(tr) or "(vide)"))
    if tags is not None and target.tags_xml:
        attendus = parse_tags(target.tags_xml)
        manquants, en_trop = attendus - tags, tags - attendus
        detail = (f"{len(manquants)} manquant(s), {len(en_trop)} en trop"
                  if (manquants or en_trop) else f"{len(tags)} present(s)")
        checks.append(("tags", not (manquants or en_trop), detail))
    return checks


def is_conform(info, target, opts, tags=None):
    """Vrai si le fichier est deja dans l'etat vise (utilise par --skip-done)."""
    return all(ok for _, ok, _ in verify(info, target, opts, tags))


def write(path, info, target, opts, tmdb):
    """Ecrit tout dans le .mkv en un seul appel mkvpropedit. Retourne (code, message).

    Un dossier temporaire sert de cwd pour referencer tags.xml / cover.jpg en
    relatif (evite les soucis de ':' dans les chemins Windows).
    """
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "tags.xml").write_text(target.tags_xml, encoding="utf-8")

        # --- Informations de segment : titre (+ date de sortie) ---
        cmd = ["mkvpropedit", str(path), "--edit", "info", "--set", f"title={target.title}"]
        if opts.date and target.date:
            cmd += ["--set", f"date={target.date}T00:00:00Z"]

        # --- Tags (+ statistiques de piste : debit, duree, nb images) ---
        cmd += ["--tags", "all:tags.xml"]
        if opts.stats:
            cmd += ["--add-track-statistics-tags"]

        # --- Pistes : nom + drapeau 'par defaut' (fusionnes par piste) ---
        audios, subs = track_selectors(info)
        primary = primary_audio_sel(audios) if opts.flags else None
        for sel, tr in audios:
            sets = []
            if opts.audio_names:
                nm = audio_track_name(tr)
                sets += ["--set", f"name={nm}"] if nm else (["--delete", "name"] if current_name(tr) else [])
            if opts.flags:
                sets += ["--set", f"flag-default={1 if sel == primary else 0}"]
            if sets:
                cmd += ["--edit", f"track:{sel}"] + sets
        for sel, tr in subs:
            sets = []
            if opts.sub_names:
                nm = subtitle_track_name(tr)
                sets += ["--set", f"name={nm}"] if nm else (["--delete", "name"] if current_name(tr) else [])
            if opts.flags:
                sets += ["--set", "flag-default=0"]   # aucun sous-titre par defaut ; forced inchange
            if sets:
                cmd += ["--edit", f"track:{sel}"] + sets

        # --- Jaquette embarquee ---
        if opts.cover and target.poster:
            try:
                tmdb.save_image(target.poster, opts.image_size, tmp / "cover.jpg")
                if has_cover(info):
                    cmd += ["--delete-attachment", "name:cover.jpg"]
                cmd += ["--attachment-name", "cover.jpg",
                        "--attachment-mime-type", "image/jpeg",
                        "--add-attachment", "cover.jpg"]
            except TmdbError as e:
                print(f"      jaquette ignoree ({e})")

        res = run_tool(cmd, cwd=tmp)
        return res.returncode, ((res.stdout or "") + (res.stderr or "")).strip()
