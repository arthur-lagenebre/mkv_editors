"""Répartition du casting d'une série : l'ensemble récurrent, puis ce que chaque saison amène.

Une série qui dure ne garde pas la même distribution. Tout afficher d'un bloc noierait le noyau qui traverse les saisons sous les invités d'un soir, bien plus nombreux ; découper saison par saison recopierait ce noyau autant de fois qu'il y a de saisons. Les acteurs vus dans PLUSIEURS saisons forment donc un ensemble, cité une fois, et chaque saison ne montre plus que ce qui lui est propre.

Le comptage vient de /aggregate_credits, qui totalise ce que chaque épisode crédite : c'est la seule source TMDB qui distingue un rôle tenu toute une saison d'une apparition unique. Une saison en ramène facilement une centaine de noms, d'où le plafond par section - passé les premiers, ce sont des silhouettes.

Une série d'une seule saison n'a rien à comparer : tout son casting forme l'ensemble, et la répartition par saison reste vide.

Tout est pur : aucun accès réseau, l'appelant fournit les castings déjà lus.
"""

from dataclasses import dataclass, field

LIMIT = 20          # acteurs gardés par section
MIN_SEASONS = 2     # vu dans au moins tant de saisons -> récurrent
MAX_ROLES = 2       # personnages cités pour un même acteur
LAST = 10 ** 6      # rang de générique inconnu : derrière tous les autres


@dataclass
class Actor:
    """Un acteur, tel que l'ensemble des saisons lues le crédite."""
    person_id: int
    name: str
    profile: str | None = None
    roles: list = field(default_factory=list)       # personnages, dans l'ordre TMDB
    episodes: int = 0
    seasons: list = field(default_factory=list)     # numéros de saison où il parait
    order: int = LAST                               # rang au générique (0 = tête d'affiche)

    @property
    def character(self):
        """Le personnage joué ; les premiers, quand un acteur en tient plusieurs."""
        return " / ".join(self.roles[:MAX_ROLES])


@dataclass
class Casting:
    """Le casting d'une série, réparti pour la fiche."""
    recurring: list = field(default_factory=list)
    per_season: list = field(default_factory=list)  # [(numéro de saison, [Actor, ...]), ...]

    def __bool__(self):
        return bool(self.recurring or self.per_season)

    @property
    def actors(self):
        """Tous les acteurs affichés - de quoi rassembler les portraits à télécharger."""
        yield from self.recurring
        for _, actors in self.per_season:
            yield from actors


def _episode_count(entry):
    """Épisodes crédités à un acteur, en retombant sur le détail de ses rôles."""
    total = entry.get("total_episode_count")
    if isinstance(total, int):
        return total
    return sum(role.get("episode_count") or 0 for role in entry.get("roles") or [])


def _billing(entry):
    return entry["order"] if isinstance(entry.get("order"), int) else LAST


def collect(casts):
    """{id de personne: Actor} pour toutes les saisons. casts = [(numéro de saison, cast TMDB), ...]."""
    actors = {}
    for number, cast in casts:
        for entry in cast:
            person = entry.get("id")
            if person is None:
                continue
            actor = actors.get(person)
            if actor is None:
                actor = actors[person] = Actor(person, entry.get("name") or "", entry.get("profile_path"))
            for role in entry.get("roles") or []:
                character = (role.get("character") or "").strip()
                if character and character not in actor.roles:
                    actor.roles.append(character)
            actor.episodes += _episode_count(entry)
            actor.order = min(actor.order, _billing(entry))
            if number not in actor.seasons:
                actor.seasons.append(number)
    return actors


def _rank(actor):
    """Ordre d'affichage : le plus présent d'abord, puis le générique, puis l'alphabet."""
    return (-len(actor.seasons), -actor.episodes, actor.order, actor.name)


def split(casts, limit=LIMIT, min_seasons=MIN_SEASONS):
    """Répartit les castings lus saison par saison en un Casting prêt à afficher.

    Un acteur ne figure qu'à un seul endroit : dans l'ensemble récurrent, ou dans la saison qui est la sienne.
    """
    actors = collect(casts)
    numbers = [number for number, _ in casts]
    shared = ({person for person, actor in actors.items() if len(actor.seasons) >= min_seasons}
              if len(numbers) >= min_seasons else set(actors))

    recurring = sorted((a for person, a in actors.items() if person in shared), key=_rank)[:limit]
    per_season = []
    for number in numbers:
        own = sorted((a for person, a in actors.items()
                      if person not in shared and number in a.seasons), key=_rank)
        if own:
            per_season.append((number, own[:limit]))
    return Casting(recurring, per_season)


def season_label(numbers):
    """'S1-3, S5' : les saisons d'un acteur, plages contigües regroupées."""
    groups = []
    for number in sorted(set(numbers)):
        if groups and number == groups[-1][1] + 1:
            groups[-1][1] = number
        else:
            groups.append([number, number])
    return ", ".join(f"S{start}" if start == end else f"S{start}-{end}" for start, end in groups)


def coverage(actor):
    """Ce qu'un acteur a tourné, en une ligne. Les saisons ne sont citées que s'il y en a plusieurs : dans une section de saison, les répéter ne dirait rien."""
    seasons = f"{season_label(actor.seasons)} · " if len(actor.seasons) > 1 else ""
    return f"{seasons}{actor.episodes} ép."
