# Veille juridique automatisée — IA et droit
### Côte d'Ivoire · Maroc · France

Ce dossier contient un dispositif de veille qui collecte chaque semaine
l'actualité juridique sur l'intelligence artificielle dans trois pays,
la filtre, la résume et produit un bulletin lisible.

Tout est gratuit : flux RSS publics, API Groq (offre gratuite),
GitHub Actions (gratuit pour les dépôts publics).

---

## 1. Les fichiers

| Fichier | À quoi il sert | Tu le modifies ? |
|---|---|---|
| `sources.yaml` | La liste des sources par pays | Oui, pour ajouter ou retirer des sources |
| `mots_cles.yaml` | Les mots qui déclenchent la sélection d'un article | Oui, pour affiner le filtre |
| `veille.py` | Le script qui fait tout le travail | Rarement |
| `requirements.txt` | Les bibliothèques à installer | Non |
| `veille.yml` | L'automatisation hebdomadaire GitHub | Non |
| `data/` | Créé automatiquement : bulletins, archive CSV, historique | Non |

---

## 2. Premier essai, sur ton ordinateur

Ouvre un terminal dans ce dossier et tape, une ligne à la fois :

```bash
pip install -r requirements.txt
python veille.py --test
```

Le mode `--test` fonctionne **sans internet et sans clé API**. Il utilise
trois articles d'exemple. Si tu vois apparaître un fichier
`data/bulletin-dernier.html`, ouvre-le dans ton navigateur : l'installation
est bonne.

Puis lance une vraie collecte, sans l'IA pour l'instant :

```bash
python veille.py --sans-ia
```

Le script va lire les flux, filtrer, et produire un vrai bulletin.
Certaines sources peuvent afficher `[échec]` : c'est normal, un site
peut être en panne ou bloquer les robots. Le script continue sans s'arrêter.

---

## 3. Activer les résumés par l'IA

1. Crée un compte gratuit sur <https://console.groq.com> et génère une clé API.
2. Déclare-la dans ton terminal.

Sur **Windows (PowerShell)** :
```powershell
$env:GROQ_API_KEY="colle_ta_cle_ici"
python veille.py
```

Sur **Mac ou Linux** :
```bash
export GROQ_API_KEY="colle_ta_cle_ici"
python veille.py
```

Ne mets jamais ta clé directement dans le fichier `veille.py` :
si tu publies le dépôt sur GitHub, elle deviendrait visible de tous.

---

## 4. Automatiser chaque semaine avec GitHub

1. Crée un dépôt GitHub, par exemple `veille-ia-droit`.
2. Envoie-y tous ces fichiers.
3. Déplace `veille.yml` dans un dossier `.github/workflows/`
   (le chemin final doit être `.github/workflows/veille.yml`).
4. Dans ton dépôt : **Settings → Secrets and variables → Actions →
   New repository secret**. Ajoute :

   | Nom du secret | Valeur |
   |---|---|
   | `GROQ_API_KEY` | ta clé Groq |
   | `MAIL_EXPEDITEUR` | ton adresse Gmail (facultatif) |
   | `MAIL_MOT_DE_PASSE` | un **mot de passe d'application** Gmail (facultatif) |
   | `MAIL_DESTINATAIRE` | l'adresse qui reçoit le bulletin (facultatif) |

5. Onglet **Actions → Veille juridique IA → Run workflow** pour un essai immédiat.

Ensuite, la veille tourne seule chaque lundi matin.

> Pour le mail : Gmail refuse ton mot de passe habituel. Il faut activer
> la validation en deux étapes, puis créer un « mot de passe d'application »
> dans les paramètres de sécurité de ton compte Google.

---

## 5. Pourquoi le Maroc et la Côte d'Ivoire passent par Google Actualités

Les sites officiels marocains et ivoiriens (SGG, CNDP, ARTCI, Journal officiel)
n'ont pas de flux RSS exploitable, et certains refusent les connexions
venant de serveurs situés à l'étranger — or GitHub Actions tourne à l'étranger.

La solution retenue : des flux de recherche Google Actualités ciblés par pays,
qui relaient rapidement la presse locale. Ils fonctionnent partout.

Les sites officiels restent listés dans `sources.yaml`, sous
`surveillance_manuelle` : ils apparaissent en bas de chaque bulletin
comme rappel à consulter toi-même, en une dizaine de minutes par semaine.

Si tu veux aussi les automatiser, deux possibilités :
- lancer le script depuis ton ordinateur au Maroc, avec le Planificateur
  de tâches Windows (il utilisera alors une adresse marocaine) ;
- ajouter une surveillance de page avec Visualping ou changedetection.io,
  qui t'enverra un mail à chaque modification.

---

## 6. Régler le filtre

Si tu reçois **trop d'articles hors sujet** : ajoute des termes dans la
section `exclus` de `mots_cles.yaml`, ou retire des mots trop larges
de la liste `juridiques` (par exemple `droit` ou `code`).

Si tu reçois **trop peu d'articles** : ajoute des mots dans `obligatoires`,
ou élargis la période avec `python veille.py --jours 30`.

Chaque source a aussi un réglage `filtre` dans `sources.yaml` :

- `double` (le défaut) exige un mot du thème IA **et** un mot juridique.
  C'est le réglage des flux officiels, qui publient de tout.
- `simple` n'exige qu'un mot du thème. C'est le réglage des recherches
  Google Actualités : leur requête contient déjà les termes juridiques,
  et leur résumé RSS se limite au titre, donc le filtre double y
  rejetterait presque tout.

Le journal indique après chaque exécution combien d'articles ont été
écartés et pour quel motif — trop ancien, déjà vu, hors sujet. C'est ce
qui te dit quel réglage toucher.

Pour ajouter une source, copie un bloc dans `sources.yaml` et change les
valeurs. Pour fabriquer un flux Google Actualités sur un sujet précis :

```
https://news.google.com/rss/search?q=TES+MOTS+CLES&hl=fr&gl=MA&ceid=MA:fr
```

Remplace `MA` par `CI` pour la Côte d'Ivoire ou `FR` pour la France,
et sépare les mots par des `+`.

---

## 7. Précaution importante

Les résumés sont produits par un modèle de langage. **Ils peuvent contenir
des erreurs, des dates fausses ou des références inexactes.** Un avertissement
figure en tête de chaque bulletin.

Avant de citer un texte dans ton mémoire, un article ou un conseil à un client :
remonte systématiquement au texte officiel via le lien. Le bulletin sert à
repérer l'information, pas à la certifier.
