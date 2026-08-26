"""Appariement des films d'un dossier aux films d'une collection TMDB.

Un dossier de saga n'est pas une réunion de fichiers indépendants : c'est une COLLECTION, que TMDB sait donner en entier. Chercher chaque fichier seul dans 900 000 films, c'est s'exposer à tous les homonymes de la Terre - "Le défi" ramène Batman, "Apocalypse" ramène un film d'amour, "Vendetta" ramène V pour Vendetta. À l'intérieur d'une saga de 26 volumes, ces homonymes n'existent pas.

Deux signaux, et un principe. Le NUMÉRO du fichier, quand il y en a un, vaut plus qu'une ressemblance : c'est une information que l'utilisateur a écrite lui-même. La RESSEMBLANCE du titre place le reste. Et un film de la saga n'est attribué qu'UNE fois : c'est ce qui permet aux évidences de se placer d'abord, puis à l'élimination de trancher les noms qui ne ressemblent à rien ("Ground Zero" pour "Resident Evil", "The final Chapter" pour "Chapitre Final").

Tout est pur : aucun accès réseau, l'appelant fournit les fiches déjà lues.
"""

from difflib import SequenceMatcher

MIN_SCORE = 0.6         # avec un rang fiable, une ressemblance moyenne suffit
MIN_SCORE_SEUL = 0.85   # sans rang, seul un titre presque exact fait foi
BONUS_ORDRE = 0.5       # de quoi faire gagner un numéro contre une ressemblance

def _norm(s):
    return " ".join((s or "").lower().split())


def close_to(name, title):
    """Ressemblance d'un nom de fichier à un titre de film de la saga.

    Le titre complet ("Resident Evil : Apocalypse") ET sa fin ("Apocalypse") sont comparés : dans un dossier de saga, les fichiers ne portent souvent que le sous-titre, puisque le reste est déjà dans le nom du dossier.
    """
    nom, titre = _norm(name), _norm(title)
    fin = _norm((title or "").split(":")[-1])
    return max(SequenceMatcher(None, nom, titre).ratio(), SequenceMatcher(None, nom, fin).ratio())

def by_release(parts):
    """Films de la collection dans l'ordre de sortie - celui des numéros."""
    return sorted(parts or [], key=lambda p: p.get("release_date") or "9999")

def trustworthy_order(files, parts):
    """La numérotation du dossier vaut-elle comme rang dans la collection ?

    Elle ne le vaut que si elle couvre la saga EXACTEMENT : 1..N pour N films. dès qu'un spin-off s'invite dans la collection sans être numéroté sur le disque, les deux ordres divergent - "Fast & Furious : Hobbs & Shaw" occupe le rang 9 de sa saga, si bien que le fichier "10 - Fast X" se ferait placer sur "Fast & Furious 9". Dans le doute, seuls les titres parlent.
    """
    numerotes = sorted(o for _, _, o in files if isinstance(o, int))
    return bool(numerotes) and numerotes == list(range(1, len(parts) + 1))

def assign(files, parts):
    """[(clé, film), ...] : au plus un film de la saga par fichier.

    `files` = [(clé, titre cherche, ordre ou None)], `parts` = les films de la collection. Les meilleures correspondances se placent d'abord, et chaque film n'étant donne qu'une fois, les fichiers restants héritent de ce qui reste - c'est l'élimination qui fait le gros du travail sur les titres muets.
    """
    ordonnes = by_release(parts)
    numerote = any(isinstance(o, int) for _, _, o in files)
    if numerote:
        # Un dossier numéroté dont les numéros ne couvrent pas la saga ne dit plus rien de sur : ni le rang (decale), ni les titres (une saga les à jumeaux - "Fast and Furious", "Fast & Furious 4", "Fast & Furious 5").
        if not trustworthy_order(files, ordonnes):
            return []
        plancher, bonus = MIN_SCORE, BONUS_ORDRE
    else:
        # Sans numéro, la ressemblance porte tout : elle doit être franche.
        plancher, bonus = MIN_SCORE_SEUL, 0.0

    scores = []
    for cle, titre, ordre in files:
        for rang, part in enumerate(ordonnes, 1):
            note = close_to(titre, part.get("title") or "")
            if ordre is not None and str(ordre) == str(rang):
                note += bonus
            scores.append((note, cle, rang, part))
    # Tri stable et déterministe : le meilleur score d'abord, puis la clé et le rang, pour que deux passages donnent exactement le même résultat.
    scores.sort(key=lambda x: (-x[0], str(x[1]), x[2]))

    cles_prises, rangs_pris, retenu = set(), set(), []
    for note, cle, rang, part in scores:
        if note < plancher or cle in cles_prises or rang in rangs_pris:
            continue
        cles_prises.add(cle)
        rangs_pris.add(rang)
        retenu.append((cle, part))
    return retenu


def most_common_collection(collections):
    """Id de la saga vers laquelle pointent au moins DEUX films du dossier.

    Un seul film ne fait pas une saga : ce serait suivre une association peut-être fausse. Deux, c'est déjà une piste.
    """
    comptes = {}
    for ident in collections:
        if ident is not None:
            comptes[ident] = comptes.get(ident, 0) + 1
    if not comptes:
        return None
    ident, n = max(comptes.items(), key=lambda kv: kv[1])
    return ident if n >= 2 else None


def fits(files, parts):
    """La numérotation du dossier tient-elle dans cette collection ?

    C'est le garde-fou qui sépare une saga d'un dossier de rangement. Le MCU numéroté 35 films et la collection TMDB qui s'en approche le plus en compte quatre : sans ce contrôle, le film n. 4 se ferait placer au 4e rang d'une saga qui n'est pas la sienne, avec le bonus d'ordre en prime.
    """
    ordres = [o for _, _, o in files if isinstance(o, int)]
    return not ordres or max(ordres) <= len(parts or [])
