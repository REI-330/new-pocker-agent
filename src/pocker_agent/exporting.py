"""Compile a bounded, offline playable demo from the canonical Python engine."""
import io
import json
import zipfile
from pathlib import Path

from .engine import RuleEngine
from .runtime import RuntimeSession, run_bots, snapshot

ASSET_DIR = Path(__file__).parent / "assets" / "cards"
MAX_NODES = 2000


def compile_game(rules, seed=7):
    engine = RuleEngine(rules, seed=seed)
    engine.setup()
    run_bots(engine)
    nodes = []
    pending = [(engine, None, None)]
    while pending:
        current, parent, edge = pending.pop()
        if len(nodes) >= MAX_NODES:
            raise ValueError("离线导出超过 2000 个决策节点，请减少轮数或手牌数量；在线试玩不受此限制")
        node_id = len(nodes)
        state = snapshot(RuntimeSession("offline", current, seed=seed))
        state["events"] = state["events"][-10:]
        node = {"state": state, "moves": []}
        nodes.append(node)
        if parent is not None:
            nodes[parent]["moves"].append({**edge, "next": node_id})
        for action_name in reversed(current.legal_actions()):
            action = next(a for a in rules.actions if a.name == action_name)
            hand = current.state.players[current.state.current_player].hand
            choices = range(len(hand) - action.amount + 1) if action_name in {"play", "discard"} else [0]
            for index in reversed(list(choices)):
                child = RuleEngine.restore(current.serialize())
                child.step(action_name, index)
                run_bots(child)
                if len(nodes) + len(pending) >= MAX_NODES:
                    raise ValueError("离线导出超过 2000 个决策节点，请减少轮数或手牌数量")
                pending.append((child, node_id, {"action": action_name, "card_index": index}))
    return nodes


def export_package(rules):
    if hasattr(rules, "kind"):
        raise ValueError("此玩法已支持网页试玩；需要动态输入的新版玩法暂不支持离线决策树导出")
    nodes = compile_game(rules)
    game = {"title": rules.title, "nodes": nodes}
    encoded = json.dumps(game, ensure_ascii=False).replace("<", "\\u003c")
    template = (Path(__file__).parent / "assets" / "offline.html").read_text(encoding="utf-8")
    content = template.replace("__GAME_DATA__", encoded)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", content)
        archive.writestr("game.json", json.dumps({"name": rules.game_id, "version": "0.1", "rules": rules.model_dump(mode="json")}, ensure_ascii=False))
        archive.writestr("README.txt", "解压后直接打开 index.html，无需服务器、API Key 或网络。\n这是 seed=7 的固定发牌练习局；所有合法选择由同一个 Python 规则引擎预先计算。\n")
        for path in ASSET_DIR.iterdir():
            if path.is_file():
                archive.write(path, "cards/" + path.name)
    return buffer.getvalue(), f"{rules.game_id}.pocker-game.zip"
