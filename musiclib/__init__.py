"""musiclib — briques des scripts de musique : MusicBrainz, fichiers .flac, albums.

Les scripts de Music/ ne gardent que leur déroulé : l'accès à MusicBrainz et à Cover Art Archive, la lecture et l'écriture des .flac, la reconnaissance des albums et le choix de leur édition vivent ici, à part de mkvlib, qui ne connaît que les films et les séries.

Ce qui ne dépend d'aucun des deux domaines - cache disque, ligne de commande, bilan d'un passage, listage des fichiers - reste dans mkvlib, en un seul exemplaire, et musiclib s'en sert.

Aucune dépendance pip : uniquement la bibliothèque standard.
"""
