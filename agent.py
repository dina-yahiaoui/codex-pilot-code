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
                    "path": {
                        "type": "string",
                        "description": "Chemin du fichier, par exemple 'agent.py'",
                    }
                },
                "required": ["path"],
            },
        },
    }
]    

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

        # 2. On envoie TOUT l'historique au LLM
        response = client.chat.completions.create(model=MODEL, messages=history)
        answer = response.choices[0].message.content

        # 3. On ajoute sa réponse à l'historique, puis on l'affiche
        history.append({"role": "assistant", "content": answer})
        print(f"\nagent > {answer}")


if __name__ == "__main__":
    main()