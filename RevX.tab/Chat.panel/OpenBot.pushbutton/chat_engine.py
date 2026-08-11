# -*- coding: utf-8 -*-
"""RevitBot - Chat engine with Groq AI + local Revit commands."""

import re
import os
import clr
import System

clr.AddReference("System")

json_serializer = None
try:
    clr.AddReference("System.Web.Extensions")
    from System.Web.Script.Serialization import JavaScriptSerializer
    json_serializer = JavaScriptSerializer()
    json_serializer.MaxJsonLength = 10485760
except Exception:
    json_serializer = None


def parse_json_simple(json_text):
    pattern = r'"content"\s*:\s*"((?:[^"\\]|\\.)*?)"'
    matches = re.findall(pattern, json_text)
    if matches:
        content = matches[0]
        content = content.replace("\\n", "\n")
        content = content.replace('\\"', '"')
        content = content.replace("\\\\", "\\")
        content = content.replace("\\t", "\t")
        return content
    return None


def parse_groq_response(json_text):
    if json_serializer is not None:
        try:
            result = json_serializer.DeserializeObject(json_text)
            choices = result["choices"]
            first = choices[0]
            msg = first["message"]
            content = msg["content"]
            if content:
                return str(content)
        except Exception:
            pass
    content = parse_json_simple(json_text)
    if content:
        return content
    raise Exception("Could not parse AI response.")


def build_json(obj):
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, str):
        escaped = obj.replace("\\", "\\\\")
        escaped = escaped.replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n")
        escaped = escaped.replace("\t", "\\t")
        return '"' + escaped + '"'
    if isinstance(obj, list):
        items = ",".join(build_json(item) for item in obj)
        return "[" + items + "]"
    if isinstance(obj, dict):
        pairs = []
        for key, val in obj.items():
            pairs.append(build_json(str(key)) + ":" + build_json(val))
        return "{" + ",".join(pairs) + "}"
    return '"' + str(obj).replace('"', '\\"') + '"'


def dict_to_json(d):
    if json_serializer is not None:
        try:
            return json_serializer.Serialize(d)
        except Exception:
            pass
    return build_json(d)


def extract_number(message):
    word_map = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
        "thirty": 30, "fifty": 50, "hundred": 100,
    }
    msg_lower = message.lower()
    for word, num in word_map.items():
        if word in msg_lower:
            return num
    match = re.search(r'\b(\d+)\b', message)
    if match:
        return int(match.group(1))
    return 1


def extract_rename_args(message):
    match = re.search(r'(?:rename|change)\s+sheet\s+(\S+)\s+to\s+(.+)', message, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2).strip()
    match = re.search(r'(?:rename|change)\s+sheet\s+(\S+)\s+(.+)', message, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2).strip()
    return None, None


def extract_renumber_args(message):
    match = re.search(r'renumber\s+sheet\s+(\S+)\s+to\s+(\S+)', message, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return None, None


def extract_delete_sheet_arg(message):
    match = re.search(r'delete\s+sheet\s+(\S+)', message, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def extract_delete_view_arg(message):
    match = re.search(r'delete\s+view\s+(.+)', message, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_sheet_revision_arg(message):
    """Sheet number for 'sheet revisions A-101' style questions."""
    match = re.search(r'sheet\s+revisions?\s+(\S+)', message, re.IGNORECASE)
    if match:
        return match.group(1).rstrip(".,!?")
    match = re.search(r'sheet\s+(\S+)', message, re.IGNORECASE)
    if match:
        return match.group(1).rstrip(".,!?")
    match = re.search(r'revisions?\s+(?:on|for|in)\s+(\S+)', message, re.IGNORECASE)
    if match:
        return match.group(1).rstrip(".,!?")
    return None


def extract_view_name(message):
    match = re.search(
        r'(?:open|activate|show|go\s+to|switch\s+to)\s+(?:view\s+)?(.+)',
        message, re.IGNORECASE
    )
    if match:
        name = match.group(1).strip().rstrip(".,!?")
        if name and len(name) > 0:
            return name
    return None


def extract_path(message):
    match = re.search(r'[A-Za-z]:[\\/][^\s]+', message)
    if match:
        return match.group(0)
    return None


def is_help_or_question(message):
    """True when the user wants guidance, not a RevitBot executable command."""
    if not message:
        return False

    msg = message.lower().strip()

    if msg.endswith("?"):
        return True

    patterns = (
        r"\bhow\s+(?:do|to|can|should)\b",
        r"\bwhat\s+(?:is|does|are|should)\b",
        r"\bwhere\s+(?:do|is|can|should)\b",
        r"\bwhy\s+(?:is|does|can|won't|can't)\b",
        r"\b(?:help|explain|guide|tutorial|walkthrough|steps?)\b",
        r"\b(?:troubleshoot|problem|issue|error|wrong|broken)\b",
        r"\b(?:doesn't|does not|won't|will not|can't|cannot)\s+work\b",
        r"\bnot\s+working\b",
        r"\bwhat\s+tool\b",
        r"\bwhich\s+tool\b",
    )
    for pat in patterns:
        if re.search(pat, msg):
            return True
    return False


def is_explicit_command(message):
    """
    True only when user clearly intends to RUN a command right now —
    not just asking about it, mentioning it, or asking how to do it.
    Must be a short direct imperative like:
    'export pdf', 'save', 'create 3 sheets', 'delete sheet A-1'
    Phrases like 'how do I export', 'what is pdf export',
    'tell me about exporting' should return False so they go to AI instead.
    """
    if not message:
        return False

    msg = message.lower().strip()

    # If it looks like a question → NOT a command
    if is_help_or_question(message):
        return False

    # If message is long and conversational → NOT a command
    # Real commands are usually short and direct (under ~60 chars)
    if len(msg) > 80:
        return False

    # Conversational filler words that indicate it's a question/discussion
    conversational = (
        r"\b(?:tell|explain|show|describe|understand|learn|know|think|wonder|"
        r"about|regarding|related|concerning|difference|between|versus|vs|"
        r"better|best|recommend|suggest|should|would|could|might|maybe|"
        r"if\s+i|when\s+i|can\s+i|do\s+i|will\s+i|is\s+it|is\s+there)\b"
    )
    if re.search(conversational, msg):
        return False

    return True


try:
    from revx_tools_knowledge import get_revx_knowledge_text, mentions_revx
except Exception:
    def get_revx_knowledge_text():
        return ""

    def mentions_revx(message):
        return False


# Read-only live model queries for Informations mode
try:
    import info_queries
except Exception:
    info_queries = None


# ── Command patterns ───────────────────────────────────────────────────
# IMPORTANT: These only fire when is_explicit_command() returns True.
# Removed: export_nwc, export_ifc, export_dwg, export_pdf, save_file,
# delete_selected (generic), delete_elements_by_category (generic)
# These now go to AI unless user types them as a very direct short command.
COMMAND_PATTERNS = [
    # ── Sheet / View management ──────────────────────────────────────
    (r".*\brenumber\s+sheet\b", "rename_sheet_number"),
    (r".*\brename\s+sheet\b", "rename_sheet"),
    (r".*\bchange\s+sheet\s+name\b", "rename_sheet"),
    (r".*\bdelete\s+sheet\s+\S+", "delete_sheet"),
    (r".*\bremove\s+sheet\s+\S+", "delete_sheet"),
    (r".*\bdelete\s+view\s+\S+", "delete_view"),
    (r".*\bremove\s+view\s+\S+", "delete_view"),

    # ── Navigation ──────────────────────────────────────────────────
    (r".*\b(?:open|activate|switch\s+to|go\s+to)\s+(?:view\s+)\S+", "open_view"),
    (r".*\bzoom\s*(?:to\s+)?(?:fit|extents?)\b", "zoom_to_fit"),

    # ── Export — only fires on very direct short commands ────────────
    # e.g. "export nwc", "export pdf", "export dwg", "export ifc"
    # NOT "how do I export", "tell me about nwc export"
    (r"^export\s+(?:nwc|navisworks)$", "export_nwc"),
    (r"^export\s+ifc$", "export_ifc"),
    (r"^export\s+(?:dwg|cad|autocad)$", "export_dwg"),
    (r"^export\s+pdf$", "export_pdf"),
    (r"^(?:nwc|navisworks)$", "export_nwc"),
    (r"^(?:ifc)$", "export_ifc"),
    (r"^(?:dwg|cad)$", "export_dwg"),
    (r"^(?:pdf)$", "export_pdf"),

    # ── Save — only fires on direct "save" or "save file" ────────────
    (r"^save(?:\s+file|\s+project|\s+model)?$", "save_file"),

    # ── Create elements ──────────────────────────────────────────────
    (r".*\b(?:create|make|add|new)\s+(?:\d+\s+)?sheets?\b", "create_sheet"),
    (r".*\b(?:create|make|add|new)\s+(?:\d+\s+)?rooms?\b", "create_room"),
    (r".*\b(?:create|make|add|new)\s+(?:\d+\s+)?levels?\b", "create_level"),
    (r".*\b(?:create|make|add|new)\s+(?:\d+\s+)?grids?\b", "create_grid"),
    (r".*\b(?:create|make|add|new)\s+(?:a\s+)?section\b", "create_section"),
    (r".*\b(?:create|make|add|new)\s+(?:a\s+)?callout\b", "create_callout"),
    (r".*\b(?:create|make|add|new)\s+(?:a\s+)?dimension\b", "create_dimension"),
    (r".*\b(?:create|make|add|write|place)\s+(?:a\s+)?text\s*note\b", "create_text_note"),
    (r".*\bfilled\s+region\b.*\b(?:pick|lines)\b", "create_filled_region_pick"),
    (r".*\b(?:create|make|add|new)\s+(?:a\s+)?filled\s+region\b", "create_filled_region"),
    (r".*\b(?:create|make|add|new)\s+(?:a\s+)?room\s*tag\b", "create_room_tag"),
    (r".*\btag\s+(?:the\s+)?rooms?\b", "create_room_tag"),
    (r".*\b(?:create|make|add|new)\s+(?:a\s+)?workset\b", "create_workset"),
    (r".*\b(?:create|make|add|new)\s+(?:a\s+)?view\s+filter\b", "create_view_filter"),

    # ── Delete — only fires when specific target is named ────────────
    # e.g. "delete selected" or "delete the selected elements"
    (r"^(?:delete|remove|erase)\s+(?:the\s+)?selected(?:\s+elements?)?\s*$", "delete_selected"),

    # ── List / Info ──────────────────────────────────────────────────
    # Revisions must come first — "revisions on sheet A-101" is more
    # specific than the generic "list revisions" pattern.
    (r".*\brevisions?\s+(?:on|for|in)\s+(?:sheet\s+)?\S+$", "sheet_revisions"),
    (r".*\bsheet\s+revisions?\s+\S+", "sheet_revisions"),
    (r".*\blist\b.*\brevisions?\b", "list_revisions"),
    (r".*\brevisions?\b.*\blist\b", "list_revisions"),
    (r"^revisions?$", "list_revisions"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?warnings?\b", "list_warnings"),
    (r"^warnings?$", "list_warnings"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?(?:revit\s+)?links?\b", "list_links"),
    (r"^links?$", "list_links"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?rooms?\b", "list_rooms"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?grids?\b", "list_grids"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?schedules?\b", "list_schedules"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?phases?\b", "list_phases"),
    (r".*\b(?:model|project)\s+(?:stats|statistics|summary|overview)\b", "model_stats"),
    (r"^(?:stats|statistics|summary|overview)$", "model_stats"),
    (r".*\b(?:selected|selection)\s+(?:info|details?|elements?)\b", "selected_info"),
    (r"^(?:selection|selected)\s*info$", "selected_info"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?views?\b", "list_views"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?sheets?\b", "list_sheets"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?levels?\b", "list_levels"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?families?\b", "list_families"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?worksets?\b", "list_worksets"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?materials?\b", "list_materials"),
    (r".*\b(?:list|show|display)\s+(?:all\s+)?categor", "list_categories"),
    (r".*\b(?:doc|project|file)\s*info\b", "doc_info"),
    (r".*\benable\s+worksharing\b", "enable_worksharing"),
]


# ── Modes ──────────────────────────────────────────────────────────────
MODE_INFORMATIONS = "informations"
MODE_TASKS = "tasks"
MODE_TOOLS = "tools"

MODE_LABELS = {
    MODE_INFORMATIONS: "Informations",
    MODE_TASKS: "Tasks",
    MODE_TOOLS: "Tools",
}

# Which commands each mode is allowed to execute. The UI buttons switch
# the engine between these scopes, so each mode stays focused and an
# out-of-mode command is refused with a hint instead of running.
MODE_COMMANDS = {
    MODE_INFORMATIONS: (
        "doc_info", "list_views", "list_sheets", "list_levels",
        "list_families", "list_worksets", "list_materials", "list_categories",
        "list_revisions", "sheet_revisions", "list_warnings", "list_links",
        "list_rooms", "list_grids", "list_schedules", "list_phases",
        "model_stats", "selected_info",
    ),
    MODE_TASKS: (
        "create_sheet", "create_room", "create_level", "create_grid",
        "create_section", "create_callout", "create_dimension",
        "create_text_note", "create_filled_region", "create_filled_region_pick",
        "create_room_tag", "create_workset", "create_view_filter",
        "rename_sheet", "rename_sheet_number", "delete_sheet", "delete_view",
        "delete_selected", "delete_elements_by_category",
        "open_view", "zoom_to_fit", "enable_worksharing",
    ),
    MODE_TOOLS: (
        "export_nwc", "export_ifc", "export_dwg", "export_pdf", "save_file",
    ),
}


def command_mode(cmd_name):
    """Return the mode a command belongs to, or None."""
    for mode, names in MODE_COMMANDS.items():
        if cmd_name in names:
            return mode
    return None


class ChatEngine(object):

    GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = "llama-3.3-70b-versatile"
    MAX_HISTORY = 20
    TIMEOUT_MS = 60000

    # ── System prompt pieces ────────────────────────────────────────────
    REVITBOT_COMMAND_RULES = (
        "IMPORTANT RULES FOR REVITBOT COMMANDS (actions this chat can run directly):\n"
        "- Only suggest typing a command when the user clearly wants to RUN an action right now.\n"
        "- If the user is asking HOW to do something, explain it — do not just say 'type export pdf'.\n"
        "- Available direct commands the user can type:\n"
        "  export nwc | export ifc | export dwg | export pdf\n"
        "  save\n"
        "  create [N] sheet(s) / room(s) / level(s) / grid(s)\n"
        "  create section / callout / dimension / text note\n"
        "  create filled region / room tag / workset / view filter\n"
        "  rename sheet [number] to [name]\n"
        "  renumber sheet [old] to [new]\n"
        "  delete sheet [number] / delete view [name]\n"
        "  open view [name] / zoom to fit\n"
        "  doc info / model stats\n"
        "  list views / sheets / levels / grids / rooms / families / worksets / materials / categories\n"
        "  list revisions / sheet revisions [num] / list links / schedules / phases / warnings\n"
        "  selected info / enable worksharing / delete selected\n"
        "- For quantities say: 'create 5 sheets', 'create 10 rooms'\n"
        "- IMPORTANT: If RevX has a tool for an export/action and the user asks about it, "
        "explain the RevX tool way. If not, explain the native Revit way.\n"
    )

    NO_REVX_RULES = (
        "CRITICAL RULE — DO NOT MENTION REVX UNLESS ASKED:\n"
        "- If the user's question is about general Revit, BIM, architecture, engineering, "
        "construction, materials, concrete, structures, families, parameters, or ANY topic "
        "not specifically about the RevX extension:\n"
        "  * Do NOT mention RevX tools at all.\n"
        "  * Do NOT say 'you can also use RevX' or 'RevX has a tool for this'.\n"
        "  * Do NOT suggest RevX buttons, panels, or ribbon paths.\n"
        "  * Just answer the question using your Revit/AEC knowledge.\n"
        "- ONLY mention RevX when the user explicitly says 'RevX', 'rev x', or asks about "
        "a specific RevX tool name such as: Copy State, Paste State, Sheet Export, Match Slope, "
        "Table Gen, Para, Get Blocks, Floor to Toposolid, Organic Mound, Health, Name Changer, "
        "Server Families, In-Place to Loadable, Object Outline, Transfer Filters, etc.\n"
    )

    REVX_HELP_RULES = (
        "REVX EXTENSION HELP:\n"
        "- RevX is a separate pyRevit extension with its own tab in Revit.\n"
        "- The user is asking about a specific RevX tool. Use the knowledge base below.\n"
        "- Give clear numbered step-by-step instructions.\n"
        "- Always state the exact ribbon path: RevX tab > Panel > Button.\n"
        "- Explain what the tool does, when to use it, prerequisites, version limits.\n"
        "- Add troubleshooting tips if relevant.\n"
        "- Be thorough — use as many words as needed.\n"
        "- Do NOT tell users to type RevitBot commands for RevX tools.\n"
        "- If the tool is not in the knowledge base, say so clearly.\n"
    )

    MODE_AI_RULES = {
        MODE_INFORMATIONS: (
            "CURRENT MODE: INFORMATIONS\n"
            "- The user selected INFORMATIONS mode.\n"
            "- Only discuss model/project information: views, sheets, levels, families, "
            "worksets, materials, categories, revisions, warnings, links, rooms, grids, "
            "schedules, phases, element data, counts, names, parameters.\n"
            "- A LIVE MODEL SNAPSHOT with real data from the user's project is included "
            "below when available — answer project questions from it, never guess.\n"
            "- If asked for details beyond the snapshot, name the RevitBot command that "
            "fetches them (e.g. 'list revisions', 'sheet revisions A-101', 'list warnings', "
            "'model stats', 'selected info').\n"
            "- Do NOT suggest creating, modifying, deleting, exporting or saving here.\n"
            "- If the user asks for an action, say it belongs to Tasks or Tools mode "
            "and they should click that button above the chat first.\n"
        ),
        MODE_TASKS: (
            "CURRENT MODE: TASKS\n"
            "- The user selected TASKS mode.\n"
            "- Focus on model actions: creating sheets/rooms/levels/grids, renaming, "
            "deleting, opening views, worksharing.\n"
            "- Do NOT handle export or save requests — those belong to Tools mode.\n"
            "- For pure information lookups, suggest switching to Informations mode.\n"
        ),
        MODE_TOOLS: (
            "CURRENT MODE: TOOLS\n"
            "- The user selected TOOLS mode.\n"
            "- Focus on project tools: exporting NWC, IFC, DWG, PDF and saving the model.\n"
            "- Explain export formats, settings and workflows when asked.\n"
            "- For model edits or info lookups, suggest Tasks or Informations mode.\n"
        ),
    }

    # ── System prompt builder ───────────────────────────────────────────
    def _build_system_prompt(self, include_revx=False):
        """
        Build system prompt. RevX knowledge is ONLY included when the user
        is asking about RevX.
        """
        base = (
            "You are RevitBot, a helpful AI assistant built into Autodesk Revit via pyRevit. "
            "You help with Revit questions, BIM topics, general AEC questions, "
            "and RevitBot direct commands.\n\n"
            + self.REVITBOT_COMMAND_RULES
            + "\n\n"
            + self.NO_REVX_RULES
        )

        # Restrict the AI's scope to the currently selected mode
        mode_rules = self.MODE_AI_RULES.get(self.mode)
        if mode_rules:
            base += "\n\n" + mode_rules

        # Informations mode background check: give the AI a live snapshot
        # of the open model so it answers with real project data.
        if self.mode == MODE_INFORMATIONS and info_queries is not None:
            try:
                snapshot = info_queries.build_model_snapshot(self._get_doc())
                if snapshot:
                    base += ("\n\nLIVE MODEL SNAPSHOT (real data from the user's open "
                             "project, refreshed for every question):\n" + snapshot)
            except Exception:
                pass

        if include_revx:
            base += "\n\n" + self.REVX_HELP_RULES
            revx = get_revx_knowledge_text()
            if revx:
                base += "\n\n" + revx

        base += (
            "\n\nGeneral tone: be concise and clear. "
            "Plain text only — no markdown symbols like **, ##, or --- in your response."
        )
        return base

    # ── Init ────────────────────────────────────────────────────────────
    def __init__(self, revit_tools):
        self.tools = revit_tools
        self.api_key = None
        self.conversation_history = []
        self._pending_view_id = None
        self.mode = MODE_INFORMATIONS
        self._load_api_key()

    # ── API Key Management ──────────────────────────────────────────────
    def _key_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "groq_key.txt")

    def _load_api_key(self):
        try:
            path = self._key_path()
            if os.path.exists(path):
                with open(path, "r") as f:
                    key = f.read().strip()
                if key and len(key) > 10:
                    self.api_key = key
        except Exception:
            pass

    def save_api_key(self, key):
        key = key.strip() if key else ""
        if key and len(key) > 10:
            self.api_key = key
            try:
                with open(self._key_path(), "w") as f:
                    f.write(key)
                return True
            except Exception:
                return False
        return False

    def has_api_key(self):
        return self.api_key is not None and len(self.api_key) > 10

    # ── Mode Management ─────────────────────────────────────────────────
    def set_mode(self, mode):
        """Restrict execution scope to one of: informations / tasks / tools."""
        if mode in MODE_COMMANDS:
            self.mode = mode

    def get_mode(self):
        return self.mode

    # ── Document access / info-query execution ──────────────────────────
    def _get_doc(self):
        for attr in ("doc", "_doc", "document"):
            doc = getattr(self.tools, attr, None)
            if doc is not None:
                return doc
        return None

    def _get_uidoc(self):
        for attr in ("uidoc", "_uidoc", "ui_document"):
            uidoc = getattr(self.tools, attr, None)
            if uidoc is not None:
                return uidoc
        return None

    def _exec_info(self, fn):
        """Run a read-only info_queries function against the live model."""
        if info_queries is None:
            return ("info_queries.py is missing — copy it into this "
                    "pushbutton folder next to script.py.", "error")
        doc = self._get_doc()
        if doc is None:
            return ("Could not access the Revit document.", "error")
        return self._exec(lambda: fn(doc, self._get_uidoc()))

    # ── Command Execution ───────────────────────────────────────────────
    def _exec(self, func):
        try:
            result = func()
            if isinstance(result, tuple) and len(result) == 3:
                success, msg, view_id = result
                if view_id:
                    self._pending_view_id = view_id
                return (msg, "tool" if success else "error")
            elif isinstance(result, tuple) and len(result) == 2:
                success, msg = result
                return (msg, "tool" if success else "error")
            else:
                return (str(result), "bot")
        except Exception as ex:
            return ("Error: {}".format(str(ex)), "error")

    def _run_command(self, cmd_name, original_message):
        count = extract_number(original_message)
        path = extract_path(original_message)

        if cmd_name == "export_nwc":
            return self._exec(lambda: self.tools.export_nwc(path))
        elif cmd_name == "export_ifc":
            return self._exec(lambda: self.tools.export_ifc(path))
        elif cmd_name == "export_dwg":
            return self._exec(lambda: self.tools.export_dwg(path))
        elif cmd_name == "export_pdf":
            return self._exec(lambda: self.tools.export_pdf(path))
        elif cmd_name == "save_file":
            return self._exec(lambda: self.tools.save_file(path))
        elif cmd_name == "create_sheet":
            return self._exec(lambda: self.tools.create_sheet(count=count))
        elif cmd_name == "create_room":
            return self._exec(lambda: self.tools.create_room(count=count))
        elif cmd_name == "create_level":
            return self._exec(lambda: self.tools.create_level(count=count))
        elif cmd_name == "create_grid":
            return self._exec(lambda: self.tools.create_grid(count=count))
        elif cmd_name == "create_section":
            return self._exec(lambda: self.tools.create_section())
        elif cmd_name == "create_callout":
            return self._exec(lambda: self.tools.create_callout())
        elif cmd_name == "create_dimension":
            return self._exec(lambda: self.tools.create_dimension())
        elif cmd_name == "create_text_note":
            return self._exec(lambda: self.tools.create_text_note())
        elif cmd_name == "create_filled_region_pick":
            return self._exec(lambda: self.tools.create_filled_region_pick_lines())
        elif cmd_name == "create_filled_region":
            return self._exec(lambda: self.tools.create_filled_region_rect())
        elif cmd_name == "create_room_tag":
            return self._exec(lambda: self.tools.create_room_tag())
        elif cmd_name == "create_workset":
            return self._exec(lambda: self.tools.create_workset())
        elif cmd_name == "create_view_filter":
            return self._exec(lambda: self.tools.create_view_filter())
        elif cmd_name == "rename_sheet":
            s_num, new_name = extract_rename_args(original_message)
            return self._exec(lambda: self.tools.rename_sheet(s_num, new_name))
        elif cmd_name == "rename_sheet_number":
            old_num, new_num = extract_renumber_args(original_message)
            return self._exec(lambda: self.tools.rename_sheet_number(old_num, new_num))
        elif cmd_name == "delete_sheet":
            s_num = extract_delete_sheet_arg(original_message)
            return self._exec(lambda: self.tools.delete_sheet(s_num))
        elif cmd_name == "delete_view":
            v_name = extract_delete_view_arg(original_message)
            return self._exec(lambda: self.tools.delete_view(v_name))
        elif cmd_name == "delete_selected":
            return self._exec(lambda: self.tools.delete_selected())
        elif cmd_name == "delete_elements_by_category":
            match = re.search(
                r'(?:delete|remove)\s+all\s+(\w+)', original_message, re.IGNORECASE
            )
            cat_name = match.group(1) if match else None
            return self._exec(lambda: self.tools.delete_elements_by_category(cat_name))
        elif cmd_name == "open_view":
            v_name = extract_view_name(original_message)
            return self._exec(lambda: self.tools.open_view(v_name))
        elif cmd_name == "zoom_to_fit":
            return self._exec(lambda: self.tools.zoom_to_fit())
        elif cmd_name == "list_views":
            return self._exec(lambda: self.tools.list_views())
        elif cmd_name == "list_sheets":
            return self._exec(lambda: self.tools.list_sheets())
        elif cmd_name == "list_levels":
            return self._exec(lambda: self.tools.list_levels())
        elif cmd_name == "list_families":
            return self._exec(lambda: self.tools.list_families())
        elif cmd_name == "list_worksets":
            return self._exec(lambda: self.tools.list_worksets())
        elif cmd_name == "list_materials":
            return self._exec(lambda: self.tools.list_materials())
        elif cmd_name == "list_categories":
            return self._exec(lambda: self.tools.list_categories())
        elif cmd_name == "doc_info":
            return self._exec(lambda: self.tools.get_document_info())
        elif cmd_name == "enable_worksharing":
            return self._exec(lambda: self.tools.enable_worksharing())
        # ── Live read-only queries (Informations mode) ─────────────────
        elif cmd_name == "list_revisions":
            return self._exec_info(lambda doc, uidoc: info_queries.list_revisions(doc))
        elif cmd_name == "sheet_revisions":
            sheet_no = extract_sheet_revision_arg(original_message)
            return self._exec_info(
                lambda doc, uidoc: info_queries.sheet_revisions(doc, sheet_no))
        elif cmd_name == "list_warnings":
            return self._exec_info(lambda doc, uidoc: info_queries.list_warnings(doc))
        elif cmd_name == "list_links":
            return self._exec_info(lambda doc, uidoc: info_queries.list_links(doc))
        elif cmd_name == "list_rooms":
            return self._exec_info(lambda doc, uidoc: info_queries.list_rooms(doc))
        elif cmd_name == "list_grids":
            return self._exec_info(lambda doc, uidoc: info_queries.list_grids(doc))
        elif cmd_name == "list_schedules":
            return self._exec_info(lambda doc, uidoc: info_queries.list_schedules(doc))
        elif cmd_name == "list_phases":
            return self._exec_info(lambda doc, uidoc: info_queries.list_phases(doc))
        elif cmd_name == "model_stats":
            return self._exec_info(lambda doc, uidoc: info_queries.model_stats(doc))
        elif cmd_name == "selected_info":
            return self._exec_info(
                lambda doc, uidoc: info_queries.selected_info(doc, uidoc))
        else:
            return ("Unknown command: {}".format(cmd_name), "error")

    # ── Message Processing ──────────────────────────────────────────────
    def process_message(self, message):
        message = message.strip()
        if not message:
            return [("Type something!", "bot")]

        msg = message.lower()

        # ── Static responses ─────────────────────────────────────────
        if re.match(r"^(set\s+api\s+key|api\s+key|change\s+key)\b", msg):
            return [("Use the key button to set your Groq API key.", "bot")]

        if re.match(r"^(hi|hello|hey|howdy|sup|yo|hola)\b", msg):
            return [("Hey! What can I help you with?", "bot")]

        if re.match(r"^(thanks?|thank\s+you|thx|ty|cheers)\b", msg):
            return [("You're welcome! Anything else?", "bot")]

        if re.match(r"^(bye|goodbye|see\s+you|cya)\b", msg):
            return [("See you! Happy Revit-ing!", "bot")]

        if re.match(r"^(help|commands?|what\s+can\s+you\s+do)\b", msg):
            return [(
                "RevitBot works in three modes — the buttons above switch "
                "between them, and commands only run in their own mode.\n\n"
                "INFORMATIONS mode (cyan) — live model queries:\n"
                "  doc info\n"
                "  model stats\n"
                "  list views / sheets / levels / grids / rooms\n"
                "  list families / worksets / materials / categories\n"
                "  list revisions\n"
                "  sheet revisions [A-101]\n"
                "  list links\n"
                "  list schedules\n"
                "  list phases\n"
                "  list warnings\n"
                "  selected info\n\n"
                "TASKS mode (green):\n"
                "  create [N] sheets / rooms / levels / grids\n"
                "  create section / callout / dimension / text note\n"
                "  create filled region / room tag / workset / view filter\n"
                "  rename sheet [A-1] to [Floor Plan]\n"
                "  renumber sheet [A-1] to [A-2]\n"
                "  delete sheet [A-1]\n"
                "  delete view [view name]\n"
                "  delete selected\n"
                "  open view [view name]\n"
                "  zoom to fit\n"
                "  enable worksharing\n\n"
                "TOOLS mode (orange):\n"
                "  export nwc\n"
                "  export ifc\n"
                "  export dwg\n"
                "  export pdf\n"
                "  save\n\n"
                "You can also ask me anything about Revit, BIM, or RevX tools!",
                "bot"
            )]

        # ── Questions and RevX → always go to AI ────────────────────
        if is_help_or_question(message):
            return None

        if mentions_revx(message):
            return None

        # ── Only run command patterns if message looks like a command ─
        if is_explicit_command(message):
            for pattern, cmd_name in COMMAND_PATTERNS:
                if re.match(pattern, msg):
                    # Mode gate — refuse out-of-mode commands with a hint
                    needed_mode = command_mode(cmd_name)
                    if needed_mode is not None and needed_mode != self.mode:
                        return [(
                            "That's a {} command, and you're in {} mode.\n"
                            "Click the {} button above to switch.".format(
                                MODE_LABELS.get(needed_mode, needed_mode),
                                MODE_LABELS.get(self.mode, self.mode),
                                MODE_LABELS.get(needed_mode, needed_mode)),
                            "bot"
                        )]
                    return [self._run_command(cmd_name, message)]

        # ── Anything else → AI ───────────────────────────────────────
        return None

    # ── Groq AI Query ───────────────────────────────────────────────────
    def query_ai(self, message):
        if not self.has_api_key():
            raise Exception(
                "No API key set. Click the key button to add your free Groq key."
            )

        self.conversation_history.append({"role": "user", "content": message})
        if len(self.conversation_history) > self.MAX_HISTORY:
            self.conversation_history = self.conversation_history[-self.MAX_HISTORY:]

        # ── Decide whether RevX knowledge is needed ──────────────────
        include_revx = mentions_revx(message)

        # Also check last 3 conversation messages for RevX context
        if not include_revx:
            recent = self.conversation_history[-3:]
            for entry in recent:
                if mentions_revx(entry.get("content", "")):
                    include_revx = True
                    break

        # ── Build prompt and messages ────────────────────────────────
        messages = [
            {"role": "system", "content": self._build_system_prompt(include_revx=include_revx)}
        ]
        for entry in self.conversation_history:
            messages.append({"role": entry["role"], "content": entry["content"]})

        # RevX answers need more tokens; general answers stay short
        max_tokens = 2048 if include_revx else 1024
        body = {
            "model": self.MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens,
            "top_p": 0.9,
        }
        json_body = dict_to_json(body)

        request = System.Net.WebRequest.Create(self.GROQ_URL)
        request.Method = "POST"
        request.ContentType = "application/json"
        request.Headers.Add("Authorization", "Bearer " + self.api_key)
        request.Timeout = self.TIMEOUT_MS

        body_bytes = System.Text.Encoding.UTF8.GetBytes(json_body)
        request.ContentLength = body_bytes.Length

        stream = request.GetRequestStream()
        stream.Write(body_bytes, 0, body_bytes.Length)
        stream.Close()

        response = None
        try:
            response = request.GetResponse()
            reader = System.IO.StreamReader(response.GetResponseStream())
            response_text = reader.ReadToEnd()
            reader.Close()
        except Exception as ex:
            error_msg = str(ex)
            if "401" in error_msg:
                raise Exception("Invalid API key. Click the key button to update it.")
            elif "429" in error_msg:
                raise Exception("Rate limit reached. Please wait a moment and try again.")
            else:
                raise Exception("Network error: {}".format(error_msg))
        finally:
            if response:
                try:
                    response.Close()
                except Exception:
                    pass

        content = parse_groq_response(response_text)
        self.conversation_history.append({"role": "assistant", "content": content})
        return (content, "ai")
