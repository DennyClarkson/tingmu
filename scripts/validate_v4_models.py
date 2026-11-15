"""Opt-in text-only live acceptance with synthetic language-learning examples."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from tingmu.agent import LearningAgent, _config
from tingmu.companion import CompanionAgent
from tingmu.models import Cue, LanguagePair, Token
from tingmu.notebooks import NotebookManager
from tingmu.providers import ModelRegistry
from tingmu.quick.features import learning_tools
from tingmu.review import TranscriptReviewer
from tingmu.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    if not args.live:
        return
    root = Path(__file__).resolve().parents[1]
    store = Store(root / ".data/v4-model-validation")
    registry = ModelRegistry(store.data_dir)
    _, base, _ = _config("openai")
    profile = registry.save_profile({"id": "validation-astra", "provider": "openai", "label": "隔离验收模型",
                                     "base_url": base, "model": "gpt-6-astra", "key_source": "environment",
                                     "env_var": "OPENAI_API_KEY"})
    report = {"synthetic_text_only": True, "no_user_media_uploaded": True, "checks": {}}
    started = time.monotonic()
    models = registry.discover_models(profile["id"])
    report["models_discovered"] = len(models)
    report["checks"]["model_discovery"] = "gpt-6-astra" in models
    print("model discovery completed", flush=True)
    resolved = registry.resolve(profile["id"])
    language = LanguagePair()
    source = [Cue("synthetic-1", 0, 4, "唔該，幫我拎杯水過嚟。"),
              Cue("synthetic-2", 4, 8, "我哋聽日一齊去飲茶，好唔好呀？"),
              Cue("synthetic-3", 8, 18, "如果聽日落雨，我哋就留喺屋企睇戲，等天氣好返先再出去行街，唔使咁急決定。")]
    reviewer = TranscriptReviewer(profile=resolved)
    reviewed = reviewer.review(source, language)
    report["checks"]["preserved_identity_timing_raw"] = all(
        (a.id, a.start, a.end, a.original_text) == (b.id, b.start, b.end, b.original_text)
        for a, b in zip(source, reviewed))
    report["checks"]["exact_segments"] = all("".join(c.segmented_texts) == c.text for c in reviewed)
    report["review_responses"] = [{k: row.get(k) for k in ("requested_model", "returned_model", "usage", "latency_seconds")}
                                  for row in reviewer.response_metadata]
    print("text review completed", flush=True)
    teacher = LearningAgent("openai", "gpt-5.4-mini")
    teacher.preserve_text = True
    annotated = teacher.annotate(reviewed, language)
    report["checks"]["teacher_preserved_reviewed_text"] = all(a.text == b.text for a, b in zip(reviewed, annotated))
    report["checks"]["readings_and_explanations"] = all(c.translation and c.explanation and c.tokens for c in annotated)
    report["synthetic_examples"] = [asdict(c) for c in annotated]
    report["teacher_responses"] = [{k: row.get(k) for k in ("requested_model", "returned_model", "usage", "latency_seconds")}
                                   for row in teacher.response_metadata]
    print("teaching completed", flush=True)
    book = NotebookManager(store)
    movie = str(store.data_dir / "synthetic.mp4")
    tool_calls, deltas = [], []
    def execute(name, arguments):
        tool_calls.append(name)
        if name == "collect_current_word":
            text = arguments["text"]
            if text not in annotated[0].text:
                raise ValueError("No such word")
            store.save_word(movie, annotated[0], next((t for t in annotated[0].tokens if t.text == text), Token(text)), language)
            book.sync("yue")
            return {"saved": True, "text": text, "language": "yue"}
        if name == "read_notebook":
            return {"markdown": book.sync("yue")["markdown"]}
        raise ValueError("Unexpected validation tool")
    companion = CompanionAgent(profile=resolved)
    reply = companion.reply("请用工具收藏当前句里的『唔該』，然后简短说明用法，用一个两列表格比较它和『多謝』，最后出一道练习题。",
                            [], language, annotated[0], {"cloud_text_enabled": True},
                            on_delta=deltas.append, tools=learning_tools(), tool_executor=execute)
    report["checks"]["agent_collected_word"] = any(e["text"] == "唔該" for e in store.entries())
    report["checks"]["streaming_received"] = bool(deltas)
    report["checks"]["formatted_reply"] = "|" in reply
    report["chat"] = {"tool_calls": tool_calls, "delta_count": len(deltas), "reply": reply,
                      "metadata": companion.last_response_metadata}
    book.organize("yue", LearningAgent("openai", "gpt-6-astra", profile=resolved))
    report["checks"]["organized_markdown"] = bool(book.snapshot("yue")["topics"])
    report["elapsed_seconds"] = round(time.monotonic()-started, 2)
    report["passed"] = all(report["checks"].values())
    (root / "reports/v4-model-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": report["checks"], "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
