#!/usr/bin/env python3
"""Train Narada's routing model and write the files the Pi runs.

A development tool, not part of the service, and not for the Pi: it wants a
GPU. On Colab (a free T4 takes about ten minutes), with the colab CLI:

    python3 scripts/router_data.py --out train.jsonl
    colab new -s router --gpu T4
    # one file at a time, each to /content/<its name>:
    colab upload -s router <file> /content/<name>     train.jsonl, scripts/router_train.py,
                                                      basic_pipelines/garuda_auto/router.py,
                                                      tests/eval/routing_cases.json, tests/eval/routing_written.json
    # `colab exec` gives up after 30 s, so start it detached and read the log:
    echo 'import subprocess; subprocess.Popen("cd /content && (pip -q install onnx onnxruntime onnxscript; python3 router_train.py) > train.log 2>&1", shell=True, start_new_session=True)' | colab exec -s router
    echo 'print(open("/content/train.log").read()[-3000:])' | colab exec -s router
    colab download -s router /content/out/<file> resources/models/narada_router/<file>    model.onnx, tokenizer.json, meta.json
    colab stop -s router

The model is a small transformer encoder with four heads (see
garuda_auto/router.py for what it reads and how it answers). It is trained on
made-up houses, scored on sentences it has never seen, exported to ONNX,
shrunk to 8-bit weights, and scored again through the very code the Pi runs,
so the numbers printed last are the numbers of the file that ships.

The default base reads a hundred languages, and most of its size is a
vocabulary for scripts this house will never type. Before training, every
piece of that vocabulary not written in Latin letters, Telugu or Devanagari
is dropped (--keep-all turns that off): the file and its memory on the Pi
shrink by more than half and nothing it is used for changes.
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from tokenizers import Tokenizer
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

HERE = Path(__file__).resolve().parent
for place in (HERE, HERE.parent / "basic_pipelines" / "garuda_auto"):
    sys.path.insert(0, str(place))
import router  # noqa: E402  (garuda_auto/router.py: the encoding the Pi uses)

INTENTS = ["device_control", "all_off", "scene", "timer", "state_query",
           "automation_rule", "mode_change", "explain", "other"]
ACTIONS = ["on", "off", "none"]
FIELDS = ("intent", "device", "action", "scene")
SKIP = -100
THRESHOLD = 0.85


class Router(nn.Module):
    def __init__(self, base):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(base)
        width = self.encoder.config.hidden_size
        self.drop = nn.Dropout(0.1)
        self.intent = nn.Linear(width, len(INTENTS))
        self.action = nn.Linear(width, len(ACTIONS))
        self.device = nn.Linear(width, 1)
        self.scene = nn.Linear(width, 1)

    def forward(self, input_ids, attention_mask):
        hidden = self.drop(self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state)
        first = hidden[:, 0]
        return self.intent(first), self.action(first), self.device(hidden).squeeze(-1), self.scene(hidden).squeeze(-1)


def _kept(piece):
    """Is this piece of vocabulary written in a script the house uses?"""
    for ch in piece:
        o = ord(ch)
        if not (o < 0x80 or 0x900 <= o <= 0x97F or 0xC00 <= o <= 0xC7F or 0x2000 <= o <= 0x206F or o == 0x2581):
            return False
    return True


def prune(tokenizer, model):
    """Drop the vocabulary of scripts nobody here types, from the tokenizer and from the model with it."""
    spec = json.loads(tokenizer.to_str())
    if spec["model"]["type"] != "Unigram":
        return tokenizer                           # a small English vocabulary: nothing worth dropping
    vocab = spec["model"]["vocab"]
    special = {t["id"] for t in spec["added_tokens"] if t["id"] < 8}       # <s> <pad> </s> <unk>, not <mask>
    keep = [i for i, (piece, _) in enumerate(vocab) if i in special or (i > max(special) and _kept(piece)
                                                                        and piece != "<mask>")]
    spec["model"]["vocab"] = [vocab[i] for i in keep]
    spec["added_tokens"] = [t for t in spec["added_tokens"] if t["id"] in special]
    old = model.encoder.embeddings.word_embeddings
    new = nn.Embedding(len(keep), old.embedding_dim, padding_idx=old.padding_idx)
    new.weight.data.copy_(old.weight.data[keep])
    model.encoder.embeddings.word_embeddings = new
    model.encoder.config.vocab_size = len(keep)
    print(f"vocabulary {len(vocab)} -> {len(keep)} pieces", flush=True)
    return Tokenizer.from_str(json.dumps(spec))


def load_cases(path):
    """Rows of a .jsonl training file or of a tests/eval/*.json set (whose house may be shared)."""
    path = Path(path)
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    data = json.loads(path.read_text())
    return [{"devices": data.get("devices", []), "scenes": data.get("scenes", []), **case} for case in data["cases"]]


def featurise(rows, tokenizer, cls_id, sep_id):
    cache, out = {}, []
    for row in rows:
        ids, device_at, scene_at = router.encode(tokenizer, cls_id, sep_id, row["say"], row["devices"],
                                                 row["scenes"], cache)
        device_ids = [d["id"] for d in row["devices"][:len(device_at)]]
        scene_ids = [s["id"] for s in row["scenes"][:len(scene_at)]]

        def target(value, known, at):
            if value is None:
                return SKIP
            if value == "none":
                return 0
            return at[known.index(value)] if value in known else SKIP

        out.append({"ids": ids, "device_at": device_at, "scene_at": scene_at,
                    "intent": INTENTS.index(row["intent"]),
                    "action": SKIP if row["action"] is None else ACTIONS.index(row["action"]),
                    "device": target(row["device"], device_ids, device_at),
                    "scene": target(row["scene"], scene_ids, scene_at)})
    return out


def collate(items, pad_id, device):
    width = max(len(i["ids"]) for i in items)
    ids = torch.full((len(items), width), pad_id, dtype=torch.long)
    mask = torch.zeros((len(items), width), dtype=torch.long)
    device_ok = torch.zeros((len(items), width), dtype=torch.bool)
    scene_ok = torch.zeros((len(items), width), dtype=torch.bool)
    for n, item in enumerate(items):
        ids[n, :len(item["ids"])] = torch.tensor(item["ids"])
        mask[n, :len(item["ids"])] = 1
        device_ok[n, [0] + item["device_at"]] = True
        scene_ok[n, [0] + item["scene_at"]] = True
    target = {f: torch.tensor([i[f] for i in items]) for f in FIELDS}
    return (ids.to(device), mask.to(device), device_ok.to(device), scene_ok.to(device),
            {f: t.to(device) for f, t in target.items()})


def train(args, model, pad_id, items, device):
    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = args.epochs * math.ceil(len(items) / args.batch)
    schedule = get_linear_schedule_with_warmup(optimiser, int(0.06 * steps), steps)
    scaler = torch.amp.GradScaler(enabled=device == "cuda")
    loss_fn = nn.CrossEntropyLoss(ignore_index=SKIP, label_smoothing=0.05)
    # No smoothing where the model points: it would hand weight to positions that are not candidates.
    point_fn = nn.CrossEntropyLoss(ignore_index=SKIP)
    step, started = 0, time.time()
    for epoch in range(args.epochs):
        model.train()
        random.shuffle(items)
        # Batches of like length waste little on padding.
        chunks = [sorted(items[i:i + args.batch * 50], key=lambda x: len(x["ids"]))
                  for i in range(0, len(items), args.batch * 50)]
        batches = [chunk[i:i + args.batch] for chunk in chunks for i in range(0, len(chunk), args.batch)]
        random.shuffle(batches)
        total = 0.0
        for batch in batches:
            ids, mask, device_ok, scene_ok, target = collate(batch, pad_id, device)
            with torch.autocast(device_type=device, enabled=device == "cuda"):
                intent, action, dev, scn = model(ids, mask)
                dev = dev.float().masked_fill(~device_ok, -1e4)
                scn = scn.float().masked_fill(~scene_ok, -1e4)
                loss = (loss_fn(intent.float(), target["intent"]) + loss_fn(action.float(), target["action"])
                        + point_fn(dev, target["device"]) + point_fn(scn, target["scene"]))
            optimiser.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimiser)
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimiser)
            scaler.update()
            schedule.step()
            total += loss.item()
            step += 1
        print(f"epoch {epoch + 1}/{args.epochs}  loss {total / len(batches):.4f}  {time.time() - started:.0f} s", flush=True)


def score(name, rows, answers, quiet=False, show=12):
    """Print how a set went; `answers` is one {"field": (value, probs)} per row. Returns the summary."""
    kinds, fields = {}, {f: [0, 0] for f in FIELDS}
    sure = sure_wrong = whole = 0
    misses = []
    for row, got in zip(rows, answers):
        ok = True
        for f in FIELDS:
            if row[f] is None:
                continue
            fields[f][1] += 1
            hit = got[f][0] == row[f]
            fields[f][0] += hit
            ok &= hit
        k = kinds.setdefault(row["kind"], [0, 0])
        k[0] += ok
        k[1] += 1
        whole += ok
        confidence = got["intent"][1][got["intent"][0]]
        if confidence >= THRESHOLD:
            sure += 1
            sure_wrong += got["intent"][0] != row["intent"]
        if not ok:
            misses.append((row, got))
    summary = {"whole": whole, "of": len(rows), "kinds": kinds, "fields": fields, "sure": sure, "sure_wrong": sure_wrong}
    if not quiet:
        print(f"\n== {name}: whole decision right {whole}/{len(rows)}")
        print("   " + "  ".join(f"{f} {a}/{b}" for f, (a, b) in fields.items()))
        print("   " + "  ".join(f"{k} {a}/{b}" for k, (a, b) in sorted(kinds.items())))
        print(f"   sure of the intent (>= {THRESHOLD}) on {sure}/{len(rows)}; wrong while sure: {sure_wrong}")
        for row, got in misses[:show]:
            wrong = {f: got[f][0] for f in FIELDS if row[f] is not None and got[f][0] != row[f]}
            print(f"     {row['say']!r} [{row['kind']}]: {wrong}, wanted "
                  f"{ {f: row[f] for f in wrong} }")
    return summary



def torch_answers(model, tok, rows, cls_id, sep_id, device, heat=None):
    """The model's answers in the shape router.RouterModel.answer gives, plus the raw scores for calibration."""
    heat = heat or {}
    items = featurise(rows, tok, cls_id, sep_id)
    model.eval()
    answers, raw = [], []
    with torch.no_grad():
        for row, item in zip(rows, items):
            ids = torch.tensor([item["ids"]], device=device)
            intent, action, dev, scn = (t[0].float().cpu().numpy() for t in model(ids, torch.ones_like(ids)))
            scores = {"intent": intent, "action": action, "device": dev[[0] + item["device_at"]],
                      "scene": scn[[0] + item["scene_at"]]}
            labels = {"intent": INTENTS, "action": ACTIONS,
                      "device": ["none"] + [d["id"] for d in row["devices"][:len(item["device_at"])]],
                      "scene": ["none"] + [s["id"] for s in row["scenes"][:len(item["scene_at"])]]}
            got = {}
            for f in FIELDS:
                probs = router._softmax(scores[f], heat.get(f, 1.0))
                got[f] = (labels[f][int(probs.argmax())], dict(zip(labels[f], probs.tolist())))
            answers.append(got)
            raw.append((scores, labels))
    return answers, raw


def calibrate(rows, raw):
    """One temperature per head, chosen so that a stated 0.9 is right about nine times in ten."""
    heat = {}
    for f in FIELDS:
        best = (None, 1e9)
        for t in np.arange(0.5, 4.01, 0.05):
            loss, n = 0.0, 0
            for row, (scores, labels) in zip(rows, raw):
                if row[f] is None or row[f] not in labels[f]:
                    continue
                probs = router._softmax(scores[f], t)
                loss -= math.log(max(probs[labels[f].index(row[f])], 1e-9))
                n += 1
            if n and loss / n < best[1]:
                best = (float(t), loss / n)
        heat[f] = round(best[0] or 1.0, 2)
    return heat


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--train", default="train.jsonl")
    ap.add_argument("--tests", nargs="*", default=["routing_cases.json", "routing_written.json"])
    ap.add_argument("--base", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                    help="sentence-transformers/all-MiniLM-L6-v2 is a fifth of the size and twice as fast, "
                         "but cannot read Telugu or Hindi in their own script")
    ap.add_argument("--keep-all", action="store_true", help="do not drop the vocabulary of other scripts")
    ap.add_argument("--out", default="out")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=4e-5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--show", type=int, default=12, help="how many misses to print for each set")
    args = ap.parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    cls_id, sep_id, pad_id = tokenizer.cls_token_id, tokenizer.sep_token_id, tokenizer.pad_token_id or 0
    model = Router(args.base)
    tok = tokenizer.backend_tokenizer if args.keep_all else prune(tokenizer.backend_tokenizer, model)
    tok.no_truncation()
    tok.no_padding()
    model.to(device)
    rows = load_cases(args.train)
    items = featurise(rows, tok, cls_id, sep_id)
    lengths = sorted(len(i["ids"]) for i in items)
    print(f"{args.base} on {device}: {len(items)} examples, tokens median {lengths[len(lengths) // 2]}, "
          f"longest {lengths[-1]}", flush=True)

    print(f"{sum(p.numel() for p in model.parameters()) / 1e6:.1f} M parameters", flush=True)
    train(args, model, pad_id, items, device)

    # The written set is split in two: one half sets the temperatures, the other is the test.
    sets = {Path(p).stem: load_cases(p) for p in args.tests if Path(p).exists()}
    heat = {}
    if "routing_written" in sets:
        written = sets.pop("routing_written")
        tune, sets["routing_written (test half)"] = written[0::2], written[1::2]
        heat = calibrate(tune, torch_answers(model, tok, tune, cls_id, sep_id, device)[1])
        print(f"temperatures {heat}")
    results = {}
    for name, cases in sets.items():
        results[name] = score(f"{name}, torch", cases,
                              torch_answers(model, tok, cases, cls_id, sep_id, device, heat)[0], show=args.show)

    # The files the Pi runs.
    model.eval().cpu().float()
    sample = torch.tensor([items[0]["ids"]])
    torch.onnx.export(model, (sample, torch.ones_like(sample)), str(out / "full.onnx"),
                      input_names=["input_ids", "attention_mask"],
                      output_names=["intent", "action", "device", "scene"],
                      dynamic_axes={"input_ids": {0: "batch", 1: "tokens"}, "attention_mask": {0: "batch", 1: "tokens"},
                                    "device": {0: "batch", 1: "tokens"}, "scene": {0: "batch", 1: "tokens"},
                                    "intent": {0: "batch"}, "action": {0: "batch"}},
                      opset_version=17, dynamo=False)
    from onnxruntime.quantization import QuantType, quantize_dynamic
    quantize_dynamic(str(out / "full.onnx"), str(out / "model.onnx"), weight_type=QuantType.QUInt8)
    tok.save(str(out / "tokenizer.json"))
    meta = {"base": args.base, "intents": INTENTS, "actions": ACTIONS, "cls_id": cls_id, "sep_id": sep_id,
            "temperature": heat, "trained_on": len(items), "epochs": args.epochs}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))

    for label, file in (("onnx", "full.onnx"), ("onnx 8-bit (the file that ships)", "model.onnx")):
        (out / "probe").mkdir(exist_ok=True)
        for name in ("tokenizer.json", "meta.json"):
            (out / "probe" / name).write_bytes((out / name).read_bytes())
        (out / "probe" / "model.onnx").write_bytes((out / file).read_bytes())
        shipped = router.RouterModel(str(out / "probe"))
        for name, cases in sets.items():
            started = time.perf_counter()
            answers = [shipped.answer(c["say"], c["devices"], c["scenes"]) for c in cases]
            took = (time.perf_counter() - started) * 1000 / max(1, len(cases))
            results[f"{name}, {label}"] = score(f"{name}, {label} ({took:.0f} ms each here)", cases, answers,
                                                quiet=file == "full.onnx", show=args.show)
            if file == "full.onnx":
                s = results[f"{name}, {label}"]
                print(f"\n== {name}, {label}: whole decision right {s['whole']}/{s['of']}")
    # What the tests hold the shipped file to: its own score here, less two for another onnxruntime's rounding.
    shipped = {k.split(",")[0]: v["whole"] for k, v in results.items() if "8-bit" in k}
    meta["floor"] = {name: max(0, whole - 2) for name, whole in shipped.items()}
    meta["results"] = {k: {"whole": v["whole"], "of": v["of"], "sure": v["sure"], "sure_wrong": v["sure_wrong"],
                           "kinds": v["kinds"]} for k, v in results.items()}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    for file in ("full.onnx", "model.onnx"):
        print(f"{file}: {(out / file).stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
