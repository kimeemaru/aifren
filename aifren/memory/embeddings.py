import os

from sentence_transformers import SentenceTransformer


# ============================================================
# Local Embedding Model
# ============================================================

from aifren.runtime.runtime_layout import resource_path

MODEL_DIR = str(resource_path("models/all-MiniLM-L6-v2"))


class EmbeddingModel:

    def __init__(self):

        print(
            "Loading local embedding model..."
        )

        if not os.path.isdir(
            MODEL_DIR
        ):

            raise FileNotFoundError(
                "\nLocal embedding model not found.\n\n"
                "Expected model directory:\n"
                f"{MODEL_DIR}\n\n"
                "Place the all-MiniLM-L6-v2 model "
                "inside the models directory."
            )

        from aifren.runtime.config import configured_inference_device, require_torch_device
        import torch
        device = configured_inference_device()
        require_torch_device(torch, device)
        self.model = SentenceTransformer(
            MODEL_DIR, local_files_only=True, **({"device": device} if device else {}))
        if device and self.model.device.type != device:
            raise RuntimeError("Embedding model did not load on the requested inference device.")

        print(
            "Local embedding model loaded."
        )

        print(
            f"Embedding dimensions: "
            f"{self.model.get_embedding_dimension()}"
        )

    def encode(self, text):

        return self.model.encode(
            text,
            normalize_embeddings=True
        )
