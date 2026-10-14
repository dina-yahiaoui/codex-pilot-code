# codex-pilot-code

Agent IA en ligne de commande, écrit en Python, qui aide à travailler sur un projet de code depuis le terminal : il peut lire des fichiers, chercher dans le projet, modifier des fichiers et lancer des commandes, toujours avec l'accord de l'utilisateur.

Projet réalisé à La Plateforme (Bachelor IA/Data). L'agent est construit **sans framework agentique** (pas de LangChain, LangGraph, etc.) : seulement le SDK officiel d'OpenAI. La boucle agentique est écrite à la main.

---

## Installation

Il faut **Python 3.10 ou plus récent**, **Git**, et une **clé API OpenAI**.

**1. Cloner le projet**

```bash
git clone https://github.com/dina-yahiaoui/codex-pilot-code.git
cd codex-pilot-code
```

**2. Créer et activer un environnement virtuel**

```bash
python -m venv .venv
```

- Windows : `.venv\Scripts\activate`
- Mac / Linux : `source .venv/bin/activate`

**3. Installer les dépendances**

```bash
pip install -r requirements.txt
```

**4. Ajouter sa clé API**

Copier le modèle `.env.example` en `.env` :

- Windows : `copy .env.example .env`
- Mac / Linux : `cp .env.example .env`

Puis ouvrir `.env` et remplacer `sk-ta-cle-ici` par sa vraie clé OpenAI. Ce fichier est ignoré par Git : la clé n'est jamais envoyée sur GitHub.

**5. Lancer l'agent**

```bash
python agent.py
```

---

## Utilisation

On écrit sa demande en français, comme dans une conversation :

```
vous > quels fichiers il y a dans mon projet ?
vous > où est définie la fonction main ?
vous > lis prompts/system.md et résume-le
vous > crée un fichier notes.txt qui contient "À faire : README"
```

Avant chaque outil, l'agent affiche ce qu'il compte faire (`[réflexion]`) puis l'outil utilisé (`[outil]`).

### Commandes

| Commande | Effet |
|---|---|
| `/help` | Affiche les commandes et la liste des outils |
| `/dryrun` | Active ou désactive le mode dry-run |
| `/reset` | Efface l'historique et commence une nouvelle conversation |
| `/exit` | Quitte l'agent (aussi `exit`, `quit` ou `Ctrl + C`) |

---

## Les outils de l'agent

| Outil | Rôle | Confirmation demandée |
|---|---|---|
| `read_file` | Lit un fichier texte | Non |
| `list_files` | Liste les fichiers d'un dossier | Non |
| `grep` | Cherche un texte dans tous les fichiers du projet | Non |
| `git_status` | Affiche l'état du dépôt Git | Non |
| `edit_file` | Crée un fichier ou remplace un passage | **Oui**, avec affichage avant / après |
| `run_shell` | Exécute une commande dans le terminal | **Oui** |

Les outils qui ne font que regarder fonctionnent directement. Ceux qui modifient quelque chose demandent toujours l'accord de l'utilisateur.

---

## Sécurité

`run_shell` est l'outil le plus risqué, car c'est le LLM qui écrit la commande. Il est protégé de trois façons :

1. **Commandes interdites** : une commande qui contient `rm -rf`, `del /s`, `sudo`, `shutdown`, `git push --force`, `git reset --hard`, `.env`, `curl`, `wget`… est refusée sans même être proposée.
2. **Confirmation** : chaque commande est affichée et n'est exécutée qu'après un `o`.
3. **Mode dry-run** (`/dryrun`) : l'agent montre la commande qu'il lancerait, sans l'exécuter. Pratique pour tester sans risque.

Autres protections :

- le fichier `.env` (clé API) ne peut être ni lu, ni listé, ni modifié par l'agent ;
- une commande est arrêtée au bout de 30 secondes ;
- l'agent ne peut pas enchaîner plus de 10 outils pour une même question ;
- les sorties de commandes et les résultats de recherche trop longs sont coupés, pour limiter le nombre de tokens envoyés.

---

## Fiabilité

L'agent ne plante pas en cas de problème : l'erreur est expliquée et on peut reposer sa question.

- **Erreur d'outil** (fichier introuvable, texte absent…) : le message d'erreur est renvoyé au LLM, qui peut corriger sa demande.
- **Clé API absente ou invalide**, **pas d'internet**, **crédit épuisé** : un message clair s'affiche.
- **`Ctrl + C` pendant une réponse** : la question est annulée, l'agent reste ouvert.

---

## Fonctionnement

```
Utilisateur  ──►  REPL (main)  ──►  Boucle agentique (agent_turn)  ◄──►  LLM (gpt-4o-mini)
                                         │        ▲
                               exécute   ▼        │  résultat
                                  Outils (fonctions Python)
```

1. L'utilisateur écrit une question dans le terminal.
2. Le programme envoie au LLM **tout l'historique** de la conversation et la **liste des outils**.
3. Si le LLM demande un outil, c'est **le programme** qui l'exécute, puis renvoie le résultat au LLM.
4. On recommence jusqu'à ce que le LLM donne sa réponse finale.

Le LLM ne touche jamais lui-même aux fichiers : il décide, le programme exécute.

### Détails techniques

- **System prompt** : chargé au lancement depuis `prompts/system.md`.
- **Historique** : le LLM n'a pas de mémoire, donc le programme lui renvoie toute la conversation à chaque appel. L'historique est sauvegardé dans `history.json` après chaque échange et rechargé au lancement suivant. `/reset` l'efface.
- **Orchestrateur** (`run_tool`) : reçoit le nom de l'outil demandé par le LLM et appelle la bonne fonction Python grâce au dictionnaire `TOOL_FUNCTIONS`. Pour ajouter un outil : écrire la fonction, la décrire dans `TOOLS`, l'ajouter à `TOOL_FUNCTIONS`.
- **Raisonnement visible** : chaque outil a un paramètre obligatoire `raison`, que le LLM doit remplir et que le programme affiche.

---

## Structure du projet

```
codex-pilot-code/
├── agent.py              programme principal : REPL, boucle agentique, outils
├── prompts/
│   └── system.md         system prompt
├── requirements.txt      dépendances (openai, python-dotenv)
├── .env.example          modèle pour le fichier .env
├── .gitignore            exclut .env, .venv et history.json
├── TESTS.md              liste des tests à faire à la main
├── JOURNAL_DES_TESTS.md  tests réalisés pendant le développement et résultats
└── README.md
```

---

## Limites connues

- Le LLM peut se tromper même avec les bonnes informations (par exemple, mal compter des fonctions dans un fichier). Les résultats importants sont à vérifier.
- La liste des commandes interdites compare du texte : une commande dangereuse écrite autrement pourrait passer. La confirmation reste donc indispensable.
- L'historique grossit à chaque échange, ce qui consomme de plus en plus de tokens. Il faut utiliser `/reset` de temps en temps.
- Il n'y a pas d'outil pour supprimer un fichier : c'est volontaire, la suppression passe par `run_shell` avec confirmation.

---

## Auteure

Dina Yahiaoui — La Plateforme, Marseille