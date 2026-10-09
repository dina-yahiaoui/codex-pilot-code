import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import openai
from dotenv import load_dotenv
from openai import OpenAI

# ---------------------------------------------------------------------------
# COULEURS DU TERMINAL (codes ANSI, sans bibliothèque)
# ---------------------------------------------------------------------------
if platform.system() == "Windows":
    os.system("")  # active les couleurs dans le terminal Windows

RESET = "\033[0m"     # revient à la couleur normale
BOLD = "\033[1m"
GRAY = "\033[90m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"


def color(text, code):
    """Entoure le texte d'un code couleur, puis revient à la normale."""
    return f"{code}{text}{RESET}"


load_dotenv()          # lit ta clé dans le fichier .env

# Sans clé, OpenAI() plante : on vérifie avant et on explique quoi faire
if not os.getenv("OPENAI_API_KEY"):
    print(color("Erreur : la clé OPENAI_API_KEY est introuvable.", RED))
    print("Crée un fichier .env avec la ligne : OPENAI_API_KEY=sk-...")
    sys.exit(1)

client = OpenAI()
MODEL = "gpt-4o-mini"

# Mode dry-run : si True, run_shell affiche les commandes sans les exécuter
# (on l'active ou le désactive en tapant /dryrun dans le REPL)
DRY_RUN = False

# Commandes (ou morceaux de commandes) toujours refusées
FORBIDDEN = [
    "rm -rf", "rm -r", "rmdir /s", "del /s", "remove-item",   # suppressions massives
    "format c:", "mkfs", "shutdown", "reboot", "sudo",            # système
    "git push --force", "git reset --hard",                    # Git destructeur
    ".env",                                                    # protège la clé API
    "curl", "wget", "invoke-webrequest",                       # envoi de données sur internet
]


def load_system_prompt():
    """Charge le system prompt depuis le fichier Markdown."""
    path = Path(__file__).parent / "prompts" / "system.md"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HISTORIQUE SAUVEGARDÉ DANS UN FICHIER JSON
# ---------------------------------------------------------------------------
HISTORY_PATH = Path(__file__).parent / "history.json"


def save_history(history):
    """Écrit tout l'historique dans history.json."""
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),  # garde les accents, lisible
        encoding="utf-8",
    )


def load_history():
    """Recharge l'historique de la session précédente, ou en crée un nouveau."""
    system_message = {"role": "system", "content": load_system_prompt()}

    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(history, list) and history:
                history[0] = system_message  # on remet le system prompt à jour
                if len(history) > 1:
                    print(color(f"Historique chargé : {len(history) - 1} messages de la session précédente.", GRAY))
                return history
        except json.JSONDecodeError:
            pass
        print(color("Historique illisible : nouvelle session.", YELLOW))

    return [system_message]


def read_file(path):
    """Outil : lit un fichier texte et renvoie son contenu."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Erreur : le fichier '{path}' n'existe pas."
    except Exception as e:
        return f"Erreur en lisant '{path}' : {e}"


# Dossiers et fichiers à ne jamais montrer au LLM
IGNORED = {".git", ".venv", "__pycache__", ".env"}


def list_files(path="."):
    """Outil : liste les fichiers et dossiers d'un dossier."""
    folder = Path(path)
    if not folder.is_dir():
        return f"Erreur : le dossier '{path}' n'existe pas."

    lines = []
    for item in sorted(folder.iterdir()):
        if item.name in IGNORED:
            continue
        if item.is_dir():
            lines.append(f"{item.name}/")   # le / montre que c'est un dossier
        else:
            lines.append(item.name)

    if not lines:
        return f"Le dossier '{path}' est vide."
    return "\n".join(lines)


def confirm(question):
    """Demande oui/non à l'utilisateur. Renvoie True seulement s'il répond oui."""
    answer = input(color(f"  {question} (o/n) ", YELLOW)).strip().lower()
    return answer in ("o", "oui")


def edit_file(path, old_text, new_text):
    """Outil : remplace un passage d'un fichier, ou crée le fichier si old_text est vide."""
    file_path = Path(path)

    if file_path.name == ".env":
        return "Erreur : modifier le fichier .env est interdit."

    # Cas 1 : créer un nouveau fichier
    if old_text == "":
        if file_path.exists():
            return f"Erreur : '{path}' existe déjà. Donne old_text pour le modifier."
        print(color(f"\n  --- nouveau fichier : {path} ---", BOLD))
        print(color(new_text, GREEN) + "\n")
        if not confirm(f"Créer le fichier '{path}' ?"):
            return "Action annulée par l'utilisateur."
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(new_text, encoding="utf-8")
        return f"Fichier '{path}' créé."

    # Cas 2 : modifier un fichier existant
    if not file_path.is_file():
        return f"Erreur : le fichier '{path}' n'existe pas."

    content = file_path.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count == 0:
        return "Erreur : le texte à remplacer n'a pas été trouvé. Relis le fichier avec read_file."
    if count > 1:
        return f"Erreur : le texte à remplacer apparaît {count} fois. Donne un passage plus long."

    print(color("\n  --- avant ---", BOLD))
    print(color(old_text, RED))
    print(color("  --- après ---", BOLD))
    print(color(new_text, GREEN) + "\n")
    if not confirm(f"Modifier '{path}' ?"):
        return "Action annulée par l'utilisateur."

    file_path.write_text(content.replace(old_text, new_text), encoding="utf-8")
    return f"Fichier '{path}' modifié."


def git_status():
    """Outil : affiche l'état du dépôt Git (branche, fichiers modifiés)."""
    try:
        result = subprocess.run(
            ["git", "status"],
            capture_output=True,   # récupère ce que la commande affiche
            text=True,             # en texte, pas en octets
            encoding="utf-8",
            errors="replace",      # évite un plantage sur un caractère bizarre
            timeout=10,            # abandonne après 10 secondes
        )
    except FileNotFoundError:
        return "Erreur : Git n'est pas installé sur cet ordinateur."
    except subprocess.TimeoutExpired:
        return "Erreur : git status a mis trop de temps à répondre."

    if result.returncode != 0:     # 0 = succès, autre chose = erreur
        return f"Erreur Git : {result.stderr.strip()}"
    return result.stdout


def run_shell(command):
    """Outil : exécute une commande shell, avec 3 sécurités."""
    # Sécurité 1 : liste de commandes interdites
    lowered = command.lower()
    for forbidden in FORBIDDEN:
        if forbidden in lowered:
            return f"Refusé : la commande contient '{forbidden}', qui est interdit."

    # Sécurité 2 : mode dry-run, on montre sans exécuter
    if DRY_RUN:
        print(color(f"  [dry-run] {command}", YELLOW))
        return f"Mode dry-run : la commande '{command}' n'a PAS été exécutée."

    # Sécurité 3 : confirmation de l'utilisateur
    if not confirm(f"Exécuter la commande : {command} ?"):
        return "Commande refusée par l'utilisateur."

    try:
        result = subprocess.run(
            command,
            shell=True,            # passe par le terminal (cmd sous Windows)
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "Erreur : la commande a dépassé 30 secondes et a été arrêtée."

    output = (result.stdout + result.stderr).strip() or "(aucune sortie)"
    if len(output) > 5000:         # évite d'envoyer trop de tokens au LLM
        output = output[:5000] + "\n[... sortie tronquée]"
    return f"Code de retour : {result.returncode}\n{output}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Lit le contenu d'un fichier texte du projet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "raison": {
                        "type": "string",
                        "description": "Explique en une phrase pourquoi tu utilises cet outil.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Chemin du fichier, par exemple 'agent.py'",
                    },
                },
                "required": ["raison", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Liste les fichiers et dossiers d'un dossier du projet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "raison": {
                        "type": "string",
                        "description": "Explique en une phrase pourquoi tu utilises cet outil.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Dossier à lister, par exemple 'prompts'. '.' pour la racine du projet.",
                    },
                },
                "required": ["raison"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Modifie un fichier en remplaçant old_text par new_text. "
                "Lis toujours le fichier avec read_file avant, pour copier old_text exactement. "
                "Pour créer un nouveau fichier, mets old_text vide."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raison": {
                        "type": "string",
                        "description": "Explique en une phrase pourquoi tu utilises cet outil.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Chemin du fichier à modifier ou à créer.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Texte exact à remplacer, copié du fichier. Vide pour créer un fichier.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Nouveau texte qui remplace old_text.",
                    },
                },
                "required": ["raison", "path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Affiche l'état du dépôt Git : branche actuelle, fichiers modifiés ou non suivis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "raison": {
                        "type": "string",
                        "description": "Explique en une phrase pourquoi tu utilises cet outil.",
                    },
                },
                "required": ["raison"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Exécute une commande dans le terminal et renvoie sa sortie. "
                f"Le système est {platform.system()} : utilise des commandes compatibles. "
                "L'utilisateur doit confirmer chaque commande."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raison": {
                        "type": "string",
                        "description": "Explique en une phrase pourquoi tu utilises cet outil.",
                    },
                    "command": {
                        "type": "string",
                        "description": "La commande à exécuter, par exemple 'python --version'.",
                    },
                },
                "required": ["raison", "command"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# L'ORCHESTRATEUR
# ---------------------------------------------------------------------------
# Nom de l'outil (ce que le LLM demande) -> fonction Python à exécuter
# Pour ajouter un outil : une ligne ici + sa description dans TOOLS
TOOL_FUNCTIONS = {
    "read_file": read_file,
    "list_files": list_files,
    "edit_file": edit_file,
    "git_status": git_status,
    "run_shell": run_shell,
}


def run_tool(name, arguments_json):
    """Envoie l'appel du LLM vers la bonne fonction. Ne plante jamais."""
    # 1. Lire les arguments envoyés par le LLM (texte JSON -> dictionnaire)
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError:
        return f"Erreur : arguments invalides pour l'outil '{name}'."

    # 2. Afficher le raisonnement, puis l'enlever des arguments
    raison = args.pop("raison", "")
    if raison:
        print(color(f"  [réflexion] {raison}", GRAY))
    print(color(f"  [outil] {name}({args})", BLUE))

    # 3. Trouver la fonction qui correspond au nom demandé
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return f"Erreur : l'outil '{name}' n'existe pas."

    # 4. L'exécuter ; si elle échoue, l'erreur part au LLM au lieu de planter
    try:
        return func(**args)
    except Exception as e:
        return f"Erreur pendant l'exécution de '{name}' : {e}"


MAX_TOOL_ROUNDS = 10  # sécurité : jamais plus de 10 outils à la suite


def agent_turn(history):
    """La boucle agentique : tourne jusqu'à ce que le LLM réponde sans demander d'outil."""
    for _ in range(MAX_TOOL_ROUNDS):
        # 1. On envoie l'historique ET la liste des outils
        response = client.chat.completions.create(
            model=MODEL, messages=history, tools=TOOLS
        )
        message = response.choices[0].message

        # 2. Pas de demande d'outil -> c'est la réponse finale
        if not message.tool_calls:
            history.append({"role": "assistant", "content": message.content})
            return message.content

        # 3. Le LLM demande un outil : on garde sa demande dans l'historique
        history.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in message.tool_calls
            ],
        })

        # 4. On exécute chaque outil demandé et on renvoie le résultat
        for call in message.tool_calls:
            # L'orchestrateur trouve et exécute le bon outil
            result = run_tool(call.function.name, call.function.arguments)

            history.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })
        # 5. On recommence : le LLM va lire le résultat et décider de la suite

    return "Arrêt : trop d'appels d'outils à la suite."


def main():
    global DRY_RUN  # pour pouvoir modifier la variable DRY_RUN définie en haut du fichier

    print(color("CodexPilot Code", BOLD + CYAN))
    print(color("/exit pour quitter, /dryrun pour le mode dry-run, /reset pour effacer l'historique", GRAY))

    # L'historique, gardé par NOTRE programme (le serveur ne se souvient de rien)
    # Il est rechargé depuis history.json s'il existe
    history = load_history()

    # Le REPL : on boucle pour garder la main entre chaque échange
    while True:
        try:
            user_input = input(color("\nvous > ", BOLD)).strip()
        except (KeyboardInterrupt, EOFError):  # Ctrl+C ou Ctrl+Z / fermeture du terminal
            print("\nAu revoir !")
            break

        if user_input in ("/exit", "exit", "quit"):
            print("Au revoir !")
            break
        if not user_input:
            continue
        if user_input == "/dryrun":
            DRY_RUN = not DRY_RUN   # inverse : True devient False et inversement
            print(color(f"Mode dry-run : {'activé' if DRY_RUN else 'désactivé'}", YELLOW))
            continue
        if user_input == "/reset":
            history = [{"role": "system", "content": load_system_prompt()}]
            save_history(history)
            print(color("Historique effacé : nouvelle session.", YELLOW))
            continue

        # On retient la taille de l'historique AVANT l'échange :
        # en cas d'erreur, on revient à cet état pour ne pas garder un échange à moitié fini
        size_before = len(history)

        # 1. On ajoute ton message à l'historique
        history.append({"role": "user", "content": user_input})

        # 2. On lance la boucle agentique (elle ajoute elle-même les réponses à l'historique)
        try:
            answer = agent_turn(history)
            print(color("\nagent > ", BOLD + GREEN) + str(answer))
        except KeyboardInterrupt:
            del history[size_before:]
            print(color("\n[interrompu] Question annulée.", YELLOW))
            continue
        except openai.AuthenticationError:
            del history[size_before:]
            print(color("\n[erreur] Clé API invalide : vérifie OPENAI_API_KEY dans le fichier .env.", RED))
            continue
        except openai.APIConnectionError:
            del history[size_before:]
            print(color("\n[erreur] Impossible de joindre OpenAI : vérifie ta connexion internet.", RED))
            continue
        except openai.RateLimitError:
            del history[size_before:]
            print(color("\n[erreur] Limite atteinte ou crédit épuisé : réessaie plus tard ou vérifie ton solde.", RED))
            continue
        except openai.APIError as e:
            del history[size_before:]
            print(color(f"\n[erreur] Problème avec l'API OpenAI : {e}", RED))
            continue

        # 3. On sauvegarde après chaque échange (rien n'est perdu si le programme s'arrête)
        save_history(history)


if __name__ == "__main__":
    main()