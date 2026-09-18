#!/usr/bin/env python3
r"""
Metadata.py — Étiquette les .mkv d'une série à partir de TMDB (données en français).

Écrit DIRECTEMENT dans chaque .mkv (sans re-encodage ni remux, c'est quasi instantané) :
  - le titre et la DATE de sortie dans les informations de segment
  - le synopsis, les numéros saison/épisode, réalisateur(s), scénariste(s), casting (tags)
  - les tags de STATISTIQUES de piste (débit, durée, nb d'images)  [--no-stats pour désactiver]
  - la vignette de l'épisode comme jaquette (attachment "cover.jpg")
  - le nom des pistes AUDIO       -> codec + canaux + débit (ex. "E-AC-3 5.1 640 kb/s"), suivi de AD ou Commentary quand la piste porte le drapeau malvoyant ou commentaire
  - le nom des pistes SOUS-TITRES -> uniquement les drapeaux actifs (Forced, SDH...), ou "Full"
  - les DRAPEAUX 'par défaut'     -> une seule piste audio par défaut (la FR, jamais une audiodescription ni un commentaire), aucun sous-titre
  - les drapeaux FORCED et SDH    -> posés sur un sous-titre dont le NOM les annonce ("Français force", "English SDH") alors que le drapeau manque - sinon, le renommer d'après ses seuls drapeaux effacerait l'information. Une seule piste par langue, et rien dans une langue qui declare déjà le drapeau.  [--no-flags]

=> Fichier 100% autonome : toutes les métadonnées voyagent avec le .mkv.

Dépendances EXTERNES (à avoir dans le PATH) :
  - mkvpropedit, mkvmerge et mkvextract   -> paquet MKVToolNix
  - ffprobe                   -> paquet FFmpeg  (pour le débit audio + la vérif. des durées)

Aucune dépendance pip. Necessite un accès Internet (API TMDB + jaquettes). Le code partage avec les autres scripts du dépôt vit dans mkvlib/ (à la racine).

Installation des outils (Windows) :
  winget install MoritzBunkus.MKVToolNix
  winget install Gyan.FFmpeg

Clé TMDB gratuite : themoviedb.org -> Paramètres -> API. À mettre dans le fichier .env à la racine du dépôt, sur une ligne TMDB_KEY=... : c'est la seule source.

Usage — pointe --dir sur la RACINE de la série (dossiers "Saison N") :

  # Simulation (n'écrit rien) puis application :
  python Metadata.py --dir "...\Secret Level"
  python Metadata.py --dir "...\Secret Level" --apply

  # Vérification (lecture seule) : rapporte ce qui n'est pas encore conforme.
  python Metadata.py --dir "...\Secret Level" --verify

  # La série est identifiée par recherche sur le nom du dossier ; --tmdb-id ne
  # sert qu'a corriger une recherche qui se trompe ou ne trouve rien :
  python Metadata.py --dir "...\Secret Level" --tmdb-id 261579 --apply

Structure attendue : un sous-dossier "Saison N" par saison (les .mkv dedans), chaque .mkv préfixe par son numéro d'épisode ("01 - ...", "05 - ...").
Si --dir pointe directement sur un dossier de saison, seule celle-ci est traitée. Un dossier "Specials" vaut la saison 0.
L'identifiant TMDB peut être épinglé dans le nom du dossier de la série, sous la forme "Ma Série [tmdbid-1396]" : plus besoin de --tmdb-id aux passages suivants.

Options principales :
  --tmdb-id STR    identifiant TMDB (défaut : recherche sur le nom du dossier)
  --language STR   langue TMDB (défaut : fr-FR)
  --no-cache       ignore le cache des réponses TMDB (garde 7 jours) et le rafraîchit
  --series-name STR  force le nom de série (sinon auto depuis TMDB)
  --apply          applique réellement (défaut : simulation)
  --verify         vérifie seulement (aucune écriture)
  --skip-done      saute les fichiers déjà conformes
  --no-tag         ne modifie aucun épisode ; genere seulement folder.jpg / récap (séries non-MKV)
  --no-cover / --no-date / --no-audio-names / --no-sub-names / --no-flags / --no-stats
  --artwork        écrit folder.jpg (vignette de dossier) en anglais (série et chaque saison)
  --recap          genere une fiche récap HTML de la série (onglets par saison)
                   -> fichier UNIQUE : les vignettes sont encodées dedans, rien à côté
                   -> les épisodes absents du disque sont grises et comptes par saison
                   -> un épisode dont TMDB n'a pas de vignette reprend l'affiche de sa saison
                   -> dernier onglet "Casting" : les acteurs vus dans plusieurs saisons,
                      puis, saison par saison, ceux qui n'appartiennent qu'à elle
  --image-size STR taille TMDB jaquette / folder.jpg : w300 / w780 / original (défaut : w780)
  --still-size STR taille TMDB des vignettes du récap (défaut : w300)
  --profile-size STR taille TMDB des portraits du casting (défaut : w185)
  --cast-limit N   acteurs gardés par section de l'onglet Casting (défaut : 20)
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # pour importer mkvlib
from mkvlib import artwork, cache, cast, cli, embed, lookup, mkv, naming  # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError, TmdbError                    # noqa: E402

PROFILE_SIZE = "w185"   # portraits du casting : la taille TMDB faite pour un visage


# ----------------------------------------------------------------------------
# 1. État vise pour un épisode : tags Matroska + titre + date + jaquette
#    TargetTypeValue : 70 = COLLECTION (série), 60 = SEASON, 50 = ÉPISODE
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

    lines = [mkv.simple("TITLE", ep.get("name", "")), mkv.simple("PART_NUMBER", ep.get("episode_number", ""))]
    if ep.get("overview"):
        lines.append(mkv.simple("SYNOPSIS", ep["overview"]))
        lines.append(mkv.simple("SUMMARY", ep["overview"]))
    if ep.get("air_date"):
        lines.append(mkv.simple("DATE_RELEASED", ep["air_date"]))
    lines += mkv.credits_lines(ep.get("crew", []), ep.get("guest_stars", []), max_actors)
    if ep.get("vote_average"):
        lines.append(mkv.simple("COMMENT", f"TMDB {round(ep['vote_average'], 1)}/10 ({ep.get('vote_count', 0)} votes)"))
    blocks.append(mkv.tag_block(50, lines))
    return mkv.tags_document(blocks)


def episode_target(season, ep, series_name, opts):
    """Ce que le .mkv de cet épisode devrait contenir."""
    return mkv.Target(
        title=ep.get("name", ""),
        date=ep.get("air_date"),
        tags_xml=build_tags_xml(season, ep, series_name),
        poster=(ep.get("still_path") or season.get("poster_path")) if opts.cover else None,
    )


# ----------------------------------------------------------------------------
# 2. Traitement d'une saison
# ----------------------------------------------------------------------------
@dataclass
class SeasonRun:
    """Une saison traitée : son dossier, ses données TMDB, et les numéros d'épisode effectivement présents sur le disque (pour la fiche récap)."""
    folder: Path
    number: int
    data: dict
    owned: set = field(default_factory=set)

    @property
    def episodes(self):
        return self.data.get("episodes", [])


def season_run(folder, number, data, args):
    """Assemble une saison traitée, avec l'inventaire de ce qui est sur le disque.

    L'inventaire se lit dans les noms de fichiers, tous formats vidéo confondus : il reste donc juste même avec --no-tag, pour une série qui n'est pas en .mkv.
    """
    owned = naming.owned_numbers(folder, data.get("episodes", []), args.match_threshold)
    return SeasonRun(Path(folder), number, data, owned)


@dataclass
class Candidate:
    """Un .mkv du dossier, confronte aux données TMDB."""
    path: Path
    episode: dict | None = None
    method: str = ""
    notes: list = field(default_factory=list)   # remarques à afficher sous le fichier
    info: dict | None = None
    tags: set | None = None                     # tags déjà écrits, si on les à relus


def build_plan(mkv_dir, season, args, opts):
    """[Candidate, ...] pour les .mkv du dossier, dans l'ordre des noms.

    Les fichiers associes sont lus en parallèle : chacun coûte deux sous-processus qu'on ne fait qu'attendre.
    """
    episodes = season.get("episodes", [])
    by_num = {e.get("episode_number"): e for e in episodes}
    plan = []
    for f in sorted(Path(mkv_dir).glob("*.mkv")):
        f = f.resolve()
        ep, method = naming.match_episode(f.name, episodes, args.match_threshold, by_num)
        plan.append(Candidate(f, ep, method))

    # Les tags ne sont relus que si on doit les comparer : un processus de plus.
    besoin_tags = args.verify or args.skip_done
    lectures = mkv.inspect_all([c.path for c in plan if c.episode], args.probe, besoin_tags)
    for candidate in plan:
        lecture = lectures.get(candidate.path)
        if lecture is None:
            continue
        candidate.info, candidate.tags = lecture.info, lecture.tags
        if lecture.note:
            candidate.notes.append(lecture.note)
        dmin, runtime = lecture.probe.duration_min, candidate.episode.get("runtime")
        if dmin and runtime and abs(dmin - runtime) > 3:
            candidate.notes.append(f"duree {dmin:.0f}min vs {runtime}min attendues -> a verifier")
    return plan


def process_season(mkv_dir, season, args, opts, tmdb):
    """Construit le plan d'une saison, l'affiche, et applique si --apply. Retourne le Report de la saison."""
    plan = build_plan(mkv_dir, season, args, opts)
    if not plan:
        print(f"  Aucun .mkv dans {mkv_dir}")
        return mkv.Report()

    report = mkv.Report(total=len(plan))
    for c in plan:
        if c.episode is None:
            print(f"  [NON ASSOCIE] {c.path.name}")
            continue
        report.matched += 1
        sn, en = season.get("season_number", 1), c.episode.get("episode_number", 0)
        print(f"  [S{sn:02d}E{en:02d}] {c.path.name}")
        print(f"            -> {c.episode.get('name', '')}   ({c.method})")
        for note in c.notes:
            print(f"            /!\\ {note}")
        target = episode_target(season, c.episode, args.series_name, opts)
        if args.verify:                     # mode vérification : état actuel vs vise
            diffs = [(lbl, det) for lbl, ok, det in mkv.verify(c.info, target, opts, c.tags) if not ok]
            for lbl, det in diffs:
                print(f"      [DIFF] {lbl} : actuel = {det!r}")
            if diffs:
                report.diffs += 1
            else:
                print("      [OK] deja conforme")
            continue
        if opts.date and target.date:
            print(f"      date segment -> {target.date}")
        for line in mkv.track_preview_lines(c.info, opts):
            print(line)

    if args.apply and not args.verify:
        print("  --- ecriture ---")
        for c in plan:
            if c.episode is None:
                continue
            target = episode_target(season, c.episode, args.series_name, opts)
            if args.skip_done and mkv.is_conform(c.info, target, opts, c.tags):
                print(f"  [SKIP] {c.path.name} (deja a jour)")
                continue
            code, msg = mkv.write(c.path, c.info, target, opts, tmdb)
            if code:
                report.failures += 1
            print(f"  [{'OK' if code == 0 else 'ECHEC'}] {c.path.name}" + (f"  -> {msg}" if code else ""))

    print(f"  => {report.matched}/{report.total} associe(s).")
    return report


# ----------------------------------------------------------------------------
# 3. Fiche récap HTML : vignettes encodées dans la page
# ----------------------------------------------------------------------------
def esc(value):
    """Texte prêt à poser dans la page : TMDB écrit des titres avec des & et des <."""
    return escape(str(value or ""))


def episode_image(ep, run, show):
    """Image d'un épisode dans la fiche : sa vignette, sinon l'affiche de la saison, sinon celle de la série.

    TMDB ne fournit pas de vignette pour tous les épisodes (séries peu documentées, saisons récentes) : une saison entière peut n'en avoir aucune. Une affiche répétée dit au moins de quelle saison il s'agit, là où une case vide ne dit rien. Retourne (chemin TMDB, repli ?).
    """
    still = ep.get("still_path")
    if still:
        return still, False
    return run.data.get("poster_path") or show.get("poster_path"), True


def collect_stills(runs, show, size):
    """Retourne {clé: chemin TMDB} pour toutes les images de la fiche, replis compris."""
    needed = {}
    for run in runs:
        for ep in run.episodes:
            path, _ = episode_image(ep, run, show)
            key = embed.image_key(path, size)
            if key:
                needed[key] = path
    return needed


# ----------------------------------------------------------------------------
# 3 bis. Onglet Casting : l'ensemble récurrent, puis ce que chaque saison amène
# ----------------------------------------------------------------------------
def collect_cast(runs, args, tmdb):
    """Casting cumulé de chaque saison traitée : [(numéro de saison, cast TMDB), ...].

    Une saison dont le casting échoue est simplement absente de la liste : une fiche amputée d'une section vaut mieux qu'une fiche non écrite.
    """
    casts = []
    for run in runs:
        try:
            casts.append((run.number, tmdb.aggregate_credits(args.tmdb_id, run.number).get("cast", [])))
        except TmdbError as e:
            print(f"  [recap] casting de la saison {run.number} ignore : {e}")
    return casts


def collect_profiles(casting, size):
    """Retourne {clé: chemin TMDB} pour les portraits du casting."""
    needed = {}
    for actor in casting.actors:
        key = embed.image_key(actor.profile, size)
        if key:
            needed[key] = actor.profile
    return needed


def cast_sections(casting, runs):
    """[(titre, [Actor, ...]), ...] dans l'ordre d'affichage.

    Les saisons reprennent le nom que TMDB leur donne, comme les onglets - "Specials" ne doit pas devenir "Saison 0" ici alors qu'il s'affiche autrement à côté.
    """
    names = {run.number: run.data.get("name") or f"Saison {run.number}" for run in runs}
    # Sans découpage par saison, "récurrent" n'oppose plus rien : c'est tout le casting.
    head = "Acteurs récurrents" if casting.per_season else "Casting"
    sections = [(head, casting.recurring)] if casting.recurring else []
    return sections + [(names.get(number, f"Saison {number}"), actors)
                       for number, actors in casting.per_season]


def build_cast_panel(casting, runs, images, size):
    """Le contenu de l'onglet Casting : un mur de portraits par section.

    Une ligne dit la règle du découpage : sans elle, une section "Saison 3" qui ne montre que les acteurs propres à la saison 3 se lirait comme son casting complet.
    """
    blocks = ["<p class='note'>Les acteurs vus dans plusieurs saisons sont regroupés en tête ; "
              "chaque saison ne montre ensuite que les siens.</p>"] if casting.per_season else []
    for title, actors in cast_sections(casting, runs):
        cards = []
        for actor in actors:
            key = embed.image_key(actor.profile, size)
            uri = images.get(key) if key else None
            face = embed.tag(key, uri) if uri else "<div class='noimg'></div>"
            cards.append(f"<div class='actor'><div class='ph'>{face}</div>"
                         f"<div class='n'>{esc(actor.name)}</div>"
                         f"<div class='c'>{esc(actor.character)}</div>"
                         f"<div class='e'>{esc(cast.coverage(actor))}</div></div>")
        blocks.append(f"<h2>{esc(title)}</h2><div class='grid'>{''.join(cards)}</div>")
    return "".join(blocks)


def build_recap_html(series_name, show, runs, tmdb_id, images, size, casting=None, profile_size=PROFILE_SIZE):
    """Rend la page HTML (pur rendu : ni réseau ni disque).

    'images' = {clé: data-URI}, vignettes d'épisode et portraits du casting mêlés ; un épisode sans vignette propre retombe sur l'affiche de sa saison (voir episode_image), et n'a un emplacement vide que si celle-ci manque aussi. Une affiche de repli revient sur beaucoup d'épisodes : elle est écrite une seule fois, dans une règle CSS, et non recopiée dans chaque balise.

    Les épisodes absents du disque sont grises et étiquetés, avec un compteur par saison. Une saison dont on ne connaît aucun fichier n'est pas marquée du tout : mieux vaut ne rien dire que tout déclarer manquant.

    Le casting, s'il y en a un, prend le dernier onglet."""
    tabs, panels, partagees = [], [], {}
    for i, run in enumerate(runs):
        label = run.data.get("name") or f"Saison {run.number}"
        episodes = run.episodes
        marque = bool(run.owned)          # sans inventaire, on ne juge pas
        compteur = (f"<span class='cnt'>{len(run.owned)}/{len(episodes)}</span>" if marque and episodes else "")
        tabs.append(f"<button class='tab{' active' if i == 0 else ''}' "
                    f"data-s='{run.number}'>{esc(label)}{compteur}</button>")
        cards = []
        for ep in episodes:
            path, repli = episode_image(ep, run, show)
            key = embed.image_key(path, size)
            uri = images.get(key) if key else None
            if not uri:
                img = "<div class='noimg'></div>"
            elif repli:
                partagees[key] = uri
                img = embed.shared_slot(key, "fb")
            else:
                img = embed.tag(key, uri)

            absent = marque and ep.get("episode_number") not in run.owned
            manque = "<span class='miss'>manquant</span>" if absent else ""
            rt = f" · {ep['runtime']} min" if ep.get("runtime") else ""
            cards.append(
                f"<div class='ep{' absent' if absent else ''}'>"
                + img
                + "<div class='meta'>"
                + f"<div><span class='n'>{ep.get('episode_number', 0):02d}</span> "
                + f"<span class='t'>{esc(ep.get('name'))}</span>{manque}</div>"
                + f"<div class='d'>{esc(naming.fr_date(ep.get('air_date')))}{rt}</div>"
                + f"<div class='o'>{esc(ep.get('overview'))}</div>"
                + "</div></div>")
        panels.append(f"<section class='season' data-s='{run.number}'"
                      f"{'' if i == 0 else ' hidden'}>{''.join(cards)}</section>")

    if casting:
        tabs.append("<button class='tab' data-s='cast'>Casting</button>")
        panels.append("<section class='season cast' data-s='cast' hidden>"
                      + build_cast_panel(casting, runs, images, profile_size) + "</section>")

    return (
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
        f"<meta name='tmdb-id' content='{esc(tmdb_id)}'>"
        f"<meta name='still-size' content='{esc(size)}'>"
        f"<meta name='profile-size' content='{esc(profile_size)}'>"
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
        # Emplacement de repli : l'affiche est en portrait, recadrée en 16/9 comme une vignette. Le cadrage est pris aux trois dixièmes du haut, pas au centre : une affiche place ses personnages dans sa moitié haute et son titre en bas, et un cadrage centre les coupe tous à la taille. 'background-color' et non le raccourci 'background', qui effacerait l'image posée par la règle de chaque affiche.
        ".ep .fb{width:160px;height:90px;border-radius:8px;flex:none;"
        "background-color:#21232b;background-size:cover;background-position:center 30%;"
        "background-repeat:no-repeat}"
        + "".join(embed.shared_rule(cle, uri) for cle, uri in partagees.items()) +
        ".ep.absent{opacity:.42}"
        ".miss{margin-left:8px;padding:1px 7px;border-radius:999px;font-size:11px;"
        "text-transform:uppercase;letter-spacing:.04em;background:#3a2a2e;color:#ff9aa6;"
        "vertical-align:1px}"
        ".cnt{margin-left:7px;font-size:12px;opacity:.65;font-variant-numeric:tabular-nums}"
        ".ep .meta{flex:1}.ep .n{color:#7cc4ff;font-weight:600}"
        ".ep .t{font-weight:600}.ep .d{color:#9aa0aa;font-size:14px;margin:2px 0 6px}"
        ".ep .o{color:#c7ccd4;font-size:14px}"
        # Casting : des portraits en portrait (2/3), comme les affiches du récap des films.
        ".cast h2{font-size:17px;margin:26px 0 14px;padding-bottom:8px;"
        "border-bottom:1px solid #21232b}"
        ".cast h2:first-of-type{margin-top:14px}"
        ".cast .note{color:#9aa0aa;font-size:14px;margin:0}"
        ".cast .grid{display:grid;gap:18px;"
        "grid-template-columns:repeat(auto-fill,minmax(124px,1fr))}"
        ".actor .ph{aspect-ratio:2/3;border-radius:8px;overflow:hidden;background:#21232b}"
        ".actor img,.actor .noimg{width:100%;height:100%;object-fit:cover;display:block}"
        ".actor .n{margin-top:8px;font-size:14px;font-weight:600;line-height:1.3}"
        ".actor .c{color:#c7ccd4;font-size:13px;line-height:1.35}"
        ".actor .e{color:#9aa0aa;font-size:12px;font-variant-numeric:tabular-nums}"
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
    """Écrit posters de dossier (affiche EN) / fiche récap selon les options. processed = [SeasonRun, ...]."""
    if not (args.artwork or args.recap):
        return
    apply = args.apply and not args.verify
    print("--- annexes ---")

    if args.artwork:
        for run in processed:
            poster = artwork.english_poster(lambda: tmdb.season(args.tmdb_id, run.number, artwork.ARTWORK_LANG), run.data.get("poster_path"))
            print(f"  [saison {run.number}] affiche (EN) : "
                  f"{artwork.write_poster(poster, run.folder, apply, tmdb)}")
        poster = artwork.english_poster(lambda: tmdb.series(args.tmdb_id, artwork.ARTWORK_LANG), show.get("poster_path"))
        print(f"  [serie] affiche (EN) : {artwork.write_poster(poster, root_dir, apply, tmdb)}")

    if args.recap:
        out = Path(root_dir) / "recap.html"
        known = embed.read_embedded(out)     # la fiche précédente sert de cache d'images
        # En simulation on n'interroge ni ne télécharge rien : la page est rendue sans image ni casting.
        stills = (embed.fetch(collect_stills(processed, show, args.still_size), known, args.still_size, tmdb, label="vignette") if apply else {})
        casting = cast.split(collect_cast(processed, args, tmdb), limit=args.cast_limit) if apply else cast.Casting()
        profiles = (embed.fetch(collect_profiles(casting, args.profile_size), known, args.profile_size, tmdb, label="portrait") if apply else {})
        images = {**stills, **profiles}
        html = build_recap_html(series_name, show, processed, args.tmdb_id, images, args.still_size, casting, args.profile_size)
        tally = ", ".join(t for t in (f"{len(stills)} vignette(s)" if stills else "",
                                      f"{len(profiles)} portrait(s)" if profiles else "") if t)
        print(f"  [serie] {_write_text(out, html, apply)}"
              + (f"  ({len(html) / 1_048_576:.1f} Mo, {tally} integre(s))" if tally else ""))
        legacy = Path(root_dir) / "assets"
        if legacy.is_dir():
            print(f"  [serie] note : le dossier '{legacy.name}' n'est plus utilise "
                  "(vignettes desormais integrees au HTML) -> tu peux le supprimer")


# ----------------------------------------------------------------------------
# 4. Programme principal
# ----------------------------------------------------------------------------
def parse_args():
    ap = argparse.ArgumentParser(description="Etiquette des .mkv depuis TMDB (donnees en francais via l'API).")
    ap.add_argument("--dir", required=True, help="Racine de la serie (dossiers 'Saison N') OU un seul dossier de saison")
    # --- Source TMDB ---
    ap.add_argument("--tmdb-id", help="Identifiant TMDB de la serie "
                    "(par defaut : recherche sur le nom du dossier)")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--no-cache", action="store_true", help="Ignore le cache des reponses TMDB et le rafraichit")
    ap.add_argument("--series-name", help="Force le nom de serie (sinon recupere automatiquement de TMDB)")
    # --- Ce qu'on écrit ---
    ap.add_argument("--apply", action="store_true", help="Applique reellement (defaut : simulation)")
    ap.add_argument("--verify", action="store_true", help="Verifie seulement l'etat des fichiers (aucune ecriture)")
    ap.add_argument("--skip-done", action="store_true", help="Saute les fichiers deja conformes")
    ap.add_argument("--no-tag", action="store_true", help="Ne modifie aucun episode ; genere seulement folder.jpg / recap (series non-MKV)")
    ap.add_argument("--no-cover", action="store_true", help="N'embarque pas la jaquette")
    ap.add_argument("--no-date", action="store_true", help="Ne modifie pas la date du segment")
    ap.add_argument("--no-audio-names", action="store_true", help="Ne renomme pas les pistes audio")
    ap.add_argument("--no-sub-names", action="store_true", help="Ne renomme pas les pistes de sous-titres")
    ap.add_argument("--no-flags", action="store_true", help="Ne touche pas aux drapeaux 'par defaut'")
    ap.add_argument("--no-stats", action="store_true", help="N'ajoute pas les tags de statistiques de piste")
    # --- Générateurs annexes (option, écrivent des fichiers à côté des .mkv) ---
    ap.add_argument("--artwork", action="store_true", help="Ecrit folder.jpg (vignette de dossier) en anglais")
    ap.add_argument("--recap", action="store_true", help="Genere une fiche recap HTML de la serie")
    ap.add_argument("--image-size", default="w780", help="Taille TMDB de la jaquette embarquee / folder.jpg : w300 / w780 / original")
    ap.add_argument("--still-size", default="w300",
                    help="Taille TMDB des vignettes du recap (defaut : w300 ; w400 = plus net "
                         "sur ecran HiDPI mais fiche plus lourde)")
    ap.add_argument("--profile-size", default=PROFILE_SIZE,
                    help=f"Taille TMDB des portraits du casting (defaut : {PROFILE_SIZE})")
    ap.add_argument("--cast-limit", type=int, default=cast.LIMIT,
                    help=f"Acteurs gardes par section de l'onglet Casting (defaut : {cast.LIMIT} ; "
                         "une saison en credite facilement une centaine)")
    ap.add_argument("--match-threshold", type=float, default=0.55, help="Score minimal pour une association par titre (0-1)")
    return ap.parse_args()


def main():
    args = parse_args()
    cli.setup_console()
    cli.check_dir(args.dir)
    args.probe = mkv.check_tools(needs_mkvtoolnix=not args.no_tag)
    opts = mkv.Options.from_args(args)
    tmdb = Tmdb(cli.resolve_tmdb_key(), args.language, user_agent="tag_mkv/1.0", cache=cache.Cache(read=not args.no_cache))

    # La bannière d'abord : la recherche de série s'affiche dessous, pas avant.
    print(f"=== {cli.mode_label(args)} ===")
    args.tmdb_id = lookup.resolve_show_id(tmdb, args.dir, args.tmdb_id)
    if args.tmdb_id is None:
        sys.exit("Serie non identifiee : relance avec --tmdb-id.")

    # Détails de la série via TMDB (nom auto, + poster/synopsis pour les annexes)
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
        # --- Multi-saisons : --dir est la racine de la série ---
        report = mkv.Report()
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
                report += process_season(sub, data, args, opts, tmdb)
            processed.append(season_run(sub, num, data, args))
            print()
        if not args.no_tag:
            print(f"TOTAL : {report.matched}/{report.total} fichier(s) associe(s) "
                  f"sur {len(seasons)} saison(s) detectee(s).")
        generate_sidecars(args.dir, args.series_name, show, processed, args, tmdb)
    else:
        # --- Saison unique : --dir contient directement les .mkv ---
        num = naming.season_number(Path(args.dir).name)
        num = 1 if num is None else num
        try:
            data = tmdb.season(args.tmdb_id, num)
        except TmdbError as e:
            sys.exit(f"Echec de l'appel TMDB (saison {num}) : {e}")
        report = mkv.Report()
        if args.no_tag:
            print("  episodes non modifies (--no-tag)")
        else:
            report = process_season(args.dir, data, args, opts, tmdb)
        generate_sidecars(args.dir, args.series_name, show, [season_run(Path(args.dir), num, data, args)], args, tmdb)

    reste = report.epilogue()
    if reste:
        print(f"\nA CORRIGER : {reste}.")
    return report.exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
