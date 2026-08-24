"""Appariement des films d'un dossier aux films d'une collection TMDB.

Un dossier de saga n'est pas une reunion de fichiers independants : c'est une
COLLECTION, que TMDB sait donner en entier. Chercher chaque fichier seul dans
900 000 films, c'est s'exposer a tous les homonymes de la Terre - "Le defi"
ramene Batman, "Apocalypse" ramene un film d'amour, "Vendetta" ramene V pour
Vendetta. A l'interieur d'une saga de 26 volumes, ces homonymes n'existent pas.

Deux signaux, et un principe. Le NUMERO du fichier, quand il y en a un, vaut
plus qu'une ressemblance : c'est une information que l'utilisateur a ecrite
lui-meme. La RESSEMBLANCE du titre place le reste. Et un film de la saga n'est
attribue qu'UNE fois : c'est ce qui permet aux evidences de se placer d'abord,
puis a l'elimination de trancher les noms qui ne ressemblent a rien
("Ground Zero" pour "Resident Evil", "The final Chapter" pour "Chapitre Final").

Tout est pur : aucun acces reseau, l'appelant fournit les fiches deja lues.
"""

from difflib import SequenceMatcher

MIN_SCORE = 0.6         # avec un rang fiable, une ressemblance moyenne suffit
MIN_SCORE_SEUL = 0.85   # sans rang, seul un titre presque exact fait foi
BONUS_ORDRE = 0.5       # de quoi faire gagner un numero contre une ressemblance


def _norm(s):
    return " ".join((s or "").lower().split())


def close_to(name, title):
    """Ressemblance d'un nom de fichier a un titre de film de la saga.

    Le titre complet ("Resident Evil : Apocalypse") ET sa fin ("Apocalypse")
    sont compares : dans un dossier de saga, les fichiers ne portent souvent que
    le sous-titre, puisque le reste est deja dans le nom du dossier.
    """
    nom, titre = _norm(name), _norm(title)
    fin = _norm((title or "").split(":")[-1])
    return max(SequenceMatcher(None, nom, titre).ratio(),
               SequenceMatcher(None, nom, fin).ratio())


def by_release(parts):
    """Films de la collection dans l'ordre de sortie - celui des numeros."""
    return sorted(parts or [], key=lambda p: p.get("release_date") or "9999")


def trustworthy_order(files, parts):
    """La numerotation du dossier vaut-elle comme rang dans la collection ?

    Elle ne le vaut que si elle couvre la saga EXACTEMENT : 1..N pour N films.
    Des qu'un spin-off s'invite dans la collection sans etre numerote sur le
    disque, les deux ordres divergent - "Fast & Furious : Hobbs & Shaw" occupe
    le rang 9 de sa saga, si bien que le fichier "10 - Fast X" se ferait placer
    sur "Fast & Furious 9". Dans le doute, seuls les titres parlent.
    """
    numerotes = sorted(o for _, _, o in files if isinstance(o, int))
    return bool(numerotes) and numerotes == list(range(1, len(parts) + 1))


def assign(files, parts):
    """[(cle, film), ...] : au plus un film de la saga par fichier.

    `files` = [(cle, titre cherche, ordre ou None)], `parts` = les films de la
    collection. Les meilleures correspondances se placent d'abord, et chaque
    film n'etant donne qu'une fois, les fichiers restants heritent de ce qui
    reste - c'est l'elimination qui fait le gros du travail sur les titres muets.
    """
    ordonnes = by_release(parts)
    numerote = any(isinstance(o, int) for _, _, o in files)
    if numerote:
        # Un dossier numerote dont les numeros ne couvrent pas la saga ne dit
        # plus rien de sur : ni le rang (decale), ni les titres (une saga les a
        # jumeaux - "Fast and Furious", "Fast & Furious 4", "Fast & Furious 5").
        if not trustworthy_order(files, ordonnes):
            return []
        plancher, bonus = MIN_SCORE, BONUS_ORDRE
    else:
        # Sans numero, la ressemblance porte tout : elle doit etre franche.
        plancher, bonus = MIN_SCORE_SEUL, 0.0

    scores = []
    for cle, titre, ordre in files:
        for rang, part in enumerate(ordonnes, 1):
            note = close_to(titre, part.get("title") or "")
            if ordre is not None and str(ordre) == str(rang):
                note += bonus
            scores.append((note, cle, rang, part))
    # Tri stable et deterministe : le meilleur score d'abord, puis la cle et le
    # rang, pour que deux passages donnent exactement le meme resultat.
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

    Un seul film ne fait pas une saga : ce serait suivre une association
    peut-etre fausse. Deux, c'est deja une piste.
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
    """La numerotation du dossier tient-elle dans cette collection ?

    C'est le garde-fou qui separe une saga d'un dossier de rangement. Le MCU
    numerote 35 films et la collection TMDB qui s'en approche le plus en compte
    quatre : sans ce controle, le film n. 4 se ferait placer au 4e rang d'une
    saga qui n'est pas la sienne, avec le bonus d'ordre en prime.
    """
    ordres = [o for _, _, o in files if isinstance(o, int)]
    return not ordres or max(ordres) <= len(parts or [])
