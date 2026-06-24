_DIRECT_ALIASES = {
    "help": "/help",
    "pair": "/pair",
    "diag": "/diag",
}


def normalize_command_aliases(text: str, *, bot_username: str | None) -> str:
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return text

    parts = stripped.split()
    if not parts:
        return text

    cmd = parts[0][1:]
    args = parts[1:]

    if "@" in cmd:
        cmd_name, cmd_bot = cmd.split("@", 1)
        if bot_username and cmd_bot.lower() != bot_username.lower():
            return text
        cmd = cmd_name

    cmd = cmd.lower()

    if cmd == "start":
        if args:
            return stripped
        return "/help"
    if cmd in _DIRECT_ALIASES:
        return _DIRECT_ALIASES[cmd]
    if cmd == "status":
        return "/session status"
    if cmd == "new":
        return "/session new"
    if cmd in {"stop", "cancel"}:
        return "/agent stop"
    if cmd == "run":
        if args and args[0].lower() == "status":
            return "/job ls"
        return "/run " + " ".join(args)

    if cmd == "agent":
        if not args:
            return "/agent ls"
        first = args[0].lower()
        if first in {"use", "ls", "info", "stop"}:
            return "/" + " ".join([cmd] + args)
        return f"/agent use {args[0]}"

    # Skill command routing for controlplane parity
    if cmd == "skill":
        if not args:
            return "/skill ls"
        first = args[0].lower()
        if first in {"ls", "list", "info"}:
            return "/skill ls"
        if first in {"ingest", "learn", "load"}:
            return (
                "/skill ingest " + " ".join(args[1:])
                if len(args) > 1
                else "/skill ingest"
            )
        if first in {"use", "run", "execute"}:
            return "/skill use " + " ".join(args[1:]) if len(args) > 1 else "/skill use"
        return "/skill ls"

    return "/" + " ".join([cmd] + args)
