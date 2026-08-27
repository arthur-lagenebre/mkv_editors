"""Lecture et écriture des fichiers .mkv (MKVToolNix + FFmpeg).

Films et épisodes ne différent que par les données à inscrire : le contenu visé est décrit par un `Target`, ce qu'on s'autorise à modifier par des `Options`, et la comparaison comme l'écriture sont communes.
"""

import json
import re
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

# Échecs possibles d'un outil externe : binaire absent, code de retour non nul, sortie illisible. Rattrapés ensemble, mais jamais en masquant tout le reste.
TOOL_FAILURES = (subprocess.SubprocessError, OSError, json.JSONDecodeError)


def run_tool(cmd, **kwargs):
    """Lance un outil externe et récupère sa sortie, décodée en UTF-8.

    Préciser l'encodage n'est pas un détail : mkvmerge et ffprobe écrivent leur JSON en UTF-8, alors que Python décoderait avec l'encodage local (cp1252 sous Windows). Il suffit d'un caractère absent de cp1252 dans un synopsis - le trait d'union typographique de "est-ce" en fournit un - pour que le décodage echoue dans un thread interne de subprocess : la sortie revient alors à None, sans erreur ni code de retour anormal, et le fichier devient illisible juste après avoir été étiqueté.
    """
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", **kwargs)


def first_error(result):
    """Première ligne d'erreur d'un outil externe, pour ne pas noyer le bilan.

    Les outils MKVToolNix racontent tout ce qu'ils ont fait avant d'échouer, en anglais ou en français selon la machine : ce qu'on veut afficher, c'est la ligne qui dit pourquoi.
    """
    for stream in (result.stderr, result.stdout):
        for line in (stream or "").splitlines():
            line = line.strip()
            if line.lower().startswith(("erreur", "error")):
                return line
    return ""


def _reason(exc):
    """Message court expliquant l'échec d'un outil externe."""
    if isinstance(exc, subprocess.CalledProcessError):
        err = (exc.stderr or "").strip().splitlines()
        return err[0] if err else f"code {exc.returncode}"
    return str(exc)


def check_tools(needs_mkvtoolnix=True):
    """Vérifie les outils externes. Retourne True si ffprobe est disponible.

    Sort du programme si MKVToolNix manque alors qu'on doit écrire dans des .mkv.
    """
    if not needs_mkvtoolnix:
        return False
    missing = [t for t in ("mkvpropedit", "mkvmerge", "mkvextract") if shutil.which(t) is None]
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

    Rien n'est affiché ici : la remarque est rendue à l'appelant, qui l'imprime au bon endroit. C'est ce qui permet de lire plusieurs fichiers en parallèle sans entrelacer les messages.
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
    """Durée ET débit de chaque piste audio, en UN seul appel ffprobe.

    Les deux informations venaient de deux appels distincts, soit deux processus lances par fichier là où un seul suffit.
    """
    try:
        out = run_tool(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)], check=True).stdout
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
    """Injecte les débits ffprobe dans les pistes audio de `info`, pour leur nom.

    Retourne le Probe complet (la durée sert à repérer un fichier tronqué).
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
    """{(niveau de cible, nom, valeur)} pour les tags de niveau film/saison/série.

    Les tags de PISTE sont écartés : ce sont les statistiques écrites par --add-track-statistics-tags, qui ne viennent pas de TMDB. Et un bloc sans TargetTypeValue vaut 50 : c'est la valeur par défaut de Matroska, que mkvpropedit omet à l'écriture - sans cette équivalence, un fichier fraîchement etiquete paraîtrait déjà différent de ce qu'on vient d'y écrire.
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
    """Tags déjà écrits dans le fichier, sous la forme rendue par parse_tags.

    mkvmerge -J ne donne pas leur contenu : il faut passer par mkvextract.
    """
    try:
        out = run_tool(["mkvextract", str(path), "tags", "-"], check=True).stdout
    except TOOL_FAILURES:
        return set()
    return parse_tags(out) if out else set()


# Identifiant TMDB, tel que Matroska le normalise dans ses "External Identifiers" : la valeur s'écrit "movie/1234". Inscrit dans le fichier, il survit au renommage et dispense les passages suivants de toute recherche - donc de toute erreur.
TMDB_TAG = "TMDB"
TMDB_VALUE_RE = re.compile(r"^\s*(?:movie/)?(\d+)\s*$", re.IGNORECASE)


def tmdb_value(movie_id):
    """Valeur normalisée du tag TMDB pour un film."""
    return f"movie/{movie_id}"


def tmdb_id(tags):
    """Identifiant TMDB lu dans les tags d'un fichier, ou None."""
    for _, name, value in tags or ():
        if (name or "").upper() == TMDB_TAG:
            found = TMDB_VALUE_RE.match(value or "")
            if found:
                return found.group(1)
    return None


def has_tags(info):
    """Le fichier declare-t-il des tags ? mkvmerge en donne le compte, pas le contenu.

    C'est ce qui rend la relecture abordable : un mkvextract de plus par fichier coûte autant qu'un ffprobe, et sur une médiathèque jamais étiquetée il n'y aurait rien à y lire.
    """
    return any((entry or {}).get("num_entries") for entry in ((info or {}).get("global_tags") or []))


READ_WORKERS = 8      # lectures simultanées : c'est de l'attente de sous-processus


@dataclass
class Reading:
    """Ce qu'on a pu lire d'un fichier, sans rien afficher."""
    info: dict | None = None
    probe: Probe = field(default_factory=Probe)
    tags: set | None = None        # None = non relus (lecture non demandee)
    note: str = ""


def inspect(path, with_probe=True, with_tags=False):
    """Lecture complète d'un fichier. Sans affichage : appelable en parallèle.

    Les tags sont relus quand on en a besoin (--verify, --skip-done), et quand le fichier en declare : ils portent alors peut-être l'identifiant TMDB, qui vaut mieux que n'importe quelle recherche. C'est un sous-processus de plus par fichier, mais seulement là où il y a quelque chose à lire.
    """
    info, note = identify(path)
    probe = annotate_bitrates(info, path) if (info and with_probe) else Probe()
    tags = read_tags(path) if (info and (with_tags or has_tags(info))) else None
    return Reading(info, probe, tags, note)


def progress(libelle, fait, total):
    """Ligne de progression réécrite sur place. Muette hors terminal.

    La lecture précède tout l'affichage : sur une médiathèque de 500 films, ça fait de longues minutes où le script à l'air fige. Redirige vers un fichier ou dans un CI, en revanche, un retour chariot ne ferait que salir la sortie.
    """
    try:
        if not sys.stdout.isatty():
            return
    except (AttributeError, ValueError):      # flux ferme ou remplace
        return
    print(f"\r  {libelle} : {fait}/{total}", end=("\n" if fait >= total else ""), flush=True)


def inspect_all(paths, with_probe=True, with_tags=False, workers=READ_WORKERS):
    """{chemin: Reading} pour plusieurs fichiers, lus en parallèle.

    Chaque fichier coûte deux à trois sous-processus qu'on passe son temps à attendre : une saison entière se lit en un seul de ces délais.
    """
    paths = list(paths)
    lire = lambda p: inspect(p, with_probe, with_tags)      # noqa: E731
    if len(paths) < 2:
        return {p: lire(p) for p in paths}
    with ThreadPoolExecutor(max_workers=min(workers, len(paths))) as pool:
        lectures = {}
        for chemin, lecture in zip(paths, pool.map(lire, paths)):
            lectures[chemin] = lecture
            progress("lecture des fichiers", len(lectures), len(paths))
        return lectures


# --------------------------------------------------------------------------
# Noms de pistes
# --------------------------------------------------------------------------
CHANNELS = {1: "1.0", 2: "2.0", 3: "2.1", 4: "4.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}

# Drapeaux "cochables" d'une piste de sous-titres -> étiquette à mettre dans le nom.
SUB_FLAGS = [("forced_track", "Forced"), ("flag_hearing_impaired", "SDH"), ("flag_visual_impaired", "AD"), ("flag_text_descriptions", "Text descriptions"), ("flag_commentary", "Commentary"), ("flag_original", "Original")]


def audio_track_name(track):
    """Nom "qualité" d'une piste audio : codec + canaux + débit."""
    p = track.get("properties", {})
    codec = (track.get("codec") or "").strip()
    ch = p.get("audio_channels")
    layout = CHANNELS.get(ch, f"{ch}ch" if ch else "")
    br = track.get("_bitrate_kbps")
    rate = f"{br} kb/s" if br else ""
    return " ".join(x for x in (codec, layout, rate) if x)


# Le nom d'une piste dit parfois ce que ses drapeaux taisent : "Français force" sur une piste dont flag-forced est absent, "English SDH" sur une piste qui ne se declare pas malentendante. La renommer d'après ses seuls drapeaux effacerait la dernière trace de l'information - on la remet donc là où elle appartient. {motif dans le nom: (drapeau lu par mkvmerge, propriété écrite par mkvpropedit)}
NAME_FLAGS = { re.compile(r"\bforc[eé]", re.IGNORECASE): ("forced_track", "flag-forced"), re.compile(r"\b(?:sdh|malentendants?)\b", re.IGNORECASE): ("flag_hearing_impaired", "flag-hearing-impaired") }


def flagged_from_name(subs, motif, cle):
    """Sélecteurs des sous-titres à marquer d'après leur NOM, langue par langue.

    Deux gardes, parce qu'un fichier ne doit jamais se retrouver avec deux pistes forcées dans la même langue - le lecteur en choisirait une au hasard : - rien dans une langue ou le drapeau est déjà posé. Là où il existe, la situation est déclarée, et ce n'est pas à un nom de la contredire ; - une seule piste par langue, la première rencontrée.
    """
    retenus = set()
    langues = {lang(tr).lower()[:2] for _, tr in subs if tr.get("properties", {}).get(cle)}
    for sel, tr in subs:
        code = lang(tr).lower()[:2]
        if code not in langues and motif.search(current_name(tr)):
            retenus.add(sel)
            langues.add(code)
    return retenus


def subtitle_targets(subs, opts):
    """[(sélecteur, piste, drapeaux à poser), ...] : l'état vise de chaque sous-titre.

    Poser un drapeau, c'est modifier des drapeaux : --no-flags s'en abstient, et le nom décrit alors le fichier tel qu'il est.
    """
    a_poser = {sel: set() for sel, _ in subs}
    if opts.flags:
        for motif, (cle, _) in NAME_FLAGS.items():
            for sel in flagged_from_name(subs, motif, cle):
                a_poser[sel].add(cle)
    return [(sel, tr, a_poser[sel]) for sel, tr in subs]


def subtitle_track_name(track, ajouts=()):
    """Drapeaux actifs d'une piste de sous-titres, ou 'Full' si elle n'en a aucun.

    `ajouts` sont les drapeaux qu'on s'apprete à poser d'après le nom : le nom visé décrit le fichier tel qu'il sera, pas tel qu'il est.
    """
    p = track.get("properties", {})
    labels = " ".join(label for key, label in SUB_FLAGS if p.get(key) or key in ajouts)
    return labels or "Full"


def track_selectors(info):
    """(audios, subs) : listes de (sélecteur mkvpropedit, piste), numérotées par type."""
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
    """Piste audio à marquer 'par défaut' : la française, sinon la première."""
    for sel, tr in audios:
        if is_french(tr):
            return sel
    return audios[0][0] if audios else None


def has_cover(info):
    return any(a.get("file_name", "").lower() == "cover.jpg" for a in (info or {}).get("attachments", []))


# --------------------------------------------------------------------------
# Tags Matroska (XML attendu par mkvpropedit)
#   TargetTypeValue : 70 = COLLECTION (série, saga), 60 = SEASON, 50 = FILM/ÉPISODE
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
    return ["  <Tag>", f"    <Targets><TargetTypeValue>{target_type}</TargetTypeValue></Targets>", *lines, "  </Tag>"]


def tags_document(blocks):
    """Assemble des blocs <Tag> en un document XML complet."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Tags>"]
    for block in blocks:
        lines += block
    lines.append("</Tags>")
    return "\n".join(lines)


def credits_lines(crew, cast, max_actors=20):
    """Lignes <Simple> DIRECTOR / WRITTEN_BY / ACTOR à partir des crédits TMDB."""
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
# Comparaison et écriture
# --------------------------------------------------------------------------
@dataclass
class Options:
    """Ce qu'on s'autorise à modifier : miroir des drapeaux --no-* de la ligne de commande."""
    cover: bool = True
    date: bool = True
    audio_names: bool = True
    sub_names: bool = True
    flags: bool = True
    stats: bool = True
    image_size: str = "w780"

    @classmethod
    def from_args(cls, args):
        return cls(cover=not args.no_cover, date=not args.no_date, audio_names=not args.no_audio_names, sub_names=not args.no_sub_names, flags=not args.no_flags, stats=not args.no_stats, image_size=args.image_size)


@dataclass
class Report:
    """Ce qu'un traitement a donné. Additionnable pour totaliser une série.

    `diffs` et `failures` sont ce qui reste à corriger : ils décident du code de sortie, pour qu'un script sache si le passage s'est bien termine.
    """
    matched: int = 0        # fichiers associes à une fiche TMDB
    total: int = 0          # fichiers vus
    diffs: int = 0          # fichiers non conformes (--verify)
    failures: int = 0       # écritures en échec
    skipped: int = 0        # laissés de côté : pistes ambiguës, décision humaine
    pending: int = 0        # associations restées à confirmer

    def __add__(self, other):
        return Report(self.matched + other.matched, self.total + other.total, self.diffs + other.diffs, self.failures + other.failures, self.skipped + other.skipped, self.pending + other.pending)

    @property
    def exit_code(self):
        return 1 if (self.diffs or self.failures or self.skipped or self.pending) else 0

    def epilogue(self):
        """Ligne finale à afficher quand quelque chose n'est pas passé."""
        restes = []
        if self.diffs:
            restes.append(f"{self.diffs} fichier(s) non conforme(s)")
        if self.skipped:
            restes.append(f"{self.skipped} non traite(s) pour pistes ambigues")
        if self.pending:
            restes.append(f"{self.pending} association(s) a confirmer")
        if self.failures:
            restes.append(f"{self.failures} ecriture(s) en echec")
        return " ; ".join(restes)


@dataclass
class Target:
    """État vise pour un fichier : ce que TMDB dit qu'il devrait contenir."""
    title: str
    date: str | None = None        # "AAAA-MM-JJ"
    tags_xml: str = ""
    poster: str | None = None      # chemin TMDB de la jaquette à embarquer


def track_preview_lines(info, opts):
    """Lignes d'aperçu (simulation) des renommages de pistes pour un fichier."""
    lines = []
    audios, subs = track_selectors(info)
    primary = primary_audio_sel(audios) if opts.flags else None
    if opts.audio_names:
        for sel, tr in audios:
            mark = " (defaut)" if sel == primary else ""
            lines.append(f"      audio {sel} [{lang(tr)}]{mark} : {current_name(tr) or '(vide)'!r} -> {audio_track_name(tr) or '(vide)'!r}")
    if opts.sub_names:
        for sel, tr, ajouts in subtitle_targets(subs, opts):
            p = tr.get("properties", {})
            flags = [label for key, label in SUB_FLAGS if p.get(key)]
            etat = ",".join(flags) or "aucun"
            if ajouts:
                venus = [label for key, label in SUB_FLAGS if key in ajouts]
                etat += " +" + ",".join(venus) + " (d'apres le nom)"
            lines.append(f"      st {sel} [{lang(tr)}] drapeaux={etat} : {current_name(tr) or '(vide)'!r} -> {subtitle_track_name(tr, ajouts)!r}")
    return lines


def track_conflicts(info, opts):
    """Ce qui empêche d'étiqueter ce fichier sans y perdre quelque chose.

    Renommer une piste d'après ses drapeaux suppose que les drapeaux disent tout. Quand c'est faux, le renommage efface ce que le nom était seul à porter, et personne ne s'en aperçoit : deux sous-titres français nommés 'Full', et le forcé qui n'existe plus. Plutôt que de trancher à la place de quelqu'un, on dit ce qu'on a vu et on ne touche à rien.
    """
    audios, subs = track_selectors(info)
    cibles = subtitle_targets(subs, opts)
    raisons = []

    if opts.sub_names:
        for motif, (cle, _) in NAME_FLAGS.items():
            deja = [sel for sel, tr in subs if tr.get("properties", {}).get(cle)]
            for sel, tr, ajouts in cibles:
                if cle in ajouts or tr.get("properties", {}).get(cle):
                    continue
                if motif.search(current_name(tr)):
                    cause = f"st {deja[0]} porte deja le drapeau" if deja else "--no-flags"
                    raisons.append(f"st {sel} : le nom l'annonce ({current_name(tr)!r}) mais {cause} -> le nom serait efface")

    # Deux pistes de même langue et de même nom vise : après coup, plus rien ne les distingue - ni pour un lecteur, ni pour celui qui rouvrira le fichier.
    vises = {}
    if opts.audio_names:
        for sel, tr in audios:
            vises.setdefault(("audio", lang(tr).lower()[:2], audio_track_name(tr)), []).append(sel)
    if opts.sub_names:
        for sel, tr, ajouts in cibles:
            vises.setdefault(("st", lang(tr).lower()[:2], subtitle_track_name(tr, ajouts)), []).append(sel)
    for (genre, code, nom), sels in vises.items():
        if len(sels) > 1:
            pistes = " et ".join(f"{genre} {s}" for s in sels)
            raisons.append(f"{pistes} [{code}] : meme nom vise {nom!r}")
    return raisons


def verify(info, target, opts, tags=None):
    """Compare l'état actuel du .mkv à l'état vise. [(label, ok, detail_actuel), ...]

    `tags` vient de read_tags ; à None, les tags ne sont pas comparés - c'est le cas quand on ne les a pas relus.
    """
    checks = []
    cont = (info or {}).get("container", {}).get("properties", {})
    checks.append(("titre", cont.get("title") == target.title, cont.get("title") or "(absent)"))
    if opts.date and target.date:
        cur = (cont.get("date_utc") or cont.get("date_local") or "")[:10]
        checks.append(("date", cur == target.date, cur or "(absente)"))
    if opts.cover and target.poster:
        # Sans jaquette disponible cote TMDB, en exiger une signalerait un écart que rien ne peut combler (et --skip-done ne sauterait plus jamais rien).
        present = has_cover(info)
        checks.append(("jaquette", present, "presente" if present else "absente"))
    audios, subs = track_selectors(info)
    if opts.audio_names:
        for sel, tr in audios:
            checks.append((f"audio {sel}", current_name(tr) == audio_track_name(tr), current_name(tr) or "(vide)"))
    cibles = subtitle_targets(subs, opts)
    if opts.sub_names:
        for sel, tr, ajouts in cibles:
            checks.append((f"st {sel}", current_name(tr) == subtitle_track_name(tr, ajouts), current_name(tr) or "(vide)"))
    for sel, tr, ajouts in cibles:
        # Un drapeau à poser est un écart : sans ça, --skip-done sauterait le fichier et le nom serait le seul à porter l'information, encore.
        for key, label in SUB_FLAGS:
            if key in ajouts:
                checks.append((f"st {sel} {label.lower()}", False, "absent (le nom l'annonce)"))
    if tags is not None and target.tags_xml:
        attendus = parse_tags(target.tags_xml)
        manquants, en_trop = attendus - tags, tags - attendus
        detail = (f"{len(manquants)} manquant(s), {len(en_trop)} en trop" if (manquants or en_trop) else f"{len(tags)} present(s)")
        checks.append(("tags", not (manquants or en_trop), detail))
    return checks


def is_conform(info, target, opts, tags=None):
    """Vrai si le fichier est déjà dans l'état vise (utilise par --skip-done)."""
    return all(ok for _, ok, _ in verify(info, target, opts, tags))


def write(path, info, target, opts, tmdb):
    """Écrit tout dans le .mkv en un seul appel mkvpropedit. Retourne (code, message).

    Un dossier temporaire sert de cwd pour référencer tags.xml / cover.jpg en relatif (évite les soucis de ':' dans les chemins Windows).
    """
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "tags.xml").write_text(target.tags_xml, encoding="utf-8")

        # --- Informations de segment : titre (+ date de sortie) ---
        cmd = ["mkvpropedit", str(path), "--edit", "info", "--set", f"title={target.title}"]
        if opts.date and target.date:
            cmd += ["--set", f"date={target.date}T00:00:00Z"]

        # --- Tags (+ statistiques de piste : débit, durée, nb images) ---
        cmd += ["--tags", "all:tags.xml"]
        if opts.stats:
            cmd += ["--add-track-statistics-tags"]

        # --- Pistes : nom + drapeau 'par défaut' (fusionnés par piste) ---
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
        for sel, tr, ajouts in subtitle_targets(subs, opts):
            sets = []
            if opts.sub_names:
                nm = subtitle_track_name(tr, ajouts)
                sets += ["--set", f"name={nm}"] if nm else (["--delete", "name"] if current_name(tr) else [])
            if opts.flags:
                sets += ["--set", "flag-default=0"]   # aucun sous-titre par défaut
                for motif, (cle, propriete) in NAME_FLAGS.items():
                    if cle in ajouts:   # sinon les drapeaux restent inchangés
                        sets += ["--set", f"{propriete}=1"]
            if sets:
                cmd += ["--edit", f"track:{sel}"] + sets

        # --- Jaquette embarquee ---
        if opts.cover and target.poster:
            try:
                tmdb.save_image(target.poster, opts.image_size, tmp / "cover.jpg")
                if has_cover(info):
                    cmd += ["--delete-attachment", "name:cover.jpg"]
                cmd += ["--attachment-name", "cover.jpg", "--attachment-mime-type", "image/jpeg", "--add-attachment", "cover.jpg"]
            except TmdbError as e:
                print(f"      jaquette ignoree ({e})")

        try:
            res = run_tool(cmd, cwd=tmp)
        except TOOL_FAILURES as e:
            # check_tools vérifie mkvpropedit au démarrage, mais un PATH qui change en cours de route ne doit pas produire une pile d'appels : c'est un échec d'écriture comme un autre, déjà compté par le Report.
            return 1, f"mkvpropedit inutilisable ({_reason(e)})"
        return res.returncode, ((res.stdout or "") + (res.stderr or "")).strip()
