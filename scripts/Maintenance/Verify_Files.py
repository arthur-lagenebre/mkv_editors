#!/usr/bin/env python3
r"""
Verify_Files.py - Contrôle la structure des .mkv d'un dossier, et les répare au besoin.

Un film peut se lire du début à la fin et avoir le conteneur abîmé : la vidéo sort, mais la chaîne des éléments Matroska déraille quelque part - typiquement une fin de fichier écrite à moitié après une coupure.
Ça se manifeste par un "erreur dans la structure du fichier Matroska à la position ..." que mkvpropedit répète à chaque passage, et par rien d'autre.

Rien n'est demandé à MKVToolNix pour juger la structure : mesure faite sur des fichiers volontairement cassés, mkvmerge (même en démultiplexant tout vers NUL) et mkvinfo se resynchronisent en silence et rendent 0.
Seul mkvpropedit proteste, mais il ÉCRIT dans le fichier et laisse passer une troncature. La chaîne est donc suivie directement (mkvlib/ebml.py), en LECTURE SEULE.

Deux profondeurs, parce qu'une lecture au hasard coûte ~70 ms sur un partage réseau et qu'un film de 9 Go compte 2500 clusters :
  (défaut)   les points de repère - l'index SeekHead, la table Cues, la queue du fichier. Le prix ne dépend pas de la taille du film : ~0,5 s de structure, plus la lecture d'en-tête par mkvmerge qui coûte le double.
             Mesure faite, 489 films et 4,49 To en 15 minutes. Attrape la troncature, l'index qui ment et les dégâts de fin de fichier, c'est-à-dire ce qui arrive vraiment.
  --full     toute la chaîne, contenu des clusters compris. Il faut lire le fichier entier : compter ~30 s par gigaoctet sur un partage réseau, donc à réserver à un dossier plutôt qu'a toute une médiathèque.
             Voit tout ce que voit mkvpropedit, plus ce qu'il rate.

--repair remultiplexe les fichiers en défaut : mkvmerge relit le film et le réécrit proprement à côté, le résultat est contrôle à son tour, et l'original n'est remplacé que s'il ressort sain.
Rien ne se perd au passage - identifiant TMDB, jaquette, titre du segment, nom des pistes et statistiques sont recopiés. Au moindre échec l'original reste en place.
Il faut la place d'un film de plus sur le volume, le temps du remux.

Usage :
  python Verify_Files.py --dir "\\Asgard\films"                 # contrôle rapide
  python Verify_Files.py --dir "\\Asgard\films\Ghibli" --full   # contrôle intégral
  python Verify_Files.py --dir "\\Asgard\films" --repair        # contrôle puis répare
  python Verify_Files.py --dir "\\Asgard\films" --no-recursive  # cet étage seul

--no-recursive s'en tient aux .mkv posés directement dans --dir, sans descendre dans les sous-dossiers : de quoi contrôler l'étage d'une médiathèque - les films posés à plat - sans relire les dossiers qu'elle range.

Options : --full --repair --no-recursive --log (défaut : vérification.log à la racine de --dir)

Dépendance EXTERNE : mkvmerge (MKVToolNix). Aucune dépendance pip, aucun accès réseau : ce script ne parle qu'aux fichiers.
"""

import argparse
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # pour importer mkvlib
from mkvlib import cli, ebml  # noqa: E402
from mkvlib.mkv import identify, run_tool  # noqa: E402

LARGEUR = 100        # ligne de progression : de quoi tenir dans un terminal étroit


def parse_args():
    p = argparse.ArgumentParser(description="Controle la structure des .mkv d'un dossier (recursivement par defaut).")
    p.add_argument("--dir", required=True, help="dossier a controler")
    p.add_argument("--full", action="store_true", help="suit toute la chaine, clusters compris (lit chaque fichier en entier : ~30 s par Go)")
    p.add_argument("--repair", action="store_true", help="remultiplexe les fichiers en defaut et remplace l'original si le resultat est sain")
    p.add_argument("--no-recursive", action="store_true", help="ne controle que les .mkv poses directement dans --dir, sans descendre dans les sous-dossiers")
    p.add_argument("--log", default=None, help="fichier journal (defaut : verification.log a la racine de --dir)")
    return p.parse_args()


def mkv_files(racine, recursif=True):
    """Les .mkv sous racine : ceux posés à la racine d'abord, puis dossier par dossier.

    Le même ordre que la passe de statistiques : on sait tout de suite si la racine est saine, et un dossier se contrôle d'un bloc. Sans récursion, seuls les films posés directement dans racine sont rendus - l'étage se contrôle sans relire les dossiers qu'il range.
    """
    fichiers = racine.rglob("*.mkv") if recursif else racine.glob("*.mkv")
    return sorted(fichiers, key=lambda p: (p.parent != racine, str(p.parent).lower(), p.name.lower()))


def readable_size(octets):
    """Taille lisible : on ne parle pas en téraoctets d'un dossier de trois films."""
    for unite, seuil in (("To", 1e12), ("Go", 1e9), ("Mo", 1e6)):
        if octets >= seuil:
            return f"{octets / seuil:.2f} {unite}"
    return f"{octets / 1e3:.0f} ko"


def readable_time(secondes):
    """Durée lisible, de la seconde à l'heure."""
    if secondes < 60:
        return f"{secondes:.0f} s"
    if secondes < 3600:
        return f"{secondes / 60:.1f} min"
    return f"{secondes / 3600:.1f} h"


def relative_name(chemin, racine):
    """Chemin affichable : ce qui distingue le fichier, sans le préfixe commun."""
    try:
        return str(chemin.relative_to(racine))
    except ValueError:                              # hors de la racine (lien, montage)
        return str(chemin)


def progress(fait, total, nom):
    """Ligne réécrite sur place. Muette hors terminal : redirigée, elle salirait.

    Le nom du fichier en cours compte autant que le compteur : un contrôle rapide passe une demi-seconde par film, mais un contrôle complet peut rester dix minutes sur le même, et il faut voir lequel.
    """
    try:
        if not sys.stdout.isatty():
            return
    except (AttributeError, ValueError):            # flux ferme ou remplace
        return
    ligne = f"  {fait}/{total}  {nom}"
    print(f"\r{ligne[:LARGEUR]:<{LARGEUR}}", end="", flush=True)


def clear_line():
    """Rend la ligne de progression au message qui va s'afficher à sa place."""
    try:
        if sys.stdout.isatty():
            print(f"\r{'':<{LARGEUR}}\r", end="")
    except (AttributeError, ValueError):
        pass


def first_error(resultat):
    """Première ligne d'erreur d'un outil externe, pour ne pas noyer le bilan."""
    for flux in (resultat.stderr, resultat.stdout):
        for ligne in (flux or "").splitlines():
            ligne = ligne.strip()
            if ligne.lower().startswith(("erreur", "error")):
                return ligne
    return ""


def check(chemin, complet):
    """Ce qui cloche dans le fichier, ou "" s'il est sain.

    La structure d'abord, parce qu'elle se lit sans sous-processus et qu'un conteneur rompu explique tout le reste ; la lisibilité par mkvmerge ensuite, qui reste le seul juge de ce qu'un lecteur saura ouvrir.
    """
    defaut = ebml.verifier(chemin, complet=complet)
    if defaut:
        return str(defaut)
    info, remarque = identify(chemin)
    if info is None:
        return remarque
    if not any(piste.get("type") == "video" for piste in info.get("tracks", [])):
        return "aucune piste video"
    return ""


def repair(chemin, complet):
    """(réussite, ce qui a manque). Remuxe à côté, contrôle, puis remplace.

    L'original n'est touche qu'a la toute fin, et seulement si le remux ressort sain : tant que le remplacement n'a pas eu lieu, un échec ne coûte qu'un fichier temporaire. mkvmerge rend 1 pour un simple avertissement - il a écrit quand même - et 2 ou plus quand il n'a rien pu produire.
    """
    neuf = chemin.with_name(chemin.stem + ".neuf.mkv")
    if neuf.exists():
        return False, f"un fichier {neuf.name} traine deja a cote"
    resultat = run_tool(["mkvmerge", "--quiet", "-o", str(neuf), str(chemin)])
    if resultat.returncode >= 2 or not neuf.exists():
        neuf.unlink(missing_ok=True)
        return False, first_error(resultat) or "mkvmerge n'a rien pu ecrire"
    reste = check(neuf, complet)
    if reste:
        neuf.unlink(missing_ok=True)
        return False, f"le remux est abime lui aussi ({reste})"
    try:
        os.replace(neuf, chemin)
    except OSError as exc:
        neuf.unlink(missing_ok=True)
        return False, f"remplacement impossible : {exc.strerror or exc}"
    return True, ""


def write_log(destination, racine, total, defauts, repares, echecs, complet, recursif=True):
    """Journal du passage : ce qui cloche, et ce qu'on en a fait."""
    lignes = [
        f"Verification du {datetime.now():%d/%m/%Y %H:%M} - {racine}",
        f"Controle {'complet (chaine entiere)' if complet else 'rapide (points de repere)'}{'' if recursif else ', sans les sous-dossiers'}",
        f"{total} fichier(s) controle(s), {len(defauts)} en defaut.",
        "",
    ]
    if not defauts:
        lignes.append("Aucun defaut de structure.")
    for chemin, message in defauts:
        nom = relative_name(chemin, racine)
        etat = "repare" if nom in repares else ("REPARATION ECHOUEE" if nom in echecs else "non repare")
        lignes += [nom, f"    {message}", f"    -> {etat}", ""]
    if echecs:
        lignes += ["", "REPARATIONS ECHOUEES (originaux intacts) :"]
        lignes += [f"  {nom}" for nom in echecs]
    try:
        destination.write_text("\n".join(lignes) + "\n", encoding="utf-8")
        print(f"[log] {destination} ecrit")
    except OSError as exc:
        print(f"[log] impossible d'ecrire {destination} : {exc.strerror or exc}")


def main():
    cli.setup_console()
    args = parse_args()
    racine = Path(args.dir)
    if not racine.is_dir():
        sys.exit(f"Dossier introuvable : {racine}")
    if shutil.which("mkvmerge") is None:
        sys.exit("mkvmerge est introuvable dans le PATH.\n"
                 "  Installe MKVToolNix : winget install MoritzBunkus.MKVToolNix")

    fichiers = mkv_files(racine, recursif=not args.no_recursive)
    total = len(fichiers)
    portee = "dans" if args.no_recursive else "sous"
    if not total:
        sys.exit(f"Aucun .mkv {portee} {racine}")
    octets = sum(f.stat().st_size for f in fichiers)
    print(f"{total} fichier(s), {readable_size(octets)} {portee} {racine}" + (" (sous-dossiers ignores)" if args.no_recursive else ""))
    print("Controle " + ("complet : toute la chaine, clusters compris "
                         f"(~{readable_time(octets / 1e9 * 30)} de lecture)"
                         if args.full else
                         "rapide : index, table Cues et queue de fichier"))

    defauts = []
    debut = time.time()
    for n, chemin in enumerate(fichiers, 1):
        progress(n, total, relative_name(chemin, racine))
        message = check(chemin, args.full)
        if message:
            clear_line()
            print(f"  [DEFAUT] {relative_name(chemin, racine)}")
            print(f"           {message}")
            defauts.append((chemin, message))
    clear_line()

    repares, echecs = [], []
    if args.repair and defauts:
        print(f"\nReparation de {len(defauts)} fichier(s) : remux, controle du "
              "resultat, puis remplacement.")
        for n, (chemin, _) in enumerate(defauts, 1):
            nom = relative_name(chemin, racine)
            print(f"  [{n}/{len(defauts)}] {nom} ...", end="", flush=True)
            reussi, souci = repair(chemin, args.full)
            print(" repare" if reussi else f" ECHEC : {souci}")
            (repares if reussi else echecs).append(nom)

    duree = time.time() - debut
    print(f"\nTOTAL : {total} fichier(s) controle(s) en {readable_time(duree)}, "
          f"{len(defauts)} en defaut"
          + (f", {len(repares)} repare(s), {len(echecs)} echec(s)" if args.repair else "")
          + ".")
    if defauts and not args.repair:
        print("Relance la meme commande avec --repair pour les remultiplexer.")
    if not args.full and not defauts:
        print("Le controle rapide ne juge que l'index et la queue : --full "
              "descend dans les clusters.")

    write_log(Path(args.log) if args.log else racine / "verification.log", racine, total, defauts, repares, echecs, args.full, not args.no_recursive)
    return 1 if echecs or (defauts and not args.repair) else 0


if __name__ == "__main__":
    sys.exit(main())
