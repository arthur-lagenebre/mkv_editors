# mkv_editors

[![Tests](https://github.com/arthur-lagenebre/mkv_editors/actions/workflows/tests.yml/badge.svg)](https://github.com/arthur-lagenebre/mkv_editors/actions/workflows/tests.yml)

Outils personnels pour étiqueter une médiathèque à partir de [TMDB](https://www.themoviedb.org) pour les films et les séries, en français, et de [MusicBrainz](https://musicbrainz.org) pour la musique : les métadonnées sont écrites **directement dans les fichiers** — `.mkv`, `.flac` — (sans ré-encodage ni remux, c'est quasi instantané), pour que chaque fichier reste autonome.

| Script | Rôle |
| --- | --- |
| [scripts/Movies/Metadata.py](scripts/Movies/Metadata.py) | Étiquette des films : titre, date de sortie **française**, synopsis, casting, genres, jaquette, noms de pistes ; fiche récap de la médiathèque |
| [scripts/TV_Shows/Metadata.py](scripts/TV_Shows/Metadata.py) | Idem pour une série, saison par saison, plus une fiche récap HTML |
| [scripts/Music/Metadata.py](scripts/Music/Metadata.py) | Étiquette des albums `.flac` depuis MusicBrainz : tags à la Picard, identifiants, pochette ; ReplayGain et paroles restent en place |
| [scripts/TV_Shows/Rename_Episodes.py](scripts/TV_Shows/Rename_Episodes.py) | Renomme les épisodes en `{numéro} - {titre TMDB}.ext`, sous-titres compris |
| [scripts/Movies/Rename_Movies.py](scripts/Movies/Rename_Movies.py) | Renomme les dossiers de films en `Titre (Année)`, avec épinglage de l'id TMDB |
| [scripts/Music/Rename_Tracks.py](scripts/Music/Rename_Tracks.py) | Renomme les pistes des albums `.flac` en `{numéro} - {titre MusicBrainz}.flac`, paroles `.lrc` comprises |
| [scripts/Maintenance/Verify_Files.py](scripts/Maintenance/Verify_Files.py) | Contrôle la structure Matroska des `.mkv` d'un dossier, et remultiplexe ceux qui sont abîmés |
| [scripts/Converters/Avi_To_Mkv.py](scripts/Converters/Avi_To_Mkv.py) | Remultiplexe les `.avi` en `.mkv` sans réencodage, sous-titres adjacents compris |
| [scripts/Converters/Mp4_To_Mkv.py](scripts/Converters/Mp4_To_Mkv.py) | Idem pour les `.mp4`, en gardant les langues de pistes que le fichier déclare déjà |

Les scripts qu'on lance vivent sous [scripts/](scripts/), rangés par domaine ; le reste est interne :

```text
scripts/Movies/        étiquetage et renommage des films
scripts/TV_Shows/      la même chose pour les séries
scripts/Music/         étiquetage et renommage des albums, depuis MusicBrainz
scripts/Maintenance/   contrôle et réparation des fichiers, sans rapport avec TMDB
scripts/Converters/    changement de conteneur, avant tout étiquetage
mkvlib/                le code commun, et tout ce qui touche aux films et aux séries
musiclib/              ce qui ne sert qu'à la musique
tests/                 les tests
```

[mkvlib/](mkvlib/) porte l'accès TMDB, la lecture/écriture des `.mkv` et l'analyse des noms de fichiers, ainsi que ce qui ne dépend d'aucun domaine : cache disque, ligne de commande, bilan d'un passage. [musiclib/](musiclib/) porte l'accès MusicBrainz, la lecture/écriture des `.flac` et la reconnaissance des albums ; il se sert de ces utilitaires communs, jamais de ce qui touche aux films. Aucune dépendance pip : uniquement la bibliothèque standard.

## Prérequis

- Python 3.10 ou plus récent
- [MKVToolNix](https://mkvtoolnix.download) (`mkvpropedit`, `mkvmerge`, `mkvextract`) — obligatoire pour écrire
- [FFmpeg](https://ffmpeg.org) (`ffprobe`) — facultatif : débit audio dans le nom des pistes, détection des fichiers dont la durée ne correspond pas à l'épisode, et contrôle de la durée après conversion d'un `.avi` ou d'un `.mp4`

```powershell
winget install MoritzBunkus.MKVToolNix
winget install Gyan.FFmpeg
```

`Rename_Episodes.py` et `Rename_Tracks.py` n'ont besoin d'aucun de ces outils : ils ne touchent qu'aux noms de fichiers. `Music/Metadata.py` non plus : il lit et écrit les `.flac` lui-même, et MusicBrainz ne demande aucune clé.

## Clé TMDB

Copier `.env.example` en `.env` à la racine et y renseigner la clé (themoviedb.org → Paramètres → API). Le `.env` n'est jamais commité, et sert à tous les scripts du dépôt.

```text
TMDB_KEY=votre_cle
```

C'est la **seule** source : ni variable d'environnement, ni constante dans les scripts. Le fichier est cherché en remontant depuis le dossier du script, donc tous s'en servent sans qu'on ait rien à exporter. La clé v3 (hexadécimale) comme le token de lecture v4 (JWT) fonctionnent.

## Utilisation

**Tous les scripts démarrent en simulation** : rien n'est écrit tant que `--apply` n'est pas passé. `--verify` (les `Metadata.py`) compare l'état des fichiers à TMDB sans rien modifier.

### Films

`--dir` est parcouru **récursivement**, aussi profond qu'il y a des dossiers : films à plat, dossiers de saga (`DCEU/01 - Man of Steel.mkv`) et étages intermédiaires (`Batman/Nolan Trilogy/…`) cohabitent. Un **dossier n'est jamais un film** : il ne compte ni ne se traite, il range. Il prête seulement son nom au film qu'il contient quand il n'en contient qu'un — c'est là que vit le titre dans `Inception (2010)/film.mkv`. Partout ailleurs c'est le nom du **fichier** qui parle, et les dossiers au-dessus viennent en **renfort** — du plus proche au plus lointain : `Resident Evil/Animation/3 - Vendetta.mkv` cherche `Vendetta` (qui donne *V pour Vendetta*…), puis `Resident Evil Vendetta`. Le renfort ne l'emporte que si le titre trouvé contient à la fois le dossier et ce qu'on cherchait. À titre égal, TMDB classant par popularité, une fiche portant **exactement** le titre cherché passe devant (`Blade` doit rendre *Blade*, pas *Blade II*).

Un sous-titre dont le **nom** dit « forcé » alors que le drapeau manque se voit poser le drapeau — sinon le renommer d'après ses drapeaux effacerait l'information. Et quand une piste reste **ambiguë** (un « forcé » non transposable parce qu'une autre piste porte déjà le drapeau, ou deux pistes de même langue qui porteraient le même nom), le film n'est **pas traité du tout** : rien n'est modifié, la raison s'affiche sous `[NON TRAITE]`, et le bilan les compte.

Une piste audio qui se déclare **audiodescription** (drapeau malvoyant) ou **commentaire** le dit dans son nom — `E-AC-3 5.1 640 kb/s AD`, `AC-3 2.0 Commentary` — et n'est jamais choisie comme piste par défaut. Une audiodescription est souvent encodée exactement comme la piste qu'elle double : sans l'étiquette, les deux porteraient le même nom.

Quand plusieurs fiches TMDB écrivent le **même titre autrement** (« Les Quatre Fantastiques » et « Les 4 Fantastiques »), ou quand plusieurs portent le **même titre** (« Dracula » en rend trois), le film est mis de côté et la question est posée **à la fin du passage**, dans le terminal : candidats numérotés, `Entrée` garde le premier, `i` laisse le film de côté, `q` arrête les questions. Une suite (« Iron Man 2 ») n'est pas une variante et ne demande rien. Hors terminal (sortie redirigée, CI) les films restent de côté au lieu de bloquer ; `--no-ask` reprend l'ancien comportement.

Le titre et l'année sont lus dans le nom — `Inception (2010)`. Un film coupé en plusieurs fichiers (`CD1`/`CD2`) est étiqueté en entier. Bandes-annonces, making-of et dossiers de bonus (`Extras`, `Featurettes`…) sont reconnus à leur **nom** : le poids ne décide de rien, un dessin animé de 1 Go est un film autant qu'un remux de 28 Go. Un préfixe d'ordre de saga (`1 - Iron Man`, demi-numéros compris : `1.5 - Dark Fury`) est reconnu et inscrit comme numéro dans la collection.

Un dossier de saga n'est pas une réunion de fichiers indépendants : c'est une **collection** TMDB. Les dossiers à plusieurs films sont donc réexaminés de l'intérieur — la saga est cherchée par le nom du dossier, puis parmi celles vers lesquelles plusieurs films pointent déjà, et les fichiers lui sont appariés un à un (numéro d'ordre d'abord, ressemblance ensuite ; chaque film ne servant qu'une fois, les titres muets héritent de ce qui reste). Un homonyme qui existe dans 900 000 films n'existe pas dans une saga de 26. `--no-saga` s'en passe.

À chaque passage, un **journal** est écrit à la racine de `--dir` : `metadata.log` donne le lien TMDB de chaque film trouvé, et groupe en fin de fichier ceux qui n'en ont pas — non associés, ou laissés en attente d'une réponse.

```powershell
python scripts\Movies\Metadata.py --dir "D:\Films"                    # simulation
python scripts\Movies\Metadata.py --dir "D:\Films" --apply            # applique
python scripts\Movies\Metadata.py --dir "D:\Films\Dune (2021)" --tmdb-id 438631 --apply   # force l'id
python scripts\Movies\Metadata.py --dir "D:\Films" --no-tag --recap --apply   # fiche seule
```

L'identifiant TMDB retenu est **inscrit dans le film** (tag Matroska `TMDB`, au format `movie/1234`) : au passage suivant il est relu, plus rien n'est cherché, et l'association survit au renommage. La relecture ne coûte un sous-processus de plus que sur les fichiers qui déclarent des tags — une médiathèque jamais étiquetée ne paie rien. Priorité : `--tmdb-id`, puis l'identifiant épinglé dans le **nom**, puis celui lu dans le **fichier**, puis la recherche.

Comme toute la reconnaissance repose sur les noms, [Rename_Movies.py](scripts/Movies/Rename_Movies.py) le remet d'aplomb depuis TMDB — et `--pin-id` y écrit l'identifiant, après quoi plus rien n'est cherché ni ne peut se tromper :

```powershell
python scripts\Movies\Rename_Movies.py --dir "D:\Films"                    # simulation
python scripts\Movies\Rename_Movies.py --dir "D:\Films" --apply --pin-id   # renomme et épingle
```

Un préfixe d'ordre de saga est conservé (`1 - Iron Man` → `1 - Iron Man (2008)`), les sous-titres suivent leur film en mode « `.mkv` à plat », et un identifiant déjà épinglé n'est jamais retiré.

`--recap` écrit un `recap.html` à la racine de `--dir` : un mur d'affiches groupé par saga, avec **les films qui manquent à chaque saga** — TMDB connaît la composition des collections, donc une trilogie possédée aux deux tiers se voit. Comme pour les séries, la page est un fichier unique (affiches encodées dedans) et la précédente sert de cache. `--no-tag` produit les annexes sans rien modifier dans les `.mkv`.

### Séries

Un sous-dossier `Saison N` par saison, les `.mkv` dedans, préfixés par leur numéro d'épisode. `--dir` peut aussi pointer directement sur un dossier de saison.

```powershell
python scripts\TV_Shows\Rename_Episodes.py --dir "D:\Series\Ma Serie" --apply
python scripts\TV_Shows\Metadata.py --dir "D:\Series\Ma Serie" --apply --recap
```

Les sous-titres posés à côté d'une vidéo (`.srt`, `.ass`, `.idx`/`.sub`…) sont renommés avec elle, en conservant ce qui suit le nom : `S01E02.fr.forced.srt` → `02 - Titre.fr.forced.srt`.

La série est identifiée par une recherche TMDB sur le nom du dossier (celui du parent si `--dir` pointe sur une saison) : le résultat retenu est affiché, et les cas douteux — reboot portant le même nom, titre éloigné de la recherche — sont signalés. `--tmdb-id 1234` force l'identifiant quand la recherche se trompe ou ne trouve rien — et pour que ce soit **durable**, écris-le dans le nom du dossier : `Ma Serie [tmdbid-1396]` (ou `{tmdb-1396}`). Même chose côté films : `Dune (2021) [tmdbid-438631]`.

`--recap` produit un `recap.html` unique (onglets par saison, vignettes encodées dans la page : rien à conserver à côté). Les épisodes absents du disque y sont grisés et étiquetés, avec un compteur par saison — l'inventaire se lit dans les noms de fichiers, tous formats vidéo confondus, donc il reste juste même avec `--no-tag`. `--artwork` écrit les `folder.jpg` (affiche anglaise) de la série et de chaque saison. `--no-tag` génère ces annexes sans toucher aux épisodes, pour une série qui n'est pas en `.mkv`.

Un dossier `Specials` (ou `Hors-serie`) est traité comme la saison 0 de TMDB, où vivent les épisodes spéciaux.

### Musique

Un album est un dossier qui contient des `.flac` — lui ou ses dossiers de disque (`CD1`, `CD2`…) ; les dossiers d'artiste au-dessus ne font que ranger. La référence est [MusicBrainz](https://musicbrainz.org), ouvert et **sans clé**, et les pochettes viennent de [Cover Art Archive](https://coverartarchive.org).

```powershell
python scripts\Music\Metadata.py --dir "D:\Musique"                  # simulation
python scripts\Music\Metadata.py --dir "D:\Musique" --apply          # applique
python scripts\Music\Metadata.py --dir "D:\Musique" --verify         # vérifie seulement
```

L'album est cherché d'après les tags `ALBUM` et `ALBUMARTIST` de ses fichiers, puis d'après le nom du dossier (`1994 - Born Dead`, `Daft Punk - Discovery (2001) FLAC [...]`). Un album existe en autant d'**éditions** qu'il a connu de pressages, et seules comptent celles qui ont **exactement autant de pistes** que le dossier a de fichiers : c'est ce qui départage l'album du single de même nom, le CD de 17 pistes du pressage de 16. Parmi elles, l'édition officielle, en autant de disques que le dossier en range, sur CD ou en numérique plutôt qu'en vinyle — celui de *Human After All* a bien dix pistes, mais en deux faces de cinq —, de l'année annoncée, du pays `--country` (défaut `FR`), à défaut européenne puis mondiale. Deux albums **différents** du même titre qui tiennent tous les deux font l'objet d'une question en fin de passage, comme pour les films.

Chaque fichier est placé sur sa piste par son numéro (tags, dossier de disque, nom de fichier), puis par son titre ; un titre ou une durée qui ne collent pas sont signalés. Un seul fichier sans piste et l'album n'est **pas traité** : à moitié étiqueté, il aurait l'air fait.

Les tags s'écrivent sous les noms qu'emploie [Picard](https://picard.musicbrainz.org) : titre, artistes crédités et noms de tri, numéros et totaux, dates, genres votés, label, catalogue, code-barres, identifiants MusicBrainz. Un album déjà passé par Picard ne diffère en général que par ses genres. **Tout ce qui ne vient pas de MusicBrainz reste en place** : ReplayGain, paroles, notes, ISRC. Une clé n'est gérée que si la base lui donne une valeur — un genre écrit à la main survit à un album sans genre, et `--no-genres` les laisse tous —, sauf celles qui décrivent l'édition (label, code-barres, identifiants), qui mentiraient si elles restaient d'une autre.

La pochette est ajoutée aux fichiers qui n'en ont pas : celle de l'édition, à défaut celle du release group. Une pochette déjà embarquée est gardée ; `--replace-cover` la remplace, `--no-cover` n'en ajoute aucune, `--image-size` choisit `250`, `500`, `1200` (défaut) ou `original`. `--verify` n'exige que la pochette que l'édition déclare : celle du release group peut ne pas exister.

L'identifiant de l'édition (`MUSICBRAINZ_ALBUMID`) est inscrit dans chaque fichier et relu au passage suivant. Priorité : `--mbid`, puis l'identifiant épinglé dans le nom du dossier (`2013 - Outrun [mbid-4e5d9f0c-09b6-42bf-b495-e2d7cc288bf6]`), puis celui que **tous** les fichiers déclarent, puis la recherche.

Aucun outil externe : le script lit et écrit lui-même les blocs du `.flac`. Tant que les tags tiennent dans le padding du fichier, seul l'en-tête est réécrit ; sinon — une pochette ajoutée, typiquement — le fichier est recopié à côté puis substitué, et le son ne bouge pas. La place laissée vide sur place est plafonnée à 64 Ko : l'Explorateur de Windows ne lit plus rien, ni tags ni miniature, d'un `.flac` dont le son commence au-delà de 4 Mio (mesuré à l'octet près), et c'est ainsi que les pistes de *Synthesis* avaient perdu leur pochette à l'affichage. Un fichier que son padding a fait passer au-delà est signalé par `--verify` et recopié par `--apply` ; une image embarquée assez lourde pour franchir seule la limite est signalée. Mesuré sur une médiathèque de 194 pistes : 167 écritures sur place, 27 recopies (les deux albums sans pochette), le son intact à l'octet près, et un `--verify` qui ne trouve plus rien ensuite ; sur un album recopié en entier, l'empreinte MD5 du son décodé par ffmpeg reste la même. Un fichier déjà conforme n'est jamais réécrit, d'où l'absence de `--skip-done`. MusicBrainz n'accepte qu'une requête par seconde : un premier passage coûte environ trois requêtes par album, les suivants sont servis par le cache. Seuls les `.flac` sont écrits : un dossier de `.mp3` est signalé et laissé tel quel. Le journal `metadata.log` donne le lien MusicBrainz de chaque album.

Pour les noms de fichiers, [Rename_Tracks.py](scripts/Music/Rename_Tracks.py) est le pendant de `Rename_Episodes.py` : chaque piste prend le nom `{numéro} - {titre}.flac` que donne MusicBrainz, le numéro sur autant de chiffres dans tout l'album.

```powershell
python scripts\Music\Rename_Tracks.py --dir "D:\Musique"                 # simulation
python scripts\Music\Rename_Tracks.py --dir "D:\Musique" --apply         # renomme
```

L'album est reconnu exactement comme par `Metadata.py` — mêmes éditions, même placement des fichiers, mêmes questions en fin de passage — et un album dont un seul fichier ne trouve pas sa piste n'est pas renommé du tout. Passé après `Metadata.py`, il ne cherche plus rien : l'identifiant est dans les fichiers. Un album en plusieurs disques rangés à plat préfixe le disque (`2-01 - Alive 1997.flac`) ; rangés dans `CD1`, `CD2`…, chaque dossier repart de `01`. Un `/` dans un titre devient `-` (`Robot Rock - Oh Yeah`), et les paroles `.lrc` posées à côté suivent leur piste. Les dossiers ne bougent pas : leur nom reste celui qu'on leur a donné.

### Convertir les AVI

Un `.avi` ne sait rien porter : ni jaquette, ni synopsis, ni identifiant TMDB. Tant qu'un film reste dans ce conteneur, `Metadata.py` n'a nulle part où écrire — [Avi_To_Mkv.py](scripts/Converters/Avi_To_Mkv.py) fait donc la passe qui précède l'étiquetage. Rien n'est réencodé : les pistes sont recopiées telles quelles, à la vitesse du disque, et l'image comme le son ressortent identiques.

```powershell
python scripts\Converters\Avi_To_Mkv.py --dir "D:\Films"                          # simulation
python scripts\Converters\Avi_To_Mkv.py --dir "D:\Films" --apply                  # convertit
python scripts\Converters\Avi_To_Mkv.py --dir "D:\Films" --apply --delete-source  # + efface l'.avi vérifié
```

Les sous-titres posés à côté sont embarqués au passage, et leur **encodage est mesuré fichier par fichier** : `mkvmerge` suppose de l'UTF-8, alors qu'un `.srt` d'époque est en général en `windows-1252` — et le malentendu ne se voit qu'aux accents cassés, souvent une fois l'original effacé. La langue est lue dans le suffixe du nom (`Film.fr.srt` → `fre`), `--sub-lang` tranche pour ceux qui n'en ont pas, et un `.sub` est laissé à son `.idx`, qui l'embarque déjà. `--subs none` les ignore, `--subs require` ne convertit que les films qui en ont.

La **durée** du `.mkv` produit est comparée à celle de la source (`--tolerance`, 2 s par défaut) : c'est ce qui attrape un index AVI qui ment ou un flux mal recopié, que `mkvmerge` ne signale pas toujours. `--delete-source` n'efface l'original qu'après ce contrôle — et réclame donc `ffprobe`. Un `conversion.log` est écrit à la racine de `--dir`, et le script sort en **code 1** s'il reste un fichier non converti.

`--lang` donne la langue des pistes audio (`fre` par défaut, code ISO 639-2 à trois lettres), `--default-audio` et `--default-sub` posent les drapeaux « piste par défaut », `--title` inscrit le nom du fichier comme titre du segment, et `--output-dir` écrit ailleurs qu'à côté de la source. Le `.mkv` obtenu est prêt pour [Metadata.py](scripts/Movies/Metadata.py), qui y écrira le reste.

### Convertir les MP4

Même passe pour les `.mp4`, avec [Mp4_To_Mkv.py](scripts/Converters/Mp4_To_Mkv.py). Un `.mp4` sait porter une jaquette et quelques tags, mais à la mode iTunes : `Metadata.py` écrit des tags **Matroska**, et n'a donc nulle part où les poser tant que le film reste dans ce conteneur. Rien n'est réencodé non plus — les chapitres suivent, la jaquette et les tags iTunes sont laissés derrière, puisque `Metadata.py` les réécrit ensuite depuis TMDB.

```powershell
python scripts\Converters\Mp4_To_Mkv.py --dir "D:\Films"                          # simulation
python scripts\Converters\Mp4_To_Mkv.py --dir "D:\Films" --apply                  # convertit
python scripts\Converters\Mp4_To_Mkv.py --dir "D:\Films" --apply --delete-source  # + efface le .mp4 vérifié
```

Une différence de fond avec les `.avi` : un `.mp4` **déclare souvent la langue de ses pistes**, là où un `.avi` n'en dit jamais rien. Elle est donc gardée telle quelle, et `--lang` ne sert qu'aux pistes qui n'en ont pas (`und`) — sans quoi un rip anglais correctement étiqueté ressortirait en français. `--force-lang` réétiquette tout, pour un fichier qui se trompe, et le bilan de chaque conversion dit quelles langues ont été conservées.

Les sous-titres **déjà dans le `.mp4`** (tx3g) sont recopiés par `mkvmerge` sans qu'on ait rien à demander, et comptent pour `--subs require` ; ceux posés à côté sont traités exactement comme pour un `.avi`, encodage mesuré fichier par fichier. Le reste est identique — contrôle de durée (`--tolerance`), `--delete-source` après vérification, code de sortie **1** s'il reste un fichier non converti — et le journal s'appelle `conversion_mp4.log`, pour ne pas écraser celui des `.avi` quand les deux passes visent le même dossier. Seule l'extension `.mp4` est prise : un `.m4v` est le même conteneur sous un autre nom, mais le renommer se voit, alors que l'embarquer en silence ne se verrait pas.

### Vérifier les fichiers

Un `.mkv` peut se lire du début à la fin et avoir le conteneur abîmé — une fin de fichier écrite à moitié après une coupure, par exemple. `mkvpropedit` s'en plaint alors à chaque passage (« erreur dans la structure du fichier Matroska à la position … ») et **rien d'autre ne le dit** : mesuré sur des fichiers volontairement cassés, `mkvmerge` (même en démultiplexant tout vers `NUL`) et `mkvinfo` se resynchronisent en silence et sortent en code 0.

```powershell
python scripts\Maintenance\Verify_Files.py --dir "D:\Films"                # contrôle rapide
python scripts\Maintenance\Verify_Files.py --dir "D:\Films\Ghibli" --full  # toute la chaîne, clusters compris
python scripts\Maintenance\Verify_Files.py --dir "D:\Films" --repair       # contrôle puis remultiplexe
python scripts\Maintenance\Verify_Files.py --dir "D:\Films" --no-recursive # cet étage seul
```

[Verify_Files.py](scripts/Maintenance/Verify_Files.py) suit lui-même la chaîne des éléments EBML, en lecture seule. Par défaut il s'en tient aux points de repère — l'index, la table Cues, la queue du fichier — dont le prix **ne dépend pas de la taille du film** : mesuré sur un partage réseau, 489 films et 4,49 To en 15 minutes (dont les deux tiers passés dans la lecture d'en-tête par `mkvmerge`). `--full` descend dans les clusters, mais lit chaque fichier en entier (compter ~10 s par Go sur un partage réseau) ; il voit alors tout ce que voit `mkvpropedit`, plus la troncature.

`--dir` est parcouru **récursivement**, comme partout ailleurs. `--no-recursive` s'en tient aux `.mkv` posés directement dedans : de quoi contrôler l'étage d'une médiathèque — les films rangés à plat — sans relire les dossiers de films qu'elle contient, ni les repasser après coup un par un.

`--repair` remultiplexe les fichiers en défaut : `mkvmerge` réécrit le film proprement à côté, le résultat est contrôlé à son tour, et l'original n'est remplacé que s'il ressort sain — identifiant TMDB, jaquette, titre du segment, noms de pistes et statistiques sont conservés. Au moindre échec l'original reste en place. Un `verification.log` est écrit à la racine de `--dir`, et le script sort en **code 1** s'il reste un fichier abîmé.

### Options communes

`--verify` et `--skip-done` comparent aussi **les tags écrits** (synopsis, casting, genres, dates) à ce que TMDB donne aujourd'hui : un fichier étiqueté par une version plus ancienne, ou dont les tags ont été perdus, est signalé au lieu d'être déclaré conforme. Cette relecture coûte un appel à `mkvextract` par fichier, donc elle n'a lieu qu'avec ces deux options.

Les `Metadata.py` sortent en **code 1** s'il reste quelque chose à corriger — fichiers non conformes en `--verify`, écritures en échec en `--apply` — et le résument en dernière ligne, de quoi les enchaîner dans un script.

Les réponses de TMDB et de MusicBrainz sont gardées **7 jours** dans `%LOCALAPPDATA%\mkv_editors\tmdb` et `…\musicbrainz` (`~/.cache/mkv_editors/…` ailleurs) — jamais à côté de la médiathèque. Repasser sur une grosse collection ne refait donc pas tous les appels : mesuré sur 8 requêtes, 416 ms contre 56 ms. `--no-cache` ignore ce qui est en cache et le rafraîchit ; supprimer le dossier est sans conséquence, il se reconstruit tout seul.

`--skip-done` saute ce qui est déjà conforme, `--language` change la langue TMDB (défaut `fr-FR` ; c'est elle qui détermine le pays de la date de sortie retenue pour les films), et les `--no-*` (`--no-cover`, `--no-date`, `--no-audio-names`, `--no-sub-names`, `--no-flags`, `--no-stats`) désactivent chacun une catégorie d'écriture. `--help` liste le reste.

## Tests

```powershell
python -m unittest discover -s tests -t .
```

Ils tournent aussi sur chaque push et chaque PR (Windows et Linux, Python 3.10 et 3.13) via [GitHub Actions](.github/workflows/tests.yml).

Ils couvrent la partie qui casse en silence — analyse des noms, tags produits, comparaison à l'état visé, erreurs TMDB, cas tordus du renommage — sans réseau ni outil externe.
