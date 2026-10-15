import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import openai
from dotenv import load_dotenv
from openai import OpenAI


# =============================================================================
# AFFICHAGE : COULEURS ET SPINNER
# =============================================================================

# Sous Windows, cette ligne active la gestion des couleurs dans le terminal
if platform.system() == "Windows":
    os.system("")

# Codes ANSI : le terminal les interprète comme des changements de couleur
RESET, BOLD = "\033[0m", "\033[1m"
GRAY, RED, GREEN = "\033[90m", "\033[91m", "\033[92m"
YELLOW, BLUE, CYAN = "\033[93m", "\033[94m", "\033[96m"


# Renvoie le texte entouré d'une couleur, puis revient à la couleur normale.
def color(text, code):
    return f"{code}{text}{RESET}"


# Animation affichée pendant l'attente du LLM.
# S'utilise avec `with Spinner():` : l'animation démarre au début du bloc
# et s'arrête à la fin, même si une erreur se produit dans le bloc.
class Spinner:
    FRAMES = ["|", "/", "-", "\\"]  # les 4 images de l'animation

    # Prépare l'animation : le texte affiché, le drapeau d'arrêt et le thread.
    def __init__(self, text="L'agent réfléchit"):
        self.text = text
        # Le "drapeau" qu'on lève pour arrêter l'animation
        self.stop_event = threading.Event()
        # L'animation tourne dans un thread, en parallèle de l'appel au LLM
        self.thread = threading.Thread(target=self._animate, daemon=True)

    # Tourne dans le thread : affiche les images une par une jusqu'à ce que
    # le drapeau soit levé, puis efface la ligne.
    def _animate(self):
        i = 0
        while not self.stop_event.is_set():
            # \r revient au début de la ligne : chaque image écrase la précédente
            frame = self.FRAMES[i % len(self.FRAMES)]
            print(color(f"\r  {frame} {self.text}...", CYAN), end="", flush=True)
            i += 1
            time.sleep(0.1)
        # Efface la ligne de l'animation avant d'afficher la suite
        print("\r" + " " * (len(self.text) + 10) + "\r", end="", flush=True)

    # Appelée au début du bloc `with` : lance l'animation.
    def __enter__(self):
        self.thread.start()
        return self

    # Appelée à la fin du bloc `with`, même en cas d'erreur : arrête l'animation.
    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join()


# =============================================================================
# CONFIGURATION
# =============================================================================

load_dotenv()  # lit la clé API dans le fichier .env

# Sans clé, OpenAI() plante : on vérifie avant et on explique quoi faire
if not os.getenv("OPENAI_API_KEY"):
    print(color("Erreur : la clé OPENAI_API_KEY est introuvable.", RED))
    print("Crée un fichier .env avec la ligne : OPENAI_API_KEY=sk-...")
    sys.exit(1)

client = OpenAI()        # le SDK : il sert uniquement à parler au LLM
MODEL = "gpt-4o-mini"    # modèle peu coûteux, suffisant pour le projet
MAX_TOOL_ROUNDS = 10     # nombre maximum d'outils enchaînés pour une question

# Mode dry-run : si True, run_shell montre la commande sans l'exécuter
DRY_RUN = False

# Morceaux de commandes toujours refusés par run_shell
FORBIDDEN = [
    "rm -rf", "rm -r", "rmdir /s", "del /s", "remove-item",  # suppressions massives
    "format c:", "mkfs", "shutdown", "reboot", "sudo",       # système
    "git push --force", "git reset --hard",                  # Git destructeur
    ".env",                                                  # protège la clé API
    "curl", "wget", "invoke-webrequest",                     # envoi de données sur internet
]

# Fichiers et dossiers que l'agent ne doit jamais voir (list_files, grep)
IGNORED = {".git", ".venv", "__pycache__", ".env", "history.json"}

MAX_GREP_RESULTS = 50    # au-delà, grep s'arrête pour économiser des tokens
MAX_OUTPUT = 5000        # taille maximale (en caractères) d'une sortie de commande


# =============================================================================
# SYSTEM PROMPT ET HISTORIQUE
# =============================================================================

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
HISTORY_PATH = Path(__file__).parent / "history.json"


# Lit le system prompt (consignes du LLM) dans prompts/system.md.
def load_system_prompt():
    return PROMPT_PATH.read_text(encoding="utf-8")


# Crée un historique vide, qui contient seulement le system prompt.
def new_history():
    return [{"role": "system", "content": load_system_prompt()}]


# Écrit tout l'historique dans history.json (lisible, accents conservés).
def save_history(history):
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


# Recharge l'historique de la session précédente, ou en crée un nouveau.
# Le LLM n'a pas de mémoire : c'est ce fichier qui permet à l'agent
# de se souvenir d'une session à l'autre.
def load_history():
    if not HISTORY_PATH.exists():
        return new_history()
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        history = None
    if not isinstance(history, list) or not history:  # fichier abîmé ou vide
        print(color("Historique illisible : nouvelle session.", YELLOW))
        return new_history()

    # On remplace l'ancien system prompt par la version actuelle du fichier
    history[0] = new_history()[0]
    if len(history) > 1:
        print(color(f"Historique chargé : {len(history) - 1} messages de la session précédente.", GRAY))
    return history


# =============================================================================
# LES OUTILS
# Chaque outil est une fonction Python normale qui renvoie du TEXTE.
# En cas de problème, elle renvoie un message d'erreur au lieu de planter :
# ce message part au LLM, qui peut alors corriger sa demande.
# =============================================================================

# Demande oui/non à l'utilisateur. Tout ce qui n'est pas « o » ou « oui » vaut non.
def confirm(question):
    answer = input(color(f"  {question} (o/n) ", YELLOW)).strip().lower()
    return answer in ("o", "oui")


# Outil : lit un fichier texte et renvoie son contenu.
def read_file(path):
    if Path(path).name == ".env":  # .name : protège aussi ./.env ou dossier/../.env
        return "Erreur : lire le fichier .env est interdit (il contient la clé API)."
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Erreur : le fichier '{path}' n'existe pas."
    except Exception as e:
        return f"Erreur en lisant '{path}' : {e}"


# Outil : liste le contenu d'un dossier (les dossiers finissent par /).
def list_files(path="."):
    folder = Path(path)
    if not folder.is_dir():
        return f"Erreur : le dossier '{path}' n'existe pas."

    names = [
        f"{item.name}/" if item.is_dir() else item.name
        for item in sorted(folder.iterdir())
        if item.name not in IGNORED
    ]
    return "\n".join(names) or f"Le dossier '{path}' est vide."


# Outil : cherche un texte dans tous les fichiers d'un dossier et de ses sous-dossiers.
# Renvoie une ligne par résultat, au format fichier:numéro de ligne: contenu.
# La recherche ne tient pas compte des majuscules.
def grep(pattern, path="."):
    folder = Path(path)
    if not folder.is_dir():
        return f"Erreur : le dossier '{path}' n'existe pas."

    results = []
    for file in sorted(folder.rglob("*")):  # rglob parcourt aussi les sous-dossiers
        # On saute les dossiers, et tout ce qui se trouve dans .git, .venv, .env...
        if not file.is_file() or any(part in IGNORED for part in file.parts):
            continue
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue  # fichier binaire ou illisible : on l'ignore

        for number, line in enumerate(lines, start=1):
            if pattern.lower() in line.lower():
                results.append(f"{file}:{number}: {line.strip()}")
                if len(results) >= MAX_GREP_RESULTS:
                    results.append(f"[... arrêt après {MAX_GREP_RESULTS} résultats]")
                    return "\n".join(results)

    return "\n".join(results) or f"Aucun résultat pour '{pattern}'."


# Outil : remplace old_text par new_text dans un fichier.
# Si old_text est vide, crée un nouveau fichier contenant new_text.
# Chaque écriture est montrée à l'utilisateur et demande sa confirmation.
def edit_file(path, old_text, new_text):
    file_path = Path(path)
    if file_path.name == ".env":
        return "Erreur : modifier le fichier .env est interdit."

    # Cas 1 : création d'un nouveau fichier
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

    # Cas 2 : modification d'un fichier existant
    if not file_path.is_file():
        return f"Erreur : le fichier '{path}' n'existe pas."
    content = file_path.read_text(encoding="utf-8")

    # Le passage doit apparaître exactement une fois, sinon on ne sait pas quoi remplacer
    count = content.count(old_text)
    if count == 0:
        return "Erreur : le texte à remplacer n'a pas été trouvé. Relis le fichier avec read_file."
    if count > 1:
        return f"Erreur : le texte à remplacer apparaît {count} fois. Donne un passage plus long."

    # On montre l'avant (rouge) et l'après (vert), puis on demande l'accord
    print(color("\n  --- avant ---", BOLD) + "\n" + color(old_text, RED))
    print(color("  --- après ---", BOLD) + "\n" + color(new_text, GREEN) + "\n")
    if not confirm(f"Modifier '{path}' ?"):
        return "Action annulée par l'utilisateur."

    file_path.write_text(content.replace(old_text, new_text), encoding="utf-8")
    return f"Fichier '{path}' modifié."


# Lance une commande et récupère sa sortie en texte (utilisé par git_status et run_shell).
# errors="replace" évite un plantage si la sortie contient un caractère inconnu.
def run_command(command, timeout, shell=False):
    return subprocess.run(
        command, shell=shell, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


# Outil : affiche l'état du dépôt Git. Commande fixe, donc sans confirmation.
def git_status():
    try:
        result = run_command(["git", "status"], timeout=10)
    except FileNotFoundError:
        return "Erreur : Git n'est pas installé sur cet ordinateur."
    except subprocess.TimeoutExpired:
        return "Erreur : git status a mis trop de temps à répondre."

    # returncode : 0 = succès, autre chose = erreur (ex. : pas un dépôt Git)
    if result.returncode != 0:
        return f"Erreur Git : {result.stderr.strip()}"
    return result.stdout


# Outil : exécute une commande écrite par le LLM, avec 3 sécurités.
def run_shell(command):
    # Sécurité 1 : commandes interdites, refusées sans rien demander
    for forbidden in FORBIDDEN:
        if forbidden in command.lower():
            return f"Refusé : la commande contient '{forbidden}', qui est interdit."

    # Sécurité 2 : en mode dry-run, on montre la commande sans l'exécuter
    if DRY_RUN:
        print(color(f"  [dry-run] {command}", YELLOW))
        return f"Mode dry-run : la commande '{command}' n'a PAS été exécutée."

    # Sécurité 3 : l'utilisateur doit confirmer
    if not confirm(f"Exécuter la commande : {command} ?"):
        return "Commande refusée par l'utilisateur."

    try:
        result = run_command(command, timeout=30, shell=True)
    except subprocess.TimeoutExpired:
        return "Erreur : la commande a dépassé 30 secondes et a été arrêtée."

    # On coupe les sorties trop longues pour ne pas envoyer trop de tokens au LLM
    output = (result.stdout + result.stderr).strip() or "(aucune sortie)"
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + "\n[... sortie tronquée]"
    return f"Code de retour : {result.returncode}\n{output}"


# =============================================================================
# DESCRIPTION DES OUTILS POUR LE LLM
# Le LLM ne voit pas notre code Python : il voit seulement ces fiches
# (nom, description, paramètres). C'est le format "function calling" d'OpenAI.
# =============================================================================

# Construit la fiche d'un outil au format attendu par OpenAI.
# Chaque paramètre est du texte. On ajoute automatiquement un paramètre
# obligatoire "raison" : le LLM doit expliquer pourquoi il utilise l'outil,
# et run_tool l'affiche dans le terminal.
def make_tool(name, description, required=(), **params):
    properties = {"raison": {"type": "string", "description": "Explique en une phrase pourquoi tu utilises cet outil."}}
    for param, param_description in params.items():
        properties[param] = {"type": "string", "description": param_description}

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": ["raison", *required]},
        },
    }


TOOLS = [
    make_tool(
        "read_file", "Lit le contenu d'un fichier texte du projet.",
        required=["path"],
        path="Chemin du fichier, par exemple 'agent.py'.",
    ),
    make_tool(
        "list_files", "Liste les fichiers et dossiers d'un dossier du projet.",
        path="Dossier à lister, par exemple 'prompts'. '.' pour la racine du projet.",
    ),
    make_tool(
        "grep",
        "Cherche un texte dans tous les fichiers du projet et renvoie les lignes trouvées "
        "(fichier:numéro de ligne: contenu). Utile pour trouver où une fonction ou un mot est utilisé.",
        required=["pattern"],
        pattern="Le texte à chercher, par exemple 'def main'.",
        path="Dossier où chercher. '.' pour tout le projet.",
    ),
    make_tool(
        "edit_file",
        "Modifie un fichier en remplaçant old_text par new_text. "
        "Lis toujours le fichier avec read_file avant, pour copier old_text exactement. "
        "Pour créer un nouveau fichier, mets old_text vide.",
        required=["path", "old_text", "new_text"],
        path="Chemin du fichier à modifier ou à créer.",
        old_text="Texte exact à remplacer, copié du fichier. Vide pour créer un fichier.",
        new_text="Nouveau texte qui remplace old_text.",
    ),
    make_tool(
        "git_status", "Affiche l'état du dépôt Git : branche actuelle, fichiers modifiés ou non suivis.",
    ),
    make_tool(
        "run_shell",
        "Exécute une commande dans le terminal et renvoie sa sortie. "
        f"Le système est {platform.system()} : utilise des commandes compatibles. "
        "L'utilisateur doit confirmer chaque commande.",
        required=["command"],
        command="La commande à exécuter, par exemple 'python --version'.",
    ),
]


# =============================================================================
# ORCHESTRATEUR ET BOUCLE AGENTIQUE
# =============================================================================

# Annuaire des outils : nom demandé par le LLM -> fonction Python à exécuter.
# Ajouter un outil = écrire la fonction + sa fiche dans TOOLS + une ligne ici.
TOOL_FUNCTIONS = {
    "read_file": read_file,
    "list_files": list_files,
    "grep": grep,
    "edit_file": edit_file,
    "git_status": git_status,
    "run_shell": run_shell,
}


# Orchestrateur : exécute l'outil demandé par le LLM. Ne plante jamais.
# Renvoie toujours un texte (le résultat, ou un message d'erreur pour le LLM).
def run_tool(name, arguments_json):
    # Le LLM envoie ses arguments en texte JSON : on les transforme en dictionnaire
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError:
        return f"Erreur : arguments invalides pour l'outil '{name}'."

    # On affiche le raisonnement du LLM, puis on le retire des arguments
    raison = args.pop("raison", "")
    if raison:
        print(color(f"  [réflexion] {raison}", GRAY))
    print(color(f"  [outil] {name}({args})", BLUE))

    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return f"Erreur : l'outil '{name}' n'existe pas."

    # func(**args) "déballe" le dictionnaire : {"path": "a.py"} devient func(path="a.py")
    try:
        return func(**args)
    except Exception as e:
        return f"Erreur pendant l'exécution de '{name}' : {e}"


# Boucle agentique : appelle le LLM jusqu'à ce qu'il réponde sans demander d'outil.
# L'historique est modifié directement : on y ajoute les demandes du LLM,
# les résultats des outils et la réponse finale.
def agent_turn(history):
    for _ in range(MAX_TOOL_ROUNDS):
        # 1. On envoie tout l'historique et la liste des outils
        with Spinner():
            response = client.chat.completions.create(model=MODEL, messages=history, tools=TOOLS)
        message = response.choices[0].message

        # 2. Aucun outil demandé : c'est la réponse finale
        if not message.tool_calls:
            history.append({"role": "assistant", "content": message.content})
            return message.content

        # 3. Le LLM demande des outils : on enregistre sa demande dans l'historique
        #    (l'API exige de voir la demande avant les résultats)
        history.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {"id": call.id, "type": "function",
                 "function": {"name": call.function.name, "arguments": call.function.arguments}}
                for call in message.tool_calls
            ],
        })

        # 4. On exécute chaque outil et on ajoute son résultat.
        #    tool_call_id relie chaque résultat à la bonne demande.
        for call in message.tool_calls:
            result = run_tool(call.function.name, call.function.arguments)
            history.append({"role": "tool", "tool_call_id": call.id, "content": result})

        # 5. On recommence : le LLM lit les résultats et décide de la suite

    return "Arrêt : trop d'appels d'outils à la suite."


# =============================================================================
# REPL : LA BOUCLE PRINCIPALE DU PROGRAMME
# =============================================================================

HELP_TEXT = """
Commandes :
  /help     affiche cette aide
  /dryrun   active ou désactive le mode dry-run (run_shell montre sans exécuter)
  /reset    efface l'historique et commence une nouvelle session
  /exit     quitte l'agent (aussi : exit, quit, Ctrl + C)
"""

# Message affiché pour chaque erreur de l'API OpenAI.
# Les erreurs plus précises doivent être avant openai.APIError, qui les englobe toutes.
API_ERRORS = [
    (openai.AuthenticationError, "Clé API invalide : vérifie OPENAI_API_KEY dans le fichier .env."),
    (openai.APIConnectionError, "Impossible de joindre OpenAI : vérifie ta connexion internet."),
    (openai.RateLimitError, "Limite atteinte ou crédit épuisé : réessaie plus tard ou vérifie ton solde."),
]


# Affiche les commandes, puis les outils (lus directement dans TOOLS).
def print_help():
    print(color(HELP_TEXT, GRAY))
    print(color("Outils de l'agent :", GRAY))
    for tool in TOOLS:
        name = tool["function"]["name"]
        first_sentence = tool["function"]["description"].split(".")[0]
        print(color(f"  {name:<12}{first_sentence}", GRAY))
    print(color(f"\nMode dry-run : {'activé' if DRY_RUN else 'désactivé'}", GRAY))


# Lit les questions de l'utilisateur, lance la boucle agentique, affiche les réponses.
def main():
    global DRY_RUN  # permet de modifier la variable DRY_RUN définie en haut du fichier

    print(color("CodexPilot Code", BOLD + CYAN))
    print(color("Tape /help pour voir les commandes, /exit pour quitter", GRAY))
    history = load_history()

    while True:
        # Ctrl + C ou fermeture du terminal : on quitte proprement au lieu de planter
        try:
            user_input = input(color("\nvous > ", BOLD)).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAu revoir !")
            break

        # --- Commandes internes (elles ne sont pas envoyées au LLM) ---
        if user_input in ("/exit", "exit", "quit"):
            print("Au revoir !")
            break
        if not user_input:
            continue
        if user_input == "/help":
            print_help()
            continue
        if user_input == "/dryrun":
            DRY_RUN = not DRY_RUN
            print(color(f"Mode dry-run : {'activé' if DRY_RUN else 'désactivé'}", YELLOW))
            continue
        if user_input == "/reset":
            history = new_history()
            save_history(history)
            print(color("Historique effacé : nouvelle session.", YELLOW))
            continue

        # --- Question pour l'agent ---
        # On note la taille de l'historique : en cas d'erreur, on revient à cet état,
        # pour ne pas garder un échange à moitié fini que l'API refuserait ensuite.
        size_before = len(history)
        history.append({"role": "user", "content": user_input})

        try:
            answer = agent_turn(history)
            print(color("\nagent > ", BOLD + GREEN) + str(answer))
        except KeyboardInterrupt:
            del history[size_before:]
            print(color("\n[interrompu] Question annulée.", YELLOW))
            continue
        except openai.APIError as e:
            del history[size_before:]
            # On cherche le message qui correspond au type d'erreur
            message = next((text for error_type, text in API_ERRORS if isinstance(e, error_type)),
                           f"Problème avec l'API OpenAI : {e}")
            print(color(f"\n[erreur] {message}", RED))
            continue

        # Sauvegarde après chaque échange : rien n'est perdu si le programme s'arrête
        save_history(history)


if __name__ == "__main__":
    main()