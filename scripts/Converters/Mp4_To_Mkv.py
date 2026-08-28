#!/usr/bin/env python3
r"""
Mp4_To_Mkv.py - Remultiplexe les .mp4 en .mkv, sans réencodage, sous-titres compris.

Un .mp4 sait porter une jaquette et quelques tags, mais à la mode iTunes : Metadata.py écrit des tags Matroska, et n'a donc nulle part où les poser tant que le film reste dans ce conteneur. C'est la passe qui précède l'étiquetage : elle change le conteneur, et rien d'autre.

Rien n'est réencodé - les pistes sont recopiées telles quelles, à la vitesse du disque - donc l'image et le son ressortent identiques. Les chapitres suivent ; la jaquette et les tags iTunes, non : Metadata.py les réécrit ensuite, depuis TMDB.

Ce qui distingue ce passage de celui des .avi tient en une chose : un .mp4 déclare souvent la langue de ses pistes. Elle est alors gardée, et --lang ne sert qu'à celles qui n'en ont pas ("und") ; --force-lang réétiquette tout, pour un fichier qui se trompe. Un .avi, lui, ne dit jamais rien de ses langues, et il faut bien les lui donner.

Les sous-titres posés à côté sont embarqués au passage, et leur encodage est mesuré fichier par fichier : mkvmerge suppose de l'UTF-8, alors qu'un .srt d'époque est en général en windows-1252, et le malentendu ne se voit qu'aux accents cassés, souvent une fois l'original effacé. La langue est lue dans le suffixe du nom ("Film.fr.srt"), et un .sub est laissé à son .idx, qui l'embarque déjà. Ceux qui sont déjà DANS le mp4 (tx3g) sont recopiés sans qu'on ait rien à demander, et comptent pour --subs require.

La durée du .mkv produit est comparée à celle de la source : le moov d'un .mp4 ne ment pas comme un index AVI, mais il peut manquer, ou n'avoir jamais été écrit sur un fichier coupé en cours de copie - mkvmerge remultiplexe alors ce qu'il trouve, sans toujours s'en plaindre. --delete-source n'efface l'original qu'après ce contrôle, et réclame donc ffprobe.

Usage :
  python Mp4_To_Mkv.py --dir "D:\Films"                          # simulation
  python Mp4_To_Mkv.py --dir "D:\Films" --apply                  # convertit
  python Mp4_To_Mkv.py --dir "D:\Films" --apply --lang eng --sub-lang fre
  python Mp4_To_Mkv.py --dir "D:\Films" --apply --delete-source  # + efface le .mp4 verifie

Options : --apply --lang --force-lang --sub-lang --subs --sub-charset --default-audio --default-sub --title --output-dir --overwrite --tolerance --delete-source --no-recursive --log (défaut : conversion_mp4.log à la racine de --dir)

Dépendances EXTERNES : mkvmerge (MKVToolNix), et ffprobe (FFmpeg) pour le contrôle des durées. Aucune dépendance pip, aucun accès réseau : ce script ne parle qu'aux fichiers.
"""

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # pour importer mkvlib
from mkvlib import cli, naming  # noqa: E402
from mkvlib.mkv import first_error, identify, probe, run_tool  # noqa: E402

TEXT_SUB_EXT = {".srt", ".ass", ".ssa"}   # sous-titres texte : eux seuls ont un encodage
SUB_EXT = TEXT_SUB_EXT | {".idx"}         # un .sub n'est pas cité : mkvmerge le prend avec son .idx
MKVMERGE_WARNING = 1                      # mkvmerge : 1 = il a écrit malgré une remarque, 2+ = il n'a rien produit
NO_LANGUAGE = {"", "und"}                 # ce que mkvmerge répond d'une piste qui ne dit pas sa langue

# Suffixe de nom de fichier -> code ISO 639-2/B, le seul que mkvmerge accepte en --language.
LANG_HINTS = {
    "fr": "fre", "fre": "fre", "fra": "fre", "french": "fre", "vf": "fre", "vff": "fre", "vfq": "fre", "fr-fr": "fre", "en": "eng", "eng": "eng", "english": "eng", "vo": "eng", "vost": "eng",
    "es": "spa", "spa": "spa", "de": "ger", "ger": "ger", "deu": "ger", "it": "ita", "ita": "ita", "nl": "dut", "dut": "dut", "ja": "jpn", "jpn": "jpn", "pt": "por", "por": "por"
}


def parse_args():
    p = argparse.ArgumentParser(description="Remultiplexe les .mp4 d'un dossier en .mkv, sans reencodage (recursivement par defaut).")
    p.add_argument("--dir", required=True, help="dossier a convertir")
    p.add_argument("--apply", action="store_true", help="convertit reellement (defaut : simulation)")
    p.add_argument("--lang", default="fre", help="langue des pistes audio qui n'en declarent aucune, code ISO 639-2 a trois lettres (defaut : fre)")
    p.add_argument("--force-lang", action="store_true", help="impose --lang a toutes les pistes audio, y compris celles qui declarent deja une langue")
    p.add_argument("--sub-lang", default=None, help="langue d'un sous-titre dont le nom ne dit rien (defaut : celle de --lang)")
    p.add_argument("--subs", choices=("auto", "none", "require"), default="auto", help="auto : embarque les sous-titres poses a cote ; none : les ignore ; require : ne convertit que les films qui en ont, dedans ou a cote (defaut : auto)")
    p.add_argument("--sub-charset", default="windows-1252", help="encodage de repli des sous-titres texte qui ne sont pas en UTF-8 (defaut : windows-1252)")
    p.add_argument("--default-audio", action="store_true", help="marque la premiere piste audio comme piste par defaut")
    p.add_argument("--default-sub", action="store_true", help="marque le premier sous-titre pose a cote comme piste par defaut")
    p.add_argument("--title", action="store_true", help="inscrit le nom du fichier comme titre du segment")
    p.add_argument("--output-dir", default=None, help="dossier de sortie, ou les fichiers sont poses a plat (defaut : a cote de la source)")
    p.add_argument("--overwrite", action="store_true", help="ecrase un .mkv deja present au lieu de passer son tour")
    p.add_argument("--tolerance", type=float, default=2.0, help="ecart de duree tolere entre la source et le resultat, en secondes (defaut : 2.0)")
    p.add_argument("--delete-source", action="store_true", help="efface le .mp4 une fois la duree du .mkv verifiee (demande ffprobe)")
    p.add_argument("--no-recursive", action="store_true", help="ne convertit que les .mp4 poses directement dans --dir, sans descendre dans les sous-dossiers")
    p.add_argument("--log", default=None, help="fichier journal (defaut : conversion_mp4.log a la racine de --dir)")
    args = p.parse_args()
    if args.sub_lang is None:
        args.sub_lang = args.lang
    if args.output_dir:
        args.output_dir = Path(args.output_dir)
    if len(args.lang) != 3 or len(args.sub_lang) != 3:
        print("Attention : mkvmerge attend un code ISO 639-2 a trois lettres (fre, eng, ger...) ; "
              f"'{args.lang}' / '{args.sub_lang}' risque d'etre refuse.\n")
    return args


def mp4_files(root, recursive=True):
    """Les .mp4 sous root : ceux posés à la racine d'abord, puis dossier par dossier.

    Le même ordre que Verify_Files, pour qu'un passage se relise pareil d'un script à l'autre. L'extension est comparée en minuscules plutôt que filtrée par glob : un fichier sorti d'un caméscope ou d'un vieux téléphone écrit volontiers ".MP4", et sous Linux le glob ne le verrait pas.
    """
    found = root.rglob("*") if recursive else root.glob("*")
    files = (f for f in found if f.is_file() and f.suffix.lower() == ".mp4")
    return sorted(files, key=lambda p: (p.parent != root, str(p.parent).lower(), p.name.lower()))


def detect_charset(path, fallback):
    """Encodage d'un sous-titre texte : UTF-8 s'il l'est vraiment, sinon `fallback`.

    mkvmerge suppose de l'UTF-8 et n'a aucun moyen de se tromper bruyamment : un .srt en windows-1252 passe quand même, avec des accents remplacés par des losanges qu'on ne découvre qu'à la lecture du film. Le test est donc fait ici, en strict - un texte réellement UTF-8 ne peut pas échouer, un texte latin-1 accentué ne peut pas réussir.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return fallback
    if raw.startswith(b"\xef\xbb\xbf"):
        return "UTF-8"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "UTF-16"
    try:
        raw.decode("utf-8", errors="strict")
        return "UTF-8"
    except UnicodeDecodeError:
        return fallback


def guess_sub_language(sub, video, default):
    """Langue déduite du suffixe du nom : 'Film.fr.srt' -> fre, 'Film.srt' -> défaut."""
    suffix = sub.stem[len(video.stem):].strip(". _-").lower()
    return LANG_HINTS.get(suffix, default)


def find_subtitles(video):
    """Les sous-titres posés à côté de la vidéo, qui portent son nom.

    Un .sub est ignoré volontairement : en VobSub, c'est le .idx qui décrit les deux, et mkvmerge va chercher le .sub tout seul. Le citer aurait fait entrer la même piste deux fois.
    """
    return [f for f in naming.files_with_ext(video.parent, SUB_EXT)
            if f.stem == video.stem or f.stem.startswith(video.stem + ".")]


def tracks_of(info, kind):
    """Les pistes d'un type donné, dans l'ordre où mkvmerge les a lues."""
    return [t for t in (info or {}).get("tracks", []) if t.get("type") == kind]


def track_language(track):
    """Langue déclarée par une piste, ou "" quand elle n'en déclare pas.

    mkvmerge répond "und" pour une piste muette sur ce point, ce qui revient au même qu'un champ absent : les deux sont rendus ici comme une absence, pour qu'un seul test suffise ensuite.
    """
    language = (track.get("properties") or {}).get("language") or ""
    return "" if language in NO_LANGUAGE else language


def audio_languages(info, args):
    """([(id, langue à écrire)], [langues gardées]) pour les pistes audio.

    C'est là que ce script se sépare de son voisin Avi_To_Mkv : un .mp4 déclare souvent la langue de ses pistes, et un rip anglais correctement étiqueté ressortirait en français si on écrasait tout au passage. On n'écrit donc que sur les pistes qui ne disent rien - à moins que --force-lang ne tranche, pour un fichier qui se trompe.
    """
    to_write, kept = [], []
    for track in tracks_of(info, "audio"):
        language = track_language(track)
        if language and not args.force_lang:
            kept.append(language)
        else:
            to_write.append((track["id"], args.lang))
    return to_write, kept


def build_command(video, output, info, subs, args):
    """(commande mkvmerge, [(nom, langue, encodage)], [langues gardées]) pour un fichier.

    Une option mkvmerge s'applique au fichier qui la SUIT : chaque bloc de langue et d'encodage doit donc précéder son sous-titre. Les identifiants de piste sont ceux de 'mkvmerge -J', et non ceux de ffprobe, qui numérote autrement ; côté sous-titres posés à côté il n'y a qu'une piste par fichier, donc toujours 0.
    """
    command = ["mkvmerge", "--output", str(output)]

    to_write, kept = audio_languages(info, args)
    for track_id, language in to_write:
        command += ["--language", f"{track_id}:{language}"]
    audio_ids = [t["id"] for t in tracks_of(info, "audio")]
    if audio_ids and args.default_audio:
        command += ["--default-track-flag", f"{audio_ids[0]}:yes"]
    if args.title:
        command += ["--title", video.stem]
    command.append(str(video))

    attached = []
    for index, sub in enumerate(subs):
        language = guess_sub_language(sub, video, args.sub_lang)
        command += ["--language", f"0:{language}"]
        charset = None
        if sub.suffix.lower() in TEXT_SUB_EXT:
            charset = detect_charset(sub, args.sub_charset)
            command += ["--sub-charset", f"0:{charset}"]
        command += ["--default-track-flag", "0:yes" if (index == 0 and args.default_sub) else "0:no"]
        command.append(str(sub))
        attached.append((sub.name, language, charset))

    return command, attached, kept


def display_command(command):
    """La commande telle qu'on la retaperait, guillemets compris."""
    return " ".join(f'"{word}"' if " " in word else word for word in command)


def convert(video, args, check_duration):
    """(statut, détail) : ce qu'on a fait du fichier, et ce qu'il y a à en dire.

    Rien n'est affiché ici : c'est l'appelant qui imprime, et le journal reprend mot pour mot ce que le terminal a montré.
    """
    output = (args.output_dir or video.parent) / (video.stem + ".mkv")
    if output.exists() and not args.overwrite:
        return "ignore", f"{output.name} existe deja (--overwrite pour l'ecraser)"

    info, problem = identify(video)
    if info is None:
        return "erreur", problem
    if not tracks_of(info, "video"):
        return "erreur", "aucune piste video"

    subs = find_subtitles(video) if args.subs != "none" else []
    embedded = tracks_of(info, "subtitles")
    if args.subs == "require" and not subs and not embedded:
        return "ignore", "aucun sous-titre, ni dedans ni a cote (--subs require)"

    command, attached, kept = build_command(video, output, info, subs, args)
    note = "".join(f", {name} en {language}" + (f" ({charset})" if charset else "")
                   for name, language, charset in attached)
    if embedded:
        note += f", {len(embedded)} sous-titre(s) deja dans le mp4"
    if kept:
        note += ", langue(s) d'origine gardee(s) : " + ", ".join(sorted(set(kept)))
    if not args.apply:
        return "simule", display_command(command)

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    result = run_tool(command)
    if result.returncode > MKVMERGE_WARNING or not output.exists():
        return "erreur", first_error(result) or "mkvmerge n'a rien pu ecrire"
    if result.returncode == MKVMERGE_WARNING:
        note += ", avertissement mkvmerge"

    # Le moov d'un .mp4 dit la durée sans se tromper, mais un fichier coupé en cours de
    # copie n'en a pas : mkvmerge remultiplexe alors ce qu'il trouve, sans toujours le dire.
    if not check_duration:
        return "non verifie", f"converti, duree non controlee (ffprobe absent){note}"
    source, produced = probe(video).duration_min, probe(output).duration_min
    if source is None or produced is None:
        return "non verifie", f"converti, duree indeterminable{note}"
    gap = abs(source - produced) * 60
    if gap > args.tolerance:
        return "ecart", f"{gap:.1f} s de difference avec la source : {output.name} est suspect, l'original est garde"

    if args.delete_source:
        try:
            video.unlink()
            note += ", source effacee"
        except OSError as exc:
            note += f", source non effacee : {exc.strerror or exc}"
    return "converti", f"ecart {gap:.2f} s{note}"


def write_log(destination, root, entries, args, summary):
    """Journal du passage : un fichier par ligne, avec ce qu'on en a fait."""
    lines = [
        f"Conversion du {datetime.now():%d/%m/%Y %H:%M} - {root}",
        cli.mode_label(args, "aucune conversion") + ("" if not args.no_recursive else ", sans les sous-dossiers"),
        f"{len(entries)} fichier(s) .mp4 - {summary}",
        "",
    ]
    for name, status, detail in entries:
        lines += [name, f"    {status} : {detail}", ""]
    failed = [name for name, status, _ in entries if status in ("erreur", "ecart")]
    if failed:
        lines += ["", "NON CONVERTIS (originaux intacts) :"] + [f"  {name}" for name in failed]
    try:
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[log] {destination} ecrit")
    except OSError as exc:
        print(f"[log] impossible d'ecrire {destination} : {exc.strerror or exc}")


def main():
    cli.setup_console()
    args = parse_args()
    root = cli.check_dir(args.dir)
    if shutil.which("mkvmerge") is None:
        sys.exit("mkvmerge est introuvable dans le PATH.\n"
                 "  Installe MKVToolNix : winget install MoritzBunkus.MKVToolNix")
    check_duration = shutil.which("ffprobe") is not None
    if args.delete_source and not check_duration:
        sys.exit("--delete-source n'efface un .mp4 qu'apres avoir compare la duree du .mkv a la sienne,\n"
                 "et ffprobe est introuvable dans le PATH : installe FFmpeg (winget install Gyan.FFmpeg)\n"
                 "ou relance sans --delete-source.")
    if not check_duration:
        print("ffprobe absent -> la duree des fichiers produits ne sera pas controlee "
              "(winget install Gyan.FFmpeg)\n")

    files = mp4_files(root, recursive=not args.no_recursive)
    scope = "dans" if args.no_recursive else "sous"
    if not files:
        sys.exit(f"Aucun .mp4 {scope} {root}")
    print(f"=== {cli.mode_label(args, 'rien ne sera converti ; ajoute --apply')} ===")
    print(f"{len(files)} fichier(s) .mp4 {scope} {root}"
          + (" (sous-dossiers ignores)" if args.no_recursive else ""))
    print("Remultiplexage sans reencodage : l'image et le son sont recopies tels quels.")
    print("Les pistes qui declarent deja leur langue la gardent"
          + (" - sauf ici, ou --force-lang l'impose.\n" if args.force_lang else " (--force-lang pour l'imposer).\n"))

    entries, start = [], time.time()
    for n, path in enumerate(files, 1):
        name = naming.relative_name(path, root)
        print(f"  [{n}/{len(files)}] {name}")
        status, detail = convert(path, args, check_duration)
        mark = f"[{status.upper()}] " if status in ("erreur", "ecart") else ""
        print(f"          {mark}{detail}")
        entries.append((name, status, detail))

    counts = {}
    for _, status, _ in entries:
        counts[status] = counts.get(status, 0) + 1
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    elapsed = time.time() - start
    print(f"\nTOTAL : {len(entries)} fichier(s) en {elapsed / 60:.1f} min - {summary}.")
    if not args.apply:
        print("Simulation : rien n'a ete ecrit. Ajoute --apply pour convertir.")
    elif counts.get("converti") and not args.delete_source:
        print("Les .mp4 sont toujours a cote de leurs .mkv : etiquette-les avec "
              "Movies/Metadata.py, puis efface-les quand le resultat te convient.")

    write_log(Path(args.log) if args.log else root / "conversion_mp4.log", root, entries, args, summary)
    return 1 if counts.get("erreur") or counts.get("ecart") else 0


if __name__ == "__main__":
    sys.exit(main())
