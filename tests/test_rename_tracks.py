"""Music/Rename_Tracks.py : les noms visés, et des albums renommés pour de vrai d'après une sortie MusicBrainz."""

import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musiclib import album as albums
from musiclib import lookup
from scripts.Music import Rename_Tracks as renamer
from tests.test_album import DOUBLE, OUTRUN, piste
from tests.test_flac import fichier
from tests.test_music_metadata import AlbumTestCase

MEDIUM = {"position": 2}


class TestNomVise(unittest.TestCase):
    def test_numero_et_titre(self):
        self.assertEqual(renamer.track_stem(MEDIUM, piste(3, "Protovision"), 2), "03 - Protovision")

    def test_largeur_imposee_par_l_album(self):
        self.assertEqual(renamer.track_stem(MEDIUM, piste(7, "Rinzler"), 3), "007 - Rinzler")

    def test_disque_prefixe(self):
        self.assertEqual(renamer.track_stem(MEDIUM, piste(1, "Alive 1997"), 2, with_disc=True), "2-01 - Alive 1997")

    def test_medley_garde_ses_separateurs(self):
        # Supprimer le "/" comme les autres caractères interdits collerait les deux morceaux.
        self.assertEqual(renamer.track_stem(MEDIUM, piste(1, "Robot Rock / Oh Yeah"), 2), "01 - Robot Rock - Oh Yeah")

    def test_caracteres_interdits_retires(self):
        self.assertEqual(renamer.track_stem(MEDIUM, piste(9, 'TRON: "Legacy"?'), 2), "09 - TRON Legacy")


class RenameTracksTestCase(AlbumTestCase):
    def run_album(self, release, apply=True):
        """Renomme l'album du dossier d'après `release`, en capturant l'affichage."""
        (found_album,) = albums.find_albums(self.dossier.parent)
        metas, _ = lookup.read_album(found_album)
        output = io.StringIO()
        with redirect_stdout(output):
            tally = renamer.rename_album(lookup.Found(found_album, metas), release, types.SimpleNamespace(apply=apply))
        self.output = output.getvalue()
        return tally

    def names(self, folder=None):
        return sorted(p.name for p in (folder or self.dossier).iterdir())

    def place(self, relative, *lines):
        path = self.dossier / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fichier(lines))


class TestRenommage(RenameTracksTestCase):
    release = OUTRUN

    def test_renommage_d_apres_musicbrainz(self):
        self.poser(name_pattern="{n}. Kavinsky - {title}.flac")
        tally = self.run_album(self.release)
        self.assertEqual(self.names(), ["01 - Prelude.flac", "02 - Blizzard.flac", "03 - Protovision.flac"])
        self.assertEqual(tally.named, 3)

    def test_casse_seule(self):
        # Cas réel : "03 - ProtoVision.flac", que Windows voit déjà au nom de "03 - Protovision.flac".
        self.poser(titres=("Prelude", "Blizzard", "ProtoVision"))
        tally = self.run_album(self.release)
        self.assertEqual(self.names(), ["01 - Prelude.flac", "02 - Blizzard.flac", "03 - Protovision.flac"])
        self.assertEqual(tally.named, 3)

    def test_deja_au_bon_nom(self):
        self.poser()
        tally = self.run_album(self.release)
        self.assertEqual(tally.named, 3)
        self.assertIn("deja au bon nom", self.output)

    def test_simulation_ne_renomme_rien(self):
        self.poser(name_pattern="{n} {title}.flac")
        tally = self.run_album(self.release, apply=False)
        self.assertEqual(self.names(), ["1 Prelude.flac", "2 Blizzard.flac", "3 Protovision.flac"])
        self.assertEqual(tally.named, 0)
        self.assertIn("-> 01 - Prelude.flac", self.output)

    def test_un_fichier_sans_piste_bloque_tout_l_album(self):
        self.poser(titres=("Prelude", "Blizzard", "Protovision", "Bonus inconnu"), name_pattern="{n} {title}.flac")
        before = self.names()
        tally = self.run_album(self.release)
        self.assertEqual(self.names(), before)
        self.assertEqual(tally.named, 0)
        self.assertIn("[NON TRAITE]", self.output)

    def test_paroles_suivent_leur_piste(self):
        self.poser(name_pattern="{n} {title}.flac")
        (self.dossier / "1 Prelude.lrc").write_text("[00:01.00] ...", encoding="utf-8")
        tally = self.run_album(self.release)
        self.assertIn("01 - Prelude.lrc", self.names())
        self.assertEqual(tally.subtitles, 1)


class TestPlusieursDisques(RenameTracksTestCase):
    def test_disques_a_plat_prefixes(self):
        self.place("a.flac", "TITLE=Robot Rock", "TRACKNUMBER=1", "DISCNUMBER=1")
        self.place("b.flac", "TITLE=Touch It", "TRACKNUMBER=2", "DISCNUMBER=1")
        self.place("c.flac", "TITLE=Alive 1997", "TRACKNUMBER=1", "DISCNUMBER=2")
        self.run_album(DOUBLE)
        self.assertEqual(self.names(), ["1-01 - Robot Rock.flac", "1-02 - Touch It.flac", "2-01 - Alive 1997.flac"])

    def test_dossiers_de_disque_repartent_de_01(self):
        self.place("CD1/a.flac", "TITLE=Robot Rock", "TRACKNUMBER=1")
        self.place("CD1/b.flac", "TITLE=Touch It", "TRACKNUMBER=2")
        self.place("CD2/c.flac", "TITLE=Alive 1997", "TRACKNUMBER=1")
        self.run_album(DOUBLE)
        self.assertEqual(self.names(self.dossier / "CD1"), ["01 - Robot Rock.flac", "02 - Touch It.flac"])
        self.assertEqual(self.names(self.dossier / "CD2"), ["01 - Alive 1997.flac"])


if __name__ == "__main__":
    unittest.main()
