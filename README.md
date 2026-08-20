# mkv_editors

Outils personnels pour étiqueter une médiathèque à partir de [TMDB](https://www.themoviedb.org),
en français : les métadonnées sont écrites **directement dans les `.mkv`** (sans ré-encodage ni
remux, c'est quasi instantané), pour que chaque fichier reste autonome.

| Script | Rôle |
|---|---|
| [Movies/Metadata.py](Movies/Metadata.py) | Étiquette des films : titre, date de sortie **française**, synopsis, casting, genres, jaquette, noms de pistes |
| [TV_Shows/Metadata.py](TV_Shows/Metadata.py) | Idem pour une série, saison par saison, plus une fiche récap HTML |
| [TV_Shows/Rename_Episodes.py](TV_Shows/Rename_Episodes.py) | Renomme les épisodes en `{numéro} - {titre TMDB}.ext` |

Le code commun aux trois vit dans [mkvlib/](mkvlib/) (accès TMDB, lecture/écriture des `.mkv`,
analyse des noms de fichiers). Aucune dépendance pip : uniquement la bibliothèque standard.

## Prérequis

- Python 3.10 ou plus récent
- [MKVToolNix](https://mkvtoolnix.download) (`mkvpropedit`, `mkvmerge`) — obligatoire pour écrire
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

Ordre de priorité : `.env` > variable d'environnement `TMDB_API_KEY` > `TMDB_KEY` >
constante `TMDB_KEY` en haut du script. La clé v3 (hexadécimale) comme le token de lecture v4
(JWT) fonctionnent.

## Utilisation

**Tous les scripts démarrent en simulation** : rien n'est écrit tant que `--apply` n'est pas
passé. `--verify` (les deux `Metadata.py`) compare l'état des fichiers à TMDB sans rien modifier.

### Films

Un sous-dossier par film, ou des `.mkv` à plat. Le titre et l'année sont lus dans le nom —
`Inception (2010)`. Un préfixe d'ordre de saga (`1 - Iron Man`) est reconnu et inscrit comme
numéro dans la collection.

```powershell
python Movies\Metadata.py --dir "D:\Films"                    # simulation
python Movies\Metadata.py --dir "D:\Films" --apply            # applique
python Movies\Metadata.py --dir "D:\Films\Dune (2021)" --tmdb-id 438631 --apply   # force l'id
```

### Séries

Un sous-dossier `Saison N` par saison, les `.mkv` dedans, préfixés par leur numéro d'épisode.
`--dir` peut aussi pointer directement sur un dossier de saison.

```powershell
python TV_Shows\Rename_Episodes.py --dir "D:\Series\Ma Serie" --tmdb-id 1234 --apply
python TV_Shows\Metadata.py --dir "D:\Series\Ma Serie" --tmdb-id 1234 --apply --recap
```

`--recap` produit un `recap.html` unique (onglets par saison, vignettes encodées dans la page :
rien à conserver à côté). `--artwork` écrit les `folder.jpg` (affiche anglaise) de la série et de
chaque saison. `--no-tag` génère ces annexes sans toucher aux épisodes, pour une série qui n'est
pas en `.mkv`.

### Options communes

`--skip-done` saute ce qui est déjà conforme, `--language` change la langue TMDB (défaut `fr-FR` ;
c'est elle qui détermine le pays de la date de sortie retenue pour les films), et les `--no-*`
(`--no-cover`, `--no-date`, `--no-audio-names`, `--no-sub-names`, `--no-flags`, `--no-stats`)
désactivent chacun une catégorie d'écriture. `--help` liste le reste.

## Tests

```powershell
python -m unittest discover -s tests -t .
```

Ils couvrent la partie qui casse en silence — analyse des noms, tags produits, comparaison à
l'état visé, erreurs TMDB, cas tordus du renommage — sans réseau ni outil externe.
