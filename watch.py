#!/usr/bin/env python3
"""Avisa no Discord quando a Steam publica qualquer atualizacao dos apps vigiados.

Roda a cada 5 minutos pelo GitHub Actions. Consulta a versao publicada com o SteamCMD
(imagem oficial em Docker, sem login), compara com state.json e posta no Discord.
O estado so avanca depois que o Discord confirma o recebimento do aviso.
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
TZ = ZoneInfo("America/Fortaleza")
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
STATE_PATH = os.path.join(ROOT, "state.json")
WEBHOOK = os.environ["DISCORD_WEBHOOK"].strip()
MENTION = os.environ["MENTION_USER_ID"].strip()
SELFTEST = os.environ.get("SELFTEST", "").lower() == "true"
PREFIX = "[TESTE] " if SELFTEST else ""

TOKEN = re.compile(r'"([^"]*)"|([{}])')


def log(msg):
    print(f"{datetime.now(TZ):%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def fmt_unix(ts):
    return datetime.fromtimestamp(int(ts), TZ).strftime("%d/%m/%Y %H:%M")


def parse_block(tokens, pos):
    out = {}
    while pos < len(tokens):
        m = tokens[pos]
        if m.group(2) == "}":
            return out, pos + 1
        key = m.group(1)
        pos += 1
        nxt = tokens[pos]
        if nxt.group(2) == "{":
            out[key], pos = parse_block(tokens, pos + 1)
        else:
            out[key] = nxt.group(1)
            pos += 1
    return out, pos


def extract(text, app_id):
    m = re.search(r'^"%s"\s*$' % app_id, text, re.M)
    if not m:
        return None
    tokens = list(TOKEN.finditer(text[m.end():]))
    if not tokens or tokens[0].group(2) != "{":
        return None
    app, _ = parse_block(tokens, 1)
    depots = app.get("depots", {})
    pub = depots.get("branches", {}).get("public")
    if not pub or not pub.get("buildid"):
        return None
    manifests = []
    for key, val in depots.items():
        if key.isdigit() and isinstance(val, dict):
            gid = val.get("manifests", {}).get("public")
            if isinstance(gid, dict):
                gid = gid.get("gid")
            if gid:
                manifests.append(f"{key}:{gid}")
    return {
        "buildid": pub["buildid"],
        "timeupdated": pub.get("timeupdated", ""),
        "timebuildupdated": pub.get("timebuildupdated", ""),
        "depots": sorted(manifests),
        "branches": sorted(depots.get("branches", {}).keys()),
    }


def query_all(app_ids):
    found = {}
    for attempt in range(1, 4):
        todo = [a for a in app_ids if a not in found]
        if not todo:
            break
        cmd = ["docker", "run", "--rm", "steamcmd/steamcmd:latest", "+login", "anonymous", "+app_info_update", "1"]
        for a in todo:
            cmd += ["+app_info_print", a]
        cmd += ["+quit"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=300).stdout
            for a in todo:
                info = extract(out, a)
                if info:
                    found[a] = info
                else:
                    log(f"app {a}: resposta sem dados (tentativa {attempt})")
        except Exception as exc:  # noqa: BLE001
            log(f"erro ao consultar a Steam: {exc} (tentativa {attempt})")
        if len(found) < len(app_ids):
            time.sleep(15)
    return found


def send_discord(content, silent=False):
    body = {"content": content, "username": "Monitor FC 27", "allowed_mentions": {"users": [MENTION]}}
    if silent:
        body["flags"] = 4096
    data = json.dumps(body).encode("utf-8")
    for attempt in range(1, 5):
        try:
            req = urllib.request.Request(
                WEBHOOK + "?wait=true", data=data, method="POST",
                headers={"Content-Type": "application/json", "User-Agent": "DiscordBot (steam-update-watch, 1.0)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                if json.loads(resp.read().decode("utf-8")).get("id"):
                    return True
        except (urllib.error.URLError, ValueError) as exc:
            log(f"discord: erro {exc} (tentativa {attempt})")
        time.sleep(5 * attempt)
    return False


def compare(old, new):
    changes = []
    if old["buildid"] != new["buildid"]:
        changes.append(f"versão pública: {old['buildid']} → {new['buildid']}")
    elif old["timeupdated"] != new["timeupdated"]:
        changes.append("a data da versão pública mudou")
    diff = set(old["depots"]) ^ set(new["depots"])
    if diff:
        ids = sorted({d.split(":")[0] for d in diff})
        txt = ", ".join(ids[:6]) + (f" e mais {len(ids) - 6}" if len(ids) > 6 else "")
        changes.append(f"arquivos do jogo (depots) alterados ({len(ids)}): {txt}")
    added = sorted(set(new["branches"]) - set(old["branches"]))
    removed = sorted(set(old["branches"]) - set(new["branches"]))
    if added:
        changes.append("novos ramos na Steam: " + ", ".join(added))
    if removed:
        changes.append("ramos removidos: " + ", ".join(removed))
    return changes


def main():
    state = {"apps": {}, "failures": 0, "failAlerted": False, "lastHeartbeat": ""}
    if os.path.exists(STATE_PATH):
        state.update(json.load(open(STATE_PATH, encoding="utf-8")))
    first_run = not state["apps"]
    mention = f"<@{MENTION}>"
    apps = CFG["apps"]
    results = query_all([a["id"] for a in apps])
    any_fail = len(results) < len(apps)
    report = []

    for app in apps:
        app_id, name = app["id"], app["name"]
        new = results.get(app_id)
        if not new:
            log(f"app {app_id}: não consegui consultar a Steam")
            continue
        old = state["apps"].get(app_id)
        if SELFTEST and old and app is apps[0]:
            old = {"buildid": "1", "timeupdated": "1", "timebuildupdated": "1", "depots": [], "branches": old["branches"]}
        if not old:
            state["apps"][app_id] = new
            report.append(f"{name}: versão {new['buildid']}, liberada em {fmt_unix(new['timeupdated'])}")
            log(f"app {app_id}: base gravada (versão {new['buildid']})")
            continue
        changes = compare(old, new)
        if changes:
            msg = (f"{mention} {PREFIX}**ATUALIZAÇÃO NA STEAM: {name}**\n"
                   f"Liberada na Steam em: {fmt_unix(new['timeupdated'])} (Fortaleza)\n"
                   + "\n".join(f"• {c}" for c in changes)
                   + f"\nDetalhes: <https://steamdb.info/app/{app_id}/patches/>")
            if send_discord(msg):
                log(f"app {app_id}: aviso enviado ({'; '.join(changes)})")
                if not SELFTEST:
                    state["apps"][app_id] = new
            else:
                log(f"app {app_id}: FALHA ao enviar aviso, tenta de novo na próxima rodada")
        else:
            log(f"app {app_id}: sem mudança (versão {new['buildid']})")
            state["apps"][app_id] = new

    if any_fail:
        state["failures"] += 1
        if state["failures"] >= CFG["failureAlertAfter"] and not state["failAlerted"]:
            mins = state["failures"] * CFG["intervalMinutes"]
            if send_discord(f"{mention} {PREFIX}**O monitor na nuvem não está conseguindo consultar a Steam há cerca de {mins} minutos.** Enquanto isso, atualizações podem passar sem aviso."):
                state["failAlerted"] = True
    else:
        if state["failAlerted"]:
            send_discord(f"{PREFIX}Monitor na nuvem voltou a consultar a Steam normalmente.")
        state["failures"] = 0
        state["failAlerted"] = False

    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    if first_run and report:
        text = f"{mention} {PREFIX}**Monitor do FC 27 na nuvem ligado.** Vou te marcar aqui quando a Steam publicar qualquer atualização.\n" + "\n".join(report)
        if send_discord(text):
            state["lastHeartbeat"] = today
    elif not any_fail and now.hour >= CFG["heartbeatHour"] and state["lastHeartbeat"] != today:
        lines = [f"{a['name']}: versão {state['apps'][a['id']]['buildid']}, liberada em {fmt_unix(state['apps'][a['id']]['timeupdated'])}"
                 for a in apps if a["id"] in state["apps"]]
        if send_discord(f"{PREFIX}Monitor na nuvem ativo, sem novidades.\n" + "\n".join(lines), silent=True):
            state["lastHeartbeat"] = today

    if not SELFTEST:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")


if __name__ == "__main__":
    main()
