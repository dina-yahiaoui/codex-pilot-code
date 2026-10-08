import json
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
]

# ---------------------------------------------------------------------------
# L'ORCHESTRATEUR
# ---------------------------------------------------------------------------
# Nom de l'outil (ce que le LLM demande) -> fonction Python à exécuter
# Pour ajouter un outil : une ligne ici + sa description dans TOOLS
TOOL_FUNCTIONS = {
    "read_file": read_file,
    "list_files": list_files,
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