"""Renommage des episodes : cas ou le plan et le disque ne sont pas d'accord."""

import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from TV_Shows import Rename_Episodes as rename

SAISON = {"season_number": 1, "episodes": [
    {"episode_number": 1, "name": "Le debut"},
    {"episode_number": 2, "name": "Suite"},
]}


class RenameTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def creer(self, *noms):
        for nom in noms:
            (self.dir / nom).write_text(nom, encoding="utf-8")

    def lancer(self, apply=True, saison=SAISON, seuil=0.55):
        """Joue une saison en capturant l'affichage (garde la sortie des tests lisible)."""
        args = types.SimpleNamespace(apply=apply, match_threshold=seuil)
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            resultat = rename.rename_season(self.dir, saison, args)
        self.sortie = sortie.getvalue()
        return resultat, sorted(p.name for p in self.dir.iterdir())


class TestRenommage(RenameTestCase):
    def test_renommage_simple(self):
        self.creer("S01E02.mkv")
        (_, fichiers) = self.lancer()
        self.assertEqual(fichiers, ["02 - Suite.mkv"])

    def test_casse_seule(self):
        # Regression : Windows voit deja le nom cible comme pris, le fichier restait tel quel.
        self.creer("01 - le debut.mkv")
        ((ok, total), fichiers) = self.lancer()
        self.assertEqual(fichiers, ["01 - Le debut.mkv"])
        self.assertEqual((ok, total), (1, 1))

    def test_deux_fichiers_pour_un_episode(self):
        # Regression : la collision n'apparaissait qu'a l'ecriture, sans explication.
        self.creer("S01E02 - vf.mkv", "S01E02 - vostfr.mkv")
        ((ok, total), fichiers) = self.lancer()
        self.assertIn("02 - Suite.mkv", fichiers)
        self.assertIn("S01E02 - vostfr.mkv", fichiers)     # le second est laisse intact
        self.assertEqual((ok, total), (1, 2))
        self.assertIn("[DOUBLON]", self.sortie)

    def test_numerotation_croisee(self):
        # Regression : chacun visait le nom que l'autre portait, les deux etaient abandonnes.
        self.creer("01 - Suite.mkv", "02 - Le debut.mkv")
        ((ok, _), fichiers) = self.lancer()
        self.assertEqual(fichiers, ["01 - Le debut.mkv", "02 - Suite.mkv"])
        self.assertEqual(ok, 2)

    def test_fichier_etranger_non_ecrase(self):
        self.creer("01 - Le debut.mkv", "S01E01 - autre version.mkv")
        (_, fichiers) = self.lancer()
        self.assertIn("S01E01 - autre version.mkv", fichiers)
        self.assertEqual(len(fichiers), 2)
        self.assertIn("[DOUBLON]", self.sortie)

    def test_simulation_n_ecrit_rien(self):
        self.creer("S01E01.mkv")
        (_, fichiers) = self.lancer(apply=False)
        self.assertEqual(fichiers, ["S01E01.mkv"])

    def test_extensions_non_video_ignorees(self):
        self.creer("S01E01.mkv", "S01E01.srt", "notes.txt")
        (_, fichiers) = self.lancer()
        self.assertEqual(sorted(fichiers), ["01 - Le debut.mkv", "S01E01.srt", "notes.txt"])

    def test_largeur_du_numero_suit_la_saison(self):
        saison = {"season_number": 1,
                  "episodes": [{"episode_number": n, "name": f"Ep{n}"} for n in range(1, 101)]}
        self.creer("S01E07.mkv")
        (_, fichiers) = self.lancer(saison=saison)
        self.assertEqual(fichiers, ["007 - Ep7.mkv"])

    def test_non_associe_laisse_en_place(self):
        self.creer("bande annonce.mkv")
        ((ok, total), fichiers) = self.lancer(seuil=0.95)
        self.assertEqual(fichiers, ["bande annonce.mkv"])
        self.assertEqual((ok, total), (0, 1))
        self.assertIn("[NON ASSOCIE]", self.sortie)

    def test_saison_sans_donnees_tmdb(self):
        self.creer("S01E01.mkv")
        (resultat, fichiers) = self.lancer(saison={"season_number": 1, "episodes": []})
        self.assertEqual(resultat, (0, 0))
        self.assertEqual(fichiers, ["S01E01.mkv"])


if __name__ == "__main__":
    unittest.main()
