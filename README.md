# Dashboard Adhérence & Suivi Comportemental — CSA

Application Streamlit qui croise le **Planning Hebdo** avec deux exports Vocalcom.

```bash
pip install -r requirements.txt
streamlit run app.py
```
Le bouton **🧪 Démo** de la barre latérale charge des données fictives pour tester.

## Fichiers en entrée

| Fichier | Rôle |
|---|---|
| **Planning Hebdo** (.xlsx, feuille `Planning_Hebdo`) | Heures théoriques par agent et par jour |
| **Agents activities by dates** | **Seule source** des arrivées / départs réels (jointure par Login, nom tronqué) |
| **Agents report** | États : Attente, Traitement, Post-travail, Pause + sous-catégories (ex. motifs de pause) |

L'export « Agents pause report » n'est **plus utilisé** (reconnu puis ignoré). Le type de chaque
fichier est détecté d'après son contenu, pas son nom.

## Paramètres
- Seuil de retard toléré (min)
- Export en cours de journée (temps réel) + heure d'extraction
- Date de l'export (utilisée seulement si elle n'est pas détectée dans le fichier)

Plus de pause autorisée, ni de seuil d'alerte de pause, ni de timeline des pauses.

## Onglets
1. **Dashboard** : KPI (présence, absences, retards, taux de travail, % attente, % pause), donut des états
   (principaux ↔ détail), sunburst cliquable, ponctualité.
2. **États par agent** : classement par n'importe quel état ou sous-état (ex. *Post-travail*, *Pause Ostie*),
   barres empilées de toute l'équipe.
3. **Matrice** : planning vs réel + % de travail / post-travail / attente / pause, export CSV.
4. **Fiche individuelle** : prise/fin de poste, planning vs réel, **post-travail** (%, durée, moyenne par contact,
   comparaison à l'équipe), répartition détaillée, synthèse d'entretien.

## Règles de calcul
- **Retard** = arrivée − début prévu, compté s'il dépasse le seuil.
- **Départ anticipé** = fin prévue − départ réel (ignoré en temps réel si la fin prévue est après l'heure d'extraction).
- **Pourcentages d'états** = temps de l'état ÷ somme des états principaux de l'agent (total = 100 %).
- **Taux de travail** = Traitement + Post-travail.
- Groupes d'états reconnus par le début du libellé : *Attente*, *Traitement*, *Post…*, *Pause…* ; le reste = *Autre*.
  Adapter `analysis.etat_groupe` si vos libellés diffèrent.
- Shifts éclatés : borne globale (début le plus tôt / fin la plus tardive).
