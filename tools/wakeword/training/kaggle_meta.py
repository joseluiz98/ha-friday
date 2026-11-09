"""Metadados do Kaggle e veredito do status do kernel (spec 2026-10-24). Funções puras + uma CLI pequena.
  python3 kaggle_meta.py meta <modelo> <pasta>   -> <pasta>/kernel-metadata.json e <pasta>/data/dataset-metadata.json
  python3 kaggle_meta.py decide "<texto do status>" -> running | complete | failed"""
import json, os, sys

OWNER = "joseluiz98"


def slug(model: str) -> str:
    return model.replace("_", "-")


def kernel_metadata(model, owner=OWNER) -> dict:
    s = slug(model)
    return {"id": f"{owner}/wakeword-{s}", "title": f"wakeword-{s}", "code_file": "wakeword_v2.ipynb",
            "language": "python", "kernel_type": "notebook", "is_private": True, "enable_gpu": True,
            "enable_internet": True, "dataset_sources": [f"{owner}/wakeword-{s}-data"],
            "competition_sources": [], "kernel_sources": []}


def dataset_metadata(model, owner=OWNER) -> dict:
    s = slug(model)
    return {"id": f"{owner}/wakeword-{s}-data", "title": f"wakeword-{s}-data", "licenses": [{"name": "other"}]}


def decide(status_text: str) -> str:
    t = (status_text or "").lower()
    if "complete" in t:
        return "complete"
    if "running" in t or "queued" in t:
        return "running"
    return "failed"


def main(argv):
    if len(argv) == 4 and argv[1] == "meta":
        _, _, model, pasta = argv
        os.makedirs(os.path.join(pasta, "data"), exist_ok=True)
        json.dump(kernel_metadata(model), open(os.path.join(pasta, "kernel-metadata.json"), "w"), indent=1)
        json.dump(dataset_metadata(model), open(os.path.join(pasta, "data", "dataset-metadata.json"), "w"), indent=1)
        return 0
    if len(argv) == 3 and argv[1] == "decide":
        print(decide(argv[2]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
