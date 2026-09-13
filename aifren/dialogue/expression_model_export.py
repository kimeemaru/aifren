"""Explicit local conversion of pinned classifier weights; never a turn-time job.

Run only via automatic_expression.py --download. No remote code, inference
service or global settings changes. The exported graph is a derived cache.
"""
from pathlib import Path
import sys


def export(directory: Path) -> None:
    import onnx
    import torch
    from transformers import AutoModelForSequenceClassification
    from onnxruntime.quantization import quantize_dynamic, QuantType
    torch.set_num_threads(1)  # This isolated installer process only.

    class Export(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.model = AutoModelForSequenceClassification.from_pretrained(
                directory, local_files_only=True, trust_remote_code=False,
                use_safetensors=True, attn_implementation="eager").eval().cpu()

        def forward(self, input_ids, attention_mask):
            return self.model(input_ids=input_ids, attention_mask=attention_mask).logits

    model = Export()
    ids = torch.tensor([[0, 100, 200, 2]], dtype=torch.long)
    intermediate = directory / "model-float.onnx"
    with torch.inference_mode():
        torch.onnx.export(model, (ids, torch.ones_like(ids)), str(intermediate),
            opset_version=17, input_names=["input_ids", "attention_mask"],
            output_names=["logits"], dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"}, "logits": {0: "batch"}},
            dynamo=False)
    # Remove exporter diagnostic paths; graph data must not depend on where the
    # application was installed. This does not change nodes, weights or shapes.
    graph = onnx.load(str(intermediate), load_external_data=False)
    def strip_diagnostics(message):
        for field, value in message.ListFields():
            if field.name == "doc_string":
                setattr(message, field.name, "")
            elif field.message_type is not None:
                for child in value if field.is_repeated else (value,):
                    strip_diagnostics(child)
    strip_diagnostics(graph)
    onnx.save(graph, str(intermediate))
    quantize_dynamic(str(intermediate), str(directory / "model_quantized.onnx"),
                     weight_type=QuantType.QInt8, per_channel=True)
    intermediate.unlink()


if __name__ == "__main__":
    export(Path(sys.argv[1]))
