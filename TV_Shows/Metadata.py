#!/usr/bin/env python3
r"""
Metadata.py — Etiquette les .mkv d'une serie a partir de TMDB (donnees en francais).

Ecrit DIRECTEMENT dans chaque .mkv (sans re-encodage ni remux, c'est quasi instantane) :
  - le titre et la DATE de sortie dans les informations de segment
  - le synopsis, les numeros saison/episode, realisateur(s), scenariste(s), casting (tags)
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
Le code partage avec les autres scripts du depot vit dans mkvlib/ (a la racine).

Installation des outils (Windows) :
  winget install MoritzBunkus.MKVToolNix
  winget install Gyan.FFmpeg

Cle TMDB gratuite : themoviedb.org -> Parametres -> API. Fournie de 3 facons (par priorite) :
  1) fichier .env a la racine du depot (TMDB_KEY=...)   2) variable d'env TMDB_API_KEY
  3) constante TMDB_KEY en haut du fichier

Usage — pointe --dir sur la RACINE de la serie (dossiers "Saison N") :

  # Simulation (n'ecrit rien) puis application :
  python Metadata.py --dir "...\Secret Level"
  python Metadata.py --dir "...\Secret Level" --apply

  # Verification (lecture seule) : rapporte ce qui n'est pas encore conforme.
  python Metadata.py --dir "...\Secret Level" --verify

  # La serie est identifiee par recherche sur le nom du dossier ; --tmdb-id ne
  # sert qu'a corriger une recherche qui se trompe ou ne trouve rien :
  python Metadata.py --dir "...\Secret Level" --tmdb-id 261579 --apply

Structure attendue : un sous-dossier "Saison N" par saison (les .mkv dedans), chaque .mkv
prefixe par son numero d'episode ("01 - ...", "05 - ..."). Si --dir pointe directement sur
un dossier de saison, seule celle-ci est traitee.

Options principales :
  --tmdb-id STR    identifiant TMDB (defaut : recherche sur le nom du dossier)
  --language STR   langue TMDB (defaut : fr-FR)
  --series-name STR  force le nom de serie (sinon auto depuis TMDB)
  --apply          applique reellement (defaut : simulation)
  --verify         verifie seulement (aucune ecriture)
  --skip-done      saute les fichiers deja conformes
  --no-tag         ne modifie aucun episode ; genere seulement folder.jpg / recap (series non-MKV)
  --no-cover / --no-date / --no-audio-names / --no-sub-names / --no-flags / --no-stats
  --artwork        ecrit folder.jpg (vignette de dossier) en anglais (serie et chaque saison)
  --recap          genere une fiche recap HTML de la serie (onglets par saison)
                   -> fichier UNIQUE : les vignettes sont encodees dedans, rien a cote
  --image-size STR taille TMDB jaquette / folder.jpg : w300 / w780 / original (defaut : w780)
  --still-size STR taille TMDB des vignettes du recap (defaut : w300)
"""

import argparse
import base64
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # pour importer mkvlib
from mkvlib import artwork, cli, lookup, mkv, naming              # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError, TmdbError            # noqa: E402

# ============================================================================
# Cle API TMDB : colle-la ici entre les guillemets pour ne plus avoir a la
# retaper. Priorite : .env > env TMDB_API_KEY > ceci.
TMDB_KEY = ""
# ============================================================================


# ----------------------------------------------------------------------------
# 1. Etat vise pour un episode : tags Matroska + titre + date + jaquette
#    TargetTypeValue : 70 = COLLECTION (serie), 60 = SEASON, 50 = EPISODE
# ----------------------------------------------------------------------------
def build_tags_xml(season, ep, series_name, max_actors=20):
    blocks = []
    if series_name:
        blocks.append(mkv.tag_block(70, [mkv.simple("TITLE", series_name)]))

    blocks.append(mkv.tag_block(60, [
        mkv.simple("PART_NUMBER", season.get("season_number", "")),
        mkv.simple("TITLE", season.get("name", "")),
        mkv.simple("TOTAL_PARTS", len(season.get("episodes", []))),
    ]))

    lines = [mkv.simple("TITLE", ep.get("name", "")),
             mkv.simple("PART_NUMBER", ep.get("episode_number", ""))]
    if ep.get("overview"):
        lines.append(mkv.simple("SYNOPSIS", ep["overview"]))
        lines.append(mkv.simple("SUMMARY", ep["overview"]))
    if ep.get("air_date"):
        lines.append(mkv.simple("DATE_RELEASED", ep["air_date"]))
    lines += mkv.credits_lines(ep.get("crew", []), ep.get("guest_stars", []), max_actors)
    if ep.get("vote_average"):
        lines.append(mkv.simple(
            "COMMENT",
            f"TMDB {round(ep['vote_average'], 1)}/10 ({ep.get('vote_count', 0)} votes)"))
    blocks.append(mkv.tag_block(50, lines))
    return mkv.tags_document(blocks)


def episode_target(season, ep, series_name, opts):
    """Ce que le .mkv de cet episode devrait contenir."""
    return mkv.Target(
        title=ep.get("name", ""),
        date=ep.get("air_date"),
        tags_xml=build_tags_xml(season, ep, series_name),
        poster=(ep.get("still_path") or season.get("poster_path")) if opts.cover else None,
    )


# ----------------------------------------------------------------------------
# 2. Traitement d'une saison
# ----------------------------------------------------------------------------
def build_plan(mkv_dir, season, args, opts):
    """[(fichier, episode|None, methode, avertissement, info_mkvmerge|None), ...]"""
    episodes = season.get("episodes", [])
    by_num = {e.get("episode_number"): e for e in episodes}
    plan = []
    for f in sorted(Path(mkv_dir).glob("*.mkv")):
        f = f.resolve()
        num = naming.detect_episode_number(f.name)
        if num in by_num:
            ep, method = by_num[num], f"n.{num:02d} (depuis le nom)"
        else:
            ep, score = naming.best_title_match(f.name, episodes)
            method = f"titre (~{score:.0%})"
            if score < args.match_threshold:
                ep = None

        warn, info = "", None
        if ep:
            info = mkv.identify(f)              # pistes + pieces jointes (lecture seule)
            if info and args.probe:
                probe = mkv.annotate_bitrates(info, f)   # debits pour le nom des pistes
                dmin, runtime = probe.duration_min, ep.get("runtime")
                if dmin and runtime and abs(dmin - runtime) > 3:
                    warn = f"duree {dmin:.0f}min vs {runtime}min attendues -> a verifier"
        plan.append((f, ep, method, warn, info))
    return plan


def process_season(mkv_dir, season, args, opts, tmdb):
    """Construit le plan d'une saison, l'affiche, et applique si --apply.
    Retourne (nb_associes, nb_fichiers)."""
    plan = build_plan(mkv_dir, season, args, opts)
    if not plan:
        print(f"  Aucun .mkv dans {mkv_dir}")
        return 0, 0

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
        target = episode_target(season, ep, args.series_name, opts)
        if args.verify:                     # mode verification : etat actuel vs vise
            diffs = [(lbl, det) for lbl, ok, det in mkv.verify(info, target, opts) if not ok]
            for lbl, det in diffs:
                print(f"      [DIFF] {lbl} : actuel = {det!r}")
            if not diffs:
                print("      [OK] deja conforme")
            continue
        if opts.date and target.date:
            print(f"      date segment -> {target.date}")
        for line in mkv.track_preview_lines(info, opts):
            print(line)

    if args.apply and not args.verify:
        print("  --- ecriture ---")
        for f, ep, _, _, info in plan:
            if ep is None:
                continue
            target = episode_target(season, ep, args.series_name, opts)
            if args.skip_done and mkv.is_conform(info, target, opts):
                print(f"  [SKIP] {f.name} (deja a jour)")
                continue
            code, msg = mkv.write(f, info, target, opts, tmdb)
            print(f"  [{'OK' if code == 0 else 'ECHEC'}] {f.name}" + (f"  -> {msg}" if code else ""))

    print(f"  => {matched}/{len(plan)} associe(s).")
    return matched, len(plan)


# ----------------------------------------------------------------------------
# 3. Fiche recap HTML : vignettes encodees dans la page
# ----------------------------------------------------------------------------
# Les vignettes du recap sont encodees en base64 DANS le HTML : la fiche est un
# fichier unique, deplacable et partageable tel quel, sans dossier d'images a cote.
STILL_MIME = "image/jpeg"
# Cle de cache d'une vignette = taille TMDB + chemin TMDB (ex. "w300/aBc123.jpg").
# Le chemin TMDB change des que l'image change, donc l'invalidation est automatique.
STILL_KEY_RE = re.compile(r"^[\w./-]+$")
# Retrouve les vignettes deja encodees dans un recap.html precedent.
EMBEDDED_RE = re.compile(r"<img data-still='([^']+)' src='(data:[^']+)'")


def still_key(still_path, size):
    key = f"{size}{still_path}"
    return key if STILL_KEY_RE.match(key) else None


def read_embedded_stills(recap_path):
    """Relit les vignettes encodees dans un recap.html existant.

    C'est le cache : regenerer la fiche ne retelecharge que les vignettes nouvelles
    ou modifiees, sans qu'aucun fichier annexe n'ait a etre conserve sur le disque."""
    try:
        html = Path(recap_path).read_text(encoding="utf-8")
    except OSError:
        return {}
    return dict(EMBEDDED_RE.findall(html))


def collect_stills(seasons, size):
    """Retourne {cle: chemin TMDB} pour toutes les vignettes d'episode disponibles."""
    needed = {}
    for _, season in seasons:
        for ep in season.get("episodes", []):
            still = ep.get("still_path")
            key = still_key(still, size) if still else None
            if key:
                needed[key] = still
    return needed


def fetch_stills(needed, cached, size, tmdb, workers=8):
    """Resout {cle: chemin TMDB} en {cle: data-URI}.

    Reprend ce que le recap existant contenait deja et telecharge le reste en
    parallele (une serie longue = des centaines de vignettes : en sequentiel, chaque
    image paie son propre aller-retour TLS)."""
    stills = {k: cached[k] for k in needed if k in cached}
    todo = sorted((k, p) for k, p in needed.items() if k not in stills)
    if not todo:
        if stills:
            print(f"  [recap] {len(stills)} vignette(s) reprise(s) de la fiche existante")
        return stills

    print(f"  [recap] {len(todo)} vignette(s) a telecharger"
          + (f", {len(stills)} reprise(s) de la fiche existante" if stills else ""))

    def grab(item):
        key, path = item
        try:
            raw = tmdb.image(path, size)
        except TmdbError as e:
            print(f"      vignette ignoree ({path}) : {e}")
            return key, None
        return key, f"data:{STILL_MIME};base64," + base64.b64encode(raw).decode("ascii")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, uri in pool.map(grab, todo):
            if uri:
                stills[key] = uri
    return stills


def build_recap_html(series_name, show, seasons, tmdb_id, stills, size):
    """Rend la page HTML (pur rendu : ni reseau ni disque).

    'stills' = {cle: data-URI} ; un episode sans vignette disponible recoit un
    emplacement vide plutot qu'une balise <img> sans source."""
    def esc(s):
        return escape(str(s or ""))

    tabs, panels = [], []
    for i, (num, season) in enumerate(seasons):
        label = season.get("name") or f"Saison {num}"
        tabs.append(f"<button class='tab{' active' if i == 0 else ''}' data-s='{num}'>{esc(label)}</button>")
        cards = []
        for ep in season.get("episodes", []):
            still = ep.get("still_path")
            key = still_key(still, size) if still else None
            uri = stills.get(key) if key else None
            img = (f"<img data-still='{key}' src='{uri}' alt='' decoding='async' loading='lazy'>"
                   if uri else "<div class='noimg'></div>")

            rt = f" · {ep['runtime']} min" if ep.get("runtime") else ""
            cards.append(
                "<div class='ep'>"
                + img
                + "<div class='meta'>"
                + f"<div><span class='n'>{ep.get('episode_number', 0):02d}</span> "
                + f"<span class='t'>{esc(ep.get('name'))}</span></div>"
                + f"<div class='d'>{esc(naming.fr_date(ep.get('air_date')))}{rt}</div>"
                + f"<div class='o'>{esc(ep.get('overview'))}</div>"
                + "</div></div>")
        panels.append(f"<section class='season' data-s='{num}'{'' if i == 0 else ' hidden'}>"
                      f"{''.join(cards)}</section>")

    return (
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
        f"<meta name='tmdb-id' content='{esc(tmdb_id)}'>"
        f"<meta name='still-size' content='{esc(size)}'>"
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
        ".ep img,.ep .noimg{width:160px;height:90px;object-fit:cover;border-radius:8px;"
        "background:#21232b;flex:none}"
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


def _write_text(path, text, apply):
    if not apply:
        return f"ecrirait {Path(path).name}"
    Path(path).write_text(text, encoding="utf-8")
    return f"{Path(path).name} ecrit"


def generate_sidecars(root_dir, series_name, show, processed, args, tmdb):
    """Ecrit posters de dossier (affiche EN) / fiche recap selon les options.
    processed = [(dossier_saison, numero, donnees_saison), ...]."""
    if not (args.artwork or args.recap):
        return
    apply = args.apply and not args.verify
    print("--- annexes ---")

    if args.artwork:
        for folder, num, season in processed:
            poster = artwork.english_poster(
                lambda: tmdb.season(args.tmdb_id, num, artwork.ARTWORK_LANG),
                season.get("poster_path"))
            print(f"  [saison {num}] affiche (EN) : "
                  f"{artwork.write_poster(poster, folder, apply, tmdb)}")
        poster = artwork.english_poster(
            lambda: tmdb.series(args.tmdb_id, artwork.ARTWORK_LANG),
            show.get("poster_path"))
        print(f"  [serie] affiche (EN) : {artwork.write_poster(poster, root_dir, apply, tmdb)}")

    if args.recap:
        out = Path(root_dir) / "recap.html"
        seasons = [(n, s) for _, n, s in processed]
        needed = collect_stills(seasons, args.still_size)
        # En simulation on ne telecharge rien : la page est rendue sans vignette.
        stills = (fetch_stills(needed, read_embedded_stills(out), args.still_size, tmdb)
                  if apply else {})
        html = build_recap_html(series_name, show, seasons, args.tmdb_id, stills, args.still_size)
        print(f"  [serie] {_write_text(out, html, apply)}"
              + (f"  ({len(html) / 1_048_576:.1f} Mo, {len(stills)} vignette(s) integree(s))"
                 if stills else ""))
        legacy = Path(root_dir) / "assets"
        if legacy.is_dir():
            print(f"  [serie] note : le dossier '{legacy.name}' n'est plus utilise "
                  "(vignettes desormais integrees au HTML) -> tu peux le supprimer")


# ----------------------------------------------------------------------------
# 4. Programme principal
# ----------------------------------------------------------------------------
def parse_args():
    ap = argparse.ArgumentParser(
        description="Etiquette des .mkv depuis TMDB (donnees en francais via l'API).")
    ap.add_argument("--dir", required=True,
                    help="Racine de la serie (dossiers 'Saison N') OU un seul dossier de saison")
    # --- Source TMDB ---
    ap.add_argument("--tmdb-id", help="Identifiant TMDB de la serie "
                    "(par defaut : recherche sur le nom du dossier)")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--series-name", help="Force le nom de serie (sinon recupere automatiquement de TMDB)")
    # --- Ce qu'on ecrit ---
    ap.add_argument("--apply", action="store_true", help="Applique reellement (defaut : simulation)")
    ap.add_argument("--verify", action="store_true", help="Verifie seulement l'etat des fichiers (aucune ecriture)")
    ap.add_argument("--skip-done", action="store_true", help="Saute les fichiers deja conformes")
    ap.add_argument("--no-tag", action="store_true",
                    help="Ne modifie aucun episode ; genere seulement folder.jpg / recap (series non-MKV)")
    ap.add_argument("--no-cover", action="store_true", help="N'embarque pas la jaquette")
    ap.add_argument("--no-date", action="store_true", help="Ne modifie pas la date du segment")
    ap.add_argument("--no-audio-names", action="store_true", help="Ne renomme pas les pistes audio")
    ap.add_argument("--no-sub-names", action="store_true", help="Ne renomme pas les pistes de sous-titres")
    ap.add_argument("--no-flags", action="store_true", help="Ne touche pas aux drapeaux 'par defaut'")
    ap.add_argument("--no-stats", action="store_true", help="N'ajoute pas les tags de statistiques de piste")
    # --- Generateurs annexes (option, ecrivent des fichiers a cote des .mkv) ---
    ap.add_argument("--artwork", action="store_true", help="Ecrit folder.jpg (vignette de dossier) en anglais")
    ap.add_argument("--recap", action="store_true", help="Genere une fiche recap HTML de la serie")
    ap.add_argument("--image-size", default="w780",
                    help="Taille TMDB de la jaquette embarquee / folder.jpg : w300 / w780 / original")
    ap.add_argument("--still-size", default="w300",
                    help="Taille TMDB des vignettes du recap (defaut : w300 ; w400 = plus net "
                         "sur ecran HiDPI mais fiche plus lourde)")
    ap.add_argument("--match-threshold", type=float, default=0.55,
                    help="Score minimal pour une association par titre (0-1)")
    return ap.parse_args()


def main():
    args = parse_args()
    cli.setup_console()
    args.probe = mkv.check_tools(needs_mkvtoolnix=not args.no_tag)
    opts = mkv.Options.from_args(args)
    tmdb = Tmdb(cli.resolve_tmdb_key(TMDB_KEY), args.language, user_agent="tag_mkv/1.0")

    # La banniere d'abord : la recherche de serie s'affiche dessous, pas avant.
    print(f"=== {cli.mode_label(args)} ===")
    args.tmdb_id = lookup.resolve_show_id(tmdb, args.dir, args.tmdb_id)
    if args.tmdb_id is None:
        sys.exit("Serie non identifiee : relance avec --tmdb-id.")

    # Details de la serie via TMDB (nom auto, + poster/synopsis pour les annexes)
    try:
        show = tmdb.series(args.tmdb_id)
    except TmdbError as e:
        sys.exit(f"Echec de l'appel TMDB (serie) : {e}")
    args.series_name = args.series_name or show.get("name", "")

    print(f"    serie : {args.series_name}   |   source : TMDB {args.language} (id {args.tmdb_id})\n")

    if args.no_tag and not (args.artwork or args.recap):
        print("Astuce : --no-tag sans --artwork ni --recap ne produit rien. "
              "Ajoute --artwork et/ou --recap.\n")

    seasons = naming.find_seasons(args.dir)
    if seasons:
        # --- Multi-saisons : --dir est la racine de la serie ---
        total_m = total_f = 0
        processed = []
        for sub, num in seasons:
            print(f"--- {sub.name}  (TMDB saison {num}) ---")
            try:
                data = tmdb.season(args.tmdb_id, num)
            except TmdbError as e:
                print(f"  echec TMDB saison {num} : {e} -> saison ignoree\n")
                continue
            if args.no_tag:
                print("  episodes non modifies (--no-tag)")
            else:
                m, tot = process_season(sub, data, args, opts, tmdb)
                total_m += m
                total_f += tot
            processed.append((sub, num, data))
            print()
        if not args.no_tag:
            print(f"TOTAL : {total_m}/{total_f} fichier(s) associe(s) "
                  f"sur {len(seasons)} saison(s) detectee(s).")
        generate_sidecars(args.dir, args.series_name, show, processed, args, tmdb)
    else:
        # --- Saison unique : --dir contient directement les .mkv ---
        num = naming.season_number(Path(args.dir).name) or 1
        try:
            data = tmdb.season(args.tmdb_id, num)
        except TmdbError as e:
            sys.exit(f"Echec de l'appel TMDB (saison {num}) : {e}")
        if args.no_tag:
            print("  episodes non modifies (--no-tag)")
        else:
            process_season(args.dir, data, args, opts, tmdb)
        generate_sidecars(args.dir, args.series_name, show,
                          [(Path(args.dir), num, data)], args, tmdb)


if __name__ == "__main__":
    try:
        main()
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
