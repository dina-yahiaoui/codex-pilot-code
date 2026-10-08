import json
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()          # lit ta clé dans le fichier .env
client = OpenAI()
MODEL = "gpt-4o-mini"


def load_system_prompt():
    """Charge le system prompt depuis le fichier Markdown."""
    path = Path(__file__).parent / "prompts" / "system.md"
    return path.read_text(encoding="utf-8")


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
    answer = input(f"  {question} (o/n) ").strip().lower()
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
        print(f"\n  --- nouveau fichier : {path} ---\n{new_text}\n")
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

    print(f"\n  --- avant ---\n{old_text}\n  --- après ---\n{new_text}\n")
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
        print(f"  [réflexion] {raison}")
    print(f"  [outil] {name}({args})")

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
    # L'historique, gardé par NOTRE programme (le serveur ne se souvient de rien)
    history = [{"role": "system", "content": load_system_prompt()}]
    print("CodexPilot Code — tape /exit pour quitter")

    # Le REPL : on boucle pour garder la main entre chaque échange
    while True:
        user_input = input("\nvous > ").strip()

        if user_input in ("/exit", "exit", "quit"):
            print("Au revoir !")
            break
        if not user_input:
            continue

        # 1. On ajoute ton message à l'historique
        history.append({"role": "user", "content": user_input})

        # 2. On lance la boucle agentique (elle ajoute elle-même les réponses à l'historique)
        answer = agent_turn(history)
        print(f"\nagent > {answer}")


if __name__ == "__main__":
    main()