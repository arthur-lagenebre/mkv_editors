"""Répartition du casting d'une série entre récurrents et saisons (aucun réseau)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import cast


def entry(person, name, character="Role", episodes=1, order=0, profile=None):
    """Une entrée de /aggregate_credits, réduite aux champs qui servent."""
    return {"id": person, "name": name, "profile_path": profile, "order": order,
            "total_episode_count": episodes,
            "roles": [{"character": character, "episode_count": episodes}]}


class TestRegroupement(unittest.TestCase):
    def test_un_acteur_de_deux_saisons_est_recurrent(self):
        casting = cast.split([(1, [entry(7, "Alice", episodes=10)]),
                              (2, [entry(7, "Alice", episodes=8)])])
        self.assertEqual([a.name for a in casting.recurring], ["Alice"])
        self.assertEqual(casting.per_season, [])

    def test_les_episodes_se_cumulent_sur_les_saisons(self):
        casting = cast.split([(1, [entry(7, "Alice", episodes=10)]),
                              (2, [entry(7, "Alice", episodes=8)])])
        actor = casting.recurring[0]
        self.assertEqual((actor.episodes, actor.seasons), (18, [1, 2]))

    def test_un_acteur_d_une_seule_saison_reste_dans_la_sienne(self):
        casting = cast.split([(1, [entry(7, "Alice"), entry(9, "Invite")]),
                              (2, [entry(7, "Alice")])])
        self.assertEqual([a.name for a in casting.recurring], ["Alice"])
        self.assertEqual([(n, [a.name for a in acts]) for n, acts in casting.per_season],
                         [(1, ["Invite"])])

    def test_un_acteur_ne_parait_qu_une_fois(self):
        # Récurrent en tête ET dans chacune de ses saisons, la page le dirait deux fois.
        casting = cast.split([(1, [entry(7, "Alice")]), (2, [entry(7, "Alice")])])
        self.assertNotIn("Alice", [a.name for _, acts in casting.per_season for a in acts])

    def test_une_saison_sans_acteur_propre_n_a_pas_de_section(self):
        casting = cast.split([(1, [entry(7, "Alice")]), (2, [entry(7, "Alice"), entry(9, "Invite")])])
        self.assertEqual([n for n, _ in casting.per_season], [2])

    def test_une_seule_saison_donne_tout_le_casting(self):
        # Rien à comparer : découper par saison répéterait simplement l'onglet.
        casting = cast.split([(3, [entry(7, "Alice"), entry(9, "Invite")])])
        self.assertEqual([a.name for a in casting.recurring], ["Alice", "Invite"])
        self.assertEqual(casting.per_season, [])

    def test_sans_casting_le_resultat_est_vide(self):
        self.assertFalse(cast.split([]))
        self.assertFalse(cast.split([(1, [])]))

    def test_une_entree_sans_identifiant_est_ignoree(self):
        casting = cast.split([(1, [{"name": "Anonyme"}, entry(7, "Alice")])])
        self.assertEqual([a.name for a in casting.recurring], ["Alice"])


class TestOrdreEtPlafond(unittest.TestCase):
    def test_le_plus_de_saisons_passe_devant(self):
        casting = cast.split([(1, [entry(1, "Deux saisons", episodes=1), entry(2, "Une saison", episodes=50)]),
                              (2, [entry(1, "Deux saisons", episodes=1), entry(3, "Autre", episodes=1)])])
        self.assertEqual([a.name for a in casting.recurring], ["Deux saisons"])

    def test_a_egalite_le_plus_d_episodes_passe_devant(self):
        casting = cast.split([(1, [entry(1, "Peu", episodes=2, order=0),
                                   entry(2, "Beaucoup", episodes=20, order=5)])])
        self.assertEqual([a.name for a in casting.recurring], ["Beaucoup", "Peu"])

    def test_a_egalite_complete_le_generique_tranche(self):
        casting = cast.split([(1, [entry(1, "Second role", order=4), entry(2, "Tete d'affiche", order=0)])])
        self.assertEqual([a.name for a in casting.recurring], ["Tete d'affiche", "Second role"])

    def test_le_plafond_garde_les_premiers(self):
        cast_tmdb = [entry(i, f"Acteur {i}", episodes=100 - i) for i in range(10)]
        casting = cast.split([(1, cast_tmdb)], limit=3)
        self.assertEqual([a.name for a in casting.recurring], ["Acteur 0", "Acteur 1", "Acteur 2"])

    def test_le_plafond_vaut_aussi_par_saison(self):
        commun = entry(99, "Vedette", episodes=100)
        saison = [entry(i, f"Invite {i}", episodes=10 - i) for i in range(5)]
        casting = cast.split([(1, [commun] + saison), (2, [commun])], limit=2)
        self.assertEqual([a.name for _, acts in casting.per_season for a in acts], ["Invite 0", "Invite 1"])


class TestRoles(unittest.TestCase):
    def test_les_personnages_d_un_meme_acteur_se_cumulent(self):
        casting = cast.split([(1, [entry(7, "Alice", character="Le medecin")]),
                              (2, [entry(7, "Alice", character="Sa jumelle")])])
        self.assertEqual(casting.recurring[0].character, "Le medecin / Sa jumelle")

    def test_un_personnage_repete_n_est_pas_redit(self):
        casting = cast.split([(1, [entry(7, "Alice", character="Le medecin")]),
                              (2, [entry(7, "Alice", character="Le medecin")])])
        self.assertEqual(casting.recurring[0].character, "Le medecin")

    def test_seuls_les_premiers_personnages_sont_cites(self):
        roles = [{"character": f"Role {i}", "episode_count": 1} for i in range(5)]
        casting = cast.split([(1, [dict(entry(7, "Alice"), roles=roles)])])
        self.assertEqual(casting.recurring[0].character, "Role 0 / Role 1")

    def test_le_compte_retombe_sur_le_detail_des_roles(self):
        # /aggregate_credits d'une saison peut omettre le total : il reste les rôles.
        sans_total = {"id": 7, "name": "Alice", "roles": [{"character": "A", "episode_count": 4},
                                                          {"character": "B", "episode_count": 3}]}
        self.assertEqual(cast.split([(1, [sans_total])]).recurring[0].episodes, 7)


class TestLibelles(unittest.TestCase):
    def test_saisons_contigues_regroupees(self):
        self.assertEqual(cast.season_label([1, 2, 3]), "S1-3")

    def test_trou_dans_les_saisons(self):
        self.assertEqual(cast.season_label([1, 2, 3, 5]), "S1-3, S5")

    def test_saison_seule(self):
        self.assertEqual(cast.season_label([4]), "S4")

    def test_les_speciaux_gardent_leur_numero(self):
        self.assertEqual(cast.season_label([0, 1]), "S0-1")

    def test_une_seule_saison_ne_se_cite_pas(self):
        # Dans la section d'une saison, redire laquelle n'apprend rien.
        actor = cast.Actor(1, "Alice", episodes=3, seasons=[2])
        self.assertEqual(cast.coverage(actor), "3 ép.")

    def test_plusieurs_saisons_se_citent(self):
        actor = cast.Actor(1, "Alice", episodes=30, seasons=[1, 2, 4])
        self.assertEqual(cast.coverage(actor), "S1-2, S4 · 30 ép.")


if __name__ == "__main__":
    unittest.main()
