# mkv_editors

[![Tests](https://github.com/arthur-lagenebre/mkv_editors/actions/workflows/tests.yml/badge.svg)](https://github.com/arthur-lagenebre/mkv_editors/actions/workflows/tests.yml)

Outils personnels pour étiqueter une médiathèque à partir de [TMDB](https://www.themoviedb.org),
en français : les métadonnées sont écrites **directement dans les `.mkv`** (sans ré-encodage ni
remux, c'est quasi instantané), pour que chaque fichier reste autonome.

| Script | Rôle |
|---|---|
| [Movies/Metadata.py](Movies/Metadata.py) | Étiquette des films : titre, date de sortie **française**, synopsis, casting, genres, jaquette, noms de pistes ; fiche récap de la médiathèque |
| [TV_Shows/Metadata.py](TV_Shows/Metadata.py) | Idem pour une série, saison par saison, plus une fiche récap HTML |
| [TV_Shows/Rename_Episodes.py](TV_Shows/Rename_Episodes.py) | Renomme les épisodes en `{numéro} - {titre TMDB}.ext`, sous-titres compris |
| [Movies/Rename_Movies.py](Movies/Rename_Movies.py) | Renomme les dossiers de films en `Titre (Année)`, avec épinglage de l'id TMDB |

Le code commun aux trois vit dans [mkvlib/](mkvlib/) (accès TMDB, lecture/écriture des `.mkv`,
analyse des noms de fichiers). Aucune dépendance pip : uniquement la bibliothèque standard.

## Prérequis

- Python 3.10 ou plus récent
- [MKVToolNix](https://mkvtoolnix.download) (`mkvpropedit`, `mkvmerge`, `mkvextract`) — obligatoire pour écrire
- [FFmpeg](https://ffmpeg.org) (`ffprobe`) — facultatif : débit audio dans le nom des pistes,
  et détection des fichiers dont la durée ne correspond pas à l'épisode

```powershell
winget install MoritzBunkus.MKVToolNix
winget install Gyan.FFmpeg
```

`Rename_Episodes.py` n'a besoin d'aucun de ces outils : il ne touche qu'aux noms de fichiers.

## Clé TMDB

Copier `.env.example` en `.env` à la racine et y renseigner la clé (themoviedb.org →
Paramètres → API). Le `.env` n'est jamais commité, et sert à tous les scripts du dépôt.

```
TMDB_KEY=votre_cle
```

C'est la **seule** source : ni variable d'environnement, ni constante dans les scripts. Le
fichier est cherché en remontant depuis le dossier du script, donc les trois s'en servent sans
qu'on ait rien à exporter. La clé v3 (hexadécimale) comme le token de lecture v4 (JWT)
fonctionnent.

## Utilisation

**Tous les scripts démarrent en simulation** : rien n'est écrit tant que `--apply` n'est pas
passé. `--verify` (les deux `Metadata.py`) compare l'état des fichiers à TMDB sans rien modifier.

### Films

`--dir` est parcouru **récursivement**, aussi profond qu'il y a des dossiers : films à plat,
dossiers de saga (`DCEU/01 - Man of Steel.mkv`) et étages intermédiaires (`Batman/Nolan
Trilogy/…`) cohabitent. Un **dossier n'est jamais un film** : il ne compte ni ne se traite, il
range. Il prête seulement son nom au film qu'il contient quand il n'en contient qu'un — c'est
là que vit le titre dans `Inception (2010)/film.mkv`. Partout ailleurs c'est le nom du
**fichier** qui parle, et les dossiers au-dessus viennent en **renfort** — du plus proche au plus
lointain : `Resident Evil/Animation/3 - Vendetta.mkv` cherche `Vendetta` (qui donne *V pour
Vendetta*…), puis `Resident Evil Vendetta`. Le renfort ne l'emporte que si le titre trouvé
contient à la fois le dossier et ce qu'on cherchait. À titre égal, TMDB classant par popularité,
une fiche portant **exactement** le titre cherché passe devant (`Blade` doit rendre *Blade*, pas
*Blade II*).

Un sous-titre dont le **nom** dit « forcé » alors que le drapeau manque se voit poser le
drapeau — sinon le renommer d'après ses drapeaux effacerait l'information. Et quand une piste
reste **ambiguë** (un « forcé » non transposable parce qu'une autre piste porte déjà le drapeau,
ou deux pistes de même langue qui porteraient le même nom), le film n'est **pas traité du tout** :
rien n'est modifié, la raison s'affiche sous `[NON TRAITE]`, et le bilan les compte.

Quand plusieurs fiches TMDB écrivent le **même titre autrement** (« Les Quatre Fantastiques »
et « Les 4 Fantastiques »), ou quand plusieurs portent le **même titre** (« Dracula » en rend
trois), le film est mis de côté et la question est posée **à la fin du
passage**, dans le terminal : candidats numérotés, `Entrée` garde le premier, `i` laisse le film
de côté, `q` arrête les questions. Une suite (« Iron Man 2 ») n'est pas une variante et ne
demande rien. Hors terminal (sortie redirigée, CI) les films restent de côté au lieu de bloquer ;
`--no-ask` reprend l'ancien comportement.

Le titre et l'année sont lus dans le nom — `Inception (2010)`. Un film coupé en plusieurs
fichiers (`CD1`/`CD2`) est étiqueté en entier. Bandes-annonces, making-of et dossiers de bonus
(`Extras`, `Featurettes`…) sont reconnus à leur **nom** : le poids ne décide de rien, un dessin
animé de 1 Go est un film autant qu'un remux de 28 Go. Un préfixe d'ordre de saga
(`1 - Iron Man`, demi-numéros compris : `1.5 - Dark Fury`) est reconnu et inscrit comme numéro dans
la collection.

À chaque passage, un **journal** est écrit à la racine de `--dir` : `metadata.log` donne le lien
TMDB de chaque film trouvé, et groupe en fin de fichier ceux qui n'en ont pas — non associés, ou
laissés en attente d'une réponse.

```powershell
python Movies\Metadata.py --dir "D:\Films"                    # simulation
python Movies\Metadata.py --dir "D:\Films" --apply            # applique
python Movies\Metadata.py --dir "D:\Films\Dune (2021)" --tmdb-id 438631 --apply   # force l'id
python Movies\Metadata.py --dir "D:\Films" --no-tag --recap --apply   # fiche seule
```

L'identifiant TMDB retenu est **inscrit dans le film** (tag Matroska `TMDB`, au format
`movie/1234`) : au passage suivant il est relu, plus rien n'est cherché, et l'association
survit au renommage. La relecture ne coûte un sous-processus de plus que sur les fichiers qui
déclarent des tags — une médiathèque jamais étiquetée ne paie rien. Priorité : `--tmdb-id`, puis
l'identifiant épinglé dans le **nom**, puis celui lu dans le **fichier**, puis la recherche.

Comme toute la reconnaissance repose sur les noms,
[Rename_Movies.py](Movies/Rename_Movies.py) le remet d'aplomb depuis TMDB — et `--pin-id` y
écrit l'identifiant, après quoi plus rien n'est cherché ni ne peut se tromper :

```powershell
python Movies\Rename_Movies.py --dir "D:\Films"                    # simulation
python Movies\Rename_Movies.py --dir "D:\Films" --apply --pin-id   # renomme et épingle
```

Un préfixe d'ordre de saga est conservé (`1 - Iron Man` → `1 - Iron Man (2008)`), les
sous-titres suivent leur film en mode « `.mkv` à plat », et un identifiant déjà épinglé n'est
jamais retiré.

`--recap` écrit un `recap.html` à la racine de `--dir` : un mur d'affiches groupé par saga,
avec **les films qui manquent à chaque saga** — TMDB connaît la composition des collections,
donc une trilogie possédée aux deux tiers se voit. Comme pour les séries, la page est un
fichier unique (affiches encodées dedans) et la précédente sert de cache. `--no-tag` produit
les annexes sans rien modifier dans les `.mkv`.

### Séries

Un sous-dossier `Saison N` par saison, les `.mkv` dedans, préfixés par leur numéro d'épisode.
`--dir` peut aussi pointer directement sur un dossier de saison.

```powershell
python TV_Shows\Rename_Episodes.py --dir "D:\Series\Ma Serie" --apply
python TV_Shows\Metadata.py --dir "D:\Series\Ma Serie" --apply --recap
```

Les sous-titres posés à côté d'une vidéo (`.srt`, `.ass`, `.idx`/`.sub`…) sont renommés avec
elle, en conservant ce qui suit le nom : `S01E02.fr.forced.srt` → `02 - Titre.fr.forced.srt`.

La série est identifiée par une recherche TMDB sur le nom du dossier (celui du parent si `--dir`
pointe sur une saison) : le résultat retenu est affiché, et les cas douteux — reboot portant le
même nom, titre éloigné de la recherche — sont signalés. `--tmdb-id 1234` force l'identifiant
quand la recherche se trompe ou ne trouve rien — et pour que ce soit **durable**, écris-le dans
le nom du dossier : `Ma Serie [tmdbid-1396]` (ou `{tmdb-1396}`). Même chose côté films :
`Dune (2021) [tmdbid-438631]`.

`--recap` produit un `recap.html` unique (onglets par saison, vignettes encodées dans la page :
rien à conserver à côté). Les épisodes absents du disque y sont grisés et étiquetés, avec un
compteur par saison — l'inventaire se lit dans les noms de fichiers, tous formats vidéo
confondus, donc il reste juste même avec `--no-tag`. `--artwork` écrit les `folder.jpg` (affiche anglaise) de la série et de
chaque saison. `--no-tag` génère ces annexes sans toucher aux épisodes, pour une série qui n'est
pas en `.mkv`.

Un dossier `Specials` (ou `Hors-serie`) est traité comme la saison 0 de TMDB, où vivent les
épisodes spéciaux.

### Options communes

`--verify` et `--skip-done` comparent aussi **les tags écrits** (synopsis, casting, genres,
dates) à ce que TMDB donne aujourd'hui : un fichier étiqueté par une version plus ancienne, ou
dont les tags ont été perdus, est signalé au lieu d'être déclaré conforme. Cette relecture coûte
un appel à `mkvextract` par fichier, donc elle n'a lieu qu'avec ces deux options.

Les deux `Metadata.py` sortent en **code 1** s'il reste quelque chose à corriger — fichiers non
conformes en `--verify`, écritures en échec en `--apply` — et le résument en dernière ligne, de
quoi les enchaîner dans un script.

Les réponses de TMDB sont gardées **7 jours** dans `%LOCALAPPDATA%\mkv_editors\tmdb`
(`~/.cache/mkv_editors/tmdb` ailleurs) — jamais à côté de la médiathèque. Repasser sur une
grosse collection ne refait donc pas tous les appels : mesuré sur 8 requêtes, 416 ms contre
56 ms. `--no-cache` ignore ce qui est en cache et le rafraîchit ; supprimer le dossier est
sans conséquence, il se reconstruit tout seul.

`--skip-done` saute ce qui est déjà conforme, `--language` change la langue TMDB (défaut `fr-FR` ;
c'est elle qui détermine le pays de la date de sortie retenue pour les films), et les `--no-*`
(`--no-cover`, `--no-date`, `--no-audio-names`, `--no-sub-names`, `--no-flags`, `--no-stats`)
désactivent chacun une catégorie d'écriture. `--help` liste le reste.

## Tests

```powershell
python -m unittest discover -s tests -t .
```

Ils tournent aussi sur chaque push et chaque PR (Windows et Linux, Python 3.10 et 3.13) via
[GitHub Actions](.github/workflows/tests.yml).

Ils couvrent la partie qui casse en silence — analyse des noms, tags produits, comparaison à
l'état visé, erreurs TMDB, cas tordus du renommage — sans réseau ni outil externe.
