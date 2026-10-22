# -*- coding: utf-8 -*-
"""demo.py — données fictives pour tester le dashboard sans export Vocalcom."""
import datetime as dt
import random

import pandas as pd

import parsers as P


def build_demo(date: dt.date, n: int = 24, seed: int = 7):
    rnd = random.Random(seed)
    jour = P.date_to_jour_fr(date)
    prenoms = ["Aina", "Hery", "Lanto", "Mamy", "Nirina", "Rivo", "Tiana", "Voahangy", "Fenitra",
               "Joel", "Kanto", "Miora", "Njaka", "Onja", "Sarobidy", "Toky", "Zo", "Haja",
               "Fanja", "Mirana", "Rado", "Soa", "Tahina", "Faly"]
    plan, acts, states = [], [], []
    for i in range(n):
        login = str(1000 + i)
        nom = f"{prenoms[i % len(prenoms)]} Demo{i}"
        debut = dt.time(7, 0) if i % 2 == 0 else dt.time(8, 0)
        fin = dt.time(15, 0) if i % 2 == 0 else dt.time(16, 0)
        row = {"Matricule_RH": f"M{200 + i}", "Login": login, "Nom": nom}
        for j in P.JOURS_FR:
            row[f"{j}_debut"], row[f"{j}_fin"] = debut, fin
        if i == n - 1:
            row[f"{jour}_debut"] = row[f"{jour}_fin"] = "OFF"
        plan.append(row)
        if i in (n - 1, n - 2):  # OFF + une absence
            continue
        retard = rnd.choice([0, 0, 0, 2, 4, 9, 15, 25])
        arr = (dt.datetime.combine(date, debut) + dt.timedelta(minutes=retard - rnd.randint(0, 3)))
        dep_min = 0 if rnd.random() > 0.2 else rnd.randint(10, 40)
        dep = dt.datetime.combine(date, fin) - dt.timedelta(minutes=dep_min)
        dur = (dep - arr).total_seconds() / 60
        acts.append({"Login": login, "Nom_tronque": nom[:14], "Arrivee": arr.time().replace(second=0),
                     "Depart": dep.time().replace(second=0), "Duree_travail_min": round(dur, 1)})
        pause = dur * rnd.uniform(0.08, 0.2); post = dur * rnd.uniform(0.06, 0.18)
        att = dur * rnd.uniform(0.15, 0.4); trt = max(dur - pause - post - att, 0)
        ostie = pause * rnd.uniform(0, 0.35) if i % 5 == 0 else 0
        dej = pause * rnd.uniform(0.3, 0.6)
        base = {"Login": login, "Nom": nom, "Temps_presence_min": round(dur, 1)}
        def add(cat, parent, niv, t, occ):
            states.append({**base, "Categorie": cat, "Parent": parent, "Niveau": niv,
                           "Occurences": occ, "Temps_total_min": round(t, 2), "Pourcent_temps": None})
        add("Attente", None, 1, att, rnd.randint(20, 60))
        add("Traitement", None, 1, trt, rnd.randint(20, 60))
        add("Appel entrant", "Traitement", 2, trt * 0.8, rnd.randint(15, 50))
        add("Appel sortant", "Traitement", 2, trt * 0.2, rnd.randint(2, 15))
        add("Post-travail", None, 1, post, rnd.randint(15, 50))
        add("Pause", None, 1, pause, rnd.randint(2, 6))
        add("Pause déjeuner", "Pause", 2, dej, 1)
        add("Pause Ostie", "Pause", 2, ostie, 1 if ostie else 0)
        add("Pause technique", "Pause", 2, max(pause - dej - ostie, 0), rnd.randint(1, 4))
    return pd.DataFrame(plan), pd.DataFrame(acts), pd.DataFrame(states)
