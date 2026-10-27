#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================
 VEILLE JURIDIQUE AUTOMATISÉE — IA ET DROIT
 Côte d'Ivoire · Maroc · France
============================================================

Ce que fait ce script, dans l'ordre :

  1. COLLECTE  : il lit les flux RSS listés dans sources.yaml
  2. FILTRE    : il ne garde que les articles qui parlent à la fois
                 d'IA/numérique ET de droit (voir mots_cles.yaml)
  3. DÉDOUBLONNE : il écarte les articles déjà vus les fois précédentes
  4. RÉSUME    : il demande à l'IA (Groq) un résumé en 3 lignes
                 et un classement par thème  [étape facultative]
  5. PUBLIE    : il écrit un bulletin HTML + une archive CSV
                 et, si tu le configures, l'envoie par mail

Pour le lancer :
    python veille.py                 -> veille normale
    python veille.py --test          -> démonstration hors ligne (sans internet)
    python veille.py --sans-ia       -> sans appeler Groq (plus rapide, gratuit)
    python veille.py --jours 14      -> remonte 14 jours en arrière au lieu de 7
"""

# --- Bibliothèques fournies avec Python ---------------------
import argparse          # pour lire les options de la ligne de commande
import csv               # pour écrire l'archive en tableau
import html               # pour échapper les caractères spéciaux dans le HTML
import json              # pour mémoriser les articles déjà vus
import os                # pour lire les clés secrètes (variables d'environnement)
import re                # pour nettoyer le texte
import smtplib           # pour l'envoi du mail
import sys               # pour arrêter le script proprement en cas d'erreur
import unicodedata       # pour ignorer les accents dans le filtrage
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

# --- Bibliothèques à installer (voir requirements.txt) ------
try:
    import feedparser    # lit les flux RSS
    import requests      # télécharge les pages et parle à l'API Groq
    import yaml          # lit les fichiers de configuration .yaml
except ImportError as erreur:
    print("Bibliothèque manquante :", erreur)
    print("Lance d'abord :  pip install -r requirements.txt")
    sys.exit(1)


# ============================================================
#  RÉGLAGES GÉNÉRAUX
#  (modifie ici si tu veux changer le comportement par défaut)
# ============================================================

DOSSIER = Path(__file__).parent          # le dossier où se trouve ce script
DOSSIER_DATA = DOSSIER / "data"          # où sont stockés l'historique et les bulletins

FICHIER_VUS = DOSSIER_DATA / "deja_vus.json"   # mémoire des articles déjà traités
FICHIER_ARCHIVE = DOSSIER_DATA / "archive.csv" # tableau de tous les articles retenus

JOURS_PAR_DEFAUT = 7        # on regarde les 7 derniers jours
MAX_PAR_SOURCE = 40         # on ne lit pas plus de 40 articles par flux
MAX_RESUMES_IA = 25         # on ne demande pas plus de 25 résumés à l'IA par bulletin

# Modèle Groq utilisé pour les résumés. Si Groq change ses modèles,
# c'est la seule ligne à modifier.
MODELE_GROQ = "llama-3.3-70b-versatile"

# Certains sites refusent les robots sans "carte de visite".
# On se présente donc comme un navigateur classique.
ENTETES = {
    "User-Agent": "Mozilla/5.0 (compatible; VeilleJuridiqueIA/1.0)"
}


# ============================================================
#  PETITS OUTILS
# ============================================================

def sans_accents(texte):
    """Transforme 'décision' en 'decision' et met tout en minuscules.
    Cela permet de comparer les mots sans se soucier des accents."""
    texte = texte.lower()
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return texte


def nettoyer_html(texte):
    """Retire les balises HTML d'un résumé RSS pour garder le texte brut."""
    if not texte:
        return ""
    texte = re.sub(r"<[^>]+>", " ", texte)      # supprime <p>, <a href=...>, etc.
    texte = html.unescape(texte)                 # remplace &eacute; par é
    texte = re.sub(r"\s+", " ", texte)           # réduit les espaces multiples
    return texte.strip()


def charger_yaml(nom_fichier):
    """Ouvre un fichier .yaml et renvoie son contenu sous forme de dictionnaire."""
    chemin = DOSSIER / nom_fichier
    if not chemin.exists():
        print(f"Fichier introuvable : {chemin}")
        sys.exit(1)
    with open(chemin, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def charger_deja_vus():
    """Récupère la liste des liens déjà traités lors des exécutions précédentes."""
    if FICHIER_VUS.exists():
        try:
            with open(FICHIER_VUS, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, ValueError):
            # Fichier abîmé : on repart de zéro plutôt que de planter
            print("Attention : le fichier d'historique est illisible, il est réinitialisé.")
            return set()
    return set()


def enregistrer_deja_vus(liens):
    """Sauvegarde la liste des liens traités. On garde les 3000 plus récents
    pour que le fichier ne grossisse pas indéfiniment."""
    DOSSIER_DATA.mkdir(exist_ok=True)
    liste = list(liens)[-3000:]
    with open(FICHIER_VUS, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=1)


# ============================================================
#  ÉTAPE 1 — COLLECTE
# ============================================================

def lire_un_flux(source):
    """Télécharge un flux RSS et renvoie la liste de ses articles.
    En cas de problème (site en panne, blocage, pas d'internet),
    la fonction renvoie une liste vide au lieu d'arrêter le script."""
    nom = source.get("nom", "source sans nom")
    url = source.get("url", "")

    try:
        reponse = requests.get(url, headers=ENTETES, timeout=25)
        reponse.raise_for_status()      # déclenche une erreur si le code HTTP est 404, 500...
        flux = feedparser.parse(reponse.content)
    except Exception as erreur:
        print(f"  [échec]   {nom} : {type(erreur).__name__}")
        return []

    articles = []
    for entree in flux.entries[:MAX_PAR_SOURCE]:
        # La date peut s'appeler "published" ou "updated" selon les flux
        date_struct = entree.get("published_parsed") or entree.get("updated_parsed")
        if date_struct:
            date = datetime(*date_struct[:6], tzinfo=timezone.utc)
        else:
            date = datetime.now(timezone.utc)   # pas de date fournie : on met aujourd'hui

        articles.append({
            "titre": nettoyer_html(entree.get("title", "(sans titre)")),
            "lien": entree.get("link", ""),
            "date": date,
            "extrait": nettoyer_html(entree.get("summary", ""))[:1200],
            "source": nom,
            "pays": source.get("pays", "Non précisé"),
            "type_source": source.get("type", "presse"),
        })

    print(f"  [ok]      {nom} : {len(articles)} articles")
    return articles


def collecter(config_sources):
    """Parcourt toutes les sources actives et rassemble leurs articles."""
    print("\n--- 1. COLLECTE DES SOURCES ---")
    tous = []
    for source in config_sources.get("sources", []):
        if not source.get("actif", True):
            continue
        tous.extend(lire_un_flux(source))
    print(f"Total collecté : {len(tous)} articles")
    return tous


# ============================================================
#  ÉTAPE 2 — FILTRE
# ============================================================

def article_pertinent(article, mots):
    """Renvoie True si l'article mérite d'entrer dans le bulletin.
    Règle : au moins un mot du thème IA ET au moins un mot juridique,
    et aucun mot de la liste 'exclus' dans le titre."""
    texte = sans_accents(article["titre"] + " " + article["extrait"])
    titre = sans_accents(article["titre"])

    # 1. Rejet immédiat si un mot indésirable est dans le titre
    for mot in mots.get("exclus", []):
        if sans_accents(mot) in titre:
            return False

    # 2. Le sujet doit toucher à l'IA ou au numérique
    theme_ok = any(sans_accents(m) in texte for m in mots.get("obligatoires", []))

    # 3. Le sujet doit aussi toucher au droit
    droit_ok = any(sans_accents(m) in texte for m in mots.get("juridiques", []))

    return theme_ok and droit_ok


def filtrer(articles, mots, jours, deja_vus):
    """Applique le filtre thématique, la limite de date et le dédoublonnage."""
    print("\n--- 2. FILTRAGE ---")
    limite = datetime.now(timezone.utc) - timedelta(days=jours)

    retenus = []
    liens_de_ce_tour = set()

    for article in articles:
        if article["date"] < limite:
            continue                                    # trop ancien
        if not article["lien"]:
            continue                                    # sans lien, inutilisable
        if article["lien"] in deja_vus:
            continue                                    # déjà envoyé dans un bulletin précédent
        if article["lien"] in liens_de_ce_tour:
            continue                                    # doublon dans la même collecte
        if not article_pertinent(article, mots):
            continue                                    # hors sujet

        liens_de_ce_tour.add(article["lien"])
        retenus.append(article)

    # Les plus récents en premier
    retenus.sort(key=lambda a: a["date"], reverse=True)
    print(f"{len(retenus)} articles retenus (sur {len(articles)} collectés)")
    return retenus


# ============================================================
#  ÉTAPE 3 — RÉSUMÉ PAR L'IA (facultatif)
# ============================================================

def resumer_avec_groq(article, cle_api):
    """Demande à Groq un résumé court et un thème.
    Si l'appel échoue, on renvoie l'extrait RSS d'origine :
    le bulletin est produit quoi qu'il arrive."""

    consigne = (
        "Tu assistes un juriste qui suit l'actualité du droit de l'intelligence "
        "artificielle en Côte d'Ivoire, au Maroc et en France.\n"
        "Résume l'article ci-dessous en 2 à 3 phrases factuelles, en français, "
        "sans formule d'introduction. Si le texte est en arabe ou en anglais, "
        "résume-le quand même en français.\n"
        "Puis, sur une dernière ligne, indique le thème sous la forme exacte :\n"
        "THEME: <un seul thème parmi : IA et justice | Données personnelles | "
        "Régulation de l'IA | Responsabilité | Éthique et droits fondamentaux | Autre>\n\n"
        f"TITRE : {article['titre']}\n"
        f"EXTRAIT : {article['extrait'][:1500]}"
    )

    try:
        reponse = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {cle_api}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODELE_GROQ,
                "messages": [{"role": "user", "content": consigne}],
                "temperature": 0.2,      # réponse stable, peu « créative »
                "max_tokens": 350,
            },
            timeout=45,
        )
        reponse.raise_for_status()
        texte = reponse.json()["choices"][0]["message"]["content"].strip()
    except Exception as erreur:
        print(f"  [IA indisponible] {type(erreur).__name__} — extrait RSS conservé")
        return article["extrait"][:400], "Non classé"

    # On sépare le résumé de la ligne "THEME:"
    theme = "Non classé"
    lignes_resume = []
    for ligne in texte.splitlines():
        if sans_accents(ligne).startswith("theme:"):
            theme = ligne.split(":", 1)[1].strip()
        elif ligne.strip():
            lignes_resume.append(ligne.strip())

    return " ".join(lignes_resume), theme


def resumer_tout(articles, utiliser_ia):
    """Ajoute un résumé et un thème à chaque article retenu."""
    print("\n--- 3. RÉSUMÉS ---")

    cle_api = os.environ.get("GROQ_API_KEY", "").strip()

    if not utiliser_ia:
        print("Mode sans IA : les extraits RSS sont conservés tels quels.")
    elif not cle_api:
        print("Aucune clé GROQ_API_KEY trouvée : les extraits RSS sont conservés.")
        utiliser_ia = False
    else:
        print(f"Résumé par Groq des {min(len(articles), MAX_RESUMES_IA)} premiers articles...")

    for i, article in enumerate(articles):
        if utiliser_ia and i < MAX_RESUMES_IA:
            article["resume"], article["theme"] = resumer_avec_groq(article, cle_api)
        else:
            article["resume"] = article["extrait"][:400]
            article["theme"] = "Non classé"

    return articles


# ============================================================
#  ÉTAPE 4 — PUBLICATION
# ============================================================

# Correspondance entre le type de source et le niveau de fiabilité affiché
FIABILITE = {
    "officiel": ("Source officielle", "#1a7f37"),
    "regulateur": ("Autorité de régulation", "#1a7f37"),
    "presse": ("Presse — à vérifier", "#9a6700"),
    "doctrine": ("Doctrine", "#0969da"),
}


def construire_html(articles, config_sources, jours):
    """Fabrique le bulletin au format HTML, lisible dans un navigateur
    et directement envoyable par mail."""

    aujourdhui = datetime.now().strftime("%d/%m/%Y")

    # On regroupe les articles par pays pour la lecture
    par_pays = {}
    for article in articles:
        par_pays.setdefault(article["pays"], []).append(article)

    ordre_pays = ["Côte d'Ivoire", "Maroc", "France", "Europe"]
    pays_tries = [p for p in ordre_pays if p in par_pays]
    pays_tries += [p for p in par_pays if p not in ordre_pays]

    morceaux = []
    morceaux.append(f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veille juridique IA — {aujourdhui}</title>
<style>
  body {{ font-family: Georgia, "Times New Roman", serif; line-height: 1.6;
         max-width: 820px; margin: 0 auto; padding: 24px; color: #1a1a1a; background: #fdfdfb; }}
  h1 {{ font-size: 26px; border-bottom: 3px solid #1a1a1a; padding-bottom: 10px; margin-bottom: 4px; }}
  .sous-titre {{ color: #555; font-size: 14px; margin-bottom: 28px; }}
  h2 {{ font-size: 20px; margin-top: 36px; background: #1a1a1a; color: #fff;
        padding: 8px 14px; border-radius: 4px; }}
  .article {{ border-left: 3px solid #ddd; padding: 2px 0 2px 16px; margin: 22px 0; }}
  .article h3 {{ font-size: 16px; margin: 0 0 6px 0; font-weight: normal; }}
  .article h3 a {{ color: #0b4f9e; text-decoration: none; }}
  .article h3 a:hover {{ text-decoration: underline; }}
  .meta {{ font-size: 12px; color: #666; margin-bottom: 8px; font-family: Arial, sans-serif; }}
  .badge {{ display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 11px;
            color: #fff; margin-right: 6px; }}
  .theme {{ display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 11px;
            background: #eee; color: #333; margin-right: 6px; }}
  .resume {{ font-size: 15px; margin: 0; }}
  .avertissement {{ background: #fff8e1; border-left: 4px solid #e0a800; padding: 12px 16px;
                    font-size: 14px; margin: 24px 0; }}
  .manuel {{ background: #f3f4f6; padding: 14px 18px; border-radius: 6px; font-size: 14px; }}
  .manuel li {{ margin-bottom: 4px; }}
  footer {{ margin-top: 40px; font-size: 12px; color: #777; border-top: 1px solid #ddd; padding-top: 12px; }}
</style>
</head>
<body>

<h1>Veille juridique — Intelligence artificielle et droit</h1>
<div class="sous-titre">
  Bulletin du {aujourdhui} · {len(articles)} éléments sur les {jours} derniers jours ·
  Côte d'Ivoire · Maroc · France
</div>

<div class="avertissement">
  <strong>À vérifier avant toute citation.</strong> Les résumés sont générés
  automatiquement et peuvent comporter des erreurs ou des approximations.
  Avant d'utiliser un élément dans un travail universitaire ou un conseil,
  remonte toujours au texte officiel via le lien fourni.
</div>
""")

    if not articles:
        morceaux.append(
            "<p><em>Aucun élément nouveau sur la période. "
            "Cela peut signifier qu'il n'y a pas d'actualité, "
            "ou qu'une source est temporairement indisponible.</em></p>"
        )

    for pays in pays_tries:
        morceaux.append(f"<h2>{html.escape(pays)} &nbsp;<span style='font-weight:normal;font-size:14px'>"
                        f"({len(par_pays[pays])})</span></h2>")

        for article in par_pays[pays]:
            libelle, couleur = FIABILITE.get(article["type_source"], ("Source", "#666"))
            date_fr = article["date"].strftime("%d/%m/%Y")

            morceaux.append(f"""
<div class="article">
  <h3><a href="{html.escape(article['lien'])}">{html.escape(article['titre'])}</a></h3>
  <div class="meta">
    <span class="badge" style="background:{couleur}">{libelle}</span>
    <span class="theme">{html.escape(article.get('theme', 'Non classé'))}</span>
    {date_fr} · {html.escape(article['source'])}
  </div>
  <p class="resume">{html.escape(article.get('resume', ''))}</p>
</div>""")

    # Rappel des sources à consulter soi-même
    manuelles = config_sources.get("surveillance_manuelle", [])
    if manuelles:
        morceaux.append("<h2>À consulter à la main cette semaine</h2>")
        morceaux.append("<div class='manuel'><p>Ces sites n'ont pas de flux exploitable "
                        "par le script. Un passage de 10 minutes suffit :</p><ul>")
        for s in manuelles:
            morceaux.append(
                f"<li><a href=\"{html.escape(s['url'])}\">{html.escape(s['nom'])}</a> "
                f"<span style='color:#666'>({html.escape(s['pays'])})</span></li>"
            )
        morceaux.append("</ul></div>")

    morceaux.append(f"""
<footer>
  Bulletin produit automatiquement par <code>veille.py</code> le {aujourdhui}.
  Les sources sont modifiables dans <code>sources.yaml</code>,
  les mots-clés dans <code>mots_cles.yaml</code>.
</footer>
</body>
</html>""")

    return "\n".join(morceaux)


def ecrire_archive(articles):
    """Ajoute les articles du jour au fichier CSV, ouvrable dans Excel.
    L'en-tête n'est écrit qu'une seule fois, à la création du fichier."""
    DOSSIER_DATA.mkdir(exist_ok=True)
    nouveau = not FICHIER_ARCHIVE.exists()

    with open(FICHIER_ARCHIVE, "a", newline="", encoding="utf-8-sig") as f:
        # utf-8-sig : permet à Excel d'afficher correctement les accents
        colonnes = ["date", "pays", "source", "type_source", "theme", "titre", "resume", "lien"]
        ecrivain = csv.DictWriter(f, fieldnames=colonnes, delimiter=";")
        if nouveau:
            ecrivain.writeheader()
        for a in articles:
            ecrivain.writerow({
                "date": a["date"].strftime("%Y-%m-%d"),
                "pays": a["pays"],
                "source": a["source"],
                "type_source": a["type_source"],
                "theme": a.get("theme", ""),
                "titre": a["titre"],
                "resume": a.get("resume", ""),
                "lien": a["lien"],
            })


def envoyer_mail(contenu_html, nombre):
    """Envoie le bulletin par mail, uniquement si les trois variables
    d'environnement sont renseignées. Sinon, on passe sans rien dire d'inquiétant."""
    expediteur = os.environ.get("MAIL_EXPEDITEUR", "").strip()
    mot_de_passe = os.environ.get("MAIL_MOT_DE_PASSE", "").strip()
    destinataire = os.environ.get("MAIL_DESTINATAIRE", "").strip()

    if not (expediteur and mot_de_passe and destinataire):
        print("Envoi par mail non configuré : le bulletin reste en fichier.")
        return

    message = EmailMessage()
    message["Subject"] = f"Veille IA & droit — {nombre} éléments — {datetime.now().strftime('%d/%m/%Y')}"
    message["From"] = expediteur
    message["To"] = destinataire
    message.set_content("Ce bulletin s'affiche en HTML. Ouvre-le dans un client qui le supporte.")
    message.add_alternative(contenu_html, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as serveur:
            serveur.login(expediteur, mot_de_passe)
            serveur.send_message(message)
        print(f"Bulletin envoyé à {destinataire}")
    except Exception as erreur:
        print(f"Échec de l'envoi du mail : {type(erreur).__name__} — {erreur}")


# ============================================================
#  MODE DÉMONSTRATION (sans internet)
# ============================================================

def articles_de_demonstration():
    """Trois faux articles pour vérifier que tout fonctionne
    sans dépendre d'internet ni d'une clé API."""
    maintenant = datetime.now(timezone.utc)
    return [
        {
            "titre": "La CNIL publie ses recommandations sur les systèmes d'intelligence artificielle",
            "lien": "https://exemple.test/cnil-recommandations-ia",
            "date": maintenant - timedelta(days=1),
            "extrait": "L'autorité précise les obligations pesant sur les responsables de "
                       "traitement qui déploient des algorithmes d'apprentissage automatique, "
                       "au regard du RGPD et du règlement européen sur l'IA.",
            "source": "CNIL - actualités",
            "pays": "France",
            "type_source": "regulateur",
        },
        {
            "titre": "Maroc : un projet de loi sur l'encadrement de l'intelligence artificielle en préparation",
            "lien": "https://exemple.test/maroc-projet-loi-ia",
            "date": maintenant - timedelta(days=2),
            "extrait": "Le texte, en cours d'élaboration, viendrait compléter la loi 09-08 "
                       "relative à la protection des données à caractère personnel et "
                       "préciser le rôle de la CNDP en matière de décision automatisée.",
            "source": "Google Actualités - IA et droit (Maroc)",
            "pays": "Maroc",
            "type_source": "presse",
        },
        {
            "titre": "Côte d'Ivoire : l'ARTCI rappelle les obligations de la loi 2013-450 face aux traitements algorithmiques",
            "lien": "https://exemple.test/artci-2013-450-algorithmes",
            "date": maintenant - timedelta(days=4),
            "extrait": "L'autorité de régulation souligne que les traitements fondés sur "
                       "des algorithmes restent soumis à déclaration et que la responsabilité "
                       "du responsable de traitement demeure entière.",
            "source": "Google Actualités - ARTCI (Côte d'Ivoire)",
            "pays": "Côte d'Ivoire",
            "type_source": "presse",
        },
    ]


# ============================================================
#  PROGRAMME PRINCIPAL
# ============================================================

def main():
    analyseur = argparse.ArgumentParser(description="Veille juridique IA — CI / MA / FR")
    analyseur.add_argument("--jours", type=int, default=JOURS_PAR_DEFAUT,
                           help="nombre de jours à remonter (7 par défaut)")
    analyseur.add_argument("--sans-ia", action="store_true",
                           help="ne pas appeler Groq pour les résumés")
    analyseur.add_argument("--test", action="store_true",
                           help="mode démonstration, sans internet")
    options = analyseur.parse_args()

    print("=" * 60)
    print("  VEILLE JURIDIQUE IA — Côte d'Ivoire · Maroc · France")
    print("=" * 60)

    config_sources = charger_yaml("sources.yaml")
    mots = charger_yaml("mots_cles.yaml")
    deja_vus = charger_deja_vus()
    print(f"Historique : {len(deja_vus)} articles déjà traités précédemment.")

    # 1. Collecte
    if options.test:
        print("\n--- MODE DÉMONSTRATION (aucun appel réseau) ---")
        bruts = articles_de_demonstration()
        deja_vus = set()          # en test, on ignore l'historique
    else:
        bruts = collecter(config_sources)

    # 2. Filtre + dédoublonnage
    retenus = filtrer(bruts, mots, options.jours, deja_vus)

    # 3. Résumés
    retenus = resumer_tout(retenus, utiliser_ia=not options.sans_ia and not options.test)

    # 4. Publication
    print("\n--- 4. PUBLICATION ---")
    DOSSIER_DATA.mkdir(exist_ok=True)

    contenu = construire_html(retenus, config_sources, options.jours)

    nom_date = datetime.now().strftime("%Y-%m-%d")
    fichier_bulletin = DOSSIER_DATA / f"bulletin-{nom_date}.html"
    fichier_dernier = DOSSIER_DATA / "bulletin-dernier.html"

    for chemin in (fichier_bulletin, fichier_dernier):
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(contenu)

    print(f"Bulletin écrit : {fichier_bulletin}")

    if retenus:
        ecrire_archive(retenus)
        print(f"Archive mise à jour : {FICHIER_ARCHIVE}")

    # On mémorise les liens traités, sauf en mode démonstration
    if not options.test:
        deja_vus.update(a["lien"] for a in retenus)
        enregistrer_deja_vus(deja_vus)
        envoyer_mail(contenu, len(retenus))

    print("\nTerminé. Ouvre le fichier bulletin-dernier.html dans ton navigateur.")


if __name__ == "__main__":
    main()
