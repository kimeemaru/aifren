"""Losslessly convert the pinned CMU Flite LTS tables; no network or compiler.

Original conversion script. Source data is by Alan W Black and Kevin A. Lenzo,
Carnegie Mellon University, under the adjacent upstream COPYING terms. This
modification stores the same tables as compact JSON; it does not train a model.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re

REVISION = "6c9f20dc915b17f5619340069889db0aa007fcdc"
INPUTS = {
    "cmu_lts_model.c": "a211a7b28d37041edeae5dd4be983431ef6d3b4c6e2adc84ee05c26f227f8fc9",
    "cmu_lts_model.h": "bf65916623ea4dbcc40ae2782657e265d1d582e19ec513ec8a3ae75700633f1b",
    "cmu_lts_rules.c": "148777abbaeff9c566ee4fd18886f829d0d56c8a751800467aa7e6e0c0fef8bd",
}
OUTPUT_SHA256 = "1020599f803c75641ca680208bd06cc5d0e5e31ed2742e59086542838466b1b7"


def convert(root):
    sources = {}
    for name, expected in INPUTS.items():
        data = (root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Unexpected upstream source identity: " + name)
        sources[name] = data.decode("utf-8")
    macros = {m.group(1): [int(m.group(2), 16), int(m.group(3), 16)]
              for m in re.finditer(r"#define (LTS_STATE_\w+) (0x[\da-f]+),(0x[\da-f]+)",
                                   sources["cmu_lts_model.h"])}
    contents = sources["cmu_lts_model.c"].split("{", 1)[1].rsplit("}", 1)[0]
    contents = re.sub(r"/\*.*?\*/", "", contents, flags=re.S)
    values = []
    for value in contents.split(","):
        value = value.strip()
        if not value:
            continue
        if value in macros:
            values.extend(macros[value])
        elif re.fullmatch(r"'[^']'", value):
            values.append(ord(value[1]))
        elif value.isdecimal():
            values.append(int(value))
        else:
            raise ValueError("Unsupported source token")
    states = bytes(values)
    rules = sources["cmu_lts_rules.c"]
    phones = re.findall(r'"([^\"]+)"', rules.split("cmu_lts_phone_table", 1)[1].split("};", 1)[0])
    indexes = [int(x) for x in re.findall(r"(\d+),?\s*/\* [a-z] \*/",
                                        rules.split("cmu_lts_letter_index", 1)[1])]
    if len(states) % 6 or len(indexes) != 26:
        raise ValueError("Invalid converted table dimensions")
    model = {"schema": 1, "revision": REVISION, "states_hex": states.hex(),
             "phones": phones, "indexes": indexes, "window": 4}
    result = (json.dumps(model, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if hashlib.sha256(result).hexdigest() != OUTPUT_SHA256:
        raise ValueError("Converted resource identity mismatch")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.output.open("xb") as output:
        output.write(convert(args.sources))
