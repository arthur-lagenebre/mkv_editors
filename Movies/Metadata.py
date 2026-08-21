#!/usr/bin/env python3
r"""
Metadata.py — Etiquette des films .mkv a partir de TMDB (donnees en francais via l'API).

Meme principe que TV_Shows/Metadata.py, mais pour les films : pas de saisons/episodes,
et l'association se fait par RECHERCHE TMDB sur le titre + l'annee extraits du nom.

Ecrit DIRECTEMENT dans chaque .mkv (sans re-encodage ni remux) :
  - le titre et la DATE de sortie dans les informations de segment
    (sortie du pays de --language : fr-FR -> sortie francaise, pas la sortie d'origine)
  - le synopsis, le realisateur, les scenaristes, le casting, les genres (tags)
  - les tags de STATISTIQUES de piste (debit, duree, nb d'images)  [--no-stats]
  - l'affiche du film comme jaquette (attachment "cover.jpg")
  - le nom des pistes AUDIO       -> codec + canaux + debit (ex. "E-AC-3 5.1 640 kb/s")
  - le nom des pistes SOUS-TITRES -> uniquement les drapeaux actifs (Forced, SDH...), ou "Full"
  - les DRAPEAUX 'par defaut'     -> une seule piste audio par defaut (la FR), aucun sous-titre

Dependances EXTERNES (dans le PATH) : mkvpropedit + mkvmerge + mkvextract (MKVToolNix),
ffprobe (FFmpeg).
Aucune dependance pip. Necessite Internet (API TMDB + jaquettes).
Le code partage avec les autres scripts du depot vit dans mkvlib/ (a la racine).

Cle TMDB (par priorite) : fichier .env a la racine du depot (TMDB_KEY=...)  >  variable
d'env TMDB_API_KEY  >  constante TMDB_KEY.
(Le meme .env sert a tous les scripts du depot : la cle n'est ecrite qu'une fois.)

Structure attendue : soit un sous-dossier par film (les .mkv dedans), soit des .mkv a plat
dans --dir. Le titre et l'annee sont lus dans le nom (dossier ou fichier), ex. "Inception (2010)".
Un prefixe d'ordre de saga "{n} - " est detecte et retire pour la recherche ("1 - Iron Man"
-> recherche "Iron Man") ; l'ordre est inscrit comme numero dans la collection (tag PART_NUMBER).
Si la recherche se trompe sur un titre, epingle l'identifiant dans le nom du dossier -
"Dune (2021) [tmdbid-438631]" ou "Dune {tmdb-438631}" - il sera respecte a chaque passage.

Usage :
  python Metadata.py --dir "D:\Films"                         # simulation (n'ecrit rien)
  python Metadata.py --dir "D:\Films" --apply                 # applique
  python Metadata.py --dir "D:\Films\Inception (2010)" --tmdb-id 27205 --apply   # force l'id (1 film)
  python Metadata.py --dir "D:\Films" --verify                # verifie seulement

Options : --apply --verify --skip-done --artwork --recap --no-tag
          --no-cover --no-date --no-audio-names --no-sub-names --no-flags --no-stats
          --tmdb-id (force, si un seul film) --language (defaut fr-FR) --image-size (w780)

--recap genere une fiche HTML de la mediatheque a la racine de --dir : mur d'affiches
groupe par saga, avec les films qui MANQUENT a chaque saga (TMDB en connait la
composition). Fichier unique, les affiches sont encodees dedans. --no-tag genere les
annexes sans rien modifier dans les .mkv.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # pour importer mkvlib
from mkvlib import artwork, cli, embed, lookup, mkv, naming        # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError, TmdbError, release_region   # noqa: E402

# ============================================================================
# Cle API TMDB : colle-la ici entre les guillemets pour ne plus avoir a la
# retaper. Priorite : .env > env TMDB_API_KEY > ceci.
TMDB_KEY = ""
# ============================================================================


# ----------------------------------------------------------------------------
# 1. Detection des films sur le disque
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


# ----------------------------------------------------------------------------
# 2. Etat vise pour un film (TargetTypeValue 50 = film, 70 = collection/saga)
# ----------------------------------------------------------------------------
def build_movie_tags_xml(movie, max_actors=20):
    credits = movie.get("credits", {})
    blocks = []

    collection = (movie.get("belongs_to_collection") or {}).get("name")
    if collection:
        lines = [mkv.simple("TITLE", collection)]
        if movie.get("_order"):                       # ordre de la saga (prefixe "{n} - ")
            lines.append(mkv.simple("PART_NUMBER", movie["_order"]))
        blocks.append(mkv.tag_block(70, lines))

    lines = [mkv.simple("TITLE", movie.get("title", ""))]
    if movie.get("overview"):
        lines.append(mkv.simple("SYNOPSIS", movie["overview"]))
        lines.append(mkv.simple("SUMMARY", movie["overview"]))
    if movie.get("release_date"):
        lines.append(mkv.simple("DATE_RELEASED", movie["release_date"]))
    lines += mkv.credits_lines(credits.get("crew", []), credits.get("cast", []), max_actors)
    genres = ", ".join(g.get("name", "") for g in movie.get("genres", []))
    if genres:
        lines.append(mkv.simple("GENRE", genres))
    blocks.append(mkv.tag_block(50, lines))
    return mkv.tags_document(blocks)


def movie_target(movie, opts):
    """Ce que le .mkv de ce film devrait contenir."""
    return mkv.Target(
        title=movie.get("title", ""),
        date=movie.get("release_date"),
        tags_xml=build_movie_tags_xml(movie),
        poster=movie.get("poster_path") if opts.cover else None,
    )


# ----------------------------------------------------------------------------
# 3. Traitement d'un film
# ----------------------------------------------------------------------------
def process_movie(folder, path, movie, args, opts, tmdb, foldered):
    """Traite un film et retourne son Report."""
    report = mkv.Report(matched=1, total=1)
    # Les tags ne sont relus que si on doit les comparer : un processus de plus.
    lecture = mkv.inspect(path, args.probe, with_tags=args.verify or args.skip_done)
    info, tags = lecture.info, lecture.tags
    if lecture.note:
        print(f"      {lecture.note}")
    target = movie_target(movie, opts)

    if args.verify:
        diffs = [(lbl, det) for lbl, ok, det in mkv.verify(info, target, opts, tags) if not ok]
        for lbl, det in diffs:
            print(f"      [DIFF] {lbl} : actuel = {det!r}")
        if diffs:
            report.diffs = 1
        else:
            print("      [OK] deja conforme")
        return report

    if opts.date and target.date:
        origine = (f" (sortie {movie['_date_region']})" if movie.get("_date_region")
                   else " (sortie d'origine)")
        print(f"      date -> {target.date}{origine}")
    for line in mkv.track_preview_lines(info, opts):
        print(line)

    if args.apply:
        if args.skip_done and mkv.is_conform(info, target, opts, tags):
            print("      [SKIP] deja a jour")
        else:
            code, msg = mkv.write(path, info, target, opts, tmdb)
            if code:
                report.failures = 1
            print(f"      [{'OK' if code == 0 else 'ECHEC'}]" + (f" {msg}" if code else ""))

    if args.artwork and foldered:
        poster = artwork.english_poster(
            lambda: tmdb.movie(movie["id"], artwork.ARTWORK_LANG),
            movie.get("poster_path"))
        print(f"      affiche (EN) : {artwork.write_poster(poster, folder, args.apply, tmdb)}")
    return report


def resolve_movie(rawname, args, tmdb, single):
    """Trouve le film TMDB correspondant a un nom de dossier/fichier, ou None."""
    pinned, rawname = naming.extract_tmdb_id(rawname)
    title, year, order = naming.parse_title_year(rawname)
    if args.tmdb_id and single:
        movie_id = args.tmdb_id
    elif pinned:
        movie_id = pinned          # annonce plus bas, une fois le titre connu
    else:
        try:
            results = tmdb.search_movie(title, year)
            if not results and year:
                results = tmdb.search_movie(title, None)
        except TmdbError as e:
            print(f"  echec recherche TMDB : {e}\n")
            return None
        if not results:
            print(f"  [NON ASSOCIE] recherche '{title}'"
                  + (f" ({year})" if year else "") + " -> aucun resultat\n")
            return None
        best, notes = lookup.pick_result(results, title)
        movie_id = best["id"]
        print(f"  recherche : '{title}'" + (f" ({year})" if year else "")
              + (f" [ordre {order}]" if order else "")
              + f" -> {lookup.describe(best)}")
        for note in notes:
            print(f"  /!\\ {note}")

    try:
        movie = tmdb.movie(movie_id)
    except TmdbError as e:
        print(f"  echec details TMDB : {e}\n")
        return None
    if pinned and movie_id == pinned:
        print(f"  id epingle dans le nom : {lookup.describe(movie)}")
    movie["_order"] = order

    # Sortie nationale (fr-FR -> FR) : sans ca, TMDB donne la sortie d'origine.
    region = release_region(args.language)
    local = (tmdb.local_release_date(movie_id, region)
             if region and not args.no_date else None)
    if local:
        movie["release_date"], movie["_date_region"] = local, region
    return movie


# ----------------------------------------------------------------------------
# 5. Fiche recap de la mediatheque
# ----------------------------------------------------------------------------
@dataclass
class Card:
    """Une vignette de la fiche : un film possede, ou un film qui manque a une saga."""
    title: str
    date: str = ""
    poster: str | None = None
    owned: bool = True
    runtime: int | None = None
    overview: str = ""

    @property
    def year(self):
        return (self.date or "")[:4]


def _card(movie, owned=True):
    return Card(title=movie.get("title", ""),
                date=movie.get("release_date") or "",
                poster=movie.get("poster_path"),
                owned=owned,
                runtime=movie.get("runtime"),
                overview=movie.get("overview") or "")


def fetch_collections(movies, tmdb):
    """{id de saga: composition TMDB} pour les sagas des films trouves."""
    sagas = {}
    for movie in movies:
        info = movie.get("belongs_to_collection") or {}
        ident = info.get("id")
        if ident is None or ident in sagas:
            continue
        try:
            sagas[ident] = tmdb.collection(ident)
        except TmdbError as e:
            print(f"  [recap] saga '{info.get('name')}' ignoree : {e}")
    return sagas


def library_sections(movies, sagas):
    """[(titre de section, [Card, ...]), ...] : une section par saga, puis le reste.

    Une saga apparait avec TOUS ses films - ceux qu'on possede et les autres -
    dans l'ordre de sortie : c'est ce qui rend visible ce qui manque a la collection.
    """
    owned = {m.get("id"): m for m in movies}
    sections, classes = [], set()
    for ident, saga in sorted(sagas.items(), key=lambda kv: kv[1].get("name", "")):
        cards = []
        for part in sorted(saga.get("parts", []), key=lambda p: p.get("release_date") or "9999"):
            mine = owned.get(part.get("id"))
            cards.append(_card(mine, True) if mine else _card(part, False))
            if mine:
                classes.add(part.get("id"))
        if cards:
            sections.append((saga.get("name", "Saga"), cards))
    seuls = [m for m in movies if m.get("id") not in classes]
    if seuls:
        sections.append(("Hors saga",
                         [_card(m) for m in sorted(seuls, key=lambda m: m.get("title", ""))]))
    return sections


def collect_posters(sections, size):
    """{cle: chemin TMDB} pour toutes les affiches de la fiche."""
    needed = {}
    for _, cards in sections:
        for card in cards:
            key = embed.image_key(card.poster, size)
            if key:
                needed[key] = card.poster
    return needed


def build_recap_html(library_name, sections, posters, size):
    """Rend la fiche HTML (pur rendu : ni reseau ni disque).

    Les films manquants d'une saga sont grises et etiquetes, comme les episodes
    absents dans la fiche d'une serie."""
    def esc(s):
        return escape(str(s or ""))

    blocs = []
    total = manquants = 0
    for titre, cards in sections:
        possedes = sum(1 for c in cards if c.owned)
        total += possedes
        manquants += len(cards) - possedes
        compteur = (f"<span class='cnt'>{possedes}/{len(cards)}</span>"
                    if len(cards) != possedes else "")
        vignettes = []
        for card in cards:
            key = embed.image_key(card.poster, size)
            uri = posters.get(key) if key else None
            img = embed.tag(key, uri) if uri else "<div class='noimg'></div>"
            duree = f" · {card.runtime} min" if card.runtime else ""
            manque = "<div class='miss'>manquant</div>" if not card.owned else ""
            vignettes.append(
                f"<div class='film{'' if card.owned else ' absent'}' "
                f"title='{esc(card.overview)}'>"
                f"<div class='aff'>{img}{manque}</div>"
                f"<div class='t'>{esc(card.title)}</div>"
                f"<div class='y'>{esc(card.year)}{duree}</div>"
                "</div>")
        blocs.append(f"<section><h2>{esc(titre)}{compteur}</h2>"
                     f"<div class='grid'>{''.join(vignettes)}</div></section>")

    sagas = sum(1 for titre, _ in sections if titre != "Hors saga")
    resume = f"{total} film(s)" + (f" · {sagas} saga(s)" if sagas else "")
    if manquants:
        resume += f" · {manquants} manquant(s) dans les sagas"

    return (
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
        f"<meta name='poster-size' content='{esc(size)}'>"
        f"<title>{esc(library_name)}</title>"
        "<style>"
        "body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#14151a;color:#e8e8ea}"
        ".wrap{max-width:1180px;margin:0 auto;padding:32px}"
        "h1{margin:0 0 4px}.sub{color:#9aa0aa;margin-bottom:28px}"
        "h2{font-size:17px;margin:30px 0 14px;padding-bottom:8px;"
        "border-bottom:1px solid #21232b}"
        ".cnt{margin-left:9px;font-size:13px;font-weight:400;color:#9aa0aa;"
        "font-variant-numeric:tabular-nums}"
        ".grid{display:grid;gap:18px;grid-template-columns:repeat(auto-fill,minmax(148px,1fr))}"
        ".film .aff{position:relative;aspect-ratio:2/3;border-radius:8px;overflow:hidden;"
        "background:#21232b}"
        ".film img,.film .noimg{width:100%;height:100%;object-fit:cover;display:block}"
        ".film .t{margin-top:8px;font-size:14px;font-weight:600;line-height:1.3}"
        ".film .y{color:#9aa0aa;font-size:13px}"
        ".film.absent{opacity:.42}"
        ".miss{position:absolute;left:6px;bottom:6px;padding:2px 8px;border-radius:999px;"
        "font-size:11px;text-transform:uppercase;letter-spacing:.04em;"
        "background:#3a2a2e;color:#ff9aa6}"
        "</style></head><body><div class='wrap'>"
        f"<h1>{esc(library_name)}</h1>"
        f"<div class='sub'>{esc(resume)}</div>"
        f"{''.join(blocs)}"
        "</div></body></html>"
    )


def write_recap(root_dir, movies, args, tmdb):
    """Ecrit recap.html a la racine de --dir. Ne telecharge rien en simulation."""
    apply = args.apply and not args.verify
    out = Path(root_dir) / "recap.html"
    print("--- annexes ---")
    sections = library_sections(movies, fetch_collections(movies, tmdb))
    needed = collect_posters(sections, args.poster_size)
    posters = (embed.fetch(needed, embed.read_embedded(out), args.poster_size,
                           tmdb, label="affiche") if apply else {})
    html = build_recap_html(Path(root_dir).resolve().name, sections, posters, args.poster_size)
    if not apply:
        print(f"  [mediatheque] ecrirait {out.name}")
        return
    out.write_text(html, encoding="utf-8")
    print(f"  [mediatheque] {out.name} ecrit  "
          f"({len(html) / 1_048_576:.1f} Mo, {len(posters)} affiche(s) integree(s))")


# ----------------------------------------------------------------------------
# 6. Programme principal
# ----------------------------------------------------------------------------
def parse_args():
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
    ap.add_argument("--no-tag", action="store_true",
                    help="Ne modifie aucun film ; genere seulement folder.jpg / recap")
    ap.add_argument("--artwork", action="store_true", help="Ecrit folder.jpg (affiche EN) par film")
    ap.add_argument("--recap", action="store_true",
                    help="Genere une fiche recap HTML de la mediatheque (sagas et manquants)")
    ap.add_argument("--poster-size", default="w185",
                    help="Taille TMDB des affiches du recap (defaut : w185)")
    ap.add_argument("--image-size", default="w780", help="Taille TMDB : w300 / w780 / original")
    return ap.parse_args()


def main():
    args = parse_args()
    cli.setup_console()
    args.probe = mkv.check_tools(needs_mkvtoolnix=not args.no_tag)
    opts = mkv.Options.from_args(args)
    tmdb = Tmdb(cli.resolve_tmdb_key(TMDB_KEY), args.language, user_agent="movies_mkv/1.0")

    print(f"=== {cli.mode_label(args)} ===   source : TMDB {args.language}\n")

    movies, foldered = find_movies(args.dir)
    if not movies:
        print(f"Aucun .mkv trouve dans : {args.dir}")
        return
    if args.tmdb_id and len(movies) > 1:
        print(f"Note : --tmdb-id ne s'applique qu'a un seul film ; {len(movies)} detectes "
              "-> id ignore, recherche par nom.\n")
    if args.no_tag and not (args.artwork or args.recap):
        print("Astuce : --no-tag sans --artwork ni --recap ne produit rien. "
              "Ajoute --artwork et/ou --recap.\n")
    if args.artwork and not foldered:
        print("Note : --artwork sans effet ici (les .mkv sont a plat, pas un dossier par film).\n")

    report, resolved = mkv.Report(), []
    for folder, path, rawname in movies:
        print(f"--- {path.name} ---")
        movie = resolve_movie(rawname, args, tmdb, single=len(movies) == 1)
        if movie is None:
            report += mkv.Report(total=1)
            continue
        resolved.append(movie)
        if args.no_tag:
            print("      film non modifie (--no-tag)")
            report += mkv.Report(matched=1, total=1)
        else:
            report += process_movie(folder, path, movie, args, opts, tmdb, foldered)
        print()

    print(f"TOTAL : {report.matched}/{report.total} film(s) associe(s).")
    if args.recap and resolved:
        write_recap(args.dir, resolved, args, tmdb)
    reste = report.epilogue()
    if reste:
        print(f"\nA CORRIGER : {reste}.")
    return report.exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
