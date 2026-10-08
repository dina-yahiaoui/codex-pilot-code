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
    }
]

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
            args = json.loads(call.function.arguments)

            # NOUVEAU : on récupère la raison, on l'affiche, et on l'enlève des arguments
            raison = args.pop("raison", "")
            if raison:
                print(f"  [réflexion] {raison}")

            print(f"  [outil] {call.function.name}({args})")

            if call.function.name == "read_file":
                result = read_file(args["path"])
            else:
                result = f"Erreur : outil inconnu '{call.function.name}'."

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