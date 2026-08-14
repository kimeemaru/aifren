import os

from sentence_transformers import SentenceTransformer


# ============================================================
# Local Embedding Model
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

MODEL_DIR = os.path.join(
    BASE_DIR,
    "models",
    "all-MiniLM-L6-v2"
)


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

        self.model = SentenceTransformer(
            MODEL_DIR,
            local_files_only=True
        )

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