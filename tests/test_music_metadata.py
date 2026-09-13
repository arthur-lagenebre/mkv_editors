"""Music/Metadata.py : de l'album sur le disque aux tags écrits, avec un faux MusicBrainz et de vrais .flac fabriqués."""

import copy
import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musiclib import album as albums
from musiclib import flac, lookup
from scripts.Music import Metadata as music
from tests.test_album import OUTRUN, sortie
from tests.test_flac import bloc, fichier, image

MBID = "4e5d9f0c-09b6-42bf-b495-e2d7cc288bf6"


class FauxMusicBrainz:
    """Rend des sorties préparées, et note ce qu'on lui demande."""

    def __init__(self, releases=None, results=(), cover=b"\xff\xd8JPEG"):
        self.releases = releases or {"rel-1": OUTRUN}
        self.results = list(results)
        self.cover_bytes = cover
        self.searches, self.lookups, self.covers = [], [], []

    def search_releases(self, title, artist=None, exact=True, limit=25):
        self.searches.append((title, artist, exact))
        return list(self.results) if exact else []

    def release(self, mbid):
        self.lookups.append(mbid)
        return copy.deepcopy(self.releases[mbid])

    def release_group(self, mbid):
        return {"id": mbid, "primary-type": "Album", "genres": [{"name": "synthwave", "count": 5}]}

    def cover(self, release_id, release_group_id=None, size="1200"):
        self.covers.append((release_id, release_group_id))
        return self.cover_bytes


def arguments(**changes):
    args = dict(mbid=None, country="FR", no_cache=False, no_ask=False, apply=True, verify=False, no_cover=False, replace_cover=False, no_genres=False, image_size="1200")
    args.update(changes)
    return types.SimpleNamespace(**args)


class AlbumTestCase(unittest.TestCase):
    TITRES = ("Prelude", "Blizzard", "Protovision")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dossier = Path(self._tmp.name) / "Kavinsky" / "2013 - Outrun"
        self.dossier.mkdir(parents=True)

    def poser(self, extra_lignes=(), titres=TITRES, images=(), nom_album="Outrun", name_pattern="0{n} - {title}.flac"):
        for n, titre in enumerate(titres, 1):
            lignes = [f"TITLE={titre}", f"TRACKNUMBER={n}", f"ALBUM={nom_album}", "ARTIST=Kavinsky", "YEAR=2013", "REPLAYGAIN_TRACK_GAIN=-9.5 dB", *extra_lignes]
            (self.dossier / name_pattern.format(n=n, title=titre)).write_bytes(fichier(lignes, extra=[(flac.PICTURE, i) for i in images]))

    def trouver(self):
        (seul,) = albums.find_albums(self.dossier.parent)
        metas, erreurs = lookup.read_album(seul)
        self.assertEqual(erreurs, [])
        return lookup.Found(seul, metas)

    def traiter(self, mb, **changes):
        found, args = self.trouver(), arguments(**changes)
        sortie_texte = io.StringIO()
        with redirect_stdout(sortie_texte):
            release, group = lookup.fetch_release("rel-1", mb, with_genres=not args.no_genres)
            report = music.process_album(found, release, group, args, mb)
        return report, sortie_texte.getvalue()

    def relire(self, n=1):
        return flac.read(self.dossier / f"0{n} - {self.TITRES[n - 1]}.flac")


class TestTraitementDUnAlbum(AlbumTestCase):
    def test_simulation_n_ecrit_rien(self):
        self.poser()
        avant = [f.read_bytes() for f in sorted(self.dossier.iterdir())]
        report, sortie_texte = self.traiter(FauxMusicBrainz(), apply=False)
        self.assertEqual(avant, [f.read_bytes() for f in sorted(self.dossier.iterdir())])
        self.assertIn("a ecrire :", sortie_texte)
        self.assertEqual((report.matched, report.failures), (1, 0))

    def test_application_fusionne_avec_l_existant(self):
        self.poser()
        self.traiter(FauxMusicBrainz())
        relu = self.relire(3)
        self.assertEqual((relu.first("TITLE"), relu.first("ARTIST"), relu.first("MUSICBRAINZ_ALBUMID")), ("Protovision", "Kavinsky feat. Havoc", "rel-1"))
        self.assertEqual(relu.values("GENRE"), ["Synthwave"])
        self.assertEqual(relu.first("REPLAYGAIN_TRACK_GAIN"), "-9.5 dB")      # rien ne le rendrait
        self.assertEqual(relu.values("YEAR"), [])                              # doublon de DATE

    def test_second_passage_sans_rien_a_faire(self):
        self.poser()
        mb = FauxMusicBrainz()
        self.traiter(mb)
        avant = [f.read_bytes() for f in sorted(self.dossier.iterdir())]
        _, sortie_texte = self.traiter(mb)
        self.assertEqual(sortie_texte.count("deja conforme"), 3)
        self.assertEqual(avant, [f.read_bytes() for f in sorted(self.dossier.iterdir())])

    def test_verification_compte_les_fichiers(self):
        self.poser()
        report, sortie_texte = self.traiter(FauxMusicBrainz(), verify=True)
        self.assertEqual(report.diffs, 3)
        self.assertIn("[DIFF] ALBUM : Outrun -> OutRun", sortie_texte)
        self.assertEqual(report.exit_code, 1)

    def test_pochette_ajoutee_une_fois_pour_l_album(self):
        self.poser()
        mb = FauxMusicBrainz({"rel-1": dict(OUTRUN, **{"cover-art-archive": {"front": True}})})
        self.traiter(mb)
        self.assertEqual(mb.covers, [("rel-1", "rg-1")])
        self.assertTrue(self.relire(2).has_front_cover)

    def test_pochette_du_release_group_quand_l_edition_n_en_a_pas(self):
        # Mesuré sur "Bloodlust" : le CD européen n'a pas d'image, son release group si.
        self.poser()
        mb = FauxMusicBrainz()                          # OUTRUN ne déclare aucune pochette
        self.traiter(mb)
        self.assertEqual(mb.covers, [(None, "rg-1")])
        self.assertTrue(self.relire().has_front_cover)

    def test_sans_pochette_nulle_part_rien_n_est_reecrit(self):
        self.poser()
        mb = FauxMusicBrainz(cover=None)
        self.traiter(mb)
        avant = [f.read_bytes() for f in sorted(self.dossier.iterdir())]
        _, sortie_texte = self.traiter(mb)
        self.assertEqual(avant, [f.read_bytes() for f in sorted(self.dossier.iterdir())])
        self.assertIn("aucune pochette", sortie_texte)

    def test_pochette_existante_gardee(self):
        self.poser(images=[image(b"MIENNE")])
        mb = FauxMusicBrainz({"rel-1": dict(OUTRUN, **{"cover-art-archive": {"front": True}})})
        self.traiter(mb)
        self.assertEqual(mb.covers, [])
        self.assertEqual(self.relire().pictures[0].data, b"MIENNE")

    def test_verification_n_exige_pas_une_pochette_inexistante(self):
        # Cover Art Archive n'en a pas : exiger une pochette signalerait un écart que rien ne comble.
        self.poser()
        mb = FauxMusicBrainz()
        self.traiter(mb)
        report, _ = self.traiter(mb, verify=True)
        self.assertEqual(report.diffs, 0)

    def test_en_tete_illisible_par_windows_repare(self):
        # Mesuré sur Synthesis : 6,98 Mo de padding laissés par une écriture sur place, et l'Explorateur ne lisait plus ni tags ni pochette.
        self.poser()
        mb = FauxMusicBrainz()
        self.traiter(mb)
        chemin = self.dossier / "01 - Prelude.flac"
        meta = flac.read(chemin)
        chemin.write_bytes(flac.render(meta, meta.comments, meta.pictures, flac.WINDOWS_HEADER_LIMIT) + chemin.read_bytes()[meta.audio_offset:])

        report, sortie_texte = self.traiter(mb, verify=True)
        self.assertEqual(report.diffs, 1)
        self.assertIn("illisible pour l'Explorateur Windows", sortie_texte)
        _, sortie_texte = self.traiter(mb)
        self.assertIn("[OK] recopie", sortie_texte)
        self.assertEqual(self.relire().padding, flac.DEFAULT_PADDING)
        report, _ = self.traiter(mb, verify=True)
        self.assertEqual(report.diffs, 0)

    def test_un_fichier_en_trop_bloque_tout_l_album(self):
        self.poser(titres=("Prelude", "Blizzard", "Protovision", "Bonus inconnu"))
        avant = [f.read_bytes() for f in sorted(self.dossier.iterdir())]
        report, sortie_texte = self.traiter(FauxMusicBrainz())
        self.assertEqual(report.skipped, 1)
        self.assertIn("[NON TRAITE]", sortie_texte)
        self.assertEqual(avant, [f.read_bytes() for f in sorted(self.dossier.iterdir())])


class TestResolution(AlbumTestCase):
    def resoudre(self, mb, **options):
        found = self.trouver()
        with redirect_stdout(io.StringIO()) as sortie_texte:
            release, _ = lookup.resolve(found, mb, **options)
        return found, release, sortie_texte.getvalue()

    def test_id_lu_dans_les_fichiers_evite_la_recherche(self):
        self.poser([f"MUSICBRAINZ_ALBUMID={MBID}"])
        mb = FauxMusicBrainz({MBID: OUTRUN})
        _, release, sortie_texte = self.resoudre(mb)
        self.assertEqual((release["id"], mb.searches), ("rel-1", []))
        self.assertIn("id lu dans les fichiers", sortie_texte)

    def test_nom_epingle_prime_sur_les_fichiers(self):
        self.poser(["MUSICBRAINZ_ALBUMID=11111111-1111-1111-1111-111111111111"])
        self.dossier = self.dossier.rename(self.dossier.with_name(f"2013 - Outrun [mbid-{MBID}]"))
        mb = FauxMusicBrainz({MBID: OUTRUN})
        self.resoudre(mb)
        self.assertEqual(mb.lookups, [MBID])

    def test_recherche_d_apres_les_tags(self):
        self.poser()
        mb = FauxMusicBrainz(results=[sortie("rel-1", "OutRun", 3)])
        _, release, _ = self.resoudre(mb)
        self.assertEqual(release["id"], "rel-1")
        self.assertEqual(mb.searches[0], ("Outrun", "Kavinsky", True))

    def test_repli_sur_la_recherche_large_puis_le_dossier(self):
        self.poser(nom_album="Titre Faux")
        mb = FauxMusicBrainz(results=[])
        _, release, _ = self.resoudre(mb)
        self.assertIsNone(release)
        self.assertEqual([s[0] for s in mb.searches], ["Titre Faux", "Titre Faux", "Outrun", "Outrun"])

    def test_deux_albums_du_meme_titre_attendent_la_question(self):
        self.poser()
        rivaux = [sortie("rel-1", "OutRun", 3, groupe="rg-1"), sortie("rel-2", "OutRun", 3, groupe="rg-2")]
        found, release, _ = self.resoudre(FauxMusicBrainz(results=rivaux))
        self.assertIsNone(release)
        self.assertEqual([r["id"] for r in found.choice.rivals], ["rel-2"])

    def test_no_ask_garde_le_premier(self):
        self.poser()
        rivaux = [sortie("rel-1", "OutRun", 3, groupe="rg-1"), sortie("rel-2", "OutRun", 3, groupe="rg-2")]
        found, release, _ = self.resoudre(FauxMusicBrainz(results=rivaux), ask=False)
        self.assertEqual((release["id"], found.choice), ("rel-1", None))

    def test_terminal_non_interactif_ne_bloque_pas(self):
        self.poser()
        found = self.trouver()
        found.choice = albums.Choice(sortie("rel-1", "OutRun", 3), [sortie("rel-2", "OutRun", 3, groupe="rg-2")])
        journal = music.Journal()
        with mock.patch.object(music.cli, "can_ask", lambda: False), redirect_stdout(io.StringIO()):
            report = music.resolve_pending([found], journal, arguments(), FauxMusicBrainz())
        self.assertEqual((report.pending, journal.entries[0][2]), (1, "[A CONFIRMER]"))


if __name__ == "__main__":
    unittest.main()
