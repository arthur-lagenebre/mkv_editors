"""Verify_Files : quels fichiers un passage prend en charge."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.Maintenance import Verify_Files as verificateur


class TestSelectionDesFichiers(unittest.TestCase):
    """Une médiathèque mêle des films posés à plat et des dossiers de film."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.racine = Path(self._tmp.name)
        (self.racine / "Blade (1998).mkv").write_bytes(b"")
        dossier = self.racine / "Inception (2010)"
        dossier.mkdir()
        (dossier / "film.mkv").write_bytes(b"")
        (dossier / "notes.txt").write_text("x", encoding="utf-8")

    def test_recursif_par_defaut(self):
        # Les films posés à la racine d'abord, puis dossier par dossier.
        noms = [p.name for p in verificateur.mkv_files(self.racine)]
        self.assertEqual(noms, ["Blade (1998).mkv", "film.mkv"])

    def test_sans_recursion_l_etage_seul(self):
        # --no-recursive : contrôler un étage sans relire les dossiers qu'il range.
        noms = [p.name for p in verificateur.mkv_files(self.racine, recursif=False)]
        self.assertEqual(noms, ["Blade (1998).mkv"])


if __name__ == "__main__":
    unittest.main()
